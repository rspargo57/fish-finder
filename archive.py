#!/usr/bin/env python3
"""Fish Finder archive — persistent memory of daily snapshots.

Every build writes a snapshot to archive/YYYY-MM-DD.json. Snapshots contain
zone heat scores, model outputs (picks, effective heat, boosts), whale/bait
intel counts, weather, and moon/pressure conditions.

The archive lives at /root/fish-finder/archive/ and persists across sessions
(same user account). The model uses it two ways:

1. HTML embedding: build-inlined.py loads the last 30 days and inlines them
   as HISTORY_SNAPSHOT_PLACEHOLDER so the map/report can show trends.
2. Session context: at the start of every session, the Captain reads the last
   few snapshots to see what's been happening — so heat scores don't get
   whipsawed and predictions build on prior work.

Snapshots are additive only. Never overwrite historical data; if today's file
already exists, merge new fields into it.
"""
import json
import pathlib
import datetime
import os

ARCHIVE_DIR = pathlib.Path("/root/fish-finder/archive")
INDEX_FILE = ARCHIVE_DIR / "index.json"


def _today_iso():
    return datetime.date.today().isoformat()


# v24.106 (First Mate 2026-09-05) — per-species SST preferences, mirrors
# SPECIES_TEMP_PREF in fish-finder.src.html. Kept in sync manually; any
# time the client-side table is edited, this needs the same edit.
SPECIES_TEMP_PREF = {
    "bluefin_recreational": {"ideal": (66, 72), "tolerable": (62, 76)},
    "bluefin_giant":        {"ideal": (64, 72), "tolerable": (58, 76)},
    "yellowfin_tuna":       {"ideal": (70, 76), "tolerable": (66, 80)},
    "bigeye_tuna":          {"ideal": (68, 74), "tolerable": (64, 78)},
    "striped_bass":         {"ideal": (55, 68), "tolerable": (50, 72)},
    "bluefish":             {"ideal": (64, 74), "tolerable": (58, 78)},
    "mahi":                 {"ideal": (72, 82), "tolerable": (68, 86)},
    "thresher_shark":       {"ideal": (62, 70), "tolerable": (58, 74)},
    "mako_shark":            {"ideal": (64, 72), "tolerable": (58, 76)},
    "swordfish":            {"ideal": (68, 75), "tolerable": (62, 78)},
    "wahoo":                {"ideal": (72, 82), "tolerable": (68, 86)},
    "sailfish":             {"ideal": (75, 85), "tolerable": (72, 86)},
    "white_marlin":         {"ideal": (72, 80), "tolerable": (68, 84)},
    "blue_marlin":          {"ideal": (74, 82), "tolerable": (70, 86)},
    "false_albacore":       {"ideal": (60, 70), "tolerable": (55, 74)},
    "cobia":                {"ideal": (70, 82), "tolerable": (65, 86)},
    "king_mackerel":        {"ideal": (72, 82), "tolerable": (68, 86)},
    "spanish_mackerel":     {"ideal": (68, 80), "tolerable": (65, 84)},
    "red_drum":             {"ideal": (60, 78), "tolerable": (55, 82)},
    "blackfin_tuna":        {"ideal": (72, 82), "tolerable": (68, 86)},
    "red_snapper":          {"ideal": (68, 80), "tolerable": (62, 84)},
    "vermilion_snapper":    {"ideal": (68, 82), "tolerable": (64, 86)},
    "grouper":              {"ideal": (62, 76), "tolerable": (58, 82)},
    "amberjack":            {"ideal": (68, 80), "tolerable": (64, 84)},
    "redfish":              {"ideal": (60, 82), "tolerable": (55, 86)},
    "spotted_seatrout":     {"ideal": (65, 82), "tolerable": (55, 86)},
    "flounder":             {"ideal": (60, 76), "tolerable": (55, 80)},
    "tarpon":               {"ideal": (75, 88), "tolerable": (72, 92)},
    "mutton_snapper":       {"ideal": (72, 84), "tolerable": (68, 86)},
    "yellowtail_snapper":   {"ideal": (74, 86), "tolerable": (70, 88)},
    "cero_mackerel":        {"ideal": (74, 84), "tolerable": (70, 88)},
    "barracuda":            {"ideal": (74, 86), "tolerable": (70, 90)},
    "bonefish":             {"ideal": (72, 86), "tolerable": (68, 90)},
    "permit":               {"ideal": (72, 84), "tolerable": (68, 88)},
    "snook":                {"ideal": (72, 84), "tolerable": (65, 88)},
    "mangrove_snapper":     {"ideal": (72, 84), "tolerable": (68, 88)},
    "tautog":               {"ideal": (50, 62), "tolerable": (42, 68)},
    "weakfish":             {"ideal": (62, 74), "tolerable": (58, 78)},
    "bonito":               {"ideal": (64, 72), "tolerable": (60, 76)},
    "fluke":                {"ideal": (62, 72), "tolerable": (58, 76)},
    "sea_bass":             {"ideal": (58, 72), "tolerable": (50, 76)},
    "porgy":                {"ideal": (60, 72), "tolerable": (55, 76)},
    "cod":                  {"ideal": (40, 55), "tolerable": (36, 62)},
}


def _seg_intersect(p1, p2, p3, p4):
    """Line-segment intersection using the parametric form. Mirror of the
    JS segIntersect in fish-finder.src.html. Treats lat/lon as flat coords
    (fine at Northeast scale; sub-inch error). Returns [lat, lon] or None."""
    y1, x1 = p1[0], p1[1]
    y2, x2 = p2[0], p2[1]
    y3, x3 = p3[0], p3[1]
    y4, x4 = p4[0], p4[1]
    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denom) < 1e-9:
        return None
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
    u = -((x1 - x2) * (y1 - y3) - (y1 - y2) * (x1 - x3)) / denom
    if t < 0 or t > 1 or u < 0 or u > 1:
        return None
    return [y1 + t * (y2 - y1), x1 + t * (x2 - x1)]


def _find_all_crossings(lines_a, lines_b):
    """Given two arrays of polylines, return every intersection point.
    Each polyline is a list of [lat, lon] pairs. Mirror of JS findAllCrossings."""
    out = []
    for A in (lines_a or []):
        for i in range(len(A) - 1):
            for B in (lines_b or []):
                for j in range(len(B) - 1):
                    pt = _seg_intersect(A[i], A[i+1], B[j], B[j+1])
                    if pt:
                        out.append(pt)
    return out


