#!/usr/bin/env python3
"""Fish Finder — Charter-boat dwell detection (v23.35, Randy 2026-08-12).

Randy (verbatim, the holy-grail directive):
    "When they stay at a spot for a certain period of time, we're gonna mark
    that as a hit for that day. If they're staying there, that means they're
    catching fish. This is the holy grail of all our information. We wanna
    feed off of it and really pick our spots to go fishing for tomorrow with."

This module is the ANALYSIS half. It reads raw AIS position data collected
by `aisstream_poll.py` (a separate script that consumes the aisstream.io
WebSocket feed and writes append-only position pings to a JSONL file), and
turns those raw pings into:

  1. **Dwell events** — a vessel spent ≥ DWELL_MIN_MINUTES within a
     DWELL_RADIUS_M circle. Each dwell event has (mmsi, center_lat,
     center_lon, start_utc, end_utc, minutes, mean_speed_kt).
  2. **Zone-tagged dwells** — for each event, which of Randy's fishing
     zones the center falls within (or which zone is closest, up to some
     max distance). Multiple zones may be tagged if the dwell is on a
     boundary between two adjacent zones.
  3. **Per-zone dwell scores** — for a given lookback window (default 72h),
     count how many distinct captain-vessel dwells landed in each zone.
     Higher count = more fleet activity = the model bumps that zone's
     effectiveHeat via `captainDwellBoost()`.

Dwell definition (biology-first):
  - A tuna charter working a spot RUNS at ~20-30 kt to get there, then
    slows to trolling speed (4-8 kt) and stays in a small circle. Or, for
    swordfish/canyon work, sits nearly stopped (0-2 kt) for hours.
  - We define a dwell as: within a circle of radius 800m for at least 30
    consecutive minutes, average speed ≤ 5 kt. Adjust in DEFAULTS below.
  - False positives to guard against: a vessel drifting at anchor near a
    marina (not fishing — filter by coord being > MIN_DIST_FROM_LAND_M
    from a land polygon, or simpler: > 5 nm from Randy's home port).
  - False negatives to accept: a captain trolling a large loop wouldn't
    stay in an 800m circle. That's OK for v1 — we're catching the sit-
    and-fish behavior (swordfish, bluefin drift, bottom fishing) which
    is what the "holy grail" really means. Trolling loops can be added
    as a v2 pattern detector.

Ship-tonight status (v23.35):
  - This module is FUNCTIONAL and TESTED with synthetic data.
  - Live AIS data won't flow until Randy signs up for aisstream.io (free,
    ~5 min at https://aisstream.io/) and provides the API key.
  - Once activated: aisstream_poll.py runs on a scheduled trigger every
    2 hours, writes positions to data/captain_positions.jsonl, this
    module reads that at nightly-build time and bakes dwell events +
    per-zone scores into the HTML for the model to consume.

Model integration:
  - `captainDwellBoost(zone)` in fish-finder.src.html reads the baked
    per-zone dwell score (built by `dwell_summary_for_zones()` below) and
    boosts effectiveHeat: +0.5 if 3+ distinct captain dwells in the zone
    in the last 72h, +0.3 if 1-2, 0 otherwise. Weights start conservative
    per First Mate rules; they can rise if the accuracy loop shows dwell-
    boosted zones outperform.

Usage:
    from dwell_analysis import detect_dwells, dwell_summary_for_zones
    positions = load_positions("/root/fish-finder/data/captain_positions.jsonl")
    events = detect_dwells(positions)
    per_zone = dwell_summary_for_zones(events, zones, lookback_hours=72)
    # per_zone: {zone_id: {"dwell_count": int, "distinct_mmsis": [...], "latest": iso}}
"""
from __future__ import annotations

import datetime
import json
import math
import pathlib
from collections import defaultdict


# -----------------------------------------------------------------------------
# Tunable defaults. These are the "biology-first" thresholds documented above.
# Change carefully — they define what counts as "fishing" vs "traveling."
# -----------------------------------------------------------------------------
DEFAULTS = {
    "dwell_radius_m": 800,           # how tight the circle must be
    "dwell_min_minutes": 30,         # how long the vessel must stay in it
    "dwell_max_avg_speed_kt": 5.0,   # max mean speed inside the circle
    "min_dist_from_home_port_nm": 5, # ignore dwells in the harbor
    "zone_tag_max_nm": 15,           # dwell → zone tag if within this
    "lookback_hours_default": 72,    # for per-zone scoring
}

POSITIONS_PATH = pathlib.Path("/root/fish-finder/data/captain_positions.jsonl")
DWELLS_PATH = pathlib.Path("/root/fish-finder/data/captain_dwells.jsonl")


# -----------------------------------------------------------------------------
# Geometry helpers
# -----------------------------------------------------------------------------
def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters between two lat/lon points."""
    R = 6_371_000.0
    a1, a2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(a1) * math.cos(a2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def nm_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Nautical miles between two lat/lon points."""
    return haversine_m(lat1, lon1, lat2, lon2) / 1852.0


