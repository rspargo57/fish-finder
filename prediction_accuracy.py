#!/usr/bin/env python3
"""
v24.88 (Randy 2026-09-03) — First Mate: prediction accuracy scorer.

The First Mate skill's core mandate is to answer: "Are tomorrow's picks
better than they would have been last month?" That requires actual scoring
of past predictions against past outcomes.

**Method.** Every nightly build writes an archive snapshot to
`/root/fish-finder/archive/YYYY-MM-DD.json` that includes:
  * `picks` — the model's top offshore + inshore pick for that day
  * `look_ahead` — a 7-day forecast the model made ON that day

To score prediction accuracy at horizon Δ days:
  1. For each historical date D that has a look_ahead prediction for D+Δ,
     read the predicted zone_id from that day's `look_ahead[Δ]`.
  2. Find the snapshot for date D+Δ. Read the actual top pick's zone_id.
  3. Score:
       exact zone match             → 1.0
       different zone, same category → 0.5
       nothing (no snapshot for D+Δ) → skip
  4. Aggregate over the last 30 days of scorable predictions.

A healthy model shows `accuracy_1 > accuracy_3 > accuracy_7` (nearer is
easier). Watch for `accuracy_1 < 0.6` (model unstable) or `accuracy_1 ≈
accuracy_7` (day-to-day forecast has no effect — bug).

**Output.** Writes `data/prediction_accuracy.json` with per-horizon means,
sample counts, and the list of scored days. Also flags any signal-related
warnings the First Mate should look into.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics
from datetime import date, datetime, timedelta

BASE = pathlib.Path("/root/fish-finder")
ARCHIVE = BASE / "archive"
OUT_JSON = BASE / "data" / "prediction_accuracy.json"

# Zones roll up into a category — offshore-ish vs inshore-ish. We assume
# the archive keeps offshore/inshore separately as `picks.tuna` +
# `picks.striper`. Same-category-different-zone → half credit.

def _load(fn: pathlib.Path) -> dict | None:
    try:
        return json.loads(fn.read_text())
    except Exception:
        return None

def _snapshot_by_date() -> dict[str, dict]:
    out = {}
    for fn in sorted(ARCHIVE.glob("2026-*.json")):
        d = _load(fn)
        if not d: continue
        out[fn.stem] = d
    return out

def _predicted(snap: dict, horizon: int, category: str) -> str | None:
    """Return the zone_id predicted at horizon days ahead for `category`
    ("offshore" or "inshore"). Reads snap['look_ahead'] (a list of daily
    forecasts). Some snapshots may store the offshore pick under 'offshore'
    or under 'tuna' — try both."""
    la = snap.get("look_ahead") or []
    if horizon >= len(la):
        return None
    day = la[horizon]
    if not isinstance(day, dict): return None
    for key in (category, category[0], f"{category}_pick"):
        v = day.get(key)
        if isinstance(v, dict) and v.get("zone_id"):
            return v["zone_id"]
        if isinstance(v, str) and v: return v
    # look_ahead may store the pick under 'top' or the whole day's dict
    for key in ("top", "pick"):
        v = day.get(key)
        if isinstance(v, dict) and v.get("zone_id"):
            return v["zone_id"]
    return None

def _actual(snap: dict, category: str) -> str | None:
    """Return the actual top pick zone_id from snap['picks']. category is
    'offshore' or 'inshore'."""
    picks = snap.get("picks") or {}
    if not isinstance(picks, dict): return None
    # Try both keying schemes: offshore/inshore or tuna/striper
    for key in {"offshore": ["offshore", "tuna", "offshore_pick", "tuna_pick"],
                "inshore":  ["inshore", "striper", "inshore_pick", "striper_pick"]}[category]:
        v = picks.get(key)
        if isinstance(v, dict) and v.get("zone_id"):
            return v["zone_id"]
    return None

def _score(predicted: str | None, actual: str | None, category: str, zone_categories: dict) -> float | None:
    if not predicted or not actual: return None
    if predicted == actual: return 1.0
    # Same "family" (both offshore, or both inshore, or same sub-region)
    p_cat = zone_categories.get(predicted)
    a_cat = zone_categories.get(actual)
    if p_cat and a_cat and p_cat == a_cat:
        return 0.5
    return 0.0

def _zone_categories() -> dict[str, str]:
    """Load zone→category (offshore/inshore/nearshore) from all region files."""
    out = {}
    for fn in ["zones.json", "zones_mid_atlantic.json", "zones_south_atlantic.json",
               "zones_gulf.json", "zones_south_florida.json"]:
        try:
            d = json.loads((BASE / "data" / fn).read_text())
            for z in d.get("zones", []):
                zid = z.get("id")
                cat = z.get("category") or z.get("type") or "unknown"
                if zid: out[zid] = cat
        except Exception:
            pass
    return out

def run(verbose: bool = False) -> dict:
    snaps = _snapshot_by_date()
    zone_cats = _zone_categories()
    horizons = [1, 3, 7]
    categories = ["offshore", "inshore"]

    if verbose:
        print(f"Loaded {len(snaps)} archive snapshots, {len(zone_cats)} zones with categories")

    scored: dict[int, dict[str, list]] = {h: {c: [] for c in categories} for h in horizons}
    scored_days: list[dict] = []

    dates = sorted(snaps.keys())
    for i, d_str in enumerate(dates):
        snap = snaps[d_str]
        d = datetime.strptime(d_str, "%Y-%m-%d").date()
        day_entry = {"date": d_str}
        for h in horizons:
            target_str = (d + timedelta(days=h)).isoformat()
            actual_snap = snaps.get(target_str)
            if not actual_snap: continue
            for cat in categories:
                pred = _predicted(snap, h, cat)
                actu = _actual(actual_snap, cat)
                s = _score(pred, actu, cat, zone_cats)
                if s is None: continue
                scored[h][cat].append(s)
                day_entry[f"h{h}_{cat}"] = {"pred": pred, "actual": actu, "score": s}
                if verbose:
                    print(f"  {d_str} → +{h}d {cat:8}: pred={pred[:30]:32} actual={actu[:30]:32} score={s}")
        if any(k.startswith("h") for k in day_entry):
            scored_days.append(day_entry)

    # Aggregate
    summary = {}
    warnings = []
    for h in horizons:
        for cat in categories:
            vals = scored[h][cat]
            if not vals: continue
            m = statistics.mean(vals)
            n = len(vals)
            summary[f"accuracy_{h}_{cat}"] = {"mean": round(m, 3), "n": n}
        # Overall (both categories)
        all_vals = scored[h]["offshore"] + scored[h]["inshore"]
        if all_vals:
            summary[f"accuracy_{h}_overall"] = {"mean": round(statistics.mean(all_vals), 3), "n": len(all_vals)}

    # Warnings
    a1 = summary.get("accuracy_1_overall", {}).get("mean")
    a3 = summary.get("accuracy_3_overall", {}).get("mean")
    a7 = summary.get("accuracy_7_overall", {}).get("mean")
    if a1 is not None and a1 < 0.6:
        warnings.append(f"accuracy_1 = {a1:.2f} — model unstable day-to-day. Something is over-weighted.")
    if a1 is not None and a7 is not None and abs(a1 - a7) < 0.05:
        warnings.append(f"accuracy_1 ≈ accuracy_7 ({a1:.2f} vs {a7:.2f}) — day-to-day forecast has no effect. Bug?")
    if a7 is not None and a3 is not None and a7 > a3 + 0.05:
        warnings.append(f"accuracy_7 > accuracy_3 ({a7:.2f} vs {a3:.2f}) — investigate.")

    result = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "snapshots_examined": len(snaps),
        "date_range": [dates[0], dates[-1]] if dates else None,
        "summary": summary,
        "warnings": warnings,
        "scored_days": scored_days[-30:],  # last 30 days of detail
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, indent=2))
    if verbose:
        print(f"\nwrote {OUT_JSON}")
        print("\nSummary:")
        for k, v in summary.items():
            print(f"  {k}: {v['mean']:.3f}  (n={v['n']})")
        if warnings:
            print("\nWarnings:")
            for w in warnings:
                print(f"  ⚠ {w}")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()
    run(verbose=args.verbose)


if __name__ == "__main__":
    main()
