#!/usr/bin/env python3
"""
v24.95 (Randy 2026-09-04) — iNaturalist BAIT-fish sighting harvester.

Companion to `inaturalist_whale_harvest.py`. iNat has real photo-verified
observations of menhaden schools, mullet runs, silversides, ballyhoo, etc.
Each observation gives us a dated, geo-tagged data point for the model's
`baitBoost` signal — currently underused for southern regions because the
OTW/Fisherman auto-extract only fires when bait words appear in the SAME
report that names a zone.

Bait taxa harvested (from https://api.inaturalist.org):
  · Atlantic menhaden (Brevoortia tyrannus)  — 3966 iNat obs
  · Striped mullet (Mugil cephalus)          — 9503 iNat obs
  · Bay anchovy (Anchoa mitchilli)           — 1228 iNat obs
  · Atlantic silverside (Menidia menidia)    — 2575 iNat obs
  · Atlantic mackerel (Scomber scombrus)     — 1571 iNat obs
  · Ballyhoo (Hemiramphidae family)          — 4184 iNat obs
  · Atlantic bumper (Chloroscombrus)         — 856 iNat obs

For each region:
  - Query iNat for each taxon in the region's bbox, last 21 days
  - Attribute each obs to the nearest zone (within 30 nm)
  - Add as bait_intel entry with date, type (species), zones, source=iNat

Called from build-inlined.py at nightly build time; safe to re-run manually.
"""
from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path("/root/fish-finder")
INAT_URL = "https://api.inaturalist.org/v1/observations"

REGIONS = [
    # (region_key, zones_json, bbox: [S, W, N, E])
    ("northeast",      "zones.json",                 [40.5, -74.0, 42.0, -69.0]),
    ("mid_atlantic",   "zones_mid_atlantic.json",    [36.5, -75.5, 40.5, -74.0]),
    ("south_atlantic", "zones_south_atlantic.json",  [28.0, -82.0, 35.2, -75.5]),
    ("gulf",           "zones_gulf.json",            [28.5, -90.5, 30.85, -84.5]),
    ("south_florida",  "zones_south_florida.json",   [24.3, -84.5, 28.7,  -79.7]),
]

# Bait taxa (verified 2026-09-04 via iNat taxon search)
BAIT_TAXA = [
    (51363,  "menhaden",           "Atlantic menhaden (bunker)"),
    (106222, "mullet",             "Striped mullet"),
    (94070,  "anchovy",            "Bay anchovy"),
    (146827, "silversides",        "Atlantic silverside"),
    (118678, "mackerel",           "Atlantic mackerel"),
    (57298,  "ballyhoo",           "Ballyhoo family"),
    (179633, "bumper",             "Atlantic bumper"),
]

MAX_ATTRIBUTE_NM = 30  # observation → zone attribution radius


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3440.065
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl/2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def fetch_taxon_in_bbox(taxon_id: int, bbox: list[float], days: int = 21) -> list[dict]:
    """v24.105 — added 3-attempt retry with backoff. iNat public API 429s
    routinely during harvester runs (7 taxa × 5 regions = 35 calls per
    build); before, a single 429 dropped a full taxon-in-region for that
    day. Also: log the actual error class + last URL so failures are
    diagnosable (was silently returning []).
    """
    import time as _time
    d1 = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    s, w, n, e = bbox
    params = (
        f"taxon_id={taxon_id}&swlat={s}&swlng={w}&nelat={n}&nelng={e}"
        f"&d1={d1}&per_page=200&order_by=observed_on&order=desc&geo=true"
    )
    url = f"{INAT_URL}?{params}"
    backoffs = [3, 10]
    last_err = None
    for attempt in range(3):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 fish-finder-bait"})
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                data = json.loads(r.read())
            return data.get("results", []) or []
        except Exception as e:
            last_err = e
            if attempt < len(backoffs):
                _time.sleep(backoffs[attempt])
    # Fell through — log once so we can see a persistent iNat outage.
    print(f"    iNat bait fetch failed (taxon={taxon_id}, region={s},{w}→{n},{e}): {type(last_err).__name__}: {last_err}")
    return []


def nearest_zone(lat: float, lon: float, zones: list[dict]) -> tuple[str | None, float]:
    best = (None, 1e9)
    for z in zones:
        c = z.get("center")
        if not c or len(c) < 2:
            continue
        d = haversine_nm(lat, lon, c[0], c[1])
        if d < best[1]:
            best = (z.get("id"), d)
    return best


def process_region(region_key: str, zones_fname: str, bbox: list[float]) -> tuple[int, int]:
    """Returns (n_added, n_fetched_obs)."""
    zones_path = BASE / "data" / zones_fname
    if not zones_path.exists():
        print(f"  {region_key}: no zones file")
        return 0, 0
    d = json.load(open(zones_path))
    zones = d.get("zones", [])

    existing = d.get("bait_intel") or []
    # Dedupe key: (species_label, date, first_zone_id)
    existing_keys = {
        (b.get("type", b.get("bait", "")), b.get("date"), (b.get("zones") or [None])[0])
        for b in existing
    }

    total_fetched = 0
    added = 0
    for taxon_id, short, label in BAIT_TAXA:
        obs = fetch_taxon_in_bbox(taxon_id, bbox)
        total_fetched += len(obs)
        for o in obs:
            when = o.get("observed_on") or o.get("observed_on_string")
            if not when:
                continue
            geo = o.get("geojson") or {}
            coords = geo.get("coordinates")
            if not coords or len(coords) < 2:
                continue
            lon, lat = coords[0], coords[1]  # geojson is [lon, lat]
            zid, dist = nearest_zone(lat, lon, zones)
            if zid is None or dist > MAX_ATTRIBUTE_NM:
                continue
            when_iso = when[:10]
            key = (short, when_iso, zid)
            if key in existing_keys:
                continue
            existing_keys.add(key)
            existing.append({
                "date":       when_iso,
                "type":       short,
                "species":    label,
                "zones":      [zid],
                "source":     "iNaturalist",
                "source_url": f"https://www.inaturalist.org/observations/{o.get('id')}",
                "lat":        round(lat, 4),
                "lon":        round(lon, 4),
                "dist_nm":    round(dist, 1),
            })
            added += 1

    d["bait_intel"] = existing
    zones_path.write_text(json.dumps(d, indent=2))
    print(f"  {region_key:15}: {total_fetched:>3} iNat obs · +{added} new bait_intel entries")
    return added, total_fetched


def run(verbose: bool = False) -> dict:
    print("\n=== iNaturalist BAIT harvest ===")
    totals = {"added": 0, "fetched": 0}
    error = None
    try:
        for reg, fn, bbox in REGIONS:
            a, f = process_region(reg, fn, bbox)
            totals["added"] += a
            totals["fetched"] += f
        print(f"\n  TOTAL: +{totals['added']} bait_intel entries from {totals['fetched']} iNat observations across 5 regions")
    except Exception as e:
        error = str(e)
        print(f"  iNat bait harvest FAILED: {e}")
    # v24.100 — source-health record
    try:
        import source_health as _sh  # avoid circular import at module load
        _sh.record("inat_bait", rows=totals["fetched"], ok=(error is None), error=error)
    except Exception:
        pass
    return totals


if __name__ == "__main__":
    run(verbose=True)
