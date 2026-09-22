#!/usr/bin/env python3
"""YouTube RSS harvester for Fish Finder.

Pulls each channel's public RSS feed (no API key needed, no OAuth), parses
recent titles + descriptions, extracts species / size-class / location /
date patterns, and returns structured intel that the Captain can use
as a corroborating source during the nightly heat-refresh.

Design:
- Every channel = one HTTP call to youtube.com/feeds/videos.xml?channel_id=X
- Each feed returns up to 15 most recent entries
- We keep entries published within the last N days (default 14 in-season, 30 off)
- We pattern-match against known species names, size vocabulary, and locations
- We flag videos that look like trip recaps vs tactical/educational content
- We NEVER auto-change a heat score based on YouTube alone — this feeds the
  two-independent-sources rule as ONE source. The Captain still makes the call.

Usage as a module (from build-inlined.py):
    import youtube_harvest as yth
    intel = yth.harvest_all(channels_path, cutoff_days=14, verbose=True)

Usage from CLI (for a manual test run):
    python3 youtube_harvest.py                 # 14-day window, verbose
    python3 youtube_harvest.py --days 30       # 30-day window
    python3 youtube_harvest.py --raw           # dump raw JSON
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

BASE = pathlib.Path(__file__).parent

# v23.12 (Randy 2026-08-08): "Let's fix it so you can understand the text."
# youtube-transcript-api pulls auto-generated captions from YouTube — no API
# key needed. This turns every captain video into ~30-40K characters of
# spoken intel. Imported optionally: if the library isn't installed the
# harvester falls back to title + description only (no hard failure).
try:
    from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore
    _YTT_API = YouTubeTranscriptApi()
    _TRANSCRIPTS_AVAILABLE = True
except Exception:
    _YTT_API = None
    _TRANSCRIPTS_AVAILABLE = False

# Cache transcripts by video_id on disk so repeat nightly builds don't
# refetch the same video. TTL = forever (videos are immutable once posted;
# transcript revisions are rare). Cache limit: 500 videos ≈ 15 MB.
_TRANSCRIPT_CACHE_DIR = BASE / "cache" / "yt_transcripts"
_TRANSCRIPT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
# Trim: individual transcripts get truncated at 40K chars to keep archives
# manageable. In-context scanning still uses the full text — trim happens
# AFTER pattern extraction, before storage.
_TRANSCRIPT_STORE_MAX_CHARS = 40000

# Atom namespaces used by YouTube's RSS feed
_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_YT_NS = "{http://www.youtube.com/xml/schemas/2015}"
_MEDIA_NS = "{http://search.yahoo.com/mrss/}"

# ---------------------------------------------------------------------------
# Pattern vocabularies — used for extraction from titles + descriptions
# ---------------------------------------------------------------------------

# Species keywords → canonical species key (must match zones.json species IDs)
SPECIES_PATTERNS: dict[str, list[str]] = {
    "bluefin_giant": [
        r"\bgiant\s+bluefin\b",
        r"\bgiant\s+tuna\b",
        r"\bbluefin\s+giant\b",
        r"\b(?:100|150|200|250|300|400|500|600|700|800)\+?\s*(?:lb|pound|pounder)\b",
        r"\b(?:73|75|80|85|90|95|100|110)\+?\s*(?:inch|\")\s*bluefin\b",
        r"\bwicked\s+fat\s+tuna\b",
        r"\bharpoon(?:ed)?\b",
    ],
    "bluefin_recreational": [
        r"\bbluefin\b",         # bare "bluefin" defaults to recreational; giant patterns override
        r"\bBFT\b",
        r"\bschool(?:ie)?\s+tuna\b",
        r"\brec\s+bluefin\b",
        r"\bshort\s+bluefin\b",
    ],
    "yellowfin_tuna": [
        r"\byellowfin\b",
        r"\bYFT\b",
        r"\byellow(?:\s|-)?fin\s+tuna\b",
    ],
    "bigeye_tuna": [
        r"\bbigeye\b",
        r"\bbig(?:\s|-)?eye\s+tuna\b",
    ],
    "mahi": [r"\bmahi\b", r"\bdorado\b", r"\bdolphin\s*fish\b"],
    "swordfish": [r"\bswordfish\b", r"\bsword\b(?!\s*plant)"],
    "wahoo": [r"\bwahoo\b", r"\bono\b"],
    "white_marlin": [r"\bwhite\s+marlin\b"],
    "blue_marlin": [r"\bblue\s+marlin\b"],
    "sailfish": [r"\bsailfish\b"],
    "thresher_shark": [r"\bthresher\b"],
    "mako_shark": [r"\bmako\b"],
    "striped_bass": [
        r"\bstriped\s+bass\b",
        r"\bstriper(?:s)?\b",
        r"\bstripe(?:r)?\b(?=\s|,|\.|$)",
        r"\bstriped?\s+bass\s+blitz\b",
        r"\bcow\s+bass\b",
    ],
    "bluefish": [r"\bbluefish\b", r"\bchopper(?:s)?\b(?=\s|,|\.|$)"],
    "false_albacore": [
        r"\bfalse\s+albacore\b",
        r"\balbie(?:s)?\b",
        r"\bfat\s+alberts?\b",
    ],
}

# Location keywords → zone_hint (matches part of a zone name in zones.json).
# We look for these in titles/descriptions and record which zones they
# corroborate. Which zones each location LIFTS is defined separately in
# data/youtube_zone_map.json (fuzzy geography — "Montauk" lifts several
# nearby zones). The Captain still verifies.
LOCATION_PATTERNS: dict[str, list[str]] = {
    "montauk": [r"\bmontauk\b", r"\bthe\s+end\b(?=\s|,|\.|$)"],
    "block_island": [r"\bblock\s+island\b", r"\bBI\b(?=\s|,|\.|$)"],
    "coxes_ledge": [r"\bcoxe(?:'|)s\s+ledge\b", r"\bcox(?:\s|')?s\b"],
    "hudson_canyon": [r"\bhudson\s+canyon\b"],
    "atlantis_canyon": [r"\batlantis\s+canyon\b"],
    "veatch_canyon": [r"\bveatch\b"],
    "block_canyon": [r"\bblock\s+canyon\b"],
    "the_dip": [r"\bthe\s+dip\b"],
    "tuna_ridge": [r"\btuna\s+ridge\b"],
    "the_bank": [r"\bthe\s+bank\b"],
    "cartwright": [r"\bcartwright\b"],
    "point_judith": [r"\bpoint\s+judith\b", r"\bPt\.?\s+Judith\b"],
    "snug_harbor": [r"\bsnug\s+harbor\b"],
    "narragansett": [r"\bnarragansett\b"],
    "cape_cod_bay": [r"\bcape\s+cod\s+bay\b", r"\bCCB\b"],
    "cape_cod_canal": [r"\bcape\s+cod\s+canal\b", r"\bthe\s+canal\b"],
    "vineyard_sound": [r"\bvineyard\s+sound\b"],
    "monomoy": [r"\bmonomoy\b"],
    "chatham": [r"\bchatham\b"],
    "stellwagen": [r"\bstellwagen\b"],
    "gloucester": [r"\bgloucester\b", r"\bcape\s+ann\b"],
    "long_island_sound": [r"\blong\s+island\s+sound\b", r"\bLIS\b"],
    "long_island": [r"\blong\s+island\b"],
    "the_race": [r"\bthe\s+race\b"],
    "plum_gut": [r"\bplum\s+gut\b"],
    "orient_point": [r"\borient\b"],
    "fishers_island": [r"\bfishers\s+island\b"],
    "old_saybrook": [r"\bold\s+saybrook\b"],
    "connecticut_river": [r"\bconnecticut\s+river\b", r"\bCT\s+river\b"],
    "watch_hill": [r"\bwatch\s+hill\b"],
    "barnegat": [r"\bbarnegat\b"],
    "long_beach_island": [r"\blong\s+beach\s+island\b", r"\bLBI\b"],
    "cape_may": [r"\bcape\s+may\b"],
    "manasquan": [r"\bmanasquan\b"],
    "point_pleasant": [r"\bpoint\s+pleasant\b"],
    "shrewsbury_rocks": [r"\bshrewsbury\b"],
    "shark_river": [r"\bshark\s+river\b"],
    "sandy_hook": [r"\bsandy\s+hook\b"],
    "wicked_tuna_grounds": [r"\bnorth\s+shore\b", r"\bMA\s+coast\b"],
    # New patterns added 2026-07-29 for full zone coverage
    "habs_ledge": [r"\bhabs?\s+ledge\b"],
    "acid_barge": [r"\bacid\s+barge\b"],
    "the_gully": [r"\bthe\s+gully\b(?!\s*canyon)"],
    "butterfish_hole": [r"\bbutterfish\s+hole\b"],
    # v24.21 (Randy 2026-08-28): CIA Grounds + Cartwright — SE of Montauk
    # nearshore. Captain Skip pairs "CIA or Butterfish Hole area".
    "cia": [r"\bCIA\s+(?:grounds?|ground|area)\b"],
    "cartwright": [r"\bcartwright\s+(?:grounds?|shoal|area)\b"],
    # v24.22 (Randy 2026-08-28): mining pass through The Fisherman regional
    # feeds surfaced these named spots that captains mention regularly but
    # our harvester wasn't tuned into:
    "coimbra": [r"\bcoimbra(?:\s+wreck)?\b"],
    "ranger": [r"\branger\s+(?:wreck|area|grounds?)\b"],
    "niantic bay": [r"\bniantic\s+bay\b"],
    "niantic": [r"\bniantic\b(?!\s+bay)"],
    "black point": [r"\bblack\s+point\b"],
    "two tree": [r"\btwo\s+tree(?:\s+channel)?\b"],
    "cornfield point": [r"\bcornfield\s+point\b"],
    "cornfield": [r"\bcornfield(?!\s+point)\b"],
    "fishers island": [r"\bfishers\s+island\b"],
    "watch hill": [r"\bwatch\s+hill(?:\s+reefs?)?\b"],
    "shinnecock bay": [r"\bshinnecock\s+bay\b"],
    "sakonnet": [r"\bsakonnet(?:\s+point)?\b"],
    "point judith light": [r"\bpoint\s+judith\s+light(?:house)?\b"],
    "east grounds": [r"\beast\s+grounds\b"],
    "sharks ledge": [r"\bsharks?\s+ledge\b"],
    "hooter": [r"\bhooter\b"],
    "deep hole": [r"\bdeep\s+hole\b"],
    "mud_hole": [r"\bmud\s+hole\b"],
    "millstone": [r"\bmillstone\b"],
    "thirty_five_fathom": [r"\b(?:35|thirty[- ]?five)[- ]?fathom(?:\s+line)?\b"],
    # v24.2 (2026-08-23) — Mid-Atlantic Zone 2 location patterns.
    # These map into zones defined in data/zones_mid_atlantic.json.
    "baltimore_canyon": [r"\bbaltimore\s+canyon\b"],
    "wilmington_canyon": [r"\bwilmington\s+canyon\b"],
    "poor_mans_canyon": [r"\bpoor\s+man'?s?\s+canyon\b"],
    "washington_canyon": [r"\bwashington\s+canyon\b"],
    "norfolk_canyon": [r"\bnorfolk\s+canyon\b"],
    "jackspot": [r"\bjackspot\b", r"\bhot\s+dog\b", r"\bthe\s+hot\s+dog\b"],
    "twenty_fathom_line": [r"\b(?:20|twenty)[- ]?fathom(?:\s+line)?\b"],
    "twenty_six_mile_hill": [r"\b(?:26|twenty[- ]?six)[- ]?mile\s+hill\b"],
    "the_cigar": [r"\bthe\s+cigar\b(?!\s*store)"],
    "the_fingers": [r"\bthe\s+fingers\b"],
    "the_parking_lot": [r"\bthe\s+parking\s+lot\b"],
    "fenwick_shoal": [r"\bfenwick\s+shoal\b"],
    "delaware_bay": [r"\bdelaware\s+bay\b"],
    "cape_may_rips": [r"\bcape\s+may\s+rips\b"],
    "ocean_city_md": [r"\bocean\s+city(?:\s+md|,\s+md|\s+maryland)?\b", r"\bOCMD\b", r"\bOC\s+MD\b"],
    "chesapeake_bay": [r"\bchesapeake\s+bay\b", r"\bCBBT\b", r"\bbay\s+bridge\s+tunnel\b"],
    "virginia_beach": [r"\bvirginia\s+beach\b", r"\bVA\s+beach\b", r"\brudee\s+inlet\b"],
    "oregon_inlet": [r"\boregon\s+inlet\b"],
    "hatteras": [r"\bhatteras\b", r"\bcape\s+hatteras\b", r"\bdiamond\s+shoals?\b"],
    "the_point_hatteras": [r"\bthe\s+point\b(?=.*hatteras|.*OBX)"],
    "obx": [r"\bOBX\b", r"\bouter\s+banks\b"],
    "sea_girt": [r"\bsea\s+girt\b"],
    "atlantic_city": [r"\batlantic\s+city\b"],
    "shinnecock_inlet": [r"\bshinnecock\s+inlet\b"],
    "moriches_inlet": [r"\bmoriches\s+inlet\b"],

    # v24.113 (2026-09-11 First Mate) — added after transcript-mining review.
    # These are classic NE/Mid-Atl spots captains actually name in their videos
    # but our extractor was missing. Each caught in the current corpus at least
    # once; adding them ensures future mentions become corroboration signals.
    "axel_carlson_reef": [r"\baxel\s+carlson(?:\s+reef)?\b"],  # NJ artificial reef
    "crab_ledge": [r"\bcrab\s+ledge\b"],                        # Cape Cod Bay S corner
    "shark_river_reef": [r"\bshark\s+river(?:\s+reef)?\b"],     # NJ nearshore
    "sea_bright": [r"\bsea\s+bright\b"],                        # NJ shore captain marker
    "the_mud_hole": [r"\bmud\s+hole\b(?!\s+guy|\s+news)", r"\bthe\s+mud\s+hole\b"],  # NY bight canyon feeder
    "cholera_bank": [r"\bcholera\s+bank\b"],                    # NY bight
    # (dropped: "the_farms" — too ambiguous without a specific zone target)
    "seventeen_fathoms": [r"\b(?:17|seventeen)[- ]?fathom\b"],  # LI bight
    "twenty_seven_fathom": [r"\b(?:27|twenty[- ]?seven)[- ]?fathom\b"],  # NJ bight

    "charleston_bump": ['\\bcharleston\\s+bump\\b'],
    "georgetown_hole": ['\\bgeorgetown\\s+hole\\b'],
    "the_big_rock": ['\\bthe\\s+big\\s+rock\\b', '\\bbig\\s+rock\\b(?=\\s*(tournament|invitational)?)'],
    "the_same_ol": ["\\bthe\\s+same\\s+ol'?\\b", '\\b100/400\\b'],
    "frying_pan_shoals": ['\\bfrying\\s+pan\\s+(?:shoals?|tower)\\b'],
    "grays_reef": ["\\bgray'?s\\s+reef\\b"],
    "hilton_head_snapper": ['\\bhilton\\s+head\\s+snapper\\b'],
    "wrightsville_rocks": ['\\bwrightsville\\s+(?:15|23)-?mile\\b'],
    "the_steeples": ['\\bthe\\s+steeples\\b'],
    "manning_line": ['\\bmanning\\s+line\\b'],
    "elton_bottom": ['\\belton\\s+bottom\\b'],
    "20fathom_jax": ['\\b20-?fathom\\s+ledge\\b'],
    "point_avon": ['\\bthe\\s+point\\b.*\\bavon\\b|\\bavon.{0,20}the\\s+point\\b'],
    "molasses_reef": ['\\bmolasses\\s+reef\\b'],
    "alligator_reef": ['\\balligator\\s+reef\\b'],
    "sombrero_reef": ['\\bsombrero\\s+reef\\b'],
    "looe_key": ['\\blooe\\s+key\\b'],
    "american_shoal": ['\\bamerican\\s+shoal\\b'],
    "marathon_hump": ['\\bmarathon\\s+(?:west\\s+)?hump\\b'],
    "east_hump": ['\\beast\\s+hump\\b'],
    "islamorada_hump": ['\\bislamorada\\s+hump\\b'],
    "409_hole": ['\\b409\\s+(?:hole|ledge)\\b'],
    "wood_wall": ['\\bwood\\s+wall\\b'],
    "riley_hump": ["\\briley'?s\\s+hump\\b"],
    "rebecca_shoal": ['\\brebecca\\s+shoal\\b'],
    "dry_tortugas": ['\\bdry\\s+tortugas\\b', '\\btortugas\\b'],
    "jupiter_ledge": ['\\bjupiter\\s+ledge\\b'],
    "sailfish_alley": ['\\bsailfish\\s+alley\\b'],
    "hillsboro_ledge": ['\\bhillsboro\\s+ledge\\b'],
    "bahamas_edge": ['\\bbahamas\\s+edge\\b', '\\bbimini\\s+edge\\b'],
    "fowey_rocks": ['\\bfowey\\s+rocks\\b'],
    "miami_ledge": ['\\bmiami\\s+(?:150|180|190)\\s+(?:ledge|trough)\\b'],
    "pulley_ridge": ['\\bpulley\\s+ridge\\b'],
    "copenhagen_wreck": ['\\bcopenhagen\\s+wreck\\b'],
    "vandenberg_wreck": ['\\bvandenberg\\b'],
    "gulf_stream_edge": ['\\bgulf\\s+stream\\s+edge\\b'],
    "the_nipple": ['\\bthe\\s+nipple\\b'],
    "the_elbow": ['\\bthe\\s+elbow\\b'],
    "the_spur": ['\\bthe\\s+spur\\b'],
    "petronius": ['\\bpetronius\\b'],
    "ram_powell": ['\\bram\\s+powell\\b'],
    "midnight_lump": ['\\bmidnight\\s+lump\\b', '\\bsackett\\s+bank\\b'],
    "perdido_pass": ['\\bperdido\\s+pass\\b'],
    "trysler_grounds": ['\\btrysler\\s+grounds\\b'],
    "usa_oriskany": ['\\bUSS\\s+oriskany\\b'],
    "florida_middle": ['\\bflorida\\s+middle\\s+grounds?\\b'],
    "chesapeake_light": ['\\bchesapeake\\s+light\\b'],
    "cbbt": ['\\bCBBT\\b', '\\bchesapeake\\s+bay\\s+bridge\\s+tunnel\\b'],
    "triangle_wrecks": ['\\btriangle\\s+wrecks\\b'],
    "toms_canyon": ['\\btoms?\\s+canyon\\b'],
    "lindenkohl_canyon": ['\\blindenkohl\\s+canyon\\b'],
    "spencer_canyon": ['\\bspencer\\s+canyon\\b'],
    "the_hot_dog": ['\\bthe\\s+hot\\s+dog\\b', '\\bjackspot\\b'],
    "the_chicken_bone": ['\\bthe\\s+chicken\\s+bone\\b'],
    "great_gully": ['\\bgreat\\s+gully\\b'],
    "bacardi_wreck": ['\\bbacardi\\s+wreck\\b'],
    "immigrant_wreck": ['\\bimmigrant\\s+wreck\\b'],
    "mohawk_wreck": ['\\bmohawk\\s+wreck\\b'],
}

# Size vocabulary — a bluefin quote with these clues gets classified for us.
GIANT_HINTS = [r"\bgiant\b", r"\bharpoon", r"\b(?:200|250|300|400|500|600|700|800)\+?\s*(?:lb|pound)"]
RECREATIONAL_HINTS = [r"\bschool\b", r"\bshort\b", r"\brec\b", r"\brecreation", r"\b(?:47|50|55|60|65|70|73)\s*(?:inch|\")"]

# Randy 2026-08-08: "When you scour those YouTube things and listen to what
# the captains say, you should be looking for bait information and whale
# sightings in the text or verbiage." Title + description scanning for both,
# using the Captain skill's canonical bait ranking + whale species list.
# NOTE: RSS feeds only expose title + description text; the ACTUAL spoken
# verbiage in videos would require pulling YouTube auto-captions (future
# enhancement — see the youtube_transcript_api option in TODO). Even title
# + description scanning catches ~20% of videos in a typical week.
BAIT_PATTERNS: dict[str, list[str]] = {
    "sand_eels": [r"\bsand\s*eels?\b", r"\bsand\s*lance\b"],
    "squid": [r"\bsquid\b", r"\bcalamari\b"],
    "bunker": [r"\bbunker\b", r"\bmenhaden\b", r"\bmossbunker\b", r"\bpogies?\b"],
    "peanut_bunker": [r"\bpeanut\s+bunker\b", r"\bpeanuts?\b(?=\s|,|\.|$)"],
    "butterfish": [r"\bbutterfish\b"],
    "mackerel": [r"\bmackerel\b", r"\btinker\s+mack(?:erel)?\b"],
    "silversides": [r"\bsilversides?\b", r"\bspearing\b"],
    "shad": [r"\bshad\b(?!\s*bush)"],   # exclude "shad bush"
    "sardines": [r"\bsardines?\b"],
    "anchovies": [r"\banchov(?:y|ies)\b"],
    "herring": [r"\bherring\b"],
    "chicken_scratch": [r"\bchicken\s+scratch\b"],   # small squid/sand-eel slurry
    "bait_generic": [r"\bloaded\s+with\s+bait\b", r"\bbait\s+ball", r"\bpush(?:ing)?\s+bait\b",
                     r"\bbait\s+everywhere\b", r"\bmarking\s+bait\b"],
}

# Whale / dolphin / porpoise mentions — normalized species keys mirror the
# Captain skill's whale_sightings.species vocabulary.
WHALE_PATTERNS: dict[str, list[str]] = {
    "humpback":     [r"\bhumpback(?:s|\s+whales?)?\b"],
    "finback":      [r"\bfinback\b", r"\bfin\s+whale\b"],
    "minke":        [r"\bminke\b"],
    "sei":          [r"\bsei\s+whale\b"],
    "right_whale":  [r"\bright\s+whale\b", r"\bnarw\b"],   # North Atlantic right whale
    "sperm_whale":  [r"\bsperm\s+whale\b"],
    "pilot_whale":  [r"\bpilot\s+whale\b"],
    "bottlenose_dolphin": [r"\bbottlenose(?:\s+dolphin)?\b"],
    "common_dolphin":     [r"\bcommon\s+dolphin\b", r"\bshort[-\s]beaked\s+common\b"],
    "risso_dolphin":      [r"\brisso[''`]?s?\s+dolphin\b"],
    "dolphin_generic":    [r"\bdolphins?\b(?!\s*fish)"],   # exclude dolphinfish (mahi)
    "porpoise":     [r"\bporpoises?\b", r"\bharbor\s+porpoise\b"],
    "whale_generic":[r"\bwhales?\s+(?:working|feeding|breaching|around|nearby|spotted|surface|breach)\b",
                     r"\bfind(?:ing)?\s+the\s+whales?\b",   # Randy's rule surfaces this
                     r"\bwhales?\s+and\s+dolphins?\b"],
}

# Trip-recap signal — the more of these that hit, the more likely this is
# a real dated trip report rather than a tactics video or product review.
TRIP_RECAP_HINTS = [
    r"\btoday\b", r"\byesterday\b", r"\bthis\s+morning\b",
    r"\bthis\s+week\b", r"\breport\b",
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\b",
    r"\b\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?\b",   # 7/28 or 7-28-26
    r"\bcaught\b", r"\bhookup\b", r"\bhook[- ]up\b", r"\blanded\b", r"\brelease\b",
    r"\bblitz\b", r"\bon\s+fire\b", r"\bcrushing\b", r"\bslammed\b",
]

# Tactics/product/review markers — these dilute the trip-recap signal.
NON_RECAP_HINTS = [
    r"\btop\s*\d+\b", r"\bhow\s+to\b", r"\btutorial\b", r"\breview\b",
    r"\bproduct\b", r"\bnew\s+product\b", r"\bbuyer(?:'s)?\s+guide\b",
    r"\bcomparison\b", r"\bICAST\b", r"\bepisode\s+\d+\b",
    r"\bpodcast\b(?!\s+report)", r"\bDIY\b",
    r"\bmerchandise\b", r"\bhat\b", r"\bshirt\b",
]

# Compile regexes once
def _compile_map(m: dict[str, list[str]]) -> dict[str, list[re.Pattern[str]]]:
    return {k: [re.compile(p, re.IGNORECASE) for p in v] for k, v in m.items()}

_species_re = _compile_map(SPECIES_PATTERNS)
_location_re = _compile_map(LOCATION_PATTERNS)
_bait_re = _compile_map(BAIT_PATTERNS)     # v23.11: bait mentions from title+desc
_whale_re = _compile_map(WHALE_PATTERNS)   # v23.11: whale/dolphin mentions


# v24.92 (Randy 2026-09-03) — auto-generate location regexes from
# youtube_zone_map.json keys that don't have explicit LOCATION_PATTERNS entries.
# When adding a new zone-mapping to youtube_zone_map.json, the location key
# ("cape lookout", "boca grande pass", etc.) automatically becomes a searchable
# word-boundary regex — no need to also edit LOCATION_PATTERNS. Hand-curated
# patterns in LOCATION_PATTERNS always win (allowing tighter regex like
# alternate spellings, or the case where two zone-map keys map to the same
# zone but need distinct regex).
def _augment_location_re_from_zone_map():
    """Auto-add simple word-boundary regexes for zone-map keys we don't have."""
    global _location_re
    p = pathlib.Path(__file__).parent / "data" / "youtube_zone_map.json"
    if not p.exists():
        return
    try:
        data = json.loads(p.read_text())
        loc_to_zones = data.get("location_to_zones", {}) or {}
    except Exception:
        return
    added = 0
    for loc_key in loc_to_zones:
        if loc_key in _location_re:
            continue  # explicit entry wins
        # Build a simple regex: word-boundary + literal key (whitespace
        # collapsed so "boca grande pass" matches "Boca Grande Pass").
        escaped = re.escape(loc_key).replace(r"\ ", r"\s+")
        try:
            pat = re.compile(rf"\b{escaped}\b", re.IGNORECASE)
        except re.error:
            continue
        _location_re[loc_key] = [pat]
        added += 1
    if added:
        # Only print in verbose mode — module load is silent by default.
        pass

