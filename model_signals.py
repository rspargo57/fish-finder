#!/usr/bin/env python3
"""Model signal helpers computed at build time.

These functions post-process the raw data (SST grid, chlorophyll grid,
current arrows, pressure samples, archive history) into higher-order
signals that feed effectiveHeat() on the client:

  - compute_sst_gradient_field:  local gradient magnitude of SST across
    the grid, returned as a list of (lat, lon, gradient_f_per_nm) points
    where the gradient exceeds a threshold. Used by sstGradientBoost().

  - compute_chla_gradient_field: same for chlorophyll (mg/m³ per nm).

  - compute_current_convergence:  scan a currents arrow grid, find pairs of
    nearby arrows that point toward each other. Returns lat/lon of
    convergence points. Used by currentConvergenceBoost().

  - compute_persistence_flags:   read the last N archive snapshots. For
    each of today's SST contour lines, check if similar contours passed
    through the same area for 3+ consecutive days. Returns a list of
    "persistent break" lat/lon points. Used by persistenceBoost().

  - analyze_pressure_trend:     read the last 24-48h of pressure samples.
    Detect "post-front" conditions (sharp rise) and "falling-fast" (bite-on)
    conditions. Returns a summary dict with derived state.

All functions are pure-Python (no scipy) — small NumPy usage for the SST
grid math. Designed to be cheap: sub-second even for the full NE bbox.
"""
import datetime
import json
import math
import pathlib
from typing import List, Dict, Any, Tuple, Optional


# ------------------------------- gradients -------------------------------

