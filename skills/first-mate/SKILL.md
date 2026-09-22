---
name: first-mate
description: The First Mate — the persistent prediction engineer for Randy Spargo's Fish Finder project. Owns the model architecture, signal design, prediction accuracy tracking, and the "gets sharper over time" learning loop. Invoke the First Mate whenever working on model math, adding a new prediction signal, tuning signal weights, analyzing prediction accuracy against archive history, evaluating whether a proposed change makes picks better or worse, or researching external prediction methods (Hilton's, ROFFS, SatFish, oceanographic papers) to import into the model. Complements the Captain skill — where the Captain owns data quality + captain sources + boat rules, the First Mate owns the prediction *methodology*.
---

# The First Mate — Fish Finder Prediction Engineer

## Mission

Randy Spargo's Fish Finder is not a static reporting tool. It's a prediction model that **must get sharper every day it runs** (Randy's 2026-07-25 standing directive). The Captain owns data quality; the First Mate owns the *prediction methodology* — how the signals combine into a score, how accurate the predictions are over time, and how the model improves.

Every session the First Mate is invoked, the top-priority question is:

> **Are tomorrow's picks better than they would have been last month? What did we learn from the archive that should change the math?**

The First Mate never lowers the bar on data quality (the Captain guards that). Instead, the First Mate treats the archive as ground truth and asks: which signals are pulling their weight? Which are noise? What's missing?

## Daily Freshness Audit — MANDATORY at session start (Randy 2026-07-30)

The First Mate's failure mode Randy called out (2026-07-30): "I don't know why the First Mate didn't pick up that the whale information wasn't being updated because he's supposed to be using that in his prediction model."

He's right. A signal returning 0 because the source data went stale is exactly the pathology the First Mate exists to catch. Every session start, BEFORE any weight discussion / accuracy analysis / signal design work:

```bash
python3 /root/fish-finder/data_freshness_audit.py
```

The output categorizes every one of the 17 signals as fresh / stale / dead / unknown. **The First Mate cannot reason about model performance if signals are silently zero.** If `whale_sightings` is dead, `whaleBoost` isn't a "signal underperforming its weight" — it's a signal not firing at all. Different diagnosis, different fix.

**The First Mate's rule going forward:** if any signal is `stale` or `dead` in today's audit, note it explicitly in any pick explanation. "Today's Tomorrow's Pick has no whale boost — CRESLI feed hasn't posted in 4 days" is dramatically better than silently ranking a zone lower and pretending the model is functioning normally.

**Audit trail in archive:** every day's snapshot at `/root/fish-finder/archive/YYYY-MM-DD.json` now carries a `freshness_audit` block. When analyzing prediction accuracy, cross-reference which signals were `dead` on the days a prediction failed — often that's the explanation, not a weight-tuning problem.

**Live-heat freshness now audited per-zone (2026-07-31 pass 16.1).** As of this pass, `data_freshness_audit.py` inspects `zone.heat_updated` (added to all 42 zones) and reports:
- `zones_stale_10_21d` and `zones_dead_over_21d` counts
- `oldest_zone_age_days` and a `stalest_zones[]` array with name/days/heat for the top 10
- Overall `live_heat` status rolls up to WORST-of-any-zone

**This directly affects prediction interpretation.** If 30 of 42 zones have `heat_updated` > 21 days ago, the model isn't broken — it's data-input-starved. The First Mate flags this as "picks stable because underlying scores haven't moved" rather than "signal weights need tuning". `build-inlined.py` auto-bumps `heat_updated` from YouTube corroboration, but numeric `zone.heat` changes still require the Captain's two-independent-sources rule.

**Pick-card verbiage (2026-07-31 pass 16.1).** The pick card in the Report tab now shows per-zone freshness (score verified date + latest chatter date), a captain-intel panel with top 3 YouTube titles, and a pick-history strip with 5-day sequence + runners-up including species tags + gap-vs-winner. This is not a First-Mate-owned display, but the First Mate can reference "eh -1.8 vs #1" from the runners-up strip when explaining why a specific zone wins.

**Provenance chips available on every prediction surface (2026-07-31 pass 16.2).** Four shared JS helpers now sit next to `effectiveHeat()`: `zoneFreshnessPill(z)`, `zoneIntelChip(z)`, `zoneProvenanceChips(z)`, `zoneIntelSummary(z)`. Any prediction surface the First Mate designs (accuracy dashboards, signal-contribution tables, weight-tuning UI) must render `zoneProvenanceChips()` under each zone name so the underlying intel provenance is visible. This composes with the freshness audit — when explaining a prediction, the First Mate can now say "picked because live_heat 9 (Jul 30 verified, corroborated by 1 YouTube channel over 7 videos) beat runner-up eh by 1.8" and every piece of that claim is visible in the UI.

**The "= same" chip on the 7-Day Look-Ahead** is an important interpretability affordance for the First Mate. When >4 of 7 forecast days carry "= same", the model is telling us it has no signal to rotate picks — that's a symptom of data-input starvation (Captain job), not a symptom of weight mis-tuning (First Mate job). When the "= same" chips DISAPPEAR from days 3+ after Captain intel refreshes, that's evidence the fresh signal actually moved the ranking — data useful for measuring which signals are pulling their weight.

## The Model Formula (current, 2026-09-06 — 17 signals)

Every zone gets an **effectiveHeat** score used for ranking. The displayed 1-10 heat badge stays untouched (that's live catch heat, guarded by the Captain). Effective heat is:

```
effectiveHeat(zone) = zone.heat * heatConfidence(zone)  // age-discount captain heat: 1.0 fresh, 0.5 aging, 0.15 stale, 0 dead
                    + whaleBoost(zone)                  // +0.8 if whale/dolphin sighting ≤ 7 days
                    + seasonalBoost(zone)               // +0.7 peak → -1.5 out of season
                    + baitBoost(zone)                   // +0.3 if bait intel ≤ 10 days
                    + trendBoost(zone)                  // +0.4 if heat ≥ 7 for 3+ days
                    + birdBoost(zone)                   // +0.4 if bird intel ≤ 5 days (currently placeholder)
                    + sstFit(zone)                      // +0.6 ideal / +0.1 tolerable / -0.4 out
                    + goldenZoneBoost(zone)             // +0.9/+0.6/+0.3 within 5/12/20 nm of SST×chla crossing
                    + sstGradientBoost(zone)            // up to +0.4 near a hard SST break (≥0.4°F/nm)
                    + chlaGradientBoost(zone)           // up to +0.4 near a hard chlorophyll edge
                    + convergenceBoost(zone)            // up to +0.4 near a current convergence point
                    + persistenceBoost(zone)            // +0.3 if an SST break has held ≥ 3 days near this zone
                    + postFrontPenalty(zone)            // -0.5 post-front / +0.3 falling-fast / 0 steady
                    + youtubeCorroborationBoost(zone)   // +0.4 if 2+ NE YT channels mentioned this zone in 14d, +0.2 if 1
                    + captainDwellBoost(zone)           // +0.5 if 3+ vessels dwelled ≥30min, +0.3 if 1-2 (needs aisstream)
                    + distancePenalty(zone)             // 0 to -0.3 past 60% of range
                    + diversityBoost(zone)              // +0.3 if 3+ distinct sources in 14 days
```

Seventeen signals now. Each with a specific job. The First Mate's job is to know why each one is weighted the way it is and to change those weights when the archive shows they're wrong.

**v24.106 (2026-09-05/06) — server-side effective_heat parity CLOSED at 17/17.** Previously `archive.compute_zone_state()` computed only 3 signals (heat + whaleBoost + seasonBoost) — meaning every accuracy metric the First Mate could produce was measuring a stripped-down model, not what Randy saw on the map. Across 4 passes this session:
- Pass 1: added heatConfidence, sstFit, baitBoost, birdBoost, diversityBoost, distancePenalty (3→9 signals)
- Pass 2: threaded `derived_signals` + `captain_dwells` through save_snapshot; added sstGradientBoost, chlaGradientBoost, convergenceBoost, persistenceBoost, postFrontPenalty, youtubeCorroborationBoost, captainDwellBoost (9→16)
- Pass 3: added trendBoost using archive-history lookup for consecutive-day count (16→17 minus goldenZone)
- Pass 4: ported findAllCrossings line-segment intersection algorithm; added goldenZoneBoost using SST × chla contour crossings (17/17 CLOSED)

The archive's effective_heat now faithfully reflects what Randy sees on the map. **This is a prerequisite for the weight-tuning approach below — you can't tune weights against a measurement that's measuring the wrong model.**

### Signal-by-signal rationale

- **zone.heat × heatConfidence** — the strongest signal, age-discounted (Randy 2026-08-02: "we shouldn't give them a prediction model based on something 20 days ago"). Confidence 1.0 for heat_updated ≤ 10 days, 0.5 for 11-21 days, 0.15 for 22-45 days, 0 for older. Only discounts captain-driven heat; environmental signals still count in full.
- **zone.heat (live)** — direct captain observation. Zone can go from 3 to 9 in a week if reports light up. Never boosted above 10, never below 1.
- **whaleBoost** — Randy's rule: "find the whales, find the tuna." CRESLI/Viking Fleet whale-watch reports. +0.8 is aggressive because whale sightings correlate with dense bait piles. Watch: does the archive show whale-boosted zones consistently outperform on days 2-5 after the sighting? If yes, keep or raise.
- **seasonalBoost** — historical migration prior. -1.5 for out-of-season is heavy on purpose (a September striper zone should not be picked in January no matter how many other signals hit).
- **baitBoost** — direct bait intel. Small +0.3 because bait intel is often lagged relative to captain reports (already reflected in live heat).
- **trendBoost** — persistence. A zone that stays hot 3+ days is more predictable than a one-day spike.
- **birdBoost** — bird activity as a bait proxy. Small +0.4. Bird intel is currently sparse; expand this as sources come in.
- **sstFit** — species-specific temperature preference. Uses SPECIES_TEMP_PREF table. A zone with SST outside all active species' tolerable ranges gets penalized.
- **goldenZoneBoost** (added 2026-07-28) — proximity to an SST×chlorophyll edge crossing. Distance-scaled (+0.9 within 5nm, +0.6 within 12nm, +0.3 within 20nm). This is the "money spot" signal — where the tuna sweet-spot temperature crosses the plankton frontal boundary.
- **sstGradientBoost** (added 2026-07-29) — proximity to a HARD SST break, i.e. a point where the temperature gradient magnitude is ≥ 0.4°F/nm. Scaled by both magnitude (harder = more) and proximity (closer = more) out to 40nm to catch Gulf-Stream fronts influencing the outer canyons. Complements the SST break-line contours (which show WHERE the 68/72°F lines are); this signal weights WHICH lines are sharp enough to concentrate bait.
- **chlaGradientBoost** (added 2026-07-29) — same idea for chlorophyll. Tight blue-green transitions concentrate bait more than gradual ones. Scaled by magnitude (mg/m³ per nm) and proximity.
- **convergenceBoost** (added 2026-07-29) — where two currents on the arrow grid point at each other, bait can't swim against and piles up on the interface. Points computed pairwise from the CURRENTS_SNAPSHOT arrows at build time.
- **persistenceBoost** (added 2026-07-29) — a break in the same location for 3+ consecutive days concentrates more bait than a fresh one. Computed by scanning the last 3 archive snapshots' SST contours and flagging today's contours that overlap the prior three within 10nm.
- **postFrontPenalty** (added 2026-07-29) — global (not per-zone) modifier from 24h pressure trend. -0.5 in "post-front" state (pressure rise ≥ 6 hPa in 24h), +0.3 in "falling-fast" state (pressure drop ≥ 4 hPa in 24h — the classic pre-front feed signal), 0 in "steady."
- **youtubeCorroborationBoost** (added 2026-07-29 — Path B ship-now-tune-later) — reads `DERIVED_SIGNALS.youtube_intel.zone_mentions[zone.id]` (pre-computed at build time by `youtube_harvest.py` from 15 NE fishing channels). Returns +0.4 if 2+ different channels mentioned any of this zone's location aliases in the last 14 days (corroborated per the Captain's two-source rule), +0.2 if a single channel mentioned it, 0 otherwise. The location→zone mapping lives in `data/youtube_zone_map.json` — hand-curated to expand fuzzy locations like "Montauk" onto the ~4 nearby zones. **Ship-now-tune-later rationale**: Randy's mission is "gets sharper every day" and we already have live corroboration on real zones. Rather than sit on the data for 30-60 days waiting to validate a weight, we ship +0.2/+0.4 (small enough to not override the other 15 signals when they disagree) and USE the archive to tune. Once 30+ days of divergent data accumulates, run this correlation: for each historical day, did the top pick's YT boost predict a subsequent captain-report improvement in that zone's live heat? If corroborated zones outperform uncorroborated ones, raise the weights; if the reverse, lower or remove.
- **captainDwellBoost** (added 2026-08-12) — "the holy grail" per Randy: when a tracked charter captain STAYS at a spot for ≥30 minutes, they're fishing → that spot IS the bite. Reads pre-computed per-zone dwell scores from `dwell_analysis.py` (via `data/captain_dwells.json`). +0.5 if ≥3 distinct captain vessels dwelled ≤72h, +0.3 if 1-2 vessels. Currently returns 0 until Randy's aisstream signup lands. Weight conservative to start; retune after 30+ days of dwells-vs-no-dwells picks.
- **distancePenalty** — soft economic penalty for burning fuel. Kicks in past 60% of Randy's range (48 nm from Old Saybrook).
- **diversityBoost** — trust signal. A zone confirmed by 3+ independent sources in 14 days is less likely to be a single-captain fluke.

## Prediction accuracy — the ground-truth loop

The archive at `/root/fish-finder/archive/YYYY-MM-DD.json` is the First Mate's most important artifact. It is append-only and never overwritten. Every nightly build writes:

- Today's `zones_state` (heat, all 17 boost components, effective_heat per zone)
- Today's `picks` (top tuna + striper picks with model math, PLUS `picks_by_species` for all 42 species we track)
- Today's `picks.runners_up` (v24.106: top-5 per pick with gap_to_winner — enables runner-up accuracy scoring + counterfactual weight-change validation)
- Today's `look_ahead` (shipped 2026-07-29) — 7-entry array of daily predictions (offshore + inshore per day) with per-slot morning + afternoon weather for each picked zone.
- Whale/bait/weather/pressure/moon context
- SST + chla contours (raw polylines) so goldenZoneBoost can recompute historically

**The First Mate's analysis toolkit** (v24.106): `python3 /root/fish-finder/first_mate_analysis.py [--region <name>] [--days N] [--json]`. Reads the archive and surfaces:
- Runner-up gap distribution (how tight was the pick? locked ≥1.5, tight <0.3, moderate in between)
- Signal starvation streaks (top-1 and top-2 tied ≥5 days → the model has no signal separating them)
- Latest runners-up snapshot per region + species
- Server/client parity warning (now historical since 17/17 parity closed)

Companion script: `scripts/backfill_runners_up.py` — one-shot backfill of runners_up into pre-v24.106 snapshots by ranking each snapshot's existing zones_state.effective_heat. Idempotent, safe to re-run.

**How to score accuracy** (once the archive has ≥ 14 days with look_ahead):

For each historical date D that has a look_ahead prediction:
1. Read the prediction made on date `D - Δ` for date `D` (where Δ ∈ {1, 3, 7})
2. Read the ACTUAL top pick on date `D` (from that day's `picks`)
3. Score: exact match = 1.0; same category (offshore/inshore) = 0.5; different = 0.0
4. Aggregate over the last 30 days: `accuracy_Δ = mean(score)`

A healthy model shows `accuracy_1 > accuracy_3 > accuracy_7` (nearer = easier). Watch for:
- **accuracy_1 < 0.6** — the model is unstable day-to-day. Something is over-weighted.
- **accuracy_1 ≈ accuracy_7** — the model isn't using the day-to-day forecast well; per-zone weather isn't affecting picks. Bug.
- **accuracy_7 > accuracy_3** — either coincidence or the 3-day pick is unlucky. Investigate.

## Weight tuning approach (when archive is ripe)

Do NOT hand-tune weights based on gut. Do NOT change weights based on any single day's result. Wait for at least **30 days of archive with look_ahead data**, then run this analysis:

1. Isolate each signal's contribution: for each day's top pick, log which boost signals were "in play" (non-zero).
2. Score whether the pick came true (see accuracy above).
3. For each signal, compute: `hit_rate_when_signal_active` vs `hit_rate_baseline`.
4. Signals with `hit_rate_when_active > baseline + 0.10` should get MORE weight (roughly proportional to the delta).
5. Signals with `hit_rate_when_active < baseline` should get LESS weight, or be removed.
6. Never change more than ONE weight per week — otherwise you can't attribute the change to any specific tweak.
7. After a weight change, run backfill: recompute prior-30-day picks with the new weights and confirm they don't degrade past picks.

Store weight changes in a version log at `/root/fish-finder/model_weights.json` with `date_changed`, `signal`, `old_weight`, `new_weight`, `rationale`, `expected_hit_rate_delta`.

## Landing picks are unconstrained now (v23 — Randy 2026-08-08)

The v23 UX rewrite made the landing MAP-first and switched the landing pick strip + 7-day outlook to **unconstrained** picks by default (no boat-range filter). Randy: "I don't want it to limit the picks by the boat."

**What this means for the accuracy loop:**

- The `picks.tuna` / `picks.striper` top-level archive keys have been effectively equivalent to `picks.tuna_unconstrained` / `picks.striper_unconstrained` since 2026-08-08 on the landing surface. `pickForDate()` in the client (map/fish-finder.src.html) never applied a boat filter (July 29 rule already handled that); the change here is that the LANDING now surfaces those picks by default instead of gating them behind Personalize.
- The archive still stores both `_in_range` and `_unconstrained` picks per snapshot. Your accuracy analysis code should not need to change — but the "did the model pick right?" question is now answered against unconstrained picks by default, not in-range.
- The `picks_by_species` archive field still carries in_range + unconstrained per species. Per-species accuracy analysis is unaffected.

**What this DOES NOT change:**
- `effectiveHeat()` formula and its 17 signals — the ranking math is untouched. Only what SURFACE we show at the top changed.
- Signal weights — no tuning happened as part of v23.
- The 30-day archive requirement before weight tuning — still holds.

**What to watch for once accuracy data accumulates:**
- Because Randy now sees unconstrained picks first, "did I go there?" ground-truth reports will more often be for zones he COULDN'T reach that day. The First Mate needs to be careful in analysis: a zone he didn't visit isn't a failed prediction if it was out of range — it's an unfished datapoint. Track visited-vs-unvisited separately in the ground-truth loop.
- The 7-day freshness lens on the pick strip means the "fresh" tier surface is smaller. If the strip falls back to the aging tier (with "· best is X days old"), that's a signal to the Captain that captain-intel harvest coverage is thin, not a model failure. Do not tune weights based on picks made from the aging tier — flag them for exclusion in the accuracy loop.

## Accumulate Over Prune (Randy 2026-08-08)

**Randy's directive (verbatim):** "A big part of its value is the data we're gonna collect... we just wanna accumulate as much relevant data about fish intel as we possibly can because we may find better ways to manipulate it later."

For the First Mate this is a first-principles rule that constrains every future weight-tuning, signal-adding, and signal-removing decision:

1. **The raw archive is the ground truth, not just the filtered archive.** Since 2026-08-08 every snapshot at `/root/fish-finder/archive/YYYY-MM-DD.json` carries a `raw_intel` block with the FULL harvested corpus — every YouTube RSS entry in the 14-day cutoff window (matched or not), every Viking Fleet whale-watch card (species-matched or not), plus fetch metadata. When running the accuracy loop or a signal-discovery experiment, read `raw_intel` — not just the current filtered signals — so tomorrow's model isn't limited to what today's model happened to find useful.

2. **Never propose a "cleanup" that drops raw archive fields.** If a snapshot has data the current model doesn't use, that data is not waste — it's future-model fuel. Any First Mate change to `archive.py` or `save_snapshot()` must preserve every field a prior snapshot carried. There's a guardrail in `save_snapshot()` (2026-08-08) that keeps `raw_intel` from a prior write if the current write omits one; do not defeat it.

3. **Prefer new signals over tighter regex on old ones.** Given a choice, spend effort adding a new source (Copernicus altimetry, WHOI acoustic buoys, eBird pelagic) rather than refining our YouTube location patterns from 30 hits to 32. Coverage breadth beats extraction precision when the raw text is preserved — you can re-run a better regex six months from now; you can't retroactively fetch a video that got deleted.

4. **When testing a new signal, back-test against the raw archive first.** Before adding a new term to `effectiveHeat()`, use the raw archive to simulate: "if this signal had been active for the last N days, would picks with it firing have outperformed picks without?" This is only possible because we preserved the raw source. Without `raw_intel`, all new-signal proposals become theoretical.

5. **The season shapes the accumulation budget.** Northeast fishing is seasonal: late June → November prime, December → April dormant. In prime season every day of archive is valuable because it's a real snapshot of real activity. In the off-season the archive is still valuable — an empty window is the baseline that next season's activity gets measured against. Never skip archiving on an off-season day.

6. **The Captain owns pipeline preservation; the First Mate owns pattern preservation.** When we notice something interesting mid-run ("bluefin ran three days early this July") it goes into `/root/fish-finder/patterns.md` (a First-Mate-owned journal), not just a passing comment in chat. That file is retrieval memory for future weight-tuning sessions the same way `raw_intel` is retrieval memory for future signal experiments.

**Consequence for signal weights:** the "minimum 30 days of archive before tuning" rule (see Weight tuning approach above) now compounds — the earlier the archive got the raw preservation upgrade, the earlier the first tuning pass can leverage novel-signal experiments. The 2026-08-08 upgrade date is the effective earliest that any raw-corpus retro-analysis becomes possible. Track that date; do not run retro experiments that require raw data from before it.

## Bait & Whale Movement Tracking (Randy 2026-08-09 — First Mate owns this)

Randy's directive (verbatim): "I want you to do some historical tracking. Track where the bait goes and how it moves each day. Same thing with the whales and dolphins. And so we really want all the info we can get about tracking this stuff. You just keep all that information in the background till we need it. Eventually we're gonna add a tab that shows where the bait has gone over time."

**This is the First Mate's job — not the Captain's.** The Captain preserves the raw entries; the First Mate reads them back and computes the patterns. Randy explicitly asked which mate owns it; the answer is First Mate because tracking movement over time is a prediction-methodology question (bait movement precedes fish concentration by hours-to-days, and modeling that lag is a future weight-tuning target).

**How the pipeline works as of v23.13 (2026-08-09):**

1. **Preservation (Captain, `archive.py`)**: every daily snapshot now carries `bait_intel_full` and `whale_sightings_full` — the FULL arrays as of that build, not just counts or the last-7-day compaction. Storage cost ≈ 5-10 KB per array per day; over a 150-day season ≈ 1.5-3 MB total. Guardrail in `save_snapshot()` never demotes a snapshot that had these arrays to one that doesn't.

2. **Analysis (First Mate, `bait_whale_history.py`)**: read-only module over the archive. Public functions:
   - `load_daily_state_series(days=30)` — oldest→newest list of `{date, bait_entries, whale_entries, has_full_arrays}`
   - `per_bait_timeline(series)` — for each bait species, `{date, zones, zone_count, entry_count, source_types}` chronologically
   - `per_whale_timeline(series)` — same shape for whale species
   - `per_zone_bait_timeline(series)` / `per_zone_whale_timeline(series)` — flip the axis: "when has zone X had bait/whales?"
   - `detect_movements(series, kind="bait"|"whale")` — day-over-day diffs: which zones newly appear in a species' footprint ("spread"), which zones fall out ("retreat"), which persist ("stable")
   - `tracking_summary(days=30)` — one-shot rollup for future UI or nightly report

3. **No UI yet.** Randy's ask was "keep it in the background till we need it." The module is ready to feed a future "Bait & Whale Over Time" tab / drawer whenever Randy asks for one. UI would be a straightforward render of the timelines + movements — the analysis is already done.

**Interpretation rules the First Mate follows:**

- **Movement detection needs 2 consecutive snapshots with full arrays.** Pre-v23.13 snapshots don't have `bait_intel_full`; those days show as "unknown_prior" and no false movement is emitted. **v23.14 (2026-08-09) backfill:** `backfill_bait_whale.py` injects `bait_intel_full` and `whale_sightings_full` into the last N snapshots by filtering the current arrays to entries dated ≤ each snapshot's date (no future intel bleed). Randy: "How about if we went back seven days to update all these things we just redid?" — that's the backfill run. Idempotent (safe to re-run), stamps `backfilled_at` on touched snapshots for audit, never demotes fuller prior arrays. **Effective earliest movement analysis is now 2026-08-03** (7 snapshots ago) instead of waiting for two fresh nightly builds.
- **`source_type` matters for weight-tuning.** YouTube-auto entries are broader-tagged (a video that says "bunker" + mentions 5 locations tags all 5). When using movement data to tune model weights, split by source: first-party captain reports first, YouTube-auto as corroboration second. The `per_bait_timeline` / `per_whale_timeline` output includes `source_types` per day so this split is trivial.
- **7-day freshness lens** matches the landing surface (Randy's rule). Movement analysis at 30-day and 90-day windows uses the same lens per snapshot — a bait entry counts toward its snapshot's day if it was ≤7d old on that day. This keeps movement patterns honest against what the model actually acted on.
- **Never claim movements the source data doesn't support.** If a bait key appears in day N with 5 zones and day N-1 with 5 different zones, that's a genuine spread OF ONE VIDEO'S ZONE-ATTRIBUTION change — flag it as "high-uncertainty" if both entries were `youtube_auto` from the same channel + same key. The Captain skill has downgrade guidance.

**Future weight-tuning targets** (once ≥30 days of movement data accumulate):
- Do bait movements correlate with subsequent live_heat lift in the arrived-at zone 1-3 days later? If yes, the current `baitBoost = +0.3` should be raised, or a new `baitMovementBoost` added (fire when bait spread INTO this zone in the last 3 days, not just present).
- Do whale movements outperform static whale_sightings as a leading indicator? Same test.
- Per-species movement patterns (bunker vs sand_eels vs squid) — do certain baits precede tuna better than others? Split the `baitBoost` by bait key if the archive shows it.

**When Randy asks "where has the bait been this week?"** the answer comes from:
```bash
python3 /root/fish-finder/bait_whale_history.py --days 14
```
Or `--json` for programmatic consumption, or `--bait bunker` / `--whale humpback` to narrow.

## Popup Reasoning — "Why this zone" translator (Randy 2026-08-09 — First Mate owns the translation)

Randy: "When I click on a top pick, I would like you to have a blurb about what's going on there. What's the most current intel? People aren't gonna spend a lot of time on it."

The zone popup now leads with a plain-English blurb that translates the model math into captain language. **The blurb generator (`_sentences` in `renderPopup`) is First-Mate methodology** because it's the interpretation layer between the raw signals and the user. When a signal is added, removed, or reweighted, the blurb's sentence generation MUST update to match — otherwise the popup will describe a version of the model that no longer exists.

**Blurb generation algorithm (v23.20+):**

- **Sentence 1 (freshest evidence, always tries to fire).** Highest-recency of (a) bait_intel entry tagged to zone (≤10d), (b) whale_sightings entry tagged to zone (≤10d), (c) YouTube video mentioning the zone (≤7d). Translated into "captain heard/saw" language, not "signal fired at strength 0.3." Example: `"Fresh bunker reported here 3d ago — Dave Anderson's Aug 6 report."`
- **Sentence 2 (strongest environmental boost as intuition).** Ranked by the CONTRIBUTION to effectiveHeat (not the raw signal strength), from: goldenZoneBoost, sstGradientBoost, chlaGradientBoost, convergenceBoost, persistenceBoost, sshaBoost. Translated into biological/oceanographic English. Example: `"The 72°F SST break crosses a chlorophyll edge right here — the classic 'money spot'."`
- **Sentence 3 (space permitting, corroboration + season).** Multi-channel YT corroboration OR peak-season prior OR out-of-season warning. `"3 different Northeast captain channels have talked about this area in the last 14 days."`
- **Zero-signals fallback.** `"Ranking rests on live catch heat (7/10) with no active environmental or intel boosts today. Live heat was verified in the last 10 days."`

**Below the blurb, in order:** rank + effective heat one-liner → latest evidence rows (dated quotes with ↗ links, capped at 4, ≤14d only) → `<details>Show the model math</details>` (boost chips + arithmetic) → `<details>Older intel + full notes</details>` (YouTube/whale/bait/notes).

**Popup sizing (v23.22 — Randy 2026-08-09):** `maxHeight: 320` on the popup binding forces Leaflet to wrap over-limit content in `.leaflet-popup-scrolled`. `autoPan: true, autoPanPadding: [24, 24]` pans the map so the popup fits inside the viewport. `map.on("popupopen")` zeros scrollTop on every candidate container so the blurb is ALWAYS the first thing on screen. Any future popup redesign must preserve these three invariants or the blurb will disappear off-screen again.

**When adding a new signal:** the blurb generator's Sentence 2 ranker (in `renderPopup`'s `_sentences` block) needs a new case. If it's not added, the signal fires in the math but never in the English — the popup will lie by omission. This is a hard rule Randy has: the popup must never lag the model.

## Bait / Lure / Bite-Window Extraction (Randy 2026-09-11 — First Mate owns this)

Randy's directive: "When we go out fishing for tuna in a certain spot, maybe we're gonna have a portion that recommends what bait they're using to catch them, what lures they're using to catch the fish at each specific spot. Have the First Mate look at that data and organize it."

**Shipped v24.114-v24.118.** Reads the transcript cache the device-side catch-up populates from Randy's residential IP.

- **v24.117** — vocab expansion. Split `pencil poppers` out of `poppers` (distinct rig; negative lookbehind stops double-counting). Added `hopkins spoons` as new `spoons` category (Hopkins hammered stainless + Krocodile). Fixed `AA jig`→`Ava jig` typo. Broadened bunker family (menhaden/pogies/freelining synonyms; verb-first "chunking menhaden" order). Expanded USAGE_CONTEXT to catch past-tense verbs (threw, ripped up on, freelining) and post-strike language (crushed/smashed/hammered/slammed them). Category total: 38 canonical terms.
- **v24.118** — captain quote strip in popup. `zoneBaitLureIntel()` now renders a `🎙️ What captains are actually saying` section under the WHAT + WHEN blocks. Merges candidates from both bait/lure and bite-window rollups, dedupes by `channel|published|first-80-chars`, sorts newest first, caps at 2. Each quote card: italic snippet, cyan left border, monospace attribution `🎙️ channel · date · ▶ watch` linking to `youtube.com/watch?v=<video_id>` when present. Fixes the mobile-invisibility bug where the same info lived only in `title=` tooltips that phones don't fire.
- **v24.119** — pick card signal transparency drift caught. `pickCardHTML()` was only showing 8 of 15 signals — chla_gradient_boost, post_front_penalty, goldenZoneBoost, sstGradientBoost, convergenceBoost, persistenceBoost, captainDwellBoost were all being COMPUTED into effective_heat but silently dropped from the visible math. Refactored to the same `_pushPickBoost(label, val, posColor, negColor, extra)` helper the popup uses; every signal that fires (|v| > 0.005) now shows. Chip labeled `🌊 in-season` (not `season`) to distinguish from the adjacent 7/10 seasonal-fit rating. YouTube keeps custom `(Nch/Mv)` suffix via `extra` arg. On the Sept 9 tuna pick this closed a 0.86 arithmetic gap that had been invisible to Randy.
- **v24.120** — signal firing rates baked into every nightly `freshness_audit`. `data_freshness_audit.py` now rolls up per-signal firing rates from the snapshot's `zones_state` and emits `signal_firing_rates.<key>` blocks with `fires`, `of_zones`, `fire_rate`, `mean_when_active`, `max`, and `status` (active/rare/dormant). Plus `dormant_signals` — sorted list for `grep`-based retrospective analysis. The infrastructure a First Mate weight-tuning session needs: to see how often each signal was earning its weight across the archive. Not surfaced on the UI yet — this is retrospective data for the human First Mate operator.
- **v24.122** — `persistenceBoost` un-dormanted. Was silently 0/49 for the entire archive (28 snapshots, 2+ months) because `compute_persistence_flags` required files for exactly D-1, D-2, D-3 and Randy's archive has multi-day gaps. Rewrote the scan to take the most recent `window_days` snapshots within a `window_days × 2` lookback window — gaps tolerated as long as enough prior distinct days exist. Also widened client + server proximity 12 → 15 nm for consistency with sstGradientBoost / sshaBoost. On today's data the signal now finds 2 persistent breaks at (40.6, -70.4) and (40.3, -68.1), though no curated zone is within 15 nm of either point today — value ships forward as breaks form nearer to canyons on future days.

### Weight-change log (First Mate rule: version-log every weight change)
- **2026-09-11 · v24.122 · persistenceBoost proximity 12 → 15 nm.** Rationale: match sstGradientBoost/sshaBoost proximity cutoff (15 nm was the docstring-documented standard for "close-enough-for-oceanographic-feature-to-matter" but persistenceBoost inherited 12 nm without rationale). Expected hit-rate delta: modest widening from 12 → 15 nm ≈ 56% larger circle area, so roughly 1.5× more zones per persistent break can qualify. Not a numeric-weight change — same +0.3 boost, wider trigger radius.
 Produces `data/bait_lure_intel.json`, embedded at build time as JS global `BAIT_LURE_INTEL`, rendered under Intel Freshness in every zone popup.

**Pipeline (`bait_lure_extractor.py`):**

1. **Bait/lure vocabulary** — 40+ canonical terms grouped into 12 categories: live bait, chunk bait, cut bait, rigged bait, jigs, topwater, plugs, soft plastics, flies, trolling gear, techniques. Regex-based extraction; multiple synonyms per canonical term (e.g., "live bunker" catches "live pogies", "freelining bunker", "live-lining bunker").
2. **Usage-context filter** — a raw bait mention is NOT intel. "We saw bunker" ≠ "we caught them on bunker". Requires a usage verb (using/on/with/pulled/throwing/casting/trolling/chunking/etc.) in an 80-char window BEFORE the bait mention.
3. **Location attribution** — for each usage-verified bait mention, find the nearest zone-mapping location within 250 chars, preferring location BEFORE bait (captain names spot, then says what worked). Zone attribution goes through `youtube_zone_map.json`.
4. **Species tagging (v24.115)** — scans a 200-char window around each bait mention for target-species vocabulary (bluefin, yellowfin, bigeye, striper, bluefish, albie, mahi, swordfish, marlin, mako, thresher, wahoo, sailfish, fluke, black_sea_bass, tautog, cod, kingfish, spanish_mackerel + generic tuna). Collapses generic 'tuna' when a specific tuna species is also matched.
5. **Bite-window tagging (v24.116)** — same pattern, separate vocabulary: 11 canonical windows (dawn, morning bite, midday, afternoon bite, evening/dusk, night bite, incoming tide, outgoing tide, slack water, new moon, full moon). Broader 350-char attribution because timing context is sentence-level.

**Per-zone rollup produced:**
- `top_categories` — up to 3, by mention count
- `top_items` — up to 5 canonical bait/lures with mention counts, channel counts (for corroboration), species tags, best example snippet
- `by_species` — same rollup, but sliced per target species (for the popup's activeSpecies filter)
- `top_bite_windows` — up to 4 canonical windows with same shape

**Popup rendering (`zoneBaitLureIntel(z)` in fish-finder.src.html):**

- Reads `BAIT_LURE_INTEL.by_zone[z.id]`. Silent when nothing → never fabricates.
- **Species filter** — when `activeSpecies` is non-empty, filters bait/lure items whose `species` list overlaps active. Falls back to unfiltered list if filter would be empty. Species-agnostic items always show.
- **Corroboration cap** — items with 2+ mentions filter first; below that, shows all top items.
- **Star (★)** marks items with 2+ different channels reporting.
- **Hover tooltip on rows** = actual captain quote snippet + channel + date. Progressive disclosure — the popup stays scannable, deep info is one hover away.
- **Bite windows render as a second block** under bait/lure, with emoji per window type (🌅🌊↑🌕🌑) for one-glance scanning.
- **Captain quote strip (v24.118)** = third block under WHAT + WHEN. Two most-recent, dedup'd quotes with cyan-bordered cards, channel + date + ▶ YouTube link. This is the phone-visible version of the hover tooltip content.

**Design constraints (per Captain rule 4 — one-glance readability):**
- Cap at 3-4 items per section
- Sub-list rows are one-line, monospace metrics on the right
- Zero fabrication — silent on zones with no data
- 200-char snippet cap so hovers never overflow the map

**NOT a ranking signal (yet).** DISPLAY-only until we have 30+ days of ground-truth catches to correlate bait-lure→outcome. When that data exists, First Mate can consider promoting strongest correlations to a real signal in `effectiveHeat()`. Weight-tuning discipline applies (First Mate rule: never tune on gut).

**Data preservation (Accumulate-Over-Prune):**
- Every raw mention is stored in `data/bait_lure_intel.json` with source citation (video_id + channel + date). Even mentions that don't currently make the popup are archived.
- Rebuilt every nightly from the current transcript cache — no separate historical journal needed; the transcripts are the ground truth.

**Dependencies:**
- `cache/yt_transcripts/*.json` populated by the device-side catch-up trigger (`trig_01BSgpZ6s...`) daily
- Cloud nightly's `build-inlined.py` runs the extractor after the YouTube harvest and substitutes `BAIT_LURE_INTEL_PLACEHOLDER` in the map template

**What to update when adding vocabulary:**
- Bait/lure terms → `BAIT_TERMS` dict in `bait_lure_extractor.py` + `CATEGORY` map entry for it
- New CATEGORY value (e.g. adding "spoons" for Hopkins/Krocodile) → also add its emoji to the `catEmoji` map inside `zoneBaitLureIntel()` in `fish-finder.src.html`, otherwise items fall through to the "•" other bucket
- Species → `SPECIES_PATTERNS` list
- Bite windows → `BITE_WINDOW_PATTERNS` dict + emoji in `winEmoji` map inside `zoneBaitLureIntel()`
- USAGE_CONTEXT verbs — if a captain phrase like "ripped up on X" or "slammed them" isn't being caught, add the verb form to `USAGE_CONTEXT` in the extractor. Past tense (threw, ripped, crushed) requires its own entry — the regex is exact-form, not a stemmer.

## The signal backlog (research-informed)

External services (Hilton's Realtime-Navigator, ROFFS Oceanographic Services, SatFish, In The Spread) layer 3-4 additional signals we don't yet have. In priority order:

### 1. Altimetry / Sea Surface Height Anomalies (SSHA) — HIGH VALUE

Sea surface height variations reveal warm-core and cold-core rings pinched off from the Gulf Stream. Warm-core rings are elevated (positive SSHA) and TRAP warmer surface water while pulling nutrient-rich deep water toward their edges — where bait AND tuna congregate. Cold-core eddies are depressed (negative SSHA), indicating upwelling that fuels the whole food chain.

**Where the pros look:** eddy EDGES, not centers. A rotating boundary between warm-core and surrounding water is the classic tuna target. When altimetry lines up with an SST break AND elevated chlorophyll, "you've identified a high-confidence target."

**Data source:** Copernicus Marine Service SEALEVEL_GLO_PHY_L4_NRT (free with registration) via `resources.marine.copernicus.eu`, or NOAA's `www.star.nesdis.noaa.gov/socd/lsa/AltimetryNRT/` for near-real-time altimetry.

**Signal to add:** `sshaBoost(zone)` — for each zone, sample SSHA at coords. If within 15 nm of a strong gradient (contour boundary of SSHA ≥ 15 cm), add +0.5. If within 15 nm of a warm-core ring edge (positive anomaly ≥ 20 cm), add +0.7.

### 2. Bathymetric edges (30-fathom line, canyon rim, wrecks) — MEDIUM VALUE

Bottom structure concentrates bait predictably. The 30-fathom line, canyon walls, and specific wrecks are known tuna magnets because current pushes bait against them.

**What we already have:** the zone list is CURATED to include these features (Tuna Ridge = 30-fathom S of Block; Habs Ledge; Hudson/Block/Atlantis canyons). So this signal is *implicitly* in the model via zone selection.

**What could be added:** a proximity boost for user-added spots that happen to sit on a known structure. Requires bathymetric contour data (GEBCO, NOAA NCEI). Lower priority because Randy is unlikely to add spots that aren't already zones.

### 3. Temperature-gradient magnitude (hard vs soft breaks) — MEDIUM VALUE

Not all SST breaks are equal. A "hard break" (3°F change in 5 nm) concentrates bait far better than a "soft break" (1°F change in 20 nm). Pros track this.

**Signal to add:** `sstGradientBoost(zone)` — for each 68°F or 72°F break line within 15 nm of the zone, compute the SST gradient magnitude across a 5-nm buffer. Bonus scales with gradient: ≥ 2°F/nm = +0.4; 1-2°F/nm = +0.2; <1°F/nm = 0.

### 4. Chlorophyll-gradient magnitude — MEDIUM VALUE

Same idea as SST gradient, applied to chlorophyll edges. A tight blue-green edge is more productive than a gradual color shift.

### 5. Feature persistence (stability across days) — MEDIUM VALUE

A break that's been in the same location for 3+ days is more concentrated than one that just formed. ROFFS explicitly tracks "number of consecutive days" for their targets.

**Signal to add:** `persistenceBoost(zone)` — check if the SST-break contours from the last 3 archive snapshots pass within 10 nm of the same location as today's contours. If yes for 3+ days, +0.3.

### 6. Post-front pressure detection — LOW-MEDIUM VALUE

We have barometric pressure trend but not "post-front" detection. After a cold front passes (pressure rises sharply), the bite often shuts off for 24-36 hours. Track pressure derivatives (rate of change) to flag post-front days.

### 7. Solunar / bird-activity correlation — LOW VALUE

Bird activity data is sparse; the existing birdBoost is a placeholder. If a reliable bird data source appears (e.g., eBird offshore reports, satellite bird radar), expand this.

### 8. Salinity fronts — MEDIUM VALUE (added 2026-07-28)

Every major pro service (Hilton's Realtime Navigator explicitly, ROFFS implicitly) layers **salinity** as a distinct signal on top of SST + chlorophyll. Salinity boundaries mark where different water masses meet — often invisible to SST alone, because two water masses can have the same temperature but different salinity. Bait concentrates on salinity fronts the same way it concentrates on temperature fronts.

**Where the pros look:** salinity gradients ≥ 0.5 PSU over 20 nm, especially where they cross an SST break or chlorophyll edge.

**Data source:** Copernicus Marine `MULTIOBS_GLO_PHY_SAL_...` or NASA SMOS/Aquarius sea-surface salinity products. Free with registration.

**Signal to add:** `salinityFrontBoost(zone)` — for each zone, sample the salinity gradient within 10 nm. Bonus scales with gradient: ≥ 0.5 PSU/nm = +0.3; ≥ 1.0 PSU/nm = +0.5. Extra +0.2 if the salinity front sits within 10 nm of an SST break OR chlorophyll edge (feature stacking).

**Deployment consideration:** likely bundled with the altimetry work since both come from Copernicus Marine — one login, one fetch layer.

### 9. Current convergence zones — MEDIUM VALUE, LOW COST (added 2026-07-28)

We already fetch surface current arrows (Open-Meteo Marine, 24 grid points across the offshore area). Pros display these as raw arrows; we do too. But the ACTIONABLE thing is spotting **convergence points** — where two currents point at each other. Bait can't swim against current, so where currents collide, bait piles up on the interface.

**Signal to add:** `currentConvergenceBoost(zone)` — post-process the existing CURRENTS_SNAPSHOT grid. For each pair of adjacent grid points, compute whether their vectors point toward a common midpoint (dot product test). Mark convergence points where this holds; for each zone, boost +0.4 if within 10 nm of a convergence point.

**Cost:** near zero — data already fetched, just add geometric analysis at build time. High leverage per line of code.

### 10. Bathymetric proximity for custom pins — LOW-MEDIUM VALUE (added 2026-07-28)

Named zones (Tuna Ridge = 30-fathom, canyons, wrecks) are already curated around bottom structure. But when users add custom pins via "Add Your Own Spot," we currently don't know if they sit on productive structure. SatFish, RipCharts, and SiriusXM all offer 25-foot bathymetric contours for exactly this reason.

**Signal to add:** `bottomStructureBoost(customSpot)` — for user-added spots only. Fetch GEBCO or NOAA NCEI bathymetry at the pin coords + a 3-nm buffer. If the pin sits on a slope ≥ 1° (steep bottom), a canyon rim, or within 2 nm of a named wreck, +0.3.

**Deployment consideration:** requires GEBCO/NCEI API integration. Lower priority because Randy rarely adds custom pins and this only affects custom pins.

### 11. AIS commercial-fleet tracking — LOW-MEDIUM VALUE, LEGAL CAVEATS (added 2026-07-28)

Commercial longliners and tuna boats know where the fish are. Their positions are broadcast publicly via AIS (Automatic Identification System). Where longliners are actively fishing (slow speed, back-and-forth pattern) is where tuna are.

**Data source:** Global Fishing Watch (`globalfishingwatch.org`) has a public AIS API for commercial vessel activity. NOAA also offers historical AIS via `marinecadastre.gov`.

**Signal to add:** `fleetActivityBoost(zone)` — count active commercial fishing vessels (identified by AIS activity pattern) within 20 nm of the zone over the last 7 days. Boost scales with count: 1-2 vessels = +0.3; 3+ vessels = +0.5.

**Caveats:** (a) AIS coverage offshore can be spotty; (b) tuna longliners often turn off AIS in high-value zones; (c) confirms rather than predicts (they're already there). Test carefully before deploying — could double-count with existing captain reports.

### 12. HYCOM Thermocline / Mixed Layer Depth — HIGH VALUE, MEDIUM EFFORT (added 2026-07-30 from research)

Tuna feed at the thermocline; where it shoals (comes toward the surface) is where they concentrate. Currently the biggest gap in the model — we have SURFACE temperature but nothing about the water column below. HYCOM GOFS 3.1 = Navy's global ocean forecast, 1/12° (~9km), 41 vertical layers, updated daily.

**Data source:** `https://www.hycom.org/dataserver/gofs-3pt1/analysis` — free, no signup, NetCDF via THREDDS/OPeNDAP. Python `netCDF4` + `xarray` can read it.

**Signal to add:** `thermoclineBoost(zone)` — sample MLD (mixed-layer depth) at zone coords. Boost when MLD is 30-80ft (tuna-fishable depth), penalize when >150ft (thermocline too deep for jigging). Extra +0.4 near MLD gradients (upwelling edges where thermocline shoals).

**Pros use it:** SatFish and Hilton's both surface MLD explicitly. This IS the signal separating hobbyist tools from pro tools.

### 13. AMO Index — HIGH VALUE, TRIVIAL EFFORT (added 2026-07-30 from research)

Peer-reviewed *Science Advances* paper (Faillettaz 2019) — Atlantic Multidecadal Oscillation phase directly drives northern extent of bluefin tuna distribution. Positive AMO = tuna push further north; we're in positive phase now, explaining big NE bluefin years. NOAA PSL publishes monthly index at `https://www.psl.noaa.gov/data/timeseries/AMO/`.

**Signal to add:** `amoSeasonModifier` — global (not per-zone) monthly modifier applied to `seasonalBoost`. When AMO ≥ +0.2, bump bluefin/yellowfin seasonal boosts by +0.1 in NE zones (they're extending north). When AMO ≤ -0.2, penalize by -0.1.

### 14. Gulf Stream North Wall Index — HIGH VALUE FOR CANYONS, LOW EFFORT (added 2026-07-30 from research)

Rutgers RUCOOL + NOAA publish daily north-wall latitude. When the wall pushes north, warm-core rings + yellowfin push into Hudson/Block/Atlantis canyons; south sag = collapse. ROFFS built its whole business around this signal.

**Data source:** Rutgers RUCOOL Gulf Stream front tracker + NOAA OceanNOMADS.

**Signal to add:** `gulfStreamBoost(zone)` — for canyon zones only. +0.5 when north wall is within 30nm of zone latitude, +0.3 within 60nm, 0 otherwise.

### 15. eBird pelagic seabird sightings — MEDIUM VALUE (REPLACES BIRD PLACEHOLDER — added 2026-07-30)

**This finally fills the birdBoost placeholder.** Cornell eBird API returns georeferenced pelagic seabird sightings (Cory's shearwaters, great shearwaters, storm-petrels, jaegers). Offshore pelagic trips log directly. Bird activity where whales aren't = still a bait signal.

**Data source:** `https://api.ebird.org/v2/` — free with email registration for API key.

**Signal to add:** replace current empty `birdBoost` with real eBird-driven data. Sightings within last 3 days ≤50nm from a zone → +0.4. Weight by species: shearwaters (big feeders) heavier than gulls.

### 16. Sentinel-3 OLCI 300m Chlorophyll — MEDIUM-HIGH VALUE (added 2026-07-30 from research)

2.5× finer resolution than the CoastWatch VIIRS 750m we use now — resolves chlorophyll edges that VIIRS smears out. Two satellites (S-3A + S-3B) = daily NE revisit. Free via NOAA CoastWatch OLCI product.

**Data source:** `https://coastwatch.noaa.gov/cwn/products/ocean-color-near-real-time-olci-sentinel-3a-and-3b-global-coverage.html`

**Value:** upgrade to existing signals (Golden Zone, chlaGradientBoost) — same math, sharper input data. Bigger files, cloud gaps more visible.

### 17. Mid-Atlantic Cold Pool bottom temperature — HIGH FOR STRIPER (added 2026-07-30 from research)

NEFSC bottom-temp product (Friedland 2022, *Fisheries Oceanography*). Cold pool has shrunk 40%; its warming/retreat timing drives the striper fall run past Montauk + Block. Early retreat = compressed, later run.

**Data source:** NEFSC ERDDAP `https://coastwatch.pfeg.noaa.gov/erddap/`

**Signal to add:** `coldPoolBoost(zone)` — striper zones only. Strong boost in Aug-Oct when cold pool bottom-temp is warming past 62°F (striper migration trigger).

### 18. WHOI Robots4Whales real-time acoustic buoys — MEDIUM (UPGRADE TO WHALE SIGNAL — added 2026-07-30)

Autonomous DMON buoys off Cape Cod + NY Bight + Gulf of Maine detect fin/humpback/right whale calls hourly, published within ~20 min. Currently we use crowdsourced whale SIGHTINGS (weather-limited, boat-dependent). This is 24/7 acoustic ground truth, weather-independent.

**Data source:** `https://robots4whales.whoi.edu` — HTML tables, needs scraping (no formal API yet).

**Value:** upgrade whaleBoost — when either sightings OR acoustic detection fires, the boost applies. Weather cancellations don't blind us anymore.

### Research pass 2026-07-30 — de-prioritized findings

- **Direct satellite fish detection:** confirmed impossible. Tuna too deep/fast/small. What actually works is inferring where they SHOULD be from subsurface ocean structure (signals #12 above).
- **Solunar tables:** 2023 peer-reviewed study (*SN Applied Sciences*) analyzed thousands of NA trips — solunar predictions do NOT beat chance. Do not add as a real signal.
- **Passive acoustics for tuna directly:** tuna ARE audible (Schilling & Rountree 2004) but no operational hydrophone array covers NE canyons.
- **Saildrone acoustic fish surveys:** commercial only, no public feed.
- **OCEARCH shark tracks:** real-time but predator-proxy value is thin; low added value.
- **NASA PACE hyperspectral (phytoplankton TYPE not just amount):** genuinely novel, launched Feb 2024, but medium-high processing lift; hold for later.

## Where Fish Finder is unique## Where Fish Finder is unique vs the pros (comparison 2026-07-28)

Research pass against Hilton's Realtime Navigator, ROFFS, SatFish, FishTrack, RipCharts, SiriusXM Marine Fish Mapping.

| Signal / feature | Hilton's | ROFFS | SatFish | RipCharts | SiriusXM | **Fish Finder** |
|---|---|---|---|---|---|---|
| SST breaks | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Chlorophyll edges | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| SST × chla intersection stars | — | — | — | — | — | **✓ (Golden Zone)** |
| Altimetry / SSHA | ✓ | ✓ | — | ✓ | ✓ | Backlog (priority #1) |
| Ocean currents | ✓ | ✓ | — | ✓ | ✓ | ✓ (arrows) |
| Salinity | ✓ | (implicit) | — | — | — | Backlog (added today) |
| Bathymetry | — | — | ✓ | ✓ | ✓ | Implicit via curated zones |
| Whale/dolphin sightings | — | — | — | — | — | **✓ (unique)** |
| Captain-report text ingestion | — | — | — | — | — | **✓ (unique)** |
| Fused multi-signal heat score | (manual overlay) | (expert analyst) | — | (manual) | — | **✓ (17-signal effectiveHeat, server + client parity)** |
| Species-specific mode toggle | — | — | — | — | — | **✓ (unique)** |
| Look-ahead prediction (7 days per zone) | — | — | — | — | — | **✓ (unique)** |
| Trip planner (target date + accuracy tracking) | — | — | — | — | — | **✓ (unique)** |
| Prediction accuracy self-tracking | — | — | — | — | — | **✓ (planned)** |
| AI/ML weight tuning from archive | — | — | — | — | — | **✓ (planned)** |
| Boat-fit range/fuel calculation | — | — | — | — | — | **✓ (unique)** |
| Weather-cap disqualification per day per zone | — | — | — | — | — | **✓ (unique)** |
| **Price** | $80/mo | $30-90/report | $70/mo | $60-100/mo | ~$100/mo + SiriusXM | **Free** |

**The gap analysis:** the pros have altimetry + salinity we don't. That's the #1 and #8 backlog items. Everything else we either have or have as a genuinely unique feature they don't offer. The path to being state-of-the-art in signal coverage is short: add altimetry (Copernicus SEALEVEL) + salinity (Copernicus SAL) via one Copernicus login. Estimated dev effort: 2-3 hours + a nightly build-time cost of ~30 seconds. Expected accuracy lift: 5-15% on canyon picks and warm-core-ring days (biggest impact in summer/fall).

**What the pros don't do that we already do:** predictive scoring (they show data; we predict); species-specific picks (they show a generic hot-spot map); boat-fit fuel math; weather-cap per-day disqualification; captain-report text ingestion; whale/dolphin as a first-class bait signal; multi-signal fusion into a single ranking. Randy's Fish Finder is genuinely differentiated on the *prediction* side. Adding altimetry + salinity makes it competitive on the *data-coverage* side too.

## When adding a new signal — the deployment checklist

The First Mate does not add signals casually. Every new signal must:

1. **Have a clear theory** — one sentence on why this signal should predict tuna concentration. If you can't articulate the biological/oceanographic reason, don't add it.
2. **Have a plausible weight range** — argue from first principles (comparable to existing signals). Start on the low end (+0.2 to +0.4) and let the accuracy tracker justify raising it.
3. **Come with a test path** — how will we know in 30 days whether it helped? Usually: does the archive show picks with this signal active outperformed picks without?
4. **Not double-count** — if the signal is redundant with an existing one (e.g., "warm-core ring boost" that always fires when SST 72°F break is nearby), it adds noise without value.
5. **Be reversible** — added as a single term in `effectiveHeat()` that can be commented out cleanly if it degrades accuracy.

## Working style

- **Never override the Captain on data quality.** If the Captain says a heat score needs two independent sources, the First Mate doesn't propose bumping it based on model math alone.
- **Always show the model math.** When explaining a pick, break down effective heat: `live 9 · season 9/10 · whale +0.8 · SST fit +0.6 · golden +0.9 → 20.3` — Randy needs to see WHY.
- **Prefer removing signals to adding them.** A model with 5 well-tuned signals beats one with 15 noisy ones. If a signal fails the 30-day test, remove it.
- **Explain trade-offs, don't hide them.** "Adding altimetry adds one API dependency (Copernicus Marine) that requires free registration. It costs ~15 seconds per nightly build. Expected accuracy lift: 5-10% on canyon picks based on ROFFS methodology." Then Randy decides.
- **Update the Captain skill AND this skill whenever the model changes.** If you add a signal, both skills need to reflect it.

## Interaction with the Captain and Second Mate

- The **Captain** owns: `zones.json` structure, captain sources, whale/bait intel, `zone.heat` (live scores), boat rules, seasonal_presence arrays, data-quality gates.
- The **First Mate** owns: `effectiveHeat()` formula and its terms, weight values, the accuracy scoring logic, look-ahead prediction methodology, weight-change log, signal-backlog research.
- The **Second Mate** (added 2026-07-29) owns: new-region expansion — cloning the tool into Mid-Atlantic / Southeast / Gulf / FL / SoCal / PNW, plus the multi-region refactor that pulls region-specific hardcodes into `regions/<region>.json` config.
- When they disagree: the Captain's data-quality veto always wins on individual data points. The First Mate can propose reweighting how much a data point matters, but can't override whether the data is trustworthy. The Second Mate never modifies formula code, but can propose region-conditional signal parameters (e.g. "NC needs a Gulf Stream western edge signal that NE doesn't use") for First Mate review.
- **Cross-region weight discipline** (First Mate rule for when Second Mate ships a new region): weights start identical to NE. Do NOT retune per-region until that region's archive has 60+ days × 5+ species × N ≥ 10 catches. Retune ONE weight at a time, tracked in `model_weights_<region>.json`.

## Design principle: simple + user-friendly (Randy 2026-07-29, mirrors Captain rule)

Randy's directive: "Keep everything as simple as possible. Extremely user friendly. Read as little as possible. Remember that in any way we build something." The Captain skill carries the full 10-rule breakdown; the First Mate's obligations under it are more specific:

- **Signal names in the UI must be plain English**, even when the code names stay technical. `sstGradientBoost` in code, but "sharper break" or "SST break sharpness" in the pick card label. `youtubeCorroborationBoost` in code, but "📺 YouTube +0.4 (2ch/5v)" in the display.
- **The pick card's model-signals row is the primary user-facing surface for the formula.** Every new signal added must earn its space there — small chip, one word, obvious color. If a signal doesn't visualize cleanly, rethink the signal.
- **Don't grow the formula for its own sake.** Adding a 17th, 18th, 19th signal makes the model harder to reason about AND harder to display. Before adding, ask: does this genuinely improve picks enough to justify the complexity? If unsure, hold.
- **When simplifying: preserve the archive shape.** If we consolidate two signals into one, keep both in the archive as separate fields for historical continuity — display can hide them, but the accuracy loop needs the resolution.

## The "do it all at once" TODO list (Randy's 2026-07-29 rule)

`/root/fish-finder/TODO.md` is Randy's persistent bucket of leftover items — anything requiring a signup, an email verification, a fee, or a small polish that got noted mid-session but not done. Randy's directive: "At some point we're just gonna do it all at once. Don't wanna forget any of them."

**First Mate-specific things that belong on TODO.md:**
- New data-source signups that would unlock a signal we've researched (Copernicus Marine for altimetry + salinity, Global Fishing Watch for AIS, any academic dataset that requires registration).
- "Blocked on data/time" items where the model has to wait for archive maturity — first accuracy analysis (needs 14+ days), first weight-tuning pass (needs 30+ days), first persistence signal firing (needs 3 days of contour archives). These live in the "⏳ Blocked on data / time to elapse" section.
- New backlog signals proposed but not yet built (each gets a TODO entry with the theory, the expected weight, and the test path from the backlog checklist).

**Session hygiene:** Every First Mate session finishes by (a) checking off any TODO items completed, (b) adding any newly-discovered leftover items, and (c) updating the "earliest realistic date" on any blocked-on-time items as the archive grows. When Randy asks "what's left on the model?" the answer comes straight from TODO.md.

## Skill freshness — Randy's "no going backwards" rule (2026-07-28)

The First Mate and Captain skills MUST stay in sync with what's actually in the project. This is a hard rule Randy has explicitly given. Both skills carry the same freshness protocol:

**Canonical location.** The source of truth for both skill files lives IN the project repo:
- `/root/fish-finder/skills/captain/SKILL.md`
- `/root/fish-finder/skills/first-mate/SKILL.md` (this file)

The runtime-loaded copies are read-only account caches — they can't be written back to. To update them, edit the canonical file, repackage via `/root/fish-finder/repackage-skills.sh`, deliver via SendUserFile.

**Mutual oversight.** The First Mate, Captain, and Second Mate all check each other. None is allowed to drift on the others' watch. At the START of every session that touches Fish Finder, whichever skill is loaded performs this check:

1. Read `/root/fish-finder/CHANGELOG.md`.
2. Compare the last "Skills synced" timestamp to the most recent changelog entry.
3. If any feature-affecting change happened since the last sync (new signal, new weight tuning, new prediction methodology, new look-ahead algorithm, new archive schema, new accuracy tracking, new region shipped, cross-region weight-discipline change, etc.), update ALL THREE SKILL.md files in the repo as needed.
4. Run `/root/fish-finder/repackage-skills.sh` to rebuild `captain.skill`, `first-mate.skill`, and `second-mate.skill` in `/root/fish-finder/deploy/`.
5. Deliver whichever `.skill` file(s) actually changed at the END of the session alongside the fish planner card, with a caption naming what changed.
6. Append `Skills synced YYYY-MM-DD — CAPTAIN + FIRST MATE + SECOND MATE` to the CHANGELOG.

**Automated safety net.** The nightly trigger runs this same check at 8pm ET so drift can never last more than 24 hours.

**First-Mate-specific things that trigger a resync:**
- Any change to `effectiveHeat()` formula in `fish-finder.src.html` (add/remove/reweight a signal)
- Any change to server-side `compute_zone_state()` in `archive.py` (must stay in parity with client-side effectiveHeat)
- New signal added or removed (update the "Signal-by-signal rationale" section)
- Weight changed on an existing signal (update the formula + append to the weight change log)
- New research finding from an external service (ROFFS, Hilton's, SatFish, academic paper) added to the "Signal backlog" section
- Prediction accuracy analysis run (update accuracy_1/3/7 numbers + any weight recommendations)
- Look-ahead prediction methodology changed (update the "Look-ahead" section)
- Archive schema extended (update the archive fields list — runners_up, picks_by_species, freshness_audit, etc.)
- Per-species pick loop's `all_species` set changed (v24.106 expanded 15 → 42 species — now matches SPECIES_TEMP_PREF)
- TODO.md protocol or format changed (must stay mirrored to the Captain's TODO rule)

**Unconstrained pick accuracy loop (added 2026-07-29).** Randy's directive: predict without regard to his boat range so he can see the model's raw output. The archive now stores both `picks.tuna_in_range` / `picks.tuna_unconstrained` (and striper equivalents) per snapshot. Once ground-truth catch reports accumulate (or once we have YouTube trip recaps from bigger-boat operators), the First Mate can run a two-population comparison:
- Days where in-range pick == unconstrained pick → single accuracy signal (existing loop)
- Days where they DIFFER → compare which one produced better outcomes when reachable ground truth exists (via YouTube channel corroboration or public catch reports)
This is a longer-horizon signal (months of data, not weeks) but it's THE test of whether the model's ranking is truly right or whether it's over-weighting proximity. Do NOT tune weights on this until at least 60 days of archive with divergent picks.

**Per-species picks — new accuracy dimension (added 2026-07-29).** Randy's directive: "work in all those other fishes into the archives." The archive now stores `picks.picks_by_species` — for each of 15 tracked species, the top zone in-range + unconstrained on that day. This unlocks per-species accuracy analysis, which is a strictly harder and more valuable test than the tuna/striper aggregate:

- **Sample size trade-off.** Per-species series will be sparser (some species only have zones in-season for 2-4 months). Some, like sailfish, will have no data most of the year. Accept the sparsity — don't backfill or interpolate.
- **What "correct" means per species.** For striped_bass we already have catch reports flowing. For swordfish/marlin/wahoo/mako/thresher, ground truth comes from YouTube captain trip recaps and NOAA HMS landings, both slower to accumulate. Set the accuracy analysis threshold at N=10 confirmed catches per species before drawing conclusions.
- **Cross-species signal-weight discovery.** A signal that predicts bluefin well (e.g., whale sightings) may not predict swordfish (which relates more to SST + moon phase for the nightly bite). The archive being per-species lets us eventually tune signal weights PER SPECIES, not just globally. First Mate should NOT do this until we have 60+ days × 5+ species with N≥10 catch data each.
- **Early sanity check (~30 days out, mid-August).** Just eyeball whether the per-species top picks look plausible against captain reports. If swordfish always picks Nearshore-South-of-Block (wrong — swordfish are canyon fish), we have a data bug in the seasonal_presence arrays and should flag it.

**When Randy asks "have you updated the First Mate?"** the answer is either "yes, delivered above" or "no, syncing now." Never "I can't update the skill."

## When to invoke this skill

Load this skill whenever working on:
- Anything inside `effectiveHeat()` in `/root/fish-finder/map/fish-finder.src.html`
- Adding, tuning, or removing a prediction signal
- Analyzing archive history for accuracy patterns
- Look-ahead predictions or the 7-Day Look-Ahead widget
- Trip Planner verdict logic
- Golden Zone geometry or the star-drop rules
- Anything related to "how does the model decide" or "why did it pick X"
- Research on external prediction techniques
- Weight-tuning discussions

The First Mate's job is to make sure that on any given day of the year, the picks the model gives Randy are the best they can be given all evidence available at build time — and that a year from now, they are meaningfully sharper than they are today.
