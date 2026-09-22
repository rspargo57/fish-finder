#!/usr/bin/env python3
"""
v24.74 — Fix + expand offshore zones for S.FL and SE Coast.

Two things at once:
1. RECATEGORIZE existing category="offshore" zones (17 in S.FL, 5 in SE)
   into nearshore/midshore/canyon so the map's activeCategories filter
   actually renders them. Right now they're invisible because the filter
   set is {inshore, nearshore, midshore, canyon} and "offshore" isn't
   in that list.
2. ADD ~30 new offshore zones per region covering the famous productive
   spots we're missing.
"""
import json, math, os
from datetime import date

BASE = "/root/fish-finder"
TODAY = date.today().isoformat()

def haversine_nm(lat1, lon1, lat2, lon2):
    R = 3440.065  # earth radius in nautical miles
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))

# Coastal reference points for distance-based categorization
COAST_REFS = {
    # S. Florida
    "Palm Beach":       (26.71, -80.03),
    "Fort Lauderdale":  (26.12, -80.10),
    "Miami":            (25.76, -80.13),
    "Upper Keys":       (24.90, -80.55),
    "Middle Keys":      (24.72, -81.00),
    "Lower Keys":       (24.55, -81.78),
    "Offshore GS":      (24.85, -80.25),
    "SW FL":            (26.15, -81.83),
    # SE Coast
    "NC":               (34.15, -77.90),
    "SC":               (32.75, -79.85),
    "GA":               (31.10, -81.30),
    "NE FL":            (30.30, -81.40),
}

def categorize_by_distance(coords, region):
    """Categorize an offshore zone by distance from its region's coast."""
    ref = COAST_REFS.get(region)
    if not ref:
        return "midshore"  # safe default
    dist = haversine_nm(coords[0], coords[1], ref[0], ref[1])
    if dist <= 5:   return "nearshore"
    if dist <= 30:  return "midshore"
    return "canyon"

def default_seasonal(peak_months):
    """Build a 12-month seasonal_presence array with peaks in given months (0-indexed)."""
    arr = [3] * 12
    for m in peak_months:
        arr[m] = 9
    # Shoulder months
    for m in peak_months:
        arr[(m-1) % 12] = max(arr[(m-1) % 12], 6)
        arr[(m+1) % 12] = max(arr[(m+1) % 12], 6)
    return arr

def make_zone(zid, name, region, coords, species, notes, seasonal_map=None):
    coords_list = list(coords)
    category = categorize_by_distance(coords_list, region)
    z = {
        "id": zid,
        "name": name,
        "region": region,
        "category": category,
        "center": coords_list,
        "species": species,
        "notes": notes,
        "heat": 5,
        "heat_updated": TODAY,
    }
    if seasonal_map:
        z["seasonal_presence"] = seasonal_map
    return z