def _compute_golden_points(sst_contours, chla_contours):
    """Iterate every SST × chla contour pair, collect all crossings.
    Mirror of the client-side loop that builds window.__ffGoldenPoints.

    Accepts EITHER shape (build-inlined.py passes the flat version, but
    the wrapper version is what fetch_sst_break_contours/fetch_chla_edge_contour
    return in-memory):
      Flat:    {"f68": [[[lat,lon], ...], [...]], "f72": [[[lat,lon], ...]]}
      Wrapped: {"contours": {"f68": [[...]], ...}, "fetched_at": ...}
    """
    if not sst_contours or not chla_contours:
        return []
    # Normalize both shapes: if there's a 'contours' key and the top-level
    # doesn't look like {label: [polylines]}, unwrap it.
    def _flat(c):
        if not isinstance(c, dict):
            return {}
        # If the dict has a 'contours' key that IS a dict, that's the wrapper shape
        if "contours" in c and isinstance(c["contours"], dict):
            return c["contours"]
        return c
    sst_dict = _flat(sst_contours)
    chla_dict = _flat(chla_contours)
    if not sst_dict or not chla_dict:
        return []
    points = []
    for sst_key, sst_lines in sst_dict.items():
        for chla_key, chla_lines in chla_dict.items():
            points.extend(_find_all_crossings(sst_lines, chla_lines))
    return points


