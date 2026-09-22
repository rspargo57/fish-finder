#!/usr/bin/env python3
"""Whale/dolphin sighting harvester — daily-fresh signal for Fish Finder.

Randy 2026-07-30: "The whales and dolphins sightings are not updating, we need
that to update every day."

Scrapes the CRESLI / Viking Fleet whale-watch report feed
(https://fishingreports.vikingfleet.com/category/whale-watching-montauk/)
which posts a report after nearly every daily 2 PM whale-watch departure out
of Montauk. Each report typically lists species observed, counts, and rough
location. The scraper pulls the last N days of reports, parses date + species
+ location, and merges any NEW sightings into zones.json under
`whale_sightings` (deduped by date).

Called from build-inlined.py at nightly build time so tomorrow's picks always
reflect the latest whale activity. Existing sightings are never removed by
this script (Captain's data-quality rules — the archive is append-only). The
Captain can still manually add/edit whale entries; this just fills in what
CRESLI/Viking Fleet posts.

Design notes:
- Uses only stdlib (urllib) + a Mozilla User-Agent — no scraper deps.
- Falls back gracefully if the site is down (returns 0 new sightings, doesn't
  break the build).
- Coordinates are heuristic: reports rarely give a specific lat/lon, so we
  place the pin south of Montauk lighthouse (a reasonable default for the
  boat's typical grounds — 41.02°N, -71.85°W) unless the report mentions
  a specific location keyword.
- Zone attribution defaults to the four zones a Montauk-area whale sighting
  most likely tags: montauk_nearshore, montauk_rips_striper,
  s_block_nearshore, block_island_sound. Adjust if the model consistently
  over- or under-boosts.
"""
import json
import pathlib
import re
import urllib.request
import urllib.error
import datetime
from html.parser import HTMLParser

FEED_URL = "https://fishingreports.vikingfleet.com/category/whale-watching-montauk/"
ZONES_PATH = pathlib.Path("/root/fish-finder/data/zones.json")

# Mid-Atlantic whale-watch operator (v24.4, 2026-08-23):
# Bill McKim's Jersey Shore Whale Watching Tour — publishes near-daily trip reports
# via a WordPress RSS feed (analog of CRESLI/Viking Fleet for Zone 2). Operates
# out of Belmar/Point Pleasant area, works waters offshore of Manasquan Inlet.
MIDATL_FEED_URL = "https://jerseyshorewhalewatchingtour.com/feed/"
MIDATL_ZONES_PATH = pathlib.Path("/root/fish-finder/data/zones_mid_atlantic.json")

# Default Mid-Atl whale coords + zones — Bill McKim's boat works ~10 miles E of
# Point Pleasant / Manasquan Inlet. That puts sightings above / just SE of
# Sea Girt Reef, spilling toward Barnegat Ridge and Manasquan Ridge.
MIDATL_DEFAULT_COORDS = [40.10, -73.90]
MIDATL_DEFAULT_ZONES = [
    "sea_girt_reef",
    "manasquan_ridge",
    "barnegat_ridge",
]

# Default coords when a report doesn't name a specific spot — south of Montauk
# lighthouse where the 2 PM whale-watch boat typically works. This is also a
# short run for Randy's boat (28 nm one-way).
DEFAULT_COORDS = [41.02, -71.85]
DEFAULT_ZONES = [
    "montauk_nearshore",
    "montauk_rips_striper",
    "s_block_nearshore",
    "block_island_sound",
]

# Location-keyword → coord+zone overrides. Keys are lowercase substrings.
LOCATION_HINTS = {
    "block island": ([41.10, -71.60], ["block_island_sound", "s_block_nearshore", "block_island_striper"]),
    "atlantis":     ([39.93, -70.20], ["atlantis_canyon"]),
    "hudson":       ([39.72, -72.30], ["hudson_canyon"]),
    "coxes":        ([40.98, -71.30], ["coxes_ledge_se"]),
    "tuna ridge":   ([40.75, -71.30], ["tuna_ridge"]),
    "east atlantis":([40.05, -70.10], ["atlantis_canyon"]),
    "20 mi":        ([40.75, -71.85], ["montauk_nearshore", "s_block_nearshore"]),
    "20 miles":     ([40.75, -71.85], ["montauk_nearshore", "s_block_nearshore"]),
    "far offshore": ([40.60, -71.60], ["s_block_nearshore", "coxes_ledge_se"]),
}

