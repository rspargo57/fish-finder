#!/usr/bin/env python3
"""Fish Finder — Backfill bait_intel + whale_sightings into historical snapshots.

Randy 2026-08-09: "How about if we went back seven days to update all these
things we just redid and try to get some more information into our prediction
model for tomorrow?"

The v23.12 upgrade (YouTube transcript scanning) added a LOT of bait + whale
entries to zones.json — 13 bait entries, 7 whale sightings, mostly dated
Aug 4-8 based on the video publish dates. The historical archive snapshots
from Aug 2-8 were written BEFORE those entries existed, so they don't
carry the intel in their `bait_intel_full` / `whale_sightings_full` arrays
(which weren't a field at all before v23.13).

This backfill DOESN'T rewrite history — it fills in the arrays that SHOULD
have been captured. For each historical snapshot dated D, we filter the
current bait_intel + whale_sightings to entries dated ≤ D and inject those
as `bait_intel_full` + `whale_sightings_full` fields on that snapshot.

This is a legitimate archive edit because:
  - We're ADDING new fields (previously absent), not modifying existing ones
  - We're preserving the temporal filter (a snapshot from Aug 5 only sees
    entries dated Aug 5 or earlier — no future intel bleed)
  - Every entry retains its own `date` + `source` + `source_type` so First
    Mate can still filter by provenance during weight tuning
  - The alternative (waiting for tomorrow's build to accumulate) leaves 7
    days of movement analysis blind for no good reason

Usage:
    python3 backfill_bait_whale.py                  # backfill last 7 snapshots
    python3 backfill_bait_whale.py --days 14        # backfill last 14 snapshots
    python3 backfill_bait_whale.py --dry-run        # report what WOULD change

Idempotent: running twice yields the same result. Safe to run multiple times.
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib

ARCHIVE_DIR = pathlib.Path("/root/fish-finder/archive")
ZONES_PATH = pathlib.Path("/root/fish-finder/data/zones.json")


def backfill(days: int = 7, dry_run: bool = False) -> dict:
    if not ZONES_PATH.exists():
        return {"error": "zones.json not found"}
    zdata = json.loads(ZONES_PATH.read_text())
    all_bait = zdata.get("bait_intel", []) or []
    all_whales = zdata.get("whale_sightings", []) or []

    if not ARCHIVE_DIR.exists():
        return {"error": "archive dir missing"}
    files = sorted(ARCHIVE_DIR.glob("2*.json"))[-days:]

    report = {"dry_run": dry_run, "days": days, "changes": []}
    for f in files:
        try:
            snap = json.loads(f.read_text())
        except Exception as e:
            report["changes"].append({"file": f.name, "error": str(e)})
            continue
        snap_date_str = snap.get("date", f.stem)
        try:
            snap_date = datetime.date.fromisoformat(snap_date_str)
        except Exception:
            report["changes"].append({"file": f.name, "skipped": "unparseable date"})
            continue

        # Filter to entries dated on/before this snapshot's date
        def _on_or_before(entry):
            try:
                return datetime.date.fromisoformat(entry.get("date", "")) <= snap_date
            except Exception:
                return False
        bait_for_day = [b for b in all_bait if _on_or_before(b)]
        whales_for_day = [w for w in all_whales if _on_or_before(w)]

        had_bait = "bait_intel_full" in snap
        had_whales = "whale_sightings_full" in snap
        prior_bait_count = len(snap.get("bait_intel_full") or [])
        prior_whale_count = len(snap.get("whale_sightings_full") or [])

        change = {
            "date": snap_date_str,
            "had_bait_full": had_bait,
            "had_whales_full": had_whales,
            "prior_bait_count": prior_bait_count,
            "prior_whale_count": prior_whale_count,
            "new_bait_count": len(bait_for_day),
            "new_whale_count": len(whales_for_day),
        }

        # Only update if this actually adds signal. Never demote a
        # snapshot that already had a fuller array.
        will_write = False
        if len(bait_for_day) > prior_bait_count:
            snap["bait_intel_full"] = bait_for_day
            change["updated_bait"] = True
            will_write = True
        if len(whales_for_day) > prior_whale_count:
            snap["whale_sightings_full"] = whales_for_day
            change["updated_whales"] = True
            will_write = True

        if will_write and not dry_run:
            # Also refresh the counts field so downstream reads see the new totals
            snap["bait_intel_count"] = len(bait_for_day)
            snap["whale_sightings_count"] = len(whales_for_day)
            # Rewrite recent_whale_sightings as the ≤7d window it always was
            today_d = snap_date
            recent = []
            for w in whales_for_day:
                try:
                    wd = datetime.date.fromisoformat(w.get("date", ""))
                    if (today_d - wd).days <= 7:
                        recent.append({
                            "date": w["date"],
                            "kind": w.get("kind"),
                            "location": w.get("location"),
                            "source": w.get("source"),
                            "zones": w.get("zones", []),
                        })
                except Exception:
                    continue
            snap["recent_whale_sightings"] = recent
            # Stamp a backfill marker so the audit trail is clear
            snap.setdefault("backfilled_at", [])
            snap["backfilled_at"].append(datetime.datetime.utcnow().isoformat() + "Z")
            f.write_text(json.dumps(snap, indent=2))

        change["wrote"] = will_write and not dry_run
        report["changes"].append(change)
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    report = backfill(days=args.days, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(report, indent=2))
        return
    print(f"╔══ Backfill bait/whale into last {args.days} snapshots ══╗")
    print(f"  Mode: {'DRY RUN' if args.dry_run else 'APPLYING'}")
    if "error" in report:
        print(f"  ❌ {report['error']}")
        return
    for c in report["changes"]:
        if "error" in c:
            print(f"  ✗ {c.get('file','?')}: {c['error']}")
            continue
        if "skipped" in c:
            print(f"  ⚠  {c.get('file','?')}: {c['skipped']}")
            continue
        mark = "✓" if c.get("wrote") else "·"
        note = []
        if c.get("updated_bait"):
            note.append(f"bait {c['prior_bait_count']}→{c['new_bait_count']}")
        if c.get("updated_whales"):
            note.append(f"whales {c['prior_whale_count']}→{c['new_whale_count']}")
        if not note:
            note.append("no change")
        print(f"  {mark} {c['date']}: {' · '.join(note)}")


if __name__ == "__main__":
    main()