_augment_location_re_from_zone_map()
_giant_re = [re.compile(p, re.IGNORECASE) for p in GIANT_HINTS]
_rec_re = [re.compile(p, re.IGNORECASE) for p in RECREATIONAL_HINTS]
_recap_re = [re.compile(p, re.IGNORECASE) for p in TRIP_RECAP_HINTS]
_nonrecap_re = [re.compile(p, re.IGNORECASE) for p in NON_RECAP_HINTS]


# ---------------------------------------------------------------------------
# Feed fetching + parsing
# ---------------------------------------------------------------------------

def _fetch_feed(channel_id: str, timeout: int = 20) -> ET.Element | None:
    """v24.105 — 3-attempt retry with backoff [2s, 6s]. YouTube RSS
    routinely 429s or times out during the harvester's 15+ channel sweep;
    silent-return-None dropped whole channels' worth of intel for the day.
    Only retries on transient errors; XML parse errors don't retry (broken
    feed content won't get better on second try)."""
    import time as _time
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    backoffs = [2, 6]
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 FishFinderHarvester/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
            return ET.fromstring(body)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            # 404 = channel deleted, don't retry
            if isinstance(e, urllib.error.HTTPError) and e.code == 404:
                return None
            if attempt < len(backoffs):
                _time.sleep(backoffs[attempt])
        except ET.ParseError:
            return None
    return None


