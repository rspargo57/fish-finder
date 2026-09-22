#!/usr/bin/env python3
"""
v24.79 — Refresh stale NE zone heat_updated from OTW weekly reports.

Randy's NE zone data has 24 zones with heat_updated >21 days old. YouTube
captains rarely talk about inshore reef spots (Plum Gut, Millstone Point,
Bartlett Reef, etc.), so the auto-lift from YouTube corpus never fires
on them. But On The Water publishes weekly regional reports (CT/RI/LI/MA)
that DO cover these spots by name.

This script:
1. Fetches recent OTW weekly reports for CT/RI/LI/NY (last 14 days)
2. Text-searches for known NE zone name mentions
3. For any zone mentioned in a report published <= 14d ago, bumps
   `heat_updated` to the report date + adds an intel_sources.otw entry

Called from build-inlined.py at nightly build time. Safe to run manually.
"""
import json, re, urllib.request, urllib.error, datetime, pathlib

BASE = pathlib.Path("/root/fish-finder")
ZONES_PATH = BASE / "data" / "zones.json"
today = datetime.date.today()

# OTW report URL templates — try last 21 days for CT/RI/LI/MA
OTW_REGIONS = ["connecticut", "rhode-island", "long-island-and-nyc", "massachusetts"]

def _fetch(url, timeout=8):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 fish-finder"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status == 200:
                return r.read().decode("utf-8", errors="replace")
    except Exception:
        return None
    return None

def _candidate_urls():
    """OTW's weekly report slugs follow: {region}-fishing-report-{month}-{day}-{year}."""
    urls = []
    for offset in range(0, 10):
        d = today - datetime.timedelta(days=offset)
        month_name = d.strftime("%B").lower()
        for region in OTW_REGIONS:
            slug = f"{region}-fishing-report-{month_name}-{d.day}-{d.year}"
            urls.append((f"https://onthewater.com/fishing-reports/{d.year}/{d.month:02d}/{slug}", d, region))
    return urls

def _text_from_html(html):
    """Strip tags + whitespace-normalize."""
    txt = re.sub(r'<script[^>]*>.*?</script>', ' ', html, flags=re.S|re.I)
    txt = re.sub(r'<style[^>]*>.*?</style>', ' ', txt, flags=re.S|re.I)
    txt = re.sub(r'<[^>]+>', ' ', txt)
    txt = re.sub(r'\s+', ' ', txt)
    return txt

def refresh():
    zones = json.loads(ZONES_PATH.read_text())
    ne_zones = zones.get("zones", [])

    # Build zone name → id lookup (case-insensitive, with common variants)
    name_to_id = {}
    for z in ne_zones:
        zid = z.get("id")
        name = z.get("name", "").strip()
        if not zid or not name: continue
        # Add full name + partial (drop parenthetical suffix like "(Chappaquiddick)")
        clean = re.sub(r'\s*\([^)]*\)\s*$', '', name).strip()
        for variant in {name, clean}:
            if variant:
                name_to_id[variant.lower()] = zid

    print(f"Zone name → id lookup: {len(name_to_id)} entries")

    fetched = 0
    parsed = 0
    mentions = {}   # zid → report_date (latest)
    for url, d, region in _candidate_urls():
        html = _fetch(url)
        if not html: continue
        fetched += 1
        # Only accept if it's a real report page (has expected content)
        if 'fishing report' not in html.lower() and 'weekly report' not in html.lower():
            continue
        parsed += 1
        text = _text_from_html(html).lower()
        report_date = d.isoformat()
        for nm, zid in name_to_id.items():
            # Case-insensitive substring match. Guard against 3-letter zone names
            # matching common words (e.g. don't match "the ledge").
            if len(nm) < 5: continue
            if nm in text:
                cur = mentions.get(zid)
                if not cur or report_date > cur:
                    mentions[zid] = report_date
    print(f"Fetched {fetched} report pages, parsed {parsed} real reports")
    print(f"Zone mentions found: {len(mentions)}")

    # Apply mentions: bump heat_updated + record intel_sources.otw
    bumped = 0
    for z in ne_zones:
        zid = z.get("id")
        if zid not in mentions: continue
        report_date = mentions[zid]
        current = z.get("heat_updated", "1970-01-01")
        if report_date > current:
            z["heat_updated"] = report_date
            z.setdefault("intel_sources", {})
            z["intel_sources"]["otw"] = {"last_report_date": report_date, "source": "On The Water weekly"}
            bumped += 1
    ZONES_PATH.write_text(json.dumps(zones, indent=2))
    print(f"Bumped {bumped} zones' heat_updated")
    if mentions:
        print("\nDetail (zone_id → report_date):")
        for zid, d in sorted(mentions.items(), key=lambda kv: kv[1], reverse=True):
            print(f"  {d}  {zid}")

if __name__ == "__main__":
    refresh()
