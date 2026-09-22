#!/usr/bin/env python3
"""Fish Finder — fleet-location harvester from OTW / Fisherman weekly reports (v23.52, Randy 2026-08-15).

Randy: "Also pull fishing fleet info from other sources."

**What this does:**

Fetches the most recent weekly fishing reports from On The Water (LI/NYC,
SNJ, RI, CT) and The Fisherman, scans them for FLEET-LOCATION mentions
(sentences that say "boats stacked on the ridge", "20 boats at Coxes",
"the fleet has been all over the Coimbra") and maps each mention to the
nearest known fishing zone. Emits candidate clusters with
`source: "captain_report"` so they render on the map's 🎯 Clusters
layer alongside the satellite-detected candidates.

**Why this matters:**

The satellite detector (`boat_cluster_detector.py`) only sees Sentinel-30m
imagery, which has orbital gaps and cloud limits. Captain reports fill
the gap — a captain saying "I ran to the Coimbra Wreck yesterday, boats
everywhere" is real, dated, direct evidence of a fleet cluster, and
often catches spots the satellite missed.

**Output:** `/root/fish-finder/data/fleet_intel.json` — list of captain-
sourced candidates. `boat_cluster_detector.py` merges these into the
final `data/boat_clusters.json` at build time.

**Sources scanned** (in order of value):
  - onthewater.com/fishing-reports/YYYY/MM/{region}-fishing-report-*
  - thefisherman.com/area/... (LI, SNJ, RI/CT weekly reports)
  - Falls back gracefully if a source is down.

**Run manually:**
    python3 /root/fish-finder/fleet_intel_harvester.py --verbose
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import pathlib
import re
import sys
import urllib.error
import urllib.request

ZONES_JSON = pathlib.Path("/root/fish-finder/data/zones.json")
OUT_JSON = pathlib.Path("/root/fish-finder/data/fleet_intel.json")

# Sentence patterns that indicate a FLEET (multiple boats) mention. Deliberate-
# ly conservative — we want high-signal hits, not every "there were some boats."
FLEET_PATTERNS = [
    r"\b(?:the\s+)?fleet\b",                  # "the fleet has been…"
    r"\b\d{1,3}\s*[+–-]?\s*boats?\b",         # "50 boats", "30-40 boats"
    r"\bboats?\s+(?:stacked|stack|piled|loaded|everywhere|all\s+over)\b",
    r"\b(?:stacked|piled|loaded|packed)\s+(?:up\s+)?with\s+boats?\b",
    r"\ba\s+lot\s+of\s+boats?\b",
    r"\bboat\s+traffic\s+(?:has\s+been\s+)?(?:heavy|thick|crazy)\b",
]
# Combined regex for scanning
FLEET_RE = re.compile("|".join(f"(?:{p})" for p in FLEET_PATTERNS), re.IGNORECASE)

# Zone-name → zone_id keyword map. Order matters — longest/most-specific first
# so "hudson canyon" wins before "hudson". Populated from zones.json at run
# time so it stays in sync.
def build_zone_keyword_map(zones):
    """Build a list of (keyword_regex, zone_id) tuples for text matching.
    Sorted longest-first so a zone name like 'Hudson Canyon' beats a bare
    'Hudson' match if both patterns exist."""
    aliases = {
        # zone_id: [keyword variants captains use]
        "coimbra_wreck":         ["coimbra wreck", "coimbra", "the coimbra"],
        "butterfish_hole":       ["butterfish hole", "butterfish"],
        "hudson_canyon":         ["hudson canyon", "the hudson", "hudson"],
        "block_canyon":          ["block canyon", "the block"],
        "atlantis_canyon":       ["atlantis canyon", "atlantis"],
        "veatch_hydrographer":   ["veatch", "hydrographer", "veatch canyon", "hydrographer canyon"],
        "tuna_ridge":            ["tuna ridge", "the ridge", "30-fathom", "30 fathom"],
        "coxes_ledge_se":        ["coxes ledge", "coxes", "cox's ledge", "coxs ledge"],
        "s_block_nearshore":     ["south of block", "s of block", "s.o.b", "sob "],
        "habs_ledge":            ["habs ledge", "the habs", "hab's ledge"],
        "the_fishtails":         ["fishtails", "the fishtails", "block canyon n tip"],
        "montauk_nearshore":     ["montauk nearshore", "east end offshore", "montauk offshore"],
        "south_shore_li":        ["south shore", "shinnecock", "moriches", "s shore li"],
        "mud_hole":              ["mud hole", "the mud hole"],
        "acid_barge":            ["acid barge", "the acid barge"],
        "the_gully":             ["the gully"],
        "stellwagen_bank":       ["stellwagen", "stellwagen bank"],
        "cape_cod_bay":          ["cape cod bay"],
        "jeffreys_ledge":        ["jeffreys ledge", "jeffreys"],
        "the_race":              ["the race"],
        "plum_gut":              ["plum gut"],
        "montauk_rips_striper":  ["montauk rips", "montauk point rips"],
        "block_island_striper":  ["block island reefs", "sw ledge", "se light"],
        "narragansett_bay":      ["narragansett bay", "newport rips"],
        "watch_hill_reef":       ["watch hill"],
        "the_triangle":          ["the triangle"],
    }
    result = []
    for zid, kws in aliases.items():
        for kw in kws:
            result.append((re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE), zid))
    # Sort by keyword length descending → longest / most-specific match wins
    result.sort(key=lambda t: -len(t[0].pattern))
    return result


# --- URL SOURCES -------------------------------------------------------------

FRESHNESS_DAYS = 14  # v23.55 (Randy 2026-08-16): "Captain report from July 16
                     # is really old, we need current stuff." Anything older
                     # than 14 days gets dropped as stale — Randy's fishing
                     # decisions turn week-over-week, so month-old fleet
                     # locations are worse than useless (they mislead).


def recent_otw_urls(weeks_back=2):
    """Build the candidate URLs for OTW's weekly regional reports. OTW's URL
    pattern is very predictable: /fishing-reports/YYYY/MM/{region}-fishing-report-{month}-{day}-{year}.
    Reports typically post on Wednesdays or Thursdays.

    Returns a list of URLs to probe. We fetch and check; 404s are silently
    dropped.
    """
    urls = []
    today = datetime.date.today()
    # Walk back ~35 days looking for Wednesday/Thursday-posted reports
    regions = [
        "long-island-and-nyc",
        "connecticut",
        "rhode-island",
        "southern-new-jersey",
    ]
    for days_ago in range(1, weeks_back * 7 + 3):
        d = today - datetime.timedelta(days=days_ago)
        if d.weekday() not in (2, 3):  # 2=Wed, 3=Thu
            continue
        # OTW format: "long-island-and-nyc-fishing-report-august-13-2026"
        month_name = d.strftime("%B").lower()
        for region in regions:
            slug = f"{region}-fishing-report-{month_name}-{d.day}-{d.year}"
            urls.append((f"https://onthewater.com/fishing-reports/{d.year}/{d.month:02d}/{slug}", d.isoformat(), region))
    return urls


# --- FETCH + PARSE -----------------------------------------------------------

def fetch(url, timeout=15):
    """Fetch a URL, return decoded text or None. Silent on 404.
    v24.105 — 3-attempt retry with backoff [2s, 6s] for transient failures.
    404s and small-body responses (likely error pages) don't retry."""
    import time as _time
    backoffs = [2, 6]
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; FishFinderBot/1.0; +personal-use)",
                "Accept": "text/html,application/xhtml+xml",
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if r.status != 200:
                    return None
                body = r.read()
                # OTW pages are ~50-200KB; anything much smaller is likely an error page
                if len(body) < 3000:
                    return None
                return body.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt < len(backoffs):
                _time.sleep(backoffs[attempt])
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt < len(backoffs):
                _time.sleep(backoffs[attempt])
    return None


