#!/usr/bin/env python3
"""Charter-fleet website harvester (v24.11, 2026-08-27).

Randy: "How come we don't have any info from the charter fish captains that go out
every day, and then they usually post on their website what they catch?"

We DO have 26 YouTube channels (most are charter captains), but charters often post
DAILY trip reports to their websites that never make it to YouTube. This harvester
pulls those website posts as an additional intel source.

Currently wired:
  - **Viking Fleet fishing reports** (Montauk, NE) — daily posts from the party boat
    fleet at fishingreports.vikingfleet.com/category/fishing-reports/. Same
    WordPress theme as their whale-watch reports we already scrape; different
    category URL. Posts headers like "Wed Aug 26 – Montauk Lighthouse Jumbo Porgies"
    with a short trip summary in the card body.
  - **Oregon Inlet Fishing Center** (OBX, Mid-Atl) — WordPress RSS at
    oregon-inlet.com/feed/. Fleet aggregator for dozens of NC charter boats;
    reports posted whenever the fleet has content.

Each source returns:
  {
    added: int,          # new bait_intel entries added
    raw_posts: [dict],   # every parsed post
    fetch_ok: bool,
    region: 'northeast' | 'midatl',
    source_name: str,
  }

Promotion path: for each recent post (≤14 days for scanning, but only entries with
today ≤ 7d publish surface in the UI per Randy's rule), the title + body text is
scanned for location keywords (uses youtube_harvest._location_re) and species
names. Posts that map to at least one zone get promoted into that region's
bait_intel with source_type="charter_auto" so the "Latest report" popup block +
sidebar Bait Intel widget pick them up.
"""
import json
import re
import urllib.request
import urllib.error
import datetime
import pathlib
from html.parser import HTMLParser

BASE = pathlib.Path(__file__).parent

# --- Viking Fleet (Montauk) fishing reports ---
VF_FISH_URL = "https://fishingreports.vikingfleet.com/category/fishing-reports/"
VF_DEFAULT_COORDS = [41.02, -71.85]      # off Montauk, same as whale scraper
VF_DEFAULT_ZONES = [
    "montauk_nearshore",
    "montauk_rips_striper",
    "s_block_nearshore",
    "block_island_sound",
]

# --- Oregon Inlet Fishing Center (OBX) RSS ---
OI_RSS_URL = "https://www.oregon-inlet.com/feed/"
OI_DEFAULT_COORDS = [35.775, -75.535]
OI_DEFAULT_ZONES = [
    "oregon_inlet",
    "diamond_shoals",
    "hatteras_the_point",
]

# --- v24.19 (Randy 2026-08-28) new sources ---
# J&J Sports Fishing (Patchogue LI South Shore tackle-shop blog) — Shopify
# atom feed. Fills the LI South Shore inshore + nearshore gap. Weekly-biweekly
# forecasts + tackle-shop reports; posts named "August fishing forecast" etc.
JJ_ATOM_URL = "https://jjsportsfishing.com/blogs/fishing-reports.atom"
JJ_DEFAULT_COORDS = [40.75, -72.75]  # off Moriches / Shinnecock area
JJ_DEFAULT_ZONES = [
    "south_shore_li",
    "montauk_nearshore",
    "block_island_sound",
]

# OTW Northeast Offshore Fishing Report podcast — same crew as the YouTube
# channel we already scrape, but the podcast RSS lists back further and often
# posts descriptions with more zone-specific text than the video title alone.
# Deduped by (title, date) against YouTube in build-inlined.
OTW_POD_URL = "https://anchor.fm/s/f5b9cfe4/podcast/rss"
OTW_POD_DEFAULT_COORDS = [40.20, -71.40]  # generic NE offshore
OTW_POD_DEFAULT_ZONES = [
    "s_block_nearshore",
    "tuna_ridge",
    "block_canyon",
    "hudson_canyon",
    "atlantis_canyon",
    "montauk_nearshore",
]

# The Fisherman "Next Cast" podcast — weekly discussion + companion audio for
# the regional forecast videos. Rich descriptions from Jim Hutchinson +
# Matt Broderick + Dave Anderson. Complements the WordPress REST API we
# already pull.
NEXTCAST_POD_URL = "https://media.rss.com/the-fisherman-s-next-cast-weekly-podcast-no-13-june-16-2026/feed.xml"
NEXTCAST_DEFAULT_COORDS = [40.60, -73.00]  # generic NE/LI region
NEXTCAST_DEFAULT_ZONES = [
    "s_block_nearshore",
    "montauk_nearshore",
    "montauk_rips_striper",
    "block_island_striper",
    "narragansett_bay",
    "south_shore_li",
]


