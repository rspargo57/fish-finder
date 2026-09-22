# Zone 4 — Gulf (Perdido Key anchored) — Second Mate Pre-Scope

Randy 2026-08-29 (verbatim): *"I want you to think about it, you know, how to make that area. What are the best efficient spots in the Gulf there? And how it'll look on a map. Um, but do a little research because that's what I wanna do tomorrow and, uh, get things prepared."*

**Randy's stated anchor area: "near Perdido Key"** (Florida panhandle / Alabama border, on the Gulf of Mexico). Nightly voice transcription rendered this as "Pedrito Key" — same place.

This document is the pre-work so tomorrow's Zone 4 session has 60–70% of the answer already and just needs Randy's decisions on the remaining open questions.

---

## The 60-second summary

- **Home port candidate: Perdido Key, FL** (30.2833°N, −87.4570°W). Boat access to the Gulf via Perdido Pass. Marinas nearby: **Southwind Marina**, **Oyster Bar Marina**.
- **The star feature: DeSoto Canyon.** A submarine canyon that comes closer to shore than any other big-water structure in the northern Gulf — the classic offshore pelagic ground is 55–70 nm SW of Perdido Pass. Randy's 80 nm range covers it easily.
- **Iconic named spots (already community-established):** The Nipple, The Elbow, The Spur, The Squiggles, plus the deepwater oil rigs (Petronius, Ram Powell, Marlin, Devil's Tower).
- **Signature species (roughly year-round, but layered):** yellowfin tuna, blackfin tuna, blue marlin, wahoo, mahi, cobia (March–April spring migration is legendary), king mackerel, red snapper (extremely regulated — only a handful of recreational days per year for Florida residents), grouper, amberjack.
- **Two Gulf-unique signals** that don't exist in NE: **Loop Current** (replaces Gulf Stream — the water body yellowfin ride) and **DeSoto Canyon plume** (the deepwater edge that pushes bait against structure). Both need new harvesters — First Mate should scope in the Zone 4 session.

## Rollout sequence in the Second Mate skill

Zone 4 was already pre-named "Gulf (TX/LA/MS/AL/FL west)" as Rollout item 4. Randy's Aug 29 direction anchors it specifically at Perdido Key. Order of build follows the Second Mate's standard 9-pass Research Playbook — this document seeds passes 1–2 partially and lists candidates for 3–7.

---

## Pass 1 — Geographic scope + home port (partial pre-fill)

- **Bounding box (proposed):** min_lat 27.5 (south of Tampa), max_lat 30.75 (north of Pensacola Beach), min_lon −89.5 (LA/MS border-ish), max_lon −85.0 (Panama City area). This covers the panhandle + Alabama coast + a good chunk of the offshore water Randy would ever reach. Later Zones may split off Louisiana and West Florida as their own regions.
- **Sub-regions (chip filter):** FL-Panhandle · Alabama · (later) Mississippi · Louisiana.
- **Map center:** [29.5, −87.5], zoom 6.
- **Home port: Perdido Key, FL — coords 30.2833°N, −87.4570°W** (Perdido Pass area). NOAA gridpoint + tide station: **UNRESOLVED** (Pass 1 open work).
  - Tide station candidates: `8729840` (Pensacola FL) or `8729678` (Navy Fuel Depot Pensacola). Verify next-day tide publication.
  - NOAA gridpoint: query `api.weather.gov/points/30.2833,-87.4570` (likely office MOB — Mobile).
  - Marine forecast sample coords: pick a point 5–10 nm SW of Perdido Pass where Open-Meteo Marine returns non-null waves. Untested.
- **Alternate PORT_PRESETS (visitor-selectable):** Pensacola Beach FL, Orange Beach AL, Destin FL, Panama City FL, Dauphin Island AL.

## Pass 2 — Species active + seasonal calendars (needs full rebuild per Second Mate rule — NE calendars do not port)

Candidate species set (13 total, 10 offshore + 3 inshore focus):

**Offshore:**
| Species | Peak window | Notes |
|---|---|---|
| yellowfin_tuna | Year-round with March–May and Oct–Nov peaks | Loop Current position drives it |
| blackfin_tuna | Year-round; strongest Oct–Dec | Smaller, easier target, great table fare |
| blue_marlin | May–Oct | Emerald Coast Blue Marlin Classic anchors July |
| white_marlin | Sep–Oct | Fall run |
| wahoo | March–May + Oct–Nov | High-speed trolling |
| mahi | May–Sep | FADs and floaters, warm water |
| cobia | **March–April signature migration** along the beach | Sight-fishing from towers |
| king_mackerel | April–Nov | Massive schools; live bait |
| spanish_mackerel | April–Oct | Close in, kids' fish |
| red_snapper | **Regulated: 3–5 day FL recreational season in June** | Requires special calendar treatment — Zone must flag when open |
| vermilion_snapper | Year-round bag limit | Bottom |
| grouper (gag, red) | Regulated split-season | Bottom |
| amberjack | Regulated | Reef fish |

**Inshore:**
| Species | Peak window | Notes |
|---|---|---|
| redfish | Year-round with fall peak | Same as SE US |
| spotted_seatrout | Year-round with spring/fall peaks | |
| flounder | Fall migration Oct–Nov | Gigging tradition |

**New species that need `SPECIES` catalog additions:**
- `blackfin_tuna`, `cobia`, `king_mackerel`, `spanish_mackerel`, `red_snapper`, `vermilion_snapper`, `grouper`, `amberjack`, `redfish`, `spotted_seatrout`, `flounder`. Some already exist for Mid-Atl (cobia, king_mackerel) — reuse those definitions.

**Calendar sources to consult in tomorrow's Pass 2 build:**
- Visit Pensacola's seasonal fishing guide
- FishingBooker's "Fishing in Pensacola" complete guide
- Tradition Fishing Charters' seasonal guide (Perdido Key based)
- Florida FWC official regulation pages for red snapper + grouper + amberjack seasons

## Pass 3 — Zone discovery (candidate list — needs 2-source verification per Second Mate rule)

**Offshore / canyon (30+ nm):**
| Candidate zone | Approx location | Category | Species anchor |
|---|---|---|---|
| The Nipple | ~55–65 nm SW of Perdido Pass, ~29.4°N −87.3°W | midshore/canyon-edge | yellowfin, blue marlin, mahi |
| The Elbow | ~10 nm past Nipple, deeper water | canyon | yellowfin, blue marlin |
| The Spur | ~40–50 nm SW, N of Nipple | midshore | yellowfin |
| The Squiggles | E/SE of Nipple, deeper | canyon | marlin, tuna |
| DeSoto Canyon head | ~65 nm SW, canyon axis | canyon | blue marlin, deepwater bigeye potentially |
| The 131 Hole | (verify) | midshore | bottom + trolling |
| Petronius Rig | Deepwater floater (~150 mi offshore — probably OUT OF RANGE for Randy's 80 nm) | rig | yellowfin, blue marlin |
| Ram Powell Rig | Similar deep-water — OOR | rig | yellowfin, blue marlin |
| Marlin / Devil's Tower Rigs | OOR for 80 nm range | rig | (skip for Zone 4 v1) |

**Midshore reefs / wrecks (10–30 nm):**
| Candidate zone | Location | Category | Species anchor |
|---|---|---|---|
| The Massachusetts (wreck) | ~10 nm SW of Pensacola Pass | nearshore | amberjack, snapper, cobia |
| The Antares (wreck) | (verify) | nearshore | snapper, grouper |
| The Timberholes | (verify — Alabama waters) | midshore | bottom |
| Trysler Grounds | (verify) | midshore | grouper, snapper |
| Alabama Artificial Reef Zone | Hundreds of numbers 15–40 nm offshore | midshore | snapper, grouper, amberjack |
| Escambia County Public Reefs | Dozens of numbers 5–25 nm offshore | nearshore | snapper, king mackerel |

**Inshore / passes / bays:**
| Candidate zone | Location | Category | Species anchor |
|---|---|---|---|
| Perdido Pass | Inlet | inshore | redfish, trout, cobia (spring), Spanish mackerel |
| Perdido Bay | Behind Perdido Key | inshore | redfish, trout, flounder |
| Big Lagoon | E of Perdido Key | inshore | redfish, trout |
| Pensacola Bay | | inshore | redfish, trout |
| Pensacola Pass | Inlet | inshore | cobia sight-fishing spring |
| Ono Island back bays | AL side | inshore | redfish, snook (rare) |
| Wolf Bay / Bay LaLaunche | AL | inshore | trout, redfish |

**Cobia migration transects (season-only zones, March–April):**
| Candidate zone | Location | Category | Species anchor |
|---|---|---|---|
| Cobia Alley — Perdido Beach | Just off the beach March–April | seasonal-nearshore | cobia |
| Cobia Alley — Pensacola Beach | | seasonal-nearshore | cobia |

**Verified candidate count: ~20–22 zones for Zone 4 v1.** All above marked (verify) or without a coordinate need Second Mate research passes to lock in.

## Pass 4 — Captain source candidates (needs vetting)

**Aggregators / editorial:**
- FishingBooker Perdido Key daily — `fishingbooker.com/destinations/location/us/FL/perdido-key`
- FishingBooker Pensacola Beach daily
- FishingBooker Orange Beach AL daily
- Visit Pensacola blog fishing content
- Pensacola News Journal outdoors column

**Named captain socials + report pages:**
- Rooster Tail Fishing — `roostertailfishingperdidokey.com/fishing-pensacola-perdidokey`
- Can't Quit Fishin — `cantquitfishin.com/fishing-info/`
- Tradition Fishing Charters — `traditionfishingcharters.com/blog/`
- All Jack'd Up Charters — `alljackdupcharters.com`
- All Caught Up Fishing — `allcaughtupfishingcharters.com`
- Gulf Bay Fishing Charters — `fishingcharterpensacola.com`
- Overkill Adventures — `overkilladventures.com`

**Tackle shops:**
- Half Hitch Tackle (Panama City, Destin, Pensacola)
- Outcast Bait and Tackle (Pensacola)
- Gulf Coast Marine Supply
- J & M Tackle (Orange Beach AL)

**Forums (very active in this region):**
- **Pensacola Fishing Forum** — `pensacolafishingforum.com` — arguably the strongest single-forum fishing community in the Gulf; daily posts with dated reports
- The Hull Truth → Gulf Coast sub-forum
- Alabama Saltwater Fishing forums

**Tournaments (results as intel):**
- Emerald Coast Blue Marlin Classic (July, Destin) — annual winners + weights
- Alabama Deep Sea Fishing Rodeo (July, Dauphin Island) — 90-year tradition, huge species list
- Blue Marlin Grand Championship (Orange Beach)

## Pass 5 — YouTube channel hunt (needs `channel_id` extraction per Second Mate rule)

Candidates (assess with the acceptance bar: weekly uploads, dated titles, species+location patterns, 12+ months active):

- Chew On This Fishing (Pensacola-based)
- Overkill Adventures
- Blacktiph (Josh Jorgensen — national but Gulf-heavy)
- Fishing With Gary (Panhandle)
- Reel Time Florida Sportsman
- Salt Life TV Gulf content
- (research more once Zone 4 is scoped)

## Pass 6 — Whale-watch operators (Gulf is dolphin-heavy, not whale)

Whales are RARE in the Gulf (sperm whales in ultra-deep water only — irrelevant for Randy's range). **Dolphins are the bait proxy.** Sources:
- Any dolphin cruise operator out of Pensacola Beach / Perdido Key / Orange Beach
- Every offshore charter mentions dolphin sightings in reports
- Bottlenose dolphin pods often work bait pods near-shore; offshore Atlantic spotted dolphins ride bows near tuna

**The `whaleBoost` signal probably becomes `mammalBoost` in Zone 4** — same math, tuned for dolphin sightings, with a lighter weight since dolphins are less rare than NE humpbacks. First Mate call for tomorrow's session.

## Pass 7 — Environmental thresholds (Gulf-specific tuning)

**SST breaks — DIFFERENT from NE:**
- NE uses 68°F (bluefin/striper edge) + 72°F (yellowfin edge).
- Gulf yellowfin tolerate warmer water — proposed thresholds: **74°F (edge of "cold" Loop Current feature) + 78°F (warm Loop Current core)**. Verify with Tradition Fishing / Rooster Tail captain quotes.
- Cobia spring migration: track the **72°F beach water** — cobia move north when nearshore hits 72.

**Chlorophyll thresholds:**
- Gulf offshore water is cleaner than NE offshore. 0.15 and 0.30 mg/m³ NE thresholds may translate to 0.10 and 0.20 for offshore Gulf.
- Nearshore Gulf is much more turbid (Mississippi River plume, DeSoto Canyon river discharge) — probably 0.5+ mg/m³ for the inshore edge.

**Loop Current — CRITICAL new signal:**
- The Loop Current pushes warm water north from the Yucatan into the northern Gulf. Its western/northern edge is the yellowfin highway.
- Data source: NOAA OSPO Ocean Products (`ospo.noaa.gov`) or NASA JPL Gulf of Mexico altimetry.
- **First-class Gulf signal — propose `loopCurrentEdgeBoost` for First Mate implementation.** Analogous to the Mid-Atl `gulfStreamEdgeBoost` proposal.

**DeSoto Canyon plume:**
- Where deep upwelled water meets the shelf — concentrated bait piles.
- Data source: same altimetry + SST anomalies.

## Pass 8 — Assembly (tomorrow's session)

1. Fill in `regions/gulf.json` per template.
2. Fill in `data/zone_data_gulf.json` (or `data/zones_gulf.json` if we stay on the monolithic pattern — decide per canonical-location doctrine).
3. Fill in `data/intel_sources_gulf.json`.
4. Run `python3 build.py --region gulf --check` (or the monolithic build variant) and fix any refusal.
5. Verify map renders, SST/chla overlays work in Gulf bounds, tide widget populates.

## Pass 9 — Ship + first-week monitor

- Deliver `gulf-fish-finder.html` for Randy to test.
- Set up nightly refresh trigger for Gulf region (mirror of NE nightly).
- Watch first 7 nights — captain sources returning data? YouTube channels producing Gulf intel? Loop Current signal firing?

---

## Randy's likely questions in tomorrow's session (pre-answered)

**Q: Is my 28' Cobia enough boat for the Gulf?**
A: For the DeSoto Canyon spots (Nipple, Elbow, Spur — 55–70 nm from Perdido Pass), yes — you're at 60-90% of range. For the deepwater rigs (Petronius, Ram Powell, ~150 mi), no — those are OOR and Zone 4 v1 will not pick them. Real Gulf tuna captains run 40'+ boats offshore, but Randy's 28' handles the canyon on calm days.

**Q: Do I need a different license?**
A: Yes — Florida saltwater fishing license (different from CT). Alabama has reciprocity for FL license in some circumstances. HMS Angling permit for tuna. NOAA reporting required for bluefin, dolphin, wahoo landings.

**Q: Can I trailer my boat down or does this need a Gulf boat?**
A: Trailerable — 28' Cobia on tandem trailer to Perdido Key is 1200-mi drive from CT. Alternative: charter a Gulf-based boat with the app in-hand.

**Q: Is red snapper really only open a few days a year?**
A: For federal waters (offshore), yes — recreational for-hire season is typically a longer window (June-July); private-boat recreational is a very short window in June (Florida sets its own). State waters (within 9nm) have their own Florida-specific short season. Zone 4 must show the current-year window as a first-class app feature.

**Q: Is there any winter fishing?**
A: Yes — cobia migration in March–April is legendary (many captains say it's the best month). Yellowfin bite January–February can be excellent when Loop Current pushes north. Zone 4 does NOT go dormant in winter like Zone 1.

---

## Open decisions for Randy tomorrow

1. **Home port confirmation.** Perdido Key at 30.2833°N, −87.4570°W? Or does Randy have a specific marina in mind (Southwind, Oyster Bar, or elsewhere)?
2. **Boat.** Same 28' Cobia (trailered / theoretical for now) or a different Gulf boat profile?
3. **Species opt-in.** All 13 species above, or trim to Randy's actual target set for this region?
4. **Speed of rollout.** Full Zone 4 in one week (aggressive) or 2-3 weeks (Zone 1 quality bar strict)?
5. **Loop Current signal.** Ship without it (Zone 4 v1) and add later, OR wait to ship until Loop Current is wired (Zone 4 v1 with the signal)?

---

## Sources consulted (2026-08-29 pre-scope research)

- FishingBooker Perdido Key + Pensacola + Orange Beach location pages
- FishingBooker "Pensacola Deep Sea Fishing Complete Guide"
- Visit Pensacola seasonal fishing guide
- Tradition Fishing Charters seasonal guide + red snapper blog
- Pensacola Fishing Forum threads on The Nipple, Elbow, Spur, DeSoto Canyon
- Alabama Artificial Reef references (Salt Strong, Florida Go Fishing)
- Rooster Tail Fishing Perdido Key reports page
- NOAA Fisheries 2026 red snapper federal season bulletin