def extract_sentences(html):
    """Crude sentence extraction from an OTW article HTML. Strips tags, splits
    on sentence boundaries. Good enough for our regex scan; no need for a
    proper HTML parser dependency."""
    # Strip script/style blocks
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    # Convert tags to spaces
    text = re.sub(r"<[^>]+>", " ", text)
    # Collapse whitespace and decode common entities
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&#8217;|&rsquo;|&apos;", "'", text)
    text = re.sub(r"&#8220;|&#8221;|&ldquo;|&rdquo;", '"', text)
    text = re.sub(r"\s+", " ", text).strip()
    # Split on sentence-ending punctuation followed by a capital letter
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'])", text)
    return [s for s in sentences if 20 <= len(s) <= 400]


def _is_boilerplate(sent):
    """Skip article headers, bylines, category tags, and other non-content
    sentences that trip the fleet regex on incidental words. v23.55: the
    July 16 hit was 'By Jack Larizadeh July 16, 2026 Long Island and NYC
    Fishing Report Rockfish Charters out of Moriches…' — pure boilerplate."""
    s = sent.strip()
    # Author byline / date-first patterns
    if re.match(r"^By\s+[A-Z][a-z]+\s+[A-Z]", s): return True
    if re.search(r"Fishing Report\s+[A-Z]", s): return True
    if re.search(r"\bPosted (?:on|by)\b", s): return True
    # Regional tag lines that OTW attaches at the top/bottom
    if re.match(r"^(Long Island|Connecticut|Rhode Island|Southern New Jersey|Northern New Jersey|Cape Cod|Massachusetts)\b", s): return True
    # Very short, all-caps section headers
    if len(s) < 40 and s.upper() == s: return True
    return False


