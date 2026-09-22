# First Mate patterns journal


## 2026-09-10 — 30-day retro pattern: pick stagnation, environmental signal quiet, live_heat freshness starving

**28-day window (Aug 13 → Sept 9)** across 28 archive snapshots. Signal parity closed v24.106; this is the first real retro with the full 17-signal formula.

**Pick stagnation:** top tuna = `s_block_nearshore` 28/28 days. Top striper = `block_island_striper` 28/28 days. Unique winners: 1 each. Mean gap #1 vs #2 = 0.82 (median 0.80). Not because signals are stuck — because the DATA feeding them is stuck.

**Environmental signal firing (% of days signal fired on ≥1 zone):**
- season_boost 100%, whale_boost 86%  ← the two dominant contributors
- sst_fit 11%, bait_boost 11%, trend_boost 11%, youtube_corroboration 11%, distance_penalty 11%, diversity_boost 11%
- chla_gradient 7%, sst_gradient 7%, golden_zone 7%
- post_front_penalty 4%, convergence_boost 4%
- **0%: bird_boost, persistence_boost, captain_dwell_boost** (all dormant across the entire 28-day window)

**Interpretation** (per Accumulate-Over-Prune):
1. Not a weight-tuning problem — dormant signals aren't over-weighted, they're not FIRING. Reweighting a zero is a zero.
2. Not a code problem — v24.106 parity is confirmed, signals fire when conditions warrant.
3. IS a data-starvation problem: (a) NE water uniformity late summer explains quiet env signals; (b) 15 of 49 zones have heat_updated > 30 days ago (max 63d), triggering `live_heat=dead` on 75% of days.

**Actionable, in order of leverage:**
1. Captain refresh the 15 stale zones. 4 of them are heat=7 and 63 days stale (Wasque Rip, The Fishtails, Sow & Pigs Reef, Sakonnet Point) — if any is actually hot right now, it'd challenge the top pick.
2. Add 7 Seas Whale Watch (Gloucester) as secondary NE whale source. Viking Fleet sporadic.
3. New freshness audit rule: warn if top pick unchanged N days. Not because rotation is inherently good, but because 0 rotation = flag that a data input has flatlined.

**Do NOT act on** (per First Mate rules):
- Reweighting any signal. Wait for actual ground-truth catches (30+).
- Removing dormant signals. Their designed conditions (bird intel data source, 3-day persistent breaks, captain dwell aggregation) haven't materialized yet; preserve for fall run.

**Report artifact:** `first-mate-30d-report.html` published to Cowork gallery.
