# Fish Finder

**Mission:** Aggregate current, public information about tuna (and eventually striper) catches off the Connecticut, New York, and Rhode Island coasts — Long Island Sound out to the offshore canyons ~80–130 nautical miles from shore — and present it as an interactive heat map so Randy can see at a glance where the fish are being caught this week.

## Current state (v1 — 2026-07-22)

- **17 zones** defined across inshore, nearshore, midshore, and canyon categories.
- **Heat scale 1–10** applied to each zone based on current-season (July 2026) reported activity density.
- **Species covered:** bluefin tuna, yellowfin tuna, bigeye tuna, striped bass, mahi, mako, thresher.
- **Interactive map:** filterable by species, region (CT/NY/RI), zone type. Click any zone for details.
- **Named hot spots** located and mapped: Hudson Canyon, Block Canyon, Atlantis, Veatch/Hydrographer, The Mud Hole, Butterfish Hole, Habs Ledge, Tuna Ridge, The Gully, SE Coxes Ledge, The Acid Barge, plus nearshore corridors south of Block Island and off Montauk.

## Files

- `map/fish-finder.html` — the interactive map (self-contained, opens in any browser)
- `data/zones.json` — structured zone data (heat, species, coords, notes, sources)
- `docs/` — for future writeups
- `sources/` — for cached source snapshots when we build the refresh pipeline

## Data sources currently in the mix

Compiled from **NOAA HMS bluefin landings updates**, **On The Water** weekly reports (RI/CT/LI-NYC — July 2 & 16, 2026), **The Fisherman** magazine, **J&B Tackle Capt. Kyle's blog**, **ROFFS** offshore analysis, and captain/charter reports (Newport Sportfishing, Tall Tailz, Keepin' It Reel, Apex Angling, Rockfish, Tuna Cartel, Reel Therapy, Joe Diorio Guide Service).

Full source list is embedded in `data/zones.json` under `sources_used`.

## Known limitations (v1)

- Heat scores are hand-set from a manual read of current-week reports. No automated refresh yet.
- NOAA HMS bluefin landings data is not published with geographic breakdowns in real time — we have to infer geography from captain reports and tournament results.
- Zone bounding boxes are approximate rectangles; a next iteration should use the actual bathymetric contours (20-fathom, 30-fathom, 100-fathom lines) and hand-drawn polygons for canyon shapes.

## Roadmap

**v2 candidates (Randy to pick):**
1. **Automate the refresh** — a weekly scraper that pulls the On The Water RI/CT/LI reports and re-scores heat.
2. **Add stripers as a first-class species** with their own zone heat scoring (Long Island Sound stripers is a whole different data model).
3. **Add sea-surface temperature (SST) overlay** — canyon tuna follow the 68–72°F break.
4. **Add tournament calendar** — Star Island Yacht Club, Manhattan Cup, Oak Bluffs Monster Shark, etc., with results feeding back into heat scores.
5. **Log Randy's own trips** — a simple form to add personal catch entries.
6. **Publish the site** — GitHub Pages or Netlify so it has a URL Randy can share.
