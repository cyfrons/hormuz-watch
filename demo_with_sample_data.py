"""
demo_with_sample_data.py

*** Uses entirely fictional, made-up vessel data. Not a real feed. ***

Feeds a handful of aisstream.io-shaped messages through the same
handle_message() function ais_listener.py uses for the live connection.
This proves the parsing/storage logic works end-to-end without needing
a live connection to aisstream.io (which this sandbox can't reach).

It also writes sightings.json - a flat export of the latest known
position + identity per vessel - which is what the map page reads.
That's the same shape a real deployment would export from its database
for the frontend to fetch.
"""

import json
import sqlite3

from ais_listener import DB_PATH, handle_message, init_db

# Fictional test vessels. Real IMO/MMSI numbers are 7 and 9 digits;
# these are deliberately made up and don't correspond to real ships.
SAMPLE_MESSAGES = [
    {
        "MessageType": "ShipStaticData",
        "Message": {"ShipStaticData": {
            "UserID": 900000001, "ImoNumber": 9000001, "Name": "DEMO CARRIER ALPHA  ",
            "Type": 80, "Destination": "RDAM NL", "Eta": "07151200",
        }},
    },
    {
        "MessageType": "PositionReport",
        "Message": {"PositionReport": {"UserID": 900000001, "Latitude": 26.57, "Longitude": 56.25}},
        "MetaData": {"ShipName": "DEMO CARRIER ALPHA"},
    },
    {
        "MessageType": "ShipStaticData",
        "Message": {"ShipStaticData": {
            "UserID": 900000002, "ImoNumber": 9000002, "Name": "DEMO CARRIER BRAVO  ",
            "Type": 80, "Destination": "ZEEBRUGGE", "Eta": "07180600",
        }},
    },
    {
        "MessageType": "PositionReport",
        "Message": {"PositionReport": {"UserID": 900000002, "Latitude": 26.30, "Longitude": 56.60}},
        "MetaData": {"ShipName": "DEMO CARRIER BRAVO"},
    },
    {
        "MessageType": "ShipStaticData",
        "Message": {"ShipStaticData": {
            "UserID": 900000003, "ImoNumber": 9000003, "Name": "DEMO CARRIER CHARLIE",
            "Type": 80, "Destination": "SWINOUJSCIE PL", "Eta": "07130800",
        }},
    },
    {
        "MessageType": "PositionReport",
        "Message": {"PositionReport": {"UserID": 900000003, "Latitude": 26.75, "Longitude": 56.05}},
        "MetaData": {"ShipName": "DEMO CARRIER CHARLIE"},
    },
]


def export_latest_sightings(conn, out_path):
    """
    For each MMSI, take its most recent position and its most recent
    static/identity info, and merge them into one row. This is the
    'current picture' the map actually needs - not the raw ping log.
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
    sightings = [dict(zip(cols, row)) for row in rows if row[1] is not None]

    with open(out_path, "w") as f:
        json.dump(sightings, f, indent=2)
    return sightings


if __name__ == "__main__":
    conn = init_db()
    for msg in SAMPLE_MESSAGES:
        handle_message(conn, json.dumps(msg))
    print(f"Inserted {len(SAMPLE_MESSAGES)} sample messages into {DB_PATH}\n")

    print("Raw pings table:")
    for row in conn.execute("SELECT mmsi, name, lat, lon, destination FROM pings"):
        print(" ", row)

    sightings = export_latest_sightings(conn, "sightings.json")
    print(f"\nExported {len(sightings)} merged vessel sightings to sightings.json:")
    for s in sightings:
        print(" ", s)
