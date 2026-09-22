#!/usr/bin/env python3
"""
v24.94 (Randy 2026-09-04) — canonical species seasonal-presence calendars.

Every zone's `seasonal_presence` field maps a species key → 12-int array
(Jan..Dec, 1-10 scale) of how present/catchable that species is in that
month at a "typical" zone for that species. Values are educated priors
from public NC/SC/GA/FL DNR annual harvest reports + NOAA SEDAR migration
data + regional captain interviews aggregated in the code review that
caught the v24.93 bug (79 zones with empty seasonal_presence sailing
past the model with -1.5 penalty every day).

**Import + apply pattern.** New zone-adding code should call
`seed_seasonal_presence(zone)` after setting the zone's `species` array;
it fills in `zone["seasonal_presence"]` for every species we have a
calendar for, without overwriting anything already set. Any species not
in this dict must be added here (and its rows patched in via
`backfill_seasonal_presence(regions)`), or the model treats it as
out-of-season for that zone.

**Species not covered yet** get logged in `unknown_species` when
backfilling — the caller can decide to error or accept the gap.
"""
from __future__ import annotations

import json
import pathlib
from typing import Iterable

# ---------------------------------------------------------------------------
# Canonical 12-month calendars (Jan..Dec, 1-10 scale)
# ---------------------------------------------------------------------------

CALENDARS: dict[str, list[int]] = {
    # ————— Bottom fish (year-round with regional peaks) —————
    'snapper':              [6, 6, 6, 7, 8, 8, 9, 9, 9, 8, 7, 6],
    'red_snapper':          [4, 4, 4, 5, 7, 9, 9, 8, 7, 5, 4, 4],
    'vermilion_snapper':    [6, 6, 6, 7, 8, 8, 8, 8, 8, 8, 7, 7],
    'yellowtail_snapper':   [7, 7, 7, 8, 8, 8, 8, 8, 8, 8, 7, 7],
    'mutton_snapper':       [7, 7, 8, 8, 9, 9, 8, 8, 8, 8, 7, 7],
    'grouper':              [5, 5, 6, 7, 8, 8, 8, 8, 8, 7, 7, 6],
    'gag_grouper':          [4, 4, 5, 6, 7, 7, 8, 8, 9, 8, 6, 5],
    'red_grouper':          [6, 6, 6, 7, 8, 8, 8, 7, 7, 7, 7, 6],
    'goliath_grouper':      [7, 7, 7, 7, 8, 8, 8, 8, 8, 7, 7, 7],
    'black_sea_bass':       [9, 9, 8, 7, 6, 5, 5, 5, 6, 7, 8, 9],
    'sea_bass':             [8, 8, 7, 6, 5, 5, 5, 6, 7, 8, 8, 8],
    'amberjack':            [7, 7, 7, 8, 9, 9, 8, 7, 7, 8, 8, 7],
    'tautog':               [8, 8, 8, 6, 4, 3, 3, 3, 4, 6, 8, 8],
    'cod':                  [8, 8, 8, 7, 5, 4, 3, 3, 5, 7, 8, 8],
    'ling':                 [5, 5, 6, 7, 8, 8, 7, 7, 7, 6, 5, 5],
    'hogfish':              [7, 7, 7, 8, 8, 8, 7, 7, 7, 8, 8, 7],
    'porgy':                [4, 4, 5, 7, 8, 8, 8, 8, 8, 7, 5, 4],
    'sheepshead':           [8, 8, 8, 7, 6, 5, 5, 5, 6, 7, 8, 8],

    # ————— Pelagic mackerels + cobia —————
    'king_mackerel':        [2, 2, 3, 6, 8, 9, 9, 9, 9, 8, 6, 3],
    'kingfish':             [2, 2, 3, 6, 8, 9, 9, 9, 9, 8, 6, 3],
    'spanish_mackerel':     [1, 1, 2, 5, 8, 9, 9, 8, 8, 7, 5, 2],
    'cobia':                [1, 2, 5, 9, 9, 6, 4, 3, 5, 6, 3, 1],

    # ————— Big-game offshore pelagics (Gulf Stream) —————
    'yellowfin_tuna':       [3, 3, 4, 6, 8, 9, 9, 9, 9, 8, 6, 4],
    'blackfin_tuna':        [8, 8, 8, 8, 7, 6, 6, 6, 7, 8, 8, 8],
    'bigeye_tuna':          [3, 3, 3, 4, 5, 6, 7, 7, 8, 7, 5, 4],
    'bluefin_recreational': [4, 4, 4, 5, 7, 8, 9, 9, 8, 7, 5, 4],
    'blue_marlin':          [2, 2, 3, 5, 7, 8, 8, 8, 7, 6, 4, 2],
    'white_marlin':         [2, 2, 3, 4, 6, 7, 8, 8, 7, 6, 4, 2],
    'wahoo':                [4, 4, 5, 8, 8, 7, 6, 6, 7, 9, 8, 6],
    'mahi':                 [3, 3, 5, 7, 9, 9, 8, 8, 7, 7, 5, 4],
    'sailfish':             [7, 6, 5, 4, 3, 3, 5, 6, 6, 7, 8, 8],
    'swordfish':            [4, 4, 5, 6, 7, 8, 9, 9, 8, 7, 5, 4],

    # ————— Inshore + coastal —————
    'redfish':              [7, 7, 7, 8, 8, 8, 8, 9, 9, 9, 8, 7],
    'spotted_seatrout':     [7, 7, 6, 6, 5, 5, 6, 7, 8, 8, 8, 7],
    'flounder':             [4, 4, 5, 7, 8, 8, 8, 8, 8, 7, 5, 4],
    'fluke':                [2, 2, 3, 5, 7, 8, 8, 8, 8, 6, 3, 2],
    'snook':                [3, 3, 5, 7, 8, 9, 9, 9, 8, 6, 4, 3],
    'tarpon':               [3, 3, 4, 6, 9, 10, 9, 8, 7, 5, 3, 3],
    'bonefish':             [5, 5, 6, 8, 8, 8, 8, 8, 8, 7, 6, 5],
    'permit':               [4, 4, 5, 7, 8, 8, 8, 7, 7, 6, 5, 4],
    'barracuda':            [7, 7, 7, 7, 7, 7, 7, 7, 8, 8, 8, 7],
    'jack_crevalle':        [4, 4, 5, 7, 8, 8, 8, 8, 8, 7, 5, 4],
    'bluefish':             [3, 3, 4, 6, 8, 8, 7, 7, 8, 8, 7, 5],
    'striped_bass':         [4, 4, 5, 8, 9, 8, 6, 5, 7, 9, 9, 7],

    # ————— NE + shark + additional coastal (v24.94 extras) —————
    'bluefin_giant':        [3, 3, 3, 4, 5, 7, 9, 10, 9, 8, 6, 3],  # NE/Gulf giant tuna peak Jul-Sep
    'bonito':               [3, 3, 3, 4, 6, 7, 8, 8, 8, 7, 5, 3],   # NE/Mid-Atl summer + Fall
    'false_albacore':       [2, 2, 2, 3, 5, 7, 8, 9, 9, 9, 7, 4],   # NE fall funny-fish blitz
    'weakfish':             [3, 3, 4, 6, 8, 8, 7, 6, 7, 8, 6, 3],   # NE/Mid-Atl spring + fall
    'red_drum':             [7, 7, 7, 8, 8, 8, 8, 9, 9, 9, 8, 7],   # alias of redfish
    'mangrove_snapper':     [6, 6, 6, 7, 8, 8, 9, 9, 8, 8, 7, 6],   # FL year-round, summer peak
    'cero_mackerel':        [7, 7, 7, 7, 7, 7, 6, 6, 7, 8, 8, 8],   # FL / Keys year-round
    'mako_shark':           [3, 3, 4, 6, 8, 9, 8, 7, 6, 5, 4, 3],   # NE mako run early summer
    'thresher_shark':       [3, 3, 4, 6, 8, 8, 7, 6, 6, 6, 4, 3],   # NE summer
}


