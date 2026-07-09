# Hormuz Watch

A chokepoint/disruption tracker for the Persian Gulf, the Strait of
Hormuz, and the Gulf of Oman approaches — vessels anchored/queued in
the Gulf, ones actively transiting the strait, and ones still inbound
from the Arabian Sea side. Aimed at showing which cargoes are heading
toward European terminals, and how the picture changes over time.

**Status: prototype, live data pending.** `index.html` currently
renders sample/demo data (clearly labelled as such in the page itself)
while the live listener gets debugged. See "Going live" below.
Historical snapshots (Supabase) are wired in and tested but need
`SUPABASE_URL`/`SUPABASE_KEY` set on the host to activate - see
"Historical data" below. The historical *chart* on the site itself is
still to build, once there's real data to show.

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

## Historical data

`hormuz_transits.db` lives on local disk, which is wiped on every
restart on most free hosting - it was never meant to hold history, only
the current picture. Real "past weeks" data needs an external database.

**Setup (once):**
1. [supabase.com](https://supabase.com) → sign up free → New Project.
2. In the project, SQL Editor → New query → paste in the contents of
   `supabase_schema.sql` from this repo → Run. Creates the `snapshots`
   table.
3. Settings → API → copy the Project URL and the `anon` `public` key.
4. On your host (e.g. Render → Environment), add:
   - `SUPABASE_URL` = the project URL
   - `SUPABASE_KEY` = the anon key
5. Redeploy. `periodic_snapshot()` in `ais_listener.py` will start
   pushing a compact snapshot every 15 minutes automatically - counts
   of vessels underway/at anchor/other, plus the full vessel list for
   that moment, as one JSON row. If the env vars aren't set, this is a
   silent no-op and everything else works exactly as before.

Rather than storing every raw position ping (which adds up fast across
a whole region over weeks), only a periodic summary gets pushed - at
one snapshot every 15 minutes, months of history stays well within the
free 500MB.

The historical *chart* on the site itself (activity over time) is
still to build - needs a few days of real snapshots accumulated first
to be worth looking at.

## Data sources

- [aisstream.io](https://aisstream.io) — free real-time AIS
- [UKMTO](https://www.ukmto.org) — public maritime security advisories
  for the region
- [GIE ALSI](https://alsi.gie.eu) — confirms actual LNG receipts at
  European terminals

This project is independent analysis built from public data, not an
official or verified feed — treat destinations and ETAs as
self-reported and possibly stale, particularly during disruptions.
