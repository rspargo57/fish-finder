#!/usr/bin/env python3
"""Fish Finder — NOAA tide-prediction harvester (v23.53, Randy 2026-08-15).

Randy: "Could you add something to our chart somehow somewhere to show the
current tides and what times high, low, and high, low, and maybe make it so
I can toggle it?"

**What this does:**

Fetches today's tide-prediction schedule (high/low tides with timestamps
and heights) from the free NOAA CO-OPS API for 6 stations that span
Randy's fishing range: Randy's home CT waters (New London), the Race /
inshore stripers (Silver Eel Pond), East End LI (Montauk), RI south
shore (Newport), NY Bight (Sandy Hook), and southern NJ (Atlantic City).

**Data source:** NOAA Tides and Currents API — free, no signup, no key.
    https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?
    product=predictions&interval=hilo&datum=MLLW&time_zone=lst_ldt
    &units=english&format=json

**Output:** `/root/fish-finder/data/tides.json` with per-station schedule.
Each station has `name`, `coords`, and `tides: [{time, type, height_ft}]`.
The Fish Finder HTML embeds this as `TIDES_PLACEHOLDER` so the map's
🌊 Tides layer can render markers at each station with the schedule in
the popup.

**When to run:** every nightly build. `build-inlined.py` shells out to
this script the same way it does the boat-cluster detector.
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys
import urllib.error
import urllib.request

OUT_JSON = pathlib.Path("/root/fish-finder/data/tides.json")

# Stations chosen to blanket Randy's fishing range without redundancy.
# ID list verified 2026-08-15 via /mdapi/prod/webapi/stations/{id}.json —
# each returns real name/coords + hi/lo predictions for today's date.
STATIONS = [
    # v23.53 — Northeast (Old Saybrook home port)
    {"id": "8461490", "name": "New London, CT",       "coords": [41.3717, -72.0956], "role": "home port area (Old Saybrook is 15nm west; tides ~15-20 min later there)"},
    {"id": "8510719", "name": "Silver Eel Pond (Fishers Island / The Race), NY", "coords": [41.2567, -72.0300], "role": "The Race / Fishers Island — striper current windows"},
    {"id": "8510560", "name": "Montauk, NY",          "coords": [41.0483, -71.9594], "role": "East End LI, Block Island Sound east side"},
    {"id": "8452660", "name": "Newport, RI",          "coords": [41.5043, -71.3261], "role": "RI south shore — Narragansett, Point Judith runs"},
    # v24.4 — Mid-Atlantic
    {"id": "8531680", "name": "Sandy Hook, NJ",       "coords": [40.4669, -74.0094], "role": "NY Bight / NJ north coast — Hudson Canyon runs, Mud Hole area"},
    {"id": "8534720", "name": "Atlantic City, NJ",    "coords": [39.3567, -74.4180], "role": "Southern NJ — Barnegat / LBI area"},
    # v24.80 (Randy 2026-09-03) — SE Coast (Coastal Carolinas / GA / NE FL)
    {"id": "8658163", "name": "Wrightsville Beach, NC","coords": [34.2133, -77.7867], "role": "NC coast — Frying Pan Shoals, offshore Wrightsville runs"},
    {"id": "8656483", "name": "Beaufort, NC",         "coords": [34.7200, -76.6700], "role": "NC middle coast — Cape Lookout, Big Rock runs"},
    {"id": "8665530", "name": "Charleston, SC",       "coords": [32.7817, -79.9250], "role": "SC coast — Charleston Bump / Deli Belly runs"},
    {"id": "8670870", "name": "Fort Pulaski (Savannah), GA","coords": [32.0333, -80.9017], "role": "GA coast — Snapper Banks, Gray's Reef runs"},
    {"id": "8720030", "name": "Fernandina Beach, FL", "coords": [30.6717, -81.4650], "role": "NE FL coast — Amelia Island reef runs"},
    {"id": "8720587", "name": "St. Augustine, FL",    "coords": [29.8567, -81.2617], "role": "NE FL coast — St. Augustine ledge runs"},
    # v24.80 — Gulf (Emerald Coast: FL Panhandle + AL)
    {"id": "8735180", "name": "Dauphin Island, AL",   "coords": [30.2500, -88.0750], "role": "AL coast — Mobile Bay mouth, Fort Morgan Rigs"},
    {"id": "8729840", "name": "Pensacola, FL",        "coords": [30.4033, -87.2117], "role": "Perdido Pass / Pensacola inshore + nearshore reefs"},
    {"id": "8729108", "name": "Panama City, FL",      "coords": [30.1517, -85.6667], "role": "FL Panhandle — St. Andrew Bay runs"},
    {"id": "8728690", "name": "Apalachicola, FL",     "coords": [29.7267, -84.9800], "role": "FL Panhandle east — Apalachicola Bay + St George Island"},
    # v24.80 — S. Florida (Palm Beach → Miami → Keys + SW FL)
    {"id": "8722670", "name": "West Palm Beach, FL",  "coords": [26.7767, -80.0333], "role": "Palm Beach — Sailfish Alley, Juno Ledge kite fishing"},
    {"id": "8723170", "name": "Miami / Government Cut, FL", "coords": [25.7683, -80.1317], "role": "Miami — Fowey Rocks, Miami 150 Ledge, Gulf Stream edge"},
    {"id": "8723214", "name": "Virginia Key (Miami), FL", "coords": [25.7317, -80.1617], "role": "Miami inner + Biscayne — sailfish + tarpon"},
    {"id": "8724580", "name": "Key West, FL",         "coords": [24.5533, -81.8083], "role": "Lower Keys — American Shoal, Marquesas, Tortugas"},
    {"id": "8723970", "name": "Vaca Key (Marathon), FL","coords": [24.7100, -81.1067], "role": "Middle Keys — Sombrero Reef, Marathon Humps"},
    {"id": "8725110", "name": "Naples, FL",           "coords": [26.1317, -81.8067], "role": "SW FL — Naples Hardbottom, offshore ledges"},
    {"id": "8725520", "name": "Fort Myers, FL",       "coords": [26.6478, -81.8710], "role": "SW FL — Fort Myers reefs, Sanibel"},
]


def fetch_tide_schedule(station_id, begin_date, end_date, timeout=15):
    """Fetch high/low tide predictions for one station over the date range.
    Returns list of {time, type ("H"|"L"), height_ft} or None on failure."""
    url = (
        "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?"
        f"product=predictions&begin_date={begin_date}&end_date={end_date}"
        f"&station={station_id}&datum=MLLW&time_zone=lst_ldt&units=english"
        "&interval=hilo&format=json"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "FishFinderBot/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status != 200:
                return None
            data = json.loads(r.read())
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    preds = data.get("predictions")
    if not preds:
        return None
    out = []
    for p in preds:
        try:
            out.append({
                "time":     p["t"],            # "2026-08-16 02:16"
                "type":     p["type"],         # "H" or "L"
                "height_ft": float(p["v"]),
            })
        except (KeyError, ValueError):
            continue
    return out


def harvest_tides(verbose=False):
    """Full pipeline. Returns dict {generated_at, stations: [...], notes}."""
    result = {
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "stations": [],
        "notes": [],
    }
    # Fetch today + tomorrow so the popup can show "next 4 tides" cleanly
    # even at 11pm when tomorrow's morning tides are what matters.
    today = datetime.date.today()
    tomorrow = today + datetime.timedelta(days=1)
    begin = today.strftime("%Y%m%d")
    end = tomorrow.strftime("%Y%m%d")

    ok_count = 0
    for st in STATIONS:
        if verbose: print(f"  fetching {st['id']} {st['name']}…")
        schedule = fetch_tide_schedule(st["id"], begin, end)
        entry = {
            "id":     st["id"],
            "name":   st["name"],
            "coords": st["coords"],
            "role":   st["role"],
            "tides":  schedule or [],
        }
        if schedule:
            ok_count += 1
            if verbose:
                first = schedule[0]
                print(f"    ✓ {len(schedule)} entries · next: {first['type']} at {first['time']} ({first['height_ft']:.1f}ft)")
        else:
            if verbose: print(f"    ⚠  no data")
        result["stations"].append(entry)

    result["fetched_ok"] = ok_count
    result["fetched_total"] = len(STATIONS)
    if ok_count == 0:
        result["notes"].append("NOAA CO-OPS API returned no data for any station this run — layer will show empty popups.")

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, indent=2))
    if verbose:
        print(f"  wrote {OUT_JSON} — {ok_count}/{len(STATIONS)} stations OK")
    # v24.100 source-health record — success == ≥ half of stations returned data
    try:
        import source_health as _sh
        _sh.record("tides", rows=ok_count, ok=(ok_count >= len(STATIONS) // 2),
                   error=None if ok_count >= len(STATIONS) // 2
                         else f"only {ok_count}/{len(STATIONS)} stations returned data")
    except Exception:
        pass
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()
    r = harvest_tides(verbose=args.verbose)
    print(f"\nTide harvest: {r['fetched_ok']}/{r['fetched_total']} stations OK")


if __name__ == "__main__":
    main()