SPECIES_KEYWORDS = {
    "humpback":            "humpback",
    "finback":             "finback",
    "fin whale":           "finback",
    "minke":               "minke",
    "sei":                 "sei",
    "right whale":         "right whale",
    "sperm whale":         "sperm whale",
    "bottlenose dolphin":  "bottlenose dolphin",
    "common dolphin":      "common dolphin",
    "short-beaked common": "common dolphin",
    "risso":               "risso's dolphin",
    "pilot whale":         "pilot whale",
    "porpoise":            "harbor porpoise",
}


def parse_cards(html):
    """Extract {title, content} pairs from the Viking Fleet blog cards.

    The site uses a custom WordPress theme with `card__title` and
    `card__description` CSS classes on 10 cards per page. Regex-based
    extraction is more resilient than a full HTML parser here because the
    markup is heavily customized (no <article> tags, non-standard structure).
    """
    def clean(s):
        # Strip HTML entities + tags + collapse whitespace
        s = re.sub(r'<[^>]+>', ' ', s)
        s = (s.replace('&#8211;', '–').replace('&#8212;', '—')
              .replace('&#8216;', "'").replace('&#8217;', "'")
              .replace('&#8220;', '"').replace('&#8221;', '"')
              .replace('&amp;', '&').replace('&nbsp;', ' '))
        s = re.sub(r'\s+', ' ', s).strip()
        return s

    titles = [clean(m) for m in re.findall(
        r'<[^>]*class="card__title[^"]*"[^>]*>(.*?)</[^>]*>', html, re.DOTALL)]
    descs = [clean(m) for m in re.findall(
        r'<[^>]*class="card__description[^"]*"[^>]*>(.*?)</[^>]*>', html, re.DOTALL)]

    posts = []
    for t, d in zip(titles, descs):
        posts.append({"title": t, "content": d})
    return posts


def parse_date_from_title(title):
    """Try to pull an ISO date out of a Viking Fleet post title.

    Typical formats:
      "Whale Watching Report - Sunday July 27th, 2026"
      "Whale Watching Report - Sun 7/27/26"
      "Whale Watch — July 27"
      "Mon Aug 10 – Whale Watching"                     (Randy 2026-08-11: this
                                                         short-month form was NOT
                                                         matched by the old regex,
                                                         so all August 2026 posts
                                                         got dropped as undated)
    Returns ISO date string or None.
    """
    now = datetime.date.today()
    # Try Month DD, YYYY — full name OR 3-letter abbreviation. Adding the
    # abbreviated form (Jan, Feb, …, Dec) covers the Viking Fleet's own
    # "Mon Aug 10" style, which the old regex silently missed.
    m = re.search(
        r"(January|February|March|April|May|June|July|August|September|October|November|December|"
        r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
        r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?",
        title, re.IGNORECASE)
    if m:
        month_name = m.group(1)
        day = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else now.year
        try:
            month = datetime.datetime.strptime(month_name[:3], "%b").month
            d = datetime.date(year, month, day)
            # If parsing gave a future date within 30 days, it's probably last year
            if (d - now).days > 30:
                d = datetime.date(year - 1, month, day)
            return d.isoformat()
        except ValueError:
            return None
    # Try M/D/YY
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", title)
    if m:
        month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000
        try:
            return datetime.date(year, month, day).isoformat()
        except ValueError:
            return None
    return None


def extract_species(text):
    text_lower = text.lower()
    found = []
    for kw, norm in SPECIES_KEYWORDS.items():
        if kw in text_lower and norm not in found:
            found.append(norm)
    return found


def extract_location(text):
    """Return (coords, zones) — tries LOCATION_HINTS first, else defaults."""
    text_lower = text.lower()
    for kw, (coords, zones) in LOCATION_HINTS.items():
        if kw in text_lower:
            return coords, zones
    return DEFAULT_COORDS, DEFAULT_ZONES


def kind_from_species(species):
    has_whale = any(s in ("humpback", "finback", "minke", "sei", "right whale",
                          "sperm whale", "sei", "pilot whale") for s in species)
    has_dolphin = any("dolphin" in s or "porpoise" in s for s in species)
    if has_whale and has_dolphin:
        return "mixed"
    if has_whale:
        return "whale"
    if has_dolphin:
        return "dolphin"
    return "mixed"