# --- Viking Fleet HTML parser (h1/h2/h3 + card body) ---
class _VFPostParser(HTMLParser):
    """Extract {title, body} pairs from Viking Fleet fishing-reports page.
    The page uses <h*> tags for card titles and a following <div class="card__description">
    or paragraph text for the body. This is a permissive extractor — we grab
    each header + the next N chars of text until the next header."""
    def __init__(self):
        super().__init__()
        self.posts = []
        self._cur_title = None
        self._cur_body = []
        self._in_title = False
        self._in_body = False
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("h1", "h2", "h3", "h4"):
            # Push previous
            self._flush()
            self._in_title = True
            self._cur_title = ""
            self._cur_body = []
        elif tag == "p" or (tag == "div" and any(k == "class" and "card__description" in (v or "") for k, v in attrs)):
            if self._cur_title is not None:
                self._in_body = True

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3", "h4") and self._in_title:
            self._in_title = False
        elif tag in ("p", "div") and self._in_body:
            self._in_body = False
            self._cur_body.append(" ")

    def handle_data(self, data):
        if self._in_title:
            self._cur_title = (self._cur_title or "") + data
        elif self._in_body and self._cur_title is not None:
            self._cur_body.append(data)

    def _flush(self):
        if self._cur_title:
            title = re.sub(r"\s+", " ", self._cur_title).strip()
            body = re.sub(r"\s+", " ", " ".join(self._cur_body)).strip()
            if title and len(title) > 10:  # skip nav bits like "Home"
                self.posts.append({"title": title, "body": body[:1200]})
        self._cur_title = None
        self._cur_body = []


# Date extraction from Viking Fleet titles — same helper as whale_harvest
_MONTH_MAP = {m: i+1 for i, m in enumerate([
    "jan", "feb", "mar", "apr", "may", "jun",
    "jul", "aug", "sep", "oct", "nov", "dec"])}


def _parse_vf_date(title):
    """Titles look like: 'Wed Aug 26 – Montauk Lighthouse Jumbo Porgies'
    Returns ISO date string using current year, or None."""
    m = re.search(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2})\b", title, re.IGNORECASE)
    if not m:
        return None
    month = _MONTH_MAP.get(m.group(1)[:3].lower())
    day = int(m.group(2))
    year = datetime.date.today().year
    # If parsed date > today by more than 30 days, subtract a year (calendar boundary)
    try:
        d = datetime.date(year, month, day)
        if (d - datetime.date.today()).days > 30:
            d = datetime.date(year - 1, month, day)
        return d.isoformat()
    except ValueError:
        return None


def _fetch_with_retry(url, source_label, timeout=20):
    """v24.105 — small helper for the two non-RSS charter fetchers.
    3 attempts, [3s, 10s] backoff. Returns (body_text, err) — body is None on failure."""
    import time as _time
    backoffs = [3, 10]
    last_err = None
    for attempt in range(3):
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; FishFinderBot/1.0; charter-fishing-harvester)"
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace"), None
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_err = e
            if isinstance(e, urllib.error.HTTPError) and e.code == 404:
                break
            if attempt < len(backoffs):
                _time.sleep(backoffs[attempt])
    print(f"  WARN: {source_label} fetch failed after 3 attempts ({last_err})")
    return None, last_err


def harvest_viking_fleet_fishing():
    """Fetch Viking Fleet fishing reports page + parse cards. Returns dict."""
    print("Harvesting charter fishing reports — Viking Fleet (Montauk, NE)...")
    html, err = _fetch_with_retry(VF_FISH_URL, "VF fishing")
    if html is None:
        return {"added": 0, "raw_posts": [], "fetch_ok": False,
                "region": "northeast", "source_name": "Viking Fleet fishing reports"}
    parser = _VFPostParser()
    try:
        parser.feed(html)
        parser._flush()
    except Exception as e:
        print(f"  WARN: VF parse failed ({e})")
        return {"added": 0, "raw_posts": [], "fetch_ok": True,
                "region": "northeast", "source_name": "Viking Fleet fishing reports",
                "parse_error": str(e)}
    posts = parser.posts[:20]  # cap
    # Filter: skip nav/header cards. Real trip cards start with a weekday + date.
    real = []
    for p in posts:
        date_iso = _parse_vf_date(p["title"])
        if date_iso:
            real.append({**p, "date": date_iso,
                         "link": VF_FISH_URL})   # index URL — individual post links are complex
    print(f"  Parsed {len(posts)} cards, {len(real)} dated fishing reports")
    return {
        "added": 0, "raw_posts": real, "fetch_ok": True,
        "region": "northeast", "source_name": "Viking Fleet fishing reports (Montauk)",
        "default_coords": VF_DEFAULT_COORDS, "default_zones": VF_DEFAULT_ZONES,
    }


