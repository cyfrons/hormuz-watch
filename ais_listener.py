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

AISSTREAM_URL = "wss://stream.aisstream.io/v0/stream"


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


async def listen():
    """The real, live connection. Only works from a machine with open internet."""
    api_key = os.environ.get("AISSTREAM_KEY")
    if not api_key:
        raise SystemExit("Set an AISSTREAM_KEY environment variable first (get a free key at aisstream.io).")

    conn = init_db()
    async with websockets.connect(AISSTREAM_URL) as ws:
        await ws.send(json.dumps({
            "Apikey": api_key,
            "BoundingBoxes": [HORMUZ_BBOX],
            "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
        }))
        print("Connected. Listening for vessels in the Hormuz bounding box...")
        async for raw in ws:
            handle_message(conn, raw)


if __name__ == "__main__":
    asyncio.run(listen())
