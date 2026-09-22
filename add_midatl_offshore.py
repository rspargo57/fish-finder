#!/usr/bin/env python3
"""v24.82 — Fill in Mid-Atlantic offshore zones (canyon + midshore + wrecks)."""
import json, os
from datetime import date

BASE = "/root/fish-finder"
today = date.today().isoformat()

NEW = [
    # ---------- NJ Canyons ----------
    ("toms_canyon", "Toms Canyon", "NJ", "canyon", (39.05, -73.05),
     ["yellowfin_tuna", "bigeye_tuna", "mahi", "wahoo", "blue_marlin"],
     "70+ nm E of Barnegat Inlet. Yellowfin June-Oct, bigeye chunk bite mid-summer."),
    ("lindenkohl_canyon", "Lindenkohl Canyon", "NJ", "canyon", (38.85, -72.85),
     ["yellowfin_tuna", "bigeye_tuna", "mahi", "wahoo", "blue_marlin"],
     "Deep-water canyon 85 nm E of Little Egg Inlet. Bigeye + white marlin summer."),
    ("spencer_canyon", "Spencer Canyon", "NJ", "canyon", (38.70, -72.70),
     ["yellowfin_tuna", "bigeye_tuna", "mahi", "wahoo", "blue_marlin", "white_marlin"],
     "White marlin corridor Aug-Sep. Deep-drop swordfish."),
    ("norfolk_canyon", "Norfolk Canyon", "VA", "canyon", (37.05, -74.75),
     ["yellowfin_tuna", "bigeye_tuna", "wahoo", "mahi", "blue_marlin", "white_marlin"],
     "Southern-most Mid-Atl canyon — 60nm E of Rudee Inlet. Tuna + bill summer."),
    # ---------- NJ Nearshore / Wrecks (offshore of Randy's usual NE coverage) ----------
    ("mud_hole_nj", "The Mud Hole (NJ)", "NJ", "midshore", (40.35, -73.60),
     ["yellowfin_tuna", "bluefin_recreational", "mahi"],
     "Bluefin migration corridor 30nm E of Manasquan. Summer tuna push."),
    ("bacardi_wreck", "Bacardi Wreck", "NJ", "midshore", (40.05, -73.68),
     ["sea_bass", "tautog", "cod", "ling"],
     "Deep-water wreck 40nm E of Barnegat. Big sea bass + cod."),
    ("immigrant_wreck", "Immigrant Wreck", "NJ", "midshore", (39.55, -73.62),
     ["sea_bass", "tautog", "cod", "ling"],
     "185ft freighter 45nm E of Atlantic City. Structure bite year-round."),
    ("mohawk_wreck", "Mohawk Wreck", "NJ", "midshore", (39.85, -73.75),
     ["sea_bass", "tautog", "ling", "cod"],
     "USS Mohawk cutter, deep wreck 35nm E of Barnegat."),
    ("delaware_ii_wreck", "Delaware II / Fire Island Buoy area", "NJ", "midshore", (40.55, -73.05),
     ["sea_bass", "tautog", "cod"],
     "Series of wrecks off Long Island south shore — Mid-Atl-adjacent."),
    ("triple_wreck_reef", "Triple Wrecks Reef", "NJ", "nearshore", (39.75, -74.05),
     ["sea_bass", "tautog", "porgy", "fluke"],
     "State artificial reef complex 8nm E of Atlantic City."),
    # ---------- DE ----------
    ("indian_river_ledge", "Indian River Ledge", "DE", "midshore", (38.55, -74.75),
     ["sea_bass", "amberjack", "cod", "flounder"],
     "Deep-water ledge 30nm E of Indian River Inlet."),
    ("del_bay_wrecks", "Delaware Bay Mouth Wrecks", "DE", "nearshore", (38.75, -75.05),
     ["sea_bass", "tautog", "flounder", "striped_bass"],
     "Cluster of wrecks around Del Bay mouth. Winter tog + spring stripers."),
    ("del_49th_st_reef", "49th St Reef (Ocean City DE)", "DE", "nearshore", (38.35, -74.90),
     ["sea_bass", "tautog", "flounder", "fluke"],
     "State artificial reef offshore Ocean City Delaware."),
    ("del_20_fathom", "20 Fathom Line (E of Indian River)", "DE", "midshore", (38.60, -74.55),
     ["sea_bass", "amberjack", "flounder"],
     "Contour break 40nm E of Indian River Inlet. Bottom fishing productive."),
    # ---------- MD ----------
    ("great_gully_md", "Great Gully (MD Offshore)", "MD", "midshore", (38.20, -74.35),
     ["yellowfin_tuna", "mahi", "amberjack", "sea_bass"],
     "Deep gully 45nm E of Ocean City MD. Yellowfin + bottom combo."),
    ("chicken_bone", "The Chicken Bone", "MD", "midshore", (38.10, -74.35),
     ["mahi", "yellowfin_tuna", "amberjack", "wahoo"],
     "Structure formation 50nm E of Ocean City. Chicken-bone shaped drop."),
    ("hot_dog_extended", "The Hot Dog (Extended)", "MD", "midshore", (38.02, -74.55),
     ["sea_bass", "amberjack", "flounder"],
     "Southern extension of the Jackspot ridge."),
    ("md_wilmington_lump", "Wilmington Lump", "MD", "midshore", (38.30, -74.20),
     ["yellowfin_tuna", "mahi", "amberjack"],
     "Underwater mound 55nm E of Ocean City. Summer yellowfin bite."),
    ("md_ridge", "The Ridge (MD)", "MD", "midshore", (38.25, -74.45),
     ["sea_bass", "amberjack", "flounder"],
     "Long submerged ridge system 35nm E of Ocean City."),
    # ---------- VA ----------
    ("cbbt_all", "CBBT (Chesapeake Bay Bridge Tunnel)", "VA", "inshore", (36.98, -76.10),
     ["striped_bass", "spanish_mackerel", "flounder", "cobia", "bluefish"],
     "All 4 pilings + islands. Trophy striper + cobia migration. Winter deep tog."),
    ("chesapeake_light_tower", "Chesapeake Light Tower", "VA", "midshore", (36.91, -75.71),
     ["cobia", "amberjack", "spanish_mackerel", "king_mackerel"],
     "USCG light tower 13nm E of Cape Henry. Cobia migration May-Aug."),
    ("triangle_wrecks", "Triangle Wrecks (VA)", "VA", "midshore", (36.90, -75.80),
     ["sea_bass", "flounder", "amberjack", "cobia"],
     "3 USS destroyers scuttled in a triangle formation. Wreck bite year-round."),
    ("md_tower_wreck", "MDT Tower Wreck", "VA", "midshore", (36.85, -75.75),
     ["sea_bass", "amberjack", "flounder"],
     "MDT (Marine Deployment Target) tower wreck 20nm E of Cape Henry."),
    ("va_4_pack", "The 4 Pack (VA)", "VA", "nearshore", (36.94, -76.00),
     ["cobia", "spanish_mackerel", "sheepshead"],
     "Cluster of tower buoys 5nm off Cape Henry. Cobia sight fishing."),
    ("norfolk_canyon_edge", "Norfolk Canyon Edge (Deep Drop)", "VA", "canyon", (37.00, -74.90),
     ["swordfish", "yellowfin_tuna", "bigeye_tuna"],
     "Deep-drop swordfish along the canyon rim. Daytime sword."),
    # ---------- NC (north of Hatteras — Mid-Atl side) ----------
    ("oregon_inlet_break", "Oregon Inlet Break", "NC", "midshore", (35.70, -75.15),
     ["yellowfin_tuna", "mahi", "wahoo", "sailfish"],
     "20nm E of Oregon Inlet — Gulf Stream edge. Tuna daily in season."),
    ("point_avon", "The Point (Avon NC)", "NC", "midshore", (35.30, -75.20),
     ["yellowfin_tuna", "mahi", "sailfish", "wahoo", "blue_marlin"],
     "Where the Gulf Stream pinches closest to shore in NC. Legendary bill spot."),
]

p = os.path.join(BASE, "data", "zones_mid_atlantic.json")
d = json.load(open(p))
zones = d.get("zones", [])
existing = {z["id"] for z in zones}
added = 0
for spec in NEW:
    zid, name, region, cat, coords, species, notes = spec
    if zid in existing:
        continue
    z = {
        "id": zid, "name": name, "region": region, "category": cat,
        "center": list(coords), "species": species, "notes": notes,
        "heat": 5, "heat_updated": today,
    }
    zones.append(z)
    added += 1

d["zones"] = zones
open(p, "w").write(json.dumps(d, indent=2))
from collections import Counter
c = Counter(z.get("category","?") for z in zones)
print(f"Mid-Atl: added {added} new zones · total {len(zones)}")
print(f"Categories: {dict(c)}")