def harvest_oregon_inlet():
    """Fetch Oregon Inlet Fishing Center RSS. Returns dict."""
    print("Harvesting charter fishing reports — Oregon Inlet Fishing Center (OBX, Mid-Atl)...")
    xml_text, err = _fetch_with_retry(OI_RSS_URL, "Oregon Inlet")
    if xml_text is None:
        return {"added": 0, "raw_posts": [], "fetch_ok": False,
                "region": "midatl", "source_name": "Oregon Inlet Fishing Center"}
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(xml_text)
    except Exception as e:
        return {"added": 0, "raw_posts": [], "fetch_ok": True,
                "region": "midatl", "source_name": "Oregon Inlet Fishing Center",
                "parse_error": str(e)}
    items = []
    for item in root.iter("item"):
        title_el = item.find("title")
        pub_el = item.find("pubDate")
        link_el = item.find("link")
        desc_el = item.find("description")
        content_el = item.find("{http://purl.org/rss/1.0/modules/content/}encoded")
        title = (title_el.text or "").strip() if title_el is not None else ""
        pub = (pub_el.text or "").strip() if pub_el is not None else ""
        link = (link_el.text or "").strip() if link_el is not None else ""
        body = ""
        if content_el is not None and content_el.text:
            body = content_el.text
        elif desc_el is not None and desc_el.text:
            body = desc_el.text
        body_clean = re.sub(r"<[^>]+>", " ", body)
        body_clean = re.sub(r"\s+", " ", body_clean).strip()[:1200]
        date_iso = None
        try:
            dt = datetime.datetime.strptime(pub[:16], "%a, %d %b %Y")
            date_iso = dt.date().isoformat()
        except Exception:
            pass
        if title and date_iso:
            items.append({"title": title, "body": body_clean, "date": date_iso, "link": link})
    print(f"  Parsed {len(items)} RSS items")
    return {
        "added": 0, "raw_posts": items, "fetch_ok": True,
        "region": "midatl", "source_name": "Oregon Inlet Fishing Center (OBX)",
        "default_coords": OI_DEFAULT_COORDS, "default_zones": OI_DEFAULT_ZONES,
    }


def _fetch_rss_items(url, source_label, default_ua="Mozilla/5.0 (compatible; FishFinderBot/1.0)"):
    """Fetch + parse a generic RSS/Atom feed. Returns list of item dicts:
    {title, body, date (iso), link}. Handles both RSS <item> and Atom <entry>.

    v24.55: added gzip decode. Some CDN-fronted feeds (Salt Strong, some
    Cloudflare-proxied WordPress) return gzip-compressed bodies regardless
    of Accept-Encoding header. Handle transparently.
    """
    # v24.105 — 3-attempt retry with backoff [3s, 10s]. This helper is called
    # by 15+ charter/podcast harvesters per build; a single-shot 429 or
    # timeout previously dropped one whole source for the day.
    import time as _time
    backoffs = [3, 10]
    raw = None
    encoding = ""
    last_err = None
    for attempt in range(3):
        req = urllib.request.Request(url, headers={
            "User-Agent": default_ua,
            "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml",
            "Accept-Encoding": "gzip, identity",
        })
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read()
                encoding = (r.headers.get("Content-Encoding") or "").lower()
            break
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_err = e
            # 404 = URL rotation, don't retry
            if isinstance(e, urllib.error.HTTPError) and e.code == 404:
                break
            if attempt < len(backoffs):
                _time.sleep(backoffs[attempt])
    if raw is None:
        print(f"  WARN: {source_label} fetch failed after 3 attempts ({last_err})")
        return None
    if encoding == "gzip" or raw[:2] == b"\x1f\x8b":
        import gzip
        try:
            raw = gzip.decompress(raw)
        except Exception as _gz:
            print(f"  WARN: {source_label} gzip decode failed ({_gz})")
    xml_text = raw.decode("utf-8", errors="replace")
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(xml_text)
    except Exception as e:
        print(f"  WARN: {source_label} XML parse failed ({e})")
        return None
    items = []
    ATOM = "{http://www.w3.org/2005/Atom}"
    # RSS <item> path
    for item in root.iter("item"):
        title_el = item.find("title")
        pub_el = item.find("pubDate")
        link_el = item.find("link")
        desc_el = item.find("description")
        content_el = item.find("{http://purl.org/rss/1.0/modules/content/}encoded")
        title = ((title_el.text or "") if title_el is not None else "").strip()
        pub = ((pub_el.text or "") if pub_el is not None else "").strip()
        link = ((link_el.text or "") if link_el is not None else "").strip()
        body = ""
        if content_el is not None and content_el.text:
            body = content_el.text
        elif desc_el is not None and desc_el.text:
            body = desc_el.text
        body_clean = re.sub(r"<[^>]+>", " ", body or "")
        body_clean = re.sub(r"\s+", " ", body_clean).strip()[:1200]
        date_iso = None
        try:
            dt = datetime.datetime.strptime(pub[:16], "%a, %d %b %Y")
            date_iso = dt.date().isoformat()
        except Exception:
            pass
        if title and date_iso:
            items.append({"title": title, "body": body_clean, "date": date_iso, "link": link})
    # Atom <entry> path (used by J&J Shopify atom)
    if not items:
        for entry in root.iter(f"{ATOM}entry"):
            title_el = entry.find(f"{ATOM}title")
            pub_el = entry.find(f"{ATOM}published") or entry.find(f"{ATOM}updated")
            link_el = entry.find(f"{ATOM}link")
            content_el = entry.find(f"{ATOM}content") or entry.find(f"{ATOM}summary")
            title = ((title_el.text or "") if title_el is not None else "").strip()
            pub = ((pub_el.text or "") if pub_el is not None else "").strip()
            link = (link_el.get("href", "") if link_el is not None else "").strip()
            body = (content_el.text or "") if content_el is not None else ""
            body_clean = re.sub(r"<[^>]+>", " ", body or "")
            body_clean = re.sub(r"\s+", " ", body_clean).strip()[:1200]
            date_iso = pub[:10] if len(pub) >= 10 and pub[4] == "-" else None
            if title and date_iso:
                items.append({"title": title, "body": body_clean, "date": date_iso, "link": link})
    return items


