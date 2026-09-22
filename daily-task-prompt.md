You are running the daily update task for **Fish Finder**, Randy Spargo's Northeast tuna and striper heat-map project.

## Mission

Produce a concise **Fish Finder Daily Brief** — a self-contained HTML document — summarizing the latest tuna and striper activity across Connecticut, New York, and Rhode Island shores (from Long Island Sound out to the offshore canyons ~130 nautical miles offshore). Deliver it to Randy so he can check it each morning before heading out fishing.

## Context

Randy fishes giant bluefin tuna and stripers out of Connecticut and Rhode Island. The parent Fish Finder project tracks 27 named zones on an interactive map with 1–10 heat scores per zone. Your job is to keep the intelligence fresh with the latest reports.

- **Target species**: bluefin tuna, yellowfin tuna, bigeye tuna, striped bass
- **Region**: CT, NY, RI coasts; Long Island Sound; Block Island Sound; Atlantic offshore to Hudson, Block, Atlantis, Veatch, Hydrographer Canyons
- **Heat scoring rubric**:
  - 1–2: no current activity
  - 3–4: occasional / pass-through
  - 5–6: steady, real option
  - 7–8: multiple boats scoring
  - 9–10: on fire this week

## Baseline zones (as of 2026-07-22)

Use this as your reference. Track whether each zone's heat has moved up or down based on today's reports.

```json
{
  "reference_date": "2026-07-22",
  "zones": [
    {"id": "western_li_sound", "name": "Western Long Island Sound", "region": "CT", "heat": 7, "species": ["striped_bass"]},
    {"id": "central_li_sound", "name": "Central Long Island Sound", "region": "CT", "heat": 5, "species": ["striped_bass"]},
    {"id": "the_triangle", "name": "The Triangle", "region": "CT", "heat": 7, "species": ["striped_bass"]},
    {"id": "the_race", "name": "The Race", "region": "CT", "heat": 9, "species": ["striped_bass"]},
    {"id": "plum_gut", "name": "Plum Gut", "region": "NY", "heat": 8, "species": ["striped_bass"]},
    {"id": "race_rock", "name": "Race Rock / SW Fishers", "region": "CT", "heat": 7, "species": ["striped_bass"]},
    {"id": "watch_hill_reef", "name": "Watch Hill Reef", "region": "RI", "heat": 7, "species": ["striped_bass"]},
    {"id": "millstone_point", "name": "Millstone Point", "region": "CT", "heat": 6, "species": ["striped_bass"]},
    {"id": "narragansett_bay", "name": "Narragansett Bay / Newport Rips", "region": "RI", "heat": 8, "species": ["striped_bass"]},
    {"id": "providence_seekonk", "name": "Providence / Seekonk Rivers", "region": "RI", "heat": 6, "species": ["striped_bass"]},
    {"id": "block_island_striper", "name": "Block Island Reefs (SW Ledge / SE Light)", "region": "RI", "heat": 9, "species": ["striped_bass"]},
    {"id": "montauk_rips_striper", "name": "Montauk Point Rips", "region": "NY", "heat": 8, "species": ["striped_bass"]},
    {"id": "block_island_sound", "name": "Block Island Sound", "region": "RI", "heat": 5, "species": ["striped_bass", "bluefin_tuna"]},
    {"id": "south_shore_li", "name": "South Shore Long Island (Moriches / Shinnecock)", "region": "NY", "heat": 6, "species": ["bluefin_tuna", "yellowfin_tuna"]},
    {"id": "montauk_nearshore", "name": "Montauk / East End Offshore", "region": "NY", "heat": 7, "species": ["bluefin_tuna", "yellowfin_tuna"]},
    {"id": "s_block_nearshore", "name": "Nearshore South of Block Island", "region": "RI", "heat": 9, "species": ["bluefin_tuna"]},
    {"id": "acid_barge", "name": "The Acid Barge", "region": "RI", "heat": 6, "species": ["bluefin_tuna"]},
    {"id": "coxes_ledge_se", "name": "Southeast Coxes Ledge", "region": "RI", "heat": 7, "species": ["bluefin_tuna", "yellowfin_tuna"]},
    {"id": "the_gully", "name": "The Gully", "region": "RI", "heat": 6, "species": ["bluefin_tuna"]},
    {"id": "habs_ledge", "name": "Habs Ledge", "region": "NY", "heat": 7, "species": ["bluefin_tuna"]},
    {"id": "tuna_ridge", "name": "Tuna Ridge (30-Fathom S of Block)", "region": "RI", "heat": 8, "species": ["bluefin_tuna", "yellowfin_tuna"]},
    {"id": "butterfish_hole", "name": "The Butterfish Hole", "region": "NY", "heat": 6, "species": ["yellowfin_tuna", "bluefin_tuna"]},
    {"id": "mud_hole", "name": "The Mud Hole", "region": "NY", "heat": 6, "species": ["bluefin_tuna", "yellowfin_tuna"]},
    {"id": "hudson_canyon", "name": "Hudson Canyon", "region": "NY", "heat": 8, "species": ["yellowfin_tuna", "bigeye_tuna"]},
    {"id": "block_canyon", "name": "Block Canyon", "region": "RI", "heat": 8, "species": ["yellowfin_tuna", "bigeye_tuna"]},
    {"id": "atlantis_canyon", "name": "Atlantis Canyon", "region": "RI", "heat": 9, "species": ["yellowfin_tuna", "bigeye_tuna"]},
    {"id": "veatch_hydrographer", "name": "Veatch / Hydrographer Canyons", "region": "RI", "heat": 7, "species": ["yellowfin_tuna", "bigeye_tuna"]}
  ]
}
```

