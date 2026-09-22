#!/usr/bin/env python3
"""Update zones.json to add offshore migratory species (swordfish, wahoo,
sailfish, marlins, false albacore) with realistic seasonal presence data
sourced from The Fisherman / OTW / regional NE fishing calendars.

Also updates the canyon zones (Hudson, Block, Atlantis, Veatch) + Tuna Ridge
to include these species with proper monthly presence arrays. Adds false
albacore to fall-run inshore/nearshore striper zones.
"""
import json
from pathlib import Path

PATH = Path("/root/fish-finder/data/zones.json")
data = json.loads(PATH.read_text())

# ----- 1. New species_calendars entries -----
NEW_CALENDARS = {
    "swordfish": {
        "regional": [0, 0, 0, 0, 1, 4, 7, 8, 7, 4, 1, 0],
        "narrative": "Canyon deep-drop daytime + night drift. Peak Jul-Sep at Hudson/Block/Atlantis/Veatch. Recovering stock. HMS permit required, keeper >47\" LJFL, one fish per angler/day."
    },
    "wahoo": {
        "regional": [0, 0, 0, 0, 0, 1, 3, 7, 9, 7, 2, 0],
        "narrative": "Warm-water pelagic. Late summer/fall canyon high-speed troll (14-18 kt). Rides Gulf Stream eddies north. Peak Aug-Oct in warm-water years. Wire leaders mandatory."
    },
    "sailfish": {
        "regional": [0, 0, 0, 0, 0, 0, 1, 3, 2, 0, 0, 0],
        "narrative": "Rare NE visitor. Occasional catches at canyons in warmest August water. Not a target species — bycatch on white marlin trolls. Release-only."
    },
    "white_marlin": {
        "regional": [0, 0, 0, 0, 0, 2, 6, 9, 7, 3, 0, 0],
        "narrative": "Canyon summer/fall staple. Peak Jul-Sep at Hudson/Block/Atlantis. White Marlin Open in Ocean City every August. Naked ballyhoo on 30w trolling gear. Release-only for most anglers."
    },
    "blue_marlin": {
        "regional": [0, 0, 0, 0, 0, 1, 4, 7, 5, 2, 0, 0],
        "narrative": "Deep-canyon apex predator. Less common than white marlin in NE. Peak August at Hudson/Atlantis/Veatch. Big lures, 50-80w tackle. Release-only."
    },
    "false_albacore": {
        "regional": [0, 0, 0, 0, 0, 0, 1, 3, 8, 9, 5, 0],
        "narrative": "Fall inshore run — the light-tackle king of Sept-Oct. Blitzes on peanut bunker + silversides at Montauk Point, Block Island, Watch Hill, Race. Not keeper (poor eating) but bucket-list fight."
    }
}

# Merge into existing calendars (preserve any existing entries)
sc = data.setdefault("species_calendars", {})
for key, cal in NEW_CALENDARS.items():
    sc[key] = cal

# ----- 2. Zone-level seasonal_presence updates -----
# Canyon zones (deep water, all offshore pelagics)
CANYON_ADDITIONS = {
    "swordfish":    [0, 0, 0, 0, 1, 4, 8, 9, 8, 5, 1, 0],
    "wahoo":        [0, 0, 0, 0, 0, 1, 3, 7, 9, 7, 2, 0],
    "white_marlin": [0, 0, 0, 0, 0, 2, 6, 9, 8, 4, 0, 0],
    "blue_marlin":  [0, 0, 0, 0, 0, 1, 4, 7, 6, 2, 0, 0],
    "sailfish":     [0, 0, 0, 0, 0, 0, 1, 3, 2, 0, 0, 0]
}
# Tuna Ridge (30-fathom, shelf edge) — wahoo occasional, sword rare
TUNA_RIDGE_ADDITIONS = {
    "wahoo":        [0, 0, 0, 0, 0, 0, 1, 3, 5, 3, 0, 0],
    "swordfish":    [0, 0, 0, 0, 0, 1, 2, 3, 2, 1, 0, 0]
}
# Nearshore south-of-Block + Habs Ledge — wahoo very rare but happens
S_BLOCK_ADDITIONS = {
    "wahoo":        [0, 0, 0, 0, 0, 0, 0, 1, 2, 1, 0, 0]
}
# Fall inshore false-albacore zones (peak Sept-Oct blitzes)
FALSE_ALBIE_ADDITIONS = {
    "false_albacore": [0, 0, 0, 0, 0, 0, 1, 3, 8, 9, 4, 0]
}
FALSE_ALBIE_ZONES = {
    "montauk_rips_striper", "block_island_striper", "watch_hill_reef",
    "the_race", "plum_gut", "block_island_sound",
    "narragansett_bay", "montauk_nearshore"
}

canyon_ids = set()
for z in data["zones"]:
    if z["category"] == "canyon":
        canyon_ids.add(z["id"])
        # Add all canyon-relevant species to the zone's species array
        for sp in ["swordfish", "wahoo", "white_marlin", "blue_marlin"]:
            if sp not in z["species"]:
                z["species"].append(sp)
        # Sailfish only on the warmest-water canyons (Hudson, Atlantis, Veatch — south/east most)
        if z["id"] in {"hudson_canyon", "atlantis_canyon", "veatch_hydrographer"}:
            if "sailfish" not in z["species"]:
                z["species"].append("sailfish")
        # Set seasonal_presence
        sp_dict = z.setdefault("seasonal_presence", {})
        for sp, arr in CANYON_ADDITIONS.items():
            if sp == "sailfish" and z["id"] not in {"hudson_canyon", "atlantis_canyon", "veatch_hydrographer"}:
                continue
            sp_dict[sp] = arr[:]
    elif z["id"] == "tuna_ridge":
        for sp in TUNA_RIDGE_ADDITIONS:
            if sp not in z["species"]:
                z["species"].append(sp)
        sp_dict = z.setdefault("seasonal_presence", {})
        for sp, arr in TUNA_RIDGE_ADDITIONS.items():
            sp_dict[sp] = arr[:]
    elif z["id"] in {"s_block_nearshore", "habs_ledge"}:
        for sp in S_BLOCK_ADDITIONS:
            if sp not in z["species"]:
                z["species"].append(sp)
        sp_dict = z.setdefault("seasonal_presence", {})
        for sp, arr in S_BLOCK_ADDITIONS.items():
            sp_dict[sp] = arr[:]
    if z["id"] in FALSE_ALBIE_ZONES:
        if "false_albacore" not in z["species"]:
            z["species"].append("false_albacore")
        sp_dict = z.setdefault("seasonal_presence", {})
        for sp, arr in FALSE_ALBIE_ADDITIONS.items():
            sp_dict[sp] = arr[:]

# ----- 3. Write back -----
PATH.write_text(json.dumps(data, indent=2) + "\n")

# ----- 4. Report -----
print(f"Updated zones.json:")
print(f"  {len(canyon_ids)} canyon zones got new species")
print(f"  Tuna Ridge got wahoo + swordfish (shelf edge)")
print(f"  {len(FALSE_ALBIE_ZONES)} inshore/nearshore zones got false albacore")
print()
print("Species calendar entries now:")
for k in sorted(sc.keys()):
    if k.startswith("_"): continue
    peak = max(sc[k]["regional"]) if isinstance(sc[k], dict) else "?"
    peak_month = sc[k]["regional"].index(peak) + 1 if isinstance(sc[k], dict) else "?"
    print(f"  {k}: peak {peak}/10 in month {peak_month}")