def harvest_jj_sports_fishing():
    """v24.19: J&J Sports Fishing (Patchogue LI tackle-shop). Shopify Atom feed."""
    print("Harvesting charter/blog reports — J&J Sports Fishing (Patchogue LI, tackle-shop blog)...")
    items = _fetch_rss_items(JJ_ATOM_URL, "J&J Sports Fishing")
    if items is None:
        return {"added": 0, "raw_posts": [], "fetch_ok": False,
                "region": "northeast", "source_name": "J&J Sports Fishing (Patchogue LI)"}
    # Filter out generic shop-front title entries (Shopify includes the blog
    # index page as the first entry — we want individual posts only)
    real = [it for it in items if not re.search(r"J\s*&\s*J\s+Sports\s+Inc\.\s*[-–]\s*Bait", it["title"], re.IGNORECASE)]
    print(f"  Parsed {len(real)} posts from J&J Sports Fishing atom")
    return {
        "added": 0, "raw_posts": real, "fetch_ok": True,
        "region": "northeast", "source_name": "J&J Sports Fishing (Patchogue LI)",
        "default_coords": JJ_DEFAULT_COORDS, "default_zones": JJ_DEFAULT_ZONES,
    }


def harvest_otw_offshore_podcast():
    """v24.19: OTW Northeast Offshore Report podcast RSS. Duplicates the
    YouTube channel content in title but the podcast description is often
    richer. Dedupe by (title, date) at promotion time against YT."""
    print("Harvesting charter/blog reports — OTW Northeast Offshore Fishing Report podcast...")
    items = _fetch_rss_items(OTW_POD_URL, "OTW Northeast Offshore podcast")
    if items is None:
        return {"added": 0, "raw_posts": [], "fetch_ok": False,
                "region": "northeast", "source_name": "OTW Northeast Offshore Report podcast"}
    print(f"  Parsed {len(items)} podcast episodes")
    return {
        "added": 0, "raw_posts": items, "fetch_ok": True,
        "region": "northeast", "source_name": "OTW Northeast Offshore Report podcast",
        "default_coords": OTW_POD_DEFAULT_COORDS, "default_zones": OTW_POD_DEFAULT_ZONES,
    }


def harvest_nextcast_podcast():
    """v24.19: The Fisherman 'Next Cast' weekly podcast RSS."""
    print("Harvesting charter/blog reports — The Fisherman Next Cast podcast...")
    items = _fetch_rss_items(NEXTCAST_POD_URL, "The Fisherman Next Cast podcast")
    if items is None:
        return {"added": 0, "raw_posts": [], "fetch_ok": False,
                "region": "northeast", "source_name": "The Fisherman Next Cast podcast"}
    print(f"  Parsed {len(items)} podcast episodes")
    # Route into NE or Mid-Atl by title (many episodes are the NJ/DE regional
    # forecast which belongs to Mid-Atl).
    ne_items = []
    ma_items = []
    for it in items:
        t = it["title"].lower()
        if any(k in t for k in ("new jersey", "nj", "delaware", "delaware bay", "chesapeake", "obx", "outer banks", "hatteras")):
            ma_items.append(it)
        else:
            ne_items.append(it)
    print(f"    → {len(ne_items)} NE episodes · {len(ma_items)} Mid-Atl episodes")
    # Return NE bucket here; Mid-Atl slot comes back from a second call.
    return {
        "added": 0, "raw_posts": ne_items, "fetch_ok": True,
        "region": "northeast", "source_name": "The Fisherman Next Cast podcast (NE)",
        "default_coords": NEXTCAST_DEFAULT_COORDS, "default_zones": NEXTCAST_DEFAULT_ZONES,
        "_midatl_items": ma_items,   # build-inlined can promote these separately
    }


# v24.35 (Randy 2026-08-29): Gulf harvesters — three Gulf-region RSS feeds
# proven working: Great Days Outdoors NW-FL Fishing Report, Blue Water Charter
# 30A blog, Northwest Florida Fishing Report podcast. Each returns items with
# our standard shape (title/body/date/link); build-inlined.py routes them into
# zones_gulf.json.bait_intel via promote_charter_reports_into_zones().
GREAT_DAYS_NWFL_URL = "https://greatdaysoutdoors.com/category/fishing-report/northwest-florida-fishing-report/feed/"
BLUE_WATER_30A_URL = "https://www.bluewatercharter30a.com/fishing-reports?format=rss"
NW_FL_PODCAST_URL = "https://northwestfloridafishingreport.libsyn.com/rss"
# Default coords for Gulf catch-all when a post doesn't specify a spot: Perdido Pass
GULF_DEFAULT_COORDS = [30.267, -87.556]
# Common Gulf zone attributions when the promoter can't pin an exact spot
GULF_DEFAULT_ZONES = ["perdido_pass", "escambia_reefs", "pensacola_pass"]