def _parse_entries(root: ET.Element) -> list[dict[str, Any]]:
    entries = []
    for e in root.findall(f"{_ATOM_NS}entry"):
        title = (e.findtext(f"{_ATOM_NS}title") or "").strip()
        pub = (e.findtext(f"{_ATOM_NS}published") or "").strip()
        video_id = (e.findtext(f"{_YT_NS}videoId") or "").strip()
        # Description lives inside <media:group><media:description>
        media_group = e.find(f"{_MEDIA_NS}group")
        desc = ""
        if media_group is not None:
            desc = (media_group.findtext(f"{_MEDIA_NS}description") or "").strip()
        link_el = e.find(f"{_ATOM_NS}link")
        link = link_el.get("href") if link_el is not None else f"https://www.youtube.com/watch?v={video_id}"
        try:
            pub_dt = _dt.datetime.fromisoformat(pub.replace("Z", "+00:00"))
        except ValueError:
            pub_dt = None
        entries.append({
            "title": title,
            "description": desc,
            "published": pub,
            "published_dt": pub_dt,
            "video_id": video_id,
            "link": link,
        })
    entries.sort(
        key=lambda x: x["published_dt"] or _dt.datetime.min.replace(tzinfo=_dt.timezone.utc),
        reverse=True,
    )
    return entries


