#!/usr/bin/env python3
"""Fish Finder — Site Health Audit.

Randy 2026-08-07: "Devise a way to audit everything that we have involved
here and make sure there's no mistakes. If people start looking at this and
it's not correct, it's just gonna die."

This script runs SIX categories of checks after every build:

    1. DATA INTEGRITY   — zones.json schema, coord ranges, heat ranges,
                          date parseability, species references, etc.
    2. FRESHNESS        — extends data_freshness_audit for signal ages,
                          plus per-zone heat_updated tracking.
    3. COHERENCE        — hardcoded strings match reality (the class of
                          bug that let "Updated 2026-07-22" ship for 15
                          days). Build date in header matches today.
    4. LAYOUT/JS        — Playwright headless load: no JS errors, tabs
                          render, critical UI elements present, no
                          horizontal overflow on mobile viewport.
    5. PREDICTIONS      — top pick has non-zero confidence, pick isn't
                          identical to the trivial fallback, effective
                          heat spread across zones is reasonable.
    6. LIVE SITE        — fishfinders.app returns 200, content matches
                          what we deployed (build date matches).

Every finding has a severity:
    ERROR    — ship-blocker. Build should NOT deploy.
    WARN     — noticeable issue. Deploy but flag prominently.
    NOTE     — informational. No action needed.

Output:
    - JSON report saved to archive as `site_health_audit` key
    - Human-readable summary printed to stdout
    - Exit code 1 if any ERROR-severity findings
    - Baked into archive so the trend is queryable

Usage:
    python3 site_health_audit.py                   # audit current build
    python3 site_health_audit.py --require-clean   # exit 1 on any WARN too
    python3 site_health_audit.py --check-live      # also curl fishfinders.app
"""
import json
import re
import sys
import datetime
import pathlib
import subprocess
import shutil
import argparse

BASE = pathlib.Path("/root/fish-finder")
HTML_PATH = BASE / "map" / "fish-finder.html"
ZONES_PATH = BASE / "data" / "zones.json"
ARCHIVE_DIR = BASE / "archive"
LIVE_URL = "https://fishfinders.app"


class Findings:
    """Collects findings across all audit categories."""

    def __init__(self):
        self.by_category = {}
        self.errors = 0
        self.warns = 0
        self.notes = 0

    def add(self, category, severity, code, message, detail=None):
        assert severity in ("ERROR", "WARN", "NOTE"), severity
        self.by_category.setdefault(category, [])
        self.by_category[category].append({
            "severity": severity,
            "code": code,
            "message": message,
            "detail": detail,
        })
        if severity == "ERROR":
            self.errors += 1
        elif severity == "WARN":
            self.warns += 1
        else:
            self.notes += 1

    def category_status(self, cat):
        entries = self.by_category.get(cat, [])
        if any(e["severity"] == "ERROR" for e in entries):
            return "ERROR"
        if any(e["severity"] == "WARN" for e in entries):
            return "WARN"
        if entries:
            return "NOTE"
        return "OK"

    def to_dict(self):
        return {
            "totals": {"errors": self.errors, "warns": self.warns, "notes": self.notes},
            "categories": {
                cat: {
                    "status": self.category_status(cat),
                    "findings": self.by_category.get(cat, []),
                }
                for cat in [
                    "data_integrity", "freshness", "coherence",
                    "layout_js", "predictions", "live_site",
                ]
            },
        }


# =============== CATEGORY 1: DATA INTEGRITY ===============