def harvest_great_days_nwfl():
    """v24.35: Great Days Outdoors — Northwest Florida Fishing Report (weekly RSS)."""
    print("Harvesting charter/blog reports — Great Days Outdoors NW Florida Fishing Report...")
    items = _fetch_rss_items(GREAT_DAYS_NWFL_URL, "Great Days NW-FL Fishing Report")
    if items is None:
        return {"added": 0, "raw_posts": [], "fetch_ok": False,
                "region": "gulf", "source_name": "Great Days Outdoors NW Florida Fishing Report"}
    print(f"  Parsed {len(items)} weekly reports")
    return {
        "added": 0, "raw_posts": items, "fetch_ok": True,
        "region": "gulf", "source_name": "Great Days Outdoors NW Florida Fishing Report",
        "default_coords": GULF_DEFAULT_COORDS, "default_zones": GULF_DEFAULT_ZONES,
    }

def harvest_blue_water_30a():
    """v24.35: Blue Water Charter 30A (Santa Rosa Beach FL) — Squarespace RSS."""
    print("Harvesting charter/blog reports — Blue Water Charter 30A (Santa Rosa Beach FL)...")
    items = _fetch_rss_items(BLUE_WATER_30A_URL, "Blue Water 30A")
    if items is None:
        return {"added": 0, "raw_posts": [], "fetch_ok": False,
                "region": "gulf", "source_name": "Blue Water Charter 30A"}
    print(f"  Parsed {len(items)} charter reports")
    return {
        "added": 0, "raw_posts": items, "fetch_ok": True,
        "region": "gulf", "source_name": "Blue Water Charter 30A",
        "default_coords": [30.297, -86.128],  # Santa Rosa Beach / 30A area
        "default_zones": ["destin_edge", "destin_steps", "east_pass_destin"],
    }

def harvest_nw_florida_podcast():
    """v24.35: Northwest Florida Fishing Report podcast (Libsyn RSS, 100 episodes)."""
    print("Harvesting charter/blog reports — Northwest Florida Fishing Report podcast...")
    items = _fetch_rss_items(NW_FL_PODCAST_URL, "NW Florida Fishing Report podcast")
    if items is None:
        return {"added": 0, "raw_posts": [], "fetch_ok": False,
                "region": "gulf", "source_name": "NW Florida Fishing Report podcast"}
    print(f"  Parsed {len(items)} podcast episodes")
    return {
        "added": 0, "raw_posts": items, "fetch_ok": True,
        "region": "gulf", "source_name": "NW Florida Fishing Report podcast",
        "default_coords": GULF_DEFAULT_COORDS, "default_zones": GULF_DEFAULT_ZONES,
    }


# v24.43 (Randy 2026-08-31): Zone 5 South Florida (Atlantic Pass A) — charter
# blog/podcast RSS feeds. Florida Sportsman is the flagship regional print; Salt
# Life News and Sport Fishing Magazine's FL feed round out the mix. All parsed
# with _fetch_rss_items; each returns items with our standard shape.
# v24.43 known-dead: FL Sportsman's main site returns 404 on every /feed path
# variant we probed. Keeping the function stubbed as no-op — one-line URL swap
# will re-enable it if their team ever publishes an RSS endpoint.
FLORIDA_SPORTSMAN_URL = None
SPORT_FISHING_MAG_URL = "https://www.sportfishingmag.com/feed/"
# Default coords for S. Florida catch-all: Islamorada (Bud N' Mary's)
SFL_DEFAULT_COORDS = [24.923, -80.622]
SFL_DEFAULT_ZONES = ["alligator_reef", "islamorada_hump", "molasses_reef"]

def harvest_florida_sportsman():
    """v24.43 STUBBED: FL Sportsman doesn't expose a working RSS endpoint on
    their main site (probed /feed, /rss, /category/*/feed — all 404). Keeping
    the function so a future URL fix is a one-line change."""
    if not FLORIDA_SPORTSMAN_URL:
        return {"added": 0, "raw_posts": [], "fetch_ok": False,
                "region": "south_florida", "source_name": "Florida Sportsman (no working RSS)"}
    items = _fetch_rss_items(FLORIDA_SPORTSMAN_URL, "Florida Sportsman")
    if items is None:
        return {"added": 0, "raw_posts": [], "fetch_ok": False,
                "region": "south_florida", "source_name": "Florida Sportsman"}
    return {
        "added": 0, "raw_posts": items, "fetch_ok": True,
        "region": "south_florida", "source_name": "Florida Sportsman",
        "default_coords": SFL_DEFAULT_COORDS, "default_zones": SFL_DEFAULT_ZONES,
    }