# ---------------------------------------------------------------------------
# Extraction — turn a title+description into structured intel
# ---------------------------------------------------------------------------

def _hits(patterns: list[re.Pattern[str]], text: str) -> int:
    return sum(1 for p in patterns if p.search(text))


def _map_hits(pattern_map: dict[str, list[re.Pattern[str]]], text: str) -> list[str]:
    hits = []
    for key, pats in pattern_map.items():
        if any(p.search(text) for p in pats):
            hits.append(key)
    return hits


def extract_intel(title: str, description: str) -> dict[str, Any]:
    """Extract structured intel from a video's title + description."""
    text = f"{title}\n{description}"
    species_hits = _map_hits(_species_re, text)

    # Bluefin size classification — override bare "bluefin_recreational"
    # if giant hints present, or if title says "bluefin" but description is empty
    if "bluefin_recreational" in species_hits and _hits(_giant_re, text) > 0:
        species_hits = [s for s in species_hits if s != "bluefin_recreational"]
        if "bluefin_giant" not in species_hits:
            species_hits.append("bluefin_giant")

    location_hits = _map_hits(_location_re, text)
    # v23.11 (Randy 2026-08-08): pull bait + whale mentions the same way.
    bait_hits = _map_hits(_bait_re, text)
    whale_hits = _map_hits(_whale_re, text)
    # Collapse "whale_generic" and "dolphin_generic" if a specific species
    # already matched — a video that says "humpback whales working the bank"
    # counts as humpback, not as generic-whale-AND-humpback.
    specific_whales = {"humpback", "finback", "minke", "sei", "right_whale",
                       "sperm_whale", "pilot_whale", "bottlenose_dolphin",
                       "common_dolphin", "risso_dolphin", "porpoise"}
    if any(w in specific_whales for w in whale_hits):
        whale_hits = [w for w in whale_hits if w not in ("whale_generic", "dolphin_generic")]

    recap_score = _hits(_recap_re, text)
    nonrecap_score = _hits(_nonrecap_re, text)
    is_trip_recap = recap_score >= 2 and recap_score > nonrecap_score

    return {
        "species": species_hits,
        "locations": location_hits,
        "bait": bait_hits,        # v23.11 new
        "whales": whale_hits,     # v23.11 new
        "is_trip_recap": is_trip_recap,
        "recap_score": recap_score,
        "nonrecap_score": nonrecap_score,
    }


