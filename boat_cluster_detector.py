#!/usr/bin/env python3
"""Fish Finder — boat-cluster candidate detector (v23.48, Randy 2026-08-15).

Randy: "You should probably only be looking for those groupings of boats
at, like, twelve o'clock. And if you see a grouping … if you think it's
a grouping of boats, could you like circle it and make it obvious?"

**What this does:**

Fetches HLS Sentinel-30m (or MODIS Terra 250m fallback) satellite tiles
over Randy's high-value tuna zones — the ones where a boat CLUSTER on
a canyon lip or a wreck actually happens — and does simple bright-
anomaly-in-water detection. Any tight cluster of anomalously-bright
pixels sitting on open water becomes a **candidate**. We are HONEST
about what this can and can't do:

  - HLS Sentinel-30m is 30m/pixel native. A cluster of 200 rec-tuna
    boats packed into ~500m of a canyon lip fills roughly 250 pixels
    of imagery (16x16 area). That's real detectable signal.

  - MODIS Terra 250m/pixel is the daily-full-coverage fallback. At
    250m, the same 200-boat cluster is 2 pixels — barely detectable.
    We tag MODIS candidates as LOW confidence.

  - Cloud cover blocks everything. If the tile is dominated by clouds
    (>60% bright pixels) we skip it and log "cloudy" for that zone.

  - Sun glint, whitecaps, and cloud edges are the main false positive
    source. We filter by requiring the "background" 20x20 neighborhood
    to be watery (blue channel dominates) and the anomaly cluster to
    be tight (spread < ~10 pixels).

  - No signup / no API key. All tiles come from the free NASA GIBS
    WMTS endpoint we already probe for the satellite view layer.

**Output:** `/root/fish-finder/data/boat_clusters.json` — a list of
candidates with lat/lon, confidence tier, source imagery date, pixel
extent, and honest notes. `build-inlined.py` embeds this into the HTML
so the map's Clusters layer can circle each candidate.

**When to run:** every nightly build. The build wrapper calls
`detect_boat_clusters()` after satellite metadata is probed. Manually:
    python3 /root/fish-finder/boat_cluster_detector.py --verbose
"""
from __future__ import annotations

import argparse
import datetime
import io
import json
import math
import pathlib
import sys
import urllib.error
import urllib.request

try:
    from PIL import Image
    import numpy as np
except ImportError:
    print("ERROR: Pillow + numpy required. Install: pip install --break-system-packages Pillow numpy")
    sys.exit(1)

# --- CONFIG ------------------------------------------------------------------

# v24.77 (Randy 2026-09-03): boat cluster detector is now MULTI-REGION.
# Loads zones from all 5 region data files and scans a curated set of
# high-value spots per region — canyons, rigs, humps, and famous ledges
# where a real fleet cluster actually happens.
ZONE_FILES = [
    pathlib.Path("/root/fish-finder/data/zones.json"),                    # NE
    pathlib.Path("/root/fish-finder/data/zones_mid_atlantic.json"),
    pathlib.Path("/root/fish-finder/data/zones_south_atlantic.json"),
    pathlib.Path("/root/fish-finder/data/zones_gulf.json"),
    pathlib.Path("/root/fish-finder/data/zones_south_florida.json"),
]
OUT_JSON = pathlib.Path("/root/fish-finder/data/boat_clusters.json")

# High-value zones to scan per region — the ones where fleet clustering
# actually happens. Only offshore/midshore/canyon spots; skip inshore
# (chop + land-water boundary → false positives).
SCAN_ZONE_IDS = [
    # Northeast (v23.48 originals)
    "coimbra_wreck", "butterfish_hole", "hudson_canyon", "block_canyon",
    "atlantis_canyon", "tuna_ridge", "s_block_nearshore", "habs_ledge",
    "the_fishtails", "coxes_ledge_se",
    # Mid-Atlantic (v24.77) — NJ canyons + wreck complexes
    "hudson_canyon_midatl", "baltimore_canyon", "washington_canyon",
    "manasquan_ridge", "sea_girt_reef", "barnegat_ridge",
    # SE Coast (v24.77) — offshore ledges + tuna spots
    "the_big_rock", "the_same_ol", "nc_steeples", "nc_manning_line",
    "sc_georgetown_hole", "sc_charleston_bump", "sc_deli_belly",
    "ga_grays_reef", "nefl_ledge_20fathom", "frying_pan_shoals",
    # Gulf (v24.77) — canyon rigs + deep humps
    "petronius_platform", "ram_powell_tlp", "marlin_tlp", "devils_tower_spar",
    "the_nipple", "the_elbow", "the_spur", "callon_jacket",
    "trysler_grounds", "midnight_lump",
    # S. Florida (v24.77) — Gulf Stream humps + Bahamas edge
    "islamorada_hump", "mk_east_hump", "marathon_west_hump",
    "wood_wall", "islamorada_409_hole", "mia_stream_edge",
    "pb_west_end_bank", "ftl_bahamas_edge", "gs_sword_grounds_sfl",
    "gs_bimini_edge", "lk_riley_hump", "swfl_pulley_ridge",
]