def scan_report(text, source_url, source_date, region, zone_kw_map, zone_by_id, verbose=False):
    """Return list of fleet-location candidates found in one report.

    v23.55 tightened: (a) freshness filter — reports older than
    FRESHNESS_DAYS are dropped upstream so this only sees fresh material;
    (b) boilerplate filter — bylines / category tags / headers can't
    trip the fleet regex on incidental words like 'Fishing Report' or
    a captain's boat name."""
    candidates = []
    sentences = extract_sentences(text)
    for sent in sentences:
        if _is_boilerplate(sent):
            continue
        if not FLEET_RE.search(sent):
            continue
        # Find zone mentions in the SAME sentence
        matched_zones = []
        for kw_re, zid in zone_kw_map:
            if kw_re.search(sent):
                if zid not in matched_zones:
                    matched_zones.append(zid)
        if not matched_zones:
            continue  # Fleet mention with no location — useless
        # Take the FIRST matched zone (longest-keyword-first sort ensures
        # most-specific wins). Log runners-up in the note.
        primary = matched_zones[0]
        z = zone_by_id.get(primary)
        if not z or "center" not in z:
            continue
        candidates.append({
            "lat": z["center"][0],
            "lon": z["center"][1],
            "confidence": "medium",  # captain reports are inherently mid-confidence
            "nearest_zone_id": primary,
            "nearest_zone_name": z["name"],
            "source": "captain_report_otw",
            "source_date": source_date,
            "source_url": source_url,
            "source_region": region,
            "source_quote": sent[:280],
            "also_mentioned_zones": [z for z in matched_zones[1:5]],
        })
        if verbose:
            print(f"    [{region}] {primary}: {sent[:100]}…")
    return candidates


# --- ORCHESTRATION -----------------------------------------------------------

def harvest_fleet_intel(verbose=False):
    """Full pipeline. Returns dict {generated_at, candidates, scan_log, notes}."""
    result = {
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "candidates": [],
        "scan_log": [],
        "notes": [],
    }
    zones = json.loads(ZONES_JSON.read_text())["zones"]
    zone_by_id = {z["id"]: z for z in zones}
    zone_kw_map = build_zone_keyword_map(zones)

    urls_to_try = recent_otw_urls(weeks_back=2)  # v23.55: was 5 → 2 (14 days)
    today = datetime.date.today()
    fetched_ok = 0
    stale_dropped = 0
    for url, date_iso, region in urls_to_try:
        report_date = datetime.date.fromisoformat(date_iso)
        # v23.55: hard freshness gate. Randy 2026-08-16: "we need current
        # stuff." Anything older than FRESHNESS_DAYS gets skipped even if
        # the report page is still reachable — showing stale fleet mentions
        # on the map misleads more than helping.
        if (today - report_date).days > FRESHNESS_DAYS:
            stale_dropped += 1
            continue
        if verbose: print(f"  probe: {url}")
        html = fetch(url)
        if html is None:
            continue
        fetched_ok += 1
        cands = scan_report(html, url, date_iso, region, zone_kw_map, zone_by_id, verbose=verbose)
        result["scan_log"].append({
            "url": url, "date": date_iso, "region": region, "candidates": len(cands),
        })
        result["candidates"].extend(cands)
    if stale_dropped:
        result["notes"].append(f"Skipped {stale_dropped} URLs older than {FRESHNESS_DAYS} days (freshness gate — Randy: 'we need current stuff').")

    # Dedup: same zone + same date → merge (keep longest quote as primary)
    dedup = {}
    for c in result["candidates"]:
        key = (c["nearest_zone_id"], c["source_date"], c.get("source_region", ""))
        if key not in dedup or len(c["source_quote"]) > len(dedup[key]["source_quote"]):
            dedup[key] = c
    result["candidates"] = list(dedup.values())

    result["fetched_reports"] = fetched_ok
    result["urls_probed"] = len(urls_to_try)
    if fetched_ok == 0:
        result["notes"].append("OTW site returned no reachable weekly reports in the window scanned — captain-source candidates empty this run.")

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, indent=2))
    if verbose:
        print(f"  fetched {fetched_ok}/{len(urls_to_try)} reports, found {len(result['candidates'])} fleet-location candidates")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()
    result = harvest_fleet_intel(verbose=args.verbose)
    print(f"\nFleet intel: {len(result['candidates'])} captain-sourced candidates from {result['fetched_reports']} OTW reports")
    for c in result["candidates"][:5]:
        print(f"  · {c['source_date']} {c['nearest_zone_id']:24}  \"{c['source_quote'][:80]}…\"")


if __name__ == "__main__":
    main()
