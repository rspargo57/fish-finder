#!/usr/bin/env python3
"""
pick_backup_audit.py — Per-region top-5 pick verification.

Randy 2026-09-15: "Whatever the one, two, three, four, five picks are in all the
different regions" must be backed by verifiable data. Randy can only check NE
zones himself; the Captain has to ensure SE/Gulf/S.FL/Mid-Atl picks are backed
by something concrete, or explicitly flag them as PROVISIONAL.

RUNS: at session start (Captain rule), and inside build-inlined.py before deploy.

WRITES: /root/fish-finder/data/pick_audit.json — machine-readable per-region
audit that the map + pick card can render as a "provisional" badge.

RANKING: uses effective_heat from today's archive snapshot (matches the site's
own pick ranking). Falls back to raw heat if archive not present.

BACKING EVIDENCE — a zone is BACKED if ANY of:
  · `intel_sources` dict has ≥ 1 entry (structured intel, e.g. YouTube channels)
  · at least one `bait_intel` entry (≤ 14 days) tags this zone
  · at least one `whale_sightings` entry (≤ 21 days) tags this zone
  · zone has `heat_updated` ≤ 21 days AND non-empty `notes`
  (v24.131 broadened from strict-and — a fresh dated captain report about a
  zone counts even when intel_sources isn't populated)

REGION VERDICT:
  GREEN  — 5 of 5 backed
  YELLOW — 3-4 of 5 backed
  RED    — ≤ 2 of 5 backed  (the region Randy can't trust)
"""
import json
import sys
from datetime import date, datetime
from pathlib import Path

BASE = Path(__file__).parent
DATA = BASE / "data"
ARCHIVE = BASE / "archive"
TODAY = date.today()

REGION_FILES = {
    "northeast":       ("zones.json",                "northeast"),
    "mid_atlantic":    ("zones_mid_atlantic.json",   "mid_atlantic"),
    "south_atlantic":  ("zones_south_atlantic.json", "south_atlantic"),
    "gulf":            ("zones_gulf.json",           "gulf"),
    "south_florida":   ("zones_south_florida.json",  "south_florida"),
}

# How stale is too stale for a top pick? Same 21-day threshold the
# freshness audit uses for `heat_updated` dead status.
MAX_HEAT_AGE_DAYS = 21
BAIT_FRESH_DAYS   = 14
WHALE_FRESH_DAYS  = 21


def load_region(path):
    if not path.exists():
        return {"zones": [], "bait_intel": [], "whale_sightings": []}
    with open(path) as f:
        d = json.load(f)
    if isinstance(d, list):
        return {"zones": d, "bait_intel": [], "whale_sightings": []}
    return d


def days_stale(iso_str):
    if not iso_str:
        return None
    try:
        d = datetime.strptime(str(iso_str).split("T")[0], "%Y-%m-%d").date()
        return (TODAY - d).days
    except Exception:
        return None


def load_today_archive():
    """Return today's archive snapshot if it exists (for effective_heat), else None."""
    today_file = ARCHIVE / f"{TODAY.isoformat()}.json"
    if today_file.exists():
        try:
            with open(today_file) as f:
                return json.load(f)
        except Exception:
            return None
    # Try most-recent archive if today's isn't there yet
    all_files = sorted(ARCHIVE.glob("2026-*.json"), reverse=True)
    if all_files:
        try:
            with open(all_files[0]) as f:
                return json.load(f)
        except Exception:
            return None
    return None


def get_effective_heat_map(archive, region_key):
    """Extract {zone_id: effective_heat} for a region from the archive."""
    if not archive:
        return {}
    # NE is stored at the top level; other regions under archive['regions'][key]
    if region_key == "northeast":
        zs = archive.get("zones_state", {})
    else:
        zs = (archive.get("regions", {}).get(region_key, {}) or {}).get("zones_state", {})
    out = {}
    for zid, s in (zs.items() if isinstance(zs, dict) else []):
        if isinstance(s, dict) and "effective_heat" in s:
            out[zid] = s["effective_heat"]
    return out


def audit_zone(z, region_data):
    """Return (verdict, reasons[], meta) — BACKED or PROVISIONAL, with WHY."""
    reasons = []
    hu = z.get("heat_updated")
    stale = days_stale(hu)
    sources = z.get("intel_sources") or {}
    src_count = len(sources) if isinstance(sources, dict) else 0
    notes = (z.get("notes") or "").strip()

    # Fresh bait_intel or whale_sightings tagged to this zone count as backing.
    zid = z.get("id")
    bait = region_data.get("bait_intel", []) or []
    whales = region_data.get("whale_sightings", []) or []
    bait_ref_count = 0
    for b in bait:
        if zid in (b.get("zones") or []):
            age = days_stale(b.get("date"))
            if age is not None and age <= BAIT_FRESH_DAYS:
                bait_ref_count += 1
    whale_ref_count = 0
    for w in whales:
        if zid in (w.get("zones") or []):
            age = days_stale(w.get("date"))
            if age is not None and age <= WHALE_FRESH_DAYS:
                whale_ref_count += 1

    # Any of these makes the zone BACKED:
    backed_paths = []
    if src_count >= 1:
        backed_paths.append(f"{src_count} intel_sources")
    if bait_ref_count >= 1:
        backed_paths.append(f"{bait_ref_count} bait_intel ≤{BAIT_FRESH_DAYS}d")
    if whale_ref_count >= 1:
        backed_paths.append(f"{whale_ref_count} whale sighting ≤{WHALE_FRESH_DAYS}d")
    if hu and stale is not None and stale <= MAX_HEAT_AGE_DAYS and notes:
        backed_paths.append(f"heat verified {stale}d ago w/ notes")

    if backed_paths:
        verdict = "BACKED"
    else:
        verdict = "PROVISIONAL"
        if src_count == 0:
            reasons.append("intel_sources empty")
        if bait_ref_count == 0:
            reasons.append(f"no bait ≤{BAIT_FRESH_DAYS}d")
        if whale_ref_count == 0:
            reasons.append(f"no whale sighting ≤{WHALE_FRESH_DAYS}d")
        if not hu:
            reasons.append("no heat_updated")
        elif stale is not None and stale > MAX_HEAT_AGE_DAYS:
            reasons.append(f"heat_updated {stale}d stale")
        if not notes:
            reasons.append("no zone notes")

    return verdict, reasons, {
        "heat_updated": hu,
        "heat_updated_days_stale": stale,
        "intel_source_count": src_count,
        "bait_ref_count": bait_ref_count,
        "whale_ref_count": whale_ref_count,
        "notes_len": len(notes),
        "backing_paths": backed_paths,
    }


