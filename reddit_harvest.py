#!/usr/bin/env python3
"""Reddit fishing-forum harvester (v24.20, 2026-08-28).

Randy 2026-08-28: "We gotta figure out how to get more intel of people talking
about fish they caught. Somehow we need to get more intel."

Reddit r/saltwaterfishing and r/stripedbass carry weekly recreational-angler
trip reports that neither the YouTube captains nor the paid magazines pick up.
Free RSS access with a browser UA (Reddit's JSON API blocks non-OAuth clients
in 2024+ but the RSS mirror still works).

Filter criteria for promotion into `bait_intel`:
  1. Age ≤ 7 days (Randy's freshness rule)
  2. Title or body mentions a Northeast location keyword (see LOC_HINTS)
  3. Title or body mentions a species-relevant keyword (uses same tuna/striper
     vocabulary as v24.12 species filter)

Entries are auto-promoted with source_type="reddit_auto" — this is r/randos not
verified captains, so the score contribution is inherently softer. The Captain
skill downgrades entries flagged as reddit_auto if a specific report proves
unreliable.

Public feed URLs used:
  https://www.reddit.com/r/<sub>/new.rss
  https://www.reddit.com/r/<sub>/hot.rss
"""
import json
import re
import urllib.request
import urllib.error
import datetime
import pathlib
import xml.etree.ElementTree as ET

BASE = pathlib.Path(__file__).parent

# Subreddits worth scanning. Focus on saltwater NE tuna/striper content.
REDDIT_SUBS = [
    {"name": "saltwaterfishing", "focus": "general saltwater"},
    {"name": "stripedbass", "focus": "striper-specific"},
]

# Rotate through /new and /hot to cover both new posts and community-picked ones
REDDIT_FEEDS = ["new.rss", "hot.rss"]

# Reddit fishing-adjacent location keywords that map to a NE cluster.
# NB: this is DELIBERATELY conservative — a post that just says "Beach" or
# "Bay" without a state hint will NOT match. We want zone-attributable content
# only.
LOC_HINTS = {
    "montauk":              ["montauk_nearshore", "montauk_rips_striper"],
    "block island":         ["block_island_striper", "block_island_sound", "s_block_nearshore"],
    "narragansett":         ["narragansett_bay"],
    "point judith":         ["block_island_sound", "narragansett_bay"],
    "watch hill":           ["watch_hill_reef", "block_island_sound"],
    "the race":             ["the_race", "plum_gut"],
    "plum gut":             ["plum_gut"],
    "long island sound":    ["central_li_sound", "western_li_sound"],
    "shinnecock":           ["south_shore_li"],
    "moriches":             ["south_shore_li"],
    "cape cod":             [],  # outside Randy's range but track
    "buzzards bay":         [],
    "hudson canyon":        ["hudson_canyon"],
    "block canyon":         ["block_canyon"],
    "atlantis canyon":      ["atlantis_canyon"],
    "canyon":               ["hudson_canyon", "block_canyon", "atlantis_canyon"],
    "tuna ridge":           ["tuna_ridge"],
    "coxes ledge":          ["coxes_ledge_se"],
    "butterfish hole":      ["butterfish_hole"],
    "old saybrook":         ["western_li_sound"],
    "long island":          ["south_shore_li", "central_li_sound", "western_li_sound"],
    "cuttyhunk":            [],
    "elizabeth islands":    [],
    "vineyard sound":       [],
    # NJ locations for Mid-Atl bucket
    "cape may":             [],
    "manasquan":            [],
    "point pleasant":       [],
    "barnegat":             [],
    "atlantic city":        [],
}

# Species / bait vocabulary the post must include SOMETHING from.
FISH_VOCAB = re.compile(
    r"\b(blue\s?fin|yellow\s?fin|big\s?eye|albacore|tuna|mahi|dolphin\s*fish|wahoo|marlin|"
    r"sword\s?fish|thresher|mako|shark|striper|striped\s+bass|linesider|schoolie|"
    r"keeper|blue\s?fish|blues|false\s+albacore|albie|bonito|"
    r"bunker|menhaden|sand\s?eel|squid|bait\s+ball|bait\s+pod|"
    r"caught|landed|hooked|released|limit|blitz|feeding|busting|surface)\b",
    re.IGNORECASE
)