# ============================================================================
# S. FLORIDA — new offshore zones
# ============================================================================
# Coordinates from well-known charting: Hilton's Realtime-Navigator, NOAA
# nautical charts, FishTrack, dive guides, captain reports. All well-known
# productive spots.
SFL_NEW = [
    # ---------- Palm Beach ----------
    ("pb_sailfish_alley_north", "Sailfish Alley North (Juno-Jupiter)", "Palm Beach", (26.90, -80.02),
     ["sailfish", "king_mackerel", "mahi", "blackfin_tuna"],
     "Nov-Mar sailfish migration corridor. Kite fishing edge of Gulf Stream."),
    ("pb_loran_tower", "Loran Tower Wreck", "Palm Beach", (26.65, -80.00),
     ["amberjack", "grouper", "cobia", "king_mackerel"],
     "Sunken navigation tower. Structure fish year-round."),
    ("pb_governors_reef", "Governor's Reef", "Palm Beach", (26.72, -80.02),
     ["snapper", "grouper", "amberjack", "cobia"],
     "Artificial reef complex. Bottom fishing + trolling."),
    ("pb_the_trench", "The Trench (Palm Beach)", "Palm Beach", (26.75, -79.95),
     ["wahoo", "blackfin_tuna", "mahi", "sailfish"],
     "Deep-water ledge east of Palm Beach. Wahoo winter/spring."),
    ("pb_juno_ledge_south", "Juno Ledge South", "Palm Beach", (26.85, -80.02),
     ["sailfish", "mahi", "king_mackerel"],
     "Extension of Jupiter Ledge system south of Juno. Live-bait sails."),
    ("pb_west_end_bank", "West End Bank (Bahamas Edge)", "Palm Beach", (26.65, -79.10),
     ["wahoo", "mahi", "blackfin_tuna", "yellowfin_tuna", "blue_marlin"],
     "Bahamas Edge run — 45nm ESE Palm Beach. Wahoo March-May, marlin summer."),

    # ---------- Fort Lauderdale ----------
    ("ftl_copenhagen_wreck", "Copenhagen Wreck", "Fort Lauderdale", (26.22, -80.06),
     ["snapper", "grouper", "amberjack", "cobia"],
     "1900 shipwreck in 25ft. Snapper + cobia on wreck."),
    ("ftl_rebel_wreck", "Rebel Wreck", "Fort Lauderdale", (26.10, -80.06),
     ["snapper", "grouper", "amberjack", "goliath_grouper"],
     "185ft freighter sunk 1985. Deep drop for grouper + goliath."),
    ("ftl_ancient_mariner", "Ancient Mariner Wreck", "Fort Lauderdale", (26.10, -80.08),
     ["snapper", "grouper", "amberjack"],
     "185ft coastal freighter. Excellent structure."),
    ("ftl_hillsboro_ledge", "Hillsboro Ledge (Deep)", "Fort Lauderdale", (26.25, -80.05),
     ["sailfish", "king_mackerel", "mahi", "amberjack"],
     "Second reef ledge north Fort Lauderdale. Sailfish winter."),
    ("ftl_65_ledge", "65-Foot Ledge (Ft. Lauderdale)", "Fort Lauderdale", (26.13, -80.09),
     ["snapper", "grouper", "kingfish", "cobia"],
     "Classic reef ledge 3nm off Port Everglades."),
    ("ftl_bahamas_edge", "Bahamas Edge (Ft Lauderdale)", "Fort Lauderdale", (26.10, -79.50),
     ["wahoo", "blue_marlin", "yellowfin_tuna", "mahi"],
     "Gulf Stream to Bahamas Edge run. Trolling for pelagics."),
    ("ftl_neptune_reef", "Neptune Memorial Reef", "Fort Lauderdale", (25.85, -80.08),
     ["snapper", "grouper", "amberjack", "kingfish"],
     "Artificial reef 3.25mi E of Miami Beach. Big amberjack."),

    # ---------- Miami ----------
    ("mia_150_ledge", "Miami 150 Ledge", "Miami", (25.78, -80.08),
     ["snapper", "grouper", "cobia", "king_mackerel"],
     "150ft depth ledge. Bottom fishing + trolling combo."),
    ("mia_stream_edge", "Miami Gulf Stream Edge", "Miami", (25.75, -79.90),
     ["sailfish", "mahi", "wahoo", "blackfin_tuna"],
     "Inner edge of Gulf Stream. Kite fishing sails winter."),
    ("mia_orion_wreck", "Orion Wreck", "Miami", (25.73, -80.09),
     ["grouper", "amberjack", "snapper"],
     "119ft Coast Guard cutter sunk 1985."),
    ("mia_almirante_wreck", "Almirante Wreck (Miami)", "Miami", (25.70, -80.09),
     ["grouper", "amberjack", "cobia"],
     "197ft freighter. Deep structure drop."),
    ("mia_bahamas_run", "Miami-Bimini Run", "Miami", (25.70, -79.40),
     ["blue_marlin", "yellowfin_tuna", "wahoo", "mahi"],
     "50nm Bimini/Bahamas Bank edge. Pelagic run summer."),

    # ---------- Upper Keys ----------
    ("uk_molasses_reef", "Molasses Reef", "Upper Keys", (25.01, -80.37),
     ["mahi", "sailfish", "king_mackerel", "yellowtail_snapper"],
     "Iconic UK reef 6mi SE Key Largo. Ultra popular light tackle."),
    ("uk_alligator_reef", "Alligator Reef", "Upper Keys", (24.85, -80.62),
     ["sailfish", "mahi", "yellowtail_snapper", "king_mackerel"],
     "Historic lighthouse reef 4mi SE Islamorada."),
    ("uk_pickles_reef", "Pickles Reef", "Upper Keys", (25.00, -80.42),
     ["yellowtail_snapper", "grouper", "hogfish", "mahi"],
     "Shallow reef, big yellowtail + hogfish."),
    ("uk_islamorada_180", "Islamorada 180 (Deep)", "Upper Keys", (24.75, -80.60),
     ["mahi", "wahoo", "blackfin_tuna", "swordfish"],
     "180-fathom curve S of Islamorada Hump. Sword daytime."),

    # ---------- Middle Keys ----------
    ("mk_sombrero_reef", "Sombrero Reef", "Middle Keys", (24.63, -81.11),
     ["yellowtail_snapper", "mahi", "sailfish", "king_mackerel"],
     "Lighthouse reef 4mi S Marathon. Yellowtail + sails."),
    ("mk_east_hump", "Marathon East Hump", "Middle Keys", (24.55, -80.85),
     ["blackfin_tuna", "wahoo", "mahi", "amberjack"],
     "Underwater seamount 3nm E of West Hump. Blackfin schools."),
    ("mk_content_keys_wall", "Content Keys Wall", "Middle Keys", (24.80, -81.30),
     ["grouper", "snapper", "cobia"],
     "Deep wall system. Good grouper structure."),

    # ---------- Lower Keys ----------
    ("lk_looe_key_reef", "Looe Key Reef", "Lower Keys", (24.55, -81.40),
     ["yellowtail_snapper", "mahi", "hogfish", "king_mackerel"],
     "Sanctuary reef 6mi S of Big Pine. Yellowtail hotspot."),
    ("lk_american_shoal", "American Shoal Reef", "Lower Keys", (24.53, -81.52),
     ["yellowtail_snapper", "grouper", "mahi", "sailfish"],
     "Big offshore reef complex W of Looe. Full-day trip."),
    ("lk_riley_hump", "Riley's Hump", "Lower Keys", (24.48, -83.10),
     ["mutton_snapper", "grouper", "amberjack"],
     "Legendary mutton snapper spawning site 60nm W Key West. Regulated."),
    ("lk_rebecca_shoal", "Rebecca Shoal", "Lower Keys", (24.58, -82.55),
     ["mutton_snapper", "grouper", "amberjack", "cobia"],
     "Shoal between Marquesas and Tortugas. Mutton snapper + grouper."),

    # ---------- Offshore GS (Gulf Stream) ----------
    ("gs_sword_grounds_sfl", "Swordfish Grounds (S. Florida)", "Offshore GS", (25.90, -79.75),
     ["swordfish", "yellowfin_tuna", "mahi"],
     "1500ft+ Gulf Stream. Daytime deep-drop sword. Buoy line."),
    ("gs_bimini_edge", "Bimini Edge Wall", "Offshore GS", (25.70, -79.30),
     ["blue_marlin", "wahoo", "yellowfin_tuna"],
     "Bahamas dropoff edge. Trolling pelagics."),

    # ---------- SW FL (Gulf side) ----------
    ("swfl_pulley_ridge", "Pulley Ridge", "SW FL", (24.75, -83.75),
     ["red_grouper", "mutton_snapper", "amberjack"],
     "Deep coral reef 70nm W of Tortugas. Red grouper trophy zone."),
    ("swfl_naples_hardbottom", "Naples Hardbottom", "SW FL", (25.98, -82.10),
     ["red_grouper", "gag_grouper", "kingfish", "cobia"],
     "40nm W Naples. Winter kingfish + grouper year-round."),
    ("swfl_fort_myers_ledge", "Ft Myers Ledge (140ft)", "SW FL", (26.30, -82.60),
     ["red_grouper", "amberjack", "kingfish", "cobia"],
     "Deep ledge system 50nm SW Fort Myers."),
    ("swfl_edison_reef", "Edison Reef", "SW FL", (26.30, -82.15),
     ["snapper", "grouper", "spanish_mackerel", "cobia"],
     "State artificial reef 15nm W Fort Myers."),
]