def fetch_feed():
    """Fetch the Viking Fleet category page. Returns HTML text or None on error.

    v24.105 — 3-attempt retry with backoff [3s, 10s]. Randy's rule
    ('find the whales, find the tuna') makes this a first-class signal —
    a single-shot failure previously killed the whale-signal for the day.
    """
    import time as _time
    backoffs = [3, 10]
    last_err = None
    for attempt in range(3):
        req = urllib.request.Request(FEED_URL, headers={
            "User-Agent": "Mozilla/5.0 (compatible; FishFinderBot/1.0; whale-sighting-harvester)",
        })
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_err = e
            if attempt < len(backoffs):
                _time.sleep(backoffs[attempt])
    print(f"  WARN: whale feed fetch failed after 3 attempts ({last_err})")
    return None


def build_sighting(post):
    """Turn one parsed post {title, content} into a whale_sightings entry, or None.

    Randy 2026-08-11: "Nothing seems to change for the last three, four days."
    Root cause: Viking Fleet posts like "Sat Aug 8 – Another successful 3 species
    trip!" or "2 species of cetaceans" are describing REAL cetacean activity but
    don't name humpback/finback/dolphin/etc. The old parser dropped these because
    extract_species() came back empty — so a live, dated whale-watch-was-here
    signal never made it into zones.json and the freshness audit rightly
    reported whale_sightings stale.

    Fix: two-tier extraction.
      TIER A — a post naming specific species → detailed sighting (as before).
      TIER B — a post that clearly reports whale-watch activity (title contains
               "whale watching" OR content mentions "cetaceans"/"species trip"/
               "great whale watch") but doesn't name species → GENERIC sighting
               tagged species=["unspecified_cetacean"], kind="generic". Still
               dated + geolocated to Off Montauk, so the freshness audit sees
               a live signal and the model can still boost Montauk-area zones.
               The `quote` preserves whatever the fleet actually wrote so the
               First Mate can later analyze what "unspecified" turned out to be.
    """
    title = post.get("title", "").strip()
    content = post.get("content", "").strip()
    combined = title + " " + content
    combined_lower = combined.lower()
    date_iso = parse_date_from_title(title) or parse_date_from_title(content)
    if not date_iso:
        return None
    species = extract_species(combined)
    is_generic = False
    if not species:
        # TIER B fallback — post was clearly a whale-watch report even without
        # named species. Guard: require it to actually be a whale-watch post
        # (not some off-topic content the RSS pulled in).
        generic_markers = [
            "whale watching", "whale watch",
            "species of cetacean", "cetaceans", "cetacean",
            "species trip", "3 species", "2 species", "4 species", "5 species",
            "great whale", "successful trip", "spectacular trip",
        ]
        if any(mk in combined_lower for mk in generic_markers):
            species = ["unspecified_cetacean"]
            is_generic = True
        else:
            return None  # not a whale post at all
    coords, zones = extract_location(combined)
    kind = "generic" if is_generic else kind_from_species(species)
    # Extract a short quote — first 180 chars of content that includes species
    quote = content[:180].strip().replace("\n", " ").replace("  ", " ")
    if not quote:
        quote = title
    return {
        "date": date_iso,
        "species": species,
        "kind": kind,
        "count": None,
        "location": "Off Montauk (CRESLI/Viking Fleet whale watch)",
        "coords": coords,
        "source": "CRESLI / Viking Fleet whale watch (Montauk) — auto-harvested",
        "source_type": "viking_fleet_generic" if is_generic else "viking_fleet",
        "quote": quote,
        "zones": zones,
    }