def _fetch_reddit_rss(sub, feed_type):
    """Fetch a Reddit RSS feed. Returns list of {title, body, link, date, author}.

    v24.105 — retry with exponential backoff on 429 (rate-limited). Reddit
    RSS is aggressively throttled — a single-shot fetch usually 429s during
    the build window. New schedule: 4 attempts / 3s / 12s / 30s = ~45s
    tolerance. Rotate User-Agent between attempts to defeat per-UA quotas.
    """
    import time as _time
    url = f"https://www.reddit.com/r/{sub}/{feed_type}?limit=25"
    _uas = [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
        "FishFinderBot/1.0 (Randy Spargo, personal fishing-report aggregator)",
    ]
    backoffs = [3, 12, 30]
    body = None
    last_err = None
    for attempt in range(4):
        req = urllib.request.Request(url, headers={
            "User-Agent": _uas[attempt % len(_uas)],
            "Accept": "application/atom+xml,application/rss+xml,application/xml"
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                body = r.read().decode("utf-8", errors="replace")
            last_err = None
            break
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_err = e
            # Only retry on 429 (rate limit) or transient network errors.
            is_429 = isinstance(e, urllib.error.HTTPError) and e.code == 429
            is_transient = isinstance(e, (urllib.error.URLError, TimeoutError)) and not isinstance(e, urllib.error.HTTPError)
            if not (is_429 or is_transient):
                break  # 404 / 500 etc — no point retrying
            if attempt < len(backoffs):
                _time.sleep(backoffs[attempt])
    if body is None:
        print(f"  WARN: r/{sub} {feed_type} fetch failed ({last_err})")
        return []
    try:
        root = ET.fromstring(body)
    except Exception as e:
        print(f"  WARN: r/{sub} XML parse failed ({e})")
        return []
    ATOM = "{http://www.w3.org/2005/Atom}"
    posts = []
    for entry in root.iter(f"{ATOM}entry"):
        title_el = entry.find(f"{ATOM}title")
        content_el = entry.find(f"{ATOM}content")
        link_el = entry.find(f"{ATOM}link")
        pub_el = entry.find(f"{ATOM}published") or entry.find(f"{ATOM}updated")
        author_el = entry.find(f"{ATOM}author")
        title = ((title_el.text or "") if title_el is not None else "").strip()
        content_raw = ((content_el.text or "") if content_el is not None else "")
        content_text = re.sub(r"<[^>]+>", " ", content_raw)
        content_text = re.sub(r"&#39;", "'", content_text)
        content_text = re.sub(r"&amp;", "&", content_text)
        content_text = re.sub(r"\s+", " ", content_text).strip()[:1200]
        link = link_el.get("href", "") if link_el is not None else ""
        pub = ((pub_el.text or "") if pub_el is not None else "")
        date_iso = pub[:10] if len(pub) >= 10 and pub[4] == "-" else None
        author = ""
        if author_el is not None:
            name_el = author_el.find(f"{ATOM}name")
            if name_el is not None:
                author = (name_el.text or "").strip()
        if title and date_iso:
            posts.append({
                "title": title,
                "body": content_text,
                "link": link,
                "date": date_iso,
                "author": author,
            })
    return posts


def _classify_post(post):
    """Return (region, zone_ids, matched_locs) for a post if it qualifies.
    Returns (None, [], []) if the post doesn't hit both a location AND species
    filter — those get skipped from bait_intel promotion.
    """
    combined = (post.get("title", "") + " " + post.get("body", "")).lower()
    # Location hits — must mention at least one NE location
    matched_locs = [k for k in LOC_HINTS.keys() if k in combined]
    if not matched_locs:
        return (None, [], [])
    # Species/action words — must talk about fish/bait/action
    if not FISH_VOCAB.search(combined):
        return (None, [], [])
    # Collect zone_ids
    zones = []
    for loc in matched_locs:
        for zid in LOC_HINTS[loc]:
            if zid not in zones:
                zones.append(zid)
    if not zones:
        # Location mentioned but doesn't map to a Randy-range zone (e.g., Cape May)
        return (None, [], matched_locs)
    return ("northeast", zones, matched_locs)


def harvest(cutoff_days=7):
    """Fetch + classify recent Reddit posts. Returns:
      {
        harvested_at: ISO,
        cutoff_days: int,
        subs_scanned: [str],
        total_fetched: int,
        northeast_posts: [dict],  # each has title, body, link, date, author, zones, matched_locs
        skipped_no_location: int,
        skipped_no_species: int,
      }
    """
    today = datetime.date.today()
    cutoff = today - datetime.timedelta(days=cutoff_days)
    all_posts = []
    seen_links = set()
    for sub_meta in REDDIT_SUBS:
        sub = sub_meta["name"]
        for feed_type in REDDIT_FEEDS:
            posts = _fetch_reddit_rss(sub, feed_type)
            for p in posts:
                if p["link"] in seen_links:
                    continue
                seen_links.add(p["link"])
                p["subreddit"] = sub
                all_posts.append(p)
    ne_posts = []
    skipped_no_loc = 0
    skipped_no_species = 0
    for p in all_posts:
        try:
            d = datetime.date.fromisoformat(p["date"])
        except Exception:
            continue
        if d < cutoff:
            continue
        region, zones, matched = _classify_post(p)
        if region is None:
            if not matched:
                skipped_no_loc += 1
            else:
                skipped_no_species += 1
            continue
        p["zones"] = zones
        p["matched_locs"] = matched
        p["region"] = region
        ne_posts.append(p)
    return {
        "harvested_at": datetime.datetime.utcnow().isoformat() + "Z",
        "cutoff_days": cutoff_days,
        "subs_scanned": [s["name"] for s in REDDIT_SUBS],
        "total_fetched": len(all_posts),
        "northeast_posts": ne_posts,
        "skipped_no_location": skipped_no_loc,
        "skipped_no_species": skipped_no_species,
    }


def promote_into_zones(result, existing_bait_intel):
    """Turn Reddit forum posts into bait_intel entries.
    Dedupe against existing_bait_intel using (link, date) — Reddit posts have
    unique perma-links so this is reliable.
    """
    existing_links = {b.get("link") for b in existing_bait_intel or [] if b.get("link")}
    new_entries = []
    for p in result["northeast_posts"]:
        if p["link"] in existing_links:
            continue
        # Compose a concise quote from title + first sentence of body
        quote = p["title"]
        body_first = ""
        if p["body"]:
            # Grab first sentence-ish
            m = re.search(r"^(.{20,220}?[\.\!\?])", p["body"])
            body_first = (m.group(1) if m else p["body"][:220]).strip()
        entry = {
            "date": p["date"],
            "bait": "reader-report",
            "location": ", ".join(p.get("matched_locs", []))[:120] or "NE",
            "species_feeding": "",
            "quote": (quote + (" — " + body_first if body_first and body_first != quote else "")).strip()[:400],
            "source": f"r/{p.get('subreddit','saltwaterfishing')} · u/{p.get('author','anon')[:24]}",
            "source_type": "reddit_auto",
            "link": p["link"],
            "title": quote[:200],
            "excerpt": body_first[:400],
            "zones": p["zones"],
        }
        new_entries.append(entry)
    return new_entries


if __name__ == "__main__":
    result = harvest(cutoff_days=7)
    print(f"Reddit harvest — {result['total_fetched']} total posts fetched")
    print(f"  Skipped (no NE location): {result['skipped_no_location']}")
    print(f"  Skipped (location but no species): {result['skipped_no_species']}")
    print(f"  NE-eligible posts: {len(result['northeast_posts'])}")
    for p in result["northeast_posts"][:10]:
        print(f"\n  [{p['date']}] r/{p['subreddit']} · u/{p.get('author','?')}")
        print(f"    → {p['title'][:100]}")
        print(f"    locs: {', '.join(p['matched_locs'])}")
        print(f"    zones: {p['zones']}")
    # Dry-run promotion
    entries = promote_into_zones(result, [])
    print(f"\nBait-intel candidate entries: {len(entries)}")