def compute_zone_state(zones_data, derived=None, captain_dwells=None,
                       sst_contours=None, chla_contours=None):
    """Given the full zones.json dict, return a compact per-zone state dict
    suitable for archiving.

    v24.106 (First Mate 2026-09-05, pass 1): expanded from 3 signals to 9
    (added heatConfidence, sstFit, baitBoost, birdBoost, diversityBoost,
    distancePenalty).

    v24.106 (First Mate 2026-09-05, pass 2): +7 environmental signals from
    the derived_signals bundle — sstGradientBoost, chlaGradientBoost,
    convergenceBoost, persistenceBoost, postFrontPenalty,
    youtubeCorroborationBoost, captainDwellBoost.

    v24.106 (First Mate 2026-09-05, pass 3): +trendBoost — reads the last
    3 archive snapshots and gives +0.4 to zones with heat ≥ 7 for 3+
    consecutive days.

    v24.106 (First Mate 2026-09-05, pass 4): +goldenZoneBoost — computes
    SST × chla contour intersection points server-side (line-segment
    algorithm ported from JS) and gives +0.9/+0.6/+0.3 to zones within
    5/12/20 nm of any golden point. Server-side is now at 17 of 17 client
    signals — FULL PARITY CLOSED.
    """
    import datetime as _dt
    derived = derived or {}
    captain_dwells = captain_dwells or {}
    # v24.106 pass 4 — precompute golden points from SST × chla contours
    _golden_pts = _compute_golden_points(sst_contours, chla_contours)

    # Load last 3 archive snapshots for trendBoost, silently accept 0-3
    # depending on availability.
    _prior_heats = {}   # zone_id → [heat_D, heat_D-1, heat_D-2] (oldest first)
    try:
        _snap_files = sorted(ARCHIVE_DIR.glob("2*.json"), reverse=True)[:3]
        for _sf in _snap_files:
            try:
                _prior_snap = json.loads(_sf.read_text())
            except Exception:
                continue
            _pzs = _prior_snap.get("zones_state") or {}
            for _zid, _zst in _pzs.items():
                _h = _zst.get("heat") if isinstance(_zst, dict) else None
                if isinstance(_h, (int, float)):
                    _prior_heats.setdefault(_zid, []).append(_h)
    except Exception:
        pass
    state = {}
    today_iso = _today_iso()
    today = _dt.date.fromisoformat(today_iso)
    month_idx = today.month - 1

    # Precompute whale boosts: zone_id → 0 or 0.8
    whale_boosts = {}
    for w in zones_data.get("whale_sightings", []):
        try:
            d = _dt.date.fromisoformat(w["date"])
            if (today - d).days <= 7:
                for zid in w.get("zones", []):
                    whale_boosts[zid] = 0.8
        except Exception:
            continue

    # v24.106 — bait_intel index: zone_id → list of (date, source) tuples
    # in the last 14 days (covers baitBoost 10d + diversityBoost 14d).
    bait_by_zone: dict = {}
    for b in zones_data.get("bait_intel", []):
        try:
            d = _dt.date.fromisoformat(b.get("date", ""))
            age = (today - d).days
        except Exception:
            continue
        if age > 14 or age < 0:
            continue
        for zid in b.get("zones", []) or []:
            bait_by_zone.setdefault(zid, []).append({"age": age, "source": b.get("source", "")})

    # v24.106 — whale/bird_intel same shape for diversityBoost source counting
    whale_by_zone: dict = {}
    for w in zones_data.get("whale_sightings", []):
        try:
            d = _dt.date.fromisoformat(w.get("date", ""))
            age = (today - d).days
        except Exception:
            continue
        if age > 14 or age < 0:
            continue
        for zid in w.get("zones", []) or []:
            whale_by_zone.setdefault(zid, []).append({"age": age, "source": w.get("source", "")})

    bird_by_zone: dict = {}
    for b in zones_data.get("bird_intel", []) or []:
        try:
            d = _dt.date.fromisoformat(b.get("date", ""))
            age = (today - d).days
        except Exception:
            continue
        if age > 14 or age < 0:
            continue
        for zid in b.get("zones", []) or []:
            bird_by_zone.setdefault(zid, []).append({"age": age, "source": b.get("source", "")})

    port = zones_data.get("home_port", {}).get("coords") or [41.298, -72.372]
    max_range_nm = zones_data.get("boat", {}).get("max_range_nm") or 80

    def _distance_penalty(z):
        # v24.106 mirror of JS distancePenalty(): gradual penalty > 60% of range
        from math import radians, sin, cos, asin, sqrt
        R = 3440.065
        lat1r, lat2r = radians(port[0]), radians(z["center"][0])
        dlat = radians(z["center"][0] - port[0])
        dlon = radians(z["center"][1] - port[1])
        a = sin(dlat/2)**2 + cos(lat1r) * cos(lat2r) * sin(dlon/2)**2
        dist = 2 * R * asin(sqrt(a))
        pct = dist / max_range_nm if max_range_nm else 0
        if pct <= 0.6:
            return 0
        return -min(0.3, (pct - 0.6) * 0.75)

    def _heat_confidence(z):
        # v24.106 mirror of JS heatConfidence(): age-discount captain heat.
        hu = z.get("heat_updated")
        if not hu:
            return 0
        try:
            d = _dt.date.fromisoformat(hu[:10])
            days = (today - d).days
        except Exception:
            return 0
        if days <= 10: return 1.0
        if days <= 21: return 0.5
        if days <= 45: return 0.15
        return 0

    def _sst_fit(z):
        # v24.106 mirror of JS sstFit(). Assumes activeSpecies = all species.
        temp = z.get("current_sst_f")
        if temp is None:
            return 0
        species = z.get("species", [])
        if not species:
            return 0
        best = -0.3  # slight penalty if no species fits
        for s in species:
            pref = SPECIES_TEMP_PREF.get(s)
            if not pref:
                continue
            if pref["ideal"][0] <= temp <= pref["ideal"][1]:
                return 0.6  # ideal → biggest bonus, early return
            if pref["tolerable"][0] <= temp <= pref["tolerable"][1]:
                if best < 0.1:
                    best = 0.1
            else:
                if best < -0.4:
                    best = -0.4
        return best

    def _bait_boost(z):
        entries = bait_by_zone.get(z["id"], [])
        # baitBoost fires if any bait entry ≤ 10 days old for this zone
        for e in entries:
            if e["age"] <= 10:
                return 0.3
        return 0

    def _bird_boost(z):
        entries = bird_by_zone.get(z["id"], [])
        for e in entries:
            if e["age"] <= 5:
                return 0.4
        return 0

    def _diversity_boost(z):
        sources = set()
        for e in bait_by_zone.get(z["id"], []):
            sources.add(e["source"] or "")
        for e in whale_by_zone.get(z["id"], []):
            sources.add(e["source"] or "")
        for e in bird_by_zone.get(z["id"], []):
            sources.add(e["source"] or "")
        return 0.3 if len(sources) >= 3 else 0

    # v24.106 pass 2 — environmental boost helpers from derived_signals bundle
    from math import radians, sin, cos, asin, sqrt

    def _nm_from_zone(z, lat, lon):
        # Great-circle distance — same math as JS nmFromZone approximation.
        R = 3440.065
        lat1r, lat2r = radians(z["center"][0]), radians(lat)
        dlat = radians(lat - z["center"][0])
        dlon = radians(lon - z["center"][1])
        a = sin(dlat/2)**2 + cos(lat1r) * cos(lat2r) * sin(dlon/2)**2
        return 2 * R * asin(sqrt(a))

    def _sst_gradient_boost(z):
        # Mirror of JS sstGradientBoost() — up to 40nm reach for canyon zones
        pts = (derived.get("sst_gradient") or [])
        best = 0
        for p in pts:
            d = _nm_from_zone(z, p.get("lat", 0), p.get("lon", 0))
            if d > 40:
                continue
            g = p.get("gradient_f_per_nm", 0) or 0
            mag = 0.4 if g >= 1.0 else (0.3 if g >= 0.6 else 0.2)
            prox = 1.0 if d <= 5 else (0.7 if d <= 12 else (0.5 if d <= 20 else (0.3 if d <= 30 else 0.2)))
            best = max(best, mag * prox)
        return round(best, 2)

    def _chla_gradient_boost(z):
        # Mirror of JS chlaGradientBoost() — 15nm reach
        pts = (derived.get("chla_gradient") or [])
        best = 0
        for p in pts:
            d = _nm_from_zone(z, p.get("lat", 0), p.get("lon", 0))
            if d > 15:
                continue
            g = p.get("gradient_mg_per_nm", 0) or 0
            mag = 0.4 if g >= 0.15 else (0.3 if g >= 0.10 else 0.2)
            prox = 1.0 if d <= 5 else (0.7 if d <= 10 else 0.4)
            best = max(best, mag * prox)
        return round(best, 2)

    def _convergence_boost(z):
        pts = (derived.get("current_convergence") or [])
        best = 0
        for p in pts:
            d = _nm_from_zone(z, p.get("lat", 0), p.get("lon", 0))
            if d > 15:
                continue
            s = p.get("strength", 0) or 0
            mag = 0.4 if s >= 0.8 else (0.3 if s >= 0.4 else 0.2)
            prox = 1.0 if d <= 5 else (0.7 if d <= 10 else 0.5)
            best = max(best, mag * prox)
        return round(best, 2)

    def _persistence_boost(z):
        # v24.122 — widened 12 → 15 nm to match sstGradientBoost/sshaBoost
        # proximity convention; kept in sync with client-side persistenceBoost
        # in fish-finder.src.html so archive-time effective_heat parity holds.
        pts = (derived.get("persistent_breaks") or [])
        for p in pts:
            d = _nm_from_zone(z, p.get("lat", 0), p.get("lon", 0))
            if d <= 15:
                return 0.3
        return 0

    # Post-front penalty is GLOBAL (same value for every zone this day).
    ps = ((derived.get("pressure_state") or {}).get("state") or "")
    if ps == "post_front":
        post_front_pen = -0.5
    elif ps == "falling_fast":
        post_front_pen = 0.3
    else:
        post_front_pen = 0.0

    def _youtube_boost(z):
        yt = ((derived.get("youtube_intel") or {}).get("zone_mentions") or {}).get(z["id"])
        if not yt:
            return 0
        n = yt.get("channel_count", 0) or 0
        if n >= 2:
            return 0.4
        if n == 1:
            return 0.2
        return 0

    def _captain_dwell_boost(z):
        s = (captain_dwells.get("summary") or {}).get(z["id"])
        if not s:
            return 0
        distinct = len(s.get("distinct_mmsis", []) or [])
        if distinct >= 3:
            return 0.5
        if distinct >= 1:
            return 0.3
        return 0

    def _trend_boost(z, today_heat):
        # v24.106 pass 3 — trendBoost from prior 3 snapshots. If today's
        # heat + prior 2 all ≥ 7, that's 3 consecutive → +0.4. Missing
        # prior snapshots just mean no trend boost (not a bug — it's early
        # in the archive or the zone is new).
        priors = _prior_heats.get(z["id"], [])[:2]  # up to 2 prior days
        if not priors or len(priors) < 2:
            return 0
        # today + 2 priors all ≥ 7
        if today_heat >= 7 and all(h >= 7 for h in priors):
            return 0.4
        return 0

    def _golden_zone_boost(z):
        # v24.106 pass 4 — goldenZoneBoost mirror of JS.
        # For each precomputed golden point, take best boost by proximity band:
        #   ≤ 5 nm → +0.9, ≤ 12 nm → +0.6, ≤ 20 nm → +0.3
        if not _golden_pts:
            return 0
        best = 0
        for pt in _golden_pts:
            d = _nm_from_zone(z, pt[0], pt[1])
            if d <= 5 and best < 0.9:
                best = 0.9
                break  # can't do better than 0.9
            elif d <= 12 and best < 0.6:
                best = 0.6
            elif d <= 20 and best < 0.3:
                best = 0.3
        return best

    for z in zones_data.get("zones", []):
        zid = z["id"]
        sp = z.get("seasonal_presence", {})
        # Max seasonal fit across the zone's species for today's month
        peak_fit = 0
        peak_sp = None
        for sname, arr in sp.items():
            if isinstance(arr, list) and len(arr) == 12 and arr[month_idx] > peak_fit:
                peak_fit = arr[month_idx]
                peak_sp = sname
        # Season boost mapping mirrors JS seasonalBoost()
        if peak_fit >= 9: sb = 0.7
        elif peak_fit >= 7: sb = 0.4
        elif peak_fit >= 5: sb = 0.1
        elif peak_fit >= 3: sb = -0.3
        elif peak_fit >= 1: sb = -0.8
        else: sb = -1.5
        wb = whale_boosts.get(zid, 0)
        # v24.106 pass 1 signals
        hc = _heat_confidence(z)
        heat_discounted = (z.get("heat", 5) or 5) * hc
        sstf = _sst_fit(z)
        bb = _bait_boost(z)
        birdb = _bird_boost(z)
        divb = _diversity_boost(z)
        dp = _distance_penalty(z)
        # v24.106 pass 2 — environmental signals from derived_signals
        sstg = _sst_gradient_boost(z)
        chlag = _chla_gradient_boost(z)
        conv = _convergence_boost(z)
        pers = _persistence_boost(z)
        ytb = _youtube_boost(z)
        cdb = _captain_dwell_boost(z)
        # v24.106 pass 3 — trend boost from archive history
        trb = _trend_boost(z, z.get("heat", 5))
        # v24.106 pass 4 — golden zone boost (SST × chla contour crossings)
        gzb = _golden_zone_boost(z)
        eh = (heat_discounted + wb + sb + sstf + bb + birdb + divb + dp
              + sstg + chlag + conv + pers + post_front_pen + ytb + cdb + trb + gzb)
        state[zid] = {
            "heat": z.get("heat", 5),
            "species": z.get("species", []),
            "season_fit": peak_fit,
            "season_species": peak_sp,
            # v24.106 pass 1 — 6 signals
            "heat_confidence": round(hc, 2),
            "heat_discounted": round(heat_discounted, 2),
            "whale_boost": round(wb, 2),
            "season_boost": round(sb, 2),
            "sst_fit": round(sstf, 2),
            "bait_boost": round(bb, 2),
            "bird_boost": round(birdb, 2),
            "diversity_boost": round(divb, 2),
            "distance_penalty": round(dp, 2),
            # v24.106 pass 2 — 7 environmental signals from derived bundle
            "sst_gradient_boost": round(sstg, 2),
            "chla_gradient_boost": round(chlag, 2),
            "convergence_boost": round(conv, 2),
            "persistence_boost": round(pers, 2),
            "post_front_penalty": round(post_front_pen, 2),
            "youtube_corroboration_boost": round(ytb, 2),
            "captain_dwell_boost": round(cdb, 2),
            "trend_boost": round(trb, 2),
            "golden_zone_boost": round(gzb, 2),
            "effective_heat": round(eh, 2)
        }
    return state


