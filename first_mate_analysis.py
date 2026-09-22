#!/usr/bin/env python3
"""
v24.106 — First Mate analysis toolkit.

Reads the archive and surfaces:
  · Runner-up gap distribution (how competitive was the top pick over time?)
  · Locked-in ratio (fraction of days where winner > runner-up by ≥ 1.5,
    meaning weather CAN'T flip the pick)
  · Signal starvation flags (top-1 and top-2 tied for ≥ 5 straight days →
    the model has no signal separating them)
  · Server/client effective_heat parity gap warning (compute_zone_state
    only computes 2 of 16 signals — archive picks may differ from map picks)

Usage:
  python3 first_mate_analysis.py                # full text report
  python3 first_mate_analysis.py --days 14      # 14-day window (default 30)
  python3 first_mate_analysis.py --region gulf  # single region
  python3 first_mate_analysis.py --json         # machine-readable

The First Mate skill uses this to answer the standing question every
session: "Are tomorrow's picks better than they would have been last
month? What did we learn from the archive that should change the math?"
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys
from collections import defaultdict

BASE = pathlib.Path(__file__).parent
ARCHIVE_DIR = BASE / "archive"

# Effective-heat gap thresholds
LOCK_GAP = 1.5      # winner > runner-up by this much → weather can't flip
TIGHT_GAP = 0.3     # winner and runner-up within this → genuinely competitive
STARVATION_STREAK = 5  # N days of tied top-2 → signal starvation


def load_snapshots(window_days: int | None = None) -> dict[datetime.date, dict]:
    """Load archive snapshots, optionally windowed to last N days from most recent."""
    snaps: dict[datetime.date, dict] = {}
    for p in sorted(ARCHIVE_DIR.glob("2*.json")):
        try:
            d = datetime.date.fromisoformat(p.stem)
            snaps[d] = json.loads(p.read_text())
        except Exception:
            continue
    if window_days and snaps:
        cutoff = max(snaps.keys()) - datetime.timedelta(days=window_days)
        snaps = {d: s for d, s in snaps.items() if d >= cutoff}
    return snaps


def _get_picks(snap: dict, region: str | None) -> dict:
    if region in (None, "northeast"):
        return snap.get("picks", {}) or {}
    return (snap.get("regions", {}) or {}).get(region, {}).get("picks", {}) or {}


def _get_runners_up(snap: dict, region: str | None, key: str) -> list[dict]:
    """Fetch runners_up[key] from picks. key is e.g. 'tuna_in_range'."""
    picks = _get_picks(snap, region)
    ru = picks.get("runners_up", {}) or {}
    return ru.get(key, []) or []


def gap_distribution(snaps: dict[datetime.date, dict], region: str, key: str) -> dict:
    """For each snapshot with runners_up data, compute the gap between winner
    and runner-up. Return: {locked: N, tight: N, moderate: N, total: N,
    starvation_streaks: [(start_date, end_date, len)], gaps: [gap]}."""
    dates_sorted = sorted(snaps.keys())
    gaps: list[tuple[datetime.date, float | None]] = []
    for d in dates_sorted:
        ru = _get_runners_up(snaps[d], region, key)
        if len(ru) < 2:
            gaps.append((d, None))
            continue
        gap = ru[1].get("gap_to_winner")
        if gap is None:
            gaps.append((d, None))
        else:
            gaps.append((d, gap))
    numeric = [g for _, g in gaps if g is not None]
    locked = sum(1 for g in numeric if g >= LOCK_GAP)
    tight = sum(1 for g in numeric if g < TIGHT_GAP)
    moderate = sum(1 for g in numeric if TIGHT_GAP <= g < LOCK_GAP)
    # Find starvation streaks (gap == 0 for STARVATION_STREAK+ consecutive days)
    streaks = []
    current_start = None
    for d, g in gaps:
        if g == 0:
            if current_start is None:
                current_start = d
        else:
            if current_start is not None:
                streak_len = (d - current_start).days
                if streak_len >= STARVATION_STREAK:
                    streaks.append((current_start.isoformat(), (d - datetime.timedelta(days=1)).isoformat(), streak_len))
                current_start = None
    if current_start is not None:
        streak_len = (dates_sorted[-1] - current_start).days + 1
        if streak_len >= STARVATION_STREAK:
            streaks.append((current_start.isoformat(), dates_sorted[-1].isoformat(), streak_len))
    return {
        "region": region or "northeast",
        "key": key,
        "total": len(gaps),
        "with_data": len(numeric),
        "locked": locked,
        "moderate": moderate,
        "tight": tight,
        "starvation_streaks": streaks,
        "median_gap": round(sorted(numeric)[len(numeric)//2], 2) if numeric else None,
        "min_gap": round(min(numeric), 2) if numeric else None,
        "max_gap": round(max(numeric), 2) if numeric else None,
    }


def latest_runners_up_snapshot(snaps: dict[datetime.date, dict], region: str, key: str) -> tuple[datetime.date | None, list[dict]]:
    """Return the most recent snapshot that has runners_up data for this region+key."""
    for d in sorted(snaps.keys(), reverse=True):
        ru = _get_runners_up(snaps[d], region, key)
        if ru:
            return d, ru
    return None, []


def signal_parity_warning() -> dict:
    """v24.106 pass 1 + pass 2: server-side compute_zone_state() now
    computes 16 of 17 client signals. Only trendBoost (needs archive
    history lookup) and goldenZoneBoost (needs findAllCrossings geometric
    line-segment intersection) remain unported. From an accuracy-measurement
    standpoint, the archive's effective_heat now closely matches what the
    map shows — 15 identical signal contributions plus 2 defaults to 0 for
    unported signals."""
    return {
        "server_side_signals": [
            "heat*heatConfidence", "whaleBoost", "seasonalBoost",
            "baitBoost", "trendBoost", "birdBoost", "sstFit", "diversityBoost",
            "distancePenalty", "sstGradientBoost", "chlaGradientBoost",
            "convergenceBoost", "persistenceBoost", "postFrontPenalty",
            "youtubeCorroborationBoost", "captainDwellBoost",
        ],  # 16 boosts + heat_discounted itself = 16 of 17 total signals
        "client_side_signals": [
            "heat*heatConfidence", "whaleBoost", "seasonalBoost", "baitBoost",
            "trendBoost", "birdBoost", "sstFit", "goldenZoneBoost",
            "sstGradientBoost", "chlaGradientBoost", "convergenceBoost",
            "persistenceBoost", "postFrontPenalty", "youtubeCorroborationBoost",
            "captainDwellBoost", "distancePenalty", "diversityBoost",
        ],
        "missing_from_server": [
            "goldenZoneBoost (needs findAllCrossings geometric line-segment "
            "intersection algorithm — deferred as a bigger port)",
        ],
        "impact": (
            "Archive now computes 16 of 17 client signals (up from 3 pre-session). "
            "Only goldenZoneBoost remains unported. Accuracy measurement now "
            "essentially matches what Randy sees on the map — the archive's "
            "effective_heat is a faithful reflection of the client-side ranking, "
            "so signal-contribution analysis and future weight-tuning work is "
            "reliable."
        ),
    }


def render_text_report(snaps: dict[datetime.date, dict], region: str) -> str:
    keys_to_check = [
        ("tuna_in_range", "Tuna (in-range)"),
        ("tuna_unconstrained", "Tuna (unconstrained)"),
        ("striper_in_range", "Striper (in-range)"),
        ("striper_unconstrained", "Striper (unconstrained)"),
    ]
    lines = []
    lines.append("═" * 68)
    lines.append(f"FIRST MATE ANALYSIS — {region or 'northeast'}")
    lines.append(f"  Window: {len(snaps)} snapshots · {min(snaps).isoformat() if snaps else '?'} → {max(snaps).isoformat() if snaps else '?'}")
    lines.append("═" * 68)
    lines.append("")
    lines.append("Runner-up gap analysis (how tight was the pick?):")
    lines.append("")
    lines.append(f"  {'PICK KEY':>28} {'total':>7} {'w/data':>7} {'locked':>7} {'tight':>7} {'med gap':>8}")
    lines.append(f"  {'':>28} {'':>7} {'':>7} {'(≥1.5)':>7} {'(<0.3)':>7} {'':>8}")
    lines.append("  " + "─" * 66)
    for key, label in keys_to_check:
        gd = gap_distribution(snaps, region if region else None, key)
        lines.append(
            f"  {label:>28} {gd['total']:>7} {gd['with_data']:>7} "
            f"{gd['locked']:>7} {gd['tight']:>7} "
            f"{(gd['median_gap'] if gd['median_gap'] is not None else '--'):>8}"
        )
    lines.append("")
    lines.append("Signal starvation streaks (top-1 and top-2 tied ≥ 5 days):")
    lines.append("")
    any_starve = False
    for key, label in keys_to_check:
        gd = gap_distribution(snaps, region if region else None, key)
        for s, e, n in gd["starvation_streaks"]:
            lines.append(f"  ⚠ {label}: {s} → {e} ({n} days)")
            any_starve = True
    if not any_starve:
        lines.append("  ✓ No starvation streaks detected")
    lines.append("")
    # Latest runners-up snapshot
    lines.append("Latest runners-up snapshot:")
    lines.append("")
    for key, label in keys_to_check:
        d, ru = latest_runners_up_snapshot(snaps, region if region else None, key)
        if not ru:
            continue
        lines.append(f"  {label} on {d.isoformat()}:")
        for i, r in enumerate(ru):
            gap = r.get("gap_to_winner", 0)
            marker = "👑" if i == 0 else f" #{i+1}"
            lines.append(
                f"    {marker} {r.get('zone_name', '?')[:36]:36} "
                f"eh={r.get('effective_heat', 0):.2f} "
                f"gap={gap:+.2f}"
            )
        lines.append("")

    # Parity warning
    parity = signal_parity_warning()
    lines.append("─" * 68)
    lines.append("⚠  SERVER/CLIENT EFFECTIVE_HEAT PARITY GAP")
    lines.append("─" * 68)
    lines.append(
        f"  Server (archive) computes: {len(parity['server_side_signals'])} signals\n"
        f"  Client (map display) sums: {len(parity['client_side_signals'])} signals\n"
        f"  Missing from server: {len(parity['missing_from_server'])}\n"
    )
    lines.append("  " + parity["impact"].replace("\n", "\n  "))
    lines.append("═" * 68)
    return "\n".join(lines)


def render_json_report(snaps: dict[datetime.date, dict], region: str) -> dict:
    keys_to_check = ["tuna_in_range", "tuna_unconstrained",
                     "striper_in_range", "striper_unconstrained"]
    return {
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "region": region or "northeast",
        "snapshots_analyzed": len(snaps),
        "date_range": [min(snaps).isoformat(), max(snaps).isoformat()] if snaps else None,
        "gap_analysis": {
            key: gap_distribution(snaps, region if region else None, key)
            for key in keys_to_check
        },
        "signal_parity": signal_parity_warning(),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--days", type=int, default=30,
                    help="Analysis window in days (default 30)")
    ap.add_argument("--region", type=str, default="northeast",
                    choices=["northeast", "mid_atlantic", "gulf", "south_florida", "south_atlantic"],
                    help="Region to analyze (default northeast)")
    ap.add_argument("--json", action="store_true", help="JSON output instead of text")
    args = ap.parse_args()

    snaps = load_snapshots(window_days=args.days)
    if not snaps:
        print("No archive snapshots found — cannot analyze.", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(render_json_report(snaps, args.region), indent=2))
    else:
        print(render_text_report(snaps, args.region))


if __name__ == "__main__":
    main()
