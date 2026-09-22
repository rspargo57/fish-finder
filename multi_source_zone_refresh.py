#!/usr/bin/env python3
"""
v24.83 — Multi-source zone-mention harvester. Extends OTW scanner to also
scan The Fisherman Magazine (LI/NJ/RI-CT weekly) and Fisherman's Post (NC).
Bumps heat_updated on NE + Mid-Atl + SE zones mentioned in recent reports.
"""
import json, re, urllib.request, datetime, pathlib

BASE = pathlib.Path("/root/fish-finder")
today = datetime.date.today()

def _fetch(url, timeout=8):
    """v24.105 — 3-attempt retry with backoff [2s, 6s] and default timeout
    bumped 5s→8s. Multi-source harvester makes 50+ requests per build across
    OTW/Fisherman/Coastal Angler; a 5s timeout on a slow WordPress page (which
    happens routinely) silently dropped that zone-source pairing for the day.
    """
    import time as _time
    backoffs = [2, 6]
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 fish-finder"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if r.status == 200:
                    return r.read().decode("utf-8", errors="replace")
                # Any non-200 — no point retrying (mostly 404s from URL rotation)
                return None
        except Exception:
            if attempt < len(backoffs):
                _time.sleep(backoffs[attempt])
    return None

def _text_from_html(html):
    txt = re.sub(r'<script[^>]*>.*?</script>', ' ', html, flags=re.S|re.I)
    txt = re.sub(r'<style[^>]*>.*?</style>', ' ', txt, flags=re.S|re.I)
    txt = re.sub(r'<[^>]+>', ' ', txt)
    return re.sub(r'\s+', ' ', txt).lower()

# ------------------- SOURCE 1: OTW weekly reports -------------------
# v24.87 — corrected slugs (OTW splits NJ into northern/southern; drops
# "north-carolina" which was 404ing; adds cape-cod, maine, ma, upstate NY,
# northeast-offshore-report which is the WHOLE offshore mid-Atl/canyons feed).
def otw_urls():
    # v24.87 — narrow to last 4 days (weekly reports post Wed).  10 regions
    # × 4 days = 40 URLs; most 200s hit in <300ms, 404s cost ~2s.  We keep
    # parallelism at max_workers=16 in the fetch loop below.
    urls = []
    for offset in range(0, 4):
        d = today - datetime.timedelta(days=offset)
        month = d.strftime("%B").lower()
        for region in [
            "connecticut", "rhode-island", "long-island-and-nyc",
            "northern-new-jersey", "southern-new-jersey",
            "cape-cod", "massachusetts",
            "coastal-new-hampshire-and-maine-coast",
            "maryland-and-chesapeake-bay",
            "northeast-offshore-report",   # ← canyons, tuna, offshore mid-Atl
        ]:
            # Regions with "report" already in the name (northeast-offshore-report)
            # don't take the "-fishing-report-" infix.
            infix = "" if "report" in region else "-fishing-report"
            slug = f"{region}{infix}-{month}-{d.day}-{d.year}"
            urls.append((f"https://onthewater.com/fishing-reports/{d.year}/{d.month:02d}/{slug}", d))
    return urls

# ------------------- SOURCE 2: Fisherman Magazine LI/NJ area pages -------------------
def fisherman_area_urls():
    # These are area landing pages that show most recent reports
    return [
        ("https://www.thefisherman.com/area/li-nyc/", today),
        ("https://www.thefisherman.com/area/new-jersey/", today),
        ("https://www.thefisherman.com/area/new-england/", today),
        ("https://www.thefisherman.com/area/md-de/", today),
    ]

# ------------------- SOURCE 3: Fisherman's Post NC -------------------
def fishermans_post_urls():
    return [
        ("https://fishermanspost.com/fishing-reports/", today),
    ]

# ------------------- SOURCE 4: Coastal Angler (FL / Gulf / SE) — v24.87 -------------------
# Coastal Angler's /fishing-reports/ index page has region-anchor sections
# (Florida, Gulf, Southeast) with all recent report titles + snippets inline.
# We scrape the whole page (~140KB) and look for zone-name mentions in that
# concatenated blob. Individual report pages don't carry fresh text; the
# index does.
def coastal_angler_urls():
    return [
        ("https://coastalanglermag.com/fishing-reports/", today),
    ]