# ---------------------------------------------------------------------------
# Top-level harvest
# ---------------------------------------------------------------------------

# v23.12 (Randy 2026-08-08) — transcript fetch + on-disk cache. Returns the
# full transcript text (untrimmed) OR None if unavailable. Cached entries are
# JSON files keyed by video_id; a cache miss makes exactly one network call.
def _fetch_transcript(video_id: str, timeout: float = 10.0) -> dict[str, Any] | None:
    if not _TRANSCRIPTS_AVAILABLE or not video_id:
        return None
    cache_path = _TRANSCRIPT_CACHE_DIR / f"{video_id}.json"
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text())
        except Exception:
            pass
    try:
        data = _YTT_API.fetch(video_id)
        snippets = data.snippets if hasattr(data, "snippets") else list(data)
        if not snippets:
            return None
        # snippet objects expose .text (v1.x); tuple/dict from older APIs
        # exposes ["text"]. Support both.
        text_parts = []
        for s in snippets:
            t = getattr(s, "text", None)
            if t is None and isinstance(s, dict):
                t = s.get("text", "")
            if t:
                text_parts.append(t)
        full = " ".join(text_parts).strip()
        if not full:
            return None
        # Normalize whitespace so regex boundaries work reliably
        full = re.sub(r"\s+", " ", full)
        result = {
            "video_id": video_id,
            "fetched_at": _dt.datetime.utcnow().isoformat() + "Z",
            "snippet_count": len(snippets),
            "char_count": len(full),
            "text": full,
        }
        try:
            cache_path.write_text(json.dumps(result))
        except Exception:
            pass
        return result
    except Exception:
        # Any failure (no captions available, rate-limited, blocked, etc.)
        # falls back to "no transcript" silently. Cache a NEGATIVE result so
        # we don't hammer the API for videos that don't have captions.
        try:
            cache_path.write_text(json.dumps({
                "video_id": video_id,
                "fetched_at": _dt.datetime.utcnow().isoformat() + "Z",
                "unavailable": True,
            }))
        except Exception:
            pass
        return None


