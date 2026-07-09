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
  Strait of Hormuz, and logs vessel identity + position pings to a
  local SQLite database (`hormuz_transits.db`, not committed — see
  `.gitignore`).
- `demo_with_sample_data.py` — feeds fictional sample messages through
  the same parsing logic as the real listener, to test the pipeline
  offline, and exports the merged result to `sightings.json`.
- `index.html` — a Leaflet map that renders vessel positions and
  self-reported destinations. Currently reads an embedded copy of
  `sightings.json`'s contents; swap this for a live `fetch()` once
  there's a real endpoint to call (see comment at the top of the
  `<script>` block).

## Running the listener yourself

```bash
pip install -r requirements.txt
export AISSTREAM_KEY=your_free_key_from_aisstream.io
python3 ais_listener.py
```

Never commit your real `AISSTREAM_KEY` to this repo — always pass it
as an environment variable, as above.

## Going live

1. Run `ais_listener.py` continuously somewhere with real internet
   access (this can't run inside an AI sandbox with restricted network
   egress — it needs your own machine, a small VPS, or similar).
2. Add a periodic call to `export_latest_sightings()` (see
   `demo_with_sample_data.py` for the function) so `sightings.json`
   stays fresh.
3. Point `index.html` at that live `sightings.json` instead of the
   embedded sample array.
4. Known next steps beyond that: filter to actual LNG carriers via a
   static IMO reference list (AIS ship-type codes alone won't
   distinguish LNG carriers from other tankers), and normalize the
   messy free-text destination field.

## Data sources

- [aisstream.io](https://aisstream.io) — free real-time AIS
- [UKMTO](https://www.ukmto.org) — public maritime security advisories
  for the region
- [GIE ALSI](https://alsi.gie.eu) — confirms actual LNG receipts at
  European terminals

This project is independent analysis built from public data, not an
official or verified feed — treat destinations and ETAs as
self-reported and possibly stale, particularly during disruptions.
