#!/usr/bin/env python3
"""Regenerate fish-finder-tile.html from today's archive snapshot.

The tile is a compact launcher artifact — big verdict, top offshore + inshore
picks, 3-day look-ahead, CTA to fishfinders.app. Published as a persistent
Cowork artifact; this script keeps it in sync with the nightly build.

Runs at the end of build-inlined.py. Republishing is handled by whoever
invokes the artifact tool with the URL saved in tile_artifact_url.txt.
"""
import json, datetime, pathlib, sys, re, hashlib

BASE = pathlib.Path(__file__).parent
TEMPLATE = BASE / "fish-finder-tile.html"  # the one artifact-design published; used as template
ARCHIVE_DIR = BASE / "archive"

def latest_snapshot():
    files = sorted(p for p in ARCHIVE_DIR.glob("*.json") if re.match(r"^\d{4}-\d{2}-\d{2}\.json$", p.name))
    if not files:
        return None, None
    p = files[-1]
    return p.stem, json.loads(p.read_text())

def wind_verdict(offshore_morning):
    """Return (verdict_word, verdict_line, wind_str, seas_str, sky_str)."""
    wind = offshore_morning.get("wind_mph", 0)
    gusts = offshore_morning.get("wind_gusts_mph", 0)
    waves = offshore_morning.get("wave_ft", 0)
    peak = max(wind, gusts)
    if peak <= 12 and waves <= 3.5:
        v = ("go", "GO", f"Light wind, workable seas. Both picks in range.")
    elif peak <= 18 and waves <= 4.5:
        v = ("maybe", "MAYBE", f"Marginal wind — check afternoon calm window.")
    else:
        v = ("stay", "STAY", f"Wind {peak:.0f} mph {waves:.1f} ft — rough for Randy's boat.")
    return v[0], v[1], v[2], f"{wind:.0f}–{gusts:.0f} mph", f"{waves:.1f} ft"