def _nm_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Flat-earth nautical mile distance — fine at Northeast scale."""
    dlat = (lat2 - lat1) * 60.0
    dlon = (lon2 - lon1) * 60.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.sqrt(dlat * dlat + dlon * dlon)


def compute_sst_gradient_field(sst_rows: List[Tuple[float, float, float]],
                                threshold_f_per_nm: float = 1.0
                                ) -> List[Dict[str, Any]]:
    """Given the parsed SST CSV rows [(lat, lon, sst_C), ...], compute a
    gradient magnitude at each grid cell (in °F per nautical mile) and
    return only points where |gradient| >= threshold.

    Hard breaks (>=2°F/nm) concentrate more bait than soft breaks. Return
    list of {"lat", "lon", "gradient_f_per_nm"} for use in the client.
    """
    if not sst_rows:
        return []
    # Reshape into 2D grid
    lats = sorted({r[0] for r in sst_rows})
    lons = sorted({r[1] for r in sst_rows})
    lat_i = {v: i for i, v in enumerate(lats)}
    lon_i = {v: i for i, v in enumerate(lons)}
    grid_c = [[None] * len(lons) for _ in range(len(lats))]
    for lat, lon, sst_c in sst_rows:
        grid_c[lat_i[lat]][lon_i[lon]] = sst_c

    # Central-differences gradient
    result = []
    for i in range(1, len(lats) - 1):
        for j in range(1, len(lons) - 1):
            c = grid_c[i][j]
            if c is None: continue
            north = grid_c[i + 1][j]
            south = grid_c[i - 1][j]
            east  = grid_c[i][j + 1]
            west  = grid_c[i][j - 1]
            if None in (north, south, east, west):
                continue
            # Convert to °F
            df_ns = (north - south) * 9 / 5  # °F difference N-S
            df_ew = (east - west)   * 9 / 5  # °F difference E-W
            # nm distance between neighbors
            nm_ns = _nm_between(lats[i - 1], lons[j], lats[i + 1], lons[j])
            nm_ew = _nm_between(lats[i], lons[j - 1], lats[i], lons[j + 1])
            if nm_ns == 0 or nm_ew == 0:
                continue
            gy = df_ns / nm_ns  # °F per nm N-S
            gx = df_ew / nm_ew  # °F per nm E-W
            mag = math.sqrt(gx * gx + gy * gy)
            if mag >= threshold_f_per_nm:
                result.append({
                    "lat": round(lats[i], 3),
                    "lon": round(lons[j], 3),
                    "gradient_f_per_nm": round(mag, 2)
                })
    return result


def compute_chla_gradient_field(chla_rows: List[Tuple[float, float, float]],
                                 threshold_mg_per_nm: float = 0.05
                                 ) -> List[Dict[str, Any]]:
    """Like SST gradient but for chlorophyll (mg/m³ per nm).

    chla_rows is [(lat, lon, chla_mg_m3), ...]. Threshold defaults to
    0.05 mg/m³/nm — a real blue-green edge shows 0.10+ per nm.
    """
    if not chla_rows:
        return []
    lats = sorted({r[0] for r in chla_rows})
    lons = sorted({r[1] for r in chla_rows})
    lat_i = {v: i for i, v in enumerate(lats)}
    lon_i = {v: i for i, v in enumerate(lons)}
    grid = [[None] * len(lons) for _ in range(len(lats))]
    for lat, lon, val in chla_rows:
        if val is not None and not math.isnan(val):
            grid[lat_i[lat]][lon_i[lon]] = val

    result = []
    for i in range(1, len(lats) - 1):
        for j in range(1, len(lons) - 1):
            c = grid[i][j]
            if c is None: continue
            north = grid[i + 1][j]; south = grid[i - 1][j]
            east  = grid[i][j + 1]; west  = grid[i][j - 1]
            if None in (north, south, east, west): continue
            dv_ns = north - south
            dv_ew = east - west
            nm_ns = _nm_between(lats[i - 1], lons[j], lats[i + 1], lons[j])
            nm_ew = _nm_between(lats[i], lons[j - 1], lats[i], lons[j + 1])
            if nm_ns == 0 or nm_ew == 0: continue
            gy = dv_ns / nm_ns
            gx = dv_ew / nm_ew
            mag = math.sqrt(gx * gx + gy * gy)
            if mag >= threshold_mg_per_nm:
                result.append({
                    "lat": round(lats[i], 3),
                    "lon": round(lons[j], 3),
                    "gradient_mg_per_nm": round(mag, 3)
                })
    return result


# --------------------------- current convergence ---------------------------

def compute_current_convergence(arrows: List[Dict[str, Any]],
                                 neighbor_nm: float = 40.0,
                                 min_dot_negative: float = -0.5
                                 ) -> List[Dict[str, Any]]:
    """Given a list of current arrow points {lat, lon, dir_deg, vel_kt},
    find pairs that point TOWARD each other (dot product of unit vectors
    negative and points within neighbor_nm of each other). Return the
    midpoints as convergence points where bait is likely piling up.

    dir_deg is "toward" direction (0=N, 90=E). We convert to unit vectors
    (dx east, dy north) and check pairwise dot products.
    """
    if not arrows or len(arrows) < 2:
        return []
    # Precompute unit vectors
    for a in arrows:
        rad = math.radians(a.get("dir_deg", 0))
        # Unit vector in (east, north) = (sin θ, cos θ) — nautical convention
        a["_ux"] = math.sin(rad)
        a["_uy"] = math.cos(rad)

    result = []
    seen = set()
    for i, a in enumerate(arrows):
        for j, b in enumerate(arrows):
            if j <= i: continue
            d = _nm_between(a["lat"], a["lon"], b["lat"], b["lon"])
            if d > neighbor_nm or d < 5: continue
            # Vector from a → b (unit)
            dx = (b["lon"] - a["lon"]) * math.cos(math.radians((a["lat"] + b["lat"]) / 2))
            dy = (b["lat"] - a["lat"])
            n = math.sqrt(dx * dx + dy * dy)
            if n == 0: continue
            dx /= n; dy /= n
            # a's current relative to a→b direction
            dot_a = a["_ux"] * dx + a["_uy"] * dy   # >0 = flowing toward b
            dot_b = b["_ux"] * (-dx) + b["_uy"] * (-dy)  # >0 = flowing toward a
            # Convergence = both flowing toward each other
            if dot_a > 0.3 and dot_b > 0.3:
                mid_lat = (a["lat"] + b["lat"]) / 2
                mid_lon = (a["lon"] + b["lon"]) / 2
                strength = (dot_a + dot_b) * ((a.get("vel_kt", 0) + b.get("vel_kt", 0)) / 2)
                key = (round(mid_lat, 2), round(mid_lon, 2))
                if key in seen: continue
                seen.add(key)
                result.append({
                    "lat": round(mid_lat, 3),
                    "lon": round(mid_lon, 3),
                    "strength": round(strength, 3)
                })
    return result


# ---------------------------- persistence ----------------------------

def compute_persistence_flags(current_contours: Dict[str, List[List[List[float]]]],
                               archive_dir: pathlib.Path,
                               window_days: int = 3,
                               overlap_nm: float = 10.0,
                               snapshot_key: str = "sst_contours",
                               ) -> List[Dict[str, Any]]:
    """Read the last `window_days` archive snapshots' SST break contours.
    For each contour segment in today's `current_contours`, check whether ANY
    contour was present within `overlap_nm` on ALL prior days. If yes, return
    the segment's centroid as a "persistent break" — worth a bonus.

    v24.57 (Randy 2026-08-31) — snapshot_key parameter selects which key in
    the archived snapshot holds the contours for THIS region (e.g.
    "sst_contours" for NE, "sst_contours_gulf" for Gulf, etc.). Also relaxed
    the kind-matching: instead of requiring the SAME f-key to match across
    days, we now check ANY contour on those prior days. That way adaptive-
    threshold regions (v24.57) whose f-key varies day-to-day (f85 today,
    f83 tomorrow) still register persistence when the underlying thermal
    boundary is stable — because the SAME physical break shifts by only a
    degree or two overnight and the centroids stay within overlap_nm.
    """
    if not archive_dir.exists():
        return []
    today = datetime.date.today()
    # v24.122 (Randy 2026-09-11) — persistenceBoost had been silently dormant
    # for the entire archive (0 firings across 28 snapshots over 2+ months).
    # Root cause: the original scan required files for D-1, D-2, D-3 (exactly
    # `window_days` consecutive prior days). But nightlies fail some days —
    # Randy's archive has gaps of 1-5 days on the regular. So the missing-
    # day early-exit fired every single day and the signal was permanently 0.
    #
    # Fixed intent: "persistent over the most recent ~week" — take the
    # N most recent snapshots that exist within a 2×window_days-day lookback
    # window (default 6 days back). If we can't find `window_days` distinct
    # prior snapshots in that window, we still can't declare persistence
    # (not enough evidence) and return []. Otherwise the persistence check
    # runs against those snapshots regardless of calendar gaps between them.
    lookback_days = window_days * 2
    prior_contour_sets = []
    for i in range(1, lookback_days + 1):
        d = today - datetime.timedelta(days=i)
        f = archive_dir / f"{d.isoformat()}.json"
        if not f.exists():
            continue  # gap tolerated; keep looking
        try:
            snap = json.loads(f.read_text())
            sc = snap.get(snapshot_key, {})
            prior_contour_sets.append(sc)
            if len(prior_contour_sets) >= window_days:
                break
        except Exception:
            continue
    if len(prior_contour_sets) < window_days:
        return []  # not enough archived days to declare persistence

    def _segment_centroid(seg):
        if not seg: return None
        avg_lat = sum(p[0] for p in seg) / len(seg)
        avg_lon = sum(p[1] for p in seg) / len(seg)
        return (avg_lat, avg_lon)

    def _min_distance_to_lines(pt, lines):
        best = 1e9
        for line in lines:
            for p in line:
                d = _nm_between(pt[0], pt[1], p[0], p[1])
                if d < best:
                    best = d
                    if best < 1: return best
        return best

    def _all_lines_in(snap_contours):
        """Flatten any/all f-keys' polylines into one list — kind-agnostic."""
        out = []
        for lines in (snap_contours or {}).values():
            if isinstance(lines, list):
                out.extend(lines)
        return out

    persistent = []
    for kind, segs in (current_contours or {}).items():
        if not isinstance(segs, list):
            continue
        for seg in segs:
            c = _segment_centroid(seg)
            if not c: continue
            # Was ANY contour present within overlap_nm on ALL prior days?
            all_present = True
            for prior in prior_contour_sets:
                prior_lines = _all_lines_in(prior)
                if not prior_lines:
                    all_present = False; break
                d = _min_distance_to_lines(c, prior_lines)
                if d > overlap_nm:
                    all_present = False; break
            if all_present:
                persistent.append({
                    "lat": round(c[0], 3),
                    "lon": round(c[1], 3),
                    "kind": kind,
                    "days_persistent": window_days
                })
    return persistent


