# Fish Finder — The "Do It All At Once" List

Randy's rule (2026-07-29): "Anytime we do something excellent and there's one little thing left, put it on this list. And anything I had to go sign up for or pay a fee for — same. At some point we'll just do it all at once."

Every session that touches Fish Finder must check this file at the end of the session and add anything new. Nothing gets forgotten.

Newest additions at the bottom of each section.

---

## 🔑 Needs Randy — signups, purchases, external accounts

These are batched on purpose. When Randy sits down to knock them out, we can walk through them together in one sitting. Each one has enough context that a fresh session can pick it up cold.

- [ ] **GitHub auth for the sandbox** (2026-08-29, ~2 min)
  - `gh` CLI is installed on the sandbox but has no valid token. Two paths, either works:
    1. On Randy's laptop: run `gh auth login`, choose GitHub.com → HTTPS → paste the auth code the CLI shows. Copy the resulting token from `~/.config/gh/hosts.yml` and share it with me.
    2. Or: github.com → Settings → Developer Settings → Personal Access Tokens → Generate new token (classic) → tick `repo` scope → generate → paste to me.
  - After: I create `github.com/rspargo57/fish-finder` (private), `git remote add origin`, `git push -u origin main` — the whole project (with 3 commits so far: initial, v24.26 tile, changelog) becomes safe against sandbox recycle.
  - Blocked until Randy runs one of those; the sandbox itself has no browser to complete the OAuth flow.

- [ ] **Google Drive connector re-authorization** (2026-08-29, ~1 min)
  - The connector's OAuth token expired mid-session today. I couldn't push a "Fish Finder Backup" folder to Randy's Drive.
  - Fix: claude.ai → Settings → Connectors → Google Drive → click "Reconnect."
  - After: I can push nightly backup zips to a Drive folder as a third redundant copy (sandbox + Desktop + Drive).

- [ ] **Copernicus Marine account** (free, email verification, ~5 min)
  - URL: https://data.marine.copernicus.eu/register
  - Unlocks: altimetry (SSHA / warm-core rings) — the #1 signal we're missing per First Mate research. Also unlocks salinity fronts (bundled — same account).
  - After Randy registers: he gives me the username + password, I store them as env vars in the nightly trigger environment, and I wire up `sshaBoost` and `salinityBoost` into effectiveHeat. Expected accuracy lift: 5-10% on canyon picks (per ROFFS-style methodology).

- [ ] **Register fishfinders.app domain** (~$14/year at Cloudflare Registrar)
  - Landing page + interactive tool are already built and sitting in `/root/fish-finder/deploy/`.
  - Alternative: fishfinder.io, fishfinder.tools, fishfinder.co — I'll check availability the day Randy is ready to buy.

- [ ] **Cloudflare account** (free)
  - Needed to host fishfinders.app landing page + tool. Cloudflare Pages is free for our traffic level.
  - Randy signs up → I deploy the built site → done in one session.

- [ ] **Formsubmit.co activation** (free, one-click email verification)
  - Landing page has a beta-signup form. First time someone submits, formsubmit sends Randy a verification email. Once he clicks it, the form starts emailing him submissions.
  - Zero setup on our side — the form is already wired to Randy's email address.

- [ ] **Global Fishing Watch API token** (free registration, ~5 min)
  - URL: https://globalfishingwatch.org/our-apis/tokens
  - **What's shipping today (v23.30, no signup required):** the 🚢 Boats drawer embeds MarineTraffic's public live map centered on Randy's fishing area — commercial + charter AIS positions, no API key. It's an iframe overlay, not a native Leaflet layer, and it shows ALL AIS vessels (fishing + cargo + tanker). Randy asked for this and it's live now.
  - **What GFW would upgrade to when Randy registers:** a proper native Leaflet HEATMAP layer that mixes AIS + Sentinel-1 SAR + VMS to identify FISHING activity specifically (filters out tankers/cargo), aggregates 24-72 hour fleet density, and can be toggled directly on the map next to SST / Chla / Currents. Same source-of-truth GFW uses for their own public map. Big UX win.
  - Randy signs up → gives me the token → I swap the drawer for a native `L.tileLayer(gfwTiles, {authToken})` on the map. First iteration would be a fishing-effort heatmap; second iteration adds vessel-by-vessel click popups with vessel name, flag, gear type.