def check_data_integrity(f, zones_data):
    """zones.json schema, coord ranges, heat ranges, date parseability."""
    zones = zones_data.get("zones", [])
    if not zones:
        f.add("data_integrity", "ERROR", "no_zones",
              "zones.json contains no zones — the model has nothing to score")
        return
    # Required fields on every zone
    required = ["id", "name", "region", "category", "center", "heat", "species"]
    for z in zones:
        for field in required:
            if field not in z:
                f.add("data_integrity", "ERROR", "missing_field",
                      f"Zone {z.get('id','?')} missing required field '{field}'")

        # Coordinate sanity — Northeast US bounds
        c = z.get("center", [0, 0])
        if not (isinstance(c, list) and len(c) == 2):
            f.add("data_integrity", "ERROR", "bad_center",
                  f"Zone {z.get('id','?')} center is not [lat, lon]")
        else:
            lat, lon = c
            if not (35 <= lat <= 45):
                f.add("data_integrity", "ERROR", "lat_out_of_range",
                      f"Zone {z.get('id','?')} lat {lat} outside NE US (35-45)")
            if not (-76 <= lon <= -65):
                f.add("data_integrity", "ERROR", "lon_out_of_range",
                      f"Zone {z.get('id','?')} lon {lon} outside NE US (-76 to -65)")

        # Heat range
        h = z.get("heat", -1)
        if not (0 <= h <= 10):
            f.add("data_integrity", "ERROR", "heat_out_of_range",
                  f"Zone {z.get('id','?')} heat={h} outside 0-10")

        # heat_updated: parseable + not in future
        hu = z.get("heat_updated")
        if hu:
            try:
                d = datetime.date.fromisoformat(hu)
                if d > datetime.date.today():
                    f.add("data_integrity", "ERROR", "date_in_future",
                          f"Zone {z.get('id','?')} heat_updated={hu} is in the future")
            except ValueError:
                f.add("data_integrity", "ERROR", "date_unparseable",
                      f"Zone {z.get('id','?')} heat_updated={hu!r} is not YYYY-MM-DD")
        else:
            f.add("data_integrity", "WARN", "no_heat_updated",
                  f"Zone {z.get('id','?')} has no heat_updated — cannot audit staleness")

        # Notes present for high-heat zones
        if h >= 6 and not (z.get("notes") or "").strip():
            f.add("data_integrity", "WARN", "empty_notes",
                  f"Zone {z.get('id','?')} heat={h} has empty notes")

        # Species must be non-empty list
        sp = z.get("species", [])
        if not sp:
            f.add("data_integrity", "ERROR", "no_species",
                  f"Zone {z.get('id','?')} has no species — will never be picked")

    # Duplicate ID check
    ids = [z.get("id") for z in zones]
    dupes = [i for i in set(ids) if ids.count(i) > 1]
    for d in dupes:
        f.add("data_integrity", "ERROR", "duplicate_id",
              f"Duplicate zone id '{d}' appears {ids.count(d)} times")


# =============== CATEGORY 2: FRESHNESS ===============

def check_freshness(f, latest_snapshot, today):
    """Reuse the data_freshness_audit signals output; flag anything dead."""
    fa = latest_snapshot.get("freshness_audit") or {}
    signals = fa.get("signals", {})
    if not signals:
        f.add("freshness", "WARN", "no_freshness_audit",
              "Today's snapshot has no freshness_audit block")
        return

    for name, s in signals.items():
        status = s.get("status")
        if status == "dead":
            # bird_intel is a documented placeholder — downgrade to NOTE
            if name == "bird_intel":
                f.add("freshness", "NOTE", "bird_intel_placeholder",
                      "bird_intel is a documented placeholder (no live source)")
            else:
                f.add("freshness", "WARN", "signal_dead",
                      f"Signal '{name}' is DEAD",
                      detail=s)
        elif status == "stale":
            f.add("freshness", "NOTE", "signal_stale",
                  f"Signal '{name}' is stale",
                  detail=s)

    # live_heat digs deeper: how many zones stale/dead
    lh = signals.get("live_heat", {})
    if lh.get("zones_dead_over_21d", 0) > 0:
        f.add("freshness", "NOTE", "zones_dead_captain_intel",
              f"{lh['zones_dead_over_21d']} zones have captain heat > 21 days old (confidence-discounted in ranking)")


# =============== CATEGORY 3: COHERENCE ===============