# GIBS WMTS templates. Same endpoints the satellite-view layer probes.
GIBS_HLS_URL = ("https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/"
                "HLS_S30_Nadir_BRDF_Adjusted_Reflectance/default/{date}/"
                "GoogleMapsCompatible_Level12/{z}/{y}/{x}.jpg")
GIBS_MODIS_URL = ("https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/"
                  "MODIS_Terra_CorrectedReflectance_TrueColor/default/{date}/"
                  "GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg")

# Slippy-map math (standard XYZ → lat/lon and back)
def latlon_to_tile(lat, lon, z):
    lat_rad = math.radians(lat)
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return (x, y)

def tile_to_latlon(x, y, z):
    n = 2 ** z
    lon = x / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    lat = math.degrees(lat_rad)
    return (lat, lon)

def pixel_to_latlon(tile_x, tile_y, px, py, z, tile_size=256):
    """Convert a pixel within a tile to lat/lon."""
    lat_top, lon_left = tile_to_latlon(tile_x, tile_y, z)
    lat_bot, lon_right = tile_to_latlon(tile_x + 1, tile_y + 1, z)
    lon = lon_left + (px / tile_size) * (lon_right - lon_left)
    lat = lat_top + (py / tile_size) * (lat_bot - lat_top)
    return (lat, lon)


# --- FETCH -------------------------------------------------------------------

def fetch_tile(url, timeout=15):
    """Return PIL image or None. Skip small-body responses (GIBS returns a
    ~200-byte "no data" JPG when the tile is empty)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "FishFinderBot/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status != 200: return None
            body = r.read()
            if len(body) < 10_000: return None
            return Image.open(io.BytesIO(body)).convert("RGB")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return None


def probe_latest_hls_date():
    """Walk backward from yesterday to find the most recent date with HLS
    coverage over Randy's core fishing area. Returns ISO date or None."""
    today = datetime.date.today()
    # Test tile over Coxes Ledge / south of Block — same as build-inlined.py
    # probe. Zoom 8, y=95, x=76.
    for days_ago in range(1, 9):
        d = (today - datetime.timedelta(days=days_ago)).isoformat()
        url = (f"https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/"
               f"HLS_S30_Nadir_BRDF_Adjusted_Reflectance/default/{d}/"
               f"GoogleMapsCompatible_Level12/8/95/76.jpg")
        if fetch_tile(url, timeout=8) is not None:
            return d
    return None


# --- DETECTION ---------------------------------------------------------------

