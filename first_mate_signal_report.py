#!/usr/bin/env python3
"""
v24.106 (First Mate) — signal contribution report.

Reads today's archive snapshot and reports:
  · How often each of the 17 signals fires across all zones (firing rate)
  · Which signals contributed to today's top picks
  · Which signals never fire (dormant/broken/waiting on data)
  · Signals that dominate the ranking (could be over-weighted)

Runs against the archive's stored zones_state — that's the 17-signal
server-side compute that faithfully mirrors the client-side effectiveHeat
after v24.106's parity closure.

Usage:
  python3 first_mate_signal_report.py                # NE, today
  python3 first_mate_signal_report.py --region gulf  # single region
  python3 first_mate_signal_report.py --date 2026-08-30  # historical snapshot
  python3 first_mate_signal_report.py --json         # machine-readable
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys

BASE = pathlib.Path(__file__).parent
ARCHIVE_DIR = BASE / "archive"

# The 17 signal fields in zones_state (as of v24.106)
SIGNALS = [
    "whale_boost", "season_boost", "sst_fit", "bait_boost", "bird_boost",
    "diversity_boost", "distance_penalty", "sst_gradient_boost",
    "chla_gradient_boost", "convergence_boost", "persistence_boost",
    "post_front_penalty", "youtube_corroboration_boost", "captain_dwell_boost",
    "trend_boost", "golden_zone_boost",
]

# heat_confidence isn't a "boost" — it's a MULTIPLIER on the base heat.
# We track heat_discounted separately as the fully-computed base.


def _load_snapshot(date_str: str | None = None) -> tuple[dict, pathlib.Path]:
    if date_str:
        p = ARCHIVE_DIR / f"{date_str}.json"
        if not p.exists():
            raise SystemExit(f"No snapshot at {p}")
    else:
        candidates = sorted(ARCHIVE_DIR.glob("2*.json"))
        if not candidates:
            raise SystemExit("No archive snapshots found")
        p = candidates[-1]
    return json.loads(p.read_text()), p


def _get_zones_state(snap: dict, region: str) -> dict:
    if region in (None, "northeast"):
        return snap.get("zones_state") or {}
    rblock = (snap.get("regions") or {}).get(region) or {}
    # v24.106 pass 7 — per-region zones_state now stored (was NE-only until
    # this pass). Historical snapshots before 2026-09-06 don't have it;
    # for those, a rebuild would be needed to backfill.
    return rblock.get("zones_state") or {}


def analyze_signal_firing(zone_state: dict) -> list[dict]:
    """For each signal, report:
      · firing_count / total_zones (how many zones this fires on)
      · mean_when_firing (average boost value when it fires)
      · max_seen (the largest boost seen)
    """
    total = len(zone_state)
    if total == 0:
        return []
    out = []
    for sig in SIGNALS:
        vals = []
        for zst in zone_state.values():
            v = zst.get(sig, 0) or 0
            if abs(v) > 0.001:
                vals.append(v)
        n = len(vals)
        pct = n / total * 100 if total else 0
        mean_v = sum(vals) / n if vals else 0
        max_v = max(vals, key=abs) if vals else 0
        out.append({
            "signal": sig,
            "firing_count": n,
            "total_zones": total,
            "firing_pct": round(pct, 1),
            "mean_when_firing": round(mean_v, 3),
            "max_seen": round(max_v, 2),
        })
    return out


def analyze_top_pick(zone_state: dict, picks: dict) -> dict:
    """Break down the top tuna + top striper pick's signal contributions."""
    out = {}
    for label, key in [("Top Tuna", "tuna"), ("Top Striper", "striper")]:
        pick = picks.get(key)
        if not pick:
            continue
        zid = pick.get("zone_id")
        zst = zone_state.get(zid, {})
        if not zst:
            continue
        contribs = []
        base = zst.get("heat_discounted", 0)
        contribs.append({"signal": "heat_discounted (base)", "value": round(base, 2)})
        for sig in SIGNALS:
            v = zst.get(sig, 0) or 0
            if abs(v) > 0.001:
                contribs.append({"signal": sig, "value": round(v, 2)})
        contribs.sort(key=lambda c: -abs(c["value"]))
        out[label] = {
            "zone_name": pick.get("zone_name"),
            "zone_id": zid,
            "effective_heat": pick.get("effective_heat"),
            "contributions": contribs,
        }
    return out


def render_text(zone_state: dict, picks: dict, region: str, date: str) -> str:
    lines = []
    lines.append("═" * 68)
    lines.append(f"FIRST MATE SIGNAL REPORT — {region} · {date}")
    lines.append(f"  Zones analyzed: {len(zone_state)}")
    lines.append("═" * 68)
    lines.append("")

    # Firing rates
    firing = analyze_signal_firing(zone_state)
    lines.append("Signal firing rates:")
    lines.append("")
    lines.append(f"  {'Signal':30} {'Fires':>10} {'Mean':>7} {'Max':>7}")
    lines.append("  " + "─" * 60)
    for f in firing:
        bar = "▓" * int(f["firing_pct"] / 5)
        lines.append(
            f"  {f['signal']:30} {f['firing_count']:>3}/{f['total_zones']:<3} "
            f"({f['firing_pct']:>5.1f}%) {f['mean_when_firing']:>+7.2f} {f['max_seen']:>+7.2f} {bar}"
        )
    lines.append("")

    # Flag dormant signals
    dormant = [f for f in firing if f["firing_count"] == 0]
    if dormant:
        lines.append(f"⚠  {len(dormant)} DORMANT signals (fired on 0 zones):")
        for f in dormant:
            lines.append(f"    · {f['signal']}")
        lines.append("")

    # Top pick breakdowns
    top_picks = analyze_top_pick(zone_state, picks)
    if top_picks:
        lines.append("Top pick signal contributions:")
        lines.append("")
        for label, info in top_picks.items():
            lines.append(f"  {label}: {info['zone_name']} (eh={info['effective_heat']})")
            for c in info["contributions"]:
                lines.append(f"    {c['signal']:35} {c['value']:>+7.2f}")
            lines.append("")

    lines.append("═" * 68)
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--region", default="northeast",
                    choices=["northeast", "mid_atlantic", "gulf", "south_florida", "south_atlantic"])
    ap.add_argument("--date", default=None,
                    help="Snapshot date YYYY-MM-DD (default: latest)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    snap, path = _load_snapshot(args.date)
    zs = _get_zones_state(snap, args.region)
    if not zs and args.region != "northeast":
        print(f"No zones_state stored for region '{args.region}' — only NE zones_state is in the top-level snapshot today.",
              file=sys.stderr)
        print("Per-region zones_state is a TODO — for now, use --region northeast.",
              file=sys.stderr)
        sys.exit(1)

    picks = snap.get("picks", {}) if args.region == "northeast" else (
        (snap.get("regions", {}) or {}).get(args.region, {}) or {}
    ).get("picks", {})

    date_str = snap.get("date") or path.stem

    if args.json:
        report = {
            "date": date_str,
            "region": args.region,
            "firing": analyze_signal_firing(zs),
            "top_picks": analyze_top_pick(zs, picks),
        }
        print(json.dumps(report, indent=2))
    else:
        print(render_text(zs, picks, args.region, date_str))


if __name__ == "__main__":
    main()
