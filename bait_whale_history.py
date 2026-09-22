#!/usr/bin/env python3
"""Fish Finder — Bait & Whale Movement History (First Mate-owned)

Randy 2026-08-09: "Track where the bait goes and how it moves each day.
Same thing with the whales and dolphins. Keep all that information in the
background till we need it. Eventually we're gonna add a tab that shows
where the bait has gone over time."

This module is READ-ONLY analysis over the archive at
`/root/fish-finder/archive/YYYY-MM-DD.json`. It:

  - Reconstructs day-by-day state of bait_intel + whale_sightings from
    the `bait_intel_full` / `whale_sightings_full` arrays now saved
    into each snapshot (from v23.13 onward).
  - Computes per-zone timelines: "when did bait X show up at zone Y and
    when did it fade?"
  - Computes per-bait / per-whale timelines: "where has bunker been each
    day this month? What zones did humpback sightings hit?"
  - Detects zone-to-zone movements (new zone appears in a bait species'
    footprint that wasn't there yesterday = a spread; disappearance = a
    retreat).

NO UI. NO SIDE EFFECTS. Reads archive files and returns Python dicts /
lists. When Randy asks for a "bait-over-time" tab, that UI reads what
this module produces.

Usage as a module:
    import bait_whale_history as bwh
    hist = bwh.load_daily_state_series(days=30)   # last N days
    per_bait = bwh.per_bait_timeline(hist)
    movements = bwh.detect_movements(hist)

Usage from CLI:
    python3 bait_whale_history.py                 # print recent movement summary
    python3 bait_whale_history.py --json          # dump full analysis as JSON
    python3 bait_whale_history.py --days 60       # look back further
    python3 bait_whale_history.py --bait bunker   # focus on one bait type
    python3 bait_whale_history.py --whale humpback

First-Mate rules governing this module:
  1. Never mutate the archive. This is analysis, not ingestion.
  2. Never fabricate movements. If a zone appears in day N's bait footprint
     but wasn't in day N-1's, that's a legitimate "spread" — unless N-1's
     snapshot is missing `bait_intel_full` (pre-v23.13), in which case
     mark the movement as "unknown_prior" and skip.
  3. Timeline windows: 7-day rolling for landing surfaces (matches the
     freshness lens), 30-day for movement analysis, 90-day for
     season-over-season pattern discovery once we have that much data.
  4. Bait and whale entries carry a `source_type` field. `youtube_auto`
     entries are broader-tagged (a video that says bunker + mentions 5
     locations tags all 5); do NOT weight youtube_auto movements as
     heavily as first-party captain reports. Filter or split by
     `source_type` when the analysis is used to tune model weights.

Ownership: First Mate skill. Captain preserves the raw entries into the
archive; First Mate reads them back and computes the patterns.
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
from collections import defaultdict
from typing import Any

ARCHIVE_DIR = pathlib.Path("/root/fish-finder/archive")


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_daily_state_series(days: int = 30) -> list[dict[str, Any]]:
    """Load the last N daily snapshots and return a list ordered oldest→newest.
    Each element has {date, bait_entries, whale_entries, has_full_arrays}.
    Snapshots that pre-date the v23.13 full-array upgrade fall back to
    `recent_whale_sightings` for whales (still useful, though incomplete)
    and mark `has_full_arrays=False` for bait (nothing to reconstruct).
    """
    if not ARCHIVE_DIR.exists():
        return []
    files = sorted(ARCHIVE_DIR.glob("2*.json"))[-days:]
    series = []
    for f in files:
        try:
            snap = json.loads(f.read_text())
        except Exception:
            continue
        bait_full = snap.get("bait_intel_full")
        whale_full = snap.get("whale_sightings_full")
        # Fallback for pre-v23.13 snapshots — recent_whale_sightings gives
        # us the last 7 days at least, though it's a compacted subset.
        whale_fallback = snap.get("recent_whale_sightings")
        series.append({
            "date": snap.get("date", f.stem),
            "bait_entries": list(bait_full) if bait_full is not None else [],
            "whale_entries": list(whale_full) if whale_full is not None
                            else (list(whale_fallback) if whale_fallback else []),
            "has_full_arrays": bait_full is not None and whale_full is not None,
        })
    return series


# ---------------------------------------------------------------------------
# Per-zone timelines
# ---------------------------------------------------------------------------

def per_zone_bait_timeline(series: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """For each zone_id, return a list of {date, bait_types, source_types,
    sources, entry_count} ordered oldest→newest. Only includes days where the
    snapshot has full arrays AND the zone appears in ≥1 bait entry that's
    ≤7 days old as of that snapshot's date (matches landing freshness lens).
    """
    per_zone: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for snap in series:
        if not snap["has_full_arrays"]:
            continue
        try:
            snap_date = datetime.date.fromisoformat(snap["date"])
        except Exception:
            continue
        # Group each zone's entries for THIS day
        zone_hits: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"bait_types": set(), "source_types": set(),
                     "sources": set(), "entry_count": 0}
        )
        for entry in snap["bait_entries"]:
            try:
                edate = datetime.date.fromisoformat(entry.get("date", ""))
                age = (snap_date - edate).days
            except Exception:
                continue
            if age < 0 or age > 7:
                continue
            bait_key = entry.get("bait") or entry.get("species") or "unknown"
            for zid in entry.get("zones", []) or []:
                z = zone_hits[zid]
                z["bait_types"].add(bait_key)
                z["source_types"].add(entry.get("source_type", "captain"))
                if entry.get("source"):
                    z["sources"].add(entry["source"])
                z["entry_count"] += 1
        for zid, agg in zone_hits.items():
            per_zone[zid].append({
                "date": snap["date"],
                "bait_types": sorted(agg["bait_types"]),
                "source_types": sorted(agg["source_types"]),
                "sources": sorted(agg["sources"]),
                "entry_count": agg["entry_count"],
            })
    return dict(per_zone)


def per_zone_whale_timeline(series: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Same shape as per_zone_bait_timeline but for whale_sightings. Uses
    the model's own 7-day freshness lens (matches whaleBoost's window)."""
    per_zone: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for snap in series:
        if not snap["has_full_arrays"] and not snap["whale_entries"]:
            continue
        try:
            snap_date = datetime.date.fromisoformat(snap["date"])
        except Exception:
            continue
        zone_hits: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"species": set(), "kinds": set(), "sources": set(),
                     "source_types": set(), "entry_count": 0}
        )
        for entry in snap["whale_entries"]:
            try:
                edate = datetime.date.fromisoformat(entry.get("date", ""))
                age = (snap_date - edate).days
            except Exception:
                continue
            if age < 0 or age > 7:
                continue
            for zid in entry.get("zones", []) or []:
                z = zone_hits[zid]
                for sp in (entry.get("species") or []):
                    z["species"].add(sp)
                if entry.get("kind"):
                    z["kinds"].add(entry["kind"])
                if entry.get("source"):
                    z["sources"].add(entry["source"])
                z["source_types"].add(entry.get("source_type", "captain"))
                z["entry_count"] += 1
        for zid, agg in zone_hits.items():
            per_zone[zid].append({
                "date": snap["date"],
                "species": sorted(agg["species"]),
                "kinds": sorted(agg["kinds"]),
                "sources": sorted(agg["sources"]),
                "source_types": sorted(agg["source_types"]),
                "entry_count": agg["entry_count"],
            })
    return dict(per_zone)


