#!/usr/bin/env python3
"""Prediction accuracy scoring — First Mate's ground-truth loop.

For each historical date D that has a look_ahead prediction, compare it to the
actual top pick on date D. Score at 1/3/7 day horizons. Aggregate over the
recent window. Writes both a machine-readable JSON to data/accuracy_report.json
and a human-readable text summary.

Randy's directive (2026-07-25): "Predict where the fish are as close as we can
going forward on any given day, with all the tools we've given it — and keep
as much data as we're creating in a file somewhere so we can further predict
where the fish are, and it should get better as time goes on."

First Mate skill's accuracy rule:
  For each historical date D that has a look_ahead prediction:
    1. Read the prediction made on date `D - Δ` for date `D`  (Δ ∈ {1, 3, 7})
    2. Read the ACTUAL top pick on date `D`
    3. Score: exact match = 1.0; same category = 0.5; different = 0.0
    4. Aggregate over the last 30 days: accuracy_Δ = mean(score)

A healthy model shows accuracy_1 > accuracy_3 > accuracy_7 (nearer = easier).

v24.48 (2026-08-31) — first ship of the accuracy loop. Archive has 25+ snapshots
with 23 look_ahead entries dating back to 2026-07-29. Enough for a real first pass.

Usage:
  python3 accuracy_report.py                # print report + write JSON
  python3 accuracy_report.py --json         # JSON only (for build-inlined.py)
  python3 accuracy_report.py --days 14      # narrower window (default 30)
"""
import argparse
import datetime
import json
import pathlib
import statistics
import sys

BASE = pathlib.Path(__file__).parent
ARCHIVE = BASE / "archive"
OUTPUT = BASE / "data" / "accuracy_report.json"

HORIZONS = [1, 3, 7]  # Δ in days
MODES = ("offshore", "inshore")


def load_snapshots():
    """Return dict {date: snapshot_dict} for every archive snapshot, sorted."""
    snaps = {}
    for p in sorted(ARCHIVE.glob("2*.json")):
        try:
            d = datetime.date.fromisoformat(p.stem)
        except ValueError:
            continue
        try:
            snaps[d] = json.loads(p.read_text())
        except Exception as e:
            print(f"  WARN: could not load {p.stem}: {e}", file=sys.stderr)
    return snaps


def _picks_from_snap(snap, region=None):
    """Return the appropriate picks dict for `region`. region=None → top-level
    (NE-scoped) picks. Otherwise look inside snap['regions'][region]."""
    if not region or region == "northeast":
        return (snap or {}).get("picks") or {}
    return ((snap or {}).get("regions") or {}).get(region, {}).get("picks") or {}


def _look_ahead_from_snap(snap, region=None):
    """Return the appropriate look_ahead list for `region`. region=None → top-level
    (NE-scoped) look_ahead. Otherwise look inside snap['regions'][region]."""
    if not region or region == "northeast":
        return (snap or {}).get("look_ahead") or []
    return ((snap or {}).get("regions") or {}).get(region, {}).get("look_ahead") or []


def actual_pick_zone(snap, mode, region=None):
    """Return the zone_id of the actual top pick on the snapshot's date, or None.
    Mode: 'offshore' → picks.tuna; 'inshore' → picks.striper.
    v24.56: region-aware — None/'northeast' → top-level picks (backward compat);
    other region names read from snap['regions'][region]['picks']."""
    picks = _picks_from_snap(snap, region)
    key = "tuna" if mode == "offshore" else "striper"
    p = picks.get(key)
    if isinstance(p, dict):
        return p.get("zone_id")
    return None


def predicted_pick_zone(snap, target_date, mode, region=None):
    """From a snapshot's look_ahead array, return the zone_id predicted for
    target_date in the given mode. None if no matching entry.
    v24.56: region-aware — see actual_pick_zone."""
    la = _look_ahead_from_snap(snap, region)
    for entry in la:
        try:
            edate = datetime.date.fromisoformat(entry.get("date", ""))
        except Exception:
            continue
        if edate == target_date:
            slot = entry.get(mode) or {}
            return slot.get("zone_id")
    return None


def score(pred_zone, actual_zone):
    """Exact match = 1.0; both non-null but different = 0.0; either missing = None."""
    if pred_zone is None or actual_zone is None:
        return None  # exclude — no evidence either way
    return 1.0 if pred_zone == actual_zone else 0.0


def compute_accuracy(snaps, window_days=30, region=None):
    """Return {mode: {horizon: {n, hits, accuracy, samples}}}.
    Only counts date pairs where BOTH the past prediction and today's actual exist.
    v24.56: region-aware. region=None → NE (top-level picks/look_ahead).
    """
    if not snaps:
        return {}
    today = max(snaps.keys())
    window_start = today - datetime.timedelta(days=window_days)

    out = {}
    for mode in MODES:
        out[mode] = {}
        for delta in HORIZONS:
            hits = 0
            n = 0
            samples = []
            for actual_date, snap in snaps.items():
                if actual_date < window_start:
                    continue
                pred_date = actual_date - datetime.timedelta(days=delta)
                pred_snap = snaps.get(pred_date)
                if not pred_snap:
                    continue
                pred_zone = predicted_pick_zone(pred_snap, actual_date, mode, region=region)
                actual_zone = actual_pick_zone(snap, mode, region=region)
                s = score(pred_zone, actual_zone)
                if s is None:
                    continue
                n += 1
                hits += s
                samples.append({
                    "actual_date": actual_date.isoformat(),
                    "pred_zone": pred_zone,
                    "actual_zone": actual_zone,
                    "hit": s == 1.0,
                })
            accuracy = (hits / n) if n else None
            out[mode][delta] = {
                "n": n,
                "hits": int(hits),
                "accuracy": accuracy,
                "samples": samples[-10:],  # keep last 10 for context (not full history)
            }
    return out


