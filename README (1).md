# Hormuz Watch

A chokepoint/disruption tracker for LNG carriers transiting the Strait of
Hormuz, aimed at showing which cargoes are currently heading toward
European terminals.

**Status: prototype.** `index.html` currently renders sample/demo data
(clearly labelled as such in the page itself) so the page and pipeline
can be reviewed without a live connection running yet. See "Going live"
below for what's needed to switch it over to real data.

## How it works

- `ais_listener.py` — connects to [aisstream.io](https://aisstream.io)'s
  free real-time AIS feed, subscribed to a bounding box around the
  Strait of Hormuz, logs vessel identity + position pings to a local
  SQLite database (`hormuz_transits.db`, not committed — see
  `.gitignore`), auto-reconnects if the connection drops, and
  refreshes `sightings.json` every `EXPORT_INTERVAL_SECONDS` (60s by
  default) via a concurrent background task.
- `demo_with_sample_data.py` — feeds fictional sample messages through
  the same parsing logic as the real listener, to test the pipeline
  offline, and exports the merged result to `sightings.json` with
  `source: "demo"`.
- `index.html` — a Leaflet map that fetches `sightings.json` at load
  time and renders vessel positions and self-reported destinations.
  Falls back to embedded sample data if the fetch fails (e.g. opened
  as a lone local file rather than served over HTTP). The "DEMO DATA"
  badge and the "updated HH:MM" indicator both read directly off the
  fetched payload's `source` and `generated_at` fields, so they
  self-correct once real data is flowing — no code change needed.

`sightings.json` shape:
```json
{
  "source": "live",
  "generated_at": "2026-07-09T09:42:09Z",
  "vessels": [
    {"mmsi": 273..., "lat": 26.57, "lon": 56.25, "imo": 9..., "name": "...", "destination": "...", "eta": "MMDDHHmm"}
  ]
}
```

## Running the listener yourself

```bash
pip install -r requirements.txt
export AISSTREAM_KEY=your_free_key_from_aisstream.io
python3 ais_listener.py
```

Never commit your real `AISSTREAM_KEY` to this repo — always pass it
as an environment variable, as above.

## Going live

1. Deploy `serve.py` (not `ais_listener.py` directly) to a free-tier
   host like Render — see "Deploying to Render" below. `serve.py`
   wraps the listener in a minimal HTTP health check so free-tier
   platforms that only host "web services" will run it.
   `index.html` and its fetch of `sightings.json` are already wired
   up and waiting — nothing else to change once the listener is
   running and both files are deployed together.
2. Known next steps beyond that: filter to actual LNG carriers via a
   static IMO reference list (AIS ship-type codes alone won't
   distinguish LNG carriers from other tankers), and normalize the
   messy free-text destination field.

## Deploying to Render (free tier)

1. [render.com](https://render.com) → sign up (free, no card required
   for this) → **New** → **Web Service** → connect this GitHub repo.
2. Runtime: Python 3. Build command: `pip install -r requirements.txt`.
   Start command: `python3 serve.py`.
3. Under **Environment**, add `AISSTREAM_KEY` = your key from
   aisstream.io. Never put this in the repo itself.
4. Deploy. Check the logs for "Connected. Listening for vessels...".
5. Free-tier services on most platforms sleep after ~15 minutes with
   no incoming HTTP traffic, which will interrupt the listener until
   something wakes it back up. A free uptime-pinger (UptimeRobot,
   cron-job.org, or a scheduled GitHub Action) hitting your Render
   URL every 10 minutes keeps it awake far more of the time. Not
   required to get started - worth adding once the basic deploy is
   confirmed working.
6. Exact free-tier terms shift over time - if something above doesn't
   match what you see on signup, tell me what you're seeing and we'll
   adjust.

## Data sources

- [aisstream.io](https://aisstream.io) — free real-time AIS
- [UKMTO](https://www.ukmto.org) — public maritime security advisories
  for the region
- [GIE ALSI](https://alsi.gie.eu) — confirms actual LNG receipts at
  European terminals

This project is independent analysis built from public data, not an
official or verified feed — treat destinations and ETAs as
self-reported and possibly stale, particularly during disruptions.
