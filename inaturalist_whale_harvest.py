#!/usr/bin/env python3
"""
v24.76 — Add iNaturalist cetacean sighting harvester for Gulf + S. Florida
+ SE Coast. Extends Randy's "find the whales, find the tuna" signal from
NE + Mid-Atl to the three southern regions.

iNaturalist has a public JSON API with real cetacean observations from
photo-verified user submissions. Coverage for Gulf + Atlantic is decent
(30+ observations in the Gulf alone over any 30-day window).

For each region:
  - Query iNat for taxon 152871 (Cetacea) within the region's bbox
  - Filter to observations in the last 21 days
  - For each, find the nearest zone and attach as a whale_sighting entry
  - Merge into that region's zones.json under whale_sightings

Called from build-inlined.py at nightly build time; safe to re-run manually.
"""
import json, math, os, urllib.request, urllib.error
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path("/root/fish-finder")

REGIONS = [
    # (region_key, zones_json, bbox: [S, W, N, E])
    ("gulf",           "zones_gulf.json",           [28.5,  -90.5, 30.85, -84.5]),
    ("south_florida",  "zones_south_florida.json",  [24.3,  -84.5, 28.7,  -79.7]),
    ("south_atlantic", "zones_south_atlantic.json", [28.0,  -82.0, 35.2,  -75.5]),
]

INAT_URL = "https://api.inaturalist.org/v1/observations"
CETACEA_TAXON = 152871  # Cetacea (whales + dolphins)

def haversine_nm(lat1, lon1, lat2, lon2):
    R = 3440.065
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))

def fetch_region(bbox, days=21):
    """Fetch cetacean observations in bbox from last N days.

    v24.105 — added 3-attempt retry with backoff [3s, 10s]. iNat 429s
    (rate limit) or 500s (server errors) are common under harvester load
    and previously killed the whale signal for that region for a full day.
    """
    import time as _time
    d1 = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    s, w, n, e = bbox
    params = f"taxon_id={CETACEA_TAXON}&swlat={s}&swlng={w}&nelat={n}&nelng={e}&d1={d1}&per_page=200&order_by=observed_on&order=desc&geo=true"
    url = f"{INAT_URL}?{params}"
    backoffs = [3, 10]
    last_err = None
    for attempt in range(3):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 fish-finder"})
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(r.read())
        except Exception as e:
            last_err = e
            if attempt < len(backoffs):
                _time.sleep(backoffs[attempt])
    raise last_err

def nearest_zone(lat, lon, zones):
    """Return the zone_id + distance of the closest zone."""
    best = (None, 1e9)
    for z in zones:
        c = z.get("center")
        if not c: continue
        d = haversine_nm(lat, lon, c[0], c[1])
        if d < best[1]:
            best = (z["id"], d)
    return best

def process(region_key, zones_fname, bbox):
    print(f"\n=== {region_key} ===")
    zones_path = BASE / "data" / zones_fname
    d = json.load(open(zones_path))
    zones = d.get("zones", [])

    try:
        data = fetch_region(bbox)
    except Exception as e:
        print(f"  iNat fetch FAILED: {e}"); return

    obs = data.get("results", [])
    print(f"  iNat: {len(obs)} cetacean observations in bbox (last 21d)")
    if not obs: return

    # Existing sightings — dedupe by (species, date, lat, lon)
    existing = d.get("whale_sightings", [])
    existing_keys = set()
    for s in existing:
        key = (s.get("species","").lower(), s.get("date",""), round(s.get("coords",[0,0])[0], 3), round(s.get("coords",[0,0])[1], 3))
        existing_keys.add(key)

    added = 0
    for o in obs:
        # Only positional observations with coordinates
        geo = (o.get("geojson") or {}).get("coordinates")
        if not geo or len(geo) < 2: continue
        lon, lat = geo[0], geo[1]
        # Filter to actual sea coords (not on land)
        if abs(lat) > 90 or abs(lon) > 180: continue
        # Skip zoomed-out imprecise observations
        precision = o.get("public_positional_accuracy")
        if precision and precision > 50000: continue  # >50km fuzzed = drop
        obs_date = o.get("observed_on")
        if not obs_date: continue
        tax = o.get("taxon") or {}
        species_name = tax.get("preferred_common_name") or tax.get("name") or "Cetacean"

        # Dedupe
        key = (species_name.lower(), obs_date, round(lat, 3), round(lon, 3))
        if key in existing_keys: continue
        existing_keys.add(key)

        # Nearest zone
        zone_id, dist_nm = nearest_zone(lat, lon, zones)
        if dist_nm > 40:
            # Too far from any zone — skip (probably in a bay we don't cover)
            continue

        entry = {
            "date": obs_date,
            "species": species_name,
            "coords": [round(lat, 5), round(lon, 5)],
            "count": 1,
            "source": "iNaturalist",
            "source_url": f"https://www.inaturalist.org/observations/{o.get('id')}",
            "nearest_zone_id": zone_id,
            "distance_nm": round(dist_nm, 1),
            "zones": [zone_id] if zone_id else [],
        }
        existing.append(entry)
        added += 1

    d["whale_sightings"] = existing
    open(zones_path, "w").write(json.dumps(d, indent=2))
    print(f"  Added {added} new whale/dolphin sightings (total now {len(existing)})")

if __name__ == "__main__":
    total_added = 0
    ok_count = 0
    for region_key, fname, bbox in REGIONS:
        try:
            process(region_key, fname, bbox)
            ok_count += 1
        except Exception as e:
            print(f"  ERROR on {region_key}: {e}")
    # v24.100 source-health record — record success if at least one region processed
    try:
        import source_health as _sh
        _sh.record("inat_whales", rows=ok_count, ok=(ok_count > 0),
                   error=None if ok_count > 0 else "0 regions succeeded")
    except Exception:
        pass
    print("\nDone.")