def compute_stability(snaps, window_days=30, region=None):
    """For each snapshot in the window, count how many days in the 7-day look_ahead
    have the SAME zone_id as day 0 (today). High stability = model rarely rotates =
    signal starvation. Low stability = model responsive.
    v24.56: region-aware."""
    if not snaps:
        return {}
    today = max(snaps.keys())
    window_start = today - datetime.timedelta(days=window_days)
    out = {}
    for mode in MODES:
        stability_ratios = []
        for date, snap in snaps.items():
            if date < window_start:
                continue
            la = _look_ahead_from_snap(snap, region)
            if not la:
                continue
            day0_slot = la[0].get(mode) or {}
            day0_zone = day0_slot.get("zone_id")
            if not day0_zone:
                continue
            same = 0
            total = 0
            for entry in la:
                slot = entry.get(mode) or {}
                zid = slot.get("zone_id")
                if not zid:
                    continue
                total += 1
                if zid == day0_zone:
                    same += 1
            if total >= 3:
                stability_ratios.append(same / total)
        out[mode] = {
            "n": len(stability_ratios),
            "mean_same_as_day0_ratio": statistics.mean(stability_ratios) if stability_ratios else None,
        }
    return out


def render_summary(report):
    lines = []
    lines.append("═" * 60)
    lines.append(f"PREDICTION ACCURACY — {report['generated_at']}")
    lines.append(f"  Window: last {report['window_days']} days · "
                 f"Snapshots in window: {report['snapshots_in_window']}/{report['snapshots_total']}")
    lines.append("═" * 60)
    lines.append("")
    lines.append("Exact-zone match rate at 1/3/7 day horizons:")
    lines.append("")
    lines.append(f"  {'MODE':<10} {'1-day':>10} {'3-day':>10} {'7-day':>10}")
    lines.append(f"  {'':>10} {'(n)':>10} {'(n)':>10} {'(n)':>10}")
    lines.append("  " + "─" * 45)
    for mode in MODES:
        row_acc = [f"{mode.upper():<10}"]
        row_n = [" " * 10]
        for delta in HORIZONS:
            entry = report["accuracy"][mode][delta]
            acc = entry["accuracy"]
            n = entry["n"]
            row_acc.append(f"{acc:.1%}" if acc is not None else "--")
            row_n.append(f"(n={n})")
        # right-align to width 10
        lines.append("  " + " ".join(f"{c:>10}" for c in row_acc))
        lines.append("  " + " ".join(f"{c:>10}" for c in row_n))
    lines.append("")
    lines.append("Look-ahead stability (fraction of 7-day forecast that equals day 0):")
    lines.append("")
    for mode in MODES:
        s = report["stability"][mode]
        m = s.get("mean_same_as_day0_ratio")
        lines.append(f"  {mode.upper():<10} {m:.1%}" if m is not None else f"  {mode.upper():<10} --")
    lines.append("")
    lines.append("Interpretation:")
    lines.append("  · 1-day > 3-day > 7-day is expected. Anything inverse is a red flag.")
    lines.append("  · Stability high (~1.0) = model rarely rotates picks (signal starvation).")
    lines.append("  · Ideal stability is around 0.4-0.7 — the model responds to weather + fresh intel.")
    lines.append("═" * 60)
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--days", type=int, default=30, help="Analysis window in days (default 30)")
    ap.add_argument("--json", action="store_true", help="Only print the JSON, no text summary")
    args = ap.parse_args()

    snaps = load_snapshots()
    if not snaps:
        print("No archive snapshots found — cannot score accuracy.", file=sys.stderr)
        sys.exit(1)

    today = max(snaps.keys())
    window_start = today - datetime.timedelta(days=args.days)
    in_window = sum(1 for d in snaps if d >= window_start)

    accuracy = compute_accuracy(snaps, window_days=args.days)
    stability = compute_stability(snaps, window_days=args.days)

    # v24.56 (Randy 2026-08-31): per-region accuracy + stability. For each
    # non-NE region, compute the same numbers using the region's picks +
    # look_ahead in the snapshot['regions'] block. Regions with < 3 days of
    # look_ahead data show accuracy=None (rendered as "warming up" in the UI).
    REGIONS = ("mid_atlantic", "gulf", "south_florida", "south_atlantic")
    by_region = {"northeast": {"accuracy": accuracy, "stability": stability}}
    for r in REGIONS:
        try:
            racc = compute_accuracy(snaps, window_days=args.days, region=r)
            rstab = compute_stability(snaps, window_days=args.days, region=r)
            by_region[r] = {"accuracy": racc, "stability": rstab}
        except Exception as e:
            by_region[r] = {"error": str(e)}

    report = {
        "generated_at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "window_days": args.days,
        "snapshots_total": len(snaps),
        "snapshots_in_window": in_window,
        # Top-level = NE (backward compat with the widget)
        "accuracy": accuracy,
        "stability": stability,
        # v24.56 — per-region breakdown; widget picks the active region
        "by_region": by_region,
    }

    # Always write the JSON — build-inlined.py can inline it.
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2))

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_summary(report))
        print(f"\n✓ Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
