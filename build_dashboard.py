#!/usr/bin/env python3
"""
v24.91 (Randy 2026-09-03) — Zone Health Dashboard builder.

Compiles `data/dashboard_state.json` from all 5 region files + prediction
accuracy, then injects that state into `zone-health-dashboard.html`. Called
by build-inlined.py each nightly build so the dashboard artifact stays
current.

**When to run:** at the END of every nightly build, after all zone files
are updated + accuracy scorer has run.

**Output:** `zone-health-dashboard.html` (self-contained, ready to publish
as an artifact via the standing tile artifact URL, or serve directly).
"""
from __future__ import annotations

import datetime
import json
import pathlib

BASE = pathlib.Path(__file__).parent
DATA = BASE / "data"
DASH_HTML = BASE / "zone-health-dashboard.html"
STATE_JSON = DATA / "dashboard_state.json"

REGIONS = [
    ("NE",      "zones.json",                   "Northeast · Old Saybrook",    "🌊"),
    ("MIDATL",  "zones_mid_atlantic.json",      "Mid-Atlantic · Cape May",     "🦀"),
    ("SEATL",   "zones_south_atlantic.json",    "SE Coast · Carolinas",        "🌴"),
    ("GULF",    "zones_gulf.json",              "Gulf · Perdido / Panhandle",  "🐡"),
    ("SOFL",    "zones_south_florida.json",     "S. Florida · Keys",           "🏝"),
]


def _days_since(iso: str | None, today: datetime.date) -> int | None:
    if not iso:
        return None
    try:
        return (today - datetime.date.fromisoformat(iso[:10])).days
    except Exception:
        return None


def compile_state(today: datetime.date | None = None) -> dict:
    today = today or datetime.date.today()
    summary: dict[str, dict] = {}

    for code, fname, label, emoji in REGIONS:
        p = DATA / fname
        if not p.exists():
            continue
        d = json.loads(p.read_text())
        zones = d.get("zones", [])

        fresh = stale = dead = unknown = 0
        stalest: list[dict] = []
        for z in zones:
            days = _days_since(z.get("heat_updated"), today)
            if days is None:      unknown += 1
            elif days <= 10:      fresh   += 1
            elif days <= 21:      stale   += 1
            else:                 dead    += 1
            if days is not None:
                stalest.append({
                    "id":       z.get("id"),
                    "name":     z.get("name", z.get("id")),
                    "days":     days,
                    "heat":     z.get("heat", 0),
                    "category": z.get("category", z.get("type", "")),
                })
        stalest.sort(key=lambda z: -z["days"])

        top_by_heat = sorted(zones, key=lambda z: -z.get("heat", 0))
        top_pick = None
        if top_by_heat:
            tp = top_by_heat[0]
            top_pick = {"name": tp.get("name"), "heat": tp.get("heat")}

        # Latest whale sighting
        latest_whale_iso: str | None = None
        for w in d.get("whale_sightings", []):
            w_iso = w.get("date") or w.get("observed_on") or w.get("sighting_date")
            if w_iso and (latest_whale_iso is None or w_iso > latest_whale_iso):
                latest_whale_iso = w_iso
        latest_whale_days = _days_since(latest_whale_iso, today)

        summary[code] = {
            "code":              code,
            "label":             label,
            "emoji":             emoji,
            "zone_count":        len(zones),
            "fresh":             fresh,
            "stale":             stale,
            "dead":              dead,
            "unknown":           unknown,
            "top_pick":          top_pick,
            "stalest_top5":      stalest[:5],
            "whale_count":       len(d.get("whale_sightings", [])),
            "latest_whale_days": latest_whale_days,
            "bait_count":        len(d.get("bait_intel", [])),
        }

    # Prediction accuracy
    accuracy: dict = {}
    warnings: list[str] = []
    archive_days = 0
    p = DATA / "prediction_accuracy.json"
    if p.exists():
        acc = json.loads(p.read_text())
        accuracy = acc.get("summary", {})
        warnings = acc.get("warnings", []) or []
        archive_days = acc.get("snapshots_examined", 0)

    # v24.102 — archive-integrity rollup (is the model's memory growing?)
    arch_integrity: dict = {}
    ai_path = DATA / "archive_integrity.json"
    if ai_path.exists():
        try:
            arch_integrity = json.loads(ai_path.read_text())
        except Exception:
            arch_integrity = {}

    # v24.100 — source-health rollup (which harvesters are alive vs silently failing)
    src_health: dict = {}
    sh_path = DATA / "source_health.json"
    if sh_path.exists():
        try:
            import source_health as _sh
            src_health = _sh.summary(today=today)
        except Exception:
            src_health = {}

    # v24.96 — picks-vs-signal audit (hollow-pick count per region)
    picks_audit: dict = {}
    pa_path = DATA / "picks_audit.json"
    if pa_path.exists():
        pa = json.loads(pa_path.read_text())
        picks_audit = {
            "total_hollow": pa.get("total_hollow", 0),
            "hollow_picks": pa.get("hollow_picks", []),
            "per_region_hollow": {
                code: (r.get("hollow_in_top5") or 0)
                for code, r in (pa.get("regions") or {}).items()
            },
        }
        # merge per-region hollow count into region summary
        for code, r in summary.items():
            r["hollow_in_top5"] = picks_audit["per_region_hollow"].get(code, 0)

    return {
        "generated_at":      datetime.datetime.utcnow().isoformat() + "Z",
        "date":              today.isoformat(),
        "regions":           summary,
        "accuracy":          accuracy,
        "accuracy_warnings": warnings,
        "archive_days":      archive_days,
        "picks_audit":       picks_audit,
        "source_health":     src_health,
        "archive_integrity": arch_integrity,
    }