def harvest_sport_fishing_mag():
    """v24.43: Sport Fishing Magazine RSS — broad offshore/inshore content.
    Not FL-exclusive but heavy S. FL charter coverage (Keys tournaments, Palm
    Beach sails, Islamorada Hump)."""
    print("Harvesting charter/blog reports — Sport Fishing Magazine...")
    items = _fetch_rss_items(SPORT_FISHING_MAG_URL, "Sport Fishing Magazine")
    if items is None:
        return {"added": 0, "raw_posts": [], "fetch_ok": False,
                "region": "south_florida", "source_name": "Sport Fishing Magazine"}
    print(f"  Parsed {len(items)} Sport Fishing posts")
    return {
        "added": 0, "raw_posts": items, "fetch_ok": True,
        "region": "south_florida", "source_name": "Sport Fishing Magazine",
        "default_coords": SFL_DEFAULT_COORDS, "default_zones": SFL_DEFAULT_ZONES,
    }


# v24.50 (Randy 2026-09-01) — MULTI-REGION charter/publication harvesters.
# Coastal Angler Magazine + Saltwater Sportsman publish across the whole US
# east/south coast; each post can be about NE, Mid-Atl, SE, Gulf, or S. FL.
# Tagged region="multi" so the promoter routes each post to whichever region's
# zones the post's location keywords actually match, instead of forcing all
# posts into one region's zone set.
COASTAL_ANGLER_URL = "https://coastalanglermag.com/feed/"
SALTWATER_SPORTSMAN_URL = "https://www.saltwatersportsman.com/feed/"

def harvest_coastal_angler():
    """v24.50: Coastal Angler Magazine — multi-region fishing publication with
    heavy FL / Gulf / SE coverage. 20 items per feed poll, updates daily."""
    print("Harvesting charter/blog reports — Coastal Angler Magazine...")
    items = _fetch_rss_items(COASTAL_ANGLER_URL, "Coastal Angler")
    if items is None:
        return {"added": 0, "raw_posts": [], "fetch_ok": False,
                "region": "multi", "source_name": "Coastal Angler Magazine"}
    print(f"  Parsed {len(items)} Coastal Angler posts")
    return {
        "added": 0, "raw_posts": items, "fetch_ok": True,
        "region": "multi", "source_name": "Coastal Angler Magazine",
        # multi-region has no meaningful default_zones — posts without
        # location matches get skipped rather than mis-attributed.
    }

def harvest_saltwater_sportsman():
    """v24.50: Saltwater Sportsman magazine — broad national coverage with
    strong regional reports. Weekly cadence."""
    print("Harvesting charter/blog reports — Saltwater Sportsman...")
    items = _fetch_rss_items(SALTWATER_SPORTSMAN_URL, "Saltwater Sportsman")
    if items is None:
        return {"added": 0, "raw_posts": [], "fetch_ok": False,
                "region": "multi", "source_name": "Saltwater Sportsman"}
    print(f"  Parsed {len(items)} Saltwater Sportsman posts")
    return {
        "added": 0, "raw_posts": items, "fetch_ok": True,
        "region": "multi", "source_name": "Saltwater Sportsman",
    }


# ============================================================================
# v24.55 (Randy 2026-08-31): Dedicated regional harvesters — one per underfed
# region. Wired after probing feed URLs; each is a working RSS at commit time.
# Combined with the YT auto-heat (also v24.55), these give the four cold
# regions their own captain-intel pipelines instead of relying only on the
# NE-focused multi-region publications.
# ============================================================================

# Louisiana Sportsman — Gulf regional flagship. WordPress RSS, ~70 KB payload,
# heavy state-specific news + fishing reports. Anchor at Venice/Grand Isle.
LOUISIANA_SPORTSMAN_URL = "https://www.louisianasportsman.com/feed/"
GULF_LA_DEFAULT_COORDS = [29.264, -89.353]      # off Venice, LA
GULF_LA_DEFAULT_ZONES = ["venice_la_offshore", "midnight_lump", "grand_isle_offshore"]

def harvest_louisiana_sportsman():
    """v24.55: Louisiana Sportsman — Gulf regional flagship, dedicated LA
    coverage (Venice, Grand Isle, Mississippi River deltas). WordPress feed."""
    print("Harvesting charter/blog reports — Louisiana Sportsman...")
    items = _fetch_rss_items(LOUISIANA_SPORTSMAN_URL, "Louisiana Sportsman")
    if items is None:
        return {"added": 0, "raw_posts": [], "fetch_ok": False,
                "region": "gulf", "source_name": "Louisiana Sportsman"}
    print(f"  Parsed {len(items)} Louisiana Sportsman posts")
    return {
        "added": 0, "raw_posts": items, "fetch_ok": True,
        "region": "gulf", "source_name": "Louisiana Sportsman",
        "default_coords": GULF_LA_DEFAULT_COORDS, "default_zones": GULF_LA_DEFAULT_ZONES,
    }


# Carolina Sportsman — regional pub covering NC + SC. Range spans north
# (Mid-Atl per our schema: NC north of Cape Lookout) and south (SE: Wilmington,
# Charleston). Route via multi-region so the classifier assigns per-post.
CAROLINA_SPORTSMAN_URL = "https://www.carolinasportsman.com/feed/"

