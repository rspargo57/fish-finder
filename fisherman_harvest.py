#!/usr/bin/env python3
"""The Fisherman regional forecast harvester (v24.7, 2026-08-23).

Aggregator source for BOTH regions — The Fisherman publishes weekly regional
video forecasts and text reports via WordPress REST API. Coverage:
  - NE:    "New England Video Fishing Forecast", "Long Island Video Fishing Forecast"
  - MidAtl: "NJ/DE Bay Region Fishing Forecast", "South Jersey Fishing Report"

For each recent post we care about:
  - date        — pubDate
  - region      — inferred from title (NE | midatl)
  - title       — verbatim
  - excerpt     — first ~500 chars of content
  - link        — canonical article URL
  - locations   — regex-matched zone-keywords (feeds youtube_zone_map.json's location_to_zones)
  - species     — species-name regex hits (from youtube_harvest.SPECIES_PATTERNS)

Results feed:
  - `bait_intel` promotion into zones.json / zones_mid_atlantic.json (source_type="fisherman_auto")
  - `intel_sources.fisherman` stamp on zones that got mentioned
"""
import json
import re
import urllib.request
import urllib.error
import datetime
import pathlib

WP_API_URL = "https://www.thefisherman.com/wp-json/wp/v2/posts?per_page=30&_embed"
BASE = pathlib.Path(__file__).parent

# v24.20 (Randy 2026-08-28): The Fisherman also publishes region-specific RSS
# feeds under /area/<region>/feed/ that carry actual tackle-shop + captain
# reports (not just the weekly video forecasts). These are FRESHER, more
# zone-specific, and name captains + spots by name. Example:
#   Aug 24 "Tight Lines Tackle-East End": Kenny reported tuna spread throughout
#   the waters outside Shinnecock, from around the reef and continuing offshore.
#   Fish of various sizes are being encountered around the Coimbra, Habs and
#   Ranger areas...
#
# Regional feeds mapped to Fish Finder regions:
FISHERMAN_REGIONAL_FEEDS = [
    ("east-end", "northeast", "The Fisherman East End (Montauk/Shinnecock)"),
    ("rhode-island", "northeast", "The Fisherman Rhode Island"),
    ("connecticut", "northeast", "The Fisherman Connecticut"),
    ("long-island", "northeast", "The Fisherman Long Island"),
]