def audit_region(region_key, region_data, effective_heat_map):
    zones = region_data.get("zones", [])
    if not zones:
        return None
    # Rank by effective_heat where available; fall back to raw heat.
    def rank_key(z):
        zid = z.get("id")
        eh = effective_heat_map.get(zid)
        if eh is not None:
            return eh
        return z.get("heat") or 0
    top = sorted(zones, key=rank_key, reverse=True)[:5]
    entries = []
    backed = 0
    for i, z in enumerate(top, 1):
        verdict, reasons, meta = audit_zone(z, region_data)
        if verdict == "BACKED":
            backed += 1
        eh = effective_heat_map.get(z.get("id"))
        entries.append({
            "rank": i,
            "zone_id": z.get("id"),
            "zone_name": z.get("name"),
            "heat": z.get("heat"),
            "effective_heat": round(eh, 2) if eh is not None else None,
            "verdict": verdict,
            "reasons": reasons,
            "meta": meta,
        })
    if backed == 5:
        region_verdict = "GREEN"
    elif backed >= 3:
        region_verdict = "YELLOW"
    else:
        region_verdict = "RED"
    return {
        "region": region_key,
        "region_verdict": region_verdict,
        "backed_count": backed,
        "total": len(top),
        "picks": entries,
    }


def main():
    archive = load_today_archive()
    audit = {
        "date": TODAY.isoformat(),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "archive_used": archive.get("date") if archive else None,
        "regions": {},
        "summary": {},
    }
    total_backed = 0
    total_picks = 0
    reds, yellows, greens = [], [], []

    for region_key, (fname, archive_key) in REGION_FILES.items():
        region_data = load_region(DATA / fname)
        if not region_data.get("zones"):
            print(f"⚠️  {region_key}: no zones file at {fname}")
            continue
        eh_map = get_effective_heat_map(archive, archive_key)
        r = audit_region(region_key, region_data, eh_map)
        if not r:
            continue
        audit["regions"][region_key] = r
        total_backed += r["backed_count"]
        total_picks += r["total"]
        if r["region_verdict"] == "GREEN":
            greens.append(region_key)
        elif r["region_verdict"] == "YELLOW":
            yellows.append(region_key)
        else:
            reds.append(region_key)

    audit["summary"] = {
        "backed_of_total": f"{total_backed}/{total_picks}",
        "backed_pct": round(100 * total_backed / total_picks) if total_picks else 0,
        "green_regions": greens,
        "yellow_regions": yellows,
        "red_regions": reds,
        "max_heat_age_days": MAX_HEAT_AGE_DAYS,
        "bait_fresh_days": BAIT_FRESH_DAYS,
        "whale_fresh_days": WHALE_FRESH_DAYS,
    }

    out_path = DATA / "pick_audit.json"
    out_path.write_text(json.dumps(audit, indent=2))

    print("=" * 72)
    print(f"PICK BACKUP AUDIT — {TODAY.isoformat()}")
    print(f"Ranked by effective_heat from archive: {audit['archive_used'] or '(none — using raw heat)'}")
    print(f"Backed picks: {total_backed}/{total_picks} ({audit['summary']['backed_pct']}%)")
    print(f"🟢 GREEN regions: {', '.join(greens) or 'none'}")
    print(f"🟡 YELLOW regions: {', '.join(yellows) or 'none'}")
    print(f"🔴 RED regions:   {', '.join(reds) or 'none'}")
    print("=" * 72)
    for region_key, r in audit["regions"].items():
        emoji = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}.get(r["region_verdict"], "?")
        print(f"\n{emoji} {region_key.upper()} — {r['backed_count']}/{r['total']} backed")
        for p in r["picks"]:
            mark = "✅" if p["verdict"] == "BACKED" else "🔴"
            eh_str = f"eh={p['effective_heat']}" if p['effective_heat'] is not None else f"heat={p['heat']}"
            if p["verdict"] == "BACKED":
                detail = " · " + " · ".join(p["meta"]["backing_paths"])
            else:
                detail = " · " + " · ".join(p["reasons"]) if p["reasons"] else ""
            print(f"    {mark} #{p['rank']} {p['zone_name'][:36]:<38} {eh_str}{detail}")

    if reds:
        print("\n❌ At least one region has ≤ 2 backed picks. Captain: address before deploy.")
        sys.exit(1)
    print(f"\n✅ Audit written to {out_path}")


if __name__ == "__main__":
    main()
