#!/usr/bin/env python3
"""Build a fully self-contained fish-finder.html:
- Inlines Leaflet CSS and JS from vendor/
- Fetches a fresh NOAA forecast snapshot and embeds as fallback
- Injects the zones.json data
- Substitutes placeholders in the source template
"""
import json
import pathlib
import re
import sys
import time
import urllib.request
import datetime

BASE = pathlib.Path("/root/fish-finder")
sys.path.insert(0, str(BASE))
import archive as ff_archive  # noqa: E402
import model_signals as ff_signals  # noqa: E402
import youtube_harvest as ff_youtube  # noqa: E402
import whale_harvest as ff_whales  # noqa: E402
import multi_source_zone_refresh as ff_multi_refresh  # noqa: E402
import otw_zone_refresh as ff_otw  # noqa: E402  # kept for back-compat
import inaturalist_whale_harvest as ff_inat_whales  # noqa: E402
import inaturalist_bait_harvest as ff_inat_bait  # noqa: E402  # v24.95
import data_freshness_audit as ff_audit  # noqa: E402
import build_dashboard as ff_dashboard  # noqa: E402  # v24.91
import prediction_accuracy as ff_accuracy  # noqa: E402  # v24.91
import species_calendars as ff_calendars  # noqa: E402  # v24.94
import picks_audit as ff_picks_audit  # noqa: E402  # v24.96
import data_pruner as ff_pruner  # noqa: E402  # v24.99
import source_health as ff_health  # noqa: E402  # v24.100
import archive_integrity as ff_archint  # noqa: E402  # v24.102

# ============================================================================
# v24.58 (2026-08-31): YouTube bait+whale promotion — shared per-region helper.
# Randy: "when you scour those YouTube things you should be looking for bait
# information and whale sightings in the text." (2026-08-08). The extraction
# runs once for the whole video corpus; this helper promotes the extracted
# mentions into ONE region's zones_data (bait_intel + whale_sightings).
#
# Before v24.58 this was duplicated inline for NE + Mid-Atl only. Gulf/S.FL/SE
# got zero YT-derived whale sightings even when their region-tagged videos
# mentioned dolphins — so whaleBoost returned 0 for every non-NE-Mid-Atl zone.
# Consolidating into a single helper + calling it per region closes the gap.
# ============================================================================
_DOLPHIN_KEYS = {"bottlenose_dolphin", "common_dolphin", "risso_dolphin",
                 "dolphin_generic", "porpoise"}

def _promote_yt_intel_for_region(zones_data, yt_bait, yt_whales, zone_map, region_zone_ids, region_label):
    """Merge YouTube-extracted bait + whale mentions into one region's zones_data.
    Returns (n_bait, n_whales) counts of new entries.

    zone_map: {location_key: [zone_id, ...]} shared across all regions.
    region_zone_ids: set of zone_ids that belong to THIS region — filters out
        mentions that map only to other-region zones.
    """
    def _zids(locs):
        out = []
        for loc in (locs or []):
            for zid in zone_map.get(loc, []):
                if zid in region_zone_ids and zid not in out:
                    out.append(zid)
        return out

    n_bait = 0
    existing_bait = zones_data.setdefault("bait_intel", [])
    existing_bait_keys = {
        (e.get("date"), e.get("source"), tuple(sorted(e.get("zones") or [])))
        for e in existing_bait
    }
    for bait_key, entries in (yt_bait or {}).items():
        for e in entries:
            zids = _zids(e.get("locations"))
            if not zids or not e.get("date"):
                continue
            src = f"YouTube: {e.get('channel','?')}"
            key = (e["date"], src, tuple(sorted(zids)))
            if key in existing_bait_keys:
                continue
            existing_bait.append({
                "date": e["date"],
                "bait": bait_key,
                "location": e.get("channel", "YouTube"),
                "species_feeding": None,
                "source": src,
                "source_type": "youtube_auto",
                "quote": (e.get("title") or "")[:280],
                "link": e.get("link"),
                "zones": zids,
            })
            existing_bait_keys.add(key)
            n_bait += 1

    n_whales = 0
    existing_whales = zones_data.setdefault("whale_sightings", [])
    existing_whale_keys = {
        (e.get("date"), e.get("source"), tuple(sorted(e.get("zones") or [])))
        for e in existing_whales
    }
    for whale_key, entries in (yt_whales or {}).items():
        for e in entries:
            zids = _zids(e.get("locations"))
            if not zids or not e.get("date"):
                continue
            src = f"YouTube: {e.get('channel','?')}"
            key = (e["date"], src, tuple(sorted(zids)))
            if key in existing_whale_keys:
                continue
            coords = None
            for z in zones_data.get("zones", []):
                if z.get("id") == zids[0] and isinstance(z.get("center"), list):
                    coords = z["center"]
                    break
            kind = "dolphin" if whale_key in _DOLPHIN_KEYS else \
                   "whale" if whale_key != "whale_generic" else "mixed"
            existing_whales.append({
                "date": e["date"],
                "species": [whale_key],
                "kind": kind,
                "count": None,
                "location": e.get("channel", "YouTube"),
                "coords": coords,
                "source": src,
                "source_type": "youtube_auto",
                "quote": (e.get("title") or "")[:280],
                "link": e.get("link"),
                "zones": zids,
            })
            existing_whale_keys.add(key)
            n_whales += 1

    return n_bait, n_whales


# ============================================================================
# v24.55 (2026-08-31): YouTube auto-heat.
# Randy 2026-08-31: "if we don't know what we can do to get all of these places
# up and ready and running... where we at?" Answer: 4 of our 5 regions ship
# infrastructure-complete but their zone.heat is universally 5.0 because
# nobody has fed captain reports for those regions the way Randy feeds NE.
# The nightly already stamps intel_sources.youtube on every zone with recent
# chatter; this extension actually LIFTS heat when the chatter says "epic /
# loaded up / limited out". Turns YT from a corroboration signal into a
# primary-lift signal for regions without a Randy.
#
# GUARDRAILS:
#  - Cap YT-derived heat at 8.0. Reaching 9 or 10 always requires human-
#    verified captain intel (Captain rule).
#  - Only lift when the current heat is default (5.0) OR previously YT-lifted
#    (marked by intel_sources.youtube.heat_lift). NEVER overwrite Randy's
#    manual scores.
#  - Every build resets any prior YT-lifted heat back to baseline BEFORE
#    re-applying, so stale evidence causes natural decay to 5.0 within one
#    nightly cycle.
#  - Titles must be within 7 days for lift eligibility (fresher window than
#    the 14d corroboration window).
# ============================================================================

_YT_HOT_TIER1 = re.compile(
    r"\b("
    # Highest-signal phrases — actual catch language from FL/Gulf videos
    r"loaded up|loaded the boat|hammered|smashed|torched|torching|"
    r"wide open|full boat|absolute chaos|epic (?:day|bite|trip)|"
    r"monster (?:bite|day)|limits? out|limited out|nonstop|dialed in|"
    r"on fire|insane bite|red hot|banner day|lights out|"
    r"boxed (?:the|our) limit|slammed (?:the |them )?"
    r")\b",
    re.I,
)
_YT_HOT_TIER2 = re.compile(
    r"\b("
    # Descriptive positive-catch language — commoner in NE captain reports
    r"solid bite|good bite|steady bite|great day fishing|productive day|"
    r"nice class of|quality (?:fish|bite)|bent (?:rods|poles)|"
    r"screaming (?:reels|drags)|schoolies galore|"
    r"multiple hookups?|hookups all day|nice haul|"
    # v24.55.1: real-world NE title vocab from Aug 2026 sample
    r"settled in|big (?:bluefin|yellowfin|bigeye|striped bass|tuna)|"
    r"school (?:bluefin|bass|tuna)|"
    r"gaff shot|fish of the season|first (?:tuna|bluefin|yellowfin)|"
    r"good catch|nice catch|landed a"
    r")\b",
    re.I,
)
_YT_HEAT_CAP = 8.0    # never let YT alone push heat above 8
_YT_HEAT_WINDOW_DAYS = 7   # only titles this fresh count for lift

def _yt_heat_lift(titles, today_iso):
    """Given list of {title, published, ...} dicts, return (lift, tier_label).

    Lift is 0.0 (nothing hot) up to 2.5 (tier-1 phrase in a fresh title,
    plus corroboration). Tier label is 'epic', 'solid', or 'neutral' for
    logging.
    """
    if not titles:
        return 0.0, "no_titles"
    try:
        today = datetime.date.fromisoformat(today_iso[:10])
    except Exception:
        today = datetime.date.today()
    tier1_hits = 0
    tier2_hits = 0
    for t in titles:
        pub = (t.get("published") or "")[:10]
        try:
            d = datetime.date.fromisoformat(pub)
            if (today - d).days > _YT_HEAT_WINDOW_DAYS:
                continue
        except Exception:
            continue
        text = t.get("title") or ""
        if _YT_HOT_TIER1.search(text):
            tier1_hits += 1
        elif _YT_HOT_TIER2.search(text):
            tier2_hits += 1
    if tier1_hits >= 2:
        return 2.5, "epic_corroborated"
    if tier1_hits == 1:
        return 2.0, "epic_single"
    if tier2_hits >= 2:
        return 1.5, "solid_corroborated"
    if tier2_hits == 1:
        return 1.0, "solid_single"
    return 0.0, "neutral"

def _apply_youtube_intel_to_region(zones_path, zm, region_label, today_iso):
    """Apply YT enrichment to one region's zone file.

    Returns updated JSON string (or None if nothing to do). Writes updated
    file back to disk. Caller is responsible for template re-substitution.
    """
    if not zones_path.exists():
        return None, 0, 0, 0
    data = json.loads(zones_path.read_text())
    # Step 1: reset any previously YT-lifted heat back to baseline so stale
    # evidence decays out. Randy's manually-verified scores are safe because
    # they never carry a heat_lift marker.
    n_reset = 0
    for z in data.get("zones", []):
        src = ((z.get("intel_sources") or {}).get("youtube") or {})
        if src.get("heat_lift"):
            baseline = z.get("baseline_heat", 5.0)
            z["heat"] = baseline
            src.pop("heat_lift", None)
            src.pop("hot_tier", None)
            n_reset += 1
    # Step 2: fresh pass — stamp corroboration + apply lifts where evidence
    # supports it.
    n_stamped = 0
    n_bumped_date = 0
    n_lifted = 0
    for z in data.get("zones", []):
        zid = z.get("id")
        if zid not in zm:
            continue
        entry = zm[zid]
        titles = entry.get("top_titles", []) or []
        latest = max([(t.get("published","") or "")[:10] for t in titles] + [""]) if titles else ""
        current = z.get("heat_updated", "1970-01-01") or "1970-01-01"
        z.setdefault("intel_sources", {})
        z["intel_sources"]["youtube"] = {
            "last_video_date": latest,
            "video_count": entry.get("video_count", 0),
            "channel_count": entry.get("channel_count", 0),
        }
        n_stamped += 1
        if latest and latest > current:
            z["heat_updated"] = latest
            n_bumped_date += 1
        # Auto-heat lift — only when zone.heat is at default 5.0 (never
        # overwrite human-verified scores). The reset above means any prior
        # YT-lifted zone is back at 5.0 by now, so it's eligible.
        cur_heat = z.get("heat", 5.0)
        if abs(cur_heat - 5.0) < 0.01:
            lift, tier = _yt_heat_lift(titles, today_iso)
            if lift > 0:
                new_heat = min(_YT_HEAT_CAP, 5.0 + lift)
                z["heat"] = round(new_heat, 1)
                z["intel_sources"]["youtube"]["heat_lift"] = round(lift, 1)
                z["intel_sources"]["youtube"]["hot_tier"] = tier
                n_lifted += 1
    if n_stamped or n_bumped_date or n_lifted or n_reset:
        print(
            f"  {region_label}: stamped {n_stamped} · bumped_date {n_bumped_date} "
            f"· lifted {n_lifted} · reset {n_reset}"
        )
        zones_path.write_text(json.dumps(data, indent=2))
    return json.dumps(data, indent=2), n_stamped, n_bumped_date, n_lifted

def fetch_noaa_snapshot(gridpoint_path="OKX/85,77"):
    """Fetch current 7-day forecast. Default OKX/85,77 = Old Saybrook (Zone 1).
    Pass PHI/65,35 for Cape May (Zone 2)."""
    url = f"https://api.weather.gov/gridpoints/{gridpoint_path}/forecast"
    # v24.105 — 3-attempt retry [2s, 6s]. NWS API 500s and 429s regularly.
    data = None
    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "FishFinder-BuildScript (rspargo57@gmail.com)",
                "Accept": "application/geo+json"
            })
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.load(r)
            break
        except urllib.error.HTTPError as e:
            last_err = e
            # 404 = gridpoint rotation (like the Gulf fix earlier this session)
            if e.code == 404:
                break
            if attempt < 2:
                time.sleep([2, 6][attempt])
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep([2, 6][attempt])
    try:
        if data is None:
            raise last_err or Exception("NOAA snapshot unreachable")
        periods = data["properties"]["periods"]
        slim = [
            {
                "name": p["name"],
                "startTime": p["startTime"],
                "isDaytime": p["isDaytime"],
                "windSpeed": p.get("windSpeed", ""),
                "windDirection": p.get("windDirection", ""),
                "shortForecast": p.get("shortForecast", "")
            }
            for p in periods[:14]
        ]
        return {
            "fetched_at": datetime.datetime.utcnow().isoformat() + "Z",
            "periods": slim
        }
    except Exception as e:
        print(f"  WARN: NOAA snapshot fetch failed ({e}). Fallback will be empty.")
        return {"fetched_at": None, "periods": []}


def probe_hls_s30_latest_date():
    """Randy 2026-08-11 (v23.32): find the most recent date with a real
    HLS Sentinel-30m tile over Randy's fishing area. NASA GIBS serves HLS_S30
    at `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/
    HLS_S30_Nadir_BRDF_Adjusted_Reflectance/default/{DATE}/
    GoogleMapsCompatible_Level12/{z}/{y}/{x}.jpg` — no auth, but the S30 pass
    depends on satellite orbit + cloud cover, so we probe backward day-by-day
    until we find a tile with real bytes over Long Island offshore.

    Returns ISO date string (`YYYY-MM-DD`) or None if nothing found in 8 days.

    A "real" tile is >10KB — GIBS returns a ~200-byte "no data" JPG when the
    date/tile has nothing. We probe zoom-8 tile (76, 95) which sits over the
    Coxes-Ledge / south-of-Block-Island area — right where Randy's picks
    tend to cluster.
    """
    import urllib.request, urllib.error
    today = datetime.date.today()
    for days_ago in range(1, 9):  # 1..8 days ago (skip 0 — imagery not posted yet)
        d = (today - datetime.timedelta(days=days_ago)).isoformat()
        url = (f"https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/"
               f"HLS_S30_Nadir_BRDF_Adjusted_Reflectance/default/{d}/"
               f"GoogleMapsCompatible_Level12/8/95/76.jpg")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 FishFinderBot"})
            with urllib.request.urlopen(req, timeout=10) as r:
                if r.status == 200:
                    body = r.read()
                    if len(body) > 10_000:  # real tile
                        return d
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            continue
    return None


def fetch_sst_break_contours(
    bbox_lat=(34.5, 42.0),
    bbox_lon=(-76.5, -68.0),
    thresholds_f=(("f68", 68.0), ("f72", 72.0)),
    region_label="NE+MidAtl",
    stride=15,
    mode="fixed",
):
    """Fetch SST grid, compute contour lines at the given °F thresholds.

    Returns a dict with GeoJSON-ish line data for each threshold, suitable for
    embedding as Leaflet L.polyline arrays.

    v23.16 (Randy 2026-08-09) — retry-with-backoff on ERDDAP fetch + fallback
    to yesterday's contours if the fetch still fails.

    v24.56 (Randy 2026-08-31) — parameterized bbox + thresholds so we can
    fetch region-appropriate breaks. NE keeps 68/72°F. Gulf uses 74/79°F
    (grouper/red snapper thermocline). S. FL uses 76/81°F (sailfish coast).
    SE uses 74/78°F (Gulf Stream edge from Hatteras south). Each region's
    contours reach the front-end via its own SST_CONTOURS_{REGION} injection.

    v24.57 (Randy 2026-08-31) — mode="adaptive" replaces the fixed thresholds
    with data-driven ones: after fetching the grid, compute the median SST and
    set thresholds to (median-2°F, median+2°F). Guarantees non-zero contours
    year-round for warm-water regions where the fixed 74/79°F thresholds are
    always under water temp in summer. Key names become dynamic like "f83" /
    "f87" — the JS pipeline uses SST_BREAK_KEYS.sort() so it works regardless.
    """
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # ERDDAP griddap CSV — stride gives a balance of detail and speed.
    url = (
        "https://coastwatch.pfeg.noaa.gov/erddap/griddap/jplMURSST41.csv?"
        f"analysed_sst%5B(last)%5D%5B({bbox_lat[0]}):{stride}:({bbox_lat[1]})%5D"
        f"%5B({bbox_lon[0]}):{stride}:({bbox_lon[1]})%5D"
    )
    # per-region cache key: what a fallback snapshot's `sst_contours` block
    # needs to carry to be re-usable here. NE regions use the top-level
    # sst_contours key; other regions have their own sst_contours_{region_key}.
    snapshot_key = "sst_contours" if region_label.startswith("NE") else f"sst_contours_{region_label.lower().replace('+', '_').replace(' ', '_')}"
    threshold_labels = {k: f"{v}°F" for k, v in thresholds_f}
    empty_contours = {k: [] for k, _ in thresholds_f}

    # v24.105 — beefy retry with real exponential backoff (was 3 tries / 3.6s
    # total, which any brief CoastWatch outage would blow through — same
    # cascade-of-4-region-failures pattern we saw in chla fetches).
    # New: 5 attempts / 2s / 5s / 15s / 30s / 60s (max ~112s per fetch).
    text = None
    fetch_err = None
    backoff_schedule = [2, 5, 15, 30, 60]
    for attempt, sleep_s in enumerate(backoff_schedule):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                text = r.read().decode("utf-8")
            fetch_err = None
            if attempt > 0:
                print(f"  [{region_label}] SST fetch OK after {attempt+1} attempts")
            break
        except Exception as e:
            fetch_err = e
            if attempt < len(backoff_schedule) - 1:
                print(f"  [{region_label}] SST attempt {attempt+1} failed ({e}); retrying in {sleep_s}s...")
                time.sleep(sleep_s)
    if text is None:
        # Fallback: load prior snapshot's contours for this region.
        try:
            arc = pathlib.Path("/root/fish-finder/archive")
            for p in reversed(sorted(arc.glob("2*.json"))):
                snap = json.loads(p.read_text())
                contours = snap.get(snapshot_key) or {}
                if any(contours.get(k) for k, _ in thresholds_f):
                    print(f"  WARN: [{region_label}] SST fetch failed ({fetch_err}); using prior contours from {p.stem}")
                    return {
                        "fetched_at": None,
                        "thresholds": threshold_labels,
                        "contours": {k: contours.get(k, []) for k, _ in thresholds_f},
                        "_rows": [],
                        "fallback_from_date": p.stem,
                        "region_label": region_label,
                    }
        except Exception:
            pass
        print(f"  WARN: [{region_label}] SST contour fetch failed ({fetch_err}) AND no prior snapshot to fall back to.")
        return {"fetched_at": None, "thresholds": {}, "contours": empty_contours, "_rows": [], "region_label": region_label}
    try:
        rows = []
        for line in text.strip().split("\n")[2:]:
            parts = line.split(",")
            if len(parts) >= 4:
                try:
                    lat = float(parts[1]); lon = float(parts[2])
                    sst = float(parts[3])
                    rows.append((lat, lon, sst))
                except ValueError:
                    continue
        if not rows:
            raise ValueError("No SST rows parsed")

        lats_sorted = sorted(set(r[0] for r in rows))
        lons_sorted = sorted(set(r[1] for r in rows))
        grid = np.full((len(lats_sorted), len(lons_sorted)), np.nan)
        lat_idx = {v: i for i, v in enumerate(lats_sorted)}
        lon_idx = {v: i for i, v in enumerate(lons_sorted)}
        for lat, lon, sst in rows:
            grid[lat_idx[lat], lon_idx[lon]] = sst

        grid_f = grid * 9 / 5 + 32
        LON, LAT = np.meshgrid(lons_sorted, lats_sorted)

        # v24.57: adaptive mode — pick thresholds around this region's actual
        # median SST so the contours always land near meaningful thermal breaks.
        # In August the fixed 74/79°F Gulf thresholds return zero contours
        # because water is 85-88°F everywhere; adaptive picks 84/88 and finds
        # real breaks. In February the same code picks 68/72 and finds
        # cold-front edges. Either way the goldenZoneBoost signal has data
        # to work with year-round.
        active_thresholds = list(thresholds_f)
        if mode == "adaptive":
            valid_f = grid_f[~np.isnan(grid_f)]
            if len(valid_f) >= 10:
                med_f = float(np.median(valid_f))
                # round to integer °F for readability + clean f-key
                cool_f = int(round(med_f - 2))
                warm_f = int(round(med_f + 2))
                if warm_f <= cool_f:
                    warm_f = cool_f + 1  # never let them collide
                active_thresholds = [(f"f{cool_f}", float(cool_f)), (f"f{warm_f}", float(warm_f))]
                threshold_labels = {k: f"{v}°F (adaptive · median±2)" for k, v in active_thresholds}
                print(f"    [{region_label}] adaptive breaks: median={med_f:.1f}°F → f{cool_f}/f{warm_f}")

        result = {}
        for label, threshold_f in active_thresholds:
            fig, ax = plt.subplots()
            cs = ax.contour(LON, LAT, grid_f, levels=[threshold_f])
            polylines = []
            if cs.allsegs and cs.allsegs[0]:
                for seg in cs.allsegs[0]:
                    if len(seg) < 2:
                        continue
                    coords = [[float(pt[1]), float(pt[0])] for pt in seg]
                    polylines.append(coords)
            plt.close(fig)
            result[label] = polylines

        return {
            "fetched_at": datetime.datetime.utcnow().isoformat() + "Z",
            "thresholds": threshold_labels,
            "contours": result,
            "_rows": rows,
            "region_label": region_label,
            "mode": mode,
        }
    except Exception as e:
        try:
            arc = pathlib.Path("/root/fish-finder/archive")
            for p in reversed(sorted(arc.glob("2*.json"))):
                snap = json.loads(p.read_text())
                contours = snap.get(snapshot_key) or {}
                if any(contours.get(k) for k, _ in thresholds_f):
                    print(f"  WARN: [{region_label}] SST parse failed ({e}); using prior contours from {p.stem}")
                    return {
                        "fetched_at": None,
                        "thresholds": threshold_labels,
                        "contours": {k: contours.get(k, []) for k, _ in thresholds_f},
                        "_rows": [],
                        "fallback_from_date": p.stem,
                        "region_label": region_label,
                    }
        except Exception:
            pass
        print(f"  WARN: [{region_label}] SST contour fetch failed ({e}). No break lines.")
        return {"fetched_at": None, "thresholds": {}, "contours": empty_contours, "_rows": [], "region_label": region_label}


