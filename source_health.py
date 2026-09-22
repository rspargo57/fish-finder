#!/usr/bin/env python3
"""
v24.100 — Source-health tracker.

Every harvester (OTW multi-source, iNat cetaceans + bait, Viking Fleet
whales, YouTube RSS, NOAA tides, NDBC buoys, Coastal Angler, Fisherman
Magazine) should log its last-success timestamp + returned-row-count.
When a source silently fails for 3+ days the dashboard flags it so
Randy sees the outage before it drags the model down.

**Read/write pattern.** `data/source_health.json` is a single JSON
document with the following shape:

    {
      "generated_at": "2026-09-04T…Z",
      "sources": {
        "otw_multi": {
          "last_success":   "2026-09-04",
          "last_rows":      21,
          "last_result":    "ok",
          "consecutive_fails": 0,
          "history": [ {"date": "…", "rows": …, "result": "ok|fail"}, … 30 max ]
        },
        …
      }
    }

**API.** Harvesters call `record(source_name, rows=N, ok=True|False,
error=None)`; the tracker appends to history + updates rollup fields.
Dashboard + preflight read the state and warn on any source with
`consecutive_fails ≥ 3` or `last_success` more than 5 days ago.

Called from build-inlined.py after each harvester step + at the end
to write the summary. Safe to call from anywhere — it's just a
file-scoped JSON with dict/list operations.
"""
from __future__ import annotations

import datetime
import json
import pathlib
from typing import Any

BASE = pathlib.Path(__file__).parent
STATE_PATH = BASE / "data" / "source_health.json"
HISTORY_MAX = 30

# Sources we expect to hear from at each nightly build. If a source is
# missing from state.sources after a build, it's flagged UNKNOWN.
KNOWN_SOURCES = [
    "otw_multi",        # multi_source_zone_refresh (OTW + Fisherman + Coastal Angler)
    "youtube",          # YouTube RSS harvester
    "inat_whales",      # iNaturalist cetacean sightings
    "inat_bait",        # iNaturalist bait-fish sightings
    "viking_whales",    # Viking Fleet + CRESLI whale scraper
    # v24.106 — ndbc_buoys re-added after wiring the harvester into
    # build-inlined.py (was flagged as an unreplaced placeholder crashing
    # the client-side report/drawers).
    "ndbc_buoys",       # NDBC realtime2 buoy observations
    "tides",            # NOAA CO-OPS tide predictions
    # v24.105 (Randy 2026-09-05) — per-region SST + chla tracking. A single
    # region failing (CoastWatch has an outage or dataset rotation for one
    # bbox) is masked when we bundle them into one "sst_contours" flag.
    # Split them so the dashboard can surface exactly which region went dark.
    "sst_contours_ne",       # NOAA CoastWatch SST break contours (NE+MidAtl)
    "sst_contours_gulf",     # NOAA CoastWatch SST break contours (Gulf)
    "sst_contours_sofl",     # NOAA CoastWatch SST break contours (S.FL)
    "sst_contours_seatl",    # NOAA CoastWatch SST break contours (SE)
    "chla_contours_ne",      # NOAA CoastWatch chlorophyll edges (NE+MidAtl)
    "chla_contours_gulf",    # NOAA CoastWatch chlorophyll edges (Gulf)
    "chla_contours_sofl",    # NOAA CoastWatch chlorophyll edges (S.FL)
    "chla_contours_seatl",   # NOAA CoastWatch chlorophyll edges (SE)
    "zone_weather",     # NWS per-zone forecast (all regions)
    "archive_save",     # v24.104 — did the nightly actually write today's snapshot?
    # v24.105 — surface the 3 remaining silent-swallow harvesters. If any of
    # these silently fails (their mega-try blocks eat the traceback) we can now
    # see it in source_health.json + the dashboard.
    "fisherman_harvest",   # The Fisherman weekly regional forecasts
    "fisherman_regional",  # The Fisherman /area/<region>/feed/ RSS
    "charter_harvest",     # Viking Fleet + Oregon Inlet + J&J + OTW podcast + FL/Gulf/SE bundle
]

# Freshness thresholds (calendar days since last successful pull)
FRESH_MAX_DAYS   = 2
STALE_MAX_DAYS   = 5
# > STALE_MAX_DAYS = "dead"

# Fails-in-a-row threshold for the "silent outage" flag
DEAD_FAIL_STREAK = 3


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {"generated_at": "", "sources": {}}
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {"generated_at": "", "sources": {}}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def record(source: str, *, rows: int | None = None, ok: bool = True,
           error: str | None = None) -> None:
    """Record one harvest attempt. Called by each harvester after it runs."""
    today = datetime.date.today().isoformat()
    state = _load_state()
    state["generated_at"] = datetime.datetime.utcnow().isoformat() + "Z"
    src = state.setdefault("sources", {}).setdefault(source, {
        "last_success": None, "last_rows": None, "last_result": None,
        "consecutive_fails": 0, "history": [],
    })
    entry: dict[str, Any] = {"date": today, "rows": rows,
                             "result": "ok" if ok else "fail"}
    if error:
        entry["error"] = str(error)[:200]
    src["history"].append(entry)
    src["history"] = src["history"][-HISTORY_MAX:]
    if ok:
        src["last_success"] = today
        src["last_rows"] = rows
        src["last_result"] = "ok"
        src["consecutive_fails"] = 0
    else:
        src["last_result"] = "fail"
        src["consecutive_fails"] = int(src.get("consecutive_fails", 0)) + 1
        if error:
            src["last_error"] = str(error)[:200]
    _save_state(state)


def _days_since(iso: str | None, today: datetime.date) -> int:
    if not iso: return 99_999
    try: return (today - datetime.date.fromisoformat(iso[:10])).days
    except Exception: return 99_999


def summary(today: datetime.date | None = None) -> dict:
    """Produce a dashboard-friendly summary of every known source."""
    today = today or datetime.date.today()
    state = _load_state()
    sources = state.get("sources", {})
    out: dict[str, Any] = {"generated_at": state.get("generated_at"),
                           "date": today.isoformat(), "sources": {}}
    flags = 0
    for name in KNOWN_SOURCES:
        src = sources.get(name)
        if not src:
            out["sources"][name] = {"status": "unknown",
                                    "days_since_success": None,
                                    "consecutive_fails": None,
                                    "last_rows": None}
            flags += 1
            continue
        days = _days_since(src.get("last_success"), today)
        fails = int(src.get("consecutive_fails", 0))
        if fails >= DEAD_FAIL_STREAK or days > STALE_MAX_DAYS:
            status = "dead"
        elif days > FRESH_MAX_DAYS or fails > 0:
            status = "stale"
        else:
            status = "fresh"
        if status != "fresh":
            flags += 1
        out["sources"][name] = {
            "status": status,
            "days_since_success": days if days < 99_999 else None,
            "consecutive_fails": fails,
            "last_rows": src.get("last_rows"),
            "last_success": src.get("last_success"),
            "last_error": src.get("last_error"),
        }
    out["flags"] = flags
    out["total_known"] = len(KNOWN_SOURCES)
    return out


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()
    s = summary()
    print(f"Source health: {s['total_known'] - s['flags']}/{s['total_known']} fresh · {s['flags']} flagged")
    if args.verbose:
        for name, info in s["sources"].items():
            emoji = "✓" if info["status"] == "fresh" else "⚠" if info["status"] == "stale" else "✗"
            days = info.get("days_since_success")
            rows = info.get("last_rows")
            print(f"  {emoji} {name:20} {info['status']:>7} · last {days if days is not None else '?'}d ago · rows={rows}")


if __name__ == "__main__":
    main()
