"""
ais_listener.py

Connects to aisstream.io's free real-time AIS feed, subscribes to a
bounding box around the Strait of Hormuz, and logs every vessel we hear
from into a local SQLite database.

This is the piece that runs continuously on YOUR machine (this sandbox
can't reach the open internet, only package registries) - see the
bottom of this file for exactly how to run it for real.

Two message types matter to us:
  - ShipStaticData : sent occasionally. Identity info - name, IMO number,
                     ship type, and (usefully) the crew's self-reported
                     destination and ETA.
  - PositionReport : sent every few seconds per ship. Just lat/lon plus
                     the MMSI so we know which ship it belongs to.

We store both into one flat "pings" table. It's not a normalized schema
(a real version would split "vessels" from "position history") - but a
single append-only log is the easiest thing to reason about while we're
still testing, and easy to upgrade later.
"""

import asyncio
import json
import os
import sqlite3
from datetime import datetime, timezone

import websockets

# Bounding box: [south-west corner], [north-east corner].
# Loosely the Strait of Hormuz and its immediate approaches.
HORMUZ_BBOX = [[25.5, 55.5], [27.0, 57.0]]

DB_PATH = os.path.join(os.path.dirname(__file__), "hormuz_transits.db")
SIGHTINGS_PATH = os.path.join(os.path.dirname(__file__), "sightings.json")

AISSTREAM_URL = "wss://stream.aisstream.io/v0/stream"
EXPORT_INTERVAL_SECONDS = 60      # how often sightings.json gets refreshed
INITIAL_RECONNECT_DELAY = 10      # first retry wait, in seconds
MAX_RECONNECT_DELAY = 300         # cap: never wait longer than 5 minutes between retries


def init_db(db_path=DB_PATH):
    """Create the pings table if it doesn't exist yet, return the connection."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pings (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            mmsi        INTEGER NOT NULL,
            imo         INTEGER,
            name        TEXT,
            ship_type   INTEGER,
            lat         REAL,
            lon         REAL,
            destination TEXT,
            eta         TEXT,
            seen_at     TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def handle_message(conn, raw_message):
    """
    Parse one raw aisstream.io message and insert a row.
    Kept separate from the websocket loop below so we can call it with
    fake data too, and test the exact same parsing logic offline.
    """
    msg = json.loads(raw_message)
    mtype = msg.get("MessageType")
    now = datetime.now(timezone.utc).isoformat()

    if mtype == "PositionReport":
        r = msg["Message"]["PositionReport"]
        name = msg.get("MetaData", {}).get("ShipName", "").strip()
        conn.execute(
            "INSERT INTO pings (mmsi, name, lat, lon, seen_at) VALUES (?, ?, ?, ?, ?)",
            (r["UserID"], name, r["Latitude"], r["Longitude"], now),
        )
        conn.commit()

    elif mtype == "ShipStaticData":
        s = msg["Message"]["ShipStaticData"]
        conn.execute(
            """INSERT INTO pings (mmsi, imo, name, ship_type, destination, eta, seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                s["UserID"],
                s.get("ImoNumber"),
                s.get("Name", "").strip(),
                s.get("Type"),
                s.get("Destination", "").strip(),
                str(s.get("Eta")),
                now,
            ),
        )
        conn.commit()

    elif "error" in msg:
        # aisstream.io sends this as a real message before closing the
        # connection - e.g. an invalid key or a malformed subscription.
        # Print it loudly rather than letting it vanish silently.
        print(f"!!! aisstream.io reported an error: {msg['error']!r}")


def export_latest_sightings(conn, out_path=SIGHTINGS_PATH, source="live"):
    """
    For each MMSI, take its most recent position and its most recent
    static/identity info, and merge them into one row - the 'current
    picture' index.html actually needs, not the raw ping log.

    Writes a small envelope, not a bare array, so the frontend can show
    how fresh the data is and whether it's real ("live") or a demo run.
    """
    rows = conn.execute(
        """
        SELECT mmsi,
               MAX(CASE WHEN lat IS NOT NULL THEN lat END) AS lat,
               MAX(CASE WHEN lon IS NOT NULL THEN lon END) AS lon,
               MAX(imo) AS imo,
               MAX(CASE WHEN name != '' THEN name END) AS name,
               MAX(CASE WHEN destination != '' THEN destination END) AS destination,
               MAX(eta) AS eta
        FROM pings
        GROUP BY mmsi
        """
    ).fetchall()

    cols = ["mmsi", "lat", "lon", "imo", "name", "destination", "eta"]
    vessels = [dict(zip(cols, row)) for row in rows if row[1] is not None]

    payload = {
        "source": source,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "vessels": vessels,
    }
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)
    return payload


async def periodic_export(conn):
    """Runs alongside the listener for as long as the process lives, refreshing
    sightings.json on a timer so the map never goes too stale."""
    while True:
        await asyncio.sleep(EXPORT_INTERVAL_SECONDS)
        payload = export_latest_sightings(conn)
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"[{stamp}] exported {len(payload['vessels'])} vessel(s) to sightings.json")


async def listen():
    """
    The real, live connection. Only works from a machine with open internet.
    Reconnects automatically with exponential backoff if the connection
    drops OR is rejected outright (e.g. HTTP 429/403 during handshake) -
    matters on free-tier hosting, flaky wifi, rate limits, or any long
    unattended run. The delay resets to INITIAL_RECONNECT_DELAY the
    moment a connection actually succeeds, so one bad patch doesn't
    leave it waiting minutes between retries forever after.
    """
    api_key = os.environ.get("AISSTREAM_KEY")
    if not api_key:
        raise SystemExit("Set an AISSTREAM_KEY environment variable first (get a free key at aisstream.io).")

    conn = init_db()
    export_latest_sightings(conn)  # clear any stale committed demo data immediately,
    asyncio.create_task(periodic_export(conn))  # rather than leaving it up to 60s

    delay = INITIAL_RECONNECT_DELAY
    while True:
        try:
            async with websockets.connect(AISSTREAM_URL) as ws:
                await ws.send(json.dumps({
                    "Apikey": api_key,
                    "BoundingBoxes": [HORMUZ_BBOX],
                    "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
                }))
                print("Connected. Listening for vessels in the Hormuz bounding box...")
                delay = INITIAL_RECONNECT_DELAY  # reset backoff after a real success
                async for raw in ws:
                    try:
                        handle_message(conn, raw)
                    except Exception as e:
                        # A single unexpected message shape shouldn't kill the whole
                        # connection - log it and keep going.
                        print(f"Skipping one malformed message ({e!r}): {raw[:200]}")
        except websockets.exceptions.InvalidStatus as e:
            print(f"Handshake rejected: HTTP {e.response.status_code} {e.response.reason_phrase!r}. "
                  f"Retrying in {delay}s...")
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_RECONNECT_DELAY)
        except (websockets.exceptions.WebSocketException, OSError) as e:
            print(f"Connection failed ({e!r}). Retrying in {delay}s...")
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_RECONNECT_DELAY)


if __name__ == "__main__":
    asyncio.run(listen())