def check_coherence(f, html, zones_data, today):
    """The class of bug that let 'Updated 2026-07-22' ship for 15 days.
    Hardcoded strings in the HTML must match reality."""

    # Build date in header must match today (either substituted or fallback)
    m = re.search(r'metaInfo">Season 2026 · Updated (\d{4}-\d{2}-\d{2})', html)
    if not m:
        f.add("coherence", "ERROR", "no_build_date_in_header",
              "Header 'Updated YYYY-MM-DD' is missing from built HTML")
    else:
        header_date = m.group(1)
        if header_date != today.isoformat():
            f.add("coherence", "ERROR", "stale_build_date_header",
                  f"Header says 'Updated {header_date}' but today is {today.isoformat()} — build-date substitution failed")

    # Same check for the JS constant _buildDate
    m = re.search(r'_buildDate = "(\d{4}-\d{2}-\d{2})"', html)
    if m and m.group(1) != today.isoformat():
        f.add("coherence", "ERROR", "stale_build_date_js",
              f"JS _buildDate constant is {m.group(1)} but today is {today.isoformat()}")

    # zones.json generated field
    gen = zones_data.get("generated")
    if gen and gen != today.isoformat():
        f.add("coherence", "WARN", "stale_zones_generated",
              f"zones.json.generated={gen} doesn't match today ({today.isoformat()})")

    # Any leftover BUILD_ or _PLACEHOLDER strings in the HTML = failed substitution
    for placeholder in ["BUILD_DATE_PLACEHOLDER", "ZONE_DATA_PLACEHOLDER",
                        "NOAA_SNAPSHOT_PLACEHOLDER", "MARINE_SNAPSHOT_PLACEHOLDER",
                        "SST_CONTOURS_PLACEHOLDER", "TIDE_SNAPSHOT_PLACEHOLDER",
                        "PRESSURE_SNAPSHOT_PLACEHOLDER", "CHLA_SNAPSHOT_PLACEHOLDER",
                        "CURRENTS_SNAPSHOT_PLACEHOLDER", "ZONE_WEATHER_PLACEHOLDER",
                        "DERIVED_SIGNALS_PLACEHOLDER", "HISTORY_SNAPSHOT_PLACEHOLDER"]:
        # skip the two allowed literal mentions inside the _buildDate fallback code
        matches = [i for i in range(len(html)) if html.startswith(placeholder, i)]
        # Filter out mentions inside string literals used by the fallback JS
        real = [i for i in matches
                if html[max(0, i-30):i].count('"') % 2 == 0
                and 'includes("PLACEHOLDER")' not in html[i-40:i+40]]
        if real:
            f.add("coherence", "ERROR", "unsubstituted_placeholder",
                  f"'{placeholder}' still literal in built HTML — build script didn't substitute")

    # Zone count in HTML matches zones.json
    m = re.search(r'"zones":\s*\[', html)
    if m:
        n_zones = len(zones_data.get("zones", []))
        # Look for the phrase "42 spots" or "N spots" or similar
        m2 = re.search(r'(\d+)\+?\s*spots\b', html)
        if m2:
            claimed = int(m2.group(1))
            if abs(claimed - n_zones) > 5:  # allow "42+" slack
                f.add("coherence", "WARN", "spot_count_drift",
                      f"HTML claims '{claimed}+ spots' but zones.json has {n_zones}")


# =============== CATEGORY 4: LAYOUT / JS ===============

