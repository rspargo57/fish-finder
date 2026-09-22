#!/usr/bin/env python3
"""
v24.84 — NDBC buoy real-time observation harvester.

Fetches latest wind/wave/water-temp/pressure from NOAA NDBC buoys curated
per Fish Finder region. Gives Randy actual observed data offshore to
gut-check the modeled Open-Meteo + NOAA marine forecasts.

Output: data/ndbc_buoys.json — bundled into the map at build time and
rendered as small colored dots on the map (toggle from layer rail).
"""
import json, urllib.request, urllib.error, pathlib, datetime, re, math
from concurrent.futures import ThreadPoolExecutor

BASE = pathlib.Path("/root/fish-finder")
OUT = BASE / "data" / "ndbc_buoys.json"

# Curated buoys per region — includes name + coords + region for map filtering.
# Coordinates from NDBC station pages.
STATIONS = [
    # NORTHEAST
    ("44017", "Montauk Point, NY (23 nm SW)",       40.694, -72.048, "northeast"),
    ("44025", "Long Island 33nm S of Islip, NY",    40.251, -73.164, "northeast"),
    ("44066", "Texas Tower #4 (75 nm SE Long Island)", 39.583, -72.599, "northeast"),
    ("44008", "Nantucket 54 nm SE of Nantucket, MA", 40.502, -69.248, "northeast"),
    ("44011", "Georges Bank, MA",                   41.098, -66.582, "northeast"),
    ("44097", "Block Island, RI (SE)",              40.968, -71.129, "northeast"),
    ("44005", "Gulf of Maine (78 nm E of Portsmouth NH)", 43.204, -69.129, "northeast"),

    # MID-ATLANTIC
    ("44009", "Delaware Bay (26 nm SE Cape May)",   38.464, -74.703, "midatl"),
    ("44014", "Virginia Beach (64 nm E)",           36.611, -74.842, "midatl"),
    ("41001", "East Hatteras (150 nm E of Cape Hatteras)", 34.724, -72.317, "midatl"),
    ("44100", "Duck, NC FRF Waverider",             36.257, -75.591, "midatl"),

    # SE COAST
    ("41013", "Frying Pan Shoals, NC",              33.436, -77.743, "south_atlantic"),
    ("41008", "Grays Reef, GA",                     31.402, -80.869, "south_atlantic"),
    ("41004", "Edisto, SC (41 nm SE Charleston)",   32.502, -79.099, "south_atlantic"),
    ("41112", "Fernandina Beach, FL (offshore)",    30.709, -81.293, "south_atlantic"),
    ("41010", "Canaveral East, FL (120 nm E)",      28.906, -78.485, "south_atlantic"),

    # GULF
    ("42039", "Pensacola area (SE of Pensacola)",   28.788, -86.008, "gulf"),
    ("42040", "Mobile South (Mobile South)",        29.212, -88.226, "gulf"),
    ("42003", "East Gulf (280 nm S Panama City)",   25.925, -85.615, "gulf"),
    ("42001", "Mid Gulf (Mid Gulf)",                25.888, -89.658, "gulf"),
    ("42012", "Orange Beach, AL (44 nm SE)",        30.065, -87.555, "gulf"),
    ("42036", "West Tampa (112 nm WNW Tampa)",      28.501, -84.508, "gulf"),

    # S. FLORIDA
    ("41009", "Canaveral, FL (20 nm E)",            28.523, -80.166, "south_florida"),
    ("41113", "Cape Canaveral Nearshore",           28.400, -80.534, "south_florida"),
    ("41114", "Fort Pierce, FL (offshore)",         27.552, -80.230, "south_florida"),
    ("41116", "Jupiter Inlet Waverider",            27.076, -79.997, "south_florida"),
    ("42099", "Key West / Dry Tortugas",            27.339, -83.741, "south_florida"),
    ("41010", "Key West Approach (shared)",         28.906, -78.485, "south_florida"),
]

def _fetch(station_id, timeout=8):
    """Fetch latest realtime observation. Returns dict or None."""
    url = f"https://www.ndbc.noaa.gov/data/realtime2/{station_id}.txt"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 fish-finder"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        return None
    lines = [L for L in txt.splitlines() if L and not L.startswith("#")]
    if not lines: return None
    # First non-header line = most recent observation
    parts = lines[0].split()
    if len(parts) < 15: return None
    def _f(v):
        if v == "MM" or v == "": return None
        try: return float(v)
        except: return None
    obs = {
        "yr": parts[0], "mo": parts[1], "dy": parts[2], "hr": parts[3], "mn": parts[4],
        "wdir_deg": _f(parts[5]),      # wind direction
        "wspd_ms":  _f(parts[6]),      # wind speed m/s
        "gust_ms":  _f(parts[7]),      # gusts
        "wvht_m":   _f(parts[8]),      # significant wave height (meters)
        "dpd_sec":  _f(parts[9]),      # dominant wave period
        "apd_sec":  _f(parts[10]),     # average wave period
        "mwd_deg":  _f(parts[11]),     # mean wave direction
        "pres_hpa": _f(parts[12]),     # pressure
        "atmp_c":   _f(parts[13]),     # air temp
        "wtmp_c":   _f(parts[14]),     # water temp
    }
    # Derive user-friendly units
    if obs["wspd_ms"] is not None: obs["wspd_mph"] = round(obs["wspd_ms"] * 2.23694, 1)
    if obs["gust_ms"] is not None: obs["gust_mph"] = round(obs["gust_ms"] * 2.23694, 1)
    if obs["wvht_m"]  is not None: obs["wvht_ft"]  = round(obs["wvht_m"] * 3.28084, 1)
    if obs["wtmp_c"]  is not None: obs["wtmp_f"]   = round(obs["wtmp_c"] * 9/5 + 32, 1)
    if obs["atmp_c"]  is not None: obs["atmp_f"]   = round(obs["atmp_c"] * 9/5 + 32, 1)
    # ISO timestamp
    try:
        obs["observed_at"] = f"{obs['yr']}-{obs['mo']}-{obs['dy']}T{obs['hr']}:{obs['mn']}Z"
    except: pass
    return obs

def harvest():
    def _worker(spec):
        sid, name, lat, lon, region = spec
        obs = _fetch(sid)
        return {"id": sid, "name": name, "lat": lat, "lon": lon, "region": region, "obs": obs}

    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(_worker, STATIONS))

    ok = [r for r in results if r["obs"]]
    fail = [r for r in results if not r["obs"]]
    print(f"NDBC harvest: {len(ok)}/{len(STATIONS)} stations OK, {len(fail)} failed")
    if fail:
        print(f"  Failed: {', '.join(r['id'] for r in fail)}")
    from collections import Counter
    by_region = Counter(r['region'] for r in ok)
    print(f"  By region: {dict(by_region)}")

    out = {
        "fetched_at": datetime.datetime.utcnow().isoformat() + "Z",
        "stations": ok,
        "counts": dict(by_region),
    }
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    print(f"Wrote {OUT} ({OUT.stat().st_size:,} bytes)")

if __name__ == "__main__":
    harvest()