# v24.86: bait keyword patterns — captain reports frequently say
# "bunker are pushing", "sand eels in close", "peanut bunker everywhere".
# When we see one of these near a zone name, we ALSO add a bait_intel entry
# for that zone, so the model's baitBoost signal picks it up.
BAIT_PATTERNS = [
    (r"\bbunker\b|\bmenhaden\b", "menhaden/bunker"),
    (r"\bpeanut\s+bunker\b", "peanut bunker"),
    (r"\bsand\s?eels?\b", "sand eels"),
    (r"\banchovies\b|\banchovy\b", "anchovies"),
    (r"\bsardines?\b", "sardines"),
    (r"\bsquid\b", "squid"),
    (r"\bballyhoo\b", "ballyhoo"),
    (r"\bmullet\b", "mullet"),
    (r"\bmackerel\b", "mackerel"),
    (r"\bsilversides?\b", "silversides"),
    (r"\bbait\s+ball\b|\bbait\s+pod\b", "bait ball/pod"),
]

def refresh_region(zones_path, region_label):
    from concurrent.futures import ThreadPoolExecutor
    if not zones_path.exists():
        return 0
    zones = json.loads(zones_path.read_text())
    zone_list = zones.get("zones", [])

    # v24.90 — improved matcher. Was only checking the full canonical zone
    # name; missed today's real mentions like "Providence" (zone name is
    # "Providence / Seekonk Rivers") or "Bartlett" (zone "Bartlett Reef").
    # Now we also register cleaned + split variants:
    #   1) full lowered name
    #   2) parenthetical-stripped version
    #   3) each half of a slash split
    #   4) each comma-piece
    #   5) drop trailing generic tokens (reef, shoal, ridge, ledge, gut, ..)
    #      so "Bartlett Reef" also matches "Bartlett"
    # Registration length floor stays ≥ 5 chars to avoid catching common words.
    # v24.96 — expanded generic-tail list. Was missing "rips" (as in
    # "Cape May Rips") which prevented the matcher from bumping cape_may_rips
    # even when reports named it. Also added: pass, inlet, river, sound,
    # bridge, wreck, jetty, pier, beach, harbor, channel.
    GENERIC_TAILS = re.compile(r"\s+(reef|shoal|ridge|ledge|gut|point|bay|"
                              r"rocks?|ground|grounds|bank|banks|hole|hump|"
                              r"canyon|island|islands|shoals|rips|pass|"
                              r"inlet|river|sound|bridge|wreck|jetty|pier|"
                              r"beach|harbor|channel|flats)$", re.I)
    STOPWORDS = {"the", "and", "of", "in", "at", "to", "for"}
    name_to_id = {}
    def _register(variant, zid):
        v = (variant or "").strip().lower()
        v = re.sub(r'\s+', ' ', v).strip('.,-/ ')
        if len(v) < 5 or v in STOPWORDS: return
        # first registration wins so a shorter variant of one zone doesn't
        # get overwritten by another zone's variant
        name_to_id.setdefault(v, zid)

    for z in zone_list:
        zid = z.get("id"); name = (z.get("name","") or "").strip()
        if not zid or not name: continue
        clean = re.sub(r'\s*\([^)]*\)\s*$', '', name).strip()
        _register(name, zid)
        _register(clean, zid)
        # Slash-split ("Providence / Seekonk Rivers" → each side)
        for piece in re.split(r'\s*[/\-–]\s*', clean):
            _register(piece, zid)
            # And drop trailing generic tail on each piece
            _register(GENERIC_TAILS.sub("", piece), zid)
        # Comma-split
        for piece in clean.split(","):
            _register(piece.strip(), zid)
            _register(GENERIC_TAILS.sub("", piece.strip()), zid)
        # Drop trailing generic tail on full name
        _register(GENERIC_TAILS.sub("", clean), zid)

    print(f"\n=== {region_label} — {len(name_to_id)} zone lookups ===")

    all_urls = otw_urls() + fisherman_area_urls() + fishermans_post_urls() + coastal_angler_urls()

    # v24.96 (Randy 2026-09-04) — REGION-CONTEXT AGGREGATE mappings.
    # Captains write "the canyons are lit" or "the reefs are on fire" rather
    # than naming each spot individually. Without this, dominant weekly
    # reports (OTW MD/Chesapeake, OTW northeast-offshore) bump zero specific
    # zones because their text is generic. This dict maps a URL-slug pattern
    # to (generic-phrase, list-of-zone-ids) — when the URL matches AND the
    # phrase appears in the text, ALL listed zones get bumped.
    #
    # These are LIBERAL bumps: they only fire when a captain report ABOUT that
    # region uses the generic term. Not to be confused with pattern-based
    # matching — this is context-scoped ("in a MD report, 'the canyons' means
    # MD canyons").
    def _region_aggregate_matches(url):
        u = url.lower()
        matches = []
        # OTW Maryland / Chesapeake — MD canyons + hot spots
        if "maryland-and-chesapeake" in u:
            matches.extend([
                (r"\bthe\s+canyons?\b|\bcanyons?\b", [
                    "baltimore_canyon", "wilmington_canyon", "poor_mans_canyon",
                    "washington_canyon", "norfolk_canyon"]),
                (r"\bocean\s+city\b|\boc\b(?!\s*md)", [
                    "jackspot_hot_dog", "twenty_fathom_line", "twenty_six_mile_hill",
                    "baltimore_canyon"]),
                (r"\bthe\s+bay\b|\bchesapeake\b", ["cbbt_chesapeake_mouth"]),
            ])
        # OTW NJ (northern + southern) — NJ canyons + reefs
        if "-new-jersey-" in u:
            matches.extend([
                (r"\bthe\s+canyons?\b|\bcanyons?\b(?!\s+(runners?))", [
                    "hudson_canyon_midatl", "toms_canyon", "lindenkohl_canyon",
                    "spencer_canyon"]),
                (r"\bthe\s+ridge\b", ["barnegat_ridge", "manasquan_ridge"]),
                (r"\bthe\s+tuna\s+grounds?\b|\btuna\s+grounds?\b", [
                    "wicked_tuna_grounds", "hudson_canyon_midatl"]),
            ])
        # OTW NE Offshore — NE canyons + offshore zones
        if "northeast-offshore-report" in u:
            matches.extend([
                (r"\bthe\s+canyons?\b|\bcanyons?\b", [
                    "hudson_canyon", "atlantis_canyon", "block_canyon",
                    "veatch_hydrographer"]),
                (r"\btuna\s+ridge\b|\bthe\s+ridge\b", ["tuna_ridge"]),
                (r"\bblock\b|\bblock\s+island\b", [
                    "block_canyon", "s_block_nearshore", "block_island_striper"]),
            ])
        # OTW Cape Cod / MA — offshore MA
        if "cape-cod-fishing-report" in u or "massachusetts-fishing-report" in u:
            matches.extend([
                (r"\bthe\s+canyons?\b|\bcanyons?\b", [
                    "veatch_hydrographer", "atlantis_canyon"]),
                (r"\bstellwagen\b", []),  # stellwagen zone in NE
                (r"\bthe\s+tuna\s+grounds?\b", []),
            ])
        # OTW CT + RI + LI — NE inshore + Race/Plum + BI
        if "connecticut-fishing" in u or "rhode-island-fishing" in u:
            matches.extend([
                (r"\bthe\s+race\b", ["the_race"]),
                (r"\bthe\s+sound\b", ["western_li_sound", "central_li_sound", "block_island_sound"]),
                (r"\bblock\s+island\b|\bBI\b(?!\s+block)", [
                    "block_island_striper", "s_block_nearshore", "block_island_sound"]),
            ])
        # Fisherman's Post NC — NC coast
        if "fishermanspost" in u:
            matches.extend([
                (r"\boregon\s+inlet\b", ["oregon_inlet"]),
                (r"\bhatteras\b", ["diamond_shoals", "hatteras_the_point", "oregon_inlet"]),
                (r"\bthe\s+point\b", ["hatteras_the_point"]),
                (r"\bgulf\s+stream\b", ["oregon_inlet", "diamond_shoals"]),
            ])
        return matches

    mentions = {}          # zid → latest report_date
    bait_hits = {}         # zid → list of (date, bait_type, url)
    fetched = 0
    def _worker(item):
        url, d = item
        return (url, d, _fetch(url))
    with ThreadPoolExecutor(max_workers=16) as ex:
        for url, d, html in ex.map(_worker, all_urls):
            if not html: continue
            fetched += 1
            text = _text_from_html(html)
            iso = d.isoformat()
            # v24.96: also apply region-context aggregate mappings
            for pattern, zone_ids in _region_aggregate_matches(url):
                if re.search(pattern, text):
                    for zid in zone_ids:
                        # Verify zone exists in this region file
                        if any(z.get("id") == zid for z in zone_list):
                            if iso > mentions.get(zid, ""):
                                mentions[zid] = iso
            # Zone name mentions
            zones_here = set()
            for nm, zid in name_to_id.items():
                if nm in text:
                    zones_here.add(zid)
                    if iso > mentions.get(zid, ""):
                        mentions[zid] = iso
            # Bait word mentions — only attribute to zones ALSO mentioned in same report
            bait_types_here = []
            for pat, label in BAIT_PATTERNS:
                if re.search(pat, text):
                    bait_types_here.append(label)
            if bait_types_here and zones_here:
                for zid in zones_here:
                    bait_hits.setdefault(zid, []).append({"date": iso, "types": bait_types_here, "url": url})

    print(f"  fetched {fetched} pages, {len(mentions)} zones mentioned, {len(bait_hits)} zones with bait cues")

    bumped_dates = 0
    bumped_bait = 0
    for z in zone_list:
        zid = z.get("id")
        if zid in mentions:
            current = z.get("heat_updated","1970-01-01")
            if mentions[zid] > current:
                z["heat_updated"] = mentions[zid]
                z.setdefault("intel_sources", {})
                z["intel_sources"]["multi_source_refresh"] = {
                    "last_report_date": mentions[zid], "sources": "OTW + Fisherman Magazine + Fisherman's Post"
                }
                bumped_dates += 1
        if zid in bait_hits:
            # add fresh bait_intel entries (dedupe by date)
            existing_bait = zones.get("bait_intel", [])
            existing_dates_for_zone = {b.get("date") for b in existing_bait if zid in (b.get("zones") or [])}
            for hit in bait_hits[zid]:
                if hit["date"] in existing_dates_for_zone: continue
                entry = {
                    "date": hit["date"],
                    "type": ", ".join(sorted(set(hit["types"])))[:60],
                    "source": "OTW/Fisherman report auto-extract",
                    "source_url": hit["url"],
                    "zones": [zid],
                }
                zones.setdefault("bait_intel", []).append(entry)
                bumped_bait += 1

    zones_path.write_text(json.dumps(zones, indent=2))
    print(f"  bumped {bumped_dates} zones' heat_updated + added {bumped_bait} bait_intel entries")
    # v24.100 source-health record. We treat "fetched > 0 pages" as OK even
    # when 0 zones happened to be bumped (dedup by date; the source is alive
    # even if today's report doesn't overlap with our zones).
    try:
        import source_health as _sh
        _sh.record("otw_multi", rows=len(mentions), ok=(fetched > 0),
                   error=None if fetched > 0 else "0 pages fetched")
    except Exception:
        pass
    return bumped_dates

if __name__ == "__main__":
    # v24.87: extended to all 5 regions. NE/Mid-Atl/SE get OTW+Fisherman feed;
    # Gulf/S.FL rely primarily on Coastal Angler's index-page bulk snippets.
    for label, fname in [
        ("NE",       "zones.json"),
        ("Mid-Atl",  "zones_mid_atlantic.json"),
        ("SE",       "zones_south_atlantic.json"),
        ("Gulf",     "zones_gulf.json"),
        ("S.FL",     "zones_south_florida.json"),
    ]:
        refresh_region(BASE / "data" / fname, label)