def harvest_and_merge():
    """Fetch fresh CRESLI/Viking Fleet reports, dedupe by date, merge into
    zones.json.

    Returns a dict (Randy's 2026-08-08 preservation directive — used to be
    just the added count; now callers get everything so build-inlined.py can
    persist raw whale-watch corpus into the archive):

        {
          "added": int,                 # new sightings written to zones.json
          "raw_posts": list[{title, content}],  # ALL parsed cards, matched or not
          "parsed_sightings": list[dict],       # every sighting we would have added,
                                                # incl. ones already in zones.json
          "html_bytes": int,            # size of the fetched HTML (for audit)
          "fetch_ok": bool,
        }
    """
    print("Harvesting whale/dolphin sightings from Viking Fleet...")
    html = fetch_feed()
    if not html:
        return {"added": 0, "raw_posts": [], "parsed_sightings": [],
                "html_bytes": 0, "fetch_ok": False}
    try:
        posts = parse_cards(html)
    except Exception as e:
        print(f"  WARN: whale feed parse failed ({e})")
        return {"added": 0, "raw_posts": [], "parsed_sightings": [],
                "html_bytes": len(html), "fetch_ok": True, "parse_error": str(e)}
    print(f"  Parsed {len(posts)} posts from feed")

    zones_data = json.loads(ZONES_PATH.read_text())
    existing = zones_data.get("whale_sightings", [])
    existing_dates = {s.get("date") for s in existing}

    added = 0
    parsed_sightings = []  # every candidate we built, even duplicates
    for post in posts:
        s = build_sighting(post)
        if s:
            parsed_sightings.append(s)
        if not s:
            continue
        if s["date"] in existing_dates:
            continue  # already have this date
        # Only accept sightings within last 30 days (avoid re-adding ancient ones)
        try:
            days_old = (datetime.date.today() - datetime.date.fromisoformat(s["date"])).days
            if days_old < 0 or days_old > 30:
                continue
        except ValueError:
            continue
        existing.append(s)
        existing_dates.add(s["date"])
        added += 1
        print(f"  ✓ Added {s['date']}: {', '.join(s['species'])}")

    if added > 0:
        zones_data["whale_sightings"] = existing
        ZONES_PATH.write_text(json.dumps(zones_data, indent=2))
        print(f"  Wrote {added} new sightings to zones.json")
    else:
        print(f"  No new sightings to add (all recent posts already in zones.json)")

    # Trim raw post text so a single very long card can't blow up the archive.
    # Typical Viking Fleet card content is 100-500 chars; 1200 keeps effectively
    # everything real while protecting against pathological cases.
    trimmed_posts = []
    for p in posts:
        t = (p.get("title") or "")[:300]
        c = p.get("content") or ""
        if len(c) > 1200:
            c = c[:1200] + "…"
        trimmed_posts.append({"title": t, "content": c})

    return {
        "added": added,
        "raw_posts": trimmed_posts,
        "parsed_sightings": parsed_sightings,
        "html_bytes": len(html),
        "fetch_ok": True,
    }