def check_layout_js(f, html_path):
    """Playwright headless: load the page, catch JS errors, verify tabs render."""
    try:
        which_node = shutil.which("node")
        if not which_node:
            f.add("layout_js", "NOTE", "no_node",
                  "Node.js not available — skipped playwright checks")
            return
        # Check playwright + chromium
        pw_probe = subprocess.run(
            ["node", "-e", "try{require.resolve('playwright');console.log('OK')}catch(e){console.log('MISS')}"],
            capture_output=True, text=True, timeout=10,
        )
        if "OK" not in pw_probe.stdout:
            f.add("layout_js", "NOTE", "no_playwright",
                  "Playwright not installed — skipped browser checks")
            return
        chromium = "/opt/pw-browsers/chromium"
        if not pathlib.Path(chromium).exists():
            f.add("layout_js", "NOTE", "no_chromium",
                  f"Chromium not at {chromium} — skipped browser checks")
            return
    except Exception as e:
        f.add("layout_js", "NOTE", "browser_probe_failed", str(e))
        return

    # v24.54 (Randy 2026-09-01) — audit now runs the desktop drawer + pick sweep
    # against EVERY region, not just Northeast default. Randy: "I don't have the
    # ability to look over all this new stuff we're rolling out where I was able
    # to look at the Northeast and have context." Region-specific breakage (a
    # missing species key in S. Florida, a broken zone_id in Gulf, a species
    # picker crash in SE) previously would have gone undetected because the
    # audit only loaded the app in its NE default state. Now it loads once
    # per region via ?r=<region> URL param.
    audit_js = r"""
const { chromium, devices } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: '/opt/pw-browsers/chromium' });
  const results = { errors: [], consoleErrors: [], desktop: {}, mobile: {}, byRegion: {} };
  const REGIONS = ['northeast', 'midatl', 'south_atlantic', 'gulf', 'south_florida'];
  const DRAWER_CHIPS = ['sevenday', 'catches', 'plan', 'youtube', 'fleet', 'about', 'personalize'];
  try {
    // Desktop — legacy top-level fields keep the NE view (first region) so the
    // existing coherence + pick-card checks below keep working unchanged.
    const ctx1 = await browser.newContext({ viewport: { width: 1400, height: 900 } });
    const p1 = await ctx1.newPage();
    p1.on('pageerror', e => results.errors.push('desktop: ' + e.message));
    p1.on('console', m => { if (m.type() === 'error') results.consoleErrors.push('desktop: ' + m.text().slice(0,200)); });

    for (const region of REGIONS) {
      // Fresh page per region — full navigation with ?r=<region> so _pickRegion()
      // picks it up. Clear localStorage between loads so cached region doesn't
      // stick and confuse the next iteration.
      await p1.evaluate(() => { try { localStorage.clear(); } catch(e){} });
      const url = 'file://__HTML_PATH__?r=' + region;
      await p1.goto(url, { waitUntil: 'load', timeout: 30000 });
      await p1.waitForTimeout(2500);
      const regionInfo = await p1.evaluate(() => {
        const reportBody = document.getElementById('reportBody');
        const reportLen = reportBody ? (reportBody.innerText||'').length : 0;
        return {
          activeRegion: (typeof ACTIVE_REGION !== "undefined") ? ACTIVE_REGION : null,
          zoneCount: (typeof ZONES !== "undefined" && Array.isArray(ZONES)) ? ZONES.length : 0,
          reportBodyLen: reportLen,
          hasPickCard: !!document.querySelector('.pick-card'),
          pickCount: document.querySelectorAll('.pick-card').length,
          hasHeatBadge: !!document.querySelector('.heat-badge-big'),
          headerText: (document.querySelector('#metaInfo')||{}).textContent||'',
        };
      });
      regionInfo.drawers = {};
      for (const name of DRAWER_CHIPS) {
        try {
          await p1.evaluate((n) => {
            if (typeof openLandingDrawer === "function") openLandingDrawer(n);
          }, name);
          await p1.waitForTimeout(200);
          const info = await p1.evaluate(() => {
            const body = document.getElementById('landingDrawerBody');
            const txt = body ? (body.innerText || '') : '';
            return {
              len: txt.length,
              hasFallback: /not rendered yet|isn't in the current view/i.test(txt),
              snippet: txt.slice(0, 120),
            };
          });
          regionInfo.drawers[name] = info;
          await p1.evaluate(() => { if (typeof closeLandingDrawer === "function") closeLandingDrawer(); });
          await p1.waitForTimeout(80);
        } catch (e) {
          regionInfo.drawers[name] = { error: String(e).slice(0, 200) };
        }
      }
      results.byRegion[region] = regionInfo;
      // First region (NE) also populates the legacy top-level `desktop` fields
      // so existing checks (pickCount, reportBody length, tabCount) keep working.
      if (region === 'northeast') {
        const legacy = await p1.evaluate(() => {
          const tabs = Array.from(document.querySelectorAll('.tab')).map(t => (t.textContent||'').trim());
          return { tabCount: tabs.length, tabs };
        });
        results.desktop = { ...regionInfo, ...legacy };
      }
    }

    // Mobile — check for horizontal overflow (Randy's "half the page" bug class)
    const ctx2 = await browser.newContext({ ...devices['iPhone 14'] });
    const p2 = await ctx2.newPage();
    p2.on('pageerror', e => results.errors.push('mobile: ' + e.message));
    p2.on('console', m => { if (m.type() === 'error') results.consoleErrors.push('mobile: ' + m.text().slice(0,200)); });
    await p2.goto('file://__HTML_PATH__', { waitUntil: 'load', timeout: 30000 });
    await p2.waitForTimeout(2500);
    results.mobile = await p2.evaluate(() => ({
      viewport: window.innerWidth,
      body_scroll: document.body.scrollWidth,
      overflows: document.body.scrollWidth > window.innerWidth + 2,
    }));
  } catch (e) {
    results.errors.push('setup: ' + e.message);
  } finally {
    await browser.close();
    console.log(JSON.stringify(results));
  }
})();
""".replace("__HTML_PATH__", str(html_path).replace("'", ""))

    tmp_js = pathlib.Path("/tmp/ff_site_audit_probe.js")
    tmp_js.write_text(audit_js)
    try:
        # v24.54: bumped from 90 → 240 seconds because we now iterate the desktop
        # probe across 5 regions (roughly 35s each: 2.5s page load + 7 × 280ms
        # per drawer). Plus the mobile check. Fits comfortably under 240s in
        # steady state.
        proc = subprocess.run(
            ["node", str(tmp_js)],
            capture_output=True, text=True, timeout=240,
        )
        # The last non-empty line should be JSON
        lines = [l for l in proc.stdout.strip().split("\n") if l.strip().startswith("{")]
        if not lines:
            f.add("layout_js", "WARN", "browser_no_output",
                  "Playwright probe produced no JSON output",
                  detail={"stdout": proc.stdout[:500], "stderr": proc.stderr[:500]})
            return
        res = json.loads(lines[-1])
    except subprocess.TimeoutExpired:
        f.add("layout_js", "WARN", "browser_timeout", "Playwright probe timed out (>240s)")
        return
    except Exception as e:
        f.add("layout_js", "WARN", "browser_run_failed", str(e))
        return

    for err in res.get("errors", []):
        f.add("layout_js", "ERROR", "js_pageerror", err)
    for cerr in res.get("consoleErrors", []):
        f.add("layout_js", "WARN", "console_error", cerr)

    d = res.get("desktop", {})
    if d.get("tabCount", 0) < 2:
        f.add("layout_js", "ERROR", "tabs_missing",
              f"Only {d.get('tabCount', 0)} tabs found — expected 2 (Today's Report + The Map)")
    if d.get("reportBodyLen", 0) < 5000:
        f.add("layout_js", "ERROR", "report_body_empty_or_tiny",
              f"reportBody has only {d.get('reportBodyLen', 0)} chars — report didn't render")
    if not d.get("hasPickCard"):
        f.add("layout_js", "ERROR", "no_pick_card",
              "No .pick-card element rendered — Tomorrow's Pick section is broken")
    if d.get("pickCount", 0) < 1:
        f.add("layout_js", "ERROR", "no_pick_cards_rendered",
              "No pick cards found in the report")

    m = res.get("mobile", {})
    if m.get("overflows"):
        f.add("layout_js", "WARN", "mobile_horizontal_overflow",
              f"Mobile viewport {m.get('viewport')}px overflows to {m.get('body_scroll')}px — content clips off right edge")

    # v24.54 (Randy 2026-09-01) — per-region drawer + pick sweep. Iterates
    # over every region the app supports (northeast, midatl, south_atlantic,
    # gulf, south_florida) and verifies drawers, pick cards, and reportBody
    # render properly for EACH. Randy: "I don't have the ability to look
    # over all this new stuff... it's a lot on you now because I can't
    # oversee all of it." Region-specific breakage now surfaces automatically.
    MIN_DRAWER_LEN = 60
    by_region = res.get("byRegion") or {}
    for region_name, region_info in by_region.items():
        if not isinstance(region_info, dict):
            continue
        # Region-level sanity: pick card, report body populated, zones loaded
        if region_info.get("zoneCount", 0) < 1:
            f.add("layout_js", "ERROR", "region_no_zones",
                  f"Region '{region_name}' loaded with zero ZONES — data blob likely missing")
        if region_info.get("reportBodyLen", 0) < 500:
            f.add("layout_js", "ERROR", "region_report_body_empty",
                  f"Region '{region_name}': reportBody has only {region_info.get('reportBodyLen', 0)} chars — renderReport() failed for this region")
        if not region_info.get("hasPickCard"):
            f.add("layout_js", "ERROR", "region_no_pick_card",
                  f"Region '{region_name}': no pick-card element rendered — this region's Tomorrow's Pick section is broken")
        # Per-region drawer sweep
        drawers = region_info.get("drawers") or {}
        for name, info in drawers.items():
            if not isinstance(info, dict):
                continue
            if info.get("error"):
                f.add("layout_js", "WARN", "drawer_probe_error",
                      f"[{region_name}] Drawer '{name}' probe raised: {info['error']}")
                continue
            length = info.get("len", 0)
            if info.get("hasFallback"):
                f.add("layout_js", "ERROR", "drawer_fallback_rendered",
                      f"[{region_name}] Drawer '{name}' rendered a stub/fallback ('not rendered yet' / 'isn't in the current view'): '{info.get('snippet','')[:80]}'")
            elif length < MIN_DRAWER_LEN:
                f.add("layout_js", "ERROR", "drawer_empty",
                      f"[{region_name}] Drawer '{name}' rendered only {length} chars — likely empty or broken. Snippet: '{info.get('snippet','')[:80]}'")


