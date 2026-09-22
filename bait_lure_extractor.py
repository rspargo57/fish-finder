#!/usr/bin/env python3
"""v24.117 — Bait & lure intel from YouTube transcripts.

v24.117 (2026-09-11): Vocabulary expansion from transcript-mined captain slang.
  - Split "pencil poppers" out of generic "poppers" (distinct fishing rig).
  - Added "hopkins spoons" category (Hopkins hammered stainless + Krocodile spoons).
  - Fixed "AA jig" → "Ava jig" (Ava is the real manufacturer name).
  - Expanded bunker-family regex to catch menhaden/pogies/freelining synonyms.
  - Added Super Strike Little Neck / Polaris to poppers, Salt Pro Minnow / Rebel
    Jumpin' Minnow to swimming plugs, Krocodile to spoons.


Randy 2026-09-11: "When we go out fishing for tuna in a certain spot, maybe
we're gonna have a portion that recommends what bait they're using to catch
them, what lures they're using to catch the fish at this each specific spot.
Have the First Mate look at that data and organize it so we can recommend
what things to use."

**How it works.** Reads the transcript cache (cache/yt_transcripts/*.json)
that the device-side catch-up task populates from Randy's residential IP.
For each transcript:
  1. Scan for BAIT_LURE_PATTERNS in a usage-context (using X, on X, with X,
     pulled X, throwing X, casting X, trolling X, jigging X, chunking X, etc.)
  2. Scan for LOCATION_PATTERNS in the same paragraph.
  3. Emit (zone_id, phrase, category, source_video, channel, date, snippet)
     tuples for the intel record.

**Aggregation.** For each zone, roll up:
  - Top 3 categories most mentioned (chunk bait, jigs, poppers, trolling…)
  - Top 3 specific bait/lure phrases across those categories
  - Source citations for each (channel + date), so Randy can verify

**Output.** `data/bait_lure_intel.json` — read by the map's popup renderer
to show "🎣 What captains use here" under each zone.

**Not a ranking signal (yet).** Feature is DISPLAY-only for now. Once we
have ground-truth catch outcomes on specific bait/lures over 30+ days, the
First Mate can consider promoting the strongest correlations to a real
signal in effectiveHeat(). For now it's just information Randy can act on.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import collections
from datetime import date, datetime

BASE = pathlib.Path(__file__).parent
CACHE_DIR = BASE / "cache" / "yt_transcripts"
OUT_PATH = BASE / "data" / "bait_lure_intel.json"

# Import location extractor from existing module (reuse — First Mate rule:
# don't duplicate what's already working).
sys.path.insert(0, str(BASE))
try:
    from youtube_harvest import LOCATION_PATTERNS, _compile_map  # type: ignore
    _loc_re = _compile_map(LOCATION_PATTERNS)
except Exception:  # graceful fallback
    _loc_re = {}

# =============================================================================
# BAIT / LURE / TECHNIQUE VOCABULARY
# =============================================================================
# Grouped by category. Each entry is (canonical_name, regex).
# Categories are what shows in the popup rollup; canonical names roll up
# variants (e.g. "chunk"/"chunks"/"chunking" all map to canonical "chunks").

BAIT_TERMS = {
    # ---- Live bait ----
    "live bunker":        r"\blive[- ]bunker\b|\blive[- ]pogies?\b|\bfreelin(?:ing|ed)\s+bunker\b|\blive[- ]lining\s+bunker\b|\blive[- ]menhaden\b",
    "peanut bunker":      r"\bpeanut\s+bunker\b|\bpeanuts\b(?=.*bunker|.*bait)|\bpeanut\s+menhaden\b",
    "adult bunker":       r"\badult\s+bunker\b|\bbunker\s+adults\b|\bjumbo\s+bunker\b|\bfull[- ]grown\s+bunker\b",
    "bunker chunks":      r"\bbunker\s+chunk(?:s|ing)?\b|\bchunk(?:ing|ed)?\s+bunker\b|\bmenhaden\s+chunk(?:s|ing)?\b|\bchunk(?:ing|ed)?\s+menhaden\b|\bbucket\s+of\s+chunks?\b",
    "live mackerel":      r"\blive[- ]macker(?:e)?l\b|\blive[- ]tinker(?:s)?\b",
    "live eels":          r"\blive[- ]eels?\b|\beels?\s+on\s+the?\s+bottom\b",
    "sand eels":          r"\bsand\s+eels?\b|\bsandeels?\b",
    "butterfish":         r"\bbutterfish\b",
    "squid strips":       r"\bsquid\s+strips?\b|\bcalamari\s+strips?\b",
    "live squid":         r"\blive\s+squid\b|\bwhole\s+squid\b",
    "ballyhoo":           r"\bballyhoo\b|\brigged\s+ballyhoo\b|\bnaked\s+ballyhoo\b",
    "spanish sardines":   r"\bspanish\s+sardines?\b|\bpilchards?\b",

    # ---- Jigs ----
    "diamond jigs":       r"\bdiamond\s+jig(?:s)?\b|\bAva\s+jig(?:s)?\b|\bAva\s+0*(?:07|17|27|47)s?\b|\bAA\s+jig(?:s)?\b|\bA47s?\b|\bA27s?\b",
    "hopkins spoons":     r"\bhopkins(?:\s+hammered)?(?:\s+spoon(?:s)?)?\b|\bhammered\s+stainless\b|\bhammered\s+spoon(?:s)?\b|\bkrocodile\s+spoon(?:s)?\b|\bkroc\s+spoon(?:s)?\b",
    "epoxy jigs":         r"\bepoxy\s+jig(?:s)?\b|\bhogy\s+epoxy\b",
    "vertical jigs":      r"\bvertical\s+jig(?:s|ging)?\b|\bspeed\s+jig(?:s|ging)?\b|\bknife\s+jig(?:s)?\b|\bbutterfly\s+jig(?:s)?\b",
    "slow-pitch jigs":    r"\bslow[- ]pitch(?:\s+jig(?:s|ging)?)?\b|\bnomad\s+buffalo\b|\bshimano\s+flat[- ]fall\b",
    "bucktails":          r"\bbucktails?\b|\bsmiling\s+bill\b|\bandrus\s+jetty[- ]caster\b|\bspro\s+bucktail\b",
    "shad jigs":          r"\bshad\s+jig(?:s)?\b|\bsassy\s+shad\b|\bstorm\s+wildeye\b",

    # ---- Plugs / topwater ----
    "pencil poppers":     r"\bpencil\s+popper(?:s)?\b|\bcotton\s+cordell\s+pencil\b|\bstan[- ]?gibbs\s+pencil\b|\bafw\s+pencil\b",
    # "poppers": exclude pencil poppers via negative lookbehind (they get their own bucket above)
    "poppers":            r"(?<!pencil\s)(?<!pencil )\bpoppers?\b|\btop[- ]?water(?:\s+plug(?:s)?)?\b|\bhurricane\s+popper\b|\bstan[- ]?gibbs\s+popper\b|\bsuper\s+strike\s+little\s+neck\b|\bpolaris\s+popper\b",
    "stickbaits":         r"\bstickbaits?\b|\bwalking\s+lures?\b|\bspook(?:s)?\b|\bdog\s+walk(?:er|ing)?\b|\bshimano\s+orca\b|\byo[- ]?zuri\s+hydro\b|\bnomad\s+madscad\b",
    "swimming plugs":     r"\bswim(?:ming)?\s+plug(?:s)?\b|\bsp\s+minnow\b|\bmag\s+darter\b|\bdanny\s+plug(?:s)?\b|\bbomber\s+long[- ]a\b|\bdaiwa\s+salt\s+pro\s+minnow\b|\brebel\s+jumpin\s+minnow\b",
    "metal lips":         r"\bmetal[- ]lip(?:s)?\b|\bpichney\b|\bgibbs\s+danny\b|\bhab(?:'|)?s\s+plug(?:s)?\b",
    "soft plastics":      r"\bsoft\s+plastic(?:s)?\b|\bhogy(?:\s+eel)?\b|\bronz\b|\bal\s+gag(?:'|)?s\b|\bslug[- ]?go(?:s)?\b|\bfin[- ]s(?:\.)?[- ]?fish\b",
    "flies":              r"\bstriper\s+fl(?:y|ies)\b|\bclouser(?:s)?\b|\bdeceiver(?:s)?\b|\bhalf\s+and\s+half\b",

    # ---- Trolling gear ----
    "spreader bars":      r"\bspreader\s+bar(?:s)?\b|\bchatter\s+bar(?:s)?\b|\bjoe\s+shute\s+spread(?:er)?\b",
    "daisy chains":       r"\bdaisy\s+chain(?:s)?\b|\bchain\s+of\s+ballyhoo\b",
    "green machines":     r"\bgreen\s+machine(?:s)?\b",
    "cedar plugs":        r"\bcedar\s+plug(?:s)?\b",
    "tuna feathers":      r"\btuna\s+feather(?:s)?\b|\btuna\s+jet(?:s)?\b|\bjoe\s+shute(?:s)?\b|\bilander(?:s)?\b",

    # ---- Techniques (not lures, but Randy's ask included how) ----
    "chunking":           r"\bchunk(?:ing|ed)\b(?!\s+bunker)",  # generic chunking (bunker chunks separately)
    "trolling":           r"\btroll(?:ing|ed)\b",
    "jigging":            r"\bjigging\b",
    "live-lining":        r"\blive[- ]lin(?:ing|ed)\b",
    "drifting":           r"\bdrift(?:ing|ed)\b(?!\s+netting)",
    "trolling ballyhoo":  r"\btroll(?:ing|ed).{0,30}ballyhoo\b",
    "casting topwater":   r"\bcast(?:ing|ed).{0,20}(?:topwater|popper|stickbait)\b",
}

# Usage-context indicators. A raw bait mention isn't intel — "we saw
# bunker" ≠ "we caught them on bunker". Require one of these verbs in a
# 60-char window BEFORE the bait/lure mention.
USAGE_CONTEXT = re.compile(
    r"\b(?:using|used|on|with|pull(?:ing|ed)?|throw(?:ing|n)?|threw|"
    r"cast(?:ing|ed|s)?|jigg(?:ing|ed)?|troll(?:ing|ed)?|chunk(?:ing|ed)?|"
    r"drift(?:ing|ed)?|live[- ]?lin(?:ing|ed)?|freelin(?:ing|ed)?|"
    r"swim(?:ming|s)?|tied\s+on|bit(?:e|ing)?\s+on|hit(?:ting)?\s+on|"
    r"caught\s+on|hooked\s+on|"
    r"crushed(?:\s+(?:them|it|em))?|smashed(?:\s+(?:them|it|em))?|"
    r"hammered(?:\s+(?:them|it|em))?|slam(?:med|ming)?(?:\s+(?:them|it|em))?|"
    r"pop\s+plugs?|throw(?:ing)?\s+plugs?|rip(?:ping|ped)?(?:\s+up)?\s+on|"
    r"deploy(?:ing|ed)?|pitch(?:ing|ed)?|snap(?:ping|ped)?\s+jig(?:s)?|"
    r"chunk(?:ing|ed)|freeline(?:d|s)?)\b",
    re.IGNORECASE,
)

# v24.115 (Randy 2026-09-11) — species context. Captains say
# "for tuna we trolled ballyhoo" and "we chunked bunker for stripers" —
# different bait for different species at the same zone. Tag each mention
# with the species discussed nearby so the popup can filter to what Randy
# is actually going after.
# v24.116 (Randy 2026-09-11) — bite window / timing patterns. Captains
# routinely say things like "morning bite on chunks", "dawn was on fire",
# "the full moon lit them up". Same extraction/attribution shape as
# bait/lure: canonical bucket + regex + usage context (implicit — timing
# mentions are almost always about fishing conditions).
BITE_WINDOW_PATTERNS = {
    "dawn / first light":     r"\b(?:first\s+light|at\s+dawn|before\s+sun[- ]?up|pre[- ]?dawn|crack\s+of\s+dawn|sun\s+up|at\s+day\s?break)\b",
    "morning bite":           r"\b(?:early\s+morning|morning\s+bite|morning\s+tide|morning\s+run)\b",
    "midday / noon":          r"\b(?:midday|mid[- ]?day|around\s+noon|high\s+noon|lunch\s+bite)\b",
    "afternoon bite":         r"\b(?:afternoon\s+bite|afternoon\s+tide|late\s+afternoon)\b",
    "evening / dusk":         r"\b(?:evening\s+bite|at\s+dusk|sunset\s+bite|late[- ]day\s+bite|dusk\s+and\s+dawn|dawn\s+and\s+dusk)\b",
    "night bite":             r"\b(?:night\s+bite|night\s+fishing|after\s+dark|nocturnal\s+bite|nighttime\s+bite)\b",
    "incoming tide":          r"\b(?:incoming\s+tide|flood\s+tide|rising\s+tide|inbound\s+tide|tide\s+coming\s+in)\b",
    "outgoing tide":          r"\b(?:outgoing\s+tide|ebb\s+tide|dropping\s+tide|out[- ]?bound\s+tide|tide\s+going\s+out)\b",
    "slack water":            r"\b(?:slack\s+tide|slack\s+water|top\s+of\s+the\s+tide|bottom\s+of\s+the\s+tide|dead\s+low|dead\s+high)\b",
    "new moon":               r"\b(?:new\s+moon)\b",
    "full moon":              r"\b(?:full\s+moon|around\s+the\s+full|the\s+full)\b",
}

SPECIES_PATTERNS = [
    ("bluefin",     r"\bbluefin(?:\s+tuna)?\b"),
    ("yellowfin",   r"\byellowfin(?:\s+tuna)?\b|\bYFT\b"),
    ("bigeye",      r"\bbigeye(?:\s+tuna)?\b|\bBET\b"),
    ("tuna",        r"\btuna\b(?!\s+ridge|\s+alley)"),  # generic tuna (not zone names)
    ("striper",     r"\bstripe[dr]?\s+bass\b|\bstripers?\b|\blinesiders?\b"),
    ("bluefish",    r"\bbluefish\b|\bblues\b(?=[\s,\.]|$)|\bchoppers?\b"),
    ("albie",       r"\balbies?\b|\bfalse\s+albacore\b"),
    ("bonito",      r"\bbonito(?:s|es)?\b"),
    ("mahi",        r"\bmahi(?:\s+mahi)?\b|\bdorado\b|\bdolphin\s?fish\b"),
    ("swordfish",   r"\bsword\s?fish\b|\bswords?\b(?=[\s,\.]|$)"),
    ("marlin",      r"\b(?:blue|white)\s+marlin\b|\bmarlin\b"),
    ("mako",        r"\bmako\s+shark\b|\bmakos?\b"),
    ("thresher",    r"\bthresher(?:\s+shark)?\b"),
    ("wahoo",       r"\bwahoo\b|\bhoo\b(?=[\s,\.]|$)"),
    ("sailfish",    r"\bsailfish\b|\bsails\b(?=[\s,\.]|$)"),
    ("fluke",       r"\bfluke\b|\bsummer\s+flounder\b"),
    ("black_sea_bass", r"\bblack\s?sea\s+bass\b|\bBSB\b"),
    ("tautog",      r"\btautog\b|\btogs?\b(?=[\s,\.]|$)|\bblackfish\b"),
    ("cod",         r"\bcod\s?fish\b|\bcod\b(?=[\s,\.]|$)"),
    ("kingfish",    r"\bking\s?mackerel\b|\bkingfish\b|\bkings?\b(?=[\s,\.]|$)"),
    ("spanish_mackerel", r"\bspanish\s+macker(?:e)?l\b"),
]
_SPECIES_RE = [(name, re.compile(pat, re.IGNORECASE)) for name, pat in SPECIES_PATTERNS]

# Categorize each canonical name into a group for the rollup.
CATEGORY = {
    "live bunker": "live bait", "peanut bunker": "live bait", "adult bunker": "live bait",
    "bunker chunks": "chunk bait", "live mackerel": "live bait", "live eels": "live bait",
    "sand eels": "live bait", "butterfish": "chunk bait", "squid strips": "cut bait",
    "live squid": "live bait", "ballyhoo": "rigged bait", "spanish sardines": "live bait",
    "diamond jigs": "jigs", "epoxy jigs": "jigs", "vertical jigs": "jigs",
    "slow-pitch jigs": "jigs", "bucktails": "jigs", "shad jigs": "jigs",
    "hopkins spoons": "spoons",
    "pencil poppers": "topwater", "poppers": "topwater", "stickbaits": "topwater", "swimming plugs": "plugs",
    "metal lips": "plugs", "soft plastics": "soft plastics", "flies": "flies",
    "spreader bars": "trolling gear", "daisy chains": "trolling gear",
    "green machines": "trolling gear", "cedar plugs": "trolling gear",
    "tuna feathers": "trolling gear",
    "chunking": "technique", "trolling": "technique", "jigging": "technique",
    "live-lining": "technique", "drifting": "technique",
    "trolling ballyhoo": "technique", "casting topwater": "technique",
}


def _extract_zone_mentions_for_text(text: str) -> list[tuple[int, str]]:
    """Return [(char_position, location_key), ...] for a transcript."""
    out = []
    for loc, rex_or_list in _loc_re.items():
        # _compile_map returns a list of compiled regexes per location key
        rexes = rex_or_list if isinstance(rex_or_list, list) else [rex_or_list]
        for rex in rexes:
            for m in rex.finditer(text):
                out.append((m.start(), loc))
    return out


def _extract_bait_lure_for_text(text: str) -> list[tuple[int, str, str]]:
    """Return [(char_position, canonical_name, snippet), ...] for a transcript."""
    out = []
    for canon, patt in BAIT_TERMS.items():
        for m in re.finditer(patt, text, re.IGNORECASE):
            # Check usage context in 80-char window before the match
            start = m.start()
            window_start = max(0, start - 80)
            context = text[window_start:start + len(m.group(0)) + 20]
            if USAGE_CONTEXT.search(context):
                snippet = text[max(0, start-60):start+100]
                out.append((start, canon, snippet))
    return out


def _extract_bite_windows_for_text(text: str) -> list[tuple[int, str, str]]:
    """v24.116: return [(char_position, canonical_window, snippet), ...]."""
    out = []
    for canon, patt in BITE_WINDOW_PATTERNS.items():
        for m in re.finditer(patt, text, re.IGNORECASE):
            start = m.start()
            snippet = text[max(0, start - 60):start + 120]
            out.append((start, canon, snippet))
    return out


def build_intel(zone_map: dict, transcripts_dir: pathlib.Path,
                video_meta: dict | None = None) -> dict:
    """Scan cached transcripts + build per-zone bait/lure intel.

    zone_map: location_to_zones mapping from youtube_zone_map.json
    video_meta: optional dict of video_id -> {channel, published_at, title}
                to enrich source citations.
    """
    video_meta = video_meta or {}
    # per-zone tallies
    zone_baits: dict = collections.defaultdict(list)
    zone_windows: dict = collections.defaultdict(list)  # v24.116
    all_mentions = []

    for f in sorted(transcripts_dir.glob("*.json")):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        text = d.get("text", "")
        if not text or len(text) < 200:
            continue

        video_id = f.stem
        meta = video_meta.get(video_id, {})
        channel = meta.get("channel", "?")
        published = meta.get("published_at", meta.get("date", ""))[:10]
        title = meta.get("title", "")

        # Get location, bait/lure, and bite-window positions in this transcript
        locs = _extract_zone_mentions_for_text(text)
        baits = _extract_bait_lure_for_text(text)
        bite_windows = _extract_bite_windows_for_text(text)
        # If neither baits nor bite-windows are present, skip
        if not baits and not bite_windows:
            continue

        # For each bait mention, find the nearest location within 250 chars
        # (v24.114 tuning — 400 was too loose, pulled in adjacent topics in
        # long podcasts). Also PREFER location BEFORE bait — captains almost
        # always name the spot then talk about what worked there. When bait
        # comes first without a nearby location, skip it: probably a topic
        # transition ("bunker are around" said generally, then location talk).
        for b_pos, b_canon, snippet in baits:
            nearest_loc = None
            nearest_dist = 9999
            for l_pos, l_key in locs:
                # Prefer location BEFORE bait (l_pos < b_pos), but allow after
                # within a tighter window (150 chars) since some captains flip.
                if l_pos <= b_pos:
                    dist = b_pos - l_pos
                    max_dist = 250
                else:
                    dist = l_pos - b_pos
                    max_dist = 150
                if dist < nearest_dist and dist <= max_dist:
                    nearest_dist = dist
                    nearest_loc = l_key
            if not nearest_loc:
                continue
            # v24.115: species context. Look for any species mention in a
            # 200-char window around the bait mention. Multiple species can
            # match (a captain saying "we chased tuna and stripers with X"
            # tags both). This lets the popup filter by target species.
            b_end = b_pos + len(b_canon)
            ctx_window = text[max(0, b_pos - 200): b_end + 200]
            matched_species = []
            for sp_name, sp_re in _SPECIES_RE:
                if sp_re.search(ctx_window):
                    matched_species.append(sp_name)
            # Collapse "tuna" if a specific tuna species (bluefin/yellowfin/bigeye) is also present
            if any(s in matched_species for s in ("bluefin", "yellowfin", "bigeye")) and "tuna" in matched_species:
                matched_species = [s for s in matched_species if s != "tuna"]

            # Expand loc → zones via the zone_map
            for zid in zone_map.get(nearest_loc, []):
                zone_baits[zid].append({
                    "bait_lure": b_canon,
                    "category": CATEGORY.get(b_canon, "other"),
                    "species": matched_species,  # empty list = species-agnostic mention
                    "location_key": nearest_loc,
                    "distance_chars": nearest_dist,
                    "snippet": snippet.strip().replace("\n", " ")[:220],
                    "video_id": video_id,
                    "channel": channel,
                    "published": published,
                    "title": title,
                })
                all_mentions.append({
                    "zone_id": zid,
                    "bait_lure": b_canon,
                    "species": matched_species,
                    "channel": channel,
                    "date": published,
                })

        # v24.116: same nearest-location attribution for bite windows.
        # Different tolerance — timing context is usually a broader
        # sentence-level thing, so allow up to 350 chars.
        for w_pos, w_canon, w_snippet in bite_windows:
            nearest_loc = None
            nearest_dist = 9999
            for l_pos, l_key in locs:
                dist = abs(w_pos - l_pos)
                if dist < nearest_dist and dist <= 350:
                    nearest_dist = dist
                    nearest_loc = l_key
            if not nearest_loc:
                continue
            for zid in zone_map.get(nearest_loc, []):
                zone_windows[zid].append({
                    "window": w_canon,
                    "distance_chars": nearest_dist,
                    "snippet": w_snippet.strip().replace("\n", " ")[:200],
                    "video_id": video_id,
                    "channel": channel,
                    "published": published,
                })

    # Per-zone rollup — top categories + top specific mentions
    rollups = {}
    for zid, mentions in zone_baits.items():
        cat_counts = collections.Counter(m["category"] for m in mentions)
        item_counts = collections.Counter(m["bait_lure"] for m in mentions)
        # De-dup channels per item for corroboration count
        item_channels: dict = collections.defaultdict(set)
        item_species: dict = collections.defaultdict(collections.Counter)
        for m in mentions:
            item_channels[m["bait_lure"]].add(m["channel"])
            # v24.115: track species tags per item so popup can filter
            for sp in (m.get("species") or []):
                item_species[m["bait_lure"]][sp] += 1
        # Best example per top items (nearest to a location mention)
        best_example_for = {}
        for m in mentions:
            item = m["bait_lure"]
            if item not in best_example_for or m["distance_chars"] < best_example_for[item]["distance_chars"]:
                best_example_for[item] = m

        # Also compute per-species rollup at zone level — "for tuna at this
        # zone the top bait/lure are...". Empty species = species-agnostic.
        per_species: dict = collections.defaultdict(list)
        for m in mentions:
            species_list = m.get("species") or ["_any"]
            for sp in species_list:
                per_species[sp].append(m)
        species_rollup = {}
        for sp, ms in per_species.items():
            sp_items = collections.Counter(m["bait_lure"] for m in ms)
            species_rollup[sp] = [
                {"bait_lure": bl, "mentions": c}
                for bl, c in sp_items.most_common(3)
            ]

        # v24.116: bite-window rollup for this zone
        win_mentions = zone_windows.get(zid, [])
        win_counts = collections.Counter(w["window"] for w in win_mentions)
        win_channels: dict = collections.defaultdict(set)
        for w in win_mentions:
            win_channels[w["window"]].add(w["channel"])
        best_window_example: dict = {}
        for w in win_mentions:
            k = w["window"]
            if k not in best_window_example or w["distance_chars"] < best_window_example[k]["distance_chars"]:
                best_window_example[k] = w
        top_windows = [
            {
                "window": win,
                "mentions": cnt,
                "channels": sorted(win_channels[win]),
                "channel_count": len(win_channels[win]),
                "best_example": best_window_example[win],
            }
            for win, cnt in win_counts.most_common(4)
        ]

        rollups[zid] = {
            "total_mentions": len(mentions),
            "top_categories": [{"category": c, "count": n} for c, n in cat_counts.most_common(3)],
            "top_items": [
                {
                    "bait_lure": item,
                    "category": CATEGORY.get(item, "other"),
                    "mentions": count,
                    "channels": sorted(item_channels[item]),
                    "channel_count": len(item_channels[item]),
                    "species": [sp for sp, _ in item_species[item].most_common()],  # v24.115
                    "best_example": best_example_for[item],
                }
                for item, count in item_counts.most_common(5)
            ],
            "by_species": species_rollup,  # v24.115 — for popup species filter
            "top_bite_windows": top_windows,  # v24.116 — WHEN captains catch
            "total_bite_window_mentions": len(win_mentions),
        }

    # v24.116: also include zones that have ONLY bite-window intel (no bait/lure)
    for zid, win_mentions in zone_windows.items():
        if zid in rollups:
            continue
        win_counts = collections.Counter(w["window"] for w in win_mentions)
        win_channels: dict = collections.defaultdict(set)
        for w in win_mentions:
            win_channels[w["window"]].add(w["channel"])
        best_window_example: dict = {}
        for w in win_mentions:
            k = w["window"]
            if k not in best_window_example or w["distance_chars"] < best_window_example[k]["distance_chars"]:
                best_window_example[k] = w
        rollups[zid] = {
            "total_mentions": 0,
            "top_categories": [],
            "top_items": [],
            "by_species": {},
            "top_bite_windows": [
                {
                    "window": win, "mentions": cnt,
                    "channels": sorted(win_channels[win]),
                    "channel_count": len(win_channels[win]),
                    "best_example": best_window_example[win],
                }
                for win, cnt in win_counts.most_common(4)
            ],
            "total_bite_window_mentions": len(win_mentions),
        }

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "transcripts_scanned": sum(1 for _ in transcripts_dir.glob("*.json")),
        "zones_with_bait_lure_intel": len(rollups),
        "total_mentions": len(all_mentions),
        "by_zone": rollups,
    }


def main() -> None:
    zone_map = (json.loads((BASE / "data" / "youtube_zone_map.json").read_text())
                or {}).get("location_to_zones", {})

    # Try to get video metadata (channel/date/title) from latest archive
    video_meta = {}
    latest = None
    for a in sorted((BASE / "archive").glob("2026-*.json"))[-3:]:
        try:
            snap = json.loads(a.read_text())
            chans = ((snap.get("raw_intel") or {}).get("youtube_full_corpus") or {}).get("channels", [])
            for ch in chans:
                for v in ch.get("all_videos", []) or []:
                    if v.get("video_id"):
                        video_meta[v["video_id"]] = {
                            "channel": ch.get("name", "?"),
                            "published_at": v.get("published", ""),
                            "title": v.get("title", "?"),
                        }
            latest = snap
        except Exception:
            continue

    intel = build_intel(zone_map, CACHE_DIR, video_meta)
    OUT_PATH.write_text(json.dumps(intel, indent=2))
    print(f"Wrote {OUT_PATH}")
    print(f"  Transcripts scanned:       {intel['transcripts_scanned']}")
    print(f"  Zones with bait/lure intel: {intel['zones_with_bait_lure_intel']}")
    print(f"  Total mentions:            {intel['total_mentions']}")

    # Sample the top zones
    tops = sorted(intel["by_zone"].items(), key=lambda x: -x[1]["total_mentions"])[:10]
    print("\nTop 10 zones by bait/lure mention count:")
    for zid, r in tops:
        cats = ", ".join(f"{c['category']}(×{c['count']})" for c in r["top_categories"])
        items = " · ".join(f"{i['bait_lure']}×{i['mentions']}" for i in r["top_items"][:3])
        print(f"  {zid:30}  [{r['total_mentions']} mentions]  cats: {cats}")
        print(f"    top items: {items}")


if __name__ == "__main__":
    main()
