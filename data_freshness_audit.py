#!/usr/bin/env python3
"""Fish Finder — Data Freshness Audit.

Randy's 2026-07-30 directive: "The Captain has to make sure that every item
we're tracking — whales, bait, whatever — updates daily, and the First Mate
uses all that data. I don't know why the First Mate didn't pick up that the
whale information wasn't being updated."

This script inspects every data pipeline the effectiveHeat model depends on,
scores its freshness, and writes a machine-readable report to the archive
snapshot AND prints a human-readable summary. Called from build-inlined.py
so the nightly 8pm build catches any silent staleness the same night it
happens.

Never again should a stale signal go unnoticed.
"""
import json
import datetime
import pathlib

BASE = pathlib.Path("/root/fish-finder")


def _days_since(iso, today):
    try:
        return (today - datetime.date.fromisoformat(iso)).days
    except Exception:
        return None


def _status_label(days, warn, dead):
    if days is None:
        return "unknown"
    if days > dead:
        return "dead"
    if days > warn:
        return "stale"
    return "fresh"


def _status_emoji(label):
    return {"fresh": "🟢", "stale": "🟡", "dead": "🔴", "unknown": "❔"}.get(label, "•")


def run_audit(zones_data=None, latest_snapshot=None, today=None, verbose=True):
    """Return a dict summarizing signal freshness. If zones_data or
    latest_snapshot are None, reads them from disk. Prints a summary if
    verbose=True (default). The returned dict is what get baked into the
    archive snapshot under `freshness_audit`."""
    today = today or datetime.date.today()
    if zones_data is None:
        zones_data = json.loads((BASE / "data" / "zones.json").read_text())
    if latest_snapshot is None:
        arch = BASE / "archive"
        files = sorted(arch.glob("2*.json")) if arch.exists() else []
        latest_snapshot = json.loads(files[-1].read_text()) if files else {}

    audit = {"date": today.isoformat(), "generated_at": today.isoformat(), "signals": {}}

    # ---- WHALE SIGHTINGS ----
    ws = zones_data.get("whale_sightings", [])
    wd = sorted([s["date"] for s in ws if s.get("date")], reverse=True)
    latest_w = wd[0] if wd else None
    days_w = _days_since(latest_w, today) if latest_w else None
    active_w = [d for d in wd if _days_since(d, today) is not None and _days_since(d, today) <= 7]
    audit["signals"]["whale_sightings"] = {
        "auto_fetch": True,
        "source": "CRESLI/Viking Fleet whale-watch report (whale_harvest.py)",
        "latest_date": latest_w,
        "days_since": days_w,
        "status": _status_label(days_w, 3, 7),
        "active_in_boost_window": len(active_w),
        "boost_window_days": 7,
        "total_entries": len(ws),
    }

    # ---- BAIT INTEL ----
    bi = zones_data.get("bait_intel", [])
    bd = sorted([s["date"] for s in bi if s.get("date")], reverse=True)
    latest_b = bd[0] if bd else None
    days_b = _days_since(latest_b, today) if latest_b else None
    active_b = [d for d in bd if _days_since(d, today) is not None and _days_since(d, today) <= 10]
    audit["signals"]["bait_intel"] = {
        "auto_fetch": False,
        "source": "MANUAL — Captain hunts OTW captain quotes + tackle-shop reports",
        "latest_date": latest_b,
        "days_since": days_b,
        "status": _status_label(days_b, 5, 10),
        "active_in_boost_window": len(active_b),
        "boost_window_days": 10,
        "total_entries": len(bi),
    }

    # ---- BIRD INTEL ----
    bird = zones_data.get("bird_intel", [])
    audit["signals"]["bird_intel"] = {
        "auto_fetch": False,
        "source": "NONE — birdBoost placeholder, returns 0 until source found",
        "latest_date": None,
        "days_since": None,
        "status": "dead" if len(bird) == 0 else _status_label(
            _days_since(sorted([b.get("date") for b in bird], reverse=True)[0], today), 3, 5),
        "total_entries": len(bird),
    }

    # ---- LIVE HEAT (zone.heat) ----
    # As of 2026-07-31 every zone carries a `heat_updated` ISO date. We audit
    # freshness per-zone: > 10 days stale, > 21 days dead.
    #
    # v24.49 (Randy 2026-08-31, First Mate) — the overall status now rolls up
    # from the TOP-PICKED zones (top 5 by heat), not from the worst zone in the
    # fleet. Rationale: a fleet of 42 zones will always have some obscure never-
    # touched spot dragging the fleet-wide "worst" number to DEAD, but that
    # doesn't reflect actual prediction quality. What matters is whether the
    # zones the model is CURRENTLY RANKING AT THE TOP have fresh captain intel.
    # The fleet-wide numbers stay in the report as secondary hygiene info.
    zones = zones_data.get("zones", [])
    zones_with_heat_updated = sum(1 for z in zones if z.get("heat_updated"))
    zone_ages = []
    for z in zones:
        hu = z.get("heat_updated")
        days = _days_since(hu, today) if hu else None
        zone_ages.append({
            "id": z.get("id"), "name": z.get("name"),
            "days": days, "heat": z.get("heat"), "heat_updated": hu,
        })
    n_stale = sum(1 for za in zone_ages if za["days"] is not None and 10 < za["days"] <= 21)
    n_dead = sum(1 for za in zone_ages if za["days"] is not None and za["days"] > 21)
    n_untimestamped = sum(1 for za in zone_ages if za["days"] is None)
    max_age = max((za["days"] for za in zone_ages if za["days"] is not None), default=None)
    stalest_zones = sorted(
        [za for za in zone_ages if za["days"] is not None and za["days"] > 10],
        key=lambda za: -za["days"],
    )[:10]

    # Top-N focus: what do the zones the model would actually pick look like?
    # Sort by raw zone.heat (the input to effectiveHeat) — top 5 is what makes
    # picks. Compute their max age → THIS drives the overall live_heat status.
    top_n = sorted(zone_ages, key=lambda za: -(za["heat"] or 0))[:5]
    top_ages = [za["days"] for za in top_n if za["days"] is not None]
    if not top_ages:
        top_status = "unknown"
    else:
        top_max = max(top_ages)
        if top_max > 21:
            top_status = "dead"
        elif top_max > 10:
            top_status = "stale"
        elif top_max > 7:
            top_status = "aging"  # 8-10d — still counts as fresh but noteworthy
        else:
            top_status = "fresh"

    # Overall status: top-pick focus (v24.49). Only marked worse than fresh
    # if top zones themselves are stale.
    if n_untimestamped > 0 and top_status == "unknown":
        heat_status = "unknown"
    elif top_status in ("dead", "stale"):
        heat_status = top_status
    else:
        heat_status = "fresh"

    # v24.113 (2026-09-11 First Mate) — flag "hollow" CURRENT pick:
    # the specific zone that latest snapshot ranked #1 for tuna/striper.
    # This is different from top_picks (top-5 by BASE heat) because a zone
    # can be #1 by effective_heat via boost accumulation while having a
    # very stale zone.heat. When THAT happens, the model is essentially
    # predicting off environmental/YT signals alone — worth flagging.
    zone_age_by_id = {za["id"]: za for za in zone_ages}
    hollow_picks = []
    picks = (latest_snapshot.get("picks") or {})
    for label, key in [("tuna", "tuna"), ("striper", "striper"),
                        ("tuna_uncon", "tuna_unconstrained"),
                        ("striper_uncon", "striper_unconstrained")]:
        pk = picks.get(key) or {}
        zid = pk.get("zone_id")
        if not zid: continue
        za = zone_age_by_id.get(zid)
        if not za: continue
        age = za.get("days")
        if age is not None and age > 21:
            hollow_picks.append({
                "pick_role": label,
                "zone_id": zid,
                "zone_name": pk.get("zone_name") or za.get("name"),
                "effective_heat": pk.get("effective_heat"),
                "base_heat": za.get("heat"),
                "days_since_captain_update": age,
                "severity": "dead" if age > 30 else "stale",
            })

    audit["signals"]["live_heat"] = {
        "auto_fetch": False,
        "source": "MANUAL — Captain updates zone.heat from captain reports",
        # Primary status (v24.49) — top-5 focus, what actually drives picks
        "status": heat_status,
        "top_picks_status": top_status,
        "top_picks_max_age_days": max(top_ages) if top_ages else None,
        "top_picks": [
            {"name": za["name"], "heat": za["heat"], "days_since_update": za["days"]}
            for za in top_n
        ],
        # Fleet-wide hygiene info (kept for data-quality tracking, no longer
        # drives the overall status).
        "zones_total": len(zones),
        "zones_with_updated_timestamp": zones_with_heat_updated,
        "zones_untimestamped": n_untimestamped,
        "zones_stale_10_21d": n_stale,
        "zones_dead_over_21d": n_dead,
        "oldest_zone_age_days": max_age,
        "action": ("hunt fresh captain reports for stalest TOP zones" if heat_status in ("stale", "dead")
                   else "add heat_updated to every zone" if heat_status == "unknown"
                   else "OK — top picks fresh (obscure zones may still be stale, that's expected)"),
        "stalest_zones": [{"name": za["name"], "days_stale": za["days"], "heat": za["heat"]} for za in stalest_zones],
        # v24.113 — hollow-pick surface. Non-empty means today's top pick(s)
        # rest on a base_heat that hasn't been Captain-refreshed. Signal to hunt.
        "hollow_current_picks": hollow_picks,
    }

    # ---- ENVIRONMENTAL LAYERS (from latest snapshot) ----
    snap_date = latest_snapshot.get("date")
    snap_days = _days_since(snap_date, today) if snap_date else None
    env_layers = {
        "sst_contours":                bool(latest_snapshot.get("sst_contours")),
        "chla_contours":               bool(latest_snapshot.get("chla_contours")),
        "derived_signals":             bool(latest_snapshot.get("derived_signals_summary")),
        "weather":                     bool(latest_snapshot.get("weather")),
        "pressure":                    bool(latest_snapshot.get("pressure")),
        "look_ahead":                  bool(latest_snapshot.get("look_ahead")),
    }
    for name, present in env_layers.items():
        audit["signals"][f"env_{name}"] = {
            "auto_fetch": True,
            "source": "build-inlined.py nightly fetch",
            "status": "fresh" if (present and snap_days is not None and snap_days <= 1) else
                      ("stale" if present else "dead"),
            "snapshot_date": snap_date,
            "days_since_snapshot": snap_days,
        }

    # ---- YOUTUBE ----
    yt = latest_snapshot.get("youtube_intel", {})
    audit["signals"]["youtube_intel"] = {
        "auto_fetch": True,
        "source": "15 NE channels via RSS (youtube_harvest.py)",
        "channels_responding": yt.get("channels_ok", 0),
        "channels_total": yt.get("channel_count", 0),
        "videos_in_14d": yt.get("total_videos_in_window", 0),
        "status": "fresh" if yt.get("channels_ok", 0) > 0 else "dead",
    }

    # ---- SEASONAL / STATIC / DISTANCE ----
    audit["signals"]["seasonal_presence"] = {
        "auto_fetch": False,
        "source": "STATIC — species migration priors, updated annually",
        "status": "fresh",
        "note": "Time-invariant reference data (no staleness concept)",
    }

    # ---- v24.55: PER-REGION PIPELINE HEALTH ----
    # Randy 2026-08-31: "if we don't know what we can do to get all of these
    # places up and ready and running... where we at?" A region can ship all its
    # infrastructure (zones file, drawers, harvesters) and still be DEAD if no
    # captain intel is flowing into it — every zone stays at default heat 5.0
    # and the "picks" are pure seasonal/SST math with zero corroboration.
    # This block flags that class of failure per region so it can't hide.
    #
    # RULES (per region, applied to the region's zone file):
    #   pipeline_dead:  0 zones with heat >= 6.0
    #   pipeline_thin:  < 3 zones with heat >= 6.0
    #   pipeline_healthy: >= 3 zones with heat >= 6.0
    # ALSO flag pipeline_stale if all zones have heat_updated > 21 days old.
    REGION_FILES = {
        "northeast":      BASE / "data" / "zones.json",
        "mid_atlantic":   BASE / "data" / "zones_mid_atlantic.json",
        "gulf":           BASE / "data" / "zones_gulf.json",
        "south_florida":  BASE / "data" / "zones_south_florida.json",
        "south_atlantic": BASE / "data" / "zones_south_atlantic.json",
    }
    region_health = {}
    for rname, rpath in REGION_FILES.items():
        if not rpath.exists():
            region_health[rname] = {"status": "missing", "reason": f"{rpath.name} not found"}
            continue
        try:
            rdata = json.loads(rpath.read_text())
        except Exception as e:
            region_health[rname] = {"status": "error", "reason": str(e)}
            continue
        rzones = rdata.get("zones", [])
        n_total = len(rzones)
        heats = [z.get("heat", 0) or 0 for z in rzones]
        n_hot = sum(1 for h in heats if h >= 7)
        n_warm = sum(1 for h in heats if 6 <= h < 7)
        n_above_baseline = sum(1 for h in heats if h > 5.0)
        max_heat = max(heats) if heats else 0
        avg_heat = sum(heats) / len(heats) if heats else 0
        # Age of most recent heat_updated in the whole region
        ages = []
        for z in rzones:
            hu = z.get("heat_updated")
            days = _days_since(hu, today) if hu else None
            if days is not None:
                ages.append(days)
        freshest = min(ages) if ages else None

        # Pipeline status
        if (n_hot + n_warm) >= 3:
            pstatus = "healthy"
        elif (n_hot + n_warm) >= 1:
            pstatus = "thin"
        else:
            pstatus = "dead"
        # Overlay staleness — if pipeline looks healthy but freshest chatter is
        # >21d old, that's still a dead pipeline (heat scores are frozen relics).
        if freshest is not None and freshest > 21 and pstatus != "dead":
            pstatus = "stale"

        region_health[rname] = {
            "status": pstatus,
            "zone_count": n_total,
            "hot_count_ge7": n_hot,
            "warm_count_6to7": n_warm,
            "above_baseline_count": n_above_baseline,
            "max_heat": max_heat,
            "avg_heat": round(avg_heat, 2),
            "freshest_update_days": freshest,
            "action": {
                "dead":    "no zones above baseline — captain-intel pipeline is not feeding this region. Wire a harvester or hunt YT chatter with hot-language titles.",
                "thin":    "some intel flowing but under 3 hot zones. Add a dedicated regional harvester or expand YT channel coverage.",
                "stale":   "all intel is > 21 days old. Refresh sources or the region's picks are frozen.",
                "healthy": "OK — 3+ hot zones, pipeline is feeding.",
            }[pstatus],
        }

    audit["region_health"] = region_health
    # Add a synthetic "per-region-pipeline" signal so it shows up in the fresh/
    # stale/dead rollup and can trigger the SHIP BLOCKER in site_health_audit
    # when a region goes dead.
    dead_regions = [r for r, h in region_health.items() if h.get("status") == "dead"]
    stale_regions = [r for r, h in region_health.items() if h.get("status") in ("stale", "thin")]
    if dead_regions:
        pstat = "dead"
    elif stale_regions:
        pstat = "stale"
    else:
        pstat = "fresh"
    audit["signals"]["region_pipelines"] = {
        "auto_fetch": True,
        "source": "per-region zone.json health rollup (v24.55)",
        "status": pstat,
        "dead_regions": dead_regions,
        "stale_regions": stale_regions,
        "healthy_regions": [r for r, h in region_health.items() if h.get("status") == "healthy"],
        "action": (
            f"regions with pipeline_dead: {', '.join(dead_regions)}" if dead_regions
            else f"regions with pipeline_thin/stale: {', '.join(stale_regions)}" if stale_regions
            else "all regions have at least 3 hot zones — pipeline feeding"
        ),
    }

    # ---- SIGNAL FIRING RATES (v24.120) ----
    # Bakes per-signal firing rate into every nightly. Randy 2026-07-25
    # "get sharper over time" — this is how we watch for signal drift. If
    # `goldenZoneBoost` fires 15/49 today and 0/49 tomorrow, that's a
    # satellite/feed break; if it fires 0/49 for 30 straight days, the
    # signal isn't earning its keep. Both are visible retroactively in the
    # archive after this ships.
    signal_firing = {}
    try:
        # zones_state is a dict {zone_id: {heat, whale_boost, sst_fit, …}} —
        # the archive's canonical per-zone signal breakdown. Falling back to
        # derived_signals.zones for older snapshots that used that layout.
        zone_entries = []
        if isinstance(latest_snapshot, dict):
            zs = latest_snapshot.get("zones_state")
            if isinstance(zs, dict) and zs:
                zone_entries = list(zs.values())
            else:
                ds = latest_snapshot.get("derived_signals", {})
                if isinstance(ds, dict):
                    zone_entries = ds.get("zones") or []
        if isinstance(zone_entries, list) and zone_entries:
            SIGNAL_KEYS = [
                "whale_boost", "season_boost", "sst_fit", "bait_boost",
                "bird_boost", "diversity_boost", "distance_penalty",
                "sst_gradient_boost", "chla_gradient_boost", "convergence_boost",
                "persistence_boost", "post_front_penalty",
                "youtube_corroboration_boost", "captain_dwell_boost",
                "trend_boost", "golden_zone_boost",
            ]
            n_zones = len(zone_entries)
            for k in SIGNAL_KEYS:
                vals = []
                for z in zone_entries:
                    if not isinstance(z, dict):
                        continue
                    v = z.get(k, 0)
                    if isinstance(v, (int, float)) and abs(v) > 0.005:
                        vals.append(v)
                fires = len(vals)
                mean_v = round(sum(vals) / len(vals), 3) if vals else 0.0
                max_v = round(max(vals, key=abs), 3) if vals else 0.0
                signal_firing[k] = {
                    "fires": fires,
                    "of_zones": n_zones,
                    "fire_rate": round(fires / n_zones, 3) if n_zones else 0,
                    "mean_when_active": mean_v,
                    "max": max_v,
                    "status": (
                        "dormant" if fires == 0
                        else "rare" if fires / n_zones < 0.10
                        else "active"
                    ),
                }
    except Exception as e:
        signal_firing = {"_error": str(e)}
    audit["signal_firing_rates"] = signal_firing
    audit["dormant_signals"] = sorted(
        k for k, v in signal_firing.items()
        if isinstance(v, dict) and v.get("status") == "dormant"
    )

    # ---- SUMMARY ----
    counts = {"fresh": 0, "stale": 0, "dead": 0, "unknown": 0}
    dead_signals = []
    stale_signals = []
    for name, sig in audit["signals"].items():
        s = sig.get("status", "unknown")
        counts[s] = counts.get(s, 0) + 1
        if s == "dead":
            dead_signals.append(name)
        elif s == "stale":
            stale_signals.append(name)
    audit["summary"] = counts
    audit["dead_signals"] = dead_signals
    audit["stale_signals"] = stale_signals
    audit["healthy"] = counts["dead"] == 0

    if verbose:
        print(f"  Signal freshness — {counts['fresh']} fresh · "
              f"{counts['stale']} stale · {counts['dead']} dead · "
              f"{counts['unknown']} unknown")
        if dead_signals:
            print(f"  🔴 DEAD signals: {', '.join(dead_signals)}")
        if stale_signals:
            print(f"  🟡 STALE signals: {', '.join(stale_signals)}")
        if audit.get("dormant_signals"):
            print(f"  💤 Dormant model signals (0 zones fired): "
                  f"{', '.join(audit['dormant_signals'])}")

    return audit


def print_full_report(audit=None):
    """Human-readable full report — used for manual audits."""
    if audit is None:
        audit = run_audit(verbose=False)
    print("=" * 68)
    print(f"FISH FINDER · DATA FRESHNESS AUDIT · {audit['date']}")
    print("=" * 68)
    for name, sig in audit["signals"].items():
        status = sig.get("status", "?")
        emoji = _status_emoji(status)
        print(f"\n{emoji}  {name}  [{status.upper()}]")
        for k, v in sig.items():
            if k != "status":
                print(f"     {k}: {v}")
    print("\n" + "=" * 68)
    counts = audit["summary"]
    print(f"SUMMARY: {counts['fresh']} fresh · {counts['stale']} stale · "
          f"{counts['dead']} dead · {counts['unknown']} unknown")
    if audit["dead_signals"]:
        print(f"🔴 DEAD SIGNALS (URGENT): {', '.join(audit['dead_signals'])}")
    if audit["stale_signals"]:
        print(f"🟡 STALE SIGNALS: {', '.join(audit['stale_signals'])}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "full":
        print_full_report()
    else:
        run_audit(verbose=True)