# ---------------------------------------------------------------------------
# Per-bait / per-whale timelines (the "where has THIS bait been?" view)
# ---------------------------------------------------------------------------

def per_bait_timeline(series: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """For each bait species (bunker, sand_eels, squid, etc.), return
    [{date, zones, entry_count, source_types}, ...] — the daily footprint."""
    per_bait: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for snap in series:
        if not snap["has_full_arrays"]:
            continue
        try:
            snap_date = datetime.date.fromisoformat(snap["date"])
        except Exception:
            continue
        by_key: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"zones": set(), "entry_count": 0, "source_types": set()}
        )
        for entry in snap["bait_entries"]:
            try:
                edate = datetime.date.fromisoformat(entry.get("date", ""))
                age = (snap_date - edate).days
            except Exception:
                continue
            if age < 0 or age > 7:
                continue
            bait_key = entry.get("bait") or entry.get("species") or "unknown"
            by_key[bait_key]["zones"].update(entry.get("zones") or [])
            by_key[bait_key]["entry_count"] += 1
            by_key[bait_key]["source_types"].add(entry.get("source_type", "captain"))
        for bait_key, agg in by_key.items():
            per_bait[bait_key].append({
                "date": snap["date"],
                "zones": sorted(agg["zones"]),
                "zone_count": len(agg["zones"]),
                "entry_count": agg["entry_count"],
                "source_types": sorted(agg["source_types"]),
            })
    return dict(per_bait)