def harvest_channel(channel_def: dict[str, Any], cutoff_days: int = 14) -> dict[str, Any]:
    """Fetch one channel's RSS feed and return recent-window intel.

    Returns TWO parallel lists so we never lose raw source data (Randy's
    2026-08-08 directive — accumulate over prune):

    - `recent_videos`  → filtered list the MODEL currently uses (species /
                         location / trip-recap match). This drives
                         youtubeCorroborationBoost via zone_mentions.
    - `all_videos_in_window` → EVERY entry within the cutoff window, raw
                         title + description + published + link + video_id,
                         plus the intel extraction we ran (in case a future
                         model wants to re-score them with different
                         patterns). This is the archive-preservation copy.

    In-season we routinely fetch 150-300 raw entries across 15 channels; the
    filter drops ~80% of them because the current patterns are conservative.
    Six months from now when the accuracy loop tells us WHICH channels
    consistently precede real catch reports, we'll want the raw text.
    """
    cid = channel_def["channel_id"]
    now = _dt.datetime.now(_dt.timezone.utc)
    cutoff = now - _dt.timedelta(days=cutoff_days)

    root = _fetch_feed(cid)
    if root is None:
        return {
            "id": channel_def["id"],
            "name": channel_def["name"],
            "channel_id": cid,
            "ok": False,
            "error": "fetch failed",
            "recent_videos": [],
            "all_videos_in_window": [],
        }

    entries = _parse_entries(root)
    recent: list[dict[str, Any]] = []
    all_in_window: list[dict[str, Any]] = []
    for e in entries:
        if not (e["published_dt"] and e["published_dt"] > cutoff):
            continue
        # v23.12: pull transcript (cached after first fetch). Fall back to
        # empty string if unavailable — extract_intel just sees title + desc.
        transcript_obj = _fetch_transcript(e.get("video_id", "")) if _TRANSCRIPTS_AVAILABLE else None
        transcript_text = (transcript_obj or {}).get("text", "") if transcript_obj and not transcript_obj.get("unavailable") else ""
        # Two-pass extraction: what came from title+description, and what
        # was found ONLY in the transcript. Enables Randy to differentiate
        # "captain wrote about bunker" vs "captain said bunker on video."
        intel = extract_intel(e["title"], e["description"])
        if transcript_text:
            transcript_intel = extract_intel("", transcript_text)
            # Union each field, tag transcript-only mentions separately for
            # downstream (build-inlined can filter or downgrade if needed).
            def _new_only(all_hits, base_hits):
                return sorted(set(all_hits) - set(base_hits))
            intel["transcript_species"] = _new_only(transcript_intel["species"], intel["species"])
            intel["transcript_locations"] = _new_only(transcript_intel["locations"], intel["locations"])
            intel["transcript_bait"] = _new_only(transcript_intel["bait"], intel["bait"])
            intel["transcript_whales"] = _new_only(transcript_intel["whales"], intel["whales"])
            # Merge into main hits so downstream code that reads only
            # `species`/`locations`/`bait`/`whales` gets the full picture.
            intel["species"] = sorted(set(intel["species"]) | set(transcript_intel["species"]))
            intel["locations"] = sorted(set(intel["locations"]) | set(transcript_intel["locations"]))
            intel["bait"] = sorted(set(intel["bait"]) | set(transcript_intel["bait"]))
            intel["whales"] = sorted(set(intel["whales"]) | set(transcript_intel["whales"]))
            intel["has_transcript"] = True
            intel["transcript_chars"] = len(transcript_text)
        else:
            intel["transcript_species"] = []
            intel["transcript_locations"] = []
            intel["transcript_bait"] = []
            intel["transcript_whales"] = []
            intel["has_transcript"] = False
            intel["transcript_chars"] = 0
        # RAW preservation copy — every video in window, no filter. We keep
        # description truncated to 800 chars so a channel with a novel-length
        # description doesn't bloat the archive uncontrollably (typical desc
        # is 200-400 chars; 800 keeps almost all real content).
        desc = e.get("description", "") or ""
        # v23.12: trim transcript for archive storage but preserve enough
        # for post-hoc re-analysis. 40K chars ≈ full length of a 30-min video.
        transcript_stored = ""
        if transcript_text:
            transcript_stored = transcript_text[:_TRANSCRIPT_STORE_MAX_CHARS]
            if len(transcript_text) > _TRANSCRIPT_STORE_MAX_CHARS:
                transcript_stored += "…"
        all_in_window.append({
            "title": e["title"],
            "description": desc[:800] + ("…" if len(desc) > 800 else ""),
            "published": e["published"],
            "link": e["link"],
            "video_id": e["video_id"],
            "species": intel["species"],
            "locations": intel["locations"],
            "bait": intel["bait"],
            "whales": intel["whales"],
            # v23.12: transcript-only hits (what the captain SAID but didn't
            # type in the description) and the full stored transcript text.
            "transcript_species": intel["transcript_species"],
            "transcript_locations": intel["transcript_locations"],
            "transcript_bait": intel["transcript_bait"],
            "transcript_whales": intel["transcript_whales"],
            "has_transcript": intel["has_transcript"],
            "transcript_chars": intel["transcript_chars"],
            "transcript": transcript_stored,
            "is_trip_recap": intel["is_trip_recap"],
            "matched_filter": bool(intel["species"] or intel["locations"] or intel["is_trip_recap"]
                                    or intel["bait"] or intel["whales"]),
        })
        # FILTERED copy — feeds the live model signal.
        if (intel["species"] or intel["locations"] or intel["is_trip_recap"]
                or intel["bait"] or intel["whales"]):
            recent.append({
                "title": e["title"],
                "published": e["published"],
                "link": e["link"],
                "species": intel["species"],
                "locations": intel["locations"],
                "bait": intel["bait"],
                "whales": intel["whales"],
                # v23.12: pass-through to help downstream attribute where a
                # mention came from ("in the video" vs "in the description").
                "has_transcript": intel["has_transcript"],
                "transcript_chars": intel["transcript_chars"],
                "is_trip_recap": intel["is_trip_recap"],
            })

    return {
        "id": channel_def["id"],
        "name": channel_def["name"],
        "channel_id": cid,
        "tier": channel_def.get("tier"),
        "area": channel_def.get("area"),
        "ok": True,
        "recent_videos": recent,
        "all_videos_in_window": all_in_window,
        "kept_count": len(recent),
        "raw_count": len(all_in_window),
        "total_in_window": sum(
            1 for e in entries
            if e["published_dt"] and e["published_dt"] > cutoff
        ),
    }