# Titles that we want. Region tag is inferred from the title text.
TITLE_PATTERNS_MIDATL = [
    re.compile(r"\b(?:NJ|New Jersey)\b", re.IGNORECASE),
    re.compile(r"\bDE\b|\bDelaware\b", re.IGNORECASE),
    re.compile(r"\bMD\b|\bMaryland\b|\bOcean City\b", re.IGNORECASE),
    re.compile(r"\bVA\b|\bVirginia\b|\bChesapeake\b", re.IGNORECASE),
    re.compile(r"\bNC\b|\bNorth Carolina\b|\bHatteras\b|\bOBX\b|\bOuter Banks\b", re.IGNORECASE),
    re.compile(r"\bCape May\b", re.IGNORECASE),
    re.compile(r"\bBay Region\b", re.IGNORECASE),
    re.compile(r"\bSouth Jersey\b|\bNorth Jersey\b|\bCentral Jersey\b", re.IGNORECASE),
]
TITLE_PATTERNS_NE = [
    re.compile(r"\bNew England\b", re.IGNORECASE),
    re.compile(r"\bLong Island\b", re.IGNORECASE),
    re.compile(r"\bMassachusetts\b|\bMA\b|\bCape Cod\b|\bMartha's Vineyard\b", re.IGNORECASE),
    re.compile(r"\bRhode Island\b|\bRI\b|\bBlock Island\b|\bNarragansett\b", re.IGNORECASE),
    re.compile(r"\bConnecticut\b|\bCT\b|\bLIS\b", re.IGNORECASE),
    re.compile(r"\bMontauk\b|\bThe Race\b|\bPlum Gut\b", re.IGNORECASE),
]
# v24.35 (Randy 2026-08-29): Gulf classification — Panhandle / Perdido / Pensacola / Destin / PCB / Apalachicola vocab.
# Also picks up posts mentioning oil rigs specific to the Gulf.
TITLE_PATTERNS_GULF = [
    re.compile(r"\bFL Panhandle\b|\bFlorida Panhandle\b|\bEmerald Coast\b", re.IGNORECASE),
    re.compile(r"\bPensacola\b|\bPerdido\b|\bDestin\b|\bPanama City\b|\bPCB\b", re.IGNORECASE),
    re.compile(r"\bOrange Beach\b|\bGulf Shores\b|\bFort Morgan\b|\bDauphin Island\b", re.IGNORECASE),
    re.compile(r"\bMobile Bay\b|\bMississippi Sound\b|\bBiloxi\b|\bGulfport\b", re.IGNORECASE),
    re.compile(r"\bVenice, LA\b|\bGrand Isle\b|\bPort Fourchon\b", re.IGNORECASE),
    re.compile(r"\bAlabama offshore\b|\bLouisiana offshore\b|\bMississippi offshore\b", re.IGNORECASE),
    re.compile(r"\bDeSoto Canyon\b|\bMidnight Lump\b|\bPetronius\b|\bRam Powell\b", re.IGNORECASE),
    re.compile(r"\bthe Nipple\b|\bthe Elbow\b|\bthe Spur\b", re.IGNORECASE),  # Gulf-specific canyon-edge landmarks
    re.compile(r"\bApalachicola\b|\bCape San Blas\b|\bSt\.\s*George\b", re.IGNORECASE),
]
# v24.55 (Randy 2026-08-31): Zone 3 South Atlantic (Southeast) classification.
# Cape Lookout NC → Cape Canaveral FL. Distinguished from Mid-Atl (OBX/NC north
# of Cape Lookout) by explicit charter capital names — Wrightsville, Morehead
# City, Charleston, Savannah, Jacksonville, Daytona, Ponce Inlet.
TITLE_PATTERNS_SOUTH_ATLANTIC = [
    re.compile(r"\bSouth Atlantic\b|\bSoutheast US\b|\bSE US\b", re.IGNORECASE),
    re.compile(r"\bWrightsville\b|\bTopsail\b|\bMorehead City\b|\bBeaufort NC\b|\bAtlantic Beach NC\b", re.IGNORECASE),
    re.compile(r"\bCape Lookout\b|\bCape Fear\b", re.IGNORECASE),
    re.compile(r"\bMyrtle Beach\b|\bLittle River\b|\bCalabash\b|\bGeorgetown SC\b", re.IGNORECASE),
    re.compile(r"\bCharleston\b|\bMt Pleasant\b|\bIsle of Palms\b|\bFolly Beach\b|\bEdisto\b", re.IGNORECASE),
    re.compile(r"\bHilton Head\b|\bSt Simons\b|\bSt\.\s*Simons\b|\bBrunswick GA\b|\bSavannah\b", re.IGNORECASE),
    re.compile(r"\bJacksonville\b|\bSt Augustine\b|\bSt\.\s*Augustine\b|\bJupiter Inlet\b|\bMayport\b|\bAmelia Island\b", re.IGNORECASE),
    re.compile(r"\bDaytona\b|\bPonce Inlet\b|\bNew Smyrna\b|\bCape Canaveral\b|\bPort Canaveral\b|\bMerritt Island\b", re.IGNORECASE),
    re.compile(r"\bSC coast\b|\bGA coast\b|\bnortheast FL\b|\bNE Florida\b", re.IGNORECASE),
    re.compile(r"\bGulf Stream (?:break|edge)\b", re.IGNORECASE),   # SE anglers often use this
]

