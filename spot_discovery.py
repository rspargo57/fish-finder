#!/usr/bin/env python3
"""Unknown-spot discovery — Randy's 2026-08-29 rule.

Scans the last N days of raw captain intel (YouTube titles/descriptions/transcripts,
charter blog entries, Reddit posts) for spot-name patterns not yet in the region's
zones.json. Prints unknown mentions with frequency + the videos/quotes that named
them, so the Captain can research + add them as zones.

Usage:
    python3 spot_discovery.py                       # all regions
    python3 spot_discovery.py --region gulf         # single region
    python3 spot_discovery.py --region gulf --days 30 --min-mentions 2

Reads:
    /root/fish-finder/data/zones.json               (Northeast)
    /root/fish-finder/data/zones_mid_atlantic.json  (Mid-Atl)
    /root/fish-finder/data/zones_gulf.json          (Gulf)
    /root/fish-finder/archive/YYYY-MM-DD.json       (raw_intel)

Enforces Randy's rule: every session includes a spot-discovery pass; anything
with ≥2 mentions from different sources warrants investigation. Captain adds
verified spots as new zones per the two-source rule.
"""
import json, re, sys, pathlib, argparse, datetime, collections

BASE = pathlib.Path(__file__).parent
ARCHIVE = BASE / "archive"

# Spot-name pattern vocabulary — kinds of structure captains name in reports
STRUCTURE_TYPES = r"Reef|Rig|Wreck|Ledge|Bank|Hole|Shoal|Shoals|Grounds|Rocks|Rock|Bar|Hump|Ridge|Rip|Point|Pass|Bay|Platform|Tower|Barge|Buoy|Cove|Sound|Inlet"

SPOT_PATTERNS = [
    # "Nipple Reef" / "Petronius Platform" / "Massachusetts Wreck"
    re.compile(rf'\b([A-Z][a-z]+(?: [A-Z][a-z]+)? (?:{STRUCTURE_TYPES}))\b'),
    # "The Nipple" / "The Elbow" (common short spot names — but too many false
    # positives; only enable when a --loose flag is set)
    # re.compile(r'\bThe ([A-Z][a-z]+)\b'),
    # "USS <name>" / "SS <name>" — historical wrecks
    re.compile(r'\b((?:USS|SS|HMS|HMCS|MV|USNS) [A-Z][a-z]+(?:[A-Z][a-z]+)?)\b'),
    # Oil-rig-specific patterns
    re.compile(r'\b((?:Petronius|Marlin|Ram Powell|Devil\'s Tower|Neptune|Horn Mountain|Mad Dog|Thunder Horse|Atlantis|Mars|Ursa) (?:Platform|Rig|TLP|Spar)?)\b'),
]

# Region → keywords that filter which intel entries are relevant.
# v24.36 (2026-08-30): Gulf keywords tightened — removed ambiguous common words
# (spur/elbow/nipple/alabama/mississippi/louisiana/gulf coast) that were false-
# positive-matching NE Fisherman posts that mention e.g. "gulf coast route" or
# "Alabama license." Kept only unambiguous Gulf-specific terms + the "the
# nipple"/"the elbow"/"the spur" specific-phrase variants.
REGION_HINTS = {
    "northeast": ["long island", "cape cod", "montauk", "block island", "old saybrook", "narragansett", "hudson canyon", "block canyon", "atlantis canyon", "veatch", "cox", "coxes", "the race", "plum gut", "fishers island", "watch hill", "point judith", "cape ann", "buzzards bay", "vineyard", "nantucket", "rhode island", "connecticut"],
    "midatl": ["cape may", "atlantic city", "ocean city", "virginia beach", "oregon inlet", "outer banks", "hatteras", "chesapeake", "delaware bay", "barnegat", "jersey", "wilmington canyon", "baltimore canyon", "poor man's", "norfolk canyon"],
    "gulf": ["pensacola", "perdido", "destin", "panama city", "pcb", "orange beach", "gulf shores",
             "mobile bay", "fort morgan", "dauphin island", "gulf of mexico", "gulf of america",
             "emerald coast", "florida panhandle", "fl panhandle", "northwest florida",
             "desoto canyon", "de soto canyon", "the nipple", "the elbow", "the spur",
             "choctawhatchee", "st andrews bay", "st. andrews bay", "apalachicola",
             "uss oriskany", "oriskany reef", "m/v lulu", "vamar wreck", "empire mica",
             "petronius", "ram powell", "midnight lump", "sackett bank"],
    # Zone 5 South Florida (Atlantic Pass A) — v24.43, Randy 2026-08-31.
    "south_florida": [
        "palm beach", "jupiter", "boynton", "boca raton", "sailfish alley",
        "fort lauderdale", "ft lauderdale", "hillsboro", "pompano", "port everglades",
        "hollywood beach", "dania pier", "blue fire", "copenhagen wreck", "papa doc",
        "miami", "government cut", "haulover", "fowey rocks", "stiltsville",
        "biscayne", "elliot key", "key biscayne", "miami 190",
        "islamorada", "alligator reef", "islamorada hump", "bud n mary",
        "key largo", "molasses reef", "pennekamp",
        "uss duane", "uss bibb", "spiegel grove",
        "marathon", "marathon hump", "sombrero reef", "seven mile bridge",
        "bahia honda", "big pine key",
        "key west", "sand key", "cottrell key", "vandenberg", "uss vandenberg",
        "cayman salvager", "marquesas", "dry tortugas", "fort jefferson",
        "409 hole", "wood wall", "the color change",
        "florida keys", "the keys", "south florida", "south fl",
        "sailfish coast", "gold coast", "treasure coast", "florida straits",
    ],
}