## Steps

1. **Fetch fresh reports.** WebSearch for the most recent fishing reports (this week if published) from:
   - `onthewater.com/fishing-reports` — RI, CT, LI/NYC weekly reports
   - `thefisherman.com/area/east-end/` — Long Island East End
   - `fisheries.noaa.gov/atlantic-highly-migratory-species/2026-atlantic-bluefin-tuna-landings-updates`
   - Any tournament results if in season (Star Island YC Shark Tournament, Manhattan Cup, etc.)

2. **Extract updates.** For each report, note:
   - Zones/spots mentioned (map them to the reference zones above)
   - Species, size class, count/limit
   - Captain and boat names for attribution
   - Water temp observations if given
   - Notable outliers (60lb+ stripers, giant bluefin, etc.)

3. **Assess heat shifts.** For each zone, decide whether today's heat is higher, same, or lower than the reference. Multiple strong reports = up; nothing mentioned or "slowing down" = flat/down.

4. **Build the HTML brief.** A single self-contained file (inline CSS, dark theme matching Fish Finder — deep blue background `#0a1a2e`, panel `#12283d`, accent `#4fc3f7`). Include:
   - **Header**: "Fish Finder Daily — [today's date, formatted like 'Wednesday, July 22, 2026']"
   - **Top 5 Hottest Zones** panel with heat score, species, one-line why-it's-hot
   - **What Changed Since [reference_date]** section listing zones that moved up or down (with delta)
   - **Recent Reports** — quote 3–6 specific captain quotes with attribution and date, grouped by species
   - **NOAA SST Snapshot** — embed live image: `<img src="https://coastwatch.pfeg.noaa.gov/erddap/wms/jplMURSST41/request?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS=jplMURSST41:analysed_sst&CRS=EPSG:4326&BBOX=39,-74,42,-68&WIDTH=800&HEIGHT=400&FORMAT=image/png&STYLES=" alt="NE SST" style="width:100%;max-width:800px;border-radius:6px;">`. Add a note: "Look for the 68–72°F break lines — that's where tuna hunt."
   - **Sources** at bottom with markdown-style links

5. **Deliver.** Call `SendUserFile` with the HTML file and a one-line caption. Then attempt `mcp__remote-devices__create_artifact` (or `update_artifact` if `list_artifacts` shows `fish-finder-daily` already exists) with id `fish-finder-daily` and the file_uuid returned by SendUserFile. If the artifact tool errors or the user isn't connected, that's fine — the SendUserFile is what matters.

## Style

- **Scannable.** Randy reads this on his phone before untying the boat.
- **Specific.** Real names, real spots, real numbers. No hedging language.
- **Honest gaps.** If reports haven't updated since the reference date, say so plainly at the top ("No new weekly reports since [date] — baseline unchanged") and produce the brief anyway with the current baseline as the "today" state.
- **Under 600 lines of HTML.**

## Timezone note

Randy is in America/New_York. Format the date in his local time.