# -----------------------------------------------------------------------------
# Position ingestion
# -----------------------------------------------------------------------------
def load_positions(path: pathlib.Path = POSITIONS_PATH, since_hours: float = 168):
    """Load AIS position pings from the JSONL file.

    Each line in captain_positions.jsonl is one AIS position:
        {"mmsi": 366123456, "ts": "2026-08-12T14:30:00Z", "lat": 40.85,
         "lon": -72.5, "sog": 3.2, "cog": 220, "name": "FV FAT TUNA"}

    Filters to the last `since_hours` (default 7 days) so we don't
    re-analyze ancient positions on every call.
    """
    if not path.exists():
        return []
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=since_hours)
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            p = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            ts = datetime.datetime.fromisoformat(p["ts"].replace("Z", "+00:00")).replace(tzinfo=None)
            if ts < cutoff:
                continue
            p["_ts"] = ts
        except (KeyError, ValueError):
            continue
        if p.get("lat") is None or p.get("lon") is None:
            continue
        out.append(p)
    # Sort by (mmsi, ts) so per-vessel walks are chronological
    out.sort(key=lambda p: (p.get("mmsi", 0), p["_ts"]))
    return out


# -----------------------------------------------------------------------------
# Core dwell detection
# -----------------------------------------------------------------------------
def detect_dwells(positions: list, config: dict = None) -> list:
    """Walk each vessel's chronological position stream and emit dwell events.

    Algorithm:
      - Group positions by MMSI.
      - For each vessel's chronological list, sweep with a growing window:
        keep adding positions to a candidate dwell as long as each new
        position is within `dwell_radius_m` of the running centroid AND the
        speed (SOG) is ≤ `dwell_max_avg_speed_kt`.
      - When a position falls outside the circle OR exceeds the speed cap,
        close the candidate. If it spanned ≥ `dwell_min_minutes`, emit it
        as a dwell event.

    Returns a list of dwell dicts:
        {"mmsi": ..., "name": ..., "start": iso, "end": iso, "minutes": ...,
         "center_lat": ..., "center_lon": ..., "mean_speed_kt": ...,
         "position_count": ...}
    """
    cfg = {**DEFAULTS, **(config or {})}
    events = []
    by_mmsi = defaultdict(list)
    for p in positions:
        by_mmsi[p["mmsi"]].append(p)

    for mmsi, pings in by_mmsi.items():
        window = []  # candidate positions in the current dwell
        for p in pings:
            if not window:
                window = [p]
                continue
            # Compute centroid of current window
            cx = sum(x["lat"] for x in window) / len(window)
            cy = sum(x["lon"] for x in window) / len(window)
            dist_m = haversine_m(cx, cy, p["lat"], p["lon"])
            sog = float(p.get("sog") or 0)
            if dist_m <= cfg["dwell_radius_m"] and sog <= cfg["dwell_max_avg_speed_kt"]:
                window.append(p)
                continue
            # Window closed — emit if long enough
            _emit_if_valid(window, events, cfg)
            window = [p]
        # End of vessel's stream — check final window
        _emit_if_valid(window, events, cfg)

    return events


def _emit_if_valid(window: list, events: list, cfg: dict):
    """Emit a dwell event if the window meets the min-duration threshold."""
    if len(window) < 2:
        return
    start = window[0]["_ts"]
    end = window[-1]["_ts"]
    minutes = (end - start).total_seconds() / 60
    if minutes < cfg["dwell_min_minutes"]:
        return
    cx = sum(p["lat"] for p in window) / len(window)
    cy = sum(p["lon"] for p in window) / len(window)
    avg_sog = sum(float(p.get("sog") or 0) for p in window) / len(window)
    events.append({
        "mmsi": window[0].get("mmsi"),
        "name": window[0].get("name") or "",
        "start": start.isoformat() + "Z",
        "end": end.isoformat() + "Z",
        "minutes": round(minutes, 1),
        "center_lat": round(cx, 5),
        "center_lon": round(cy, 5),
        "mean_speed_kt": round(avg_sog, 2),
        "position_count": len(window),
    })


# -----------------------------------------------------------------------------
# Zone tagging — link dwells to Randy's fishing zones
# -----------------------------------------------------------------------------
def tag_dwells_by_zone(events: list, zones: list, config: dict = None) -> list:
    """For each dwell event, tag it with the nearest zone(s) within
    `zone_tag_max_nm`. A dwell can tag multiple zones if it sits between them.
    Adds a `zones` list to each event (in-place-safe copy).
    """
    cfg = {**DEFAULTS, **(config or {})}
    tagged = []
    for e in events:
        matches = []
        for z in zones:
            if not z.get("center"):
                continue
            nm = nm_between(e["center_lat"], e["center_lon"],
                            z["center"][0], z["center"][1])
            if nm <= cfg["zone_tag_max_nm"]:
                matches.append((z["id"], round(nm, 1)))
        matches.sort(key=lambda m: m[1])
        e2 = dict(e)
        e2["zones"] = [m[0] for m in matches[:3]]  # cap at 3 closest
        e2["nearest_zone_nm"] = matches[0][1] if matches else None
        tagged.append(e2)
    return tagged