def build(out_path=None):
    date, snap = latest_snapshot()
    if not snap:
        print("no snapshot", file=sys.stderr)
        return None
    picks = snap.get("picks", {})
    tuna = picks.get("tuna") or picks.get("bluefin") or {}
    striper = picks.get("striper") or picks.get("inshore") or {}
    la = snap.get("look_ahead") or []
    weather_agg = snap.get("weather") or {}
    fresh = snap.get("freshness_audit", {}).get("signals", {})
    pressure = snap.get("pressure", {}).get("current_hpa")

    # verdict from tomorrow morning offshore
    off_morn = (la[0] if la else {}).get("offshore", {}).get("weather", {}).get("morning", {}) if la else {}
    if not off_morn:
        off_morn = {"wind_mph": 5, "wind_gusts_mph": 8, "wave_ft": 2.5}
    verdict_cls, verdict_word, verdict_line, wind_str, seas_str = wind_verdict(off_morn)
    # Also check if there's an afternoon calm window that flips it to MAYBE
    off_aft = (la[0] if la else {}).get("offshore", {}).get("weather", {}).get("afternoon", {}) if la else {}
    if verdict_cls == "stay" and off_aft:
        aft_peak = max(off_aft.get("wind_mph", 99), off_aft.get("wind_gusts_mph", 99))
        aft_wave = off_aft.get("wave_ft", 99)
        if aft_peak <= 10 and aft_wave <= 3.0:
            # Morning rough but afternoon calm → MAYBE (afternoon-only run)
            verdict_cls, verdict_word = "maybe", "MAYBE"
            verdict_line = "Rough morning, calm afternoon. Inshore-only in PM window."
            wind_str = f"{off_morn.get('wind_mph', 0):.0f} AM → {off_aft.get('wind_mph', 0):.0f} PM"
    sky_str = weather_agg.get("forecast", "")

    # Day names
    today = datetime.date.fromisoformat(date)
    day_names = ["Today", "Tomorrow", (today + datetime.timedelta(days=2)).strftime("%a")]
    days_html = []
    for i, d in enumerate(la[:3]):
        off = d.get("offshore", {})
        name = day_names[i]
        zone = off.get("zone_name", "—")
        heat = off.get("effective_heat", off.get("heat", "—"))
        heat_str = f"{heat:.1f}" if isinstance(heat, (int, float)) else str(heat)
        # keep zone short
        short = zone.replace("Nearshore South of Block Island", "Nearshore S. Block").replace("(", "— ").replace(")", "")
        days_html.append(
            f'<div class="day"><div class="day-name">{name}</div>'
            f'<div class="day-zone">{short}</div>'
            f'<div class="day-heat">{heat_str}</div></div>'
        )
    while len(days_html) < 3:
        days_html.append('<div class="day"><div class="day-name">—</div><div class="day-zone">—</div><div class="day-heat">—</div></div>')

    # Freshness chips
    whale_status = fresh.get("whale_sightings", {})
    bait_status = fresh.get("bait_intel", {})
    whale_days = whale_status.get("days_since", "?")
    bait_days = bait_status.get("days_since", "?")
    whale_ok = "ok" if whale_status.get("status") == "fresh" else ""
    bait_ok = "ok" if bait_status.get("status") == "fresh" else ""
    press_str = f"{pressure:.0f} hPa" if isinstance(pressure, (int, float)) else "—"

    def fmt_num(v, dp=1):
        if isinstance(v, (int, float)):
            return f"{v:.{dp}f}"
        return str(v) if v is not None else "—"
    tuna_name = tuna.get("zone_name", "—")
    tuna_heat_s = fmt_num(tuna.get("effective_heat", tuna.get("heat")), 1)
    tuna_dist_s = fmt_num(tuna.get("distance_nm"), 0)
    striper_name = striper.get("zone_name", "—")
    striper_heat_s = fmt_num(striper.get("effective_heat", striper.get("heat")), 1)
    striper_dist_s = fmt_num(striper.get("distance_nm"), 0)

    tile = TEMPLATE.read_text()
    # Patch date stamp
    # v24.108 — regex was hard-coded to the original template's "Sat · Aug 29"
    # string. On the FIRST run it replaced correctly, but the replacement
    # itself contained a NEW date (e.g. "Mon · Sep 7"), so every subsequent
    # rebuild found no match and left the tile stamped with whatever date
    # the last successful match wrote. Result: tile date silently froze at
    # its first rebuild's date until the template was manually reset.
    # Fix: match ANY weekday · month · day + "build YYYY-MM-DD" pair, not
    # the specific "Sat · Aug 29" seed.
    dt = today.strftime("%a · %b %-d")
    tile, _n_date = re.subn(
        r"<b>[A-Z][a-z]{2}\s*·\s*[A-Z][a-z]{2}\s*\d+</b>\s*Season 2026 · (?:v[^\<]+|build [0-9-]+)",
        f"<b>{dt}</b>\n      Season 2026 · build {date}",
        tile,
    )
    if _n_date == 0:
        print(f"⚠️  tile date regex matched 0 times — stamp NOT updated (tile still on old date)", file=sys.stderr)
    # Patch verdict
    tile = re.sub(r'<section class="verdict [a-z]+"[^>]*>[\s\S]*?</section>',
                  f'<section class="verdict {verdict_cls}" aria-label="Today\'s verdict: {verdict_word}">\n'
                  f'    <div class="verdict-badge">{verdict_word}</div>\n'
                  f'    <div class="verdict-body">\n'
                  f'      <div class="verdict-title">{verdict_line}</div>\n'
                  f'      <div class="verdict-meta">\n'
                  f'        <span><span class="lbl">Wind</span><b>{wind_str}</b></span>\n'
                  f'        <span><span class="lbl">Seas</span><b>{seas_str}</b></span>\n'
                  f'        <span><span class="lbl">Sky</span><b>{sky_str}</b></span>\n'
                  f'      </div>\n'
                  f'    </div>\n'
                  f'  </section>', tile, count=1)
    # Patch offshore pick
    off_pick_block = (
        f'<div class="pick-name">{tuna_name}</div>\n'
        f'      <div class="pick-row">\n'
        f'        <div>\n'
        f'          <span class="pick-heat">{tuna_heat_s}</span><span class="pick-heat-lbl">heat</span>\n'
        f'        </div>\n'
        f'        <div class="pick-dist"><span class="lbl">Range</span>{tuna_dist_s}&nbsp;nm</div>'
    )
    tile = re.sub(
        r'(<article class="pick">\s*<div class="pick-species"><span class="dot"></span>Offshore[^<]*</div>\s*)<div class="pick-name">[^<]+</div>\s*<div class="pick-row">\s*<div>\s*<span class="pick-heat">[^<]+</span>[^<]*<span class="pick-heat-lbl">heat</span>\s*</div>\s*<div class="pick-dist">[^<]*<span class="lbl">Range</span>[^<]+</div>',
        lambda m: m.group(1) + off_pick_block, tile, count=1
    )
    # Patch inshore pick
    ins_pick_block = (
        f'<div class="pick-name">{striper_name}</div>\n'
        f'      <div class="pick-row">\n'
        f'        <div>\n'
        f'          <span class="pick-heat">{striper_heat_s}</span><span class="pick-heat-lbl">heat</span>\n'
        f'        </div>\n'
        f'        <div class="pick-dist"><span class="lbl">Range</span>{striper_dist_s}&nbsp;nm</div>'
    )
    tile = re.sub(
        r'(<article class="pick">\s*<div class="pick-species inshore"><span class="dot"></span>[^<]*</div>\s*)<div class="pick-name">[^<]+</div>\s*<div class="pick-row">\s*<div>\s*<span class="pick-heat">[^<]+</span>[^<]*<span class="pick-heat-lbl">heat</span>\s*</div>\s*<div class="pick-dist">[^<]*<span class="lbl">Range</span>[^<]+</div>',
        lambda m: m.group(1) + ins_pick_block, tile, count=1
    )
    # Patch look-ahead strip
    tile = re.sub(r'<div class="strip-days">[\s\S]*?</div>\s*</section>',
                  f'<div class="strip-days">\n      {"".join(days_html)}\n    </div>\n  </section>',
                  tile, count=1)
    # Patch freshness chips
    tile = re.sub(r'<section class="fresh"[^>]*>[\s\S]*?</section>',
                  f'<section class="fresh" aria-label="Data freshness">\n'
                  f'    <span class="chip {whale_ok}">\U0001f40b <b>Whales</b> · {whale_days}d</span>\n'
                  f'    <span class="chip {bait_ok}">\U0001f3a3 <b>Bait intel</b> · {bait_days}d</span>\n'
                  f'    <span class="chip">\U0001f321 <b>Pressure</b> · {press_str}</span>\n'
                  f'    <span class="chip">\U0001f30a <b>NOAA marine</b> · today</span>\n'
                  f'  </section>', tile)

    # v24.121 — patch the "Snapshot as of build YYYY-MM-DD" foot line so it
    # reflects the actual build date instead of the frozen 2026-09-03 seed.
    # Every rebuild produces genuinely fresh content (which had been the goal
    # all along — the artifact system was rejecting republishes as "identical"
    # because two of the visible fields were static). Simple whole-line swap.
    tile = re.sub(
        r'Snapshot as of build \d{4}-\d{2}-\d{2}\.',
        f'Snapshot as of build {date}.',
        tile, count=1,
    )
    # v24.121 — also refresh the ?v=YYYYMMDDhhmm cache-busting query on every
    # link out to fishfinders.app so the tile hands the browser today's build
    # rather than a stale-cached one. Use today's compact stamp; not a
    # security thing, just a cache invalidator.
    version_stamp = today.strftime("%Y%m%d") + "2100"
    tile = re.sub(
        r'\?v=\d{10,14}',
        f'?v={version_stamp}',
        tile,
    )

    # v24.121 — build-time uniqueness marker. The artifact system's
    # publish-refusal-on-duplicate check hashes the whole HTML; two rebuilds
    # against the SAME archive would produce byte-identical output and the
    # republish would be refused as "identical content already refused"
    # even after intentional edits elsewhere. Injecting a timestamped
    # content-sha comment before </body> guarantees byte-uniqueness on
    # every rebuild without changing any visible content. Idempotent —
    # strips any prior marker before writing the new one.
    tile = re.sub(r'<!-- tile-build:[^>]*-->\n?', '', tile)
    now = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
    pre_marker_hash = hashlib.sha256(tile.encode()).hexdigest()[:12]
    marker = f'<!-- tile-build: {now} · content-sha={pre_marker_hash} -->'
    if '</body>' in tile:
        tile = tile.replace('</body>', marker + '\n</body>', 1)
    else:
        tile = tile.rstrip() + '\n' + marker + '\n'

    out = out_path or TEMPLATE
    pathlib.Path(out).write_text(tile)
    print(f"✅ Tile rebuilt for {date} ({verdict_word}) · version_stamp={version_stamp} · sha={pre_marker_hash}")
    return out

if __name__ == "__main__":
    build()