def _load_zone_map(zone_map_path: pathlib.Path | str | None) -> dict[str, list[str]]:
    """Load the location→zone_ids map. Empty dict if the file is absent."""
    if zone_map_path is None:
        return {}
    p = pathlib.Path(zone_map_path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
        return data.get("location_to_zones", {}) or {}
    except Exception:
        return {}


def _compute_zone_mentions(
    per_channel_results: list[dict[str, Any]],
    location_to_zones: dict[str, list[str]],
) -> dict[str, dict[str, Any]]:
    """Roll up per-zone mentions across all channels.

    For each zone_id we track:
      - `channels`: unique list of channel names that mentioned any of the
        zone's aliased locations in the window
      - `video_count`: total number of videos that mentioned a zone alias
      - `top_titles`: up to 3 sample video titles (most recent first)
      - `via_locations`: which location aliases pointed to this zone
    """
    zone_mentions: dict[str, dict[str, Any]] = {}
    for r in per_channel_results:
        if not r.get("ok"):
            continue
        cname = r["name"]
        for v in r.get("recent_videos", []):
            # Determine which zones this video hits, tracking WHICH location
            # alias pointed to WHICH zone (per-zone attribution). A video
            # with locations ["montauk", "block_island"] hits many zones —
            # but each zone only credits the alias(es) that actually mapped
            # to it in youtube_zone_map.json.
            per_zone_locs: dict[str, set[str]] = {}
            for loc in v.get("locations", []):
                for zid in location_to_zones.get(loc, []):
                    per_zone_locs.setdefault(zid, set()).add(loc)
            for zid, locs_that_hit in per_zone_locs.items():
                entry = zone_mentions.setdefault(zid, {
                    "channels": set(),
                    "video_count": 0,
                    "top_titles": [],
                    "via_locations": set(),
                })
                entry["channels"].add(cname)
                entry["video_count"] += 1
                if len(entry["top_titles"]) < 3:
                    entry["top_titles"].append({
                        "title": v.get("title"),
                        "channel": cname,
                        "published": v.get("published"),
                        "link": v.get("link"),
                    })
                entry["via_locations"].update(locs_that_hit)
    # Convert sets to sorted lists for JSON serialization
    out: dict[str, dict[str, Any]] = {}
    for zid, e in zone_mentions.items():
        out[zid] = {
            "channels": sorted(e["channels"]),
            "channel_count": len(e["channels"]),
            "video_count": e["video_count"],
            "top_titles": e["top_titles"],
            "via_locations": sorted(e["via_locations"]),
        }
    return out


def harvest_all(
    channels_path: pathlib.Path | str,
    cutoff_days: int = 14,
    verbose: bool = False,
    pause_seconds: float = 0.25,
    zone_map_path: pathlib.Path | str | None = None,
) -> dict[str, Any]:
    """Harvest all channels listed in youtube_channels.json.

    If `zone_map_path` points to a file like `data/youtube_zone_map.json`,
    each channel result is post-processed into a `zone_mentions` dict that
    ties YouTube location mentions to specific zone_ids in zones.json — this
    is what powers the client-side `youtubeCorroborationBoost` signal.
    """
    channels_path = pathlib.Path(channels_path)
    data = json.loads(channels_path.read_text())
    channels = data.get("channels", [])

    # Auto-locate the zone map next to the channels file if not passed.
    if zone_map_path is None:
        auto = channels_path.parent / "youtube_zone_map.json"
        if auto.exists():
            zone_map_path = auto
    location_to_zones = _load_zone_map(zone_map_path)

    if verbose:
        print(f"Harvesting {len(channels)} YouTube channels (cutoff={cutoff_days}d)...")
        print(f"  Zone map: {'loaded ('+str(len(location_to_zones))+' locations)' if location_to_zones else 'not found — zone_mentions will be empty'}")

    results = []
    for c in channels:
        r = harvest_channel(c, cutoff_days=cutoff_days)
        results.append(r)
        if verbose:
            if r.get("ok"):
                print(f"  [{r['tier']:>10}] {r['name']:<45} {r['kept_count']:>2}/{r['total_in_window']:>2} kept")
            else:
                print(f"  [{c.get('tier',''):>10}] {c['name']:<45} FAIL ({r.get('error')})")
        time.sleep(pause_seconds)

    # Roll-up: aggregate corroborating locations + species + bait + whales
    # across all channels. Bait and whales are v23.11 additions per Randy's
    # ask that YouTube scanning surface those signals too.
    location_mentions: dict[str, list[str]] = {}
    species_mentions: dict[str, list[str]] = {}
    bait_mentions: dict[str, list[dict[str, Any]]] = {}      # v23.11
    whale_mentions: dict[str, list[dict[str, Any]]] = {}     # v23.11
    for r in results:
        if not r.get("ok"):
            continue
        for v in r["recent_videos"]:
            for loc in v.get("locations", []):
                location_mentions.setdefault(loc, []).append(r["name"])
            for sp in v.get("species", []):
                species_mentions.setdefault(sp, []).append(r["name"])
            # Track full-context bait entries — need date + locations for
            # build-inlined.py to build bait_intel entries from them.
            for b in v.get("bait", []):
                bait_mentions.setdefault(b, []).append({
                    "channel": r["name"], "date": (v.get("published") or "")[:10],
                    "title": v.get("title"), "link": v.get("link"),
                    "locations": v.get("locations", []),
                })
            for w in v.get("whales", []):
                whale_mentions.setdefault(w, []).append({
                    "channel": r["name"], "date": (v.get("published") or "")[:10],
                    "title": v.get("title"), "link": v.get("link"),
                    "locations": v.get("locations", []),
                })

    # Dedup source lists per key (locations + species only — bait/whales
    # keep the full entry list because build-inlined needs the context).
    for k in location_mentions:
        location_mentions[k] = sorted(set(location_mentions[k]))
    for k in species_mentions:
        species_mentions[k] = sorted(set(species_mentions[k]))

    # Per-zone rollup — this is what the client-side effectiveHeat signal uses
    zone_mentions = _compute_zone_mentions(results, location_to_zones)

    out = {
        "harvested_at": _dt.datetime.utcnow().isoformat() + "Z",
        "cutoff_days": cutoff_days,
        "channel_count": len(channels),
        "channels_ok": sum(1 for r in results if r.get("ok")),
        "total_videos_in_window": sum(r.get("kept_count", 0) for r in results if r.get("ok")),
        "channels": results,
        "corroboration": {
            "locations": location_mentions,
            "species": species_mentions,
            "bait": bait_mentions,       # v23.11
            "whales": whale_mentions,    # v23.11
        },
        "zone_mentions": zone_mentions,
    }
    # v24.100 source-health record — success == at least one channel returned
    # a video in the cutoff window. Not tying to zone_mentions count because
    # a live feed with zero zone hits is different from a dead feed.
    try:
        import source_health as _sh
        _sh.record("youtube", rows=out["total_videos_in_window"], ok=(out["channels_ok"] > 0),
                   error=None if out["channels_ok"] > 0 else f"0/{out['channel_count']} channels returned videos")
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14, help="Cutoff window in days (default 14)")
    ap.add_argument("--channels", default=str(BASE / "data" / "youtube_channels.json"))
    ap.add_argument("--raw", action="store_true", help="Print raw JSON only")
    args = ap.parse_args()

    result = harvest_all(args.channels, cutoff_days=args.days, verbose=not args.raw)

    if args.raw:
        print(json.dumps(result, indent=2, default=str))
        return

    print()
    print(f"Total videos in {args.days}-day window: {result['total_videos_in_window']}")
    print(f"Channels responding: {result['channels_ok']}/{result['channel_count']}")
    print()

    corrob = result["corroboration"]
    if corrob["species"]:
        print("Species corroboration (which channels mentioned each in the window):")
        for sp, sources in sorted(corrob["species"].items(), key=lambda kv: -len(kv[1])):
            print(f"  {sp:<25} ({len(sources)} sources): {', '.join(sources)}")
    if corrob["locations"]:
        print()
        print("Location corroboration:")
        for loc, sources in sorted(corrob["locations"].items(), key=lambda kv: -len(kv[1])):
            print(f"  {loc:<25} ({len(sources)} sources): {', '.join(sources)}")
    zm = result.get("zone_mentions", {})
    if zm:
        print()
        print("Zone mentions (fed into youtubeCorroborationBoost signal):")
        for zid, e in sorted(zm.items(), key=lambda kv: -kv[1]["channel_count"]):
            corrob_flag = "★ CORROBORATED" if e["channel_count"] >= 2 else "  single source"
            print(f"  {zid:<30} {corrob_flag}  ({e['channel_count']} ch, {e['video_count']} vids): {', '.join(e['channels'])}")


if __name__ == "__main__":
    _main()