# v24.43 (Randy 2026-08-31): Zone 5 South Florida (Atlantic Pass A) classification.
# Iconic geography — Islamorada Hump, Fowey Rocks, Vandenberg — is unambiguous.
# Broad "Florida Keys" / "South Florida" also catches Florida Sportsman regional feeds.
TITLE_PATTERNS_SOUTH_FLORIDA = [
    re.compile(r"\bSouth Florida\b|\bS(outh)? FL\b", re.IGNORECASE),
    re.compile(r"\bFlorida Keys\b|\bthe Keys\b|\bUpper Keys\b|\bMiddle Keys\b|\bLower Keys\b", re.IGNORECASE),
    re.compile(r"\bIslamorada\b|\bKey Largo\b|\bMarathon\b|\bKey West\b|\bTavernier\b|\bBig Pine\b", re.IGNORECASE),
    re.compile(r"\bPalm Beach\b|\bJupiter\b|\bBoynton\b|\bBoca Raton\b", re.IGNORECASE),
    re.compile(r"\bFort Lauderdale\b|\bFt Lauderdale\b|\bHillsboro\b|\bPompano Beach\b|\bHollywood Beach\b|\bDania\b", re.IGNORECASE),
    re.compile(r"\bMiami\b|\bBiscayne\b|\bKey Biscayne\b|\bHaulover\b|\bStiltsville\b", re.IGNORECASE),
    re.compile(r"\bFowey Rocks\b|\bAlligator Reef\b|\bMolasses Reef\b|\bSombrero Reef\b|\bSand Key\b", re.IGNORECASE),
    re.compile(r"\bIslamorada Hump\b|\bMarathon Hump\b|\bDry Tortugas\b|\bMarquesas\b|\bBahia Honda\b", re.IGNORECASE),
    re.compile(r"\bUSS Vandenberg\b|\bUSS Duane\b|\bUSS Bibb\b|\bSpiegel Grove\b|\bCayman Salvager\b", re.IGNORECASE),
    re.compile(r"\bSailfish Coast\b|\bGold Coast\b|\bTreasure Coast\b|\bFlorida Straits\b", re.IGNORECASE),
]

# Trip-recap flag — a fishing forecast post typically has one of these markers.
# Filters out product reviews, tackle gear posts, etc.
FORECAST_MARKERS = [
    re.compile(r"\bForecast\b", re.IGNORECASE),
    re.compile(r"\bReport\b(?!\s+scam)", re.IGNORECASE),
]