def fetch_tide_snapshot(station_id="8461490", station_label="New London, CT (8461490)"):
    """Fetch tide predictions. Default station 8461490 = New London CT (Zone 1).
    Pass 8536110 for Cape May (Zone 2). Returns 4 days of hi/lo predictions."""
    import urllib.parse
    today = datetime.date.today()
    end = today + datetime.timedelta(days=3)
    params = {
        "station": station_id,
        "begin_date": today.strftime("%Y%m%d"),
        "end_date": end.strftime("%Y%m%d"),
        "product": "predictions",
        "datum": "MLLW",
        "interval": "hilo",
        "time_zone": "lst_ldt",
        "units": "english",
        "format": "json",
    }
    url = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?" + urllib.parse.urlencode(params)
    # v24.105 — 3-attempt retry [2s, 6s]. NOAA CO-OPS API occasionally 429s
    # during the harvester's 5-region sweep. Was single-shot.
    data = None
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                data = json.load(r)
            break
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep([2, 6][attempt])
    try:
        if data is None:
            raise last_err or Exception("tide snapshot unreachable")
        preds = data.get("predictions", [])
        events = [
            {"time": p["t"], "type": p["type"], "height_ft": float(p["v"])}
            for p in preds
        ]
        return {
            "fetched_at": datetime.datetime.utcnow().isoformat() + "Z",
            "station": station_label,
            "events": events
        }
    except Exception as e:
        print(f"  WARN: Tide snapshot fetch failed ({e}).")
        return {"fetched_at": None, "station": None, "events": []}


def fetch_marine_snapshot(lat=41.0, lon=-72.0):
    """Fetch current 7-day marine (wave) forecast. Default lat/lon = offshore Old Saybrook (Zone 1).
    Pass (38.9, -74.9) for offshore Cape May (Zone 2)."""
    # Sample just offshore
    url = ("https://marine-api.open-meteo.com/v1/marine"
           f"?latitude={lat}&longitude={lon}"
           "&daily=wave_height_max,wave_direction_dominant,wave_period_max"
           "&timezone=America%2FNew_York&length_unit=imperial&forecast_days=7")
    # v24.105 — 3-attempt retry [2s, 6s].
    data = None
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                data = json.load(r)
            break
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep([2, 6][attempt])
    try:
        if data is None:
            raise last_err or Exception("marine snapshot unreachable")
        daily = data.get("daily", {})
        return {
            "fetched_at": datetime.datetime.utcnow().isoformat() + "Z",
            "days": [
                {
                    "date": d,
                    "wave_ft": daily["wave_height_max"][i],
                    "wave_period_s": daily["wave_period_max"][i],
                    "wave_dir_deg": daily["wave_direction_dominant"][i]
                }
                for i, d in enumerate(daily.get("time", []))
            ]
        }
    except Exception as e:
        print(f"  WARN: Marine snapshot fetch failed ({e}). Fallback will be empty.")
        return {"fetched_at": None, "days": []}