def seed_seasonal_presence(zone: dict) -> dict:
    """Populate zone['seasonal_presence'] for every species in zone['species']
    that has a calendar. Does NOT overwrite existing entries.

    Returns the modified `zone` dict (also mutates in place)."""
    species = zone.get('species') or []
    sp = zone.setdefault('seasonal_presence', {}) or {}
    for s in species:
        if s not in sp and s in CALENDARS:
            sp[s] = list(CALENDARS[s])  # copy so future edits don't mutate the template
    zone['seasonal_presence'] = sp
    return zone


def backfill_seasonal_presence(regions: Iterable[str | pathlib.Path], *,
                               verbose: bool = False) -> tuple[int, set[str]]:
    """Fill in missing calendars across every region file. Returns
    (n_calendars_added, set_of_species_with_no_calendar)."""
    added = 0
    unknown: set[str] = set()
    for r in regions:
        p = pathlib.Path(r)
        if not p.exists():
            continue
        d = json.loads(p.read_text())
        for z in d.get('zones', []):
            before = len(z.get('seasonal_presence', {}) or {})
            seed_seasonal_presence(z)
            after = len(z.get('seasonal_presence', {}) or {})
            added += (after - before)
            for s in (z.get('species') or []):
                if s not in CALENDARS:
                    unknown.add(s)
        p.write_text(json.dumps(d, indent=2))
        if verbose:
            print(f'  {p.name}: rewrote')
    if verbose and unknown:
        print(f'  WARN: no calendar for species: {sorted(unknown)}')
    return added, unknown


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--verbose', '-v', action='store_true')
    args = ap.parse_args()
    base = pathlib.Path('/root/fish-finder/data')
    added, unknown = backfill_seasonal_presence([
        base / 'zones.json',
        base / 'zones_mid_atlantic.json',
        base / 'zones_south_atlantic.json',
        base / 'zones_gulf.json',
        base / 'zones_south_florida.json',
    ], verbose=args.verbose)
    print(f'Added {added} calendars')
    if unknown:
        print(f'Unknown species (add to CALENDARS dict): {sorted(unknown)}')


if __name__ == '__main__':
    main()