def compute_top_picks(zones_data, zone_state):
    """Pick the top tuna zone and top striper zone by effective heat.

    Returns TWO picks per species: `in_range` (respects Randy's boat range —
    what he can actually reach) and `unconstrained` (ignores range — what the
    model would pick if range weren't a constraint). Randy's 2026-07-29 rule:
    the archive needs both so First Mate can eventually compare "did the model's
    true best (that Randy couldn't reach) actually produce fish?" once ground
    truth catch reports come in.
    """
    from math import radians, sin, cos, asin, sqrt

    port = zones_data["home_port"]["coords"]
    max_range = zones_data["boat"]["max_range_nm"]

    def great_circle_nm(lat1, lon1, lat2, lon2):
        R = 3440.065
        lat1r, lat2r = radians(lat1), radians(lat2)
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = sin(dlat/2)**2 + cos(lat1r) * cos(lat2r) * sin(dlon/2)**2
        return 2 * R * asin(sqrt(a))

    def in_range(z):
        return great_circle_nm(port[0], port[1], z["center"][0], z["center"][1]) <= max_range

    tuna_species = {"bluefin_recreational", "yellowfin_tuna", "bigeye_tuna"}
    striper_species = {"striped_bass"}

    def pick(species_set, range_filter=True):
        best = None
        for z in zones_data["zones"]:
            if range_filter and not in_range(z):
                continue
            if not any(s in species_set for s in z["species"]):
                continue
            eh = zone_state.get(z["id"], {}).get("effective_heat", z["heat"])
            if best is None or eh > best["effective_heat"]:
                nm = great_circle_nm(port[0], port[1], z["center"][0], z["center"][1])
                best = {
                    "zone_id": z["id"],
                    "zone_name": z["name"],
                    "distance_nm": round(nm, 1),
                    "in_range": in_range(z),
                    "heat": z["heat"],
                    "whale_boost": zone_state[z["id"]]["whale_boost"],
                    "season_boost": zone_state[z["id"]]["season_boost"],
                    "season_fit": zone_state[z["id"]]["season_fit"],
                    "effective_heat": zone_state[z["id"]]["effective_heat"]
                }
        return best

    def rank_top_n(species_set, range_filter=True, n=5):
        """v24.106 (First Mate 2026-09-05) — ranked top N zones with
        effective_heat. Stored in the archive so we can (a) measure runner-up
        prediction accuracy, (b) compute gap-to-winner distributions over time
        (a proxy for how tightly the model is picking), (c) enable
        counterfactual analysis: "if signal X had fired 0.3 higher, would
        picks have flipped?" — that's how we validate proposed weight changes
        against historical days before shipping them."""
        scored = []
        for z in zones_data["zones"]:
            if range_filter and not in_range(z):
                continue
            if not any(s in species_set for s in z["species"]):
                continue
            eh = zone_state.get(z["id"], {}).get("effective_heat", z["heat"])
            scored.append((eh, z))
        scored.sort(key=lambda x: -x[0])
        out = []
        for eh, z in scored[:n]:
            nm = great_circle_nm(port[0], port[1], z["center"][0], z["center"][1])
            out.append({
                "zone_id": z["id"],
                "zone_name": z["name"],
                "distance_nm": round(nm, 1),
                "in_range": in_range(z),
                "heat": z["heat"],
                "effective_heat": round(eh, 2),
                "whale_boost": round(zone_state.get(z["id"], {}).get("whale_boost", 0), 2),
                "season_boost": round(zone_state.get(z["id"], {}).get("season_boost", 0), 2),
                "gap_to_winner": round(scored[0][0] - eh, 2) if scored else 0,
            })
        return out

    # Per-species picks — Randy 2026-07-29: "work in all those other fishes
    # into the archives." First Mate can now do per-species accuracy analysis:
    # did the swordfish pick on 8/15 produce a swordfish catch report by 8/22?
    # Only include species with seasonal fit >= 3 for THIS day's month (dead
    # species get None so consumers can skip cleanly). Skips zones out of
    # range on the in-range picks; also includes unconstrained per-species.
    import datetime as _dt
    month_idx = _dt.date.today().month - 1

    def pick_species(species_key, range_filter=True):
        best = None
        for z in zones_data["zones"]:
            if species_key not in z.get("species", []):
                continue
            presence_arr = z.get("seasonal_presence", {}).get(species_key)
            if not isinstance(presence_arr, list) or len(presence_arr) != 12:
                continue
            presence = presence_arr[month_idx]
            if presence < 3:
                continue  # out of season → skip
            if range_filter and not in_range(z):
                continue
            eh = zone_state.get(z["id"], {}).get("effective_heat", z["heat"])
            if best is None or eh > best["effective_heat"]:
                nm = great_circle_nm(port[0], port[1], z["center"][0], z["center"][1])
                best = {
                    "zone_id": z["id"],
                    "zone_name": z["name"],
                    "distance_nm": round(nm, 1),
                    "in_range": in_range(z),
                    "season_presence": presence,
                    "heat": z["heat"],
                    "effective_heat": zone_state[z["id"]]["effective_heat"]
                }
        return best

    # Every species we track in the SPECIES catalog. Skip inshore-only species
    # when there's no seasonal presence data for zones.
    # v24.106 (First Mate 2026-09-06) — expanded from NE-only 15 species to
    # all 42 species we track across every region (matches SPECIES_TEMP_PREF).
    # Previous set was missing Gulf/S.FL/SE headliners: vermilion_snapper,
    # amberjack, red_snapper, grouper, king_mackerel, cobia, blackfin_tuna,
    # tarpon, snook, redfish, spotted_seatrout, etc. That's why per-species
    # picks for Gulf/S.FL/SE never rendered — the pick loop didn't try them.
    all_species = set(SPECIES_TEMP_PREF.keys())
    picks_by_species = {}
    for sk in all_species:
        in_r = pick_species(sk, range_filter=True)
        unc = pick_species(sk, range_filter=False)
        if in_r or unc:
            picks_by_species[sk] = {"in_range": in_r, "unconstrained": unc}

    return {
        # Backward-compatible top-level keys — historical readers see the
        # in-range pick as before, so old code doesn't break.
        "tuna": pick(tuna_species, range_filter=True),
        "striper": pick(striper_species, range_filter=True),
        # New keys for the two-pick view. First Mate uses these for the
        # "would-have-picked" accuracy comparison once ground truth arrives.
        "tuna_in_range": pick(tuna_species, range_filter=True),
        "tuna_unconstrained": pick(tuna_species, range_filter=False),
        "striper_in_range": pick(striper_species, range_filter=True),
        "striper_unconstrained": pick(striper_species, range_filter=False),
        # Per-species picks added 2026-07-29. Structure:
        # picks_by_species["swordfish"] = {"in_range": {...}, "unconstrained": {...}}
        "picks_by_species": picks_by_species,
        # v24.106 (First Mate 2026-09-05) — top-5 ranked runners-up per pick.
        # Enables: runner-up prediction accuracy, gap-to-winner analysis,
        # counterfactual weight-change validation.
        "runners_up": {
            "tuna_in_range":       rank_top_n(tuna_species, range_filter=True, n=5),
            "tuna_unconstrained":  rank_top_n(tuna_species, range_filter=False, n=5),
            "striper_in_range":    rank_top_n(striper_species, range_filter=True, n=5),
            "striper_unconstrained": rank_top_n(striper_species, range_filter=False, n=5),
        },
    }