# Load region → data-file map
REGION_FILES = {
    "northeast":     "zones.json",
    "midatl":        "zones_mid_atlantic.json",
    "gulf":          "zones_gulf.json",
    "south_florida": "zones_south_florida.json",
}

def load_known_zones(region):
    p = BASE / "data" / REGION_FILES[region]
    if not p.exists():
        return set()
    d = json.loads(p.read_text())
    known = set()
    for z in d.get("zones", []):
        known.add(z["name"].lower().strip())
        known.add(z["id"].lower().replace("_", " ").strip())
        # Also add key words from the name (e.g. "USS Oriskany" → also match just "Oriskany")
        for word in z["name"].split():
            if len(word) > 4 and word[0].isupper():
                known.add(word.lower())
    return known

def load_recent_intel(days):
    """Load all raw_intel from archive snapshots within the last `days` days."""
    cutoff = datetime.date.today() - datetime.timedelta(days=days)
    videos = []
    charter_entries = []
    for p in sorted(ARCHIVE.glob("2*.json")):
        try:
            d = datetime.date.fromisoformat(p.stem)
        except ValueError:
            continue
        if d < cutoff:
            continue
        try:
            snap = json.loads(p.read_text())
        except Exception:
            continue
        ri = snap.get("raw_intel", {})
        for ch in ri.get("youtube_full_corpus", {}).get("channels", []):
            for v in ch.get("all_videos", []):
                videos.append({
                    "date": d.isoformat(),
                    "channel": ch.get("name") or ch.get("id"),
                    "title": v.get("title", ""),
                    "description": (v.get("description") or "")[:1500],
                    "transcript": (v.get("transcript") or "")[:3000],
                    "link": v.get("link", ""),
                })
        # Charter/fisherman podcast entries in the snapshot
        for src in ri.get("charter_harvest", {}).values() if isinstance(ri.get("charter_harvest"), dict) else []:
            for e in (src if isinstance(src, list) else []):
                charter_entries.append(e)
    return videos, charter_entries

def scan_for_spots(region, videos, known_zones, min_mentions=1):
    """Return Counter of unknown spot mentions + evidence dict {spot: [context]}.
    v24.36: dedupe by (channel, title) so a single video captured in multiple
    daily archive snapshots doesn't inflate mention counts. A "mention" now
    means a unique captain-source video."""
    hints = REGION_HINTS.get(region, [])
    hits = collections.Counter()
    evidence = collections.defaultdict(list)
    seen_source_spot = set()  # (channel, title, spot_name) — one count per unique source
    for v in videos:
        text_full = f"{v['title']} {v['description']} {v['transcript']}"
        text_lower = text_full.lower()
        # Only consider videos with at least one region hint
        if not any(kw in text_lower for kw in hints):
            continue
        for pat in SPOT_PATTERNS:
            for m in pat.finditer(text_full):
                name = m.group(1).strip()
                # Filter: skip common false positives
                if any(fp in name.lower() for fp in ["gulf coast", "florida panhandle", "east coast", "west coast", "the north", "the south"]):
                    continue
                if name.lower() in known_zones:
                    continue
                # Dedupe: only count each unique (channel, title, spot) once
                source_spot_key = (v["channel"], v["title"], name)
                if source_spot_key in seen_source_spot:
                    continue
                seen_source_spot.add(source_spot_key)
                hits[name] += 1
                if len(evidence[name]) < 3:  # keep top-3 example videos per spot
                    evidence[name].append({
                        "date": v["date"],
                        "channel": v["channel"],
                        "title": v["title"][:100],
                        "link": v["link"],
                    })
    return {k: v for k, v in hits.items() if v >= min_mentions}, evidence

def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--region", choices=["northeast", "midatl", "gulf", "south_florida", "all"], default="all")
    ap.add_argument("--days", type=int, default=14, help="How many days of archive to scan (default 14)")
    ap.add_argument("--min-mentions", type=int, default=1, help="Only report spots with N+ mentions (default 1)")
    args = ap.parse_args()

    regions = ["northeast", "midatl", "gulf", "south_florida"] if args.region == "all" else [args.region]

    print(f"═══ Unknown-Spot Discovery Pass — Randy's 2026-08-29 rule ═══")
    print(f"    Scanning last {args.days} days of raw intel · min {args.min_mentions} mention(s) to report")
    print()

    videos, charter = load_recent_intel(args.days)
    print(f"Loaded: {len(videos)} YouTube videos · {len(charter)} charter entries")
    print()

    total_new = 0
    for region in regions:
        known = load_known_zones(region)
        print(f"─── {region.upper()} — {len(known)} known zone terms")
        found, evidence = scan_for_spots(region, videos, known, args.min_mentions)
        if not found:
            print(f"    ✓ Nothing new (or region intel harvesters not yet plumbed — Zone-v2 TODO)")
            print()
            continue
        for spot, count in sorted(found.items(), key=lambda kv: -kv[1]):
            print(f"    {count:2}× {spot}")
            for ev in evidence[spot]:
                print(f"          {ev['date']} · {ev['channel']:30s} · {ev['title']}")
            total_new += 1
        print()
    print(f"═══ TOTAL unknown-spot candidates: {total_new} across {len(regions)} region(s) ═══")
    if total_new:
        print()
        print("Captain action: for each candidate with ≥2 mentions from different sources OR 1 named-")
        print("captain quote with coords, research + add to the region's zones_<region>.json.")

if __name__ == "__main__":
    main()