# ============================================================================
# SE COAST — new offshore zones
# ============================================================================
SEC_NEW = [
    # ---------- North Carolina (S of Hatteras) ----------
    ("nc_manning_line", "The Manning Line", "NC", (34.10, -76.60),
     ["yellowfin_tuna", "blue_marlin", "wahoo", "mahi"],
     "Offshore Cape Lookout. Yellowfin spring, marlin summer."),
    ("nc_winyah_bay_grounds", "Winyah Bay Grounds", "NC", (33.20, -78.30),
     ["snapper", "grouper", "king_mackerel", "amberjack"],
     "Live bottom 25nm off SC line. Grouper + snapper."),
    ("nc_wrightsville_15_mile", "Wrightsville 15-Mile Rock", "NC", (34.10, -77.55),
     ["king_mackerel", "spanish_mackerel", "amberjack", "snapper"],
     "Classic hard bottom 15nm off Wrightsville Beach. Kings all summer."),
    ("nc_wrightsville_23_mile", "Wrightsville 23-Mile Rock", "NC", (34.00, -77.42),
     ["king_mackerel", "amberjack", "grouper", "snapper"],
     "Deeper live bottom 23nm SE Wrightsville. Bigger kings + AJs."),
    ("nc_ten_mile_boxcars", "The Ten-Mile Boxcars", "NC", (34.30, -77.65),
     ["king_mackerel", "spanish_mackerel", "cobia"],
     "Artificial reef of sunken railcars. Kings + Spanish."),
    ("nc_steeples", "The Steeples", "NC", (33.90, -76.75),
     ["yellowfin_tuna", "wahoo", "mahi", "blue_marlin"],
     "Offshore hump 50nm SE Wrightsville. Yellowfin school."),
    ("nc_swansboro_hole", "Swansboro Hole", "NC", (34.20, -77.10),
     ["grouper", "amberjack", "snapper"],
     "Deep structure hole. Grouper + AJ year-round."),

    # ---------- South Carolina ----------
    ("sc_georgetown_hole", "Georgetown Hole", "SC", (33.15, -78.65),
     ["yellowfin_tuna", "wahoo", "mahi", "blue_marlin", "sailfish"],
     "Legendary Gulf Stream break 55nm E Georgetown. All-species spot."),
    ("sc_charleston_bump", "Charleston Bump", "SC", (31.75, -79.10),
     ["yellowfin_tuna", "blue_marlin", "wahoo", "mahi", "sailfish"],
     "Massive underwater plateau. Marlin ground August-September."),
    ("sc_blackfish_banks", "Blackfish Bank", "SC", (33.05, -79.20),
     ["black_sea_bass", "grouper", "snapper", "amberjack"],
     "Live bottom 25nm E Georgetown. Sea bass + grouper."),
    ("sc_edisto_banks", "Edisto Banks", "SC", (32.30, -80.10),
     ["king_mackerel", "grouper", "snapper", "amberjack"],
     "Nearshore banks off Edisto Island. Popular king run."),
    ("sc_hilton_head_snapper_banks", "Hilton Head Snapper Banks", "SC", (32.05, -80.60),
     ["snapper", "grouper", "amberjack", "king_mackerel"],
     "Well-known snapper banks 25nm SE Hilton Head."),
    ("sc_deli_belly", "The Deli Belly", "SC", (32.15, -79.45),
     ["yellowfin_tuna", "wahoo", "mahi", "sailfish"],
     "Offshore break north of Charleston Bump. Wahoo winter."),

    # ---------- Georgia ----------
    ("ga_grays_reef", "Gray's Reef NMS", "GA", (31.40, -80.87),
     ["black_sea_bass", "gag_grouper", "snapper", "amberjack"],
     "National Marine Sanctuary 19mi off Sapelo Island. Regulated species."),
    ("ga_sapelo_live_bottom", "Sapelo Live Bottom", "GA", (31.35, -80.95),
     ["grouper", "snapper", "amberjack", "kingfish"],
     "Wide live bottom system. Bottom fishing productive."),
    ("ga_long_reef", "Long Reef (GA)", "GA", (31.20, -80.65),
     ["grouper", "snapper", "amberjack"],
     "Deep ledge system 40nm off Brunswick."),
    ("ga_navy_tower", "R-6 Navy Tower Wreck", "GA", (31.35, -80.60),
     ["amberjack", "grouper", "kingfish", "cobia"],
     "Sunken navy tower structure. Big amberjack."),
    ("ga_snapper_banks", "Georgia Snapper Banks", "GA", (31.00, -80.50),
     ["snapper", "grouper", "amberjack", "kingfish"],
     "Historic snapper banks 60nm off Brunswick. Deep water."),

    # ---------- NE FL ----------
    ("nefl_elton_bottom", "Elton Bottom", "NE FL", (30.40, -80.90),
     ["snapper", "grouper", "amberjack", "kingfish"],
     "Rocky bottom 25nm SE Amelia Island. Bottom fish + kings."),
    ("nefl_ledge_20fathom", "20-Fathom Ledge (Jacksonville)", "NE FL", (30.10, -81.05),
     ["snapper", "grouper", "amberjack", "kingfish", "wahoo"],
     "Continental shelf edge 40nm off Jax. Wahoo + tuna trolling."),
    ("nefl_ninemile", "Nine Mile Reef (Amelia)", "NE FL", (30.55, -81.20),
     ["king_mackerel", "spanish_mackerel", "cobia"],
     "Nearshore reef complex 9nm off Amelia Island. Kings + cobia."),
    ("nefl_st_augustine_ledge", "St. Augustine Ledge", "NE FL", (29.85, -81.10),
     ["snapper", "grouper", "amberjack", "kingfish"],
     "Rocky ledge 20nm off St. Augustine. Snapper year-round."),
    ("nefl_lion_wreck", "Lion Wreck", "NE FL", (30.20, -81.20),
     ["snapper", "grouper", "amberjack"],
     "300ft cargo ship wreck. Deep structure."),
]