def harvest_carolina_sportsman():
    """v24.55: Carolina Sportsman — NC + SC coverage. Multi-region because
    the pub straddles our Mid-Atl / South Atlantic split (OBX vs Charleston)."""
    print("Harvesting charter/blog reports — Carolina Sportsman...")
    items = _fetch_rss_items(CAROLINA_SPORTSMAN_URL, "Carolina Sportsman")
    if items is None:
        return {"added": 0, "raw_posts": [], "fetch_ok": False,
                "region": "multi", "source_name": "Carolina Sportsman"}
    print(f"  Parsed {len(items)} Carolina Sportsman posts")
    return {
        "added": 0, "raw_posts": items, "fetch_ok": True,
        "region": "multi", "source_name": "Carolina Sportsman",
    }


# Chesapeake Bay Magazine — Fishing tag feed. Squarely Mid-Atl (Chesapeake
# Bay + tributaries). Content mix: regs, features, catch reports.
CHESAPEAKE_BAY_MAG_URL = "https://www.chesapeakebaymagazine.com/tag/fishing/feed/"
MIDATL_CBM_DEFAULT_COORDS = [37.9, -76.15]     # mid-Chesapeake
MIDATL_CBM_DEFAULT_ZONES = ["chesapeake_bay_mouth", "cbbt", "cape_charles_offshore"]

def harvest_chesapeake_bay_magazine():
    """v24.55: Chesapeake Bay Magazine (Fishing tag) — Mid-Atl regional
    flagship. Category feed only, so content is fishing-focused."""
    print("Harvesting charter/blog reports — Chesapeake Bay Magazine...")
    items = _fetch_rss_items(CHESAPEAKE_BAY_MAG_URL, "Chesapeake Bay Magazine")
    if items is None:
        return {"added": 0, "raw_posts": [], "fetch_ok": False,
                "region": "midatl", "source_name": "Chesapeake Bay Magazine"}
    print(f"  Parsed {len(items)} Chesapeake Bay Magazine posts")
    return {
        "added": 0, "raw_posts": items, "fetch_ok": True,
        "region": "midatl", "source_name": "Chesapeake Bay Magazine",
        "default_coords": MIDATL_CBM_DEFAULT_COORDS,
        "default_zones": MIDATL_CBM_DEFAULT_ZONES,
    }


# Salt Strong Fishing Club — FL-focused inshore community (snook, redfish,
# tarpon, seatrout). Content covers Atlantic Coast + Gulf Coast FL. Route
# via multi so the classifier assigns per-post (south_florida vs gulf).
SALT_STRONG_URL = "https://www.saltstrong.com/feed/"

def harvest_salt_strong():
    """v24.55: Salt Strong Fishing Club — FL-heavy inshore content. Covers
    both coasts (Atlantic S.FL + Gulf FL) so route multi-region."""
    print("Harvesting charter/blog reports — Salt Strong Fishing Club...")
    items = _fetch_rss_items(SALT_STRONG_URL, "Salt Strong")
    if items is None:
        return {"added": 0, "raw_posts": [], "fetch_ok": False,
                "region": "multi", "source_name": "Salt Strong Fishing Club"}
    print(f"  Parsed {len(items)} Salt Strong posts")
    return {
        "added": 0, "raw_posts": items, "fetch_ok": True,
        "region": "multi", "source_name": "Salt Strong Fishing Club",
    }


