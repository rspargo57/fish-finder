#!/usr/bin/env python3
"""
v24.99 — Data-pruner. Keeps zone files from growing unbounded.

The model only reads bait_intel entries ≤ 10 days old (baitBoost) and whale
sightings ≤ 7 days old (whaleBoost). Anything older is dead weight in the
shipped HTML — bloating the ZONE_DATA blob and doing nothing for the picks.

Historical bait/whale is preserved separately in the archive snapshots
(bait_intel_full / whale_sightings_full), so pruning the current zones
files is safe: yesterday's build already recorded yesterday's state, and
the model's decisions don't reach back past the ≤ 10 / ≤ 7 windows.

**Retention.** Default: keep last 90 days of both bait_intel and whale
sightings in the live zones files. That's ~9x the model's read window and
enough for any Randy-facing debugging ("what have we seen for this zone
lately") without dragging around a year of every menhaden observation.

**When to run:** end of each nightly build, after all harvesters have
written but before build-inlined.py embeds zones into the HTML.
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib

BASE = pathlib.Path(__file__).parent
DATA = BASE / "data"

RETENTION_DAYS_DEFAULT = 90

REGION_FILES = [
    "zones.json",
    "zones_mid_atlantic.json",
    "zones_south_atlantic.json",
    "zones_gulf.json",
    "zones_south_florida.json",
]


def _entry_date(e: dict) -> str | None:
    return e.get("date") or e.get("observed_on") or e.get("sighting_date")


def _days_since(iso: str | None, today: datetime.date) -> int:
    if not iso:
        return 99_999  # unknown-date entries are treated as ancient (pruned)
    try:
        return (today - datetime.date.fromisoformat(iso[:10])).days
    except Exception:
        return 99_999


def prune_file(path: pathlib.Path, retention_days: int, today: datetime.date,
               verbose: bool = False) -> dict:
    if not path.exists():
        return {"path": str(path), "skipped": True}
    d = json.loads(path.read_text())

    bait = d.get("bait_intel") or []
    whales = d.get("whale_sightings") or []

    kept_bait = [b for b in bait if _days_since(_entry_date(b), today) <= retention_days]
    kept_whales = [w for w in whales if _days_since(_entry_date(w), today) <= retention_days]

    d["bait_intel"] = kept_bait
    d["whale_sightings"] = kept_whales

    path.write_text(json.dumps(d, indent=2))

    result = {
        "path":                path.name,
        "bait_before":         len(bait),
        "bait_after":          len(kept_bait),
        "bait_pruned":         len(bait) - len(kept_bait),
        "whales_before":       len(whales),
        "whales_after":        len(kept_whales),
        "whales_pruned":       len(whales) - len(kept_whales),
    }
    if verbose:
        print(f"  {path.name:35}: bait {len(bait)} → {len(kept_bait)} "
              f"(−{result['bait_pruned']}), whales {len(whales)} → {len(kept_whales)} "
              f"(−{result['whales_pruned']})")
    return result


def run(retention_days: int = RETENTION_DAYS_DEFAULT, verbose: bool = False) -> list[dict]:
    today = datetime.date.today()
    if verbose:
        print(f"Data-pruner running: keep last {retention_days} days (today = {today})")
    return [prune_file(DATA / fn, retention_days, today, verbose=verbose)
            for fn in REGION_FILES]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=RETENTION_DAYS_DEFAULT,
                    help=f"Retention window in days (default {RETENTION_DAYS_DEFAULT}).")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()
    results = run(retention_days=args.days, verbose=args.verbose)
    total_bait = sum(r.get("bait_pruned", 0) for r in results)
    total_whales = sum(r.get("whales_pruned", 0) for r in results)
    print(f"\nTotal pruned: {total_bait} bait_intel + {total_whales} whale_sightings")


if __name__ == "__main__":
    main()