def inject_state_into_html(state: dict) -> str:
    """Read the dashboard HTML template, replace the placeholder with the
    compiled state JSON, return the rewritten HTML string."""
    html = DASH_HTML.read_text()

    # Two forms to be resilient across rebuilds:
    #   const DASHBOARD_STATE = /* DASHBOARD_STATE_PLACEHOLDER */ null;
    #   const DASHBOARD_STATE = /* DASHBOARD_STATE_PLACEHOLDER */ {...};
    #
    # Strategy: find the assignment, then walk from `const DASHBOARD_STATE = `
    # to the terminating `;` and replace whatever's in between with the
    # freshly serialised state.
    needle = "const DASHBOARD_STATE ="
    i = html.find(needle)
    if i < 0:
        raise SystemExit("dashboard template missing DASHBOARD_STATE assignment")

    # Find the end of the statement — the next `;` outside any brace/string.
    j = i + len(needle)
    depth = 0
    in_str = False
    esc = False
    str_ch = ""
    end = -1
    while j < len(html):
        c = html[j]
        if in_str:
            if esc: esc = False
            elif c == "\\": esc = True
            elif c == str_ch: in_str = False
        else:
            if c in ('"', "'", "`"):
                in_str = True
                str_ch = c
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            elif c == ";" and depth == 0:
                end = j
                break
        j += 1
    if end < 0:
        raise SystemExit("could not find end of DASHBOARD_STATE assignment")

    new_assign = f" {json.dumps(state)}"
    return html[:i + len(needle)] + new_assign + html[end:]


def rebuild(today: datetime.date | None = None, verbose: bool = False) -> dict:
    state = compile_state(today=today)
    STATE_JSON.write_text(json.dumps(state, indent=2))
    if verbose:
        print(f"wrote {STATE_JSON} — {len(state['regions'])} regions")

    new_html = inject_state_into_html(state)
    DASH_HTML.write_text(new_html)
    if verbose:
        print(f"wrote {DASH_HTML} — {len(new_html):,} bytes")
    return state


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()
    state = rebuild(verbose=args.verbose)
    if args.verbose:
        for code, r in state["regions"].items():
            tp = r["top_pick"]
            print(f"  {code:6}: {r['zone_count']:>3} zones · fresh={r['fresh']:>3} stale={r['stale']:>3} dead={r['dead']:>3} · top: {tp['name'] if tp else '-'}")


if __name__ == "__main__":
    main()