def per_whale_timeline(series: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Same shape as per_bait_timeline for whale species keys."""
    per_whale: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for snap in series:
        if not snap["whale_entries"]:
            continue
        try:
            snap_date = datetime.date.fromisoformat(snap["date"])
        except Exception:
            continue
        by_key: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"zones": set(), "entry_count": 0, "source_types": set(), "kinds": set()}
        )
        for entry in snap["whale_entries"]:
            try:
                edate = datetime.date.fromisoformat(entry.get("date", ""))
                age = (snap_date - edate).days
            except Exception:
                continue
            if age < 0 or age > 7:
                continue
            for sp in (entry.get("species") or ["unknown"]):
                by_key[sp]["zones"].update(entry.get("zones") or [])
                by_key[sp]["entry_count"] += 1
                by_key[sp]["source_types"].add(entry.get("source_type", "captain"))
                if entry.get("kind"):
                    by_key[sp]["kinds"].add(entry["kind"])
        for whale_key, agg in by_key.items():
            per_whale[whale_key].append({
                "date": snap["date"],
                "zones": sorted(agg["zones"]),
                "zone_count": len(agg["zones"]),
                "entry_count": agg["entry_count"],
                "source_types": sorted(agg["source_types"]),
                "kinds": sorted(agg["kinds"]),
            })
    return dict(per_whale)


# ---------------------------------------------------------------------------
# Movement detection (day-over-day appearances / disappearances)
# ---------------------------------------------------------------------------

def detect_movements(series: list[dict[str, Any]], kind: str = "bait") -> list[dict[str, Any]]:
    """Compare each snapshot to the one before it. For each bait/whale key,
    a zone that appears TODAY but wasn't in the footprint YESTERDAY = a
    "spread"; a zone that was in yesterday's footprint but not today = a
    "retreat". Returns a chronological list of movements.

    First-Mate note: movements only make sense day-to-day when BOTH snapshots
    have `has_full_arrays=True`. Older snapshots are skipped as
    "unknown_prior" and no false movement is emitted.
    """
    tl_fn = per_bait_timeline if kind == "bait" else per_whale_timeline
    tl = tl_fn(series)
    movements = []
    for key, entries in tl.items():
        # Sort by date so we walk chronologically
        by_date = {e["date"]: set(e["zones"]) for e in entries}
        dates = sorted(by_date.keys())
        for i in range(1, len(dates)):
            prev, cur = dates[i - 1], dates[i]
            # Skip if the two dates aren't consecutive (missing snapshot in between)
            try:
                if (datetime.date.fromisoformat(cur) - datetime.date.fromisoformat(prev)).days != 1:
                    continue
            except Exception:
                continue
            prev_zones = by_date[prev]
            cur_zones = by_date[cur]
            spread = sorted(cur_zones - prev_zones)
            retreat = sorted(prev_zones - cur_zones)
            if spread or retreat:
                movements.append({
                    "kind": kind,
                    "key": key,
                    "date": cur,
                    "spread_into": spread,
                    "retreated_from": retreat,
                    "stable_in": sorted(cur_zones & prev_zones),
                })
    # Chronological, oldest first
    movements.sort(key=lambda m: (m["date"], m["kind"], m["key"]))
    return movements


# ---------------------------------------------------------------------------
# Rollup: a single "tracking snapshot" for a UI or nightly report
# ---------------------------------------------------------------------------

def tracking_summary(days: int = 30) -> dict[str, Any]:
    """One-shot analysis for use by a future UI or nightly summary."""
    series = load_daily_state_series(days=days)
    return {
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "window_days": days,
        "snapshots_available": len(series),
        "snapshots_with_full_arrays": sum(1 for s in series if s["has_full_arrays"]),
        "per_bait_timeline": per_bait_timeline(series),
        "per_whale_timeline": per_whale_timeline(series),
        "per_zone_bait_timeline": per_zone_bait_timeline(series),
        "per_zone_whale_timeline": per_zone_whale_timeline(series),
        "bait_movements": detect_movements(series, kind="bait"),
        "whale_movements": detect_movements(series, kind="whale"),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--json", action="store_true", help="Emit full analysis as JSON")
    ap.add_argument("--bait", help="Focus on one bait species (e.g. bunker)")
    ap.add_argument("--whale", help="Focus on one whale species (e.g. humpback)")
    args = ap.parse_args()

    report = tracking_summary(days=args.days)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return

    print(f"╔══ Bait & Whale Movement Tracker ══╗")
    print(f"  Window: last {args.days} days")
    print(f"  Snapshots available: {report['snapshots_available']}")
    print(f"  Snapshots with full arrays: {report['snapshots_with_full_arrays']}")
    if report['snapshots_with_full_arrays'] == 0:
        print()
        print("  ⚠  No snapshots yet carry the full bait/whale arrays.")
        print("  ⚠  Tracking data starts accumulating with v23.13 (2026-08-09).")
        print("  ⚠  Run again after a couple of nightly builds to see movements.")
        return

    pb = report["per_bait_timeline"]
    pw = report["per_whale_timeline"]

    if args.bait:
        pb = {k: v for k, v in pb.items() if k == args.bait}
    if args.whale:
        pw = {k: v for k, v in pw.items() if k == args.whale}

    print()
    print(f"── BAIT footprint by species ──")
    if not pb:
        print("  (no bait entries in this window)")
    for bait, entries in sorted(pb.items()):
        print(f"\n  🐟 {bait}")
        for e in entries:
            src = "youtube_auto" if "youtube_auto" in e["source_types"] else "captain"
            print(f"    {e['date']}: {e['zone_count']:>2} zones · {e['entry_count']} entries · {src}")
            if e["zones"] and len(e["zones"]) <= 6:
                print(f"       {', '.join(e['zones'])}")

    print()
    print(f"── WHALE footprint by species ──")
    if not pw:
        print("  (no whale entries in this window)")
    for wh, entries in sorted(pw.items()):
        print(f"\n  🐋 {wh}")
        for e in entries:
            print(f"    {e['date']}: {e['zone_count']:>2} zones · {e['entry_count']} entries")

    bm = report["bait_movements"]
    wm = report["whale_movements"]
    print()
    print(f"── MOVEMENTS (day-over-day) ──")
    if not bm and not wm:
        print("  (need at least 2 consecutive snapshots with full arrays)")
    for m in bm[-15:]:
        marks = []
        if m["spread_into"]:
            marks.append(f"+ spread → {', '.join(m['spread_into'][:4])}{'…' if len(m['spread_into'])>4 else ''}")
        if m["retreated_from"]:
            marks.append(f"− retreat ← {', '.join(m['retreated_from'][:4])}{'…' if len(m['retreated_from'])>4 else ''}")
        print(f"  {m['date']} · 🐟 {m['key']:<20} {' | '.join(marks)}")
    for m in wm[-15:]:
        marks = []
        if m["spread_into"]:
            marks.append(f"+ spread → {', '.join(m['spread_into'][:4])}{'…' if len(m['spread_into'])>4 else ''}")
        if m["retreated_from"]:
            marks.append(f"− retreat ← {', '.join(m['retreated_from'][:4])}{'…' if len(m['retreated_from'])>4 else ''}")
        print(f"  {m['date']} · 🐋 {m['key']:<20} {' | '.join(marks)}")

    print()


if __name__ == "__main__":
    _main()