OFFSHORE_SPECIES = {
    "bluefin_recreational", "bluefin_giant", "yellowfin_tuna", "bigeye_tuna",
    "mahi", "swordfish", "wahoo",
    "white_marlin", "blue_marlin", "sailfish",
    "thresher_shark", "mako_shark",
}
INSHORE_SPECIES = {"striped_bass", "bluefish", "false_albacore"}


def compute_look_ahead(zones_data, zone_weather, zone_state):
    """7-Day Look-Ahead prediction, server-side mirror of the JS renderLookAhead.

    For each of the next 7 dates in zone_weather.dates, pick the top offshore
    zone and top inshore zone by effective_heat (unconstrained per Randy's
    2026-07-29 rule — no range filter, no weather filter). Store each day's
    pick plus that day's per-zone weather for the picked zone.

    The picks will often be the SAME zone across all 7 days because effective
    heat is stable day-to-day (whales + season + heat + SST fit rarely flip
    overnight). That's fine — we still persist the per-day record so First
    Mate can score prediction accuracy N days out: "on 2026-07-30, model
    predicted top offshore zone X — did captain reports from X the following
    week corroborate a bite?" And if we later re-weight to make picks
    weather-sensitive, the per-day resolution is already there.

    Structure returned:
    [
      {"date": "2026-07-30", "day_index": 1, "day_label": "Tmrw",
       "offshore": {"zone_id":..., "zone_name":..., "distance_nm":...,
                    "effective_heat":..., "heat":...,
                    "weather": {"morning": {"wind_mph":..,"wave_ft":..,"wind_dir_deg":..},
                                "afternoon": {...}}} or None,
       "inshore":  {...} or None},
      ...
    ]
    """
    from math import radians, sin, cos, asin, sqrt

    dates = (zone_weather or {}).get("dates", [])
    if not dates:
        return []
    zw_zones = (zone_weather or {}).get("zones", {})
    port = zones_data["home_port"]["coords"]

    def great_circle_nm(lat1, lon1, lat2, lon2):
        R = 3440.065
        lat1r, lat2r = radians(lat1), radians(lat2)
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = sin(dlat/2)**2 + cos(lat1r) * cos(lat2r) * sin(dlon/2)**2
        return 2 * R * asin(sqrt(a))

    def _weather_penalty(zone_id, date_str):
        """v24.88 (Randy 2026-09-03, First Mate fix) — per-day weather term.

        The look-ahead was giving IDENTICAL picks for all 7 days because
        the pick math was date-independent. But weather isn't — a 20mph
        SW blow tomorrow can shut a zone that's ideal today. This term
        penalises effective_heat by day and per zone:

           0.0   fishable (wind ≤ 15, wave ≤ 3ft)
          -0.5   marginal (wind 16–18, wave 3.0–3.5)
          -1.5   not fishable (wind > 18 OR wave > 3.5ft)

        Uses the MORNING slot for offshore-mode (early runs) and averages
        morning+afternoon for inshore (day-long).  Missing weather returns
        0 (no penalty) so zones we didn't fetch don't get down-ranked
        artificially."""
        wx = (zw_zones.get(zone_id, {}) or {}).get(date_str, {}) or {}
        m = wx.get("morning") or {}
        wind = m.get("wind_mph")
        wave = m.get("wave_ft")
        if wind is None and wave is None:
            return 0.0
        wind = wind or 0
        wave = wave or 0
        if wind > 18 or wave > 3.5:
            return -1.5
        if wind > 15 or wave > 3.0:
            return -0.5
        return 0.0

    def pick_for_mode(species_set, date_str=None):
        """Top zone by effective heat + weather term for this date. If
        date_str is None, use pure effective_heat (backward compatible)."""
        best = None
        for z in zones_data["zones"]:
            if not any(s in species_set for s in z.get("species", [])):
                continue
            eh = zone_state.get(z["id"], {}).get("effective_heat", z["heat"])
            wp = _weather_penalty(z["id"], date_str) if date_str else 0.0
            score = eh + wp
            if best is None or score > best["_score"]:
                nm = great_circle_nm(port[0], port[1], z["center"][0], z["center"][1])
                best = {
                    "_score": score,
                    "zone_id": z["id"],
                    "zone_name": z["name"],
                    "distance_nm": round(nm, 1),
                    "heat": z["heat"],
                    "effective_heat": round(eh, 2),
                    "weather_penalty": round(wp, 2),
                    "adjusted_score": round(score, 2),
                }
        if best:
            best.pop("_score", None)
        return best

    def slot_wx(zone_id, date_str, slot_key):
        """Compact per-slot weather for a picked zone on a given date."""
        wx = (zw_zones.get(zone_id, {}) or {}).get(date_str, {}) or {}
        slot = wx.get(slot_key) or {}
        # Store the fields the accuracy loop will actually reference; skip nulls.
        out = {}
        for k in ("wind_mph", "wind_dir_deg", "wind_gusts_mph",
                 "wave_ft", "wave_period_s", "wave_dir_deg",
                 "weather_code", "precip_pct", "cloud_pct", "temp_f"):
            v = slot.get(k)
            if v is not None:
                out[k] = v
        return out

    def attach_weather(pick, date_str):
        if not pick:
            return None
        pick["weather"] = {
            "morning":   slot_wx(pick["zone_id"], date_str, "morning"),
            "afternoon": slot_wx(pick["zone_id"], date_str, "afternoon"),
        }
        return pick

    # v24.88 — compute pick PER DAY with per-day weather term so the 7-day
    # look-ahead actually varies across days when weather changes.
    day_labels = ["Today", "Tmrw"]
    look_ahead = []
    for i, d in enumerate(dates):
        label = day_labels[i] if i < len(day_labels) else d
        off_pick = pick_for_mode(OFFSHORE_SPECIES, date_str=d)
        ins_pick = pick_for_mode(INSHORE_SPECIES,  date_str=d)
        offshore = attach_weather(off_pick, d) if off_pick else None
        inshore  = attach_weather(ins_pick, d) if ins_pick  else None
        look_ahead.append({
            "date": d,
            "day_index": i,
            "day_label": label,
            "offshore": offshore,
            "inshore":  inshore,
        })
    return look_ahead