def detect_bright_anomalies(img, min_cluster_pixels=25, brightness_delta=60):
    """Simple bright-anomaly-in-water detection on a single PIL tile.

    Returns list of clusters, each a dict {px, py, size_px, confidence}.
    The confidence is a rough tier ('low' / 'medium' / 'high') based on
    cluster size and how clean the surrounding water was.

    Algorithm:
      1. Convert to numpy array (H, W, 3).
      2. Compute per-pixel luminance = 0.3R + 0.59G + 0.11B.
      3. For each pixel, subtract the median luminance in a 21x21 window.
         (Using a downsampled block-median as an approximation to keep this
         fast and dependency-light — no scipy needed.)
      4. Anomaly mask = (luminance > local_median + brightness_delta)
                    AND (pixel is watery: blue >= red AND blue >= green)
                    AND (local median is watery: local blue > local red).
      5. Connected-component label the mask with a simple 4-neighbor flood
         fill (implemented in pure numpy for speed / no scipy dep).
      6. Return clusters with min_cluster_pixels or more.
    """
    arr = np.asarray(img, dtype=np.int16)
    if arr.shape[0] < 40 or arr.shape[1] < 40:
        return []
    R, G, B = arr[..., 0], arr[..., 1], arr[..., 2]
    lum = (0.30 * R + 0.59 * G + 0.11 * B).astype(np.int16)

    # Cloud-cover guard: if the tile is >55% very bright, it's clouds. Bail.
    cloudy = (lum > 200).sum() / lum.size
    if cloudy > 0.55:
        return {"cloudy": True, "cloud_frac": float(cloudy)}

    # Local median via 16x16 block-median (fast, no scipy).
    H, W = lum.shape
    block = 16
    bh, bw = H // block, W // block
    if bh < 2 or bw < 2:
        return []
    # Reshape into blocks and take median per block
    trimmed = lum[:bh * block, :bw * block]
    blocked = trimmed.reshape(bh, block, bw, block).swapaxes(1, 2).reshape(bh, bw, -1)
    block_medians = np.median(blocked, axis=2)
    # Also compute per-block "is watery" flag: blue dominant
    b_trim = B[:bh * block, :bw * block].reshape(bh, block, bw, block).swapaxes(1, 2).mean(axis=(2, 3))
    r_trim = R[:bh * block, :bw * block].reshape(bh, block, bw, block).swapaxes(1, 2).mean(axis=(2, 3))
    watery_block = (b_trim > r_trim + 5)
    # Upsample block median back to per-pixel via nearest-neighbor
    local_median = np.kron(block_medians, np.ones((block, block), dtype=np.int16))
    local_watery = np.kron(watery_block.astype(np.uint8), np.ones((block, block), dtype=np.uint8))
    # Ensure sizes match (kron may be exact multiple already)
    local_median = local_median[:H, :W]
    local_watery = local_watery[:H, :W]

    # Anomaly mask
    per_pixel_watery = (B >= R) & (B >= G)
    anomaly = (lum > local_median + brightness_delta) & per_pixel_watery & local_watery.astype(bool)

    # Connected-component: iterative flood fill via numpy
    visited = np.zeros_like(anomaly, dtype=bool)
    clusters = []
    ys, xs = np.where(anomaly)
    for y0, x0 in zip(ys, xs):
        if visited[y0, x0]: continue
        # BFS
        stack = [(y0, x0)]
        cluster_pixels = []
        while stack:
            y, x = stack.pop()
            if y < 0 or y >= H or x < 0 or x >= W: continue
            if visited[y, x]: continue
            if not anomaly[y, x]: continue
            visited[y, x] = True
            cluster_pixels.append((y, x))
            if len(cluster_pixels) > 2000:  # runaway safety
                break
            stack.extend([(y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)])
        if len(cluster_pixels) >= min_cluster_pixels:
            ys_arr = np.array([p[0] for p in cluster_pixels])
            xs_arr = np.array([p[1] for p in cluster_pixels])
            cy = float(ys_arr.mean())
            cx = float(xs_arr.mean())
            size = len(cluster_pixels)
            # Spread: max distance from centroid
            spread = float(max(((ys_arr - cy) ** 2 + (xs_arr - cx) ** 2).max() ** 0.5, 1))
            # Confidence heuristic. Tightened v23.48 first-run analysis showed
            # medium/low tiers were dominated by cloud edges + sun glint on
            # the s_block_nearshore / habs_ledge tiles. Real boat clusters
            # are BIG (100+ px at zoom 11 ≈ 200+ boats on a canyon lip) AND
            # tight (spread < ~15 px). Anything smaller/looser is more likely
            # a whitecap or a cloud shred. Better to show 2 confident dots
            # than 50 noisy ones.
            if size >= 120 and spread < 18:
                conf = "high"
            elif size >= 60 and spread < 12:
                conf = "medium"
            else:
                conf = "low"
            clusters.append({
                "px": cx, "py": cy, "size_px": size, "spread_px": spread, "confidence": conf,
            })
    return clusters


# --- ORCHESTRATION -----------------------------------------------------------