# =============== CATEGORY 5: PREDICTIONS ===============

def check_predictions(f, latest_snapshot, zones_data):
    """Top pick must have non-zero confidence + real data behind it."""
    picks = latest_snapshot.get("picks", {})
    if not picks:
        f.add("predictions", "ERROR", "no_picks",
              "Today's snapshot has no picks section")
        return

    # Build zone lookup
    zone_by_id = {z["id"]: z for z in zones_data.get("zones", [])}
    today = datetime.date.today()

    def confidence(zone):
        hu = zone.get("heat_updated")
        if not hu:
            return 0
        try:
            days = (today - datetime.date.fromisoformat(hu)).days
        except Exception:
            return 0
        if days <= 10: return 1.0
        if days <= 21: return 0.5
        if days <= 45: return 0.15
        return 0

    for kind in ("tuna", "striper"):
        pick = picks.get(kind, {})
        zid = pick.get("zone_id")
        if not zid:
            f.add("predictions", "WARN", "no_pick_for_kind",
                  f"No {kind} pick in today's snapshot")
            continue
        zone = zone_by_id.get(zid)
        if not zone:
            f.add("predictions", "ERROR", "pick_zone_not_in_zones",
                  f"{kind} pick zone_id '{zid}' not found in zones.json")
            continue
        conf = confidence(zone)
        if conf == 0:
            f.add("predictions", "WARN", "pick_confidence_zero",
                  f"{kind} pick '{pick.get('zone_name')}' has ZERO captain-data confidence")
        elif conf <= 0.15:
            f.add("predictions", "NOTE", "pick_confidence_low",
                  f"{kind} pick '{pick.get('zone_name')}' has low confidence ({conf:.2f})")

    # Are all zone effective_heats identical (indicates broken math)?
    zs = latest_snapshot.get("zones_state", {})
    if isinstance(zs, dict) and len(zs) > 5:
        ehs = [v.get("effective_heat", 0) for v in zs.values()]
        if ehs and max(ehs) - min(ehs) < 0.1:
            f.add("predictions", "ERROR", "picks_all_tied",
                  f"All {len(ehs)} zones have effective_heat within 0.1 — model is broken")


