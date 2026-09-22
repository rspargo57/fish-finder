#!/usr/bin/env python3
"""
v24.102 — Archive integrity checker.

The archive is the model's memory — First Mate skill: "if the archive
stops growing, the model stops learning." But nothing was watching it.
Discovered on 2026-09-05: sandbox had archive up to 2026-08-30 (5 days
stale) even though nightly triggers reported SUCCEEDED — the safe-deploy
was clobbering the bootstrap tarball with stale archive on every deploy.

**What this checks.**

  1. Today OR yesterday exists as an archive snapshot. If neither, the
     nightly build hasn't successfully archived recently — big red flag.
  2. index.json matches disk (drift detection).
  3. Every snapshot parses as valid JSON.
  4. No unexpected size regression on the latest N snapshots (a build
     that shrinks from 400KB → 50KB likely lost data).
  5. Gap detection — count consecutive-day gaps in the last 14 days.

**Status classes.**

  · HEALTHY   — today OR yesterday present, no corruption, ≤1 recent gap
  · DEGRADED  — 2-3 days stale, or scattered gaps, or one size drop
  · CRITICAL  — > 3 days stale, or corruption, or index-disk drift

Preflight refuses to deploy on CRITICAL. Dashboard surfaces the class
so Randy sees when learning is stalled.
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re

BASE = pathlib.Path(__file__).parent
ARCHIVE_DIR = BASE / "archive"

# Size-regression trip: latest < LATEST_FLOOR_RATIO × 3-day-median means
# the build likely dropped a bunch of data.
LATEST_FLOOR_RATIO = 0.4


def _snapshot_files() -> list[str]:
    return sorted(
        f.name for f in ARCHIVE_DIR.glob("*.json")
        if re.match(r"^20\d\d-\d\d-\d\d\.json$", f.name)
    )


def audit(today: datetime.date | None = None) -> dict:
    today = today or datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)

    result: dict = {
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "date": today.isoformat(),
        "status": "healthy",
        "warnings": [],
        "errors": [],
        "stats": {},
    }

    if not ARCHIVE_DIR.exists():
        result["status"] = "critical"
        result["errors"].append("archive/ directory missing")
        return result

    files = _snapshot_files()
    result["stats"]["total_snapshots"] = len(files)
    if not files:
        result["status"] = "critical"
        result["errors"].append("no archive snapshots at all")
        return result

    # --- Freshness ---
    today_iso = today.isoformat() + ".json"
    yesterday_iso = yesterday.isoformat() + ".json"
    latest = files[-1]
    latest_date = datetime.date.fromisoformat(latest.replace(".json", ""))
    days_since_latest = (today - latest_date).days
    result["stats"]["latest"] = latest
    result["stats"]["days_since_latest"] = days_since_latest

    if today_iso not in files and yesterday_iso not in files:
        result["status"] = "critical"
        result["errors"].append(
            f"neither today ({today_iso}) nor yesterday ({yesterday_iso}) present — "
            f"latest is {latest} ({days_since_latest} days stale)"
        )
    elif today_iso not in files:
        # Yesterday's OK but today missing — normal until nightly fires
        # (which usually runs at 22:00 UTC). Not an error, just informational.
        result["warnings"].append(
            f"today's snapshot ({today_iso}) not yet written — nightly build fires at 22:00 UTC"
        )

    # --- Index/disk drift ---
    idx_path = ARCHIVE_DIR / "index.json"
    if idx_path.exists():
        try:
            idx = json.loads(idx_path.read_text())
            idx_days = set(idx.get("days", []))
            disk_set = set(files)
            in_disk_not_idx = disk_set - idx_days
            in_idx_not_disk = idx_days - disk_set
            if in_disk_not_idx or in_idx_not_disk:
                result["warnings"].append(
                    f"index/disk drift: {len(in_disk_not_idx)} on disk missing from index, "
                    f"{len(in_idx_not_disk)} in index missing from disk"
                )
                result["stats"]["drift"] = {
                    "in_disk_not_idx": sorted(in_disk_not_idx)[:10],
                    "in_idx_not_disk": sorted(in_idx_not_disk)[:10],
                }
        except Exception as e:
            result["errors"].append(f"archive/index.json parse failed: {e}")

    # --- Corruption + size regression ---
    sizes: list[tuple[str, int]] = []
    for fn in files[-10:]:
        p = ARCHIVE_DIR / fn
        try:
            _ = json.loads(p.read_text())
            sizes.append((fn, p.stat().st_size))
        except Exception as e:
            result["errors"].append(f"corrupt snapshot {fn}: {e}")
    if len(sizes) >= 4:
        recent = [s for _, s in sizes[-4:-1]]
        latest_size = sizes[-1][1]
        median = sorted(recent)[len(recent) // 2]
        if latest_size < median * LATEST_FLOOR_RATIO:
            result["warnings"].append(
                f"latest snapshot ({latest_size:,}b) is < {int(LATEST_FLOOR_RATIO*100)}% "
                f"of 3-day median ({median:,}b) — build may have dropped data"
            )

    # --- Gap detection (last 14 days) ---
    window_start = today - datetime.timedelta(days=14)
    gap_dates = []
    d = window_start
    while d <= today:
        if (d.isoformat() + ".json") not in files and d < today:
            gap_dates.append(d.isoformat())
        d += datetime.timedelta(days=1)
    result["stats"]["gaps_last_14d"] = len(gap_dates)
    if len(gap_dates) >= 5:
        result["status"] = "critical" if result["status"] != "critical" else "critical"
        result["errors"].append(
            f"{len(gap_dates)} missing days in last 14: {', '.join(gap_dates[:5])}"
            f"{' …' if len(gap_dates) > 5 else ''}"
        )
    elif len(gap_dates) >= 2:
        if result["status"] == "healthy":
            result["status"] = "degraded"
        result["warnings"].append(
            f"{len(gap_dates)} missing days in last 14: {', '.join(gap_dates)}"
        )

    if result["errors"]:
        result["status"] = "critical"
    elif result["warnings"] and result["status"] == "healthy":
        result["status"] = "degraded"

    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()
    r = audit()
    emoji = {"healthy": "✅", "degraded": "⚠️ ", "critical": "❌"}[r["status"]]
    print(f"{emoji} Archive integrity: {r['status'].upper()}")
    print(f"   {r['stats'].get('total_snapshots', 0)} snapshots · "
          f"latest {r['stats'].get('latest')} ({r['stats'].get('days_since_latest')}d ago)")
    for w in r["warnings"]:
        print(f"   ⚠  {w}")
    for e in r["errors"]:
        print(f"   ❌ {e}")
    # Also write to data/archive_integrity.json for the dashboard
    out = BASE / "data" / "archive_integrity.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(r, indent=2))
    if args.verbose:
        print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