def _fetch_posts():
    """Fetch the last 30 posts from The Fisherman WordPress REST API.
    Returns list of dicts with normalized fields, or [] on failure."""
    # v24.105 — 3-attempt retry with backoff [3s, 10s]. Single-shot was
    # too fragile with WordPress rate-limits.
    import time as _time
    backoffs = [3, 10]
    data = None
    last_err = None
    for attempt in range(3):
        req = urllib.request.Request(WP_API_URL, headers={
            "User-Agent": "Mozilla/5.0 (compatible; FishFinderBot/1.0; fisherman-harvester)",
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.load(r)
            break
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_err = e
            if attempt < len(backoffs):
                _time.sleep(backoffs[attempt])
    if data is None:
        print(f"  WARN: Fisherman WP API fetch failed after 3 attempts ({last_err})")
        return []
    if not isinstance(data, list):
        return []
    out = []
    for p in data:
        title = (p.get("title", {}) or {}).get("rendered", "")
        # Decode simple HTML entities in title
        title = title.replace("&#8211;", "–").replace("&#8217;", "'").replace("&amp;", "&")
        excerpt = (p.get("excerpt", {}) or {}).get("rendered", "")
        excerpt_text = re.sub(r"<[^>]+>", " ", excerpt or "")
        excerpt_text = re.sub(r"\s+", " ", excerpt_text).strip()
        content = (p.get("content", {}) or {}).get("rendered", "")
        content_text = re.sub(r"<[^>]+>", " ", content or "")
        content_text = re.sub(r"\s+", " ", content_text).strip()[:1200]
        date_iso = (p.get("date") or "")[:10]
        link = p.get("link") or ""
        out.append({
            "title": title,
            "date": date_iso,
            "link": link,
            "excerpt": excerpt_text[:400],
            "content": content_text,
        })
    return out


def _classify_region(title):
    """Return 'south_florida', 'gulf', 'south_atlantic', 'midatl', 'northeast', or None."""
    # S. Florida check FIRST — 'Florida Keys' / 'Miami' would otherwise slip
    # through Gulf patterns (both are "FL"). Islamorada Hump vs. Midnight Lump
    # is unambiguous but 'Florida' isn't.
    for pat in TITLE_PATTERNS_SOUTH_FLORIDA:
        if pat.search(title):
            return "south_florida"
    # Gulf check next — its landmarks (DeSoto Canyon, Petronius, etc.) are
    # unambiguous and could accidentally match a "NC canyon" mention otherwise.
    for pat in TITLE_PATTERNS_GULF:
        if pat.search(title):
            return "gulf"
    # v24.55: Southeast check before Mid-Atl. NC north of Cape Lookout stays
    # Mid-Atl; NC south of Cape Lookout + SC + GA + NE Florida → south_atlantic.
    # SE landmarks (Wrightsville, Charleston, Savannah) are unambiguous.
    for pat in TITLE_PATTERNS_SOUTH_ATLANTIC:
        if pat.search(title):
            return "south_atlantic"
    for pat in TITLE_PATTERNS_MIDATL:
        if pat.search(title):
            return "midatl"
    for pat in TITLE_PATTERNS_NE:
        if pat.search(title):
            return "northeast"
    return None


def _is_forecast(title, excerpt):
    """Return True if title/excerpt looks like a fishing forecast/report post."""
    combined = title + " " + excerpt
    return any(pat.search(combined) for pat in FORECAST_MARKERS)


def harvest(cutoff_days=7):
    """Fetch + classify last N days of Fisherman posts. Returns:
      {
        harvested_at: ISO,
        cutoff_days: int,
        total_posts: int,
        northeast_posts: [dict],
        midatl_posts: [dict],
      }
    Each post carries: date, title, link, excerpt, content, region.
    """
    posts = _fetch_posts()
    today = datetime.date.today()
    cutoff = today - datetime.timedelta(days=cutoff_days)
    ne_posts = []
    ma_posts = []
    gulf_posts = []
    sfl_posts = []
    for p in posts:
        try:
            d = datetime.date.fromisoformat(p["date"])
        except (ValueError, KeyError):
            continue
        if d < cutoff:
            continue
        title = p.get("title", "")
        excerpt = p.get("excerpt", "")
        if not _is_forecast(title, excerpt):
            continue
        region = _classify_region(title)
        if not region:
            continue
        p["region"] = region
        if region == "midatl":
            ma_posts.append(p)
        elif region == "gulf":
            gulf_posts.append(p)
        elif region == "south_florida":
            sfl_posts.append(p)
        else:
            ne_posts.append(p)
    return {
        "harvested_at": datetime.datetime.utcnow().isoformat() + "Z",
        "cutoff_days": cutoff_days,
        "total_posts": len(posts),
        "northeast_posts": ne_posts,
        "midatl_posts": ma_posts,
        "gulf_posts": gulf_posts,
        "south_florida_posts": sfl_posts,
    }


def promote_into_zones(result, zone_map, midatl_zone_ids, ne_zone_ids):
    """Turn Fisherman forecast posts into bait_intel promotions + intel_sources
    stamps. Uses youtube_harvest.LOCATION_PATTERNS for location extraction so the
    keyword surface is consistent across harvesters. Returns two dicts:
      {ne: [entries], midatl: [entries]}  — one bait_intel entry per (post,zone)
      {ne: {zone_id: {source_count, latest_date}}, midatl: same}
    """
    # Late import to avoid circular
    import youtube_harvest as yh
    loc_re = yh._location_re

    def _locations_from_text(text):
        hits = []
        for loc_key, patterns in loc_re.items():
            if any(p.search(text) for p in patterns):
                hits.append(loc_key)
        return hits

    def _zids_for(locs, region_set):
        z = []
        for loc in locs:
            for zid in zone_map.get(loc, []):
                if zid in region_set and zid not in z:
                    z.append(zid)
        return z

    entries = {"ne": [], "midatl": []}
    intel = {"ne": {}, "midatl": {}}

    for post in result["northeast_posts"] + result["midatl_posts"]:
        combined = post["title"] + " " + post.get("excerpt", "") + " " + post.get("content", "")
        locs = _locations_from_text(combined)
        if post["region"] == "midatl":
            zids = _zids_for(locs, midatl_zone_ids)
            bucket = "midatl"
        else:
            zids = _zids_for(locs, ne_zone_ids)
            bucket = "ne"
        if not zids:
            continue
        entry = {
            "date": post["date"],
            "title": post["title"][:280],
            "link": post["link"],
            "excerpt": post["excerpt"][:280],
            "zones": zids,
            "source": "The Fisherman regional forecast",
            "source_type": "fisherman_auto",
        }
        entries[bucket].append(entry)
        for zid in zids:
            slot = intel[bucket].setdefault(zid, {"source_count": 0, "latest_date": ""})
            slot["source_count"] += 1
            if post["date"] > slot["latest_date"]:
                slot["latest_date"] = post["date"]

    return entries, intel


def harvest_regional_reports(cutoff_days=7):
    """v24.20: pull The Fisherman's regional /area/<region>/feed/ RSS feeds.
    These carry actual tackle-shop + captain reports, often 2-5 per feed
    per Monday, with dated captain quotes naming spots and species.

    Returns:
      {
        harvested_at, cutoff_days,
        feeds: [{slug, region, source_name, count, items: [dict]}],
        total_items: int,
      }
    Each item dict: date, title (captain/shop-East End), body, link, feed_slug, feed_region.
    """
    import xml.etree.ElementTree as ET
    today = datetime.date.today()
    cutoff = today - datetime.timedelta(days=cutoff_days)
    feeds_out = []
    total = 0
    # v24.105 — 3-attempt retry with backoff [3s, 10s] on each regional feed.
    # Was single-shot; a 429 or transient TLS error silently dropped a whole
    # region's captain reports for the day.
    import time as _time
    backoffs = [3, 10]
    for slug, region, source_name in FISHERMAN_REGIONAL_FEEDS:
        url = f"https://www.thefisherman.com/area/{slug}/feed/"
        items = []
        body = None
        last_err = None
        for attempt in range(3):
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; FishFinderBot/1.0; fisherman-regional)",
                "Accept": "application/rss+xml,application/xml"
            })
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    body = r.read().decode("utf-8", errors="replace")
                break
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
                last_err = e
                if isinstance(e, urllib.error.HTTPError) and e.code == 404:
                    break
                if attempt < len(backoffs):
                    _time.sleep(backoffs[attempt])
        try:
            if body is None:
                raise last_err or Exception(f"{source_name} unreachable")
            root = ET.fromstring(body)
            for item in root.iter("item"):
                title_el = item.find("title")
                pub_el = item.find("pubDate")
                link_el = item.find("link")
                desc_el = item.find("description")
                content_el = item.find("{http://purl.org/rss/1.0/modules/content/}encoded")
                title = ((title_el.text or "") if title_el is not None else "").strip()
                pub = ((pub_el.text or "") if pub_el is not None else "").strip()
                link = ((link_el.text or "") if link_el is not None else "").strip()
                raw_body = ""
                if content_el is not None and content_el.text:
                    raw_body = content_el.text
                elif desc_el is not None and desc_el.text:
                    raw_body = desc_el.text
                body_clean = re.sub(r"<[^>]+>", " ", raw_body or "")
                body_clean = re.sub(r"&amp;", "&", body_clean)
                body_clean = re.sub(r"&#8217;", "'", body_clean)
                body_clean = re.sub(r"\s+", " ", body_clean).strip()[:1500]
                # Parse date
                date_iso = None
                try:
                    dt = datetime.datetime.strptime(pub[:16], "%a, %d %b %Y")
                    date_iso = dt.date().isoformat()
                except Exception:
                    pass
                if not (title and date_iso):
                    continue
                try:
                    d = datetime.date.fromisoformat(date_iso)
                except Exception:
                    continue
                if d < cutoff:
                    continue
                items.append({
                    "title": title, "body": body_clean, "link": link,
                    "date": date_iso, "feed_slug": slug, "feed_region": region,
                    "source_name": source_name,
                })
        except Exception as e:
            print(f"  WARN: {source_name} fetch failed ({e})")
        feeds_out.append({
            "slug": slug, "region": region, "source_name": source_name,
            "count": len(items), "items": items,
        })
        total += len(items)
    return {
        "harvested_at": datetime.datetime.utcnow().isoformat() + "Z",
        "cutoff_days": cutoff_days,
        "feeds": feeds_out,
        "total_items": total,
    }


