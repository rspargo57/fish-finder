#!/usr/bin/env python3
"""
v24.96 — Picks-vs-signal audit.

For each region, computes tomorrow's top 5 picks and counts each pick's
corroboration signals. Flags "hollow" picks — high effective_heat but
< 2 real corroboration signals. Writes `data/picks_audit.json`.

Corroboration signals per zone (max 5):
  · intel_sources.youtube present
  · intel_sources.multi_source_refresh present
  · Any fresh (≤ 10 days) bait_intel entry tagged with this zone
  · Any fresh (≤ 7 days) whale sighting within 20 nm of zone center
  · heat_updated ≤ 14 days old

Flagged as hollow: effective_heat ≥ 6.5 AND corroboration ≤ 1.

**When to run:** end of each nightly build, after all harvesters + refreshes
have written zone updates. Baked into archive snapshot's `picks_audit`
field so historical trends are trackable.
"""
from __future__ import annotations

import datetime
import json
import math
import pathlib

BASE = pathlib.Path("/root/fish-finder")
DATA = BASE / "data"

REGIONS = [
    ("NE",     "zones.json"),
    ("MIDATL", "zones_mid_atlantic.json"),
    ("SEATL",  "zones_south_atlantic.json"),
    ("GULF",   "zones_gulf.json"),
    ("SOFL",   "zones_south_florida.json"),
]

# effective_heat components (server-side approximation of the map's JS)
def _seasonal_boost(peak: int) -> float:
    if peak >= 9: return 0.7
    if peak >= 7: return 0.4
    if peak >= 5: return 0.1
    if peak >= 3: return -0.3
    if peak >= 1: return -0.8
    return -1.5


def _days_since(iso: str | None, today: datetime.date) -> int:
    if not iso: return 9999
    try:
        return (today - datetime.date.fromisoformat(iso[:10])).days
    except Exception:
        return 9999


def _rank_region(region_data: dict, today: datetime.date, tmrw_month_idx: int) -> list[dict]:
    zones = region_data.get("zones", [])
    bait = region_data.get("bait_intel", []) or []
    whales = region_data.get("whale_sightings", []) or []

    ranked = []
    for z in zones:
        heat = z.get("heat", 5) or 5
        sp = z.get("seasonal_presence", {}) or {}
        peak = 0
        for arr in sp.values():
            if isinstance(arr, list) and len(arr) == 12:
                if arr[tmrw_month_idx] > peak:
                    peak = arr[tmrw_month_idx]
        sb = _seasonal_boost(peak)

        # Bait boost
        zid = z.get("id")
        bb = 0.3 if any(
            zid in (b.get("zones") or []) and _days_since(b.get("date"), today) <= 10
            for b in bait
        ) else 0.0

        # Whale boost (proximity + age)
        wb = 0.0
        center = z.get("center") or [0, 0]
        for w in whales:
            iso = w.get("date") or w.get("observed_on") or w.get("sighting_date")
            if _days_since(iso, today) > 7:
                continue
            wlat = w.get("lat") or w.get("latitude")
            wlon = w.get("lon") or w.get("longitude")
            if wlat is None or wlon is None:
                continue
            dy = (wlat - center[0]) * 60
            dx = (wlon - center[1]) * 60 * math.cos(math.radians(center[0]))
            if math.hypot(dx, dy) <= 20:
                wb = 0.8
                break

        # Corroboration count (max 5)
        intel = z.get("intel_sources", {}) or {}
        corrob = 0
        if intel.get("youtube"): corrob += 1
        if intel.get("multi_source_refresh"): corrob += 1
        if bb > 0: corrob += 1
        if wb > 0: corrob += 1
        if _days_since(z.get("heat_updated"), today) <= 14: corrob += 1

        eh = heat + sb + bb + wb
        ranked.append({
            "zone_id":         zid,
            "zone_name":       z.get("name"),
            "effective_heat":  round(eh, 2),
            "heat":            heat,
            "season_boost":    round(sb, 2),
            "bait_boost":      round(bb, 2),
            "whale_boost":     round(wb, 2),
            "peak_seasonal":   peak,
            "corroboration":   corrob,
            "heat_updated_age":_days_since(z.get("heat_updated"), today),
        })
    ranked.sort(key=lambda r: -r["effective_heat"])
    return ranked


def audit(*, today: datetime.date | None = None, hollow_eh_threshold: float = 6.5,
          hollow_corrob_threshold: int = 1, verbose: bool = False) -> dict:
    today = today or datetime.date.today()
    tmrw = today + datetime.timedelta(days=1)
    tmi = tmrw.month - 1

    result = {
        "generated_at":            datetime.datetime.utcnow().isoformat() + "Z",
        "for_date":                tmrw.isoformat(),
        "hollow_eh_threshold":     hollow_eh_threshold,
        "hollow_corrob_threshold": hollow_corrob_threshold,
        "regions":                 {},
        "hollow_picks":            [],
    }

    for code, fname in REGIONS:
        p = DATA / fname
        if not p.exists():
            continue
        d = json.loads(p.read_text())
        ranked = _rank_region(d, today, tmi)
        top5 = ranked[:5]
        hollow_here = [
            r for r in top5
            if r["effective_heat"] >= hollow_eh_threshold
            and r["corroboration"] <= hollow_corrob_threshold
        ]
        result["regions"][code] = {
            "top5":            top5,
            "hollow_in_top5":  len(hollow_here),
            "total_zones":     len(ranked),
        }
        for h in hollow_here:
            result["hollow_picks"].append({"region": code, **h})

        if verbose:
            marker = f" 🚨 {len(hollow_here)} HOLLOW" if hollow_here else ""
            print(f"{code:6}: top1 eh={top5[0]['effective_heat']:.2f} · {len(top5)} in top5{marker}")
            for r in top5:
                m2 = " 🚨" if r["effective_heat"] >= hollow_eh_threshold and r["corroboration"] <= hollow_corrob_threshold else ""
                print(f"    {r['effective_heat']:5.2f} · corrob={r['corroboration']}/5 · hu={r['heat_updated_age']}d · {r['zone_id']}{m2}")

    result["total_hollow"] = len(result["hollow_picks"])
    (DATA / "picks_audit.json").write_text(json.dumps(result, indent=2))
    if verbose:
        print(f"\nTotal hollow picks: {result['total_hollow']}")
        print(f"Wrote {DATA / 'picks_audit.json'}")
    return result


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()
    audit(verbose=args.verbose)


if __name__ == "__main__":
    main()