def save_snapshot(zones_data, weather=None, moon=None, pressure=None, notes=None,
                  sst_contours=None, chla_contours=None, derived=None,
                  zone_weather=None, raw_intel=None, regions_bundle=None,
                  region_contours=None):
    """Write today's snapshot to archive/YYYY-MM-DD.json and update index.

    If today's file already exists, merge new fields into it (so multiple
    builds in a day just refresh the current state).

    raw_intel (Randy 2026-08-08 preservation directive): the FULL source
    corpus each harvester saw today — every raw YouTube video in the cutoff
    window (matched or not), every Viking Fleet whale-watch card
    (species-matched or not), fetch metadata. The current model doesn't
    read this back; it's future-proofing for weight tuning, novel signal
    discovery, and any retro-analysis a future model rev wants to run.
    Never prune this field from historical snapshots — that's the whole
    point of collecting it.
    """
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    today = _today_iso()
    path = ARCHIVE_DIR / f"{today}.json"

    # v24.106 pass 2 — load captain_dwells snapshot for the dwell boost
    _dwells = {}
    try:
        _dwells_path = pathlib.Path(__file__).parent / "data" / "captain_dwells.json"
        if _dwells_path.exists():
            _dwells = json.loads(_dwells_path.read_text())
    except Exception:
        _dwells = {}

    zone_state = compute_zone_state(zones_data, derived=derived, captain_dwells=_dwells,
                                     sst_contours=sst_contours, chla_contours=chla_contours)
    picks = compute_top_picks(zones_data, zone_state)
    # 7-Day Look-Ahead persistence — server-side mirror of the JS widget so
    # the archive has ground truth to score prediction accuracy against once
    # 14+ days have accumulated. Requires zone_weather to be passed in (from
    # build-inlined.py). If absent, the field is left empty rather than fake.
    look_ahead = compute_look_ahead(zones_data, zone_weather, zone_state) if zone_weather else []

    # Whale sightings in last 7 days (compact form for the archive)
    today_d = datetime.date.today()
    recent_whales = []
    for w in zones_data.get("whale_sightings", []):
        try:
            wd = datetime.date.fromisoformat(w["date"])
            if (today_d - wd).days <= 7:
                recent_whales.append({
                    "date": w["date"],
                    "kind": w.get("kind"),
                    "location": w.get("location"),
                    "source": w.get("source"),
                    "zones": w.get("zones", [])
                })
        except Exception:
            continue

    # Load prior snapshot to merge (never lose data)
    prior = {}
    if path.exists():
        try:
            prior = json.loads(path.read_text())
        except Exception:
            prior = {}

    # v23.13 (Randy 2026-08-09): "I want you to do some historical tracking.
    # Track where the bait goes and how it moves each day. Same thing with
    # the whales and dolphins. Keep all that information in the background
    # till we need it." First Mate owns the tracking; the Captain preserves
    # the raw entries into each snapshot so First Mate can reconstruct daily
    # state months from now. The FULL bait_intel + whale_sightings arrays
    # (not just counts + last-7d) live in each snapshot from this build on.
    # bait_whale_history.py reads these back to compute movement patterns.
    #
    # Storage cost: ~5-10KB/day per array. Over a full season (150 days
    # late-Jun→Nov) that's ~1.5-3 MB total. Negligible vs the raw_intel
    # YouTube corpus already stored.
    # v24.56 (Randy 2026-08-31, First Mate) — per-region picks + look_ahead.
    # The top-level `picks` and `look_ahead` remain NE-scoped for backward
    # compatibility (existing accuracy_report.py reads these). NEW:
    # `regions` carries the same shape per non-NE region so accuracy scoring
    # can compute per-region accuracy once 14+ days have accumulated per
    # region. `regions_bundle` is a dict {region_key: {zones_data, zone_weather}}
    # passed by build-inlined.py.
    regions_snap = {}
    if regions_bundle:
        for _rkey, _rbundle in regions_bundle.items():
            _rzd = _rbundle.get("zones_data")
            if not _rzd:
                continue
            try:
                # v24.106 pass 2 — per-region uses per-region contours as
                # derived signals (from region_contours). Global signals
                # (pressure, youtube, captain_dwells) shared across regions.
                _rderived = dict(derived) if derived else {}
                _rc = (region_contours or {}).get(_rkey) or {}
                # v24.106 pass 4 — per-region SST + chla contours feed the
                # region's goldenZoneBoost. Wrap in {"contours": ...} to match
                # the shape _compute_golden_points() expects.
                _rsst = {"contours": _rc.get("sst") or {}} if _rc.get("sst") else None
                _rchla = {"contours": _rc.get("chla") or {}} if _rc.get("chla") else None
                _rstate = compute_zone_state(_rzd, derived=_rderived, captain_dwells=_dwells,
                                              sst_contours=_rsst, chla_contours=_rchla)
                _rpicks = compute_top_picks(_rzd, _rstate)
                _rzw = _rbundle.get("zone_weather")
                _rlookahead = compute_look_ahead(_rzd, _rzw, _rstate) if _rzw else []
                regions_snap[_rkey] = {
                    "picks": _rpicks,
                    "look_ahead": _rlookahead,
                    "zone_count": len(_rzd.get("zones", [])),
                    # v24.106 pass 7 (First Mate 2026-09-06) — per-region
                    # zones_state so first_mate_signal_report can analyze
                    # signal firing rates for Gulf/S.FL/SE too, not just NE.
                    # Same shape as top-level zones_state (all 17 boost
                    # components per zone).
                    "zones_state": _rstate,
                }
            except Exception as _ex:
                # Never let a single region's failure block the archive save.
                # v24.105 — print traceback so build log surfaces the actual
                # bug (was reduced to str() in JSON only, invisible in the log).
                import traceback as _tb
                _tb.print_exc()
                try:
                    _err_log = pathlib.Path(__file__).parent / "cache" / "build_errors.log"
                    _err_log.parent.mkdir(exist_ok=True)
                    with open(_err_log, "a") as _ef:
                        _ef.write(f"\n=== per-region archive save failure [{_rkey}] @ {datetime.datetime.utcnow().isoformat()}Z ===\n")
                        _tb.print_exc(file=_ef)
                except Exception:
                    pass
                print(f"  WARN [archive]: region '{_rkey}' snapshot compute failed: {_ex}")
                regions_snap[_rkey] = {"error": str(_ex)}

    snap = {
        "date": today,
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "zones_state": zone_state,
        "picks": picks,
        "look_ahead": look_ahead,
        "whale_sightings_count": len(zones_data.get("whale_sightings", [])),
        "recent_whale_sightings": recent_whales,
        "bait_intel_count": len(zones_data.get("bait_intel", [])),
        # v23.13 — full arrays preserved for historical tracking (First Mate)
        "bait_intel_full": list(zones_data.get("bait_intel", [])),
        "whale_sightings_full": list(zones_data.get("whale_sightings", [])),
        # v24.56 — per-region picks + look_ahead for per-region accuracy scoring
        "regions": regions_snap,
    }
    if weather:
        snap["weather"] = weather
    if moon:
        snap["moon"] = moon
    if pressure:
        snap["pressure"] = pressure
    if notes:
        snap["notes"] = notes
    # SST + chla contours enable next-day persistence signal computation.
    # Store as-is (they're already trimmed to reasonable size at build time).
    if sst_contours:
        snap["sst_contours"] = sst_contours
    if chla_contours:
        snap["chla_contours"] = chla_contours
    # v24.57 (Randy 2026-08-31): per-region contours archived alongside NE's.
    # Enables persistenceBoost + future persistence-based signals for every
    # region, not just NE. Payload is small (each region ~5-15 KB).
    #
    # region_contours shape: {
    #   "gulf":           {"sst": {...}, "chla": {...}},
    #   "south_florida":  {"sst": {...}, "chla": {...}},
    #   "south_atlantic": {"sst": {...}, "chla": {...}},
    # }
    if region_contours:
        for rkey, blob in region_contours.items():
            if not blob:
                continue
            if blob.get("sst"):
                snap[f"sst_contours_{rkey}"] = blob["sst"]
            if blob.get("chla"):
                snap[f"chla_contours_{rkey}"] = blob["chla"]
    if derived:
        snap["derived_signals_summary"] = {
            "sst_gradient_hot_count": len(derived.get("sst_gradient", [])),
            "chla_gradient_hot_count": len(derived.get("chla_gradient", [])),
            "current_convergence_count": len(derived.get("current_convergence", [])),
            "persistent_breaks_count": len(derived.get("persistent_breaks", [])),
            "pressure_state": derived.get("pressure_state", {}).get("state")
        }
        # v24.106 pass 6 (First Mate 2026-09-06): store the FULL derived
        # signals (with lat/lon) in each snapshot, not just the summary
        # counts. Enables:
        #   · Retroactive weight tuning — can recompute picks with different
        #     weights against historical data
        #   · Signal-firing verification — see WHICH zones got which boosts
        #     on any past day
        #   · Pattern analysis over time — do SST gradients tend to concentrate
        #     in the same locations? Do convergence points persist?
        # Storage: ~1-5KB per day (points are small; NE typically has 1-5
        # sst_gradient, 30-50 chla_gradient, 0-3 convergence, 0-3 persistent).
        # Even at 10KB/day × 365 days = ~3.5MB per year — negligible.
        snap["derived_signals_full"] = {
            "sst_gradient": derived.get("sst_gradient", []),
            "chla_gradient": derived.get("chla_gradient", []),
            "current_convergence": derived.get("current_convergence", []),
            "persistent_breaks": derived.get("persistent_breaks", []),
            "pressure_state": derived.get("pressure_state", {}),
        }
        # YouTube intel is stored FULL, not just a summary — it's the source
        # material the First Mate needs for accuracy analysis (which channels
        # said what about which zones N days before a catch report came in).
        yt = derived.get("youtube_intel")
        if yt:
            snap["youtube_intel"] = yt

    # Raw source-data preservation (Randy 2026-08-08). This is the full
    # harvested corpus BEFORE any filter or signal extraction; a future model
    # rev may find signal in cards/videos we currently discard. The archive
    # is append-only; snapshots that already carry raw_intel keep it.
    if raw_intel:
        snap["raw_intel"] = raw_intel

    # Merge with prior (new keys win, but keep old ones we don't set)
    merged = {**prior, **snap}
    # Guardrail against silent loss: if the prior snapshot had raw_intel and
    # this build somehow didn't include one, keep the prior. Never demote.
    if prior.get("raw_intel") and "raw_intel" not in snap:
        merged["raw_intel"] = prior["raw_intel"]
    # v23.13 same guardrail for the full bait/whale arrays — never demote
    # from a prior snapshot that had them to a build that dropped them.
    if prior.get("bait_intel_full") and not snap.get("bait_intel_full"):
        merged["bait_intel_full"] = prior["bait_intel_full"]
    if prior.get("whale_sightings_full") and not snap.get("whale_sightings_full"):
        merged["whale_sightings_full"] = prior["whale_sightings_full"]
    # v24.106 pass 6 — same guardrail for full derived signals
    if prior.get("derived_signals_full") and not snap.get("derived_signals_full"):
        merged["derived_signals_full"] = prior["derived_signals_full"]
    # v24.106 — runners_up should also never disappear from a snapshot
    if prior.get("picks", {}).get("runners_up") and not snap.get("picks", {}).get("runners_up"):
        merged.setdefault("picks", {})["runners_up"] = prior["picks"]["runners_up"]
    path.write_text(json.dumps(merged, indent=2))

    # Update index
    all_files = sorted([p.name for p in ARCHIVE_DIR.glob("2*.json")])
    idx = {"generated_at": snap["generated_at"], "days": all_files, "count": len(all_files)}
    INDEX_FILE.write_text(json.dumps(idx, indent=2))

    return snap