def promote_regional_reports_into_zones(regional_result, zone_map, ne_zone_ids):
    """Turn regional-report items into bait_intel entries for NE zones.
    Uses youtube_harvest's LOCATION_PATTERNS to find zone-attributable spots.
    Falls back to a per-feed default zone set if no location pattern matches
    (e.g., an East End report with no specific spot mention still maps to
    Montauk-cluster zones).
    """
    import youtube_harvest as yh
    loc_re = yh._location_re

    # Default zones per feed slug (used when body has no specific location keyword)
    DEFAULT_ZONES = {
        "east-end":     ["montauk_nearshore", "montauk_rips_striper", "south_shore_li"],
        "rhode-island": ["block_island_striper", "block_island_sound", "narragansett_bay", "watch_hill_reef"],
        "connecticut":  ["western_li_sound", "central_li_sound", "the_race", "plum_gut", "the_triangle"],
        "long-island":  ["south_shore_li", "central_li_sound"],
    }

    def _locations_from_text(text):
        hits = []
        for loc_key, patterns in loc_re.items():
            if any(p.search(text) for p in patterns):
                hits.append(loc_key)
        return hits

    entries = []
    for feed in regional_result["feeds"]:
        for it in feed["items"]:
            combined = (it["title"] + " " + it["body"])
            locs = _locations_from_text(combined)
            zids = []
            for loc in locs:
                for zid in zone_map.get(loc, []):
                    if zid in ne_zone_ids and zid not in zids:
                        zids.append(zid)
            # Fall back to feed default if no specific location hit
            if not zids:
                for zid in DEFAULT_ZONES.get(feed["slug"], []):
                    if zid in ne_zone_ids:
                        zids.append(zid)
            if not zids:
                continue
            entry = {
                "date": it["date"],
                "bait": "regional-report",
                "location": ", ".join(locs)[:120] or feed["source_name"],
                "quote": (it["title"] + " — " + it["body"][:220]).strip()[:400],
                "source": f"{feed['source_name']} · The Fisherman",
                "source_type": "fisherman_regional_auto",
                "link": it["link"],
                "title": it["title"][:200],
                "excerpt": it["body"][:400],
                "zones": zids,
            }
            entries.append(entry)
    return entries