- [ ] **YouTube Data API v3 key** (free, requires Google account — ~10 min at Google Cloud Console)
  - URL: https://console.cloud.google.com/apis/library/youtube.googleapis.com
  - Free tier: 10,000 quota units/day (roughly 100 searches or 1000 video-metadata lookups). Well within our needs.
  - Unlocks Phase 2 of YouTube intel: dynamic search for "bluefin tuna Montauk uploaded last 7 days" style queries (finds NEW captains we don't yet know about), and programmatic fetching of video descriptions with full metadata (view counts, tags, categories) rather than parsing RSS feeds.
  - Phase 1 (RSS-based, no signup needed) can start immediately and doesn't depend on this.

- [ ] **Anthropic OR OpenAI API key** (paid, ~$30-70/month at our volume)
  - URLs: https://console.anthropic.com/ · https://platform.openai.com/api-keys
  - Unlocks Phase 4 of YouTube intel: vision analysis of every recent video's thumbnail (identify species, estimate size class, spot chartplotter/depth-sounder screens with lat/lon in the frame).
  - Wait until Phase 3 (transcript mining) is proven before signing up — no point paying for a second vision layer if the free text/transcript layer already gets us there.

- [ ] **Claude in Slack** (free, ~5 min — Randy 2026-09-01)
  - URL: `https://claude.ai/settings/integrations` → Slack → Add → pick workspace → Allow.
  - Unlocks: I can post tomorrow's picks + verdict into a Slack channel every morning after the nightly build. Also: catch-alerts when a captain report drops naming a top-picked zone (via ScheduleWakeup + Monitor).
  - After Randy connects it, he tells me which channel (`#fish-finder` or similar) and I wire the nightly to post there. Zero setup on my side once the OAuth is done.

- [ ] **aisstream.io API key** (free, ~5 min)
  - URL: https://aisstream.io/
  - Free tier: real-time AIS position stream via WebSocket for any set of MMSIs. Once we have the API key + a populated `data/captain_vessels.json` with MMSIs, the `🎣 Captains` layer upgrades from static home-port dots to live boat positions refreshing every ~30 seconds.
  - Also on the "MMSI research" TODO in Small Leftovers below — one signup unlocks both.

- [ ] **eBird API key** (free, email verification, ~5 min)
  - URL: https://ebird.org/api/keygen
  - Unlocks: the birdBoost signal (currently a documented placeholder returning 0). eBird has offshore pelagic-seabird observation counts that correlate with bait balls / tuna feeding activity. Free API, generous rate limits.
  - After Randy provides the key: I wire it into the nightly harvester and birdBoost fires on real data.

- [ ] **Google Cloud project for YouTube Data API v3** (already listed above under YouTube key — same signup)
  - Note: this is the same signup as the YouTube Data API v3 key entry a few items up. Listed here so Randy sees both as one Google Cloud task.

---

## 🔧 Small leftovers from features we already shipped

These are the "one little thing left" items — the feature works, but there's a polish or extension we noted at the time. Batch them into a cleanup session.

- [x] **"Add your own fish" custom species** — DONE 2026-07-29. New inline editor under the Add Spot species dropdown ("+ Don't see your fish? Add a custom species"). Fields: name, temp range °F, active months (12-chip picker), auto-assigned color from a palette. Persists in localStorage under `ff_custom_species_v1`. Custom species merge into the SPECIES catalog under a "🐠 Your Custom Species" optgroup in the picker + show as a managed list beneath the form with ✕ remove. Custom-only (no model impact yet, same as custom spots).

- [ ] **Prediction accuracy tracker UI** — the archive-driven scoring loop is designed and documented in the First Mate skill, but the UI widget that shows "accuracy_1: 0.72 · accuracy_3: 0.58 · accuracy_7: 0.41" doesn't exist yet. Needs 14+ days of archived look_ahead data before it's meaningful. Archive started collecting look_ahead 2026-07-28 — check back after 2026-08-11.

- [ ] **Auto-tuning signal weights** — First Mate skill has the methodology written out. Needs 30+ days of accuracy data before it can run. Earliest realistic date: 2026-08-27.

- [x] **My Catches log** — DONE 2026-07-29 (Randy's ask: "when I catch a fish, I can have a place there that I can interact and put the fish data in, and then you will archive it or do what we're supposed to do with it"). New "🎣 My Catches" card at the top of Today's Report. Log any catch — date, zone (or custom spot), species (built-in + custom), size in inches, kept/released, method, water temp, time of day, notes. Persists to `ff_catch_log_v1` localStorage (last 500). "Your Season So Far" summary line shows total catches, top species, top spot. "📋 Copy log for Captain" button formats all catches as a paste-ready markdown block Randy can share in chat so the Captain can promote them into dated zone.heat updates + archive result fields per the data-quality guardrails. Trip Planner outcome recorder now double-writes: recording a "caught" outcome auto-adds an entry to the catch log so Randy only records once, not twice.

- [x] **Save Trip Planner accuracy** — INFRASTRUCTURE DONE 2026-07-29. New `ff_trip_archive_v1` localStorage schema stores past trips with verdict-at-plan + outcome. Trip plan card now shows a "🎣 How'd it go?" outcome recorder in-card when the plan's target date is today or past — buttons: Went & caught / Went & skunked / Stayed home / Didn't happen. For "caught," a species picker (including custom species) captures WHAT was caught. Notes field optional. Recording archives the trip and clears the active plan. New "📈 Trip Planner Accuracy" card sits below each mode's plan card — shows aggregate "right N of last M" once at least one GO/STAY-HOME outcome exists, with the last 5 trips listed and color-coded (right_go, right_stay, part_right, wrong_stay, neutral). Actual accuracy numbers require Randy to plan + record real trips — until then the card is dormant.

- [ ] **Research pass 2026-07-30 — add new captain sources to harvester** — 9 candidates identified (Snug Harbor Marina RI, NE Offshore podcast, Tuna Cartel IG, Rockfish Charters IG, J&J Sports Fishing RSS, Fisherman's World CT, Tall Tailz Charters IG, Aces Wild YouTube `UCJJtiG7bECQkh2RGgg5FINA`, Frances Fleet). Full details in Captain SKILL.md "Captain source candidates — audit 2026-07-30" section. Prioritize Tier A first (Snug Harbor + NE Offshore podcast + Tuna Cartel + Rockfish). Estimated effort: 1-2 hours to add YouTube channels (drop into `data/youtube_channels.json`); 3-4 hours for a generic RSS harvester to handle tackle-shop blogs + podcast feeds; Instagram scraping is complex — may want to defer or skip.

- [ ] **Research pass 2026-07-30 — 7 new prediction signal candidates** — full details in First Mate SKILL.md signal backlog #12-#18. Rank order recommended by research agent:
  1. **HYCOM Thermocline / Mixed Layer Depth** (HIGH VALUE, MEDIUM EFFORT — biggest gap in current model; pros use it)
  2. **AMO index** (HIGH VALUE, TRIVIAL EFFORT — one number/month from NOAA PSL, peer-reviewed link to bluefin distribution)
  3. **Gulf Stream North Wall Index** (HIGH FOR CANYONS, LOW EFFORT — Rutgers RUCOOL daily feed)
  4. **eBird pelagic seabirds** (fills the birdBoost placeholder we've been carrying dead — free API with email signup)
  5. **Sentinel-3 OLCI 300m chlorophyll** (upgrade to existing signals — 2.5× finer than VIIRS)
  6. **Mid-Atlantic Cold Pool** (HIGH for striper, medium effort)
  7. **WHOI Robots4Whales acoustic buoys** (upgrade to whale signal — 24/7 detection, weather-independent)

  **Answer to Randy's satellite-detects-fish question:** confirmed impossible. Tuna are too deep/fast/small for satellites to see directly. What actually works is inferring where tuna SHOULD be from subsurface ocean structure — signal #12 (HYCOM MLD) is exactly that.

- [ ] **Bathymetric proximity for user-added pins** — GEBCO bathymetry contour proximity boost for custom spots (already implicit for curated zones via zone selection). Low priority per First Mate — Randy rarely adds spots off-structure.

- [x] **7-Day Look-Ahead persistence** — DONE 2026-07-29. Added `compute_look_ahead()` to `archive.py` (server-side mirror of the JS widget); `save_snapshot()` now accepts `zone_weather=` and stores a `look_ahead` array on every snapshot with 7 daily entries (date, day_index, day_label, offshore pick + inshore pick, each with full per-slot morning/afternoon weather). Verified on today's snapshot: 7 entries, offshore pick = "Nearshore South of Block Island" @ effective 9.7, inshore = "Block Island Reefs" @ 9.7, weather populated with wind/wave/direction/period/sky. Accuracy tracker now has ground truth to compare against.

- [ ] **Salinity fronts** — bundles with altimetry (same Copernicus account); wire in once Copernicus is live.

- [x] **Phase 1: YouTube RSS harvester expansion** — DONE 2026-07-29. Shipped `youtube_harvest.py` + `data/youtube_channels.json` (15 verified NE channels) + wired into nightly build + Fleet Chatter widget on Today's Report. First live harvest: 22 videos with intel in 14-day window, corroborated bluefin_giant + striped_bass + 5 named spots. Archive now stores full YouTube intel for future First Mate accuracy analysis.

- [ ] **Phase 3: YouTube auto-caption transcript mining** (bigger lift, wait until Phase 1 + 2 are proven)
  - Tool: `yt-dlp` to fetch auto-generated captions for videos on our harvester list. LLM-parse each transcript for structured intel: `{date, species, size, location_named, coordinates_if_stated, method, water_temp_if_stated, notes}`.
  - Value: charter captains narrate their trips — "we're 12 miles south of Montauk in 40 fathoms, water temp 71, caught this 47-inch bluefin on a butterfly jig." Extremely rich signal that our current source list doesn't capture.
  - Cautions: LLM extraction can hallucinate specifics ("47 inches" when he never said it). Must use strict extraction prompts + two-source corroboration + lower confidence weight than direct captain quotes.
  - Prerequisite: Phase 1 audit shows the top 5 channels consistently produce parseable narration.

- [ ] **Phase 4: YouTube thumbnail vision analysis** (biggest lift, wait until Phase 3 is validated)
  - Randy's ask 2026-07-29: "can we get intel from pictures of the fish or where they were caught?"
  - Every RSS entry already includes a `<media:thumbnail>` URL. Download → send to a vision model (Anthropic/OpenAI vision API) → extract: species visible, estimated size class, any visible location clues (water color, coastline, chartplotter screen with lat/lon or depth, sunrise vs sunset direction).
  - **Cost**: requires vision API key (Anthropic or OpenAI). ~$0.005-0.01 per image × ~225 thumbnails/night × 30 nights = ~$30-70/month. OR self-hosted vision model (free but slower + lower quality).
  - **Value ranking**: species confirmation = HIGH (fish are visually distinctive); size class = MEDIUM (held-up shots give estimates); location clues = LOW-MEDIUM (open water looks like open water; but occasional chartplotter shots with visible lat/lon or depth would be gold).
  - **Prerequisite**: Phase 1 text corroboration validated after 30+ days AND Phase 3 transcript mining live — otherwise we're stacking unvalidated signals.
  - **Requires Randy signup**: Anthropic API key or OpenAI API key.

---

## 🚀 Growth — multi-region expansion

Randy's strategic direction (2026-07-29): Fish Finder is a template for regional prediction tools. Northeast is the beachhead; the plan is to replicate the same architecture for Mid-Atlantic, Southeast, Gulf, Florida, SoCal, and eventually PNW.

- [x] **Zone 4 — Gulf / Emerald Coast** — ✅ SHIPPED 2026-08-29 (v24.29). Live at `fishfinders.app/map/?r=gulf`. 33 zones covering Perdido Key east through Panama City + Apalachicola. See CHANGELOG v24.29 for the full coverage list. Randy asked to "extend it east a bit" — done: Fort Walton/Destin + Panama City + Cape San Blas all covered.
- [ ] **Zone 4 Gulf v2 — captain intel harvesters + Loop Current** (follow-up):
  - Add Gulf-region source classification to `fisherman_harvest.py` (Fisherman Magazine FL panhandle regional feed) so Gulf zones get bait/whale entries auto-promoted.
  - Add Gulf sources to `charter_harvest.py` (Cant Quit Fishin daily, Rooster Tail, Tradition Fishing Charters blogs).
  - Add Gulf YouTube channels to `data/youtube_channels.json` — Chew On This Fishing (Pensacola), Overkill Adventures, Blacktiph, Fishing With Gary; wire keyword→zone map in `data/youtube_zone_map.json` for Gulf zone names.
  - Loop Current signal — First Mate scope. NOAA OSPO Ocean Products feed. Analogous to gulfStreamEdgeBoost proposed for Mid-Atl.
  - Whale/dolphin proxy for Gulf — dolphin cruise operators out of Pensacola/PCB. Sparse but not zero.
- [ ] **Zone 4 Gulf WARM-START retroactive** — v1 shipped 2026-08-29 cold (heat: 5 everywhere, no captain-intel harvesters plumbed). Randy's 2026-08-29 rule now mandates warm-start for every new region. Execute:
  1. Add 6-8 Gulf YouTube channels to `data/youtube_channels.json` with real channel_ids (per Second Mate Pass 5): Chew On This (Pensacola), Overkill Adventures, Northwest Florida Fishing Report podcast, Great Days Outdoors NW Florida, Blue Water Charter 30A, Fishing With Gary (Panhandle), Tradition Fishing Charters (Perdido/Pensacola), Rooster Tail Fishing.
  2. Wire Gulf to `fisherman_harvest.py` (Fisherman Magazine FL panhandle regional feed) and `charter_harvest.py` (Cant Quit Fishin, Pensacola Fishing Forum RSS).
  3. Backfill 21 days of each new source's content into the archive.
  4. Run `python3 spot_discovery.py --region gulf --days 21 --min-mentions 1` — surface unknown Gulf spot names; research + add verified ones as zones.
  5. Seed initial heat scores per warm-start protocol: 0 mentions → 5; 1-2 → 6; 3+ → 7; 5+ recent-bias → 8.
  6. Populate initial bait_intel + whale_sightings from harvester auto-promotion.
  7. Log v24.N Gulf warm-start entry to CHANGELOG.



- [ ] **Refactor for multi-region + build Mid-Atlantic prototype** — 🎯 **Second Mate agent now owns this** (created 2026-07-29). When Randy is ready to pull the trigger, invoke the Second Mate skill and it has the full plan pre-scoped.
  - **Refactor step (1 focused session):** Pull the ~15 region-specific hardcodes into a single `regions/<region>.json` config file. The Second Mate skill's "Prerequisite: the multi-region refactor" section has the complete audit — every file:line where region-specific code lives, the target JSON shape, and a 9-step refactor sequence including diff-verify.
  - **Mid-Atlantic prototype (2 focused sessions):** Second Mate's "Mid-Atlantic Playbook" section pre-scopes ~25 candidate zones (Hudson / Baltimore / Wilmington / Poor Man's / Washington / Norfolk canyons + Barnegat Ridge / Jackspot / Chicken Bone / Fingers / CBBT / Oregon Inlet / Point Hatteras), captain source candidates (OTW NJ/DE/MD/VA, FishingBooker AC/Cape May/OC MD/VA Beach/Oregon Inlet, named captains like Canyon Runner + Marli + Voo Doo + Blood Money), YouTube channel candidates, whale-watch operator candidates (Cape May Whale Watcher, Delaware Bay Whale Watch, VA Aquarium, OBX Dolphin Tours), tide-station candidates (Atlantic City, Ocean City Inlet, CBBT, Duck), and species tweaks (add cobia + king mackerel + spanish mackerel + bull red drum + spotted seatrout; recalibrate bluefin calendar — NJ peak Nov, NC peak Jan–Mar).
  - **Randy naming (2026-07-29):** each region is called a **"Zone"** at the product level. Zone 1 = Northeast (current live tool = the quality bar). Zone 2 = Mid-Atlantic (Cape May home port locked, per Randy's "going with your recommendation").
  - **Zone-1-quality doctrine (Randy 2026-07-29 verbatim):** "The Captain has to be in charge, the First Mate has to be involved, and this whole thing has to be as flawless as we made it for our first thing." No Zone ships that falls short of Zone 1 — better a partial Zone 2 held back than a mediocre one that dilutes the brand.
  - **No further Randy decisions needed** — Second Mate has everything it needs to execute when Randy says "go."
  - **Success metric:** side-by-side proof that the same engine produces Mid-Atlantic picks as cleanly as NE picks. Then rollout to Southeast → Gulf → FL → SoCal → PNW each follows the same Second Mate blueprint.
  - **Business implication:** once the refactor is done, adding a region is roughly 12–15 focused hours of research + build per region, then the nightly trigger runs itself and the model learns.

---

## 🚀 Go-live path — get this in front of other people

Randy's 2026-07-29 ask: "What do we do to get this thing so that I can have other people log in to it and use it?" The plan is a two-phase rollout — cheap ship first, backend-ify later if demand shows up.

### Phase 1 — Public beta site (Path A · fast + cheap · ~$14/yr, 30 min Randy time) — 🚀 IN PROGRESS 2026-07-29

**Randy said "Let's launch phase one." Deploy package staged, checklist delivered. Waiting on Randy signups.**

**Randy's checklist (in `LAUNCH-CHECKLIST.md` delivered to Desktop):**
- [ ] **Step 1:** Register fishfinders.app at Cloudflare Registrar (~$14/yr). Alternates if taken: fishfinder.tools, .io, .fish, .co, thefishfinders.app.
- [ ] **Step 2:** Create Cloudflare Pages project named "fishfinder"; drag-and-drop `fishfinder-app-v1.zip` (delivered).
- [ ] **Step 3:** Connect custom domain fishfinders.app to the Pages project.
- [ ] **Step 4:** Activate formsubmit.co by submitting the beta form once + clicking verification email.
- [ ] **Randy replies with:** (1) the domain he ended up with, (2) the .pages.dev URL, (3) confirmation that fishfinders.app loads.

**Claude's checklist (post-Randy-signup):**
- [x] Verify site loads at the .pages.dev URL and at the custom domain.
- [x] **Get Cloudflare API token from Randy — DONE 2026-08-02.** Randy created a scoped `Account · Cloudflare Pages · Edit` token via the dashboard walkthrough. Token verified against `/user/tokens/verify` (returns active + valid). Baked into the nightly trigger's Step 5.
- [x] **Update trigger `trig_018ZNaP7FvzVjTTfLH1RxtkR` to auto-deploy — DONE 2026-08-02.** Trigger prompt extended from 7 steps to 8; new Step 5 stages the built site into a clean tree, runs `wrangler pages deploy --project-name fishfinder --branch main`, and verifies with `curl -sI https://fishfinders.app/`. Failure is non-fatal — the trigger still finishes steps 6-8 (summary, skill freshness, TODO) and prints a `⚠ Cloudflare deploy failed` line in the brief. Manual end-to-end test done in interactive session: `curl -sI https://fishfinders.app/` returned `HTTP/2 200` after `wrangler pages deploy` uploaded the v16 build.
- [ ] Update Captain SKILL.md with "we are live" state + the live URLs.
- [ ] Write a "How to share Fish Finder with friends" one-pager for Randy.

**What users get in Phase 1:** anyone visits fishfinders.app, sees the landing page, clicks "Open the tool," gets the full Fish Finder in their browser. All data (spots, species, catches, trip archive) lives in THEIR browser (localStorage). No login, no cross-device sync, no accounts. Beta signup form emails Randy on submissions. Site auto-updates every 8:05pm ET when the nightly build finishes.

### Phase 2 — Real multi-user platform (Path B · only if Phase 1 shows demand)

Don't build until Phase 1 has proven someone wants this. Rule of thumb: 10+ organic beta signups per month, OR a piece of coverage (blog / podcast / tackle-shop feature) → Phase 2 justified.

- [ ] **Backend service** — Cloudflare Workers is cheapest (~$5/month at low traffic, scales up gradually). Alternative: a small Node/Python API on Fly.io or Render.
- [ ] **Database** — Cloudflare D1 (SQLite-based, integrated with Workers) OR Supabase (Postgres + auth bundled).
- [ ] **Auth** — magic-link email is simplest. Supabase or Clerk free-tier both work up to ~1000 users.
- [ ] **User data model** — per-user: port + boat + wind/wave caps + spots + species + trip archive + catch log. Migrate the current localStorage schemas to server-side.
- [ ] **Admin dashboard** — Randy sees: who signed up, how many active in the last 7/30 days, most-added spots, most-logged species, most-common home ports. Helps decide what to build next.
- [ ] **Cross-device sync** — user logs in on phone, sees the same catches they logged on laptop.
- [ ] **Estimated effort:** 2-3 weeks of focused development. Not a weekend project.

### Business / legal — needed BEFORE money starts flowing (Phase 2), useful even earlier

- [ ] **LLC formation** — Connecticut LLC likely right (Randy's home state + where he fishes). ~$120 filing fee via CT Secretary of State + ~$80/yr annual report. Alternative: Delaware LLC is standard for tech startups but adds a registered agent (~$100/yr) + more complexity — only worth it if raising investment. Recommendation: CT LLC unless there's a specific reason to go Delaware.
- [ ] **EIN** (federal tax ID) — free from IRS.gov, 10 minutes online. Needed for the business bank account.
- [ ] **Business bank account** — most local credit unions are free at low volume (e.g., Nutmeg State FCU if Randy already banks there). Keep subscription revenue separate from personal money.
- [ ] **Terms of Service + Privacy Policy** — I can draft both. Include a HARD fishing safety disclaimer: "This tool is informational only. Never go to sea in unsafe conditions regardless of what any pick, verdict, or forecast says. You are responsible for your vessel, your safety, and your decisions." Randy should have a lawyer's eyes on the final version before going live.
- [ ] **Business insurance** — general liability ~$50-100/month, probably not day-one. Worth considering once real users exist + Randy's picks influence someone's fishing decisions.
- [ ] **Support email** — support@fishfinders.app (Cloudflare Email Routing is free — forwards to Randy's Gmail).

### Recommendation: do Phase 1 THIS FALL (Sept-Oct), evaluate Dec, decide about Phase 2

Phase 1 is a $14 experiment. Ship it, invite 20 fishing friends, watch what happens. If people come back and use it week over week, Phase 2 is justified. If they use it once and disappear, we learned something without spending months on backend infrastructure. Randy's 2026-07-29 doctrine (from the Second Mate skill's Zone-1-quality section) applies here too: "better to hold something back than ship a diluted version" — same idea applies to Phase 2 (don't build it half-way).

---

- [ ] **Auto-bait harvester** — like `whale_harvest.py` but for bait mentions. Best source candidates: OTW Northeast Offshore Report weekly video descriptions (via YouTube RSS which we already harvest — just add bait keyword extraction), FishingBooker Montauk daily aggregator (has fresh reports but doesn't always mention bait explicitly). Estimated effort: 2-3 hours. High priority — currently the ONLY manual-refresh signal in the daily loop after we shipped auto-whales.

- [x] **`heat_updated` per-zone timestamp** — DONE 2026-07-31 (pass 16.1). All 42 zones now carry `heat_updated`. `data_freshness_audit.py` audits per-zone (10-21d = stale, >21d = dead) and rolls up the overall `live_heat` status to worst-of-any-zone. `build-inlined.py` auto-bumps the field from YouTube chatter every nightly build. Pick card shows a two-line freshness stamp (score date + latest chatter date). Data-health banner at top of the report surfaces the fleet-wide count.

- [ ] **Backfill fresh heat scores for the 30 zones flagged DEAD on 2026-07-31** — the Captain needs to hunt On The Water / Fisherman Magazine / captain-podcast reports for each of the 30 zones seeded at 2026-07-09 baseline. Priority target list (from freshness audit's `stalest_zones`): The Race, Plum Gut, Watch Hill Reef, Millstone Point, Narragansett Bay / Newport Rips, Providence/Seekonk, The Acid Barge, Southeast Coxes Ledge, The Gully, Habs Ledge, Tuna Ridge, The Butterfish Hole, The Mud Hole, Hudson Canyon, Block Canyon, Atlantis Canyon (the last two are TIER 1 — currently tied for #1 offshore pick alongside Nearshore S of Block; any fresh intel on these would potentially rotate tomorrow's pick). Two-source rule still holds — heat number only changes with corroboration.

- [ ] **Bird intel decision — remove birdBoost or find a source** — currently `birdBoost` is in `effectiveHeat()` but bird_intel is always empty (audit flags it as `dead`). Two options: (a) eBird API integration (~4 hrs, needs Google Cloud project for the API key), or (b) explicitly remove birdBoost from the formula per First Mate's "prefer removing to adding" rule. Randy's call — the +0.4 boost isn't hurting anything at 0 contribution, but it's noise in the formula documentation.

## ⏳ Blocked on data / time to elapse

These aren't tasks — they're things we can only DO after some real-world clock time has passed. Listed so we don't forget to circle back.

- [ ] **First accuracy analysis run** — earliest 2026-08-12 (needs 14 days of archived look_ahead; started collecting 2026-07-29, the day persistence actually shipped — the earlier "started 2026-07-28" note was aspirational; the code that writes look_ahead didn't exist until 2026-07-29).

- [ ] **First weight-tuning pass** — earliest 2026-08-27 (needs 30+ days).

- [ ] **Persistence signal starts firing** — the persistenceBoost function will start returning non-zero as of ~2026-08-01 (needs 3 consecutive days of SST contour archives; started collecting them 2026-07-29).

- [x] **Sentinel-2 visual overlay** — DONE 2026-08-12 (v23.32). `🛰 Satellite` chip in the layer rail overlays HLS Sentinel-30m true-color from NASA GIBS. Free, no signup, 30m/pixel, ~2-3 day revisit. Fallback to MODIS Terra 250m daily when HLS unavailable. Individual boats not resolvable; clusters (50-200 boats) visible as bright blooms. Automated YOLOv8 detection is the follow-on below.

- [ ] **Research MMSIs for tracked captains** — DATA-ENTRY task, per-captain, ~5-15 min each. Ship-tonight v23.34 added the `🎣 Captains` layer with 8 charter captains from our YouTube harvest list. Each has an entry in `data/captain_vessels.json` but `mmsi: null` — so their marker sits at home-port and the "Track live →" button searches AIS by boat name instead of deep-linking.
  - **To fill in a MMSI:** go to `https://www.vesselfinder.com/vessels?name=<boat>`, click the correct vessel, copy the 9-digit MMSI (format: 366xxxxxx for US-flagged), drop it in the `mmsi` field of the corresponding captain entry. Rebuild.
  - Priority order (most likely to have AIS): Canyon Runner (Point Pleasant NJ, big fleet), Fat Tuna Charters (Gloucester MA), Big Game Fishing RI, Reel Deal Fishing Charters (Truro MA), Rockfish Charters (Moriches NY).
  - Once MMSIs are in the file, the follow-on TODO ("Live AIS position for captains — needs aisstream signup") unlocks proper LIVE position markers instead of static home-port dots.

- [ ] **Live AIS position for tracked captains** — needs aisstream.io signup (free, 5 min at `https://aisstream.io/`). Once we have MMSIs in `data/captain_vessels.json` AND an aisstream API key, the `🎣 Captains` layer upgrades from static home-port markers to LIVE position tracking that refreshes every ~30 seconds via WebSocket. Each captain's marker will move on the map in real time as their boat pings AIS.

- [ ] **Sentinel-2 recreational-cluster AUTO-detector** (v23.32 shipped the visual overlay above; this is the ML-classification follow-on)
  - **Physics of the source:** Sentinel-2 optical imagery (10m/pixel, free via Copernicus L2A) reliably detects vessels 20-60m long. That's 65-200 ft. Randy's recreational target of 22-45 ft (7-14 m) is at or below the resolution floor for individual boat detection.
  - **What DOES work:** cluster-level detection. When 50-200 recreational boats stack up on a canyon lip they show as an unusual bright bloom in the 10m grid even when individual hulls can't be resolved. Standard oceanographic clutter (SST, chlorophyll, glint) needs to be masked out — small effort but real work.
  - **Free data path:** Copernicus Data Space Ecosystem `https://dataspace.copernicus.eu/` (same account as SEALEVEL_GLO_PHY_L4_NRT for altimetry — piggybacks on the Copernicus TODO above, one signup unlocks both). Sentinel-2 L2A tiles at 10m for the NE US arrive daily-ish depending on cloud cover.
  - **Model to reuse:** the University of Helsinki YOLOv8 model at `https://huggingface.co/mayrajeo/marine-vessel-detection-yolov8` — open source, MIT license, purpose-built for detecting small recreational vessels in Sentinel-2 imagery.
  - **Nightly build cost:** meaningful — need to pull 6-9 Sentinel-2 tiles across the NE canyon complex, mask land + clouds, run inference, aggregate clusters. Rough estimate: 3-5 min added to nightly build (currently ~90 sec).
  - **Delta over the visual overlay we shipped:** v23.32 shows Randy the raw imagery — he decides. The auto-detector would add colored ○ markers at every detected cluster centroid + a "N boats detected" count in the satellite chip tooltip. Turns manual eyeballing into a passive signal that could eventually feed `effectiveHeat()` as a `fleetClusterBoost` term (if a cluster of 20+ rec boats sits within 5nm of a zone, that IS the bite).
  - **Blocked on:** Randy signing up for Copernicus (already on Needs Randy list — one signup covers altimetry + salinity + Sentinel-2 L2A raw all at once). Once that's done, ~1 session to build the detector + a nightly overlay layer.
  - **Alternative if we want boats visible sooner:** PlanetScope (planet.com) has 3m/pixel daily imagery that would show every 22-ft+ boat cleanly. NOT free — cheapest commercial tier is roughly $150-300/month depending on area. Not worth it for personal use, worth revisiting only if fishfinders.app grows a paid tier.

---

## 🆕 Cowork / Claude capability upgrades

Standing register per Captain skill Rule 9 (Randy 2026-08-30/31: *"use the most current tools that Claude has ... implement them to help make our project as good as it can possibly be"*). Every session start, Captain scans available-skills + `<functions>` + `mcp__*` + `artifact-capabilities` for new stuff since the last session. Neutral-to-positive upgrades get adopted silently; anything user-visible or ops-changing gets flagged to Randy here before adoption.

**Currently-available capabilities Fish Finder isn't using yet, worth investigating:**

- [ ] **Artifact runtime capabilities (persistence, cross-viewer state, camera).** The `artifact-capabilities` skill exposes runtime features a published artifact can declare — persistent state per viewer, shared-across-viewers state, viewer identity, file uploads, "ask Claude a question of its own". Concrete Fish Finder wins:
  - "Log your catch" drawer that actually SAVES the catch to the artifact so Randy sees it in the archive next session (currently the drawer clones DOM, doesn't persist).
  - "Take a photo of my catch" via the camera capability → attach to the archived catch → Randy's phone → feeds the fishing-book project too.
  - Cross-viewer state so a friend Randy shares his map with sees his live pinned picks, not a stale snapshot.
- [ ] **Workflow tool (multi-agent orchestration).** The `workflow-authoring` skill exists. Fish Finder currently has one long linear `build-inlined.py` that takes ~5-10 minutes. Splitting the harvest step (YT + charter blogs + fisherman-mag + reddit + regional feeds) into a parallel workflow could cut nightly build time in half AND make failure isolation cleaner. Worth it once nightly build is regularly over 8 minutes.
- [ ] **Design canvas skill.** Multi-artboard visual designer for landing pages / marketing. Could produce a real fishfinders.app landing page instead of the current minimal one.
- [ ] **`ScheduleWakeup` / `Monitor` tools.** The session's ScheduleWakeup + Monitor tools support long-running "wake me when X happens" flows. Could power an "alert me when a captain report drops naming a top-picked zone" pipeline.
- [ ] **Chrome browser automation (`claude-in-chrome`).** Already have it available. Not being used. Could scrape captain socials (Instagram / Facebook fishing pages) that don't publish RSS but do post daily catches.
- [ ] **Remote-devices tools growth.** Watch the `mcp__remote-devices__*` toolset — new tools appear periodically. Currently using device_bash + device_list_dir + device_stage_files + device_commit_files. Any new tool (esp. one that lets me RUN a process on Randy's desktop instead of shipping files back-and-forth) simplifies ops.

**Trigger on new arrivals:** anything Anthropic ships that appears in a future session's system-reminder skill list, `<functions>` header, or MCP roster gets a line here PLUS a proactive suggestion in the session summary to Randy.

---

## Format rules

- Every open item is a checkbox `- [ ]`. Completed items get `- [x]` and stay in the file for one week as a receipt, then get moved to CHANGELOG.md.
- Newest additions go at the bottom of the section they belong in.
- Every item includes enough context (URLs, cost, dependencies, expected effort) that a fresh session can pick it up without background.
- When a session finishes work: (1) check off anything completed, (2) add newly-discovered leftovers, (3) add anything that required Randy to sign up for something.