# =============== CATEGORY 6: LIVE SITE ===============

def check_live_site(f, today):
    """Optional: curl fishfinders.app and verify it returns the current build.
    v24.105 — 2-attempt retry so a single Cloudflare edge blip doesn't
    generate a spurious 'live unreachable' flag."""
    import urllib.request, time as _time
    body = None
    last_err = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(LIVE_URL + "/map/", headers={"User-Agent": "ff-audit/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                if r.status != 200:
                    f.add("live_site", "ERROR", "live_bad_status",
                          f"fishfinders.app/map/ returned HTTP {r.status}")
                    return
                body = r.read().decode("utf-8", errors="ignore")
                break
        except Exception as e:
            last_err = e
            if attempt < 1:
                _time.sleep(3)
    if body is None:
        f.add("live_site", "NOTE", "live_unreachable",
              f"Couldn't reach fishfinders.app from this environment: {last_err}")
        return

    # Header date check on the live version
    m = re.search(r'metaInfo">Season 2026 · Updated (\d{4}-\d{2}-\d{2})', body)
    if not m:
        f.add("live_site", "ERROR", "live_no_build_date",
              "Live site is missing the 'Updated YYYY-MM-DD' header")
    else:
        live_date = m.group(1)
        days_behind = (today - datetime.date.fromisoformat(live_date)).days
        if days_behind > 2:
            f.add("live_site", "ERROR", "live_stale_build",
                  f"Live site build date is {live_date} — {days_behind} days behind today. Nightly deploy may have failed.")
        elif days_behind > 0:
            f.add("live_site", "NOTE", "live_one_build_behind",
                  f"Live site is 1 day behind — will catch up on tonight's deploy")

    if len(body) < 500_000:
        f.add("live_site", "WARN", "live_body_small",
              f"Live /map/ HTML is only {len(body):,} bytes — expected >500KB. Possible truncation.")


# =============== MAIN ===============

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-clean", action="store_true",
                        help="Exit 1 on any WARN (not just ERROR)")
    parser.add_argument("--check-live", action="store_true",
                        help="Also curl fishfinders.app")
    parser.add_argument("--skip-browser", action="store_true",
                        help="Skip playwright layout checks (faster)")
    args = parser.parse_args()

    today = datetime.date.today()
    f = Findings()

    # Load inputs
    try:
        zones_data = json.loads(ZONES_PATH.read_text())
    except Exception as e:
        f.add("data_integrity", "ERROR", "zones_json_unreadable", str(e))
        _emit(f, today)
        sys.exit(1)

    try:
        html = HTML_PATH.read_text()
    except Exception as e:
        f.add("coherence", "ERROR", "html_unreadable", str(e))
        html = ""

    # Latest snapshot
    try:
        arch_files = sorted(ARCHIVE_DIR.glob("2*.json"))
        latest_snapshot = json.loads(arch_files[-1].read_text()) if arch_files else {}
    except Exception as e:
        f.add("freshness", "WARN", "no_snapshot", str(e))
        latest_snapshot = {}

    # Run all audits
    check_data_integrity(f, zones_data)
    check_freshness(f, latest_snapshot, today)
    if html:
        check_coherence(f, html, zones_data, today)
    if not args.skip_browser:
        check_layout_js(f, HTML_PATH)
    check_predictions(f, latest_snapshot, zones_data)
    if args.check_live:
        check_live_site(f, today)

    _emit(f, today)

    # Bake into today's archive snapshot
    if arch_files:
        try:
            snap_path = arch_files[-1]
            existing = json.loads(snap_path.read_text())
            existing["site_health_audit"] = {
                "generated_at": today.isoformat(),
                **f.to_dict(),
            }
            snap_path.write_text(json.dumps(existing, indent=2))
        except Exception as e:
            print(f"  WARN: couldn't bake audit into archive ({e})")

    # Exit code — ERROR always fails, WARN fails only with --require-clean
    if f.errors > 0:
        sys.exit(1)
    if args.require_clean and f.warns > 0:
        sys.exit(1)
    sys.exit(0)


def _emit(f, today):
    """Human-readable summary to stdout."""
    total = f.errors + f.warns + f.notes
    print(f"\n{'='*60}")
    print(f"SITE HEALTH AUDIT — {today.isoformat()}")
    print(f"{'='*60}")
    print(f"❌ {f.errors} error{'s' if f.errors != 1 else ''}   "
          f"⚠  {f.warns} warning{'s' if f.warns != 1 else ''}   "
          f"ℹ  {f.notes} note{'s' if f.notes != 1 else ''}")
    print()
    labels = {
        "data_integrity": "1. DATA INTEGRITY",
        "freshness":      "2. FRESHNESS      ",
        "coherence":      "3. COHERENCE      ",
        "layout_js":      "4. LAYOUT / JS    ",
        "predictions":    "5. PREDICTIONS    ",
        "live_site":      "6. LIVE SITE      ",
    }
    emoji = {"OK": "✅", "NOTE": "ℹ ", "WARN": "🟡", "ERROR": "🔴"}
    for cat, label in labels.items():
        status = f.category_status(cat)
        print(f"  {emoji[status]} {label} {status}")
        for entry in f.by_category.get(cat, []):
            sev_e = {"ERROR": "🔴", "WARN": "🟡", "NOTE": "ℹ "}[entry["severity"]]
            print(f"      {sev_e} [{entry['code']}] {entry['message']}")
    print()
    if f.errors > 0:
        print("❌ SHIP BLOCKER — do not deploy until errors resolved.")
    elif f.warns > 0:
        print("🟡 Clean of errors. Warnings present — review before shipping.")
    else:
        print("✅ ALL CLEAR — safe to ship.")
    print()


if __name__ == "__main__":
    main()
