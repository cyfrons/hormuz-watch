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
import sys
from collections import deque
from datetime import datetime, timezone

import requests
import websockets

sys.stdout.reconfigure(line_buffering=True)  # flush every print immediately - matters if
                                              # the process ever gets killed rather than exits cleanly

# Last 50 diagnostic lines, newest last. serve.py exposes this via /status
# so we're never dependent on finding the right tab in a hosting dashboard.
RECENT_EVENTS = deque(maxlen=50)


def log_event(message):
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    line = f"[{stamp}] {message}"
    print(line)
    RECENT_EVENTS.append(line)

# Supabase config for historical snapshots - optional. If unset, snapshotting
# is silently skipped and everything else works exactly as before.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
SNAPSHOT_INTERVAL_SECONDS = 900  # 15 min - frequent enough for a useful trend,
                                 # infrequent enough to stay well within free storage

# Bounding box: [south-west corner], [north-east corner].
# The Persian/Arabian Gulf, the Strait of Hormuz, and the Gulf of Oman
# approaches - not just the strait itself, so we catch vessels anchored
# or queued in the Gulf, ones actively transiting, and ones still inbound
# from the Arabian Sea side. Overridable via HORMUZ_BBOX env var for testing.
_bbox_override = os.environ.get("HORMUZ_BBOX")
HORMUZ_BBOX = json.loads(_bbox_override) if _bbox_override else [[20.0, 47.0], [30.5, 63.0]]

DB_PATH = os.path.join(os.path.dirname(__file__), "hormuz_transits.db")
SIGHTINGS_PATH = os.path.join(os.path.dirname(__file__), "sightings.json")

AISSTREAM_URL = "wss://stream.aisstream.io/v0/stream"
EXPORT_INTERVAL_SECONDS = 60      # how often sightings.json gets refreshed
INITIAL_RECONNECT_DELAY = 10      # first retry wait, in seconds
MAX_RECONNECT_DELAY = 300         # cap: never wait longer than 5 minutes between retries


# Standard AIS navigational status codes (ITU-R M.1371) - the ones actually
# worth naming for a congestion/disruption tracker; anything else falls
# back to the raw code.
NAV_STATUS_NAMES = {
    0: "underway (engine)",
    1: "at anchor",
    2: "not under command",
    3: "restricted maneuverability",
    4: "constrained by draught",
    5: "moored",
    6: "aground",
    7: "fishing",
    8: "underway (sailing)",
    15: "unknown",
}


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
            nav_status  INTEGER,
            speed       REAL,
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
            "INSERT INTO pings (mmsi, name, lat, lon, nav_status, speed, seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (r["UserID"], name, r["Latitude"], r["Longitude"],
             r.get("NavigationalStatus"), r.get("Sog"), now),
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
        # Log it loudly rather than letting it vanish silently.
        log_event(f"!!! aisstream.io reported an error: {msg['error']!r}")


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
               MAX(eta) AS eta,
               MAX(nav_status) AS nav_status,
               MAX(speed) AS speed
        FROM pings
        GROUP BY mmsi
        """
    ).fetchall()

    cols = ["mmsi", "lat", "lon", "imo", "name", "destination", "eta", "nav_status", "speed"]
    vessels = [dict(zip(cols, row)) for row in rows if row[1] is not None]
    for v in vessels:
        v["status"] = NAV_STATUS_NAMES.get(v["nav_status"], f"code {v['nav_status']}" if v["nav_status"] is not None else "unknown")

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
        log_event(f"exported {len(payload['vessels'])} vessel(s) to sightings.json")


def _push_snapshot_sync(payload):
    """
    Blocking HTTP call to Supabase's REST API - deliberately NOT async.
    Called via asyncio.to_thread() so it runs on a background thread and
    can never stall the AIS websocket loop, even if Supabase is slow.
    """
    vessels = payload["vessels"]
    counts = {"underway": 0, "at_anchor": 0, "other": 0}
    for v in vessels:
        if v.get("nav_status") == 0:
            counts["underway"] += 1
        elif v.get("nav_status") == 1:
            counts["at_anchor"] += 1
        else:
            counts["other"] += 1

    row = {
        "total_vessels": len(vessels),
        "underway": counts["underway"],
        "at_anchor": counts["at_anchor"],
        "other_status": counts["other"],
        "vessels": vessels,
    }
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/snapshots",
        json=row,
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return row


async def periodic_snapshot(conn):
    """Runs alongside the listener, pushing a compact historical snapshot to
    Supabase every SNAPSHOT_INTERVAL_SECONDS. A no-op if Supabase isn't
    configured, and a failed push never crashes the listener - just logs."""
    if not (SUPABASE_URL and SUPABASE_KEY):
        log_event("Supabase not configured (SUPABASE_URL/SUPABASE_KEY unset) - skipping history.")
        return
    while True:
        await asyncio.sleep(SNAPSHOT_INTERVAL_SECONDS)
        payload = export_latest_sightings(conn)
        try:
            row = await asyncio.to_thread(_push_snapshot_sync, payload)
            log_event(f"Pushed snapshot to Supabase: {row['total_vessels']} vessels "
                      f"({row['underway']} underway, {row['at_anchor']} at anchor)")
        except Exception as e:
            log_event(f"Supabase snapshot push failed ({e!r}) - will retry next interval.")


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
    asyncio.create_task(periodic_snapshot(conn))
    log_event(f"Using bounding box: {HORMUZ_BBOX}"
               + (" (override active)" if _bbox_override else " (default)"))

    delay = INITIAL_RECONNECT_DELAY
    while True:
        try:
            async with websockets.connect(AISSTREAM_URL) as ws:
                await ws.send(json.dumps({
                    "Apikey": api_key,
                    "BoundingBoxes": [HORMUZ_BBOX],
                    "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
                }))
                log_event("Connected. Listening for vessels in the Hormuz bounding box...")
                delay = INITIAL_RECONNECT_DELAY  # reset backoff after a real success
                msg_count = 0
                async for raw in ws:
                    msg_count += 1
                    if msg_count == 1:
                        log_event("First message received from aisstream.io.")
                    try:
                        handle_message(conn, raw)
                    except Exception as e:
                        # A single unexpected message shape shouldn't kill the whole
                        # connection - log it and keep going.
                        log_event(f"Skipping one malformed message ({e!r}): {raw[:200]}")
        except websockets.exceptions.InvalidStatus as e:
            log_event(f"Handshake rejected: HTTP {e.response.status_code} {e.response.reason_phrase!r}. "
                      f"Retrying in {delay}s...")
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_RECONNECT_DELAY)
        except (websockets.exceptions.WebSocketException, OSError) as e:
            log_event(f"Connection failed ({e!r}). Retrying in {delay}s...")
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_RECONNECT_DELAY)


if __name__ == "__main__":
    asyncio.run(listen())