# ============================================================================
# APPLY
# ============================================================================
def process(fname, new_zones, region_label):
    p = os.path.join(BASE, "data", fname)
    d = json.load(open(p))
    zones = d.get("zones", [])
    existing_ids = {z["id"] for z in zones}

    # Step 1: recategorize any existing category="offshore" zones
    recat = 0
    for z in zones:
        if z.get("category") == "offshore":
            new_cat = categorize_by_distance(z["center"], z.get("region", ""))
            z["category"] = new_cat
            recat += 1

    # Step 2: add new zones (skip dupes)
    added = 0
    skipped = 0
    for spec in new_zones:
        zid, name, region, coords, species, notes = spec
        if zid in existing_ids:
            skipped += 1
            continue
        z = make_zone(zid, name, region, coords, species, notes)
        zones.append(z)
        added += 1

    d["zones"] = zones
    open(p, "w").write(json.dumps(d, indent=2))
    print(f"{region_label}: recategorized {recat} existing offshore → nearshore/midshore/canyon · added {added} new · skipped {skipped} dupes · total now {len(zones)}")

process("zones_south_florida.json", SFL_NEW, "S. FLORIDA")
process("zones_south_atlantic.json", SEC_NEW, "SE COAST")

# Verify final categories
print()
for name, fname in [("S.FL", "zones_south_florida.json"), ("SE", "zones_south_atlantic.json")]:
    from collections import Counter
    d = json.load(open(os.path.join(BASE, "data", fname)))
    cats = Counter(z.get("category","?") for z in d["zones"])
    print(f"{name}: {dict(cats)}  total={len(d['zones'])}")