def detect_boat_clusters(verbose=False):
    """Full pipeline. Returns dict with `date`, `candidates` list, `scan_log`
    per-zone details, and `notes` about sensor/coverage."""
    result = {
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "candidates": [],
        "scan_log": [],
        "notes": [],
    }

    # 1. Find latest HLS date with coverage
    hls_date = probe_latest_hls_date()
    if not hls_date:
        result["notes"].append("No HLS Sentinel-30m coverage in the last 8 days over Randy's area — no candidates possible from HLS this run. Consider MODIS 250m fallback in a future pass.")
        OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(result, indent=2))
        return result

    result["hls_source_date"] = hls_date
    if verbose: print(f"[boat-cluster] Using HLS Sentinel-30m from {hls_date}")

    # 2. Load zones from ALL region files + build unified zone lookup.
    # v24.77: was NE-only via ZONES_JSON. Now merges across the 5 region blobs
    # so the detector can scan spots in Mid-Atl / SE / Gulf / S. Florida.
    zone_by_id = {}
    zone_region = {}   # zone_id → source region file stem, for later debugging
    for zf in ZONE_FILES:
        if not zf.exists():
            if verbose: print(f"[boat-cluster] skip missing zone file: {zf}")
            continue
        try:
            d = json.loads(zf.read_text())
            for z in d.get("zones", []):
                zid = z.get("id")
                if not zid: continue
                zone_by_id[zid] = z
                zone_region[zid] = zf.stem
        except Exception as _e:
            if verbose: print(f"[boat-cluster] error reading {zf}: {_e}")
    if verbose: print(f"[boat-cluster] loaded {len(zone_by_id)} zones across {len(ZONE_FILES)} region files")

    # 3. Scan each zone. Zoom-11 covers ~10km / tile, native res ~40m — close
    #    to Sentinel-30m native. Grab a 2x2 tile grid centered on each zone.
    # v24.105 — parallelized 6-way across ~400 tile fetches (was sequential:
    # 44 zones × 9 tiles × ~1s = 400s+; often hit the 180s subprocess timeout).
    from concurrent.futures import ThreadPoolExecutor
    ZOOM = 11

    # Build a work list: one entry per (zone, tile-offset) to fetch.
    _fetch_jobs = []
    _zone_meta = {}
    for zid in SCAN_ZONE_IDS:
        z = zone_by_id.get(zid)
        if not z:
            result["scan_log"].append({"zone_id": zid, "status": "not_in_zones_json"})
            continue
        lat, lon = z["center"]
        tx, ty = latlon_to_tile(lat, lon, ZOOM)
        _zone_meta[zid] = {"z": z, "tx": tx, "ty": ty,
                           "tiles_scanned": 0, "cloudy_tiles": 0, "candidates": []}
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                _fetch_jobs.append((zid, tx + dx, ty + dy))

    def _fetch_and_analyze(job):
        zid, x, y = job
        url = GIBS_HLS_URL.format(date=hls_date, z=ZOOM, x=x, y=y)
        img = fetch_tile(url)
        if img is None:
            return (zid, x, y, "miss", None)
        clusters = detect_bright_anomalies(img)
        if isinstance(clusters, dict) and clusters.get("cloudy"):
            return (zid, x, y, "cloudy", None)
        return (zid, x, y, "ok", clusters)

    with ThreadPoolExecutor(max_workers=6) as ex:
        for zid, x, y, status, clusters in ex.map(_fetch_and_analyze, _fetch_jobs):
            meta = _zone_meta.get(zid)
            if not meta:
                continue
            if status == "miss":
                continue
            meta["tiles_scanned"] += 1
            if status == "cloudy":
                meta["cloudy_tiles"] += 1
                continue
            z = meta["z"]
            for c in clusters:
                lat_c, lon_c = pixel_to_latlon(x, y, c["px"], c["py"], ZOOM)
                meta["candidates"].append({
                    "lat": round(lat_c, 5),
                    "lon": round(lon_c, 5),
                    "confidence": c["confidence"],
                    "size_px": c["size_px"],
                    "spread_px": round(c["spread_px"], 1),
                    "nearest_zone_id": zid,
                    "nearest_zone_name": z["name"],
                    "source": "HLS_Sentinel_30m",
                    "source_date": hls_date,
                    "source_tile": {"z": ZOOM, "x": x, "y": y},
                })

    for zid, meta in _zone_meta.items():
        z = meta["z"]
        result["scan_log"].append({
            "zone_id": zid,
            "zone_name": z["name"],
            "tiles_scanned": meta["tiles_scanned"],
            "cloudy_tiles": meta["cloudy_tiles"],
            "candidates_found": len(meta["candidates"]),
        })
        result["candidates"].extend(meta["candidates"])
        if verbose:
            status = f"tiles={meta['tiles_scanned']}, cloudy={meta['cloudy_tiles']}, cand={len(meta['candidates'])}"
            print(f"[boat-cluster] {zid:30s} {status}")

    # 4. Post-process: dedup nearby candidates (< 1nm apart) → keep highest-conf
    result["candidates"] = _dedup_candidates(result["candidates"])
    # 5. Filter to HIGH confidence only for the map. Cloud edges + sun glint
    #    generate a lot of medium hits that Randy would (correctly) ignore.
    #    Better to show 2 real dots than 40 noisy ones. Full unfiltered list
    #    still lands in `all_candidates` for future accuracy analysis.
    result["all_candidates"] = result["candidates"]
    result["candidates"] = [c for c in result["candidates"] if c["confidence"] == "high"]
    result["notes"].append(f"Showing HIGH-confidence candidates only ({len(result['candidates'])} of {len(result['all_candidates'])}). Medium/low tiers preserved in `all_candidates` for future tuning; suppressed on the map to keep noise down. A real boat fleet cluster on a canyon lip is ~120+ Sentinel-30m pixels tightly grouped; smaller/looser signals are usually clouds or sun glint.")

    # 6. Merge in captain-sourced fleet mentions from OTW / Fisherman weekly
    #    reports (Randy 2026-08-15: "also pull fishing fleet info from other
    #    sources"). Captain reports fill the gaps satellite imagery leaves
    #    behind — cloud-covered days, orbital-swath misses, and spots the
    #    satellite CAN see but where the fleet is a smaller cluster below
    #    our tight-detector threshold.
    try:
        from fleet_intel_harvester import harvest_fleet_intel
        if verbose: print("[boat-cluster] Harvesting OTW captain fleet mentions...")
        fi = harvest_fleet_intel(verbose=False)
        result["candidates"].extend(fi.get("candidates", []))
        result["captain_source_count"] = len(fi.get("candidates", []))
        result["captain_source_fetched"] = fi.get("fetched_reports", 0)
        if verbose:
            print(f"[boat-cluster]   + {result['captain_source_count']} captain-sourced candidates from {result['captain_source_fetched']} OTW reports")
    except Exception as e:
        result["notes"].append(f"Captain-report harvester failed ({e}). Satellite-only candidates this run.")
        if verbose: print(f"[boat-cluster] Captain harvester failed: {e}")

    # 7. Persist
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, indent=2))
    if verbose:
        print(f"[boat-cluster] Wrote {OUT_JSON} — {len(result['candidates'])} final candidates")
    return result