def promote_charter_reports_into_zones(harvests, zone_map, region_zone_ids, cutoff_days=14):
    """Given a list of harvest results, promote ≤cutoff-day posts into bait_intel.
    Uses youtube_harvest._location_re to detect location mentions; falls back
    to source's default_zones if no location keywords hit.

    Args:
      harvests: list of harvest result dicts (from harvest_* above)
      zone_map: dict of location_key → [zone_ids] (youtube_zone_map.json)
      region_zone_ids: {"northeast": {ids}, "midatl": {ids}}
      cutoff_days: only consider posts ≤N days old

    Returns:
      {"northeast": [entries], "midatl": [entries]}
    """
    import youtube_harvest as yh
    loc_re = yh._location_re
    today = datetime.date.today()
    cutoff = today - datetime.timedelta(days=cutoff_days)

    def _locations_from_text(text):
        hits = []
        for loc_key, patterns in loc_re.items():
            if any(p.search(text) for p in patterns):
                hits.append(loc_key)
        return hits

    def _zids_for(locs, region):
        z = []
        for loc in locs:
            for zid in zone_map.get(loc, []):
                if zid in region_zone_ids.get(region, set()) and zid not in z:
                    z.append(zid)
        return z

    by_region = {"northeast": [], "midatl": [], "gulf": [], "south_florida": [], "south_atlantic": []}
    # v24.51 (Randy 2026-09-01) — smarter multi-region routing.
    # v24.50's first attempt routed a Bahamas post to Northeast because the
    # word "long island" appeared incidentally. Root cause: pure location-
    # keyword matching is noisy — common English phrases collide with
    # unrelated geographies. Fix: two-stage router.
    #   1. fisherman_harvest._classify_region() on title+body first. This
    #      looks for STRONG regional signals (state names, iconic geography)
    #      and returns None for generic posts. Much less noisy.
    #   2. If a region is classified AND ≥2 zone matches within that
    #      region are found, promote. Otherwise skip — a single incidental
    #      match doesn't earn a bait_intel entry.
    try:
        import fisherman_harvest as _fh
        _classify = _fh._classify_region
    except Exception:
        _classify = lambda _t: None

    def _route_multi(post_title, post_body):
        combined = (post_title or "") + " " + (post_body or "")
        # Two-tier confidence routing:
        #
        #   High confidence: TITLE classifies as a specific region AND
        #     ≥1 zone match in that region. Title carries the strongest
        #     signal about what a post is genuinely about.
        #
        #   Medium confidence: title classifies as None but BODY classifies
        #     as a region AND ≥2 zone matches in that region. This catches
        #     regional trip reports whose titles are generic ("September's
        #     Choice") but whose bodies discuss specific spots. ≥2 matches
        #     avoids the failure mode we saw with a Bahamas trip whose body
        #     mentioned Miami as departure inlet — that would have been 1
        #     match and correctly gets rejected.
        #
        #   Everything else: skip. Better to under-promote than pollute
        #     bait_intel with wrong region attributions.
        title_region = _classify(post_title or "")
        body_region = _classify(combined[:2000]) if not title_region else None
        region = title_region or body_region
        if not region or region not in region_zone_ids:
            return None, []
        locs = _locations_from_text(combined)
        zids = []
        for loc in locs:
            for zid in zone_map.get(loc, []):
                if zid in region_zone_ids[region] and zid not in zids:
                    zids.append(zid)
        min_zones = 1 if title_region else 2
        if len(zids) < min_zones:
            return None, []
        return region, zids

    for h in harvests:
        region = h.get("region")
        source_name = h.get("source_name", "Charter")
        posts = h.get("raw_posts", [])

        # --- MULTI-REGION mode (v24.50) — route each post independently ---
        if region == "multi":
            for post in posts:
                try:
                    d = datetime.date.fromisoformat(post["date"])
                except (KeyError, ValueError):
                    continue
                if d < cutoff or d > today + datetime.timedelta(days=1):
                    continue
                routed_region, zids = _route_multi(post["title"], post.get("body", ""))
                if not routed_region or not zids:
                    continue  # post is generic (no matching zones in any region) — skipped
                if routed_region not in by_region:
                    by_region[routed_region] = []
                quote = (post.get("body") or post["title"]).strip()[:280]
                by_region[routed_region].append({
                    "date": post["date"],
                    "bait": "captain_report",
                    "location": source_name,
                    "species_feeding": None,
                    "source": source_name,
                    "source_type": "charter_auto",
                    "quote": quote,
                    "link": post.get("link", ""),
                    "zones": zids,
                })
            continue  # done with this multi-region harvest

        # --- SINGLE-REGION mode (original behavior) ---
        if region not in by_region:
            by_region[region] = []  # future-proof for southeast/socal/pnw
        default_zones = [z for z in (h.get("default_zones") or [])
                         if z in region_zone_ids.get(region, set())]
        for post in posts:
            try:
                d = datetime.date.fromisoformat(post["date"])
            except (KeyError, ValueError):
                continue
            if d < cutoff or d > today + datetime.timedelta(days=1):
                continue
            combined = post["title"] + " " + post.get("body", "")
            locs = _locations_from_text(combined)
            zids = _zids_for(locs, region)
            if not zids:
                zids = default_zones  # source's default operating area
            if not zids:
                continue
            quote = (post.get("body") or post["title"]).strip()[:280]
            by_region[region].append({
                "date": post["date"],
                "bait": "captain_report",
                "location": source_name,
                "species_feeding": None,
                "source": source_name,
                "source_type": "charter_auto",
                "quote": quote,
                "link": post.get("link", ""),
                "zones": zids,
            })
    return by_region


if __name__ == "__main__":
    vf = harvest_viking_fleet_fishing()
    print(f"  → {len(vf.get('raw_posts', []))} recent VF reports")
    for p in vf["raw_posts"][:5]:
        print(f"      {p['date']} · {p['title'][:80]}")
    oi = harvest_oregon_inlet()
    print(f"  → {len(oi.get('raw_posts', []))} recent OI reports")
    for p in oi["raw_posts"][:5]:
        print(f"      {p['date']} · {p['title'][:80]}")

    # Promote test
    zone_map = json.loads((BASE / "data" / "youtube_zone_map.json").read_text()).get("location_to_zones", {})
    ne_ids = {z["id"] for z in json.loads((BASE / "data" / "zones.json").read_text()).get("zones", [])}
    ma_ids = {z["id"] for z in json.loads((BASE / "data" / "zones_mid_atlantic.json").read_text()).get("zones", [])}
    region_ids = {"northeast": ne_ids, "midatl": ma_ids}
    entries = promote_charter_reports_into_zones([vf, oi], zone_map, region_ids)
    print(f"\nPromotable entries: NE={len(entries['northeast'])}, MidAtl={len(entries['midatl'])}")
    for e in entries["northeast"][:3]:
        print(f"  NE {e['date']} · {e['location']} → {e['zones']}")
    for e in entries["midatl"][:3]:
        print(f"  MA {e['date']} · {e['location']} → {e['zones']}")