# ---------------------------- pressure ----------------------------

def analyze_pressure_trend(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Given the existing pressure_snapshot samples (list of {t, hpa} every
    3h), detect three states:

      - "post_front": pressure has risen >= 6 hPa in the last 24h — bite
        typically off for 24-36h.
      - "falling_fast": pressure has fallen >= 4 hPa in the last 24h — the
        classic "feed hard before the front" signal.
      - "steady": neither of the above.

    Returns dict with `state`, `24h_delta_hpa`, `12h_delta_hpa`, `current_hpa`.
    """
    if not samples or len(samples) < 3:
        return {"state": "unknown", "current_hpa": None,
                "delta_24h_hpa": 0.0, "delta_12h_hpa": 0.0}
    # Assume samples are chronological (they are, from build-inlined.py)
    current = samples[-1].get("hpa")
    # 12h back = 4 samples ago (samples are 3h apart)
    if len(samples) >= 5:
        p12 = samples[-5].get("hpa")
        delta_12h = current - p12 if (current is not None and p12 is not None) else 0.0
    else:
        delta_12h = 0.0
    if len(samples) >= 9:
        p24 = samples[-9].get("hpa")
        delta_24h = current - p24 if (current is not None and p24 is not None) else 0.0
    else:
        delta_24h = 0.0
    if delta_24h >= 6.0:
        state = "post_front"
    elif delta_24h <= -4.0:
        state = "falling_fast"
    elif delta_12h <= -3.0:
        state = "falling_fast"
    else:
        state = "steady"
    return {
        "state": state,
        "current_hpa": current,
        "delta_24h_hpa": round(delta_24h, 2),
        "delta_12h_hpa": round(delta_12h, 2)
    }