def load_history(days=30):
    """Return the last N daily snapshots, oldest first."""
    if not ARCHIVE_DIR.exists():
        return []
    files = sorted([p for p in ARCHIVE_DIR.glob("2*.json")])[-days:]
    out = []
    for f in files:
        try:
            out.append(json.loads(f.read_text()))
        except Exception:
            continue
    return out


def load_zone_trend(zone_id, days=30):
    """Return time series for a single zone: [{date, heat, effective_heat}, ...]"""
    history = load_history(days)
    trend = []
    for snap in history:
        zs = snap.get("zones_state", {}).get(zone_id)
        if zs:
            trend.append({
                "date": snap["date"],
                "heat": zs.get("heat"),
                "effective_heat": zs.get("effective_heat"),
                "whale_boost": zs.get("whale_boost"),
                "season_fit": zs.get("season_fit")
            })
    return trend


if __name__ == "__main__":
    # Quick CLI: `python3 archive.py save` writes today from zones.json
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "save":
        z = json.load(open("/root/fish-finder/data/zones.json"))
        snap = save_snapshot(z)
        print(f"Wrote {ARCHIVE_DIR / (snap['date'] + '.json')}")
        print(f"Total archived days: {len(load_history(9999))}")
    elif len(sys.argv) > 1 and sys.argv[1] == "history":
        h = load_history(30)
        for s in h:
            picks = s.get("picks", {})
            t = picks.get("tuna", {})
            st = picks.get("striper", {})
            print(f"{s['date']}: tuna→{t.get('zone_name','?')} ({t.get('effective_heat','?')}) · striper→{st.get('zone_name','?')} ({st.get('effective_heat','?')}) · whales={s.get('whale_sightings_count',0)}")
    else:
        print("Usage: archive.py [save|history]")