def sample_sst_at_zones(zones_data):
    """For each zone center, fetch today's SST in °F from NOAA/JPL MUR SST.
    Returns dict of zone_id → sst_f. Uses one griddap request per zone.

    v24.105 — parallelized 8-way via ThreadPoolExecutor. Was sequential:
    ~1s per zone × 300+ zones across all 5 regions = 5+ min just for SST
    sampling, and the whole build stalled during it. Threading brings it
    to ~40-60s. Kept a per-zone try/except so a single failed zone doesn't
    kill the batch. 15s timeout per zone (was 10s — some MUR endpoints
    are slower than others).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    ds_url = "https://coastwatch.pfeg.noaa.gov/erddap/griddap/jplMURSST41.csv"
    def _one(z):
        try:
            lat, lon = z["center"][0], z["center"][1]
            url = f"{ds_url}?analysed_sst%5B(last)%5D%5B({lat})%5D%5B({lon})%5D"
            req = urllib.request.Request(url, headers={"User-Agent": "FishFinder"})
            with urllib.request.urlopen(req, timeout=15) as r:
                text = r.read().decode("utf-8")
            lines = text.strip().split("\n")
            if len(lines) < 3:
                return z["id"], None
            parts = lines[2].split(",")
            if len(parts) >= 4:
                sst_c = float(parts[3])
                sst_f = round(sst_c * 9/5 + 32, 1)
                return z["id"], sst_f
        except Exception:
            pass
        return z["id"], None

    out = {}
    zones = zones_data.get("zones", [])
    with ThreadPoolExecutor(max_workers=8) as ex:
        for zid, val in ex.map(_one, zones):
            if val is not None:
                out[zid] = val
    return out


def fetch_chla_edge_contour(
    bbox_lat=(34.5, 42.0),
    bbox_lon=(-76.5, -68.0),
    thresholds=(("blue_green", 0.15), ("green_inner", 0.30)),
    region_label="NE+MidAtl",
    stride=5,
):
    """Fetch chlorophyll grid, compute contour lines at the given mg/m³ thresholds
    — the 'blue-green edges' where predators hunt. Values below = clear blue
    water; values above = plankton-rich green water; the boundary concentrates
    bait.

    Uses NOAA CoastWatch gap-filled VIIRS SNPP+NOAA-20 NRT daily (~2 days
    latency).

    v24.57 (Randy 2026-08-31) — parameterized bbox + thresholds so each region
    gets its own chlorophyll edges tuned to its water chemistry:
      NE / Mid-Atl : 0.15 / 0.30 mg/m³ — open-shelf clear water
      Gulf         : 0.5  / 1.5  mg/m³ — Mississippi outflow keeps baseline high
      S. Florida   : 0.10 / 0.20 mg/m³ — Bahamas-class clarity, tighter thresholds
      SE           : 0.20 / 0.40 mg/m³ — Gulf Stream inner shelf, moderate
    """
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ds = "noaacwNPPN20VIIRSDINEOFDaily"
    url = (f"https://coastwatch.noaa.gov/erddap/griddap/{ds}.csv?"
           f"chlor_a%5B(last)%5D%5B(0.0)%5D%5B({bbox_lat[0]}):{stride}:({bbox_lat[1]})%5D"
           f"%5B({bbox_lon[0]}):{stride}:({bbox_lon[1]})%5D")
    # Per-region snapshot key for the prior-day fallback.
    snapshot_key = "chla_contours" if region_label.startswith("NE") else f"chla_contours_{region_label.lower().replace('+','_').replace(' ','_').replace('.','')}"
    threshold_labels = {k: f"{v} mg/m³" for k, v in thresholds}
    empty_contours = {k: [] for k, _ in thresholds}

    def _chla_fallback(reason):
        try:
            arc = pathlib.Path("/root/fish-finder/archive")
            for p in reversed(sorted(arc.glob("2*.json"))):
                snap = json.loads(p.read_text())
                contours = snap.get(snapshot_key) or {}
                if any(contours.get(k) for k, _ in thresholds):
                    print(f"  WARN: [{region_label}] chla fetch failed ({reason}); using prior contours from {p.stem}")
                    return {
                        "fetched_at": None,
                        "contours": {k: contours.get(k, []) for k, _ in thresholds},
                        "_rows": [],
                        "fallback_from_date": p.stem,
                        "region_label": region_label,
                    }
        except Exception:
            pass
        return None
    # v24.105 (Randy 2026-09-05) — beefy retry with real exponential backoff.
    # Original 3-attempt / 0.4-2s backoff (total ~3.6s) was killed by any brief
    # CoastWatch outage (we saw all 4 regions 404 in a single build window on
    # 2026-09-05 despite the same URLs succeeding seconds later from a probe).
    # New schedule: 5 attempts, ~2s / 5s / 15s / 30s / 60s (max ~112s per fetch).
    # A one-minute outage no longer causes a cascade across all 4 regions.
    text = None
    fetch_err = None
    backoff_schedule = [2, 5, 15, 30, 60]
    for attempt, sleep_s in enumerate(backoff_schedule):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; FishFinder/1.0)"
            })
            with urllib.request.urlopen(req, timeout=45) as r:
                text = r.read().decode("utf-8")
            fetch_err = None
            if attempt > 0:
                print(f"  [{region_label}] chla fetch OK after {attempt+1} attempts")
            break
        except Exception as e:
            fetch_err = e
            if attempt < len(backoff_schedule) - 1:
                print(f"  [{region_label}] chla attempt {attempt+1} failed ({e}); retrying in {sleep_s}s...")
                time.sleep(sleep_s)
    if text is None:
        fb = _chla_fallback(fetch_err)
        if fb:
            return fb
        print(f"  WARN: [{region_label}] Chlorophyll fetch failed ({fetch_err}). No chla edges.")
        return {"fetched_at": None, "thresholds": threshold_labels, "contours": empty_contours, "_rows": [], "region_label": region_label}
    try:
        rows = []
        # Header row + units row, then data rows. Data columns: time, altitude, lat, lon, chlor_a
        for line in text.strip().split("\n")[2:]:
            parts = line.split(",")
            if len(parts) >= 5:
                try:
                    lat = float(parts[2]); lon = float(parts[3])
                    v = float(parts[4])
                    rows.append((lat, lon, v))
                except ValueError:
                    continue
        if not rows:
            raise ValueError("No chla rows parsed")
        lats_sorted = sorted(set(r[0] for r in rows))
        lons_sorted = sorted(set(r[1] for r in rows))
        grid = np.full((len(lats_sorted), len(lons_sorted)), np.nan)
        lat_idx = {v: i for i, v in enumerate(lats_sorted)}
        lon_idx = {v: i for i, v in enumerate(lons_sorted)}
        for lat, lon, v in rows:
            grid[lat_idx[lat], lon_idx[lon]] = v
        LON, LAT = np.meshgrid(lons_sorted, lats_sorted)

        result = {}
        for label, threshold in thresholds:
            fig, ax = plt.subplots()
            cs = ax.contour(LON, LAT, grid, levels=[threshold])
            polylines = []
            if cs.allsegs and cs.allsegs[0]:
                for seg in cs.allsegs[0]:
                    if len(seg) < 2:
                        continue
                    coords = [[float(pt[1]), float(pt[0])] for pt in seg]
                    polylines.append(coords)
            plt.close(fig)
            result[label] = polylines

        return {
            "fetched_at": datetime.datetime.utcnow().isoformat() + "Z",
            "thresholds": threshold_labels,
            "wms_layer": ds + ":chlor_a",
            "wms_url": f"https://coastwatch.noaa.gov/erddap/wms/{ds}/request?",
            "contours": result,
            "_rows": rows,
            "region_label": region_label,
        }
    except Exception as e:
        fb = _chla_fallback(e)
        if fb:
            return fb
        print(f"  WARN: [{region_label}] Chlorophyll fetch failed ({e}). No chla edges.")
        return {
            "fetched_at": None,
            "thresholds": {},
            "wms_layer": "noaacwNPPN20VIIRSDINEOFDaily:chlor_a",
            "wms_url": "https://coastwatch.noaa.gov/erddap/wms/noaacwNPPN20VIIRSDINEOFDaily/request?",
            "contours": empty_contours,
            "_rows": [],
            "region_label": region_label,
        }


def fetch_current_arrows():
    """Fetch surface current direction + speed at a grid of points across the
    tuna-relevant bbox. Open-Meteo Marine returns ocean_current_velocity (km/h)
    and ocean_current_direction (degrees, direction current is flowing TOWARD).

    Grid: 6x5 lat/lon (30 points). Sampled at forecast time = tomorrow 6am ET.
    """
    import urllib.parse
    # Points across tuna zones (excludes Long Island Sound where currents are
    # tidal-dominated, better shown on tide widget).
    # v24.3 (2026-08-23): grid extended south to cover Mid-Atl waters — Cape May
    # to Cape Hatteras. Widened from 4x6 NE-only grid (24 points) to 8x8 across
    # both regions (~64 points). Still parallel-fetched via concurrent.futures.
    lat_pts = [35.5, 36.5, 37.5, 38.5, 39.3, 40.0, 40.6, 41.2]
    lon_pts = [-75.5, -74.5, -73.5, -72.5, -71.5, -70.5, -69.5, -68.5]
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    # v24.3: parallelize the 64-point grid fetch (was serial). With 8s timeout
    # and 64 points, serial worst case was ~9 min; parallel with 12 workers
    # completes in ~5-15s even at Open-Meteo's rate limit.
    import concurrent.futures

    def _fetch_point(lat_lon):
        lat, lon = lat_lon
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "ocean_current_velocity,ocean_current_direction",
            "timezone": "America/New_York",
            "start_date": tomorrow,
            "end_date": tomorrow,
            "length_unit": "imperial"
        }
        url = "https://marine-api.open-meteo.com/v1/marine?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=8) as r:
                d = json.load(r)
            h = d.get("hourly", {})
            idx = 6
            vel_arr = h.get("ocean_current_velocity", [])
            dir_arr = h.get("ocean_current_direction", [])
            if idx < len(vel_arr) and vel_arr[idx] is not None and dir_arr[idx] is not None:
                return {
                    "lat": lat,
                    "lon": lon,
                    "vel_kt": round(vel_arr[idx] * 0.539957, 2),
                    "dir_deg": round(dir_arr[idx], 1)
                }
        except Exception:
            return None
        return None

    all_points = [(lat, lon) for lat in lat_pts for lon in lon_pts]
    arrows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        for result in pool.map(_fetch_point, all_points):
            if result:
                arrows.append(result)
    return {
        "fetched_at": datetime.datetime.utcnow().isoformat() + "Z",
        "forecast_for": tomorrow + "T06:00 ET",
        "arrows": arrows
    }


def fetch_zone_weather(zones_data):
    """For each zone, fetch a 7-day wind + wave forecast (today + 6 days
    ahead) at ~6am (morning trip) and ~2pm (afternoon return) ET. Uses
    Open-Meteo's regular weather + marine endpoints. Parallelized so 27 zones
    complete in ~10s.

    Returns:
      {
        fetched_at: ISO,
        today:     "YYYY-MM-DD",
        tomorrow:  "YYYY-MM-DD",
        dates:     [7 date strings, today..today+6],
        zones: {
          zone_id: {
            "YYYY-MM-DD": {
              morning:   { wind_mph, wind_dir_deg, wind_gusts_mph, wave_ft, wave_period_s, wave_dir_deg },
              afternoon: { wind_mph, wind_dir_deg, wind_gusts_mph, wave_ft, wave_period_s, wave_dir_deg }
            },
            ...
          }
        }
      }

    Enables per-zone weather in popups (tomorrow) AND the Trip Planner
    feature — the user picks a target zone + target day, and every nightly
    build refreshes the forecast for THAT zone on THAT day so they can watch
    the plan evolve as the trip approaches.
    """
    import urllib.parse
    import concurrent.futures
    today = datetime.date.today()
    dates = [(today + datetime.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    start_date = dates[0]
    end_date   = dates[-1]

    # v23.10 (Randy 2026-08-08): retry-with-backoff for the two Open-Meteo
    # fetches per zone. Before this, a single timeout silently dropped wind
    # or wave data for 4-8 zones per build (any zones showing "—" in the
    # day-chip weather rows on the landing were victims of a swallowed
    # exception). Now we retry twice with 400ms + 900ms backoffs before
    # giving up — cheap coverage boost, adds ~1s worst-case per build.
    def _fetch_json_retry(url, tries=3, base_timeout=15):
        last_err = None
        for attempt in range(tries):
            try:
                with urllib.request.urlopen(url, timeout=base_timeout) as r:
                    return json.load(r)
            except Exception as e:
                last_err = e
                if attempt < tries - 1:
                    time.sleep(0.4 * (attempt + 1) ** 1.5)
        raise last_err

    def _fetch_one(zone):
        zid = zone["id"]
        lat, lon = zone["center"][0], zone["center"][1]
        # v24.6 (2026-08-23): nearshore-null fallback. Open-Meteo occasionally
        # returns null wind for coordinates right on the coastline (Cape May
        # Rips, Delaware Bay Mouth, some CT inshore zones). If the primary
        # fetch returns EMPTY, retry once with the sample point shifted 0.06°
        # (~4 nm) offshore (SE for East Coast waters). Turns the intermittent
        # "no forecast" into always-works while preserving the original
        # zone.center for map rendering + pick attribution.
        fallback_lat = lat - 0.06   # South
        fallback_lon = lon + 0.06   # East (further into the Atlantic)
        # Initialize per-date buckets
        by_date = {d: {"morning": {}, "afternoon": {}} for d in dates}
        # Wind + sky/precip/temp from Open-Meteo weather. We pull sky signals
        # (weather_code, precipitation_probability, cloud_cover, temp) in the
        # SAME call as wind — no extra network hit. Randy's 2026-07-29 rule:
        # Trip Planner should show sun/rain forecast, not just wind + waves.
        try:
            w_params = {
                "latitude": lat, "longitude": lon,
                "hourly": ("wind_speed_10m,wind_direction_10m,wind_gusts_10m,"
                           "weather_code,precipitation_probability,cloud_cover,temperature_2m"),
                "timezone": "America/New_York",
                "start_date": start_date, "end_date": end_date,
                "wind_speed_unit": "mph",
                "temperature_unit": "fahrenheit"
            }
            url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(w_params)
            d = _fetch_json_retry(url)
            h = d.get("hourly", {})
            times = h.get("time", [])
            ws = h.get("wind_speed_10m", [])
            wd = h.get("wind_direction_10m", [])
            wg = h.get("wind_gusts_10m", [])
            wc = h.get("weather_code", [])
            pp = h.get("precipitation_probability", [])
            cc = h.get("cloud_cover", [])
            tp = h.get("temperature_2m", [])
            for i, t in enumerate(times):
                if i >= len(ws) or ws[i] is None: continue
                # t like "2026-07-29T06:00" — parse date + hour
                if "T" not in t: continue
                date_str, hh_str = t.split("T")
                hh = int(hh_str.split(":")[0])
                slot = "morning" if hh == 6 else ("afternoon" if hh == 14 else None)
                if not slot or date_str not in by_date: continue
                by_date[date_str][slot]["wind_mph"] = round(ws[i], 1)
                by_date[date_str][slot]["wind_dir_deg"] = round(wd[i], 0) if i < len(wd) and wd[i] is not None else None
                by_date[date_str][slot]["wind_gusts_mph"] = round(wg[i], 1) if i < len(wg) and wg[i] is not None else None
                # Sky / precip / temp
                if i < len(wc) and wc[i] is not None:
                    by_date[date_str][slot]["weather_code"] = int(wc[i])
                if i < len(pp) and pp[i] is not None:
                    by_date[date_str][slot]["precip_pct"] = int(pp[i])
                if i < len(cc) and cc[i] is not None:
                    by_date[date_str][slot]["cloud_pct"] = int(cc[i])
                if i < len(tp) and tp[i] is not None:
                    by_date[date_str][slot]["temp_f"] = round(tp[i], 0)
        except Exception:
            pass
        # Waves from Open-Meteo marine
        try:
            m_params = {
                "latitude": lat, "longitude": lon,
                "hourly": "wave_height,wave_period,wave_direction",
                "timezone": "America/New_York",
                "start_date": start_date, "end_date": end_date,
                "length_unit": "imperial"
            }
            url = "https://marine-api.open-meteo.com/v1/marine?" + urllib.parse.urlencode(m_params)
            d = _fetch_json_retry(url)
            h = d.get("hourly", {})
            times = h.get("time", [])
            wh = h.get("wave_height", [])
            wp = h.get("wave_period", [])
            wdir = h.get("wave_direction", [])
            for i, t in enumerate(times):
                if i >= len(wh) or wh[i] is None: continue
                if "T" not in t: continue
                date_str, hh_str = t.split("T")
                hh = int(hh_str.split(":")[0])
                slot = "morning" if hh == 6 else ("afternoon" if hh == 14 else None)
                if not slot or date_str not in by_date: continue
                by_date[date_str][slot]["wave_ft"] = round(wh[i], 1)
                by_date[date_str][slot]["wave_period_s"] = round(wp[i], 1) if i < len(wp) and wp[i] is not None else None
                by_date[date_str][slot]["wave_dir_deg"] = round(wdir[i], 0) if i < len(wdir) and wdir[i] is not None else None
        except Exception:
            pass
        # v24.6 (2026-08-23): nearshore-null fallback — if we ended up with an
        # empty by_date (Open-Meteo returned null for a coastline coord), retry
        # both fetches with the sample point shifted ~4 nm offshore. Do this
        # ONCE only to keep build time bounded.
        got_anything = any(any(v["morning"]) or any(v["afternoon"]) for v in by_date.values())
        got_wind = any("wind_mph" in v["morning"] or "wind_mph" in v["afternoon"] for v in by_date.values())
        if not got_anything or not got_wind:
            fb_by_date = {d: {"morning": {}, "afternoon": {}} for d in dates}
            try:
                w_params = {
                    "latitude": fallback_lat, "longitude": fallback_lon,
                    "hourly": ("wind_speed_10m,wind_direction_10m,wind_gusts_10m,"
                               "weather_code,precipitation_probability,cloud_cover,temperature_2m"),
                    "timezone": "America/New_York",
                    "start_date": start_date, "end_date": end_date,
                    "wind_speed_unit": "mph", "temperature_unit": "fahrenheit"
                }
                url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(w_params)
                d = _fetch_json_retry(url)
                h = d.get("hourly", {})
                times = h.get("time", [])
                ws = h.get("wind_speed_10m", [])
                wd = h.get("wind_direction_10m", [])
                wg = h.get("wind_gusts_10m", [])
                wc = h.get("weather_code", [])
                pp = h.get("precipitation_probability", [])
                cc = h.get("cloud_cover", [])
                tp = h.get("temperature_2m", [])
                for i, t in enumerate(times):
                    if i >= len(ws) or ws[i] is None: continue
                    if "T" not in t: continue
                    date_str, hh_str = t.split("T")
                    hh = int(hh_str.split(":")[0])
                    slot = "morning" if hh == 6 else ("afternoon" if hh == 14 else None)
                    if not slot or date_str not in fb_by_date: continue
                    fb_by_date[date_str][slot]["wind_mph"] = round(ws[i], 1)
                    fb_by_date[date_str][slot]["wind_dir_deg"] = round(wd[i], 0) if i < len(wd) and wd[i] is not None else None
                    fb_by_date[date_str][slot]["wind_gusts_mph"] = round(wg[i], 1) if i < len(wg) and wg[i] is not None else None
                    if i < len(wc) and wc[i] is not None: fb_by_date[date_str][slot]["weather_code"] = int(wc[i])
                    if i < len(pp) and pp[i] is not None: fb_by_date[date_str][slot]["precip_pct"] = int(pp[i])
                    if i < len(cc) and cc[i] is not None: fb_by_date[date_str][slot]["cloud_pct"] = int(cc[i])
                    if i < len(tp) and tp[i] is not None: fb_by_date[date_str][slot]["temp_f"] = round(tp[i], 0)
            except Exception:
                pass
            try:
                m_params = {
                    "latitude": fallback_lat, "longitude": fallback_lon,
                    "hourly": "wave_height,wave_period,wave_direction",
                    "timezone": "America/New_York",
                    "start_date": start_date, "end_date": end_date,
                    "length_unit": "imperial"
                }
                url = "https://marine-api.open-meteo.com/v1/marine?" + urllib.parse.urlencode(m_params)
                d = _fetch_json_retry(url)
                h = d.get("hourly", {})
                times = h.get("time", [])
                wh = h.get("wave_height", [])
                wp = h.get("wave_period", [])
                wdir = h.get("wave_direction", [])
                for i, t in enumerate(times):
                    if i >= len(wh) or wh[i] is None: continue
                    if "T" not in t: continue
                    date_str, hh_str = t.split("T")
                    hh = int(hh_str.split(":")[0])
                    slot = "morning" if hh == 6 else ("afternoon" if hh == 14 else None)
                    if not slot or date_str not in fb_by_date: continue
                    fb_by_date[date_str][slot]["wave_ft"] = round(wh[i], 1)
                    fb_by_date[date_str][slot]["wave_period_s"] = round(wp[i], 1) if i < len(wp) and wp[i] is not None else None
                    fb_by_date[date_str][slot]["wave_dir_deg"] = round(wdir[i], 0) if i < len(wdir) and wdir[i] is not None else None
            except Exception:
                pass
            # Merge fallback into primary — primary values win where present
            for d, slots in fb_by_date.items():
                for slot in ("morning", "afternoon"):
                    for k, v in slots[slot].items():
                        by_date[d][slot].setdefault(k, v)
            by_date = {d: {**v, "_fallback_used": True} if any(v["morning"]) or any(v["afternoon"]) else v
                       for d, v in by_date.items()}

        # Drop empty per-date buckets (no data at all)
        by_date = {d: v for d, v in by_date.items()
                   if any(v.get("morning") or {}) or any(v.get("afternoon") or {})}
        return zid, by_date

    result = {}
    # v24.106 (First Mate 2026-09-06) — hard wall-clock budget so one hung
    # zone can't block the whole region. Each _fetch_one has 45s max per
    # zone (3 retries × 15s); with 8 workers concurrent max would be
    # ~45s × N/8. For 47 Mid-Atl zones, ~4-5 min if EVERY zone times out.
    # Cap the batch at 90s and drop stragglers — better a partial region
    # than a stuck build.
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(_fetch_one, z) for z in zones_data.get("zones", [])]
        try:
            for f in concurrent.futures.as_completed(futures, timeout=180):
                try:
                    zid, by_date = f.result(timeout=1)
                    if by_date:
                        result[zid] = by_date
                except Exception:
                    pass
        except concurrent.futures.TimeoutError:
            # Cancel any still-pending futures + log how many we dropped
            n_pending = sum(1 for f in futures if not f.done())
            print(f"  ⚠  zone_weather: {n_pending} zone fetches exceeded 180s wall-clock — dropped")
            for f in futures:
                f.cancel()

    return {
        "fetched_at": datetime.datetime.utcnow().isoformat() + "Z",
        "today":    dates[0],
        "tomorrow": dates[1],
        "dates":    dates,
        "zones":    result
    }


def fetch_pressure_snapshot():
    """Fetch 48h barometric pressure trend for Old Saybrook.
    Falling pressure = feeding activity; rising = post-front slowdown.
    Open-Meteo gives hourly surface_pressure in hPa.
    """
    url = ("https://api.open-meteo.com/v1/forecast"
           "?latitude=41.29&longitude=-72.37"
           "&hourly=surface_pressure"
           "&past_hours=24&forecast_hours=24"
           "&timezone=America%2FNew_York")
    # v24.105 — 3-attempt retry [2s, 6s] backoff. Pressure trend drives
    # the postFrontPenalty signal in the model, so silent failure = flat
    # 0 pressure signal for the day.
    data = None
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                data = json.load(r)
            break
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep([2, 6][attempt])
    if data is None:
        # v24.133 (2026-09-17) — was `raise last_err` here, which killed the
        # WHOLE nightly build when Open-Meteo rate-limited or SSL-timed-out.
        # Three nights in a row (Sept 15/16/17) failed silently in the trigger
        # because of this one hard-fail path. Same pattern as the wave-snapshot
        # fetcher which correctly warns+returns empty on failure. Match it.
        print(f"  WARN: Pressure snapshot fetch failed after 3 attempts ({last_err}). Fallback will be empty; postFrontPenalty signal will be flat today.")
        return {"fetched_at": None, "samples": []}
    try:
        h = data.get("hourly", {})
        times = h.get("time", [])
        press = h.get("surface_pressure", [])
        # Simplify: keep every 3 hours to keep JSON small
        samples = []
        for i, t in enumerate(times):
            if i % 3 == 0 and press[i] is not None:
                samples.append({"t": t, "hpa": round(press[i], 1)})
        return {
            "fetched_at": datetime.datetime.utcnow().isoformat() + "Z",
            "samples": samples
        }
    except Exception as e:
        print(f"  WARN: Pressure snapshot parse failed ({e}). Fallback will be empty.")
        return {"fetched_at": None, "samples": []}

# Whale/dolphin sighting harvest — Randy 2026-07-30: "the whales and dolphins
# sightings are not updating, we need that to update every day." Runs BEFORE
# zones.json is read so fresh sightings from CRESLI/Viking Fleet flow into
# the built HTML AND into the effective-heat model + archive. Never removes
# existing entries (Captain's data-quality: archive is append-only). Fails
# silently on network errors — the build continues with whatever's in file.
# We capture the full harvest result (raw posts + parsed sightings + fetch
# metadata) so the archive can persist it. Randy 2026-08-08 preservation
# directive: keep raw source data — future model iterations may find signal
# in cards we discarded today (e.g. bait-density mentions, weather notes).
try:
    whale_harvest_result = ff_whales.harvest_and_merge()
except Exception as e:
    print(f"  WARN: whale harvest failed ({e}) — using existing sightings only")
    whale_harvest_result = {"added": 0, "raw_posts": [], "parsed_sightings": [],
                            "html_bytes": 0, "fetch_ok": False, "error": str(e)}

# v24.4 (2026-08-23): Mid-Atlantic whale harvest — Jersey Shore Whale Watching
# Tour (Bill McKim, Belmar NJ) publishes near-daily reports via WordPress RSS.
# Analog of CRESLI/Viking Fleet for Zone 2. Sightings default to Sea Girt Reef
# / Manasquan Ridge / Barnegat Ridge — the zones nearest his 10-mi-offshore
# operating area.
try:
    midatl_whale_result = ff_whales.harvest_midatl_and_merge()
except Exception as e:
    print(f"  WARN: Mid-Atl whale harvest failed ({e})")
    midatl_whale_result = {"added": 0, "raw_posts": [], "parsed_sightings": [],
                           "html_bytes": 0, "fetch_ok": False, "error": str(e)}

template = (BASE / "map" / "fish-finder.src.html").read_text()
leaflet_css = (BASE / "vendor" / "leaflet.css").read_text()
leaflet_js = (BASE / "vendor" / "leaflet.js").read_text()
# v24.98 — also inline MarkerCluster (13,000+ state-reef pins need clustering
# or the map grinds). Loaded AFTER leaflet.js. Was previously only hot-patched
# into the built HTML; back-porting to build so full rebuilds don't lose it.
_mc_css_default = (BASE / "vendor" / "MarkerCluster.Default.css").read_text() if (BASE / "vendor" / "MarkerCluster.Default.css").exists() else ""
_mc_css_base    = (BASE / "vendor" / "MarkerCluster.css").read_text()          if (BASE / "vendor" / "MarkerCluster.css").exists()          else ""
_mc_js          = (BASE / "vendor" / "leaflet.markercluster-src.js").read_text() if (BASE / "vendor" / "leaflet.markercluster-src.js").exists() else ""
markercluster_css = _mc_css_base + "\n" + _mc_css_default
markercluster_js  = _mc_js
zones_json = (BASE / "data" / "zones.json").read_text()

print("Fetching NOAA wind snapshot (Northeast — OKX/85,77 Old Saybrook)...")
noaa_snapshot = fetch_noaa_snapshot("OKX/85,77")
noaa_json = json.dumps(noaa_snapshot)
if noaa_snapshot.get("periods"):
    print(f"  Got {len(noaa_snapshot['periods'])} wind periods")

print("Fetching NOAA wind snapshot (Mid-Atlantic — PHI/65,35 Cape May)...")
noaa_snapshot_midatl = fetch_noaa_snapshot("PHI/65,35")
noaa_midatl_json = json.dumps(noaa_snapshot_midatl)
if noaa_snapshot_midatl.get("periods"):
    print(f"  Got {len(noaa_snapshot_midatl['periods'])} wind periods")

print("Fetching NOAA wind snapshot (Gulf — MOB office Perdido Key)...")
# v24.105 — Perdido Key 30.29,-87.55 → MOB/72,51 (was MOB/61,51 which
# started 404-ing sometime before 2026-09-05; verified via
# https://api.weather.gov/points/30.29,-87.55).
noaa_snapshot_gulf = fetch_noaa_snapshot("MOB/72,51")
noaa_gulf_json = json.dumps(noaa_snapshot_gulf)
if noaa_snapshot_gulf.get("periods"):
    print(f"  Got {len(noaa_snapshot_gulf['periods'])} wind periods")

# Zone 5 (South Florida) — v24.42, Randy 2026-08-30
print("Fetching NOAA wind snapshot (S. Florida — MFL office Miami)...")
noaa_snapshot_sofl = fetch_noaa_snapshot("MFL/110,50")
noaa_sofl_json = json.dumps(noaa_snapshot_sofl)
if noaa_snapshot_sofl.get("periods"):
    print(f"  Got {len(noaa_snapshot_sofl['periods'])} wind periods")

# Zone 3 (South Atlantic / Southeast) — v24.45, Randy 2026-08-31
print("Fetching NOAA wind snapshot (SE — CHS office Charleston)...")
noaa_snapshot_seatl = fetch_noaa_snapshot("CHS/62,60")
noaa_seatl_json = json.dumps(noaa_snapshot_seatl)
if noaa_snapshot_seatl.get("periods"):
    print(f"  Got {len(noaa_snapshot_seatl['periods'])} wind periods")

print("Fetching Open-Meteo wave snapshot (Northeast — offshore Old Saybrook)...")
marine_snapshot = fetch_marine_snapshot()
marine_json = json.dumps(marine_snapshot)
if marine_snapshot.get("days"):
    print(f"  Got {len(marine_snapshot['days'])} wave days")

print("Fetching Open-Meteo wave snapshot (Mid-Atlantic — offshore Cape May)...")
marine_snapshot_midatl = fetch_marine_snapshot(lat=38.9, lon=-74.9)
marine_midatl_json = json.dumps(marine_snapshot_midatl)
if marine_snapshot_midatl.get("days"):
    print(f"  Got {len(marine_snapshot_midatl['days'])} wave days")

print("Fetching Open-Meteo wave snapshot (Gulf — offshore Perdido Key)...")
marine_snapshot_gulf = fetch_marine_snapshot(lat=30.15, lon=-87.55)
marine_gulf_json = json.dumps(marine_snapshot_gulf)
if marine_snapshot_gulf.get("days"):
    print(f"  Got {len(marine_snapshot_gulf['days'])} wave days")

print("Fetching Open-Meteo wave snapshot (S. Florida — offshore Islamorada)...")
marine_snapshot_sofl = fetch_marine_snapshot(lat=24.7, lon=-80.4)
marine_sofl_json = json.dumps(marine_snapshot_sofl)
if marine_snapshot_sofl.get("days"):
    print(f"  Got {len(marine_snapshot_sofl['days'])} wave days")

print("Fetching Open-Meteo wave snapshot (SE — offshore Charleston)...")
marine_snapshot_seatl = fetch_marine_snapshot(lat=32.2, lon=-79.2)
marine_seatl_json = json.dumps(marine_snapshot_seatl)
if marine_snapshot_seatl.get("days"):
    print(f"  Got {len(marine_snapshot_seatl['days'])} wave days")

print("Fetching NOAA tide predictions (Northeast — New London CT)...")
tide_snapshot = fetch_tide_snapshot()
tide_json = json.dumps(tide_snapshot)
if tide_snapshot.get("events"):
    print(f"  Got {len(tide_snapshot['events'])} tide events")

print("Fetching NOAA tide predictions (Mid-Atlantic — Cape May NJ)...")
tide_snapshot_midatl = fetch_tide_snapshot("8536110", "Cape May, NJ (8536110)")
tide_midatl_json = json.dumps(tide_snapshot_midatl)
if tide_snapshot_midatl.get("events"):
    print(f"  Got {len(tide_snapshot_midatl['events'])} tide events")

print("Fetching NOAA tide predictions (Gulf — Pensacola FL)...")
tide_snapshot_gulf = fetch_tide_snapshot("8729840", "Pensacola, FL (8729840)")
tide_gulf_json = json.dumps(tide_snapshot_gulf)
if tide_snapshot_gulf.get("events"):
    print(f"  Got {len(tide_snapshot_gulf['events'])} tide events")

print("Fetching NOAA tide predictions (S. Florida — Key West FL)...")
tide_snapshot_sofl = fetch_tide_snapshot("8724580", "Key West, FL (8724580)")
tide_sofl_json = json.dumps(tide_snapshot_sofl)
if tide_snapshot_sofl.get("events"):
    print(f"  Got {len(tide_snapshot_sofl['events'])} tide events")

print("Fetching NOAA tide predictions (SE — Charleston SC)...")
tide_snapshot_seatl = fetch_tide_snapshot("8665530", "Charleston, SC (8665530)")
tide_seatl_json = json.dumps(tide_snapshot_seatl)
if tide_snapshot_seatl.get("events"):
    print(f"  Got {len(tide_snapshot_seatl['events'])} tide events")

print("Fetching 48h barometric pressure trend...")
pressure_snapshot = fetch_pressure_snapshot()
pressure_json = json.dumps(pressure_snapshot)
if pressure_snapshot.get("samples"):
    print(f"  Got {len(pressure_snapshot['samples'])} pressure samples")

print("Fetching chlorophyll edge contours (0.15 & 0.30 mg/m³) — NE + Mid-Atl bbox...")
chla_snapshot = fetch_chla_edge_contour()
chla_json = json.dumps(chla_snapshot)
if chla_snapshot.get("contours"):
    nb = len(chla_snapshot["contours"].get("blue_green", []))
    ng = len(chla_snapshot["contours"].get("green_inner", []))
    print(f"  Got {nb} contour segments at 0.15 mg/m³, {ng} at 0.30 mg/m³")
# v24.105 — record chla fetch health per-region so a single region going
# dark surfaces in source_health.json (and thus the dashboard).
try:
    _c = chla_snapshot.get("contours") or {}
    _rows_ne = sum(len(v) for v in _c.values())
    _ok_ne = _rows_ne > 0 and chla_snapshot.get("fetched_at") is not None
    ff_health.record("chla_contours_ne", rows=_rows_ne, ok=_ok_ne,
                             error=None if _ok_ne else "empty or fallback")
except Exception:
    pass

# v24.57 (Randy 2026-08-31): per-region chla edges. NE/Mid-Atl share the
# existing fetch. Gulf/S.FL/SE each need their own bbox + thresholds tuned
# to their water chemistry. Rationale:
#   Gulf (0.5/1.5) — Mississippi outflow pushes baseline high year-round;
#                    the NE thresholds are always crossed → meaningless.
#   S.FL (0.10/0.20) — Bahamas-clarity Gulf Stream water; tight thresholds
#                    catch the actual bait-holding edge.
#   SE   (0.20/0.40) — inner-shelf Gulf Stream, moderate baseline.
print("Fetching chlorophyll edge contours (0.5 & 1.5 mg/m³) — Gulf bbox...")
chla_snapshot_gulf = fetch_chla_edge_contour(
    bbox_lat=(28.0, 31.5),
    bbox_lon=(-95.0, -83.0),
    thresholds=(("blue_green", 0.5), ("green_inner", 1.5)),
    region_label="Gulf",
    stride=8,
)
chla_gulf_json = json.dumps(chla_snapshot_gulf)
if chla_snapshot_gulf.get("contours"):
    nb = len(chla_snapshot_gulf["contours"].get("blue_green", []))
    ng = len(chla_snapshot_gulf["contours"].get("green_inner", []))
    print(f"  Got {nb} contour segments at 0.5 mg/m³, {ng} at 1.5 mg/m³")
try:
    _c = chla_snapshot_gulf.get("contours") or {}
    _rows_g = sum(len(v) for v in _c.values())
    _ok_g = _rows_g > 0 and chla_snapshot_gulf.get("fetched_at") is not None
    ff_health.record("chla_contours_gulf", rows=_rows_g, ok=_ok_g,
                             error=None if _ok_g else "empty or fallback")
except Exception:
    pass

print("Fetching chlorophyll edge contours (0.10 & 0.20 mg/m³) — S. Florida bbox...")
chla_snapshot_sofl = fetch_chla_edge_contour(
    bbox_lat=(24.0, 27.5),
    bbox_lon=(-82.0, -79.0),
    thresholds=(("blue_green", 0.10), ("green_inner", 0.20)),
    region_label="S.Florida",
    stride=5,
)
chla_sofl_json = json.dumps(chla_snapshot_sofl)
if chla_snapshot_sofl.get("contours"):
    nb = len(chla_snapshot_sofl["contours"].get("blue_green", []))
    ng = len(chla_snapshot_sofl["contours"].get("green_inner", []))
    print(f"  Got {nb} contour segments at 0.10 mg/m³, {ng} at 0.20 mg/m³")
try:
    _c = chla_snapshot_sofl.get("contours") or {}
    _rows_s = sum(len(v) for v in _c.values())
    _ok_s = _rows_s > 0 and chla_snapshot_sofl.get("fetched_at") is not None
    ff_health.record("chla_contours_sofl", rows=_rows_s, ok=_ok_s,
                             error=None if _ok_s else "empty or fallback")
except Exception:
    pass

print("Fetching chlorophyll edge contours (0.20 & 0.40 mg/m³) — SE bbox...")
chla_snapshot_seatl = fetch_chla_edge_contour(
    bbox_lat=(30.0, 36.0),
    bbox_lon=(-82.0, -74.5),
    thresholds=(("blue_green", 0.20), ("green_inner", 0.40)),
    region_label="SE",
    stride=5,
)
chla_seatl_json = json.dumps(chla_snapshot_seatl)
if chla_snapshot_seatl.get("contours"):
    nb = len(chla_snapshot_seatl["contours"].get("blue_green", []))
    ng = len(chla_snapshot_seatl["contours"].get("green_inner", []))
    print(f"  Got {nb} contour segments at 0.20 mg/m³, {ng} at 0.40 mg/m³")
try:
    _c = chla_snapshot_seatl.get("contours") or {}
    _rows_se = sum(len(v) for v in _c.values())
    _ok_se = _rows_se > 0 and chla_snapshot_seatl.get("fetched_at") is not None
    ff_health.record("chla_contours_seatl", rows=_rows_se, ok=_ok_se,
                             error=None if _ok_se else "empty or fallback")
except Exception:
    pass

print("Fetching surface current arrows (tomorrow 6am ET)...")
currents_snapshot = fetch_current_arrows()
currents_json = json.dumps(currents_snapshot)
if currents_snapshot.get("arrows"):
    print(f"  Got {len(currents_snapshot['arrows'])} current-arrow points")

print("Computing SST break-line contours (68°F & 72°F) — NE + Mid-Atl bbox...")
sst_contours = fetch_sst_break_contours()
sst_contours_json = json.dumps(sst_contours)
if sst_contours.get("contours"):
    n68 = len(sst_contours["contours"].get("f68", []))
    n72 = len(sst_contours["contours"].get("f72", []))
    print(f"  Got {n68} contour segments at 68°F, {n72} at 72°F")
try:
    _c = sst_contours.get("contours") or {}
    _rows = sum(len(v) for v in _c.values())
    _ok = _rows > 0 and sst_contours.get("fetched_at") is not None
    ff_health.record("sst_contours_ne", rows=_rows, ok=_ok,
                     error=None if _ok else "empty or fallback")
except Exception:
    pass

# v24.56 (Randy 2026-08-31): per-region SST break lines. NE/MidAtl share a
# single fetch (their bboxes overlap). Gulf/S.FL/SE each need their own fetch
# with region-appropriate break thresholds since the tuna-sweet-spot 68/72°F
# doesn't apply below Cape Hatteras.
#
# Break rationale per First Mate:
#   Gulf     — 74/79°F : DeSoto Canyon thermocline, grouper + red snapper edge
#   S.FL     — 76/81°F : Sailfish Coast — sailfish/mahi/tuna trolling zone
#   SE       — 74/78°F : Gulf Stream inner/outer wall from Hatteras south
print("Computing SST break-line contours (adaptive, ~median ±2°F) — Gulf bbox...")
sst_contours_gulf = fetch_sst_break_contours(
    bbox_lat=(28.0, 31.5),
    bbox_lon=(-95.0, -83.0),
    thresholds_f=(("f74", 74.0), ("f79", 79.0)),   # ignored under mode=adaptive; used as fallback if median fails
    region_label="Gulf",
    stride=20,
    mode="adaptive",   # v24.57 — median-based so summer + winter both find breaks
)
sst_contours_gulf_json = json.dumps(sst_contours_gulf)
if sst_contours_gulf.get("contours"):
    _gc = sst_contours_gulf["contours"]
    print(f"  Got contours per key: " + ", ".join(f"{k}={len(v)}" for k, v in _gc.items()))
try:
    _c = sst_contours_gulf.get("contours") or {}
    _rows = sum(len(v) for v in _c.values())
    _ok = _rows > 0 and sst_contours_gulf.get("fetched_at") is not None
    ff_health.record("sst_contours_gulf", rows=_rows, ok=_ok,
                     error=None if _ok else "empty or fallback")
except Exception:
    pass

print("Computing SST break-line contours (adaptive, ~median ±2°F) — S. Florida bbox...")
sst_contours_sofl = fetch_sst_break_contours(
    bbox_lat=(24.0, 27.5),
    bbox_lon=(-82.0, -79.0),
    thresholds_f=(("f76", 76.0), ("f81", 81.0)),
    region_label="S.Florida",
    stride=15,
    mode="adaptive",   # v24.57
)
sst_contours_sofl_json = json.dumps(sst_contours_sofl)
if sst_contours_sofl.get("contours"):
    _sc = sst_contours_sofl["contours"]
    print(f"  Got contours per key: " + ", ".join(f"{k}={len(v)}" for k, v in _sc.items()))
try:
    _c = sst_contours_sofl.get("contours") or {}
    _rows = sum(len(v) for v in _c.values())
    _ok = _rows > 0 and sst_contours_sofl.get("fetched_at") is not None
    ff_health.record("sst_contours_sofl", rows=_rows, ok=_ok,
                     error=None if _ok else "empty or fallback")
except Exception:
    pass

print("Computing SST break-line contours (adaptive, ~median ±2°F) — SE bbox...")
sst_contours_seatl = fetch_sst_break_contours(
    bbox_lat=(30.0, 36.0),
    bbox_lon=(-82.0, -74.5),
    thresholds_f=(("f74", 74.0), ("f78", 78.0)),
    region_label="SE",
    stride=15,
    mode="adaptive",   # v24.57
)
sst_contours_seatl_json = json.dumps(sst_contours_seatl)
if sst_contours_seatl.get("contours"):
    _ec = sst_contours_seatl["contours"]
    print(f"  Got contours per key: " + ", ".join(f"{k}={len(v)}" for k, v in _ec.items()))
try:
    _c = sst_contours_seatl.get("contours") or {}
    _rows = sum(len(v) for v in _c.values())
    _ok = _rows > 0 and sst_contours_seatl.get("fetched_at") is not None
    ff_health.record("sst_contours_seatl", rows=_rows, ok=_ok,
                     error=None if _ok else "empty or fallback")
except Exception:
    pass

# Simple placeholder substitutions (avoid regex to sidestep backslash escapes in JS)
# v24.98 — leaflet.css + MarkerCluster.css inlined together as one <style>;
# leaflet.js + leaflet.markercluster-src.js inlined together as one <script>.
# MarkerCluster is optional — falls back to plain layerGroup client-side when
# the library isn't present.
css_link = '<link rel="stylesheet" href="LEAFLET_CSS_PLACEHOLDER">'
_css_parts = ["/* --- Inlined Leaflet 1.9.4 CSS --- */", leaflet_css]
if markercluster_css:
    _css_parts += ["/* --- Leaflet.markercluster CSS (v1.5.3) --- */", markercluster_css]
_css_parts += ["/* --- End inlined stylesheets --- */"]
css_block = "<style>\n" + "\n".join(_css_parts) + "\n</style>"
assert css_link in template, "CSS placeholder not found"
template = template.replace(css_link, css_block)

js_script = '<script src="LEAFLET_JS_PLACEHOLDER"></script>'
_js_parts = ["/* --- Inlined Leaflet 1.9.4 JS --- */", leaflet_js]
if markercluster_js:
    _js_parts += ["/* --- Leaflet.markercluster JS (v1.5.3) --- */", markercluster_js]
_js_parts += ["/* --- End inlined scripts --- */"]
js_block = "<script>\n" + "\n".join(_js_parts) + "\n</script>"
assert js_script in template, "JS placeholder not found"
template = template.replace(js_script, js_block)

# Per-zone weather (wind + waves at each zone's coords, tomorrow morning + afternoon)
print("Fetching per-zone weather — Northeast (42 zones)...")
try:
    zones_dict_for_wx = json.loads(zones_json)
    zone_weather = fetch_zone_weather(zones_dict_for_wx)
    _nz_covered = len(zone_weather.get('zones', {}))
    _nz_total = len(zones_dict_for_wx['zones'])
    print(f"  Got weather for {_nz_covered}/{_nz_total} zones")
    # v24.105 — record zone_weather health at the NE-region call site (the
    # other regions are separate try blocks below; if a nightly build stops
    # at NE — likely if it stops at all — we'll see it in source_health).
    try:
        ff_health.record("zone_weather", rows=_nz_covered,
                          ok=_nz_covered >= _nz_total * 0.9)
    except Exception:
        pass
except Exception as e:
    print(f"  WARN: Per-zone weather fetch failed ({e})")
    zone_weather = {"fetched_at": None, "forecast_date": None, "zones": {}}
    try:
        ff_health.record("zone_weather", rows=0, ok=False, error=str(e))
    except Exception:
        pass
zone_weather_json = json.dumps(zone_weather)

# Per-zone weather — Mid-Atlantic (20 zones). fetch_zone_weather takes a zones_data
# dict with a "zones" key, so we adapt the MidAtl blob into that shape.
print("Fetching per-zone weather — Mid-Atlantic (20 zones)...")
zone_weather_midatl_json = "null"
try:
    _midatl_wx_path = pathlib.Path(__file__).parent / "data" / "zones_mid_atlantic.json"
    if _midatl_wx_path.exists():
        _midatl_wx_data = json.loads(_midatl_wx_path.read_text())
        zone_weather_midatl = fetch_zone_weather(_midatl_wx_data)
        print(f"  Got weather for {len(zone_weather_midatl.get('zones', {}))}/{len(_midatl_wx_data['zones'])} zones")
        zone_weather_midatl_json = json.dumps(zone_weather_midatl)
    else:
        print("  Skipped (zones_mid_atlantic.json missing)")
except Exception as e:
    print(f"  WARN: Per-zone weather fetch failed ({e})")

# Sample SST at each zone center and inject into the zone data
print("Sampling SST at each Northeast zone center...")
try:
    zones_dict_pre = json.loads(zones_json)
    sst_map = sample_sst_at_zones(zones_dict_pre)
    print(f"  Got SST for {len(sst_map)}/{len(zones_dict_pre['zones'])} zones")
    for z in zones_dict_pre["zones"]:
        if z["id"] in sst_map:
            z["current_sst_f"] = sst_map[z["id"]]
    zones_json_enriched = json.dumps(zones_dict_pre, indent=2)
    # v24.106 (First Mate 2026-09-05): persist SST back to zones.json so the
    # server-side archive can compute sstFit() from disk. All other regions
    # (Mid-Atl, Gulf, S.FL, SE) already write SST back to their zone files —
    # NE was the missing one, which is why archive.compute_zone_state()'s
    # newly-ported sstFit signal was returning 0 for every NE zone.
    _ne_zones_path = pathlib.Path(__file__).parent / "data" / "zones.json"
    try:
        _ne_zones_path.write_text(json.dumps(zones_dict_pre, indent=2))
    except Exception as _we:
        print(f"  WARN: could not persist NE SST samples to zones.json ({_we})")
except Exception as e:
    print(f"  WARN: SST sampling failed ({e}). Zones will lack current_sst_f.")
    zones_json_enriched = zones_json

# v24.3 (2026-08-23): also sample SST for Mid-Atl zones — otherwise sstFit()
# returns 0 for every Mid-Atl zone and the model loses a signal.
_midatl_sst_path = pathlib.Path(__file__).parent / "data" / "zones_mid_atlantic.json"
if _midatl_sst_path.exists():
    print("Sampling SST at each Mid-Atlantic zone center...")
    try:
        _midatl_data = json.loads(_midatl_sst_path.read_text())
        _midatl_sst_map = sample_sst_at_zones(_midatl_data)
        print(f"  Got SST for {len(_midatl_sst_map)}/{len(_midatl_data['zones'])} zones")
        for z in _midatl_data.get("zones", []):
            if z["id"] in _midatl_sst_map:
                z["current_sst_f"] = _midatl_sst_map[z["id"]]
        _midatl_sst_path.write_text(json.dumps(_midatl_data, indent=2))
    except Exception as e:
        print(f"  WARN: Mid-Atl SST sampling failed ({e}).")

# v24.29 (2026-08-29): also sample SST for Gulf zones.
_gulf_sst_path = pathlib.Path(__file__).parent / "data" / "zones_gulf.json"
if _gulf_sst_path.exists():
    print("Sampling SST at each Gulf zone center...")
    try:
        _gulf_data = json.loads(_gulf_sst_path.read_text())
        _gulf_sst_map = sample_sst_at_zones(_gulf_data)
        print(f"  Got SST for {len(_gulf_sst_map)}/{len(_gulf_data['zones'])} zones")
        for z in _gulf_data.get("zones", []):
            if z["id"] in _gulf_sst_map:
                z["current_sst_f"] = _gulf_sst_map[z["id"]]
        _gulf_sst_path.write_text(json.dumps(_gulf_data, indent=2))
    except Exception as e:
        print(f"  WARN: Gulf SST sampling failed ({e}).")

# v24.42 (2026-08-30): also sample SST for South Florida zones.
_sofl_sst_path = pathlib.Path(__file__).parent / "data" / "zones_south_florida.json"
if _sofl_sst_path.exists():
    print("Sampling SST at each S. Florida zone center...")
    try:
        _sofl_data = json.loads(_sofl_sst_path.read_text())
        _sofl_sst_map = sample_sst_at_zones(_sofl_data)
        print(f"  Got SST for {len(_sofl_sst_map)}/{len(_sofl_data['zones'])} zones")
        for z in _sofl_data.get("zones", []):
            if z["id"] in _sofl_sst_map:
                z["current_sst_f"] = _sofl_sst_map[z["id"]]
        _sofl_sst_path.write_text(json.dumps(_sofl_data, indent=2))
    except Exception as e:
        print(f"  WARN: S. Florida SST sampling failed ({e}).")

# v24.45 (2026-08-31): also sample SST for South Atlantic zones.
_seatl_sst_path = pathlib.Path(__file__).parent / "data" / "zones_south_atlantic.json"
if _seatl_sst_path.exists():
    print("Sampling SST at each SE zone center...")
    try:
        _seatl_data = json.loads(_seatl_sst_path.read_text())
        _seatl_sst_map = sample_sst_at_zones(_seatl_data)
        print(f"  Got SST for {len(_seatl_sst_map)}/{len(_seatl_data['zones'])} zones")
        for z in _seatl_data.get("zones", []):
            if z["id"] in _seatl_sst_map:
                z["current_sst_f"] = _seatl_sst_map[z["id"]]
        _seatl_sst_path.write_text(json.dumps(_seatl_data, indent=2))
    except Exception as e:
        print(f"  WARN: SE SST sampling failed ({e}).")

# Per-zone weather — Gulf zones (fetched here after SST sampling above)
print("Fetching per-zone weather — Gulf (33 zones)...")
zone_weather_gulf_json = "null"
try:
    if _gulf_sst_path.exists():
        _gulf_wx_data = json.loads(_gulf_sst_path.read_text())
        zone_weather_gulf = fetch_zone_weather(_gulf_wx_data)
        print(f"  Got weather for {len(zone_weather_gulf.get('zones', {}))}/{len(_gulf_wx_data['zones'])} zones")
        zone_weather_gulf_json = json.dumps(zone_weather_gulf)
    else:
        print("  Skipped (zones_gulf.json missing)")
except Exception as e:
    print(f"  WARN: Gulf per-zone weather fetch failed ({e})")

# Per-zone weather — S. Florida zones (v24.42)
print("Fetching per-zone weather — S. Florida (28 zones)...")
zone_weather_sofl_json = "null"
try:
    if _sofl_sst_path.exists():
        _sofl_wx_data = json.loads(_sofl_sst_path.read_text())
        zone_weather_sofl = fetch_zone_weather(_sofl_wx_data)
        print(f"  Got weather for {len(zone_weather_sofl.get('zones', {}))}/{len(_sofl_wx_data['zones'])} zones")
        zone_weather_sofl_json = json.dumps(zone_weather_sofl)
    else:
        print("  Skipped (zones_south_florida.json missing)")
except Exception as e:
    print(f"  WARN: S. Florida per-zone weather fetch failed ({e})")

# Per-zone weather — SE zones (v24.45)
print("Fetching per-zone weather — SE (20 zones)...")
zone_weather_seatl_json = "null"
try:
    if _seatl_sst_path.exists():
        _seatl_wx_data = json.loads(_seatl_sst_path.read_text())
        zone_weather_seatl = fetch_zone_weather(_seatl_wx_data)
        print(f"  Got weather for {len(zone_weather_seatl.get('zones', {}))}/{len(_seatl_wx_data['zones'])} zones")
        zone_weather_seatl_json = json.dumps(zone_weather_seatl)
    else:
        print("  Skipped (zones_south_atlantic.json missing)")
except Exception as e:
    print(f"  WARN: SE per-zone weather fetch failed ({e})")

# v24.99 — prune old bait_intel / whale_sightings entries from every region
# BEFORE they get embedded into the shipped HTML. Model only reads ≤10-day
# bait and ≤7-day whales; anything older is dead weight bloating the payload.
# Historical intel is preserved in archive snapshots' bait_intel_full /
# whale_sightings_full fields, so this is safe to run at build time.
try:
    print("Pruning old bait/whale intel from region files (keep 90 days)...")
    _pruned = ff_pruner.run(retention_days=90, verbose=False)
    _bp = sum(r.get("bait_pruned", 0) for r in _pruned)
    _wp = sum(r.get("whales_pruned", 0) for r in _pruned)
    print(f"  pruned {_bp} bait_intel + {_wp} whale_sightings entries older than 90d")
    # Re-read zones_json so the pruned data reaches the substitution below
    zones_json = pathlib.Path(__file__).parent / "data" / "zones.json"
    zones_json = zones_json.read_text()
    # zones_json_enriched carries downstream promotions; re-derive from disk
    # in case pruner touched it. Enrichment steps ABOVE this (bait/whale/
    # heat_updated bumps) already wrote to disk, so re-reading is safe.
    zones_json_enriched = zones_json
except Exception as e:
    # v24.105 — traceback logging so a data-pruner bug (or the re-read below
    # blowing up) doesn't get reduced to a one-line WARN.
    import traceback as _tb
    _tb.print_exc()
    print(f"  WARN: data pruner failed ({e})")

# Inject zones data. zones.json parses cleanly as JavaScript object literal
# because JSON is a strict subset of JS object syntax.
assert "ZONE_DATA_PLACEHOLDER" in template, "ZONE_DATA placeholder not found"
template = template.replace("ZONE_DATA_PLACEHOLDER", zones_json_enriched.rstrip().rstrip(";"))

# v24 (2026-08-22): also inject Mid-Atlantic Zone 2 data. If the file doesn't
# exist yet (older sandbox), inject an empty stub so the template still parses.
_midatl_path = pathlib.Path(__file__).parent / "data" / "zones_mid_atlantic.json"
if _midatl_path.exists():
    with open(_midatl_path) as _mf:
        _midatl_json = _mf.read()
else:
    _midatl_json = "null"
_midatl_injected_blob = _midatl_json.rstrip().rstrip(";")   # tracked for v24.55 YT re-sub
assert "ZONE_DATA_MIDATL_PLACEHOLDER" in template, "ZONE_DATA_MIDATL placeholder not found"
template = template.replace("ZONE_DATA_MIDATL_PLACEHOLDER", _midatl_injected_blob)

# v24.29 (2026-08-29): inject Gulf Zone 4 data.
_gulf_path = pathlib.Path(__file__).parent / "data" / "zones_gulf.json"
if _gulf_path.exists():
    with open(_gulf_path) as _gf:
        _gulf_json = _gf.read()
else:
    _gulf_json = "null"
_gulf_injected_blob = _gulf_json.rstrip().rstrip(";")   # tracked for v24.55 YT re-sub
assert "ZONE_DATA_GULF_PLACEHOLDER" in template, "ZONE_DATA_GULF placeholder not found"
template = template.replace("ZONE_DATA_GULF_PLACEHOLDER", _gulf_injected_blob)

# v24.42 (2026-08-30): inject S. Florida Zone 5 data.
_sofl_path = pathlib.Path(__file__).parent / "data" / "zones_south_florida.json"
if _sofl_path.exists():
    with open(_sofl_path) as _sf:
        _sofl_json = _sf.read()
else:
    _sofl_json = "null"
_sofl_injected_blob = _sofl_json.rstrip().rstrip(";")   # tracked for v24.55 YT re-sub
assert "ZONE_DATA_SOUTH_FLORIDA_PLACEHOLDER" in template, "ZONE_DATA_SOUTH_FLORIDA placeholder not found"
template = template.replace("ZONE_DATA_SOUTH_FLORIDA_PLACEHOLDER", _sofl_injected_blob)

# v24.45 (2026-08-31): inject S. Atlantic Zone 3 data.
_seatl_path = pathlib.Path(__file__).parent / "data" / "zones_south_atlantic.json"
if _seatl_path.exists():
    with open(_seatl_path) as _sef:
        _seatl_json = _sef.read()
else:
    _seatl_json = "null"
_seatl_injected_blob = _seatl_json.rstrip().rstrip(";")   # tracked for v24.55 YT re-sub
assert "ZONE_DATA_SOUTH_ATLANTIC_PLACEHOLDER" in template, "ZONE_DATA_SOUTH_ATLANTIC placeholder not found"
template = template.replace("ZONE_DATA_SOUTH_ATLANTIC_PLACEHOLDER", _seatl_injected_blob)

# Inject snapshots as fallback
assert "NOAA_SNAPSHOT_PLACEHOLDER" in template, "NOAA snapshot placeholder not found"
template = template.replace("NOAA_SNAPSHOT_PLACEHOLDER", noaa_json)
assert "NOAA_SNAPSHOT_MIDATL_PLACEHOLDER" in template, "NOAA midatl snapshot placeholder not found"
template = template.replace("NOAA_SNAPSHOT_MIDATL_PLACEHOLDER", noaa_midatl_json)
assert "NOAA_SNAPSHOT_GULF_PLACEHOLDER" in template, "NOAA gulf snapshot placeholder not found"
template = template.replace("NOAA_SNAPSHOT_GULF_PLACEHOLDER", noaa_gulf_json)
assert "NOAA_SNAPSHOT_SOUTH_FLORIDA_PLACEHOLDER" in template, "NOAA s.florida snapshot placeholder not found"
template = template.replace("NOAA_SNAPSHOT_SOUTH_FLORIDA_PLACEHOLDER", noaa_sofl_json)
assert "NOAA_SNAPSHOT_SOUTH_ATLANTIC_PLACEHOLDER" in template, "NOAA s.atlantic snapshot placeholder not found"
template = template.replace("NOAA_SNAPSHOT_SOUTH_ATLANTIC_PLACEHOLDER", noaa_seatl_json)
assert "MARINE_SNAPSHOT_PLACEHOLDER" in template, "Marine snapshot placeholder not found"
template = template.replace("MARINE_SNAPSHOT_PLACEHOLDER", marine_json)
assert "MARINE_SNAPSHOT_MIDATL_PLACEHOLDER" in template, "Marine midatl snapshot placeholder not found"
template = template.replace("MARINE_SNAPSHOT_MIDATL_PLACEHOLDER", marine_midatl_json)
assert "MARINE_SNAPSHOT_GULF_PLACEHOLDER" in template, "Marine gulf snapshot placeholder not found"
template = template.replace("MARINE_SNAPSHOT_GULF_PLACEHOLDER", marine_gulf_json)
assert "MARINE_SNAPSHOT_SOUTH_FLORIDA_PLACEHOLDER" in template, "Marine s.florida snapshot placeholder not found"
template = template.replace("MARINE_SNAPSHOT_SOUTH_FLORIDA_PLACEHOLDER", marine_sofl_json)
assert "MARINE_SNAPSHOT_SOUTH_ATLANTIC_PLACEHOLDER" in template, "Marine s.atlantic snapshot placeholder not found"
template = template.replace("MARINE_SNAPSHOT_SOUTH_ATLANTIC_PLACEHOLDER", marine_seatl_json)
assert "SST_CONTOURS_PLACEHOLDER" in template, "SST contours placeholder not found"
template = template.replace("SST_CONTOURS_PLACEHOLDER", sst_contours_json)

# v24.56: per-region SST contour injection. If a placeholder isn't in the
# template yet (older HTML), silently skip.
for ph, blob in [
    ("SST_CONTOURS_GULF_PLACEHOLDER", sst_contours_gulf_json),
    ("SST_CONTOURS_SOUTH_FLORIDA_PLACEHOLDER", sst_contours_sofl_json),
    ("SST_CONTOURS_SOUTH_ATLANTIC_PLACEHOLDER", sst_contours_seatl_json),
]:
    if ph in template:
        template = template.replace(ph, blob)
assert "TIDE_SNAPSHOT_PLACEHOLDER" in template, "Tide snapshot placeholder not found"
template = template.replace("TIDE_SNAPSHOT_PLACEHOLDER", tide_json)
assert "TIDE_SNAPSHOT_MIDATL_PLACEHOLDER" in template, "Tide midatl snapshot placeholder not found"
template = template.replace("TIDE_SNAPSHOT_MIDATL_PLACEHOLDER", tide_midatl_json)
assert "TIDE_SNAPSHOT_GULF_PLACEHOLDER" in template, "Tide gulf snapshot placeholder not found"
template = template.replace("TIDE_SNAPSHOT_GULF_PLACEHOLDER", tide_gulf_json)
assert "TIDE_SNAPSHOT_SOUTH_FLORIDA_PLACEHOLDER" in template, "Tide s.florida snapshot placeholder not found"
template = template.replace("TIDE_SNAPSHOT_SOUTH_FLORIDA_PLACEHOLDER", tide_sofl_json)
assert "TIDE_SNAPSHOT_SOUTH_ATLANTIC_PLACEHOLDER" in template, "Tide s.atlantic snapshot placeholder not found"
template = template.replace("TIDE_SNAPSHOT_SOUTH_ATLANTIC_PLACEHOLDER", tide_seatl_json)
assert "PRESSURE_SNAPSHOT_PLACEHOLDER" in template, "Pressure snapshot placeholder not found"
template = template.replace("PRESSURE_SNAPSHOT_PLACEHOLDER", pressure_json)

# v24.48 (Randy 2026-08-31, First Mate) — run the accuracy scorer against the
# archive and inline the report. Client-side About drawer surfaces it as the
# "Model Accuracy" section. Report is a snapshot of how well the model's past
# predictions matched what actually got picked, at 1/3/7 day horizons.
print("Running prediction accuracy report (30-day window)...")
_accuracy_json = "null"
try:
    import subprocess as _sp_acc
    _acc_run = _sp_acc.run(
        ["python3", str(BASE / "accuracy_report.py"), "--days", "30"],
        capture_output=True, text=True, timeout=30
    )
    if _acc_run.returncode == 0:
        _acc_path = BASE / "data" / "accuracy_report.json"
        if _acc_path.exists():
            _accuracy_json = _acc_path.read_text().strip()
            print(f"  Wrote accuracy_report.json ({len(_accuracy_json):,} bytes)")
    else:
        print(f"  WARN: accuracy_report.py exit {_acc_run.returncode} — {_acc_run.stderr[:200]}")
except Exception as _e:
    print(f"  WARN: accuracy report failed ({_e}) — inlining null")
assert "ACCURACY_REPORT_PLACEHOLDER" in template, "Accuracy report placeholder not found"
template = template.replace("ACCURACY_REPORT_PLACEHOLDER", _accuracy_json)
assert "CHLA_SNAPSHOT_PLACEHOLDER" in template, "Chlorophyll snapshot placeholder not found"
template = template.replace("CHLA_SNAPSHOT_PLACEHOLDER", chla_json)

# v24.57: per-region chla snapshot injection
for ph, blob in [
    ("CHLA_SNAPSHOT_GULF_PLACEHOLDER", chla_gulf_json),
    ("CHLA_SNAPSHOT_SOUTH_FLORIDA_PLACEHOLDER", chla_sofl_json),
    ("CHLA_SNAPSHOT_SOUTH_ATLANTIC_PLACEHOLDER", chla_seatl_json),
]:
    if ph in template:
        template = template.replace(ph, blob)
# v24.32 (Randy 2026-08-29): state artificial reef registries — AL/MS/LA
# state reef registry data scraped 2026-08-29 into data/state_reefs.json
# (2331 total sites). Loaded once at build time and inlined; no per-day fetch
# needed since these registries update quarterly at most.
_state_reefs_path = pathlib.Path(__file__).parent / "data" / "state_reefs.json"
if _state_reefs_path.exists():
    with open(_state_reefs_path) as _srf:
        _state_reefs_json = _srf.read()
    print(f"  State reefs bundle: {len(_state_reefs_json):,} bytes")
else:
    _state_reefs_json = "null"
assert "STATE_REEFS_PLACEHOLDER" in template, "STATE_REEFS placeholder not found"
template = template.replace("STATE_REEFS_PLACEHOLDER", _state_reefs_json.rstrip().rstrip(";"))

assert "CURRENTS_SNAPSHOT_PLACEHOLDER" in template, "Currents snapshot placeholder not found"
template = template.replace("CURRENTS_SNAPSHOT_PLACEHOLDER", currents_json)
assert "ZONE_WEATHER_PLACEHOLDER" in template, "Zone weather placeholder not found"
template = template.replace("ZONE_WEATHER_PLACEHOLDER", zone_weather_json)
assert "ZONE_WEATHER_MIDATL_PLACEHOLDER" in template, "Zone weather midatl placeholder not found"
template = template.replace("ZONE_WEATHER_MIDATL_PLACEHOLDER", zone_weather_midatl_json)
assert "ZONE_WEATHER_GULF_PLACEHOLDER" in template, "Zone weather gulf placeholder not found"
template = template.replace("ZONE_WEATHER_GULF_PLACEHOLDER", zone_weather_gulf_json)
assert "ZONE_WEATHER_SOUTH_FLORIDA_PLACEHOLDER" in template, "Zone weather s.florida placeholder not found"
template = template.replace("ZONE_WEATHER_SOUTH_FLORIDA_PLACEHOLDER", zone_weather_sofl_json)
assert "ZONE_WEATHER_SOUTH_ATLANTIC_PLACEHOLDER" in template, "Zone weather s.atlantic placeholder not found"
template = template.replace("ZONE_WEATHER_SOUTH_ATLANTIC_PLACEHOLDER", zone_weather_seatl_json)
# v23.32 satellite layer metadata — probe HLS Sentinel-30m NOW (before the
# replacement block below tries to read _satellite_layer_meta_json). NASA
# GIBS is free + no auth. Probe cost: up to 8 HEAD-like GETs, usually
# resolves on the 1st-3rd try.
print("Probing HLS Sentinel-30m for latest available imagery date...")
_hls_latest = probe_hls_s30_latest_date()
if _hls_latest:
    print(f"  ✓ Latest HLS S30 date over NE offshore: {_hls_latest}")
else:
    print(f"  ⚠  No HLS S30 tiles found in the last 8 days — 🛰 Satellite layer will fall back to MODIS Terra True Color (250m).")
_satellite_layer_meta_json = json.dumps({
    "hls_latest_date": _hls_latest,
    "modis_fallback_date": (datetime.date.today() - datetime.timedelta(days=1)).isoformat(),
    "probed_at": datetime.datetime.utcnow().isoformat() + "Z",
})
assert "SATELLITE_LAYER_META_PLACEHOLDER" in template, "Satellite layer meta placeholder not found"
template = template.replace("SATELLITE_LAYER_META_PLACEHOLDER", _satellite_layer_meta_json)

# v23.48 (Randy 2026-08-15): "Only look for those groupings of boats at like
# twelve o'clock… if you think it's a grouping of boats, could you like
# circle it and make it obvious?" Boat-cluster candidate detection runs at
# each build via boat_cluster_detector.py. Results land in
# data/boat_clusters.json and get baked into BOAT_CLUSTERS below so the
# map's 🎯 Clusters layer can circle each candidate.
try:
    print("Running boat-cluster candidate detection (Sentinel-30m noon pass)...")
    import subprocess
    _bc_result = subprocess.run(
        ["python3", "/root/fish-finder/boat_cluster_detector.py"],
        capture_output=True, text=True, timeout=180
    )
    if _bc_result.returncode == 0:
        _last_line = (_bc_result.stdout.strip().split("\n") or [""])[-1] if _bc_result.stdout else ""
        print(f"  ✓ {_last_line}")
    else:
        print(f"  ⚠  Detector exit {_bc_result.returncode}. Layer will show empty.")
except Exception as _bc_err:
    print(f"  ⚠  Detector failed: {_bc_err}. Layer will show empty.")
_boat_clusters_path = pathlib.Path("/root/fish-finder/data/boat_clusters.json")
if _boat_clusters_path.exists():
    _boat_clusters_json = _boat_clusters_path.read_text()
else:
    _boat_clusters_json = '{"candidates": [], "notes": ["detector output missing"]}'
assert "BOAT_CLUSTERS_PLACEHOLDER" in template, "Boat clusters placeholder not found"
template = template.replace("BOAT_CLUSTERS_PLACEHOLDER", _boat_clusters_json)

# v23.53 (Randy 2026-08-15): "Could you add something to our chart somehow
# somewhere to show the current tides and what times high, low, and high,
# low, and maybe make it so I can toggle it?" Tide-harvester fetches
# today+tomorrow high/low predictions from NOAA CO-OPS for 6 stations
# blanketing Randy's fishing range (New London through Atlantic City).
# Runs at every build; data lands in data/tides.json and gets baked into
# TIDES below so the map's 🌊 Tides layer can render marker + popup at
# each station without any runtime API calls.
try:
    print("Fetching NOAA tide predictions...")
    import subprocess
    _t_result = subprocess.run(
        ["python3", "/root/fish-finder/tide_harvester.py"],
        capture_output=True, text=True, timeout=90
    )
    if _t_result.returncode == 0:
        _last = (_t_result.stdout.strip().split("\n") or [""])[-1] if _t_result.stdout else ""
        print(f"  ✓ {_last}")
    else:
        print(f"  ⚠  Tide harvester exit {_t_result.returncode}. Layer will show empty popups.")
except Exception as _t_err:
    print(f"  ⚠  Tide harvester failed: {_t_err}. Layer will show empty popups.")
_tides_path = pathlib.Path("/root/fish-finder/data/tides.json")
if _tides_path.exists():
    _tides_json = _tides_path.read_text()
else:
    _tides_json = '{"stations": [], "notes": ["tide harvester output missing"]}'
assert "TIDES_PLACEHOLDER" in template, "Tides placeholder not found"
template = template.replace("TIDES_PLACEHOLDER", _tides_json)

# v24.106 (First Mate 2026-09-05) — NDBC_BUOYS_PLACEHOLDER was never
# substituted, causing every page load to hit `NDBC_BUOYS_PLACEHOLDER is
# not defined` → JS crash → entire Report + drawers didn't render. Root
# cause: ndbc_buoy_harvest.py exists as a standalone script but was never
# wired into the nightly build. Wire it now + substitute the placeholder.
try:
    print("Fetching NDBC realtime2 buoy observations...")
    _ndbc_result = subprocess.run(
        ["python3", "/root/fish-finder/ndbc_buoy_harvest.py"],
        capture_output=True, text=True, timeout=90
    )
    if _ndbc_result.returncode == 0:
        _last = (_ndbc_result.stdout.strip().split("\n") or [""])[-1] if _ndbc_result.stdout else ""
        print(f"  ✓ {_last}")
    else:
        print(f"  ⚠  NDBC harvester exit {_ndbc_result.returncode}. Layer will be empty.")
except Exception as _n_err:
    print(f"  ⚠  NDBC harvester failed: {_n_err}. Layer will be empty.")
_ndbc_path = pathlib.Path("/root/fish-finder/data/ndbc_buoys.json")
if _ndbc_path.exists():
    _ndbc_json = _ndbc_path.read_text()
else:
    _ndbc_json = '{"stations": [], "counts": {}, "fetched_at": null, "notes": ["ndbc harvester output missing"]}'
try:
    ff_health.record("ndbc_buoys", rows=len(json.loads(_ndbc_json).get("stations", [])), ok=True)
except Exception:
    pass
if "NDBC_BUOYS_PLACEHOLDER" in template:
    template = template.replace("NDBC_BUOYS_PLACEHOLDER", _ndbc_json)

# v24.114 (Randy 2026-09-11) — bait & lure intel from YouTube transcripts.
# Run the extractor now (reads cache/yt_transcripts/*, produces
# data/bait_lure_intel.json) and inline the JSON as the JS global
# BAIT_LURE_INTEL. Read by the zone popup's zoneBaitLureIntel() renderer.
# Failing here should NOT kill the build — the popup gracefully hides the
# section when the global is empty or the extractor errored.
print("Extracting bait/lure intel from cached transcripts...")
try:
    import bait_lure_extractor as ff_bl
    _bl_zone_map = (json.loads((BASE / "data" / "youtube_zone_map.json").read_text())
                    or {}).get("location_to_zones", {})
    # Reuse youtube_intel we just built so video meta (channel/date) is fresh
    _bl_video_meta = {}
    for _vid_id, _info in (youtube_intel.get("videos") or {}).items() if isinstance(youtube_intel.get("videos"), dict) else []:
        _bl_video_meta[_vid_id] = _info
    # Alternative: read from the harvested video list if it's a list
    if not _bl_video_meta:
        for _ch in (youtube_intel.get("channels") or []):
            for _v in (_ch.get("all_videos") or []):
                if _v.get("video_id"):
                    _bl_video_meta[_v["video_id"]] = {
                        "channel": _ch.get("name", "?"),
                        "published_at": _v.get("published", ""),
                        "title": _v.get("title", "?"),
                    }
    _bl_intel = ff_bl.build_intel(_bl_zone_map, BASE / "cache" / "yt_transcripts", _bl_video_meta)
    (BASE / "data" / "bait_lure_intel.json").write_text(json.dumps(_bl_intel, indent=2))
    _bl_json = json.dumps(_bl_intel)
    print(f"  Bait/lure intel: {_bl_intel['zones_with_bait_lure_intel']} zones, "
          f"{_bl_intel['total_mentions']} mentions from {_bl_intel['transcripts_scanned']} transcripts")
except Exception as _bl_e:
    print(f"  ⚠  bait/lure extractor failed (non-fatal): {_bl_e}")
    _bl_json = '{"generated_at":null,"transcripts_scanned":0,"zones_with_bait_lure_intel":0,"total_mentions":0,"by_zone":{}}'
if "BAIT_LURE_INTEL_PLACEHOLDER" in template:
    template = template.replace("BAIT_LURE_INTEL_PLACEHOLDER", _bl_json)

# v24.130 (Randy 2026-09-15): pick backup audit — inline data/pick_audit.json
# as PICK_AUDIT so the map popup + pick card can render a "⚠ Provisional"
# chip when a top-5 zone in any region has 0 intel_sources / stale heat /
# no notes. Runs early (right after we've established zones + intel_sources)
# so the audit is against the just-refreshed data, not stale from earlier
# in the build.
_pick_audit_path = BASE / "data" / "pick_audit.json"
try:
    import subprocess as _sp
    _sp.run(["python3", str(BASE / "pick_backup_audit.py")], timeout=30,
            capture_output=True, text=True)
except Exception:
    pass  # audit is advisory — never kill the build
if _pick_audit_path.exists():
    _pick_audit_json = _pick_audit_path.read_text()
else:
    _pick_audit_json = '{"date":null,"regions":{},"summary":{"backed_pct":0,"red_regions":[],"yellow_regions":[],"green_regions":[]}}'
if "PICK_AUDIT_PLACEHOLDER" in template:
    template = template.replace("PICK_AUDIT_PLACEHOLDER", _pick_audit_json)

# v23.34 (Randy 2026-08-12): tracked charter captains — home ports + MMSIs
# for the 🎣 Captains map layer. Randy: "Track all the charter boats we're
# pulling information from. Focus on tuna and canyons/swordfish. Add them
# to the map so we can follow them." The file at data/captain_vessels.json
# is the source of truth; add captains + MMSIs there.
_captain_vessels_path = pathlib.Path("/root/fish-finder/data/captain_vessels.json")
if _captain_vessels_path.exists():
    _captain_vessels_json = _captain_vessels_path.read_text()
else:
    _captain_vessels_json = '{"_notes": "captain_vessels.json missing", "captains": []}'
assert "CAPTAIN_VESSELS_PLACEHOLDER" in template, "Captain vessels placeholder not found"
template = template.replace("CAPTAIN_VESSELS_PLACEHOLDER", _captain_vessels_json)

# v23.35 (Randy 2026-08-12) — Captain-dwell scoring. Randy's "holy grail":
# when a captain vessel stays at a spot ≥ 30 min, they're fishing → that
# spot IS the bite → boost tomorrow's picks accordingly.
#
# Pipeline: aisstream_poll.py (needs Randy's aisstream API key) writes AIS
# positions to data/captain_positions.jsonl. dwell_analysis.py reads that
# and computes per-zone dwell scores over the last 72h. We bake those
# scores here so the client-side captainDwellBoost() reads them at render
# time. If there's no data yet (aisstream not activated), the summary is
# an empty dict and the boost silently returns 0 for every zone.
try:
    import dwell_analysis as ff_dwells
    # v24.105 — bug fix: `zones_data` was referenced here before being defined
    # (its actual definition is 100+ lines below at 2058/2068). Every build
    # printed "captain-dwell analysis failed (name 'zones_data' is not defined)"
    # so the captainDwellBoost signal has been silently 0 for every zone
    # since captain-dwell was added. Load zones locally from disk here.
    _dwell_zones_local = json.loads(zones_json)
    _dwell_positions = ff_dwells.load_positions()
    _dwell_events = ff_dwells.detect_dwells(_dwell_positions)
    # Filter out dwells too close to Randy's home port (usually at anchor)
    _hp_coords = _dwell_zones_local.get("home_port", {}).get("coords") or [41.298, -72.372]
    _dwell_events = ff_dwells.filter_out_home_port(_dwell_events, tuple(_hp_coords))
    _captain_dwells_summary = ff_dwells.dwell_summary_for_zones(
        _dwell_events, _dwell_zones_local.get("zones", []), lookback_hours=72)
    print(f"  Captain dwells: {len(_dwell_positions)} raw positions → "
          f"{len(_dwell_events)} dwell events → "
          f"{len(_captain_dwells_summary)} zones with captain activity")
except Exception as e:
    # v24.105 — full traceback logging (same pattern as v24.104's archive fix)
    import traceback as _tb
    _tb.print_exc()
    try:
        _err_path = BASE / "cache" / "build_errors.log"
        _err_path.parent.mkdir(exist_ok=True)
        with open(_err_path, "a") as _ef:
            _ef.write(f"\n=== captain-dwell failure @ {datetime.datetime.utcnow().isoformat()}Z ===\n")
            _tb.print_exc(file=_ef)
    except Exception:
        pass
    print(f"  WARN: captain-dwell analysis failed ({e}); summary will be empty.")
    _captain_dwells_summary = {}
_captain_dwells_json = json.dumps({
    "summary": _captain_dwells_summary,
    "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
    "activation_status": "active" if _captain_dwells_summary else "waiting_for_aisstream_key",
})
assert "CAPTAIN_DWELLS_PLACEHOLDER" in template, "Captain dwells placeholder not found"
template = template.replace("CAPTAIN_DWELLS_PLACEHOLDER", _captain_dwells_json)

# v24.132 (Randy 2026-09-15) — Anonymous fleet position trails. Reads
# data/captain_positions.jsonl (populated by aisstream_poll.py when Randy
# activates AIS) and inlines the last 24h as a plain array of {ts, lat, lon}
# — MMSIs and vessel names STRIPPED. The map renders these as aggregate
# 30-min-bucket trails, so the visible surface never per-boat.
_captain_positions_path = BASE / "data" / "captain_positions.jsonl"
_captain_positions = []
if _captain_positions_path.exists():
    try:
        _cutoff = (datetime.datetime.utcnow() - datetime.timedelta(hours=24)).isoformat() + "Z"
        with open(_captain_positions_path) as _pf:
            for _line in _pf:
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _rec = json.loads(_line)
                except Exception:
                    continue
                _ts = _rec.get("ts", "")
                if _ts < _cutoff:
                    continue
                _lat = _rec.get("lat")
                _lon = _rec.get("lon")
                if not (isinstance(_lat, (int, float)) and isinstance(_lon, (int, float))):
                    continue
                # Strip identifying fields — build-inlined is the choke point that
                # guarantees the map never gets MMSIs or vessel names, per Randy's
                # 2026-09-15 rule ("track them, but don't say who each one is").
                _captain_positions.append({"ts": _ts, "lat": _lat, "lon": _lon})
    except Exception as _e:
        print(f"  WARN: reading captain_positions.jsonl failed ({_e}) — trails empty")
_captain_positions_json = json.dumps(_captain_positions)
if "CAPTAIN_POSITIONS_PLACEHOLDER" in template:
    template = template.replace("CAPTAIN_POSITIONS_PLACEHOLDER", _captain_positions_json)
    print(f"  Anonymous fleet positions (last 24h): {len(_captain_positions)} points inlined")

# -------- DERIVED SIGNALS (computed from raw data above) --------
# The model uses these to spot conditions that concentrate bait & tuna:
# hard SST breaks, hard chlorophyll edges, current convergences, persistent
# breaks that have stayed in the same place for multiple days, and pressure
# state (post-front vs falling-fast). All feed the client-side effectiveHeat.
print("Computing derived model signals...")

sst_gradient_field = ff_signals.compute_sst_gradient_field(
    sst_contours.get("_rows", []), threshold_f_per_nm=0.4)
print(f"  SST gradient hot points (>=0.4°F/nm): {len(sst_gradient_field)}")

chla_gradient_field = ff_signals.compute_chla_gradient_field(
    chla_snapshot.get("_rows", []), threshold_mg_per_nm=0.02)
print(f"  Chlorophyll gradient hot points (>=0.02 mg/nm): {len(chla_gradient_field)}")

current_convergence = ff_signals.compute_current_convergence(
    currents_snapshot.get("arrows", []))
print(f"  Current convergence points: {len(current_convergence)}")

persistent_breaks = ff_signals.compute_persistence_flags(
    sst_contours.get("contours", {}),
    BASE / "archive",
    window_days=3, overlap_nm=10.0,
    snapshot_key="sst_contours",   # NE
)
# v24.57 (Randy 2026-08-31): per-region persistence. Each region reads from
# its own archived snapshot key ("sst_contours_gulf" etc.). Currently these
# keys don't exist in older snapshots — the archive extension in save_snapshot
# starts populating them TONIGHT. So persistence for non-NE regions activates
# 3 days from now (once ≥3 days of per-region contours accumulate). Until
# then compute_persistence_flags returns [] for those regions (missing prior
# snapshot early-exit), which is honest.
for _rkey, _contours_dict in [
    ("gulf",           sst_contours_gulf.get("contours", {})),
    ("south_florida",  sst_contours_sofl.get("contours", {})),
    ("south_atlantic", sst_contours_seatl.get("contours", {})),
]:
    try:
        _rp = ff_signals.compute_persistence_flags(
            _contours_dict,
            BASE / "archive",
            window_days=3, overlap_nm=10.0,
            snapshot_key=f"sst_contours_{_rkey}",
        )
        if _rp:
            for pt in _rp:
                pt["region"] = _rkey
            persistent_breaks.extend(_rp)
            print(f"  Persistent SST breaks in {_rkey}: {len(_rp)}")
    except Exception as _pe:
        print(f"  WARN: [{_rkey}] persistence flag compute failed ({_pe})")
print(f"  Persistent SST breaks (≥3 days in place, all regions): {len(persistent_breaks)}")

pressure_state = ff_signals.analyze_pressure_trend(pressure_snapshot.get("samples", []))
print(f"  Pressure state: {pressure_state.get('state')} (24h Δ = {pressure_state.get('delta_24h_hpa')} hPa)")

# -------- YOUTUBE CAPTAIN INTEL HARVEST --------
# Pull the last 14 days from 15 verified NE fishing YouTube channels (charter
# captains, tackle shops, regional publications). Extract species / location /
# trip-recap patterns from each recent title + description. This feeds the
# Captain's two-independent-sources rule as ONE source; heat changes still
# require a second confirmation from a captain report or aggregator.
print("Harvesting YouTube captain intel (last 14 days)...")
try:
    youtube_intel = ff_youtube.harvest_all(
        BASE / "data" / "youtube_channels.json",
        cutoff_days=14,
        verbose=False,
    )
    print(f"  Channels responding: {youtube_intel['channels_ok']}/{youtube_intel['channel_count']}")
    print(f"  Videos with intel in 14d window: {youtube_intel['total_videos_in_window']}")
    corrob = youtube_intel.get("corroboration", {})
    sp = corrob.get("species", {})
    loc = corrob.get("locations", {})
    if sp:
        top_species = sorted(sp.items(), key=lambda kv: -len(kv[1]))[:3]
        print(f"  Top species mentions: {', '.join(f'{k}({len(v)})' for k, v in top_species)}")
    if loc:
        top_loc = sorted(loc.items(), key=lambda kv: -len(kv[1]))[:5]
        print(f"  Top location mentions: {', '.join(f'{k}({len(v)})' for k, v in top_loc)}")
except Exception as _yt_err:
    print(f"  WARN: YouTube harvest failed ({_yt_err})")
    youtube_intel = {
        "harvested_at": datetime.datetime.utcnow().isoformat() + "Z",
        "cutoff_days": 14,
        "error": str(_yt_err),
        "channels": [],
        "corroboration": {"locations": {}, "species": {}},
        "total_videos_in_window": 0,
        "channels_ok": 0,
    }

# -------- HEAT-FRESHNESS BUMP + v24.55 AUTO-HEAT LIFT --------
# Randy's 2026-07-31 rule: every zone needs a visible heat_updated stamp so we
# can see WHEN the underlying intel was last corroborated.
#
# v24.55 (Randy 2026-08-31) EXTENSION: for the 4 regions without a Randy
# feeding first-party captain intel, YouTube titles with hot language
# ("epic day", "loaded up", "wide open", "limits") also LIFT zone.heat off
# the 5.0 default. Cap at 8.0; anything higher still needs human verification.
# See _apply_youtube_intel_to_region + _yt_heat_lift helpers at file top.
#
# NOTE ordering: the ZONE_DATA_* placeholders were already substituted into
# the template at lines ~1112-1153 using the disk contents at read time.
# After enrichment we RE-substitute each region's updated blob back into
# the template so the freshly-lifted values reach today's shipped HTML.
zm = youtube_intel.get("zone_mentions", {}) or {}
_today_iso = datetime.date.today().isoformat()


# v24.79→v24.83 (Randy 2026-09-03): multi-source zone-mention refresh —
# scans OTW weekly reports + The Fisherman Magazine + Fisherman's Post
# in parallel; bumps heat_updated on any zone mentioned in any recent report.
try:
    # v24.133 (Randy 2026-09-17) — this step KILLED the nightly build three
    # nights in a row (Sept 15/16/17). Symptom: log printed "=== NE — 119
    # zone lookups ===" then process SIGKILL'd silently with no traceback.
    # 46 URLs × 3 regions × ThreadPool(16) is inside sandbox tolerance in
    # theory but hangs/slow fetches to OTW/Fisherman can drag any one worker
    # past the sandbox's silent wall-clock cap. Cage it in a subprocess with
    # a HARD 120s per-region timeout so a hang can't kill the whole build.
    # The build continues either way; if refresh times out, zones' YouTube
    # auto-bump still runs downstream and captain intel is preserved from
    # the previous nightly. Not ideal, but far better than 3 stale nights.
    print("Refreshing NE + Mid-Atl + SE zone heat_updated from OTW + Fisherman sources...")
    import subprocess as _sp
    for _lab, _fn in [("NE", "zones.json"), ("Mid-Atl", "zones_mid_atlantic.json"), ("SE", "zones_south_atlantic.json")]:
        _refresh_cmd = [
            "python3", "-c",
            (
                "import sys, json; sys.path.insert(0, '/root/fish-finder'); "
                "import multi_source_zone_refresh as m; "
                f"m.refresh_region(__import__('pathlib').Path('/root/fish-finder/data/{_fn}'), '{_lab}')"
            ),
        ]
        try:
            _rr = _sp.run(_refresh_cmd, capture_output=True, text=True, timeout=120)
            _out = (_rr.stdout or "").strip()
            _err = (_rr.stderr or "").strip()
            if _out:
                print(_out)
            if _rr.returncode != 0:
                print(f"  {_lab} refresh subprocess exit={_rr.returncode}: {_err[:400]}")
        except _sp.TimeoutExpired:
            print(f"  ⏱  {_lab} refresh exceeded 120s wall-clock — skipped this region (previous nightly's captain intel preserved)")
        except Exception as _re:
            print(f"  {_lab} refresh wrapper crashed: {_re}")
except Exception as _oe:
    print(f"  WARN: multi-source refresh wrapper failed: {_oe}")

# Region 1 — NORTHEAST. The zones_data variable is used later by the bait/whale
# promotion block below, so keep it in-scope by reading it here and passing to
# the helper via disk (helper returns updated JSON string).
_ne_new_blob, _ne_stamped, _ne_dated, _ne_lifted = _apply_youtube_intel_to_region(
    BASE / "data" / "zones.json", zm, "NE", _today_iso
)
if _ne_new_blob is not None:
    zones_data = json.loads(_ne_new_blob)
    # Keep zones_json in sync — later bait/whale promotion uses it
    zones_json = _ne_new_blob
    # Re-substitute NE into template
    _old = zones_json_enriched.rstrip().rstrip(";")
    _new = _ne_new_blob.rstrip().rstrip(";")
    if _old in template and _old != _new:
        template = template.replace(_old, _new)
        zones_json_enriched = _new
else:
    zones_data = json.loads(zones_json)

# Region 2 — MID-ATLANTIC.
_ma_new_blob, _ma_stamped, _ma_dated, _ma_lifted = _apply_youtube_intel_to_region(
    BASE / "data" / "zones_mid_atlantic.json", zm, "Mid-Atl", _today_iso
)
if _ma_new_blob is not None:
    _new = _ma_new_blob.rstrip().rstrip(";")
    if _midatl_injected_blob in template and _midatl_injected_blob != _new:
        template = template.replace(_midatl_injected_blob, _new)
        _midatl_injected_blob = _new

# Region 3 — GULF.
_gu_new_blob, _gu_stamped, _gu_dated, _gu_lifted = _apply_youtube_intel_to_region(
    BASE / "data" / "zones_gulf.json", zm, "Gulf", _today_iso
)
if _gu_new_blob is not None:
    _new = _gu_new_blob.rstrip().rstrip(";")
    if _gulf_injected_blob in template and _gulf_injected_blob != _new:
        template = template.replace(_gulf_injected_blob, _new)
        _gulf_injected_blob = _new

# Region 4 — SOUTH FLORIDA.
_sf_new_blob, _sf_stamped, _sf_dated, _sf_lifted = _apply_youtube_intel_to_region(
    BASE / "data" / "zones_south_florida.json", zm, "S.FL", _today_iso
)
if _sf_new_blob is not None:
    _new = _sf_new_blob.rstrip().rstrip(";")
    if _sofl_injected_blob in template and _sofl_injected_blob != _new:
        template = template.replace(_sofl_injected_blob, _new)
        _sofl_injected_blob = _new

# Region 5 — SOUTH ATLANTIC (Southeast).
_se_new_blob, _se_stamped, _se_dated, _se_lifted = _apply_youtube_intel_to_region(
    BASE / "data" / "zones_south_atlantic.json", zm, "SE", _today_iso
)
if _se_new_blob is not None:
    _new = _se_new_blob.rstrip().rstrip(";")
    if _seatl_injected_blob in template and _seatl_injected_blob != _new:
        template = template.replace(_seatl_injected_blob, _new)
        _seatl_injected_blob = _new

_yt_lifted_total = _ne_lifted + _ma_lifted + _gu_lifted + _sf_lifted + _se_lifted
if _yt_lifted_total:
    print(f"  v24.55: YT auto-heat lifted {_yt_lifted_total} zones across 5 regions")

# ================================================================
# v23.11 (Randy 2026-08-08): YouTube-derived BAIT + WHALE promotion.
# Randy asked "when you scour those YouTube things you should be looking
# for bait information and whale sightings in the text." The harvester now
# extracts those mentions per video; here we promote them into
# zones_data["bait_intel"] and zones_data["whale_sightings"] so they
# actually flow into the pinned bait layer + whaleBoost signal.
#
# Guardrails:
#  - Deduplicate: skip if a (source, date, zone) triple is already present.
#  - Tag every added entry `"source_type": "youtube_auto"` so the Captain can
#    later filter or downgrade these if they turn out low-quality vs first-
#    party captain quotes.
#  - Only promote when the mention has BOTH a bait/whale hit AND a location
#    that maps to at least one real zone (via youtube_zone_map). Videos with
#    a bait mention but no zone attribution get preserved in raw_intel for
#    future review but not merged into bait_intel.
# ================================================================
_yt_bait_promoted = 0
_yt_whale_promoted = 0
try:
    corr = youtube_intel.get("corroboration", {}) or {}
    yt_bait = corr.get("bait", {}) or {}
    yt_whales = corr.get("whales", {}) or {}
    # Load location→zones map (same file youtube_harvest uses for zone_mentions)
    zone_map = {}
    try:
        zm_path = BASE / "data" / "youtube_zone_map.json"
        if zm_path.exists():
            zone_map = json.loads(zm_path.read_text()).get("location_to_zones", {}) or {}
    except Exception:
        pass

    # ---- NE promotion (also re-substitutes into the template so today's HTML sees it)
    _ne_zone_ids = {z.get("id") for z in zones_data.get("zones", []) if z.get("id")}
    _yt_bait_promoted, _yt_whale_promoted = _promote_yt_intel_for_region(
        zones_data, yt_bait, yt_whales, zone_map, _ne_zone_ids, "NE")
    if _yt_bait_promoted or _yt_whale_promoted:
        (BASE / "data" / "zones.json").write_text(json.dumps(zones_data, indent=2))
        print(f"  Promoted from YouTube (NE): {_yt_bait_promoted} bait entries · {_yt_whale_promoted} whale sightings")
        try:
            zones_json_updated = json.dumps(zones_data, indent=2).rstrip().rstrip(";")
            old_blob = zones_json_enriched.rstrip().rstrip(";")
            if old_blob in template:
                template = template.replace(old_blob, zones_json_updated)
                zones_json_enriched = zones_json_updated
                zones_json = json.dumps(zones_data, indent=2)
        except Exception as _e:
            print(f"  WARN: could not re-substitute after YT bait/whale promotion ({_e})")

    # v24.58 (Randy 2026-08-31): loop over the other 4 regions instead of only
    # Mid-Atl. Gulf/S.FL/SE now get YT-derived whale_sightings + bait_intel
    # populated for region-tagged videos — closes the whaleBoost=0 gap.
    for _rkey, _rzones_path, _rlabel in [
        ("mid_atlantic",   BASE / "data" / "zones_mid_atlantic.json",   "Mid-Atl"),
        ("gulf",           BASE / "data" / "zones_gulf.json",           "Gulf"),
        ("south_florida",  BASE / "data" / "zones_south_florida.json",  "S.FL"),
        ("south_atlantic", BASE / "data" / "zones_south_atlantic.json", "SE"),
    ]:
        if not _rzones_path.exists():
            continue
        try:
            _rdata = json.loads(_rzones_path.read_text())
            _rids = {z.get("id") for z in _rdata.get("zones", []) if z.get("id")}
            _rb, _rw = _promote_yt_intel_for_region(
                _rdata, yt_bait, yt_whales, zone_map, _rids, _rlabel)
            if _rb or _rw:
                _rzones_path.write_text(json.dumps(_rdata, indent=2))
                print(f"  Promoted from YouTube ({_rlabel}): {_rb} bait entries · {_rw} whale sightings")
        except Exception as _re:
            print(f"  WARN: [{_rlabel}] YT bait/whale promotion failed ({_re})")
except Exception as _e:
    # v24.105 — add traceback logging (same pattern as v24.104's archive fix
    # and other mega-tries this session). The YouTube-bait/whale-promotion
    # block is ~50 lines wrapping regex + multiple per-region file writes;
    # a single-line WARN was hiding real bugs.
    import traceback as _tb
    _tb.print_exc()
    try:
        _err_path = BASE / "cache" / "build_errors.log"
        _err_path.parent.mkdir(exist_ok=True)
        with open(_err_path, "a") as _ef:
            _ef.write(f"\n=== youtube_bait_whale_promotion failure @ {datetime.datetime.utcnow().isoformat()}Z ===\n")
            _tb.print_exc(file=_ef)
    except Exception:
        pass
    print(f"  WARN: YouTube bait/whale promotion failed ({_e}) — proceeding without")
    # Re-enrich (re-apply SST samples if we have them) and re-substitute into
    # the already-processed template so the current build carries the bumps.
    try:
        for z in zones_data["zones"]:
            if 'sst_map' in dir() and z["id"] in sst_map:  # noqa
                z["current_sst_f"] = sst_map[z["id"]]
    except Exception:
        pass
    zones_json_updated = json.dumps(zones_data, indent=2).rstrip().rstrip(";")
    old_blob = zones_json_enriched.rstrip().rstrip(";")
    if old_blob in template:
        template = template.replace(old_blob, zones_json_updated)
        zones_json_enriched = zones_json_updated
        zones_json = json.dumps(zones_data, indent=2)
        print(f"  Re-substituted ZONE_DATA into template with bumped heat_updated")
    else:
        print(f"  WARN: could not find old zone blob to replace — bump will apply next build")

# ================================================================
# v24.7 (2026-08-23): The Fisherman regional forecast harvester.
# Aggregator source that publishes weekly video/text regional forecasts via
# WordPress REST API. Coverage: NE (New England + Long Island editions) +
# Mid-Atl (NJ/DE Bay Region + South Jersey editions). Auto-promotes each
# post-with-mentions into bait_intel for the appropriate region, tagged
# source_type="fisherman_auto".
# ================================================================
try:
    import fisherman_harvest as ff_fisherman
    fisherman_result = ff_fisherman.harvest(cutoff_days=14)
    n_ne = len(fisherman_result.get("northeast_posts", []))
    n_ma = len(fisherman_result.get("midatl_posts", []))
    print(f"Harvested The Fisherman regional forecasts: NE {n_ne}, Mid-Atl {n_ma} (last 14 days)")
    try:
        # v24.105 — record even zero-row success (site was up, we parsed clean;
        # the 'no new posts this week' state is not a failure).
        ff_health.record("fisherman_harvest", rows=n_ne + n_ma, ok=True)
    except Exception:
        pass
    # Defensive: re-derive zone_map + _midatl_path in case the YouTube block above
    # threw before defining them.
    _midatl_path = BASE / "data" / "zones_mid_atlantic.json"
    zone_map_path = BASE / "data" / "youtube_zone_map.json"
    zone_map = json.loads(zone_map_path.read_text()).get("location_to_zones", {}) if zone_map_path.exists() else {}
    if n_ne or n_ma:
        # Load region zone-id sets for promotion filtering
        ne_zone_ids = {z.get("id") for z in zones_data.get("zones", []) if z.get("id")}
        _ma_data_for_fm = json.loads(_midatl_path.read_text()) if _midatl_path.exists() else {"zones": []}
        ma_zone_ids_fm = {z.get("id") for z in _ma_data_for_fm.get("zones", []) if z.get("id")}
        fm_entries, fm_intel = ff_fisherman.promote_into_zones(
            fisherman_result, zone_map, ma_zone_ids_fm, ne_zone_ids
        )
        # NE promotion
        ne_bait_keys = {(e.get("date"), e.get("source"), tuple(sorted(e.get("zones") or [])))
                        for e in zones_data.get("bait_intel", [])}
        n_ne_added = 0
        for e in fm_entries["ne"]:
            key = (e["date"], e["source"], tuple(sorted(e["zones"])))
            if key in ne_bait_keys:
                continue
            zones_data.setdefault("bait_intel", []).append({
                "date": e["date"], "bait": "regional_forecast", "location": "The Fisherman",
                "species_feeding": None, "source": e["source"], "source_type": e["source_type"],
                "quote": e["excerpt"] or e["title"], "link": e["link"], "zones": e["zones"],
            })
            ne_bait_keys.add(key)
            n_ne_added += 1
        for zid, meta in fm_intel["ne"].items():
            for z in zones_data.get("zones", []):
                if z.get("id") == zid:
                    z.setdefault("intel_sources", {})
                    z["intel_sources"]["fisherman"] = {
                        "source_count": meta["source_count"],
                        "latest_date": meta["latest_date"],
                    }
                    break
        if n_ne_added or fm_intel["ne"]:
            (BASE / "data" / "zones.json").write_text(json.dumps(zones_data, indent=2))
            print(f"  Fisherman (NE): {n_ne_added} bait entries, {len(fm_intel['ne'])} zones stamped")
            # Re-substitute
            zjs = json.dumps(zones_data, indent=2).rstrip().rstrip(";")
            old_blob = zones_json_enriched.rstrip().rstrip(";")
            if old_blob in template:
                template = template.replace(old_blob, zjs)
                zones_json_enriched = zjs
                zones_json = json.dumps(zones_data, indent=2)
        # Mid-Atl promotion
        if _midatl_path.exists() and (fm_entries["midatl"] or fm_intel["midatl"]):
            ma_bait_keys = {(e.get("date"), e.get("source"), tuple(sorted(e.get("zones") or [])))
                            for e in _ma_data_for_fm.get("bait_intel", [])}
            n_ma_added = 0
            for e in fm_entries["midatl"]:
                key = (e["date"], e["source"], tuple(sorted(e["zones"])))
                if key in ma_bait_keys:
                    continue
                _ma_data_for_fm.setdefault("bait_intel", []).append({
                    "date": e["date"], "bait": "regional_forecast", "location": "The Fisherman",
                    "species_feeding": None, "source": e["source"], "source_type": e["source_type"],
                    "quote": e["excerpt"] or e["title"], "link": e["link"], "zones": e["zones"],
                })
                ma_bait_keys.add(key)
                n_ma_added += 1
            for zid, meta in fm_intel["midatl"].items():
                for z in _ma_data_for_fm.get("zones", []):
                    if z.get("id") == zid:
                        z.setdefault("intel_sources", {})
                        z["intel_sources"]["fisherman"] = {
                            "source_count": meta["source_count"],
                            "latest_date": meta["latest_date"],
                        }
                        break
            _midatl_path.write_text(json.dumps(_ma_data_for_fm, indent=2))
            print(f"  Fisherman (Mid-Atl): {n_ma_added} bait entries, {len(fm_intel['midatl'])} zones stamped")
except Exception as _fe:
    # v24.105 — traceback + source_health, same fix pattern as charter block.
    import traceback as _tb
    _err_path = BASE / "cache" / "build_errors.log"
    try:
        _err_path.parent.mkdir(exist_ok=True)
        with open(_err_path, "a") as _ef:
            _ef.write(f"\n=== fisherman_harvest failure @ {datetime.datetime.utcnow().isoformat()}Z ===\n")
            _tb.print_exc(file=_ef)
    except Exception:
        pass
    _tb.print_exc()
    print(f"  WARN: Fisherman harvest failed ({_fe}) — proceeding without")
    fisherman_result = {"harvested_at": None, "northeast_posts": [], "midatl_posts": [], "error": str(_fe)}
    try:
        ff_health.record("fisherman_harvest", rows=0, ok=False, error=str(_fe))
    except Exception:
        pass

# ================================================================
# v24.20 (Randy 2026-08-28): The Fisherman REGIONAL /area/<region>/feed/ RSS
# feeds. These carry actual tackle-shop + captain reports (not just the weekly
# video forecasts), 8-10 dated reports per feed per Monday, naming captains
# and specific spots (Coimbra Wreck, Habs Ledge, Ranger, etc). This is by far
# the highest-signal intel source in the whole harvester stack.
# ================================================================
try:
    regional_result = ff_fisherman.harvest_regional_reports(cutoff_days=7)
    print(f"Fisherman regional feeds — {regional_result['total_items']} recent reports")
    try:
        ff_health.record("fisherman_regional", rows=regional_result.get('total_items', 0), ok=True)
    except Exception:
        pass
    for f in regional_result["feeds"]:
        print(f"  {f['source_name']}: {f['count']} items")
    zone_map_reg = json.loads((BASE / "data" / "youtube_zone_map.json").read_text()).get("location_to_zones", {})
    ne_ids_reg = {z["id"] for z in zones_data.get("zones", []) if z.get("id")}
    regional_entries = ff_fisherman.promote_regional_reports_into_zones(
        regional_result, zone_map_reg, ne_ids_reg
    )
    if regional_entries:
        # Dedupe against existing bait_intel by (link) OR (date,source,zones)
        existing_links_reg = {b.get("link") for b in zones_data.get("bait_intel", []) if b.get("link")}
        existing_keys_reg = {(e.get("date"), e.get("source"), tuple(sorted(e.get("zones") or [])))
                             for e in zones_data.get("bait_intel", [])}
        n_reg = 0
        for e in regional_entries:
            if e.get("link") and e["link"] in existing_links_reg:
                continue
            key = (e["date"], e["source"], tuple(sorted(e["zones"])))
            if key in existing_keys_reg:
                continue
            zones_data.setdefault("bait_intel", []).append(e)
            existing_keys_reg.add(key)
            if e.get("link"):
                existing_links_reg.add(e["link"])
            n_reg += 1
        if n_reg:
            (BASE / "data" / "zones.json").write_text(json.dumps(zones_data, indent=2))
            print(f"  Fisherman regional (NE): {n_reg} entries promoted")
            _zjs = json.dumps(zones_data, indent=2).rstrip().rstrip(";")
            _old = zones_json_enriched.rstrip().rstrip(";")
            if _old in template:
                template = template.replace(_old, _zjs)
                zones_json_enriched = _zjs
                zones_json = json.dumps(zones_data, indent=2)
except Exception as _rre:
    import traceback as _tb
    _err_path = BASE / "cache" / "build_errors.log"
    try:
        _err_path.parent.mkdir(exist_ok=True)
        with open(_err_path, "a") as _ef:
            _ef.write(f"\n=== fisherman_regional failure @ {datetime.datetime.utcnow().isoformat()}Z ===\n")
            _tb.print_exc(file=_ef)
    except Exception:
        pass
    _tb.print_exc()
    print(f"  WARN: Fisherman regional-feeds failed ({_rre}) — proceeding without")
    try:
        ff_health.record("fisherman_regional", rows=0, ok=False, error=str(_rre))
    except Exception:
        pass

# ================================================================
# v24.11 (2026-08-27): Charter-fleet website scrapers.
# Randy: "How come we don't have any info from the charter fish captains that
# go out every day, and then they usually post on their website what they
# catch?" We DO have 26 YouTube channels — but charter fleets often post daily
# to their own websites and never touch YouTube. Two new sources tonight:
#   1. Viking Fleet fishing reports (Montauk, NE) — party-boat daily trips
#   2. Oregon Inlet Fishing Center (OBX, Mid-Atl) — WordPress RSS fleet aggregator
# Both auto-promote hits into their region's bait_intel with source_type="charter_auto".
# ================================================================
try:
    import charter_harvest as ff_charter
    vf_result = ff_charter.harvest_viking_fleet_fishing()
    oi_result = ff_charter.harvest_oregon_inlet()
    # v24.19 (Randy 2026-08-28): three additional NE sources.
    # 1. J&J Sports Fishing (Patchogue LI tackle-shop Shopify atom)
    # 2. OTW Northeast Offshore Report podcast RSS
    # 3. The Fisherman "Next Cast" podcast RSS (routes NE vs Mid-Atl by title)
    jj_result = ff_charter.harvest_jj_sports_fishing()
    otw_pod_result = ff_charter.harvest_otw_offshore_podcast()
    nc_result = ff_charter.harvest_nextcast_podcast()
    # v24.35 (Randy 2026-08-29): three Gulf-region harvesters wired.
    gd_result = ff_charter.harvest_great_days_nwfl()
    bw_result = ff_charter.harvest_blue_water_30a()
    nwfl_pod_result = ff_charter.harvest_nw_florida_podcast()
    # v24.43 (Randy 2026-08-31): two S. Florida-flavored harvesters — Florida
    # Sportsman magazine RSS (state-wide FL coverage) + Sport Fishing Magazine
    # (broad offshore/inshore, heavy S. FL). Both routed to south_florida.
    try:
        fs_result = ff_charter.harvest_florida_sportsman()
    except Exception as _e:
        print(f"  WARN: FL Sportsman harvest failed ({_e})")
        fs_result = {"raw_posts": [], "region": "south_florida", "source_name": "Florida Sportsman", "fetch_ok": False, "default_coords": [24.923, -80.622], "default_zones": ["alligator_reef"]}
    try:
        sfm_result = ff_charter.harvest_sport_fishing_mag()
    except Exception as _e:
        print(f"  WARN: Sport Fishing Mag harvest failed ({_e})")
        sfm_result = {"raw_posts": [], "region": "south_florida", "source_name": "Sport Fishing Magazine", "fetch_ok": False, "default_coords": [24.923, -80.622], "default_zones": ["alligator_reef"]}
    # v24.50 (Randy 2026-09-01) — two multi-region publications that route
    # each post to whichever region's zones match its location keywords.
    try:
        ca_result = ff_charter.harvest_coastal_angler()
    except Exception as _e:
        print(f"  WARN: Coastal Angler harvest failed ({_e})")
        ca_result = {"raw_posts": [], "region": "multi", "source_name": "Coastal Angler Magazine", "fetch_ok": False}
    try:
        sws_result = ff_charter.harvest_saltwater_sportsman()
    except Exception as _e:
        print(f"  WARN: Saltwater Sportsman harvest failed ({_e})")
        sws_result = {"raw_posts": [], "region": "multi", "source_name": "Saltwater Sportsman", "fetch_ok": False}
    # v24.55 (Randy 2026-08-31) — four dedicated regional harvesters, one per
    # underfed region. Each was probed at commit time and confirmed to return
    # working RSS. Together with YT auto-heat (also v24.55) these give the
    # cold regions their own captain-intel pipelines.
    try:
        la_sport_result = ff_charter.harvest_louisiana_sportsman()
    except Exception as _e:
        print(f"  WARN: Louisiana Sportsman harvest failed ({_e})")
        la_sport_result = {"raw_posts": [], "region": "gulf", "source_name": "Louisiana Sportsman", "fetch_ok": False}
    try:
        car_sport_result = ff_charter.harvest_carolina_sportsman()
    except Exception as _e:
        print(f"  WARN: Carolina Sportsman harvest failed ({_e})")
        car_sport_result = {"raw_posts": [], "region": "multi", "source_name": "Carolina Sportsman", "fetch_ok": False}
    try:
        cbm_result = ff_charter.harvest_chesapeake_bay_magazine()
    except Exception as _e:
        print(f"  WARN: Chesapeake Bay Magazine harvest failed ({_e})")
        cbm_result = {"raw_posts": [], "region": "midatl", "source_name": "Chesapeake Bay Magazine", "fetch_ok": False}
    try:
        ss_result = ff_charter.harvest_salt_strong()
    except Exception as _e:
        print(f"  WARN: Salt Strong harvest failed ({_e})")
        ss_result = {"raw_posts": [], "region": "multi", "source_name": "Salt Strong Fishing Club", "fetch_ok": False}
    # Split Next Cast into NE + Mid-Atl buckets
    nc_ma_items = nc_result.pop("_midatl_items", []) if isinstance(nc_result, dict) else []
    nc_ma_result = {
        "added": 0, "raw_posts": nc_ma_items, "fetch_ok": nc_result.get("fetch_ok", False),
        "region": "midatl", "source_name": "The Fisherman Next Cast podcast (Mid-Atl)",
        "default_coords": [39.1, -74.5], "default_zones": ["cape_may_rips", "delaware_bay_mouth"],
    }
    charter_harvests = [vf_result, oi_result, jj_result, otw_pod_result, nc_result, nc_ma_result,
                        gd_result, bw_result, nwfl_pod_result,
                        fs_result, sfm_result,  # v24.43: + FL Sportsman + Sport Fishing Mag
                        ca_result, sws_result,  # v24.50: + Coastal Angler + Saltwater Sportsman (multi-region)
                        la_sport_result, car_sport_result, cbm_result, ss_result]  # v24.55: 4 regional flagships
    # v24.105 — record charter harvest health. rows = total raw posts across
    # all 17 sub-harvesters; ok = at least one succeeded (partial success is
    # not a failure — captain intel is redundant across sources by design).
    try:
        _total_raw = sum(len(h.get("raw_posts", []) if isinstance(h, dict) else []) for h in charter_harvests)
        _any_ok = any(isinstance(h, dict) and h.get("fetch_ok", True) for h in charter_harvests)
        ff_health.record("charter_harvest", rows=_total_raw, ok=_any_ok,
                         error=None if _any_ok else "all 17 sub-harvesters failed")
    except Exception:
        pass
    _midatl_path2 = BASE / "data" / "zones_mid_atlantic.json"
    _gulf_path_ch = BASE / "data" / "zones_gulf.json"
    _sfl_path_ch = BASE / "data" / "zones_south_florida.json"
    _seatl_path_ch = BASE / "data" / "zones_south_atlantic.json"    # v24.55
    zone_map_path2 = BASE / "data" / "youtube_zone_map.json"
    zone_map2 = json.loads(zone_map_path2.read_text()).get("location_to_zones", {}) if zone_map_path2.exists() else {}
    ne_zone_ids2 = {z.get("id") for z in zones_data.get("zones", []) if z.get("id")}
    _ma_data_ch = json.loads(_midatl_path2.read_text()) if _midatl_path2.exists() else {"zones": []}
    ma_zone_ids2 = {z.get("id") for z in _ma_data_ch.get("zones", []) if z.get("id")}
    _g_data_ch = json.loads(_gulf_path_ch.read_text()) if _gulf_path_ch.exists() else {"zones": []}
    g_zone_ids2 = {z.get("id") for z in _g_data_ch.get("zones", []) if z.get("id")}
    _sfl_data_ch = json.loads(_sfl_path_ch.read_text()) if _sfl_path_ch.exists() else {"zones": []}
    sfl_zone_ids2 = {z.get("id") for z in _sfl_data_ch.get("zones", []) if z.get("id")}
    _seatl_data_ch = json.loads(_seatl_path_ch.read_text()) if _seatl_path_ch.exists() else {"zones": []}
    seatl_zone_ids2 = {z.get("id") for z in _seatl_data_ch.get("zones", []) if z.get("id")}
    ch_entries = ff_charter.promote_charter_reports_into_zones(
        charter_harvests, zone_map2,
        {"northeast": ne_zone_ids2, "midatl": ma_zone_ids2, "gulf": g_zone_ids2,
         "south_florida": sfl_zone_ids2, "south_atlantic": seatl_zone_ids2},
        cutoff_days=14,
    )
    # NE promotion (Viking Fleet)
    n_ne_ch = 0
    ne_bait_keys2 = {(e.get("date"), e.get("source"), tuple(sorted(e.get("zones") or [])))
                     for e in zones_data.get("bait_intel", [])}
    for e in ch_entries["northeast"]:
        key = (e["date"], e["source"], tuple(sorted(e["zones"])))
        if key in ne_bait_keys2:
            continue
        zones_data.setdefault("bait_intel", []).append(e)
        ne_bait_keys2.add(key)
        n_ne_ch += 1
    if n_ne_ch:
        (BASE / "data" / "zones.json").write_text(json.dumps(zones_data, indent=2))
        print(f"  Charter reports (NE): {n_ne_ch} entries promoted")
        zjs = json.dumps(zones_data, indent=2).rstrip().rstrip(";")
        old_blob = zones_json_enriched.rstrip().rstrip(";")
        if old_blob in template:
            template = template.replace(old_blob, zjs)
            zones_json_enriched = zjs
            zones_json = json.dumps(zones_data, indent=2)
    # Mid-Atl promotion (Oregon Inlet)
    if _midatl_path2.exists() and ch_entries["midatl"]:
        ma_bait_keys2 = {(e.get("date"), e.get("source"), tuple(sorted(e.get("zones") or [])))
                        for e in _ma_data_ch.get("bait_intel", [])}
        n_ma_ch = 0
        for e in ch_entries["midatl"]:
            key = (e["date"], e["source"], tuple(sorted(e["zones"])))
            if key in ma_bait_keys2:
                continue
            _ma_data_ch.setdefault("bait_intel", []).append(e)
            ma_bait_keys2.add(key)
            n_ma_ch += 1
        if n_ma_ch:
            _midatl_path2.write_text(json.dumps(_ma_data_ch, indent=2))
            print(f"  Charter reports (Mid-Atl): {n_ma_ch} entries promoted")
    # v24.35 Gulf promotion (Great Days NW-FL + Blue Water 30A + NW FL podcast)
    if _gulf_path_ch.exists() and ch_entries.get("gulf"):
        g_bait_keys2 = {(e.get("date"), e.get("source"), tuple(sorted(e.get("zones") or [])))
                        for e in _g_data_ch.get("bait_intel", [])}
        n_g_ch = 0
        for e in ch_entries["gulf"]:
            key = (e["date"], e["source"], tuple(sorted(e["zones"])))
            if key in g_bait_keys2:
                continue
            _g_data_ch.setdefault("bait_intel", []).append(e)
            g_bait_keys2.add(key)
            n_g_ch += 1
        if n_g_ch:
            _gulf_path_ch.write_text(json.dumps(_g_data_ch, indent=2))
            print(f"  Charter reports (Gulf): {n_g_ch} entries promoted")
    # v24.43 S. Florida promotion (FL Sportsman + Sport Fishing Magazine)
    if _sfl_path_ch.exists() and ch_entries.get("south_florida"):
        sfl_bait_keys = {(e.get("date"), e.get("source"), tuple(sorted(e.get("zones") or [])))
                         for e in _sfl_data_ch.get("bait_intel", [])}
        n_sfl_ch = 0
        for e in ch_entries["south_florida"]:
            key = (e["date"], e["source"], tuple(sorted(e["zones"])))
            if key in sfl_bait_keys:
                continue
            _sfl_data_ch.setdefault("bait_intel", []).append(e)
            sfl_bait_keys.add(key)
            n_sfl_ch += 1
        if n_sfl_ch:
            _sfl_path_ch.write_text(json.dumps(_sfl_data_ch, indent=2))
            print(f"  Charter reports (S. Florida): {n_sfl_ch} entries promoted")
    # v24.55 SE promotion (Carolina Sportsman multi-region)
    if _seatl_path_ch.exists() and ch_entries.get("south_atlantic"):
        seatl_bait_keys = {(e.get("date"), e.get("source"), tuple(sorted(e.get("zones") or [])))
                           for e in _seatl_data_ch.get("bait_intel", [])}
        n_seatl_ch = 0
        for e in ch_entries["south_atlantic"]:
            key = (e["date"], e["source"], tuple(sorted(e["zones"])))
            if key in seatl_bait_keys:
                continue
            _seatl_data_ch.setdefault("bait_intel", []).append(e)
            seatl_bait_keys.add(key)
            n_seatl_ch += 1
        if n_seatl_ch:
            _seatl_path_ch.write_text(json.dumps(_seatl_data_ch, indent=2))
            print(f"  Charter reports (SE): {n_seatl_ch} entries promoted")
except Exception as _ce:
    # v24.105 — full traceback + source_health so silent failures in this
    # 178-line mega-try can be diagnosed later (same fix as v24.104's
    # archive-save split, applied to the charter harvester bundle).
    import traceback as _tb
    _err_path = BASE / "cache" / "build_errors.log"
    try:
        _err_path.parent.mkdir(exist_ok=True)
        with open(_err_path, "a") as _ef:
            _ef.write(f"\n=== charter_harvest failure @ {datetime.datetime.utcnow().isoformat()}Z ===\n")
            _tb.print_exc(file=_ef)
    except Exception:
        pass
    _tb.print_exc()
    print(f"  WARN: Charter harvester failed ({_ce}) — proceeding without")
    try:
        ff_health.record("charter_harvest", rows=0, ok=False, error=str(_ce))
    except Exception:
        pass

# ================================================================
# v24.20 (Randy 2026-08-28): Reddit harvester — r/saltwaterfishing + r/stripedbass.
# Recreational-angler forum posts complement captain-video/podcast intel with
# amateur trip reports and observations ("Pogies are in thick", "Weekend
# Gameplan 8/28-8/30"). Filtered to posts that mention BOTH a NE location AND
# a species/action keyword, then promoted with source_type="reddit_auto".
# ================================================================
try:
    import reddit_harvest as ff_reddit
    reddit_result = ff_reddit.harvest(cutoff_days=7)
    print(f"Reddit harvest — {reddit_result['total_fetched']} posts fetched · "
          f"{len(reddit_result['northeast_posts'])} NE-eligible after filter")
    reddit_entries = ff_reddit.promote_into_zones(
        reddit_result, zones_data.get("bait_intel", [])
    )
    if reddit_entries:
        ne_bait_keys3 = {(e.get("date"), e.get("source"), tuple(sorted(e.get("zones") or [])))
                         for e in zones_data.get("bait_intel", [])}
        n_reddit = 0
        for e in reddit_entries:
            key = (e["date"], e["source"], tuple(sorted(e["zones"])))
            if key in ne_bait_keys3:
                continue
            zones_data.setdefault("bait_intel", []).append(e)
            ne_bait_keys3.add(key)
            n_reddit += 1
        if n_reddit:
            (BASE / "data" / "zones.json").write_text(json.dumps(zones_data, indent=2))
            print(f"  Reddit reports (NE): {n_reddit} entries promoted")
            _zjs = json.dumps(zones_data, indent=2).rstrip().rstrip(";")
            _old = zones_json_enriched.rstrip().rstrip(";")
            if _old in template:
                template = template.replace(_old, _zjs)
                zones_json_enriched = _zjs
                zones_json = json.dumps(zones_data, indent=2)
except Exception as _re:
    print(f"  WARN: Reddit harvester failed ({_re}) — proceeding without")


# v24.76 (Randy 2026-09-03): iNaturalist cetacean sighting harvester for
# Gulf + S. Florida + SE Coast. Adds Randy's "find the whales, find the tuna"
# signal to the three southern regions using public cetacean observations.
try:
    print("Harvesting iNaturalist cetacean sightings (Gulf + S. Florida + SE)...")
    for _region_key, _fname, _bbox in ff_inat_whales.REGIONS:
        try:
            ff_inat_whales.process(_region_key, _fname, _bbox)
        except Exception as _e:
            print(f"  {_region_key} FAIL: {_e}")
except Exception as _ie:
    print(f"  WARN: iNat harvest wrapper failed: {_ie}")

# v24.95 (Randy 2026-09-04): iNaturalist BAIT-fish harvester — companion to
# the cetacean harvester above. Fetches photo-verified observations of
# menhaden, mullet, silversides, anchovies, mackerel, ballyhoo, bumper
# in every region's bbox, attributes each to the nearest zone within 30nm,
# adds as bait_intel entries. Feeds the model's baitBoost signal directly.
try:
    print("Harvesting iNaturalist BAIT-fish observations across all 5 regions...")
    ff_inat_bait.run(verbose=True)
except Exception as _ibe:
    print(f"  WARN: iNat bait harvest failed: {_ibe}")

derived_signals = {
    "computed_at": datetime.datetime.utcnow().isoformat() + "Z",
    "sst_gradient": sst_gradient_field,
    "chla_gradient": chla_gradient_field,
    "current_convergence": current_convergence,
    "persistent_breaks": persistent_breaks,
    "pressure_state": pressure_state,
    "youtube_intel": youtube_intel,
}
derived_json = json.dumps(derived_signals)
assert "DERIVED_SIGNALS_PLACEHOLDER" in template, "Derived signals placeholder not found"
template = template.replace("DERIVED_SIGNALS_PLACEHOLDER", derived_json)

# Save today's snapshot to the archive BEFORE embedding history, so today's
# state is included in the history the map shows.
print("Writing archive snapshot for today...")
weather_summary = {}
if noaa_snapshot.get("periods"):
    p = noaa_snapshot["periods"][0]
    weather_summary["wind"] = f"{p.get('windSpeed','?')} {p.get('windDirection','?')}"
    weather_summary["forecast"] = p.get("shortForecast", "")
if marine_snapshot.get("days"):
    weather_summary["waves_ft"] = marine_snapshot["days"][0].get("wave_ft")
pressure_summary = None
if pressure_snapshot.get("samples"):
    ps = pressure_snapshot["samples"]
    pressure_summary = {
        "current_hpa": ps[-1]["hpa"],
        "samples_count": len(ps)
    }
# v24.104 — split the mega-try so a raw_intel_bundle failure doesn't
# masquerade as "Archive save failed" and swallow the actual save step.
# Each stage now catches its own exception + logs full traceback.
zones_dict = None
raw_intel_bundle = None
_regions_bundle = None
_region_contours = None

try:
    zones_dict = json.loads(zones_json)
except Exception as e:
    import traceback as _tb
    print(f"  🚨 Parsing zones_json failed ({e})", file=sys.stderr)
    print(_tb.format_exc(), file=sys.stderr)

try:
    # Raw source-data preservation bundle — Randy 2026-08-08 directive.
    # Everything the harvesters saw today, in raw form, so a future weight
    # tuning pass can look back at exactly what was available. Filters and
    # signals are recomputed downstream; source data is not.
    raw_intel_bundle = {
        "whale_harvest": {
            "fetch_ok": whale_harvest_result.get("fetch_ok"),
            "html_bytes": whale_harvest_result.get("html_bytes"),
            "added_today": whale_harvest_result.get("added"),
            "raw_posts": whale_harvest_result.get("raw_posts", []),
            "parsed_sightings": whale_harvest_result.get("parsed_sightings", []),
        },
        "youtube_full_corpus": {
            # Per-channel raw entry lists — every video the RSS returned in
            # the cutoff window, matched or not. Trimmed at the harvester.
            "channels": [
                {
                    "id": c.get("id"),
                    "name": c.get("name"),
                    "tier": c.get("tier"),
                    "area": c.get("area"),
                    "ok": c.get("ok"),
                    "raw_count": c.get("raw_count", 0),
                    "kept_count": c.get("kept_count", 0),
                    "all_videos": c.get("all_videos_in_window", []),
                }
                for c in (youtube_intel.get("channels", []) or [])
            ],
        },
    }
except Exception as e:
    import traceback as _tb
    print(f"  🚨 raw_intel_bundle build failed ({e})", file=sys.stderr)
    print(_tb.format_exc(), file=sys.stderr)
    raw_intel_bundle = {}  # empty so save_snapshot can still run

try:
    # v24.56 (Randy 2026-08-31): per-region snapshot bundle for accuracy
    # scoring. Load the other 4 regions' zones + zone weather and hand them
    # to save_snapshot so it records their picks + look_ahead too.
    _regions_bundle = {}
    for _rkey, _rzones_path, _rzone_weather in [
        ("mid_atlantic",   BASE / "data" / "zones_mid_atlantic.json",   zone_weather_midatl_json),
        ("gulf",           BASE / "data" / "zones_gulf.json",           zone_weather_gulf_json),
        ("south_florida",  BASE / "data" / "zones_south_florida.json",  zone_weather_sofl_json),
        ("south_atlantic", BASE / "data" / "zones_south_atlantic.json", zone_weather_seatl_json),
    ]:
        try:
            if not _rzones_path.exists():
                continue
            _rzd = json.loads(_rzones_path.read_text())
            _rzw = None
            if _rzone_weather and _rzone_weather != "null":
                try: _rzw = json.loads(_rzone_weather)
                except Exception: _rzw = None
            _regions_bundle[_rkey] = {"zones_data": _rzd, "zone_weather": _rzw}
        except Exception as _re:
            print(f"  WARN: could not load {_rkey} for per-region snapshot ({_re})")

    # v24.57 (Randy 2026-08-31): per-region contours archived alongside
    # NE's so persistenceBoost can fire for every region. Payload is small
    # (each region ~5-15 KB total for SST + chla).
    _region_contours = {
        "gulf": {
            "sst":  sst_contours_gulf.get("contours", {}),
            "chla": chla_snapshot_gulf.get("contours", {}),
        },
        "south_florida": {
            "sst":  sst_contours_sofl.get("contours", {}),
            "chla": chla_snapshot_sofl.get("contours", {}),
        },
        "south_atlantic": {
            "sst":  sst_contours_seatl.get("contours", {}),
            "chla": chla_snapshot_seatl.get("contours", {}),
        },
    }
except Exception as e:
    import traceback as _tb
    print(f"  🚨 regions_bundle/region_contours build failed ({e})", file=sys.stderr)
    print(_tb.format_exc(), file=sys.stderr)
    if _regions_bundle is None: _regions_bundle = {}
    if _region_contours is None: _region_contours = {}

# The actual save_snapshot call — now in its own try so a failure here
# is unambiguously about the archive step, not any upstream bundle build.
try:
    if zones_dict is None:
        raise RuntimeError("zones_dict is None — cannot save snapshot")
    snap = ff_archive.save_snapshot(
        zones_dict,
        weather=weather_summary,
        pressure=pressure_summary,
        notes="auto-written by build-inlined.py",
        sst_contours=sst_contours.get("contours", {}),
        chla_contours=chla_snapshot.get("contours", {}),
        derived=derived_signals,
        # 2026-07-29: pass zone_weather so archive can persist the 7-Day
        # Look-Ahead. Enables First Mate's accuracy scoring once 14+ days
        # have accumulated (target: 2026-08-12).
        zone_weather=zone_weather,
        # 2026-08-08: preservation bundle — see raw_intel_bundle above.
        raw_intel=raw_intel_bundle,
        # v24.56: per-region picks + look_ahead → enables per-region accuracy
        regions_bundle=_regions_bundle,
        # v24.57: per-region contours → enables per-region persistenceBoost
        region_contours=_region_contours,
    )
    print(f"  Saved snapshot: archive/{snap['date']}.json")
    # v24.104 — record success in source_health so the happy path shows up
    # as fresh (not unknown) on the dashboard.
    try:
        ff_health.record("archive_save", rows=1, ok=True)
    except Exception:
        pass
    # Report preservation growth so ops can see accumulation live.
    _yt_raw = sum(c.get("raw_count", 0) for c in raw_intel_bundle["youtube_full_corpus"]["channels"])
    _yt_kept = sum(c.get("kept_count", 0) for c in raw_intel_bundle["youtube_full_corpus"]["channels"])
    print(f"  Preserved raw intel: {_yt_raw} yt videos ({_yt_kept} matched filter), "
          f"{len(raw_intel_bundle['whale_harvest']['raw_posts'])} whale cards")
except Exception as e:
    # v24.104 — no more silent archive failures. This except was swallowing
    # the reason nightlies stopped writing archives Aug 31 → Sep 4 without
    # any alert. Full traceback goes to stderr + a persisted error log +
    # source_health so all three surfaces catch it next morning.
    import traceback as _tb
    tb_text = _tb.format_exc()
    print(f"  🚨 Archive save FAILED ({e})", file=sys.stderr)
    print(tb_text, file=sys.stderr)
    try:
        _err_log = BASE / "cache" / "build_errors.log"
        _err_log.parent.mkdir(parents=True, exist_ok=True)
        with _err_log.open("a") as _f:
            _f.write(f"\n\n=== {datetime.datetime.utcnow().isoformat()}Z: archive save failed ===\n")
            _f.write(tb_text)
    except Exception:
        pass
    try:
        ff_health.record("archive_save", rows=0, ok=False, error=str(e))
    except Exception:
        pass
    # Deliberately don't re-raise: the site should still deploy today (the
    # user shouldn't lose the map because the model can't remember). But the
    # nightly-health check (v24.103) will now catch a broken archive on the
    # bootstrap next morning and page Randy.

# ---- Data-freshness audit (Randy 2026-07-30 directive) --------------------
# Every signal the effectiveHeat model reads gets checked for staleness. If
# anything is stale, the summary line printed here plus the archive record
# make it impossible for a stale signal to hide silently. The Captain + First
# Mate now start every session by reading this from the latest snapshot.
print("Running data-freshness audit...")
try:
    audit = ff_audit.run_audit(
        zones_data=zones_dict,
        latest_snapshot=snap,
        verbose=True,
    )
    # Bake into snapshot so history carries the audit trail
    snap_path = ff_archive.ARCHIVE_DIR / f"{snap['date']}.json"
    existing = json.loads(snap_path.read_text())
    existing["freshness_audit"] = audit
    snap_path.write_text(json.dumps(existing, indent=2))
except Exception as e:
    print(f"  WARN: freshness audit failed ({e})")

# v24.94 — backfill any missing seasonal_presence calendars before the model
# ranks anything. Catches the "new zone added without seasonal_presence"
# regression that killed 79 zones' pick eligibility (fixed retroactively in
# v24.93). This runs FIRST so all downstream scoring uses complete data.
try:
    print("Backfilling missing species-seasonal calendars...")
    _added, _unknown = ff_calendars.backfill_seasonal_presence([
        BASE / "data" / "zones.json",
        BASE / "data" / "zones_mid_atlantic.json",
        BASE / "data" / "zones_south_atlantic.json",
        BASE / "data" / "zones_gulf.json",
        BASE / "data" / "zones_south_florida.json",
    ])
    print(f"  species-calendars added: {_added}")
    if _unknown:
        print(f"  WARN: no calendar for species: {sorted(_unknown)} (add to species_calendars.py)")
except Exception as e:
    print(f"  WARN: seasonal_presence backfill failed ({e})")

# v24.91 — run the prediction accuracy scorer against the archive so the
# dashboard's accuracy row has fresh numbers.  Post-v24.88 look-ahead fix,
# this reading becomes meaningful signal (before the fix every day predicted
# the same zone so accuracy scored 100% trivially).
try:
    print("Scoring prediction accuracy against archive...")
    ff_accuracy.run(verbose=False)
except Exception as e:
    print(f"  WARN: accuracy scorer failed ({e})")

# v24.96 — picks-vs-signal audit. Computes tomorrow's top 5 per region +
# flags "hollow" picks (high effective_heat but low corroboration). If a
# future zone-add or signal change introduces a hollow top pick, this
# audit catches it before Randy sees it. Writes data/picks_audit.json.
# v24.102 — archive integrity check runs right after the snapshot is written,
# so today's snapshot is included in the check. Result is baked into the
# dashboard + written to data/archive_integrity.json for preflight.
try:
    _ai = ff_archint.audit()
    _st = _ai["status"]
    _emoji = {"healthy": "✅", "degraded": "⚠", "critical": "🚨"}[_st]
    print(f"{_emoji} Archive integrity: {_st.upper()} · latest {_ai['stats'].get('latest')} · "
          f"{_ai['stats'].get('total_snapshots', 0)} snapshots · "
          f"{_ai['stats'].get('gaps_last_14d', 0)} gaps in last 14d")
    for _w in _ai["warnings"]: print(f"  ⚠  {_w}")
    for _e in _ai["errors"]:   print(f"  🚨 {_e}")
    (BASE / "data" / "archive_integrity.json").write_text(json.dumps(_ai, indent=2))
except Exception as _aie:
    print(f"  WARN: archive integrity check failed ({_aie})")

# v24.100 — source-health rollup snapshot after all harvesters have run.
# Just calling summary() reads what the harvesters already recorded.
try:
    _sh_summary = ff_health.summary()
    _sh_dead = [n for n, i in _sh_summary["sources"].items() if i["status"] == "dead"]
    _sh_stale = [n for n, i in _sh_summary["sources"].items() if i["status"] == "stale"]
    print(f"Source health: {_sh_summary['total_known'] - _sh_summary['flags']}/{_sh_summary['total_known']} fresh")
    if _sh_dead:  print(f"  🚨 DEAD sources ({len(_sh_dead)}): {', '.join(_sh_dead)}")
    if _sh_stale: print(f"  ⚠  STALE sources ({len(_sh_stale)}): {', '.join(_sh_stale)}")
except Exception as _sh_e:
    print(f"  WARN: source health rollup failed ({_sh_e})")

try:
    print("Running picks-vs-signal audit...")
    _pa = ff_picks_audit.audit(verbose=False)
    print(f"  hollow picks in top-5 across all regions: {_pa['total_hollow']}")
    if _pa['total_hollow'] > 0:
        for hp in _pa['hollow_picks']:
            print(f"    🚨 {hp['region']} · {hp['zone_id']} · eh={hp['effective_heat']:.2f} · corrob={hp['corroboration']}/5")
except Exception as e:
    print(f"  WARN: picks audit failed ({e})")

# v24.130 (Randy 2026-09-15) — per-region pick backup audit. The Captain rule
# "every top-5 pick must be backed by verifiable data or flagged as provisional"
# requires this to run every nightly. Complements picks_audit.py (which flags
# high-heat hollow picks) by also flagging LOW-heat top picks with 0 intel
# sources — the failure mode Randy called out for SE/Gulf/S.FL where every
# pick is heat=5 baseline with nothing behind it.
try:
    print("Running per-region pick backup audit...")
    import subprocess as _sp
    _pb = _sp.run(
        ["python3", str(BASE / "pick_backup_audit.py")],
        capture_output=True, text=True, timeout=60,
    )
    print((_pb.stdout or "").strip())
    if _pb.returncode != 0:
        # exit 1 means at least one region audited RED — flag but don't abort
        # the build (the deploy path has its own gate)
        print(f"  ⚠ pick_backup_audit reported RED region(s) — see data/pick_audit.json")
except Exception as e:
    print(f"  WARN: pick backup audit failed ({e})")

# v24.91 — rebuild the Zone Health Dashboard artifact each nightly build.
# Reads all 5 region files + prediction_accuracy.json, injects fresh state
# into zone-health-dashboard.html. Randy has the artifact bookmarked;
# it auto-refreshes to today's snapshot on each build.
try:
    print("Rebuilding Zone Health Dashboard...")
    dstate = ff_dashboard.rebuild(verbose=True)
    print(f"  dashboard: {len(dstate['regions'])} regions, {dstate['archive_days']} archive days")
except Exception as e:
    print(f"  WARN: dashboard rebuild failed ({e})")

# Load up to 30 days of history for embedding in the HTML.
print("Loading history for embedded trends...")
history = ff_archive.load_history(days=30)
history_json = json.dumps(history)
print(f"  Loaded {len(history)} days of history ({len(history_json):,} bytes)")

assert "HISTORY_SNAPSHOT_PLACEHOLDER" in template, "History snapshot placeholder not found"
template = template.replace("HISTORY_SNAPSHOT_PLACEHOLDER", history_json)

# Randy 2026-08-07: bake the build date into the "Updated ..." header line
# so it advances every time we rebuild. The old code read from zones.json's
# `generated` field which was hardcoded and never advanced. Now the header
# always reflects the actual build date. Two substitution sites in the
# template — the .meta div's initial text and the JS constant.
build_date = datetime.date.today().isoformat()
assert "BUILD_DATE_PLACEHOLDER" in template, "Build date placeholder not found"
n_replaced = template.count("BUILD_DATE_PLACEHOLDER")
template = template.replace("BUILD_DATE_PLACEHOLDER", build_date)
print(f"  Baked build date: {build_date} ({n_replaced} substitution site{'s' if n_replaced != 1 else ''})")

# Also update zones.json's `generated` field so the fallback path shows the
# right date too. This keeps zones.json honest — no more July-22 in the file.
# Randy 2026-08-27 fix: the bump used to happen AFTER template.replace() had
# already embedded the OLD zones_json, so the deployed HTML kept the stale
# generated date even after zones.json was updated. Now we bump zones.json
# to disk AND rewrite the embedded ZONE_DATA blob inside the template so
# both agree — that's what safe-deploy.sh's post-deploy verify checks against.
try:
    _zones_data = json.loads(zones_json)
    if _zones_data.get("generated") != build_date:
        _zones_data["generated"] = build_date
        (BASE / "data" / "zones.json").write_text(json.dumps(_zones_data, indent=2))
        print(f"  Bumped zones.json.generated to {build_date}")
        # Rewrite the embedded blob so the built HTML matches.
        _updated_blob = json.dumps(_zones_data, indent=2).rstrip().rstrip(";")
        _old_blob = zones_json_enriched.rstrip().rstrip(";")
        if _old_blob in template:
            template = template.replace(_old_blob, _updated_blob)
            zones_json_enriched = _updated_blob
            zones_json = json.dumps(_zones_data, indent=2)
            print(f"  Re-embedded ZONE_DATA with fresh generated date")
        else:
            print(f"  WARN: could not re-embed ZONE_DATA (blob mismatch — investigate)")
except Exception as _e:
    print(f"  WARN: could not update zones.json.generated ({_e})")

# Same rebump for Mid-Atlantic zones (v24)
try:
    _midatl_path = BASE / "data" / "zones_mid_atlantic.json"
    if _midatl_path.exists():
        _midatl_data = json.loads(_midatl_path.read_text())
        if _midatl_data.get("generated") != build_date:
            _midatl_data["generated"] = build_date
            _midatl_path.write_text(json.dumps(_midatl_data, indent=2))
            print(f"  Bumped zones_mid_atlantic.json.generated to {build_date}")
            # Rewrite the embedded Mid-Atl blob in the template. The Mid-Atl
            # blob lives under ZONE_DATA_MIDATL_PLACEHOLDER, which by now has
            # been substituted with the pre-bump Mid-Atl JSON. Locate + replace.
            _midatl_updated = json.dumps(_midatl_data, indent=2).rstrip().rstrip(";")
            # We don't have a stored `midatl_json_enriched` — reconstruct via
            # a targeted search for the generated field within the ZONE_DATA_MIDATL
            # region. Cheaper approach: search the template for the OLD generated
            # date pattern inside the Mid-Atl blob (the file has two `generated`
            # fields, one per region, so replace only the second one).
            import re as _re
            _matches = list(_re.finditer(r'"generated":\s*"(\d{4}-\d{2}-\d{2})"', template))
            if len(_matches) >= 2:
                # Second match = Mid-Atl (NE first, Mid-Atl second in region-blob order)
                _second = _matches[1]
                _old_ma_date = _second.group(1)
                if _old_ma_date != build_date:
                    template = template[:_second.start()] + f'"generated": "{build_date}"' + template[_second.end():]
                    print(f"  Re-embedded Mid-Atl generated ({_old_ma_date} → {build_date})")
except Exception as _e:
    print(f"  WARN: could not update Mid-Atl generated ({_e})")

# v24.29 (2026-08-29): same rebump for Gulf zones (third region blob)
try:
    _gulf_path_bump = BASE / "data" / "zones_gulf.json"
    if _gulf_path_bump.exists():
        _gulf_data_bump = json.loads(_gulf_path_bump.read_text())
        if _gulf_data_bump.get("generated") != build_date:
            _gulf_data_bump["generated"] = build_date
            _gulf_path_bump.write_text(json.dumps(_gulf_data_bump, indent=2))
            print(f"  Bumped zones_gulf.json.generated to {build_date}")
            # Re-embed by finding the 3rd generated field in the template (NE, Mid-Atl, Gulf order)
            import re as _re2
            _matches3 = list(_re2.finditer(r'"generated":\s*"(\d{4}-\d{2}-\d{2})"', template))
            if len(_matches3) >= 3:
                _third = _matches3[2]
                _old_g_date = _third.group(1)
                if _old_g_date != build_date:
                    template = template[:_third.start()] + f'"generated": "{build_date}"' + template[_third.end():]
                    print(f"  Re-embedded Gulf generated ({_old_g_date} → {build_date})")
except Exception as _e:
    print(f"  WARN: could not update Gulf generated ({_e})")

# v24.42 (2026-08-30): same rebump for S. Florida zones (fourth region blob)
try:
    _sofl_path_bump = BASE / "data" / "zones_south_florida.json"
    if _sofl_path_bump.exists():
        _sofl_data_bump = json.loads(_sofl_path_bump.read_text())
        if _sofl_data_bump.get("generated") != build_date:
            _sofl_data_bump["generated"] = build_date
            _sofl_path_bump.write_text(json.dumps(_sofl_data_bump, indent=2))
            print(f"  Bumped zones_south_florida.json.generated to {build_date}")
            # Re-embed by finding the 4th generated field in the template (NE, Mid-Atl, Gulf, S.Fla order)
            import re as _re3
            _matches4 = list(_re3.finditer(r'"generated":\s*"(\d{4}-\d{2}-\d{2})"', template))
            if len(_matches4) >= 4:
                _fourth = _matches4[3]
                _old_s_date = _fourth.group(1)
                if _old_s_date != build_date:
                    template = template[:_fourth.start()] + f'"generated": "{build_date}"' + template[_fourth.end():]
                    print(f"  Re-embedded S. Florida generated ({_old_s_date} → {build_date})")
except Exception as _e:
    print(f"  WARN: could not update S. Florida generated ({_e})")

# v24.45 (2026-08-31): same rebump for South Atlantic zones (fifth region blob).
# Order in the template is REGION_BLOBS declaration order: NE, Mid-Atl,
# S.Atlantic, Gulf, S.Florida. So S.Atlantic is index 2 (third generated field).
# But the file-injection order above (NE→MidAtl→Gulf→SFla→SAtl) doesn't match
# REGION_BLOBS declaration order — the ZONE_DATA_*_PLACEHOLDER replacement runs
# in the ORDER of the `template.replace` calls, so S.Atl JSON lands last. Search
# specifically for the S.Atl region_id to find its generated field.
try:
    _seatl_path_bump = BASE / "data" / "zones_south_atlantic.json"
    if _seatl_path_bump.exists():
        _seatl_data_bump = json.loads(_seatl_path_bump.read_text())
        if _seatl_data_bump.get("generated") != build_date:
            _seatl_data_bump["generated"] = build_date
            _seatl_path_bump.write_text(json.dumps(_seatl_data_bump, indent=2))
            print(f"  Bumped zones_south_atlantic.json.generated to {build_date}")
            # Find the generated field within the south_atlantic blob specifically.
            # Search for the region_id marker, then find the "generated" field near it.
            import re as _re4
            _marker = template.find('"region_id": "south_atlantic"')
            if _marker > 0:
                # Look BACKWARDS from the marker for the enclosing "generated" field
                # (S.Atlantic zones file has "generated" appearing shortly before "region_id")
                window_start = max(0, _marker - 500)
                window = template[window_start:_marker + 100]
                m = _re4.search(r'"generated":\s*"(\d{4}-\d{2}-\d{2})"', window)
                if m:
                    _old_sa_date = m.group(1)
                    if _old_sa_date != build_date:
                        # Replace only within the window
                        abs_start = window_start + m.start()
                        abs_end = window_start + m.end()
                        template = template[:abs_start] + f'"generated": "{build_date}"' + template[abs_end:]
                        print(f"  Re-embedded SE generated ({_old_sa_date} → {build_date})")
except Exception as _e:
    print(f"  WARN: could not update SE generated ({_e})")

out = BASE / "map" / "fish-finder.html"
out.write_text(template)
print(f"Wrote {out} ({len(template):,} bytes)")
print(f"Zones: {len(json.loads(zones_json)['zones'])}")

# Randy 2026-08-07: SITE HEALTH AUDIT + AUTO-FIX LOOP.
#
# After every build we run site_health_audit.py across 6 categories. If it
# finds ERRORs, we try to auto-fix them via audit_auto_fix.py, then re-audit.
# Up to 2 fix passes before we give up and exit with code 2 (deploy blocker).
#
# The auto-fix handler only touches KNOWN MECHANICAL fixes (rerun a stale
# harvester, reseed a missing timestamp, retry a transient fetch). It never
# fabricates data. Anything requiring judgment (missing captain intel, new
# code bug, layout regression) is passed through as "needs_human_attention"
# and the exit code stays 2 so the deploy gate blocks + Randy is alerted.

def _run_audit():
    """Return the audit subprocess result (stdout, exit_code)."""
    import subprocess as _sp
    try:
        _r = _sp.run(
            ["python3", str(BASE / "site_health_audit.py")],
            capture_output=True, text=True, timeout=180,
        )
        return _r.stdout, _r.returncode
    except _sp.TimeoutExpired:
        return "(audit timed out)", 3

def _run_auto_fix():
    """Return the auto-fix subprocess result (stdout, exit_code)."""
    import subprocess as _sp
    try:
        _r = _sp.run(
            ["python3", str(BASE / "audit_auto_fix.py")],
            capture_output=True, text=True, timeout=300,
        )
        return _r.stdout, _r.returncode
    except _sp.TimeoutExpired:
        return "(auto-fix timed out)", 3

import os as _os
_inside_recursion = _os.environ.get("FF_AUDIT_INSIDE_BUILD") == "1"

# v24.26 (2026-08-29): rebuild the Cowork artifact tile from today's archive
# so the persistent gallery tile stays in sync with the nightly build. The
# tile itself is republished by whoever runs the Artifact tool with the URL
# saved in tile_artifact_url.txt — this build step just regenerates the HTML.
print("\nRebuilding Cowork artifact tile from today's snapshot...")
try:
    import subprocess as _sp
    # v24.105 — add 60s timeout. Was unbounded, so a hang in build_tile.py
    # would hang the entire nightly build indefinitely.
    _tile_res = _sp.run(
        ["python3", str(BASE / "build_tile.py")],
        capture_output=True, text=True, timeout=60,
    )
    print((_tile_res.stdout or "") + (_tile_res.stderr or ""))
except _sp.TimeoutExpired:
    print("⚠️  tile rebuild timed out after 60s (non-fatal — build continues)")
except Exception as _tile_e:
    print(f"⚠️  tile rebuild failed (non-fatal): {_tile_e}")

print("\nRunning site health audit...")
_audit_out, _audit_exit = _run_audit()
print(_audit_out)

if _audit_exit == 0:
    print("✅ Site health audit passed on first try. No auto-fix needed.")
elif _inside_recursion:
    # We're already inside a recursive rebuild triggered by auto-fix.
    # DO NOT try to auto-fix again — that would loop. Just exit with code 2.
    print("❌ Audit still failing inside recursive rebuild — halting.")
    import sys as _sys
    _sys.exit(2)
else:
    # Try auto-fix up to 2 passes
    for _pass in range(1, 3):
        print(f"\n🔧 Auto-fix pass {_pass}/2 — attempting mechanical fixes...")
        _fix_out, _fix_exit = _run_auto_fix()
        print(_fix_out)
        print("\n🔁 Re-running audit after fix pass...")
        _audit_out, _audit_exit = _run_audit()
        print(_audit_out)
        if _audit_exit == 0:
            print(f"✅ Audit clean after {_pass} auto-fix pass(es).")
            break
    else:
        # Fell through — still failing after all passes
        print("❌ SITE HEALTH AUDIT STILL FAILING AFTER AUTO-FIX ATTEMPTS.")
        print("   The remaining findings need human attention (code fix or")
        print("   fresh captain intel). Trigger's Claude session may attempt")
        print("   code fixes; otherwise Randy is alerted in the summary.")
        import sys as _sys
        _sys.exit(2)