def filter_out_home_port(events: list, home_port_coords: tuple, config: dict = None) -> list:
    """Drop dwells that happen too close to Randy's home port (usually at anchor
    or moored). Default: within 5 nm of the port coords."""
    cfg = {**DEFAULTS, **(config or {})}
    keep = []
    for e in events:
        nm = nm_between(e["center_lat"], e["center_lon"], *home_port_coords)
        if nm >= cfg["min_dist_from_home_port_nm"]:
            keep.append(e)
    return keep


# -----------------------------------------------------------------------------
# Per-zone summary for build-time HTML bake
# -----------------------------------------------------------------------------
def dwell_summary_for_zones(events: list, zones: list, lookback_hours: int = None) -> dict:
    """Aggregate tagged dwells into per-zone scores for the model.

    Returns:
        {zone_id: {
            "dwell_count": int,             # total dwell events in window
            "distinct_mmsis": [int, ...],   # unique vessels
            "total_minutes": float,         # sum of dwell durations
            "latest_ts": iso | None,        # most recent dwell end
        }}

    The model's `captainDwellBoost(zone)` reads `dwell_count` and
    `distinct_mmsis` count to decide the boost magnitude.
    """
    lookback = lookback_hours or DEFAULTS["lookback_hours_default"]
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=lookback)
    tagged = tag_dwells_by_zone(events, zones)
    by_zone = defaultdict(lambda: {"dwell_count": 0, "distinct_mmsis": set(),
                                    "total_minutes": 0.0, "latest_ts": None})
    for e in tagged:
        try:
            end_ts = datetime.datetime.fromisoformat(e["end"].replace("Z", "+00:00")).replace(tzinfo=None)
        except (KeyError, ValueError):
            continue
        if end_ts < cutoff:
            continue
        for zid in e.get("zones", []):
            slot = by_zone[zid]
            slot["dwell_count"] += 1
            slot["distinct_mmsis"].add(e["mmsi"])
            slot["total_minutes"] += e["minutes"]
            if slot["latest_ts"] is None or e["end"] > slot["latest_ts"]:
                slot["latest_ts"] = e["end"]
    # Convert sets → lists for JSON serialization
    return {zid: {**v, "distinct_mmsis": sorted(v["distinct_mmsis"])}
            for zid, v in by_zone.items()}


# -----------------------------------------------------------------------------
# CLI — for manual testing + smoke tests
# -----------------------------------------------------------------------------
def _smoke_test():
    """Synthetic dwell detection test — validates the algorithm without live AIS."""
    now = datetime.datetime.utcnow()
    positions = []
    # Vessel 1: dwells at Coxes Ledge SE (~41.05°N, -71.00°W) for 45 minutes
    for i in range(46):
        positions.append({
            "mmsi": 366100001, "name": "TEST FAT TUNA",
            "ts": (now - datetime.timedelta(hours=2, minutes=45 - i)).isoformat() + "Z",
            "lat": 41.05 + (i % 3) * 0.001,
            "lon": -71.00 + (i % 4) * 0.001,
            "sog": 3.5,
        })
    # Vessel 2: transiting fast — no dwell
    for i in range(30):
        positions.append({
            "mmsi": 366100002, "name": "TEST TRANSIT",
            "ts": (now - datetime.timedelta(hours=1, minutes=30 - i)).isoformat() + "Z",
            "lat": 41.30 - i * 0.005,
            "lon": -72.00 + i * 0.008,
            "sog": 22.0,
        })
    parsed = []
    for p in positions:
        p["_ts"] = datetime.datetime.fromisoformat(p["ts"].replace("Z", ""))
        parsed.append(p)
    parsed.sort(key=lambda p: (p["mmsi"], p["_ts"]))
    events = detect_dwells(parsed)
    print(f"Smoke test: {len(events)} dwell events detected (expected 1)")
    for e in events:
        print(f"  {e}")
    # Zone tagging test
    fake_zones = [
        {"id": "coxes_ledge_se", "center": [41.05, -71.00]},
        {"id": "the_race", "center": [41.24, -72.03]},
    ]
    summary = dwell_summary_for_zones(events, fake_zones, lookback_hours=72)
    print(f"Per-zone summary: {json.dumps(summary, indent=2)}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "smoke":
        _smoke_test()
    else:
        # Real analysis run
        positions = load_positions()
        print(f"Loaded {len(positions)} position pings")
        if not positions:
            print("No position data yet. Once aisstream.io is activated and")
            print("aisstream_poll.py has run, data/captain_positions.jsonl will")
            print("populate. Then this script produces dwell events for the model.")
            sys.exit(0)
        events = detect_dwells(positions)
        print(f"Detected {len(events)} dwell events")
        for e in events[-10:]:
            print(f"  {e['name'] or e['mmsi']}: {e['minutes']}min at ({e['center_lat']}, {e['center_lon']})")
