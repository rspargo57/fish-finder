#!/usr/bin/env python3
"""
v24.66 — Compile Atlantic state artificial-reef data (NJ/DE/MD/VA/NC/SC/GA/FL).
"""
import json, re, urllib.request, sys, os, time
from pathlib import Path
from collections import Counter

BASE = Path(__file__).parent
STAGE = BASE / "data" / "staging"

def fetch(url, timeout=90):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 fishing-app"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")

# TidesPro embeds coordinates as: showOnMap(LONGITUDE, LATITUDE) inside <tr>s.
# Each <tr> contains the reef name text followed by the button call. Pattern:
#   NAME <a ... href="javascript:showOnMap(LON, LAT)">
def parse_tidespro(html, state):
    # Find all <tr>...showOnMap(...)... blocks
    reefs = []
    # Match each row that has a showOnMap call
    for m in re.finditer(r'<tr[^>]*>(.*?)</tr>', html, re.S | re.I):
        row = m.group(1)
        # Extract coordinate call
        cm = re.search(r'showOnMap\s*\(\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)', row)
        if not cm: continue
        lon, lat = float(cm.group(1)), float(cm.group(2))
        # Basic sanity for US East Coast + Gulf
        if not (20 <= lat <= 45 and -100 <= lon <= -65): continue
        # Name: first <td> or text before the button — strip HTML
        first_td = re.search(r'<td[^>]*>(.*?)</td>', row, re.S | re.I)
        if not first_td: continue
        name = re.sub(r'<[^>]+>', '', first_td.group(1)).strip()
        name = re.sub(r'\s+', ' ', name).strip()
        if not name or len(name) > 100: continue
        reefs.append({"name": name[:80], "coords": [round(lat, 5), round(lon, 5)], "state": state})
    return reefs

# ------------------ Florida (FWC JSON) ------------------
fl_reefs = []
fl_json_path = STAGE / "fl_reefs_all.json"
if fl_json_path.exists():
    d = json.load(open(fl_json_path))
    for feat in d.get("features", []):
        a = feat.get("attributes", {})
        try:
            lat = float(a.get("Lat_DD")); lon = float(a.get("Long_DD"))
        except (TypeError, ValueError):
            continue
        if abs(lat) > 90 or abs(lon) > 180: continue
        name = a.get("Name") or a.get("DeployID") or f"FWC-{a.get('OBJECTID')}"
        fl_reefs.append({"name": str(name)[:80], "coords": [round(lat, 5), round(lon, 5)], "state": "FL"})
    print(f"FL (FWC API): {len(fl_reefs)} reefs")

# ------------------ Atlantic states from TidesPro ------------------
sources = [
    ("NJ", "https://www.tidespro.com/fishing/us/new-jersey/new-jersey-artificial-reefs"),
    ("DE", "https://www.tidespro.com/fishing/us/delaware/delaware-artificial-reefs"),
    ("MD", "https://www.tidespro.com/fishing/us/maryland/maryland-artificial-reefs"),
    ("VA", "https://www.tidespro.com/fishing/us/virginia/virginia-artificial-reefs"),
    ("NC", "https://www.tidespro.com/fishing/us/north-carolina/outer-banks"),
    ("NC", "https://www.tidespro.com/fishing/us/north-carolina/onslow-bay"),
    ("NC", "https://www.tidespro.com/fishing/us/north-carolina/estuarine"),
    ("SC", "https://www.tidespro.com/fishing/us/south-carolina/south-carolina-reefs"),
    ("GA", "https://www.tidespro.com/fishing/us/georgia/georgia-reefs"),
]
all_reefs = list(fl_reefs)
for state, url in sources:
    try:
        html = fetch(url, timeout=90)
        reefs = parse_tidespro(html, state)
        all_reefs.extend(reefs)
        print(f"{state} ({url.split('/')[-1]}): {len(reefs)} reefs")
        time.sleep(0.4)
    except Exception as e:
        print(f"{state} FAILED: {e}")

# Dedupe on (state, 4-decimal coord grid, name-prefix)
seen = set()
final = []
for r in all_reefs:
    key = (r["state"], round(r["coords"][0], 4), round(r["coords"][1], 4), r["name"][:24].lower())
    if key in seen: continue
    seen.add(key)
    final.append(r)
print(f"\nFinal (deduped): {len(final)} reefs")
counts = Counter(r["state"] for r in final)
print("\nBy state:")
for s, c in sorted(counts.items()):
    print(f"  {s}: {c}")

out = {
    "source": "TidesPro state registries (NJ/DE/MD/VA/NC/SC/GA) + FWC ArcGIS official API (FL)",
    "fetched_at": time.strftime("%Y-%m-%d"),
    "counts": dict(counts),
    "reefs": final,
}
outpath = BASE / "data" / "state_reefs_atlantic.json"
outpath.write_text(json.dumps(out, separators=(",", ":")))
print(f"\nWrote {outpath} ({outpath.stat().st_size:,} bytes)")