def _dedup_candidates(candidates, dedup_nm=3.5):
    """Collapse candidates within `dedup_nm` of each other into one (keep the
    highest-confidence one). O(n^2) is fine given n is tiny.
    v23.55 (Randy 2026-08-16): bumped 1nm → 3.5nm after Randy noticed the
    map showed 5 stacked POSSIBLE FLEET pills all on top of each other in
    the S-of-Block / Block Reefs area. A single fleet on a canyon lip
    typically spreads across 1-3nm as boats work the break, so pixel
    clusters within 3.5nm should collapse to one candidate."""
    if not candidates: return []
    kept = []
    conf_rank = {"low": 0, "medium": 1, "high": 2}
    for c in sorted(candidates, key=lambda x: -conf_rank[x["confidence"]]):
        too_close = False
        for k in kept:
            dlat = c["lat"] - k["lat"]
            dlon = (c["lon"] - k["lon"]) * math.cos(math.radians((c["lat"] + k["lat"]) / 2))
            dist_nm = (dlat ** 2 + dlon ** 2) ** 0.5 * 60
            if dist_nm < dedup_nm:
                too_close = True; break
        if not too_close:
            kept.append(c)
    return kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()
    result = detect_boat_clusters(verbose=args.verbose)
    print(f"\nFinal: {len(result['candidates'])} candidate cluster(s), source: {result.get('hls_source_date', 'none')}")
    if result.get('notes'):
        for n in result['notes']:
            print(f"  · {n}")


if __name__ == "__main__":
    main()