def _parse_rss_items(xml_text):
    """Parse Jersey Shore Whale Watch RSS feed into {title, content, pubdate} list."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(xml_text)
    except Exception as e:
        print(f"  WARN: Mid-Atl whale feed parse failed ({e})")
        return []
    items = []
    for item in root.iter("item"):
        title_el = item.find("title")
        pub_el = item.find("pubDate")
        # description = short blurb; content:encoded = full body (WP convention)
        desc_el = item.find("description")
        content_el = item.find("{http://purl.org/rss/1.0/modules/content/}encoded")
        title = (title_el.text or "").strip() if title_el is not None else ""
        pub = (pub_el.text or "").strip() if pub_el is not None else ""
        body = ""
        if content_el is not None and content_el.text:
            body = content_el.text
        elif desc_el is not None and desc_el.text:
            body = desc_el.text
        # Strip HTML tags from body for parsing
        body_clean = re.sub(r"<[^>]+>", " ", body)
        body_clean = re.sub(r"\s+", " ", body_clean).strip()
        # Parse pubDate to ISO YYYY-MM-DD (RSS format: 'Fri, 22 Aug 2026 13:45:57 +0000')
        date_iso = None
        try:
            dt = datetime.datetime.strptime(pub[:16], "%a, %d %b %Y")
            date_iso = dt.date().isoformat()
        except Exception:
            # fallback: try to extract "August 19" style from title
            date_iso = parse_date_from_title(title)
        items.append({"title": title, "content": body_clean, "date": date_iso})
    return items


def build_midatl_sighting(item):
    """Build a whale_sightings entry from a Jersey Shore RSS item.
    Uses the same species / generic-cetacean logic as the Viking Fleet parser,
    plus the Mid-Atl default coords + zones (Sea Girt Reef area)."""
    title = item.get("title", "").strip()
    content = item.get("content", "").strip()
    date_iso = item.get("date")
    if not date_iso:
        return None
    combined = title + " " + content
    combined_lower = combined.lower()
    species = extract_species(combined)
    is_generic = False
    if not species:
        # Same TIER B markers as Viking Fleet — "whale watching report", generic
        # cetacean mentions, dated trip reports.
        generic_markers = [
            "whale watching", "whale watch",
            "species of cetacean", "cetaceans", "cetacean",
            "species trip", "3 species", "2 species", "4 species", "5 species",
            "great whale", "successful trip", "spectacular trip",
            "trip report", "whales today",
        ]
        if any(mk in combined_lower for mk in generic_markers):
            species = ["unspecified_cetacean"]
            is_generic = True
        else:
            return None
    kind = "generic" if is_generic else kind_from_species(species)
    quote = content[:180].strip().replace("\n", " ").replace("  ", " ")
    if not quote:
        quote = title
    return {
        "date": date_iso,
        "species": species,
        "kind": kind,
        "count": None,
        "location": "Off Manasquan Inlet (Jersey Shore Whale Watch — Bill McKim)",
        "coords": MIDATL_DEFAULT_COORDS,
        "source": "Jersey Shore Whale Watching Tour (Bill McKim / Belmar NJ) — auto-harvested",
        "source_type": "jersey_shore_whale_watch_generic" if is_generic else "jersey_shore_whale_watch",
        "quote": quote,
        "zones": list(MIDATL_DEFAULT_ZONES),
    }


def harvest_midatl_and_merge():
    """Fetch Jersey Shore Whale Watch RSS feed, merge new sightings into
    zones_mid_atlantic.json under whale_sightings. Same schema + dedup logic
    as harvest_and_merge() for Zone 1 — just wired to the Mid-Atl data file."""
    print("Harvesting Mid-Atl whale/dolphin sightings from Jersey Shore Whale Watching Tour...")
    if not MIDATL_ZONES_PATH.exists():
        print("  Skipped: zones_mid_atlantic.json not present.")
        return {"added": 0, "raw_posts": [], "parsed_sightings": [],
                "html_bytes": 0, "fetch_ok": False, "region": "midatl"}
    # v24.105 — same retry hardening as harvest_and_merge() above.
    import time as _time
    backoffs = [3, 10]
    xml_text = None
    last_err = None
    for attempt in range(3):
        req = urllib.request.Request(MIDATL_FEED_URL, headers={
            "User-Agent": "Mozilla/5.0 (compatible; FishFinderBot/1.0; whale-sighting-harvester)",
        })
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                xml_text = r.read().decode("utf-8", errors="replace")
            break
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_err = e
            if attempt < len(backoffs):
                _time.sleep(backoffs[attempt])
    if xml_text is None:
        print(f"  WARN: Mid-Atl whale feed fetch failed after 3 attempts ({last_err})")
        return {"added": 0, "raw_posts": [], "parsed_sightings": [],
                "html_bytes": 0, "fetch_ok": False, "region": "midatl"}
    items = _parse_rss_items(xml_text)
    print(f"  Parsed {len(items)} RSS items")
    zones_data = json.loads(MIDATL_ZONES_PATH.read_text())
    existing = zones_data.get("whale_sightings", [])
    existing_dates = {s.get("date") for s in existing if s.get("source_type", "").startswith("jersey_shore_whale_watch")}
    added = 0
    parsed_sightings = []
    for item in items:
        s = build_midatl_sighting(item)
        if s:
            parsed_sightings.append(s)
        if not s:
            continue
        if s["date"] in existing_dates:
            continue
        try:
            days_old = (datetime.date.today() - datetime.date.fromisoformat(s["date"])).days
            if days_old < 0 or days_old > 30:
                continue
        except ValueError:
            continue
        existing.append(s)
        existing_dates.add(s["date"])
        added += 1
        print(f"  ✓ Added {s['date']}: {', '.join(s['species'])}")
    if added > 0:
        zones_data["whale_sightings"] = existing
        MIDATL_ZONES_PATH.write_text(json.dumps(zones_data, indent=2))
        print(f"  Wrote {added} new Mid-Atl sightings to zones_mid_atlantic.json")
    else:
        print(f"  No new Mid-Atl sightings to add (all recent posts already in file)")
    trimmed_items = []
    for it in items:
        trimmed_items.append({
            "title": (it.get("title") or "")[:300],
            "content": (it.get("content") or "")[:1200],
            "date": it.get("date"),
        })
    return {
        "added": added,
        "raw_posts": trimmed_items,
        "parsed_sightings": parsed_sightings,
        "html_bytes": len(xml_text),
        "fetch_ok": True,
        "region": "midatl",
    }


if __name__ == "__main__":
    result = harvest_and_merge()
    print(f"Summary: added={result['added']}, raw_posts={len(result['raw_posts'])}, "
          f"parsed_sightings={len(result['parsed_sightings'])}, html_bytes={result['html_bytes']}")
    # Also run Mid-Atl harvest when invoked directly
    result2 = harvest_midatl_and_merge()
    print(f"Mid-Atl summary: added={result2['added']}, raw_posts={len(result2['raw_posts'])}, "
          f"parsed_sightings={len(result2['parsed_sightings'])}")