if __name__ == "__main__":
    result = harvest(cutoff_days=14)
    print(f"Total posts (30 fetched): {result['total_posts']}")
    print(f"Northeast forecasts:  {len(result['northeast_posts'])}")
    for p in result["northeast_posts"]:
        print(f"  {p['date']} · {p['title'][:80]}")
    print(f"Mid-Atlantic forecasts: {len(result['midatl_posts'])}")
    for p in result["midatl_posts"]:
        print(f"  {p['date']} · {p['title'][:80]}")

    # Quick zone-attribution smoke test
    zone_map = json.loads((BASE / "data" / "youtube_zone_map.json").read_text()).get("location_to_zones", {})
    ma_ids = {z["id"] for z in json.loads((BASE / "data" / "zones_mid_atlantic.json").read_text()).get("zones", [])}
    ne_ids = {z["id"] for z in json.loads((BASE / "data" / "zones.json").read_text()).get("zones", [])}
    entries, intel = promote_into_zones(result, zone_map, ma_ids, ne_ids)
    print(f"\nBait-intel candidates → NE: {len(entries['ne'])}, MidAtl: {len(entries['midatl'])}")
    print(f"Zones with corroboration → NE: {len(intel['ne'])}, MidAtl: {len(intel['midatl'])}")
    for zid, meta in intel["midatl"].items():
        print(f"  MIDATL {zid}: {meta['source_count']} posts, last {meta['latest_date']}")
