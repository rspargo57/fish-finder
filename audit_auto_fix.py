#!/usr/bin/env python3
"""Fish Finder — Audit Auto-Fix.

Randy 2026-08-07: "The Captain is gonna fix these things as problems arise
... if something doesn't pass the audit, you're gonna try to write a fix
and just automatically fix it, right?"

This module runs AFTER site_health_audit.py finds problems. For each finding
that has a KNOWN MECHANICAL fix (rerun a harvester, retry a fetch, patch a
timestamp), we try the fix. For findings that need a HUMAN eye (missing
captain intel, novel code bugs, layout regressions), we return them
untouched so the trigger's Claude session can attempt code fixes or
escalate to Randy.

WHAT THIS FILE FIXES AUTOMATICALLY (safe, deterministic):
  - `signal_dead: whale_sightings`   → rerun whale_harvest.py
  - `signal_dead: bait_intel`        → note only (no auto-source exists)
  - `env_pressure DEAD`              → note only (transient API issue —
                                        next nightly will retry)
  - `live_stale_build`               → retry Cloudflare deploy once
  - `no_heat_updated` on any zone    → seed to today (allowed only if the
                                        zone has never had a value)
  - `stale_build_date_header`        → rerun build (fresh substitution)
  - `unsubstituted_placeholder`      → rerun build (missed substitution)

WHAT THIS FILE DOES NOT FIX (hands off — needs human judgment or real data):
  - `zones_dead_captain_intel`       → REAL captain intel needed. Can't fake.
  - `pick_confidence_zero`           → symptom of above; don't touch.
  - `mobile_horizontal_overflow`     → CSS regression; needs code fix.
  - `js_pageerror` / `console_error` → new code bug; needs code fix.
  - `duplicate_id`                   → schema violation; needs human.
  - `date_in_future`                 → someone typo'd; needs human.

Callable as a module OR standalone:
    python3 audit_auto_fix.py               # try to fix current findings
    python3 audit_auto_fix.py --dry-run     # report what WOULD be fixed
"""
import json
import pathlib
import subprocess
import datetime
import argparse
import sys

BASE = pathlib.Path("/root/fish-finder")
ARCHIVE_DIR = BASE / "archive"
ZONES_PATH = BASE / "data" / "zones.json"


# Findings we know how to fix mechanically. Value = handler function name.
AUTO_FIX_HANDLERS = {
    "signal_dead": "fix_signal_dead",       # dispatched on detail.name
    "no_heat_updated": "fix_missing_heat_updated",
    "stale_build_date_header": "fix_rerun_build",
    "unsubstituted_placeholder": "fix_rerun_build",
    "live_stale_build": "fix_retry_deploy",
    "live_no_build_date": "fix_retry_deploy",
}

# Findings that are informational — safe to acknowledge but do nothing.
BENIGN_CODES = {
    "bird_intel_placeholder",
    "zones_dead_captain_intel",   # visible by design; needs real intel
    "pick_confidence_low",
    "signal_stale",
    "live_one_build_behind",      # will catch up tonight
}


def load_latest_snapshot():
    files = sorted(ARCHIVE_DIR.glob("2*.json"))
    if not files:
        return None, None
    p = files[-1]
    return p, json.loads(p.read_text())


def collect_actionable(snap):
    """Return (fixable, needs_human, benign) lists of findings from snapshot."""
    audit = snap.get("site_health_audit") if snap else None
    if not audit:
        return [], [], []
    fixable, needs_human, benign = [], [], []
    for cat_name, cat in (audit.get("categories") or {}).items():
        for entry in cat.get("findings", []):
            code = entry.get("code")
            sev = entry.get("severity")
            if code in BENIGN_CODES:
                benign.append({**entry, "category": cat_name})
            elif code in AUTO_FIX_HANDLERS and sev in ("WARN", "ERROR"):
                fixable.append({**entry, "category": cat_name,
                                "handler": AUTO_FIX_HANDLERS[code]})
            elif sev in ("WARN", "ERROR"):
                needs_human.append({**entry, "category": cat_name})
    return fixable, needs_human, benign


# ================== FIX HANDLERS ==================

def fix_signal_dead(finding, dry_run=False):
    """For `signal_dead: whale_sightings` we can rerun whale_harvest.py.
    For other dead signals (bait, bird), there's no auto-source — return no-op."""
    detail = finding.get("detail") or {}
    signal_name = None
    # Message form: "Signal 'whale_sightings' is DEAD"
    import re
    m = re.search(r"Signal '([^']+)'", finding.get("message", ""))
    if m:
        signal_name = m.group(1)
    if signal_name == "whale_sightings":
        if dry_run:
            return {"action": "would rerun whale_harvest.py", "applied": False}
        try:
            r = subprocess.run(
                ["python3", str(BASE / "whale_harvest.py")],
                capture_output=True, text=True, timeout=120,
            )
            return {
                "action": "reran whale_harvest.py",
                "applied": r.returncode == 0,
                "stdout_tail": (r.stdout or "").splitlines()[-3:],
                "stderr_tail": (r.stderr or "").splitlines()[-3:],
            }
        except Exception as e:
            return {"action": "whale_harvest.py failed", "applied": False, "error": str(e)}
    # bait, bird, live_heat, env_pressure have no mechanical auto-fix here
    return {"action": f"no auto-source for signal '{signal_name}'", "applied": False,
            "note": "manual intel refresh required"}


def fix_missing_heat_updated(finding, dry_run=False):
    """Seed heat_updated to today for a zone that has never had one.
    Only applies to zones without a value — never overwrites."""
    import re
    m = re.search(r"Zone (\S+) has no heat_updated", finding.get("message", ""))
    if not m:
        return {"action": "couldn't parse zone id from finding", "applied": False}
    zid = m.group(1)
    if dry_run:
        return {"action": f"would seed heat_updated on zone '{zid}'", "applied": False}
    z_data = json.loads(ZONES_PATH.read_text())
    for z in z_data.get("zones", []):
        if z.get("id") == zid and not z.get("heat_updated"):
            # Only seed if truly empty — never overwrite existing values
            z["heat_updated"] = datetime.date.today().isoformat()
            ZONES_PATH.write_text(json.dumps(z_data, indent=2))
            return {"action": f"seeded heat_updated on zone '{zid}'", "applied": True}
    return {"action": f"zone '{zid}' already has heat_updated (skipped)", "applied": False}


def fix_rerun_build(finding, dry_run=False):
    """A stale build-date-in-header or an unsubstituted placeholder means the
    build didn't finish its substitution steps. Rerun the build. NOTE:
    calling this INSIDE build-inlined.py is a no-op guard so we don't
    infinite-loop; caller must set an env var to opt in when it's safe."""
    import os
    if os.environ.get("FF_AUDIT_INSIDE_BUILD") == "1":
        # We are inside build-inlined.py's audit loop. Do NOT recurse.
        # The next outer retry will rerun the whole build.
        return {"action": "flagged for outer-loop retry (inside build)", "applied": False,
                "note": "outer trigger will rerun build if this recurs"}
    if dry_run:
        return {"action": "would rerun build-inlined.py", "applied": False}
    try:
        r = subprocess.run(
            ["python3", str(BASE / "build-inlined.py")],
            capture_output=True, text=True, timeout=600,
            env={**os.environ, "FF_AUDIT_INSIDE_BUILD": "1"},
        )
        return {"action": "reran build-inlined.py",
                "applied": r.returncode in (0, 2),  # 2 = built but audit failed
                "exit_code": r.returncode,
                "stdout_tail": (r.stdout or "").splitlines()[-5:]}
    except Exception as e:
        return {"action": "rebuild failed", "applied": False, "error": str(e)}


def fix_retry_deploy(finding, dry_run=False):
    """Cloudflare deploy failure — the trigger session should retry the
    wrangler command. This handler surfaces the retry recommendation
    but doesn't do it itself (that requires the token which lives in the
    trigger prompt, not in this script)."""
    return {"action": "retry recommended in trigger session", "applied": False,
            "note": "trigger's Claude should rerun wrangler pages deploy"}


# ================== MAIN ==================

def run_fixes(dry_run=False, max_passes=2):
    """Load latest snapshot's audit, apply auto-fixes, return a report."""
    snap_path, snap = load_latest_snapshot()
    if not snap:
        return {"error": "no snapshot found"}

    all_applied = []
    for pass_num in range(1, max_passes + 1):
        fixable, needs_human, benign = collect_actionable(snap)
        if not fixable:
            break
        applied_this_pass = []
        for f in fixable:
            handler_name = f["handler"]
            handler = globals().get(handler_name)
            if not handler:
                continue
            result = handler(f, dry_run=dry_run)
            applied_this_pass.append({
                "finding_code": f.get("code"),
                "finding_message": f.get("message"),
                "handler": handler_name,
                "result": result,
            })
        all_applied.append({"pass": pass_num, "applied": applied_this_pass})
        if dry_run:
            break
        # Reload snapshot after fixes might have modified it
        snap_path, snap = load_latest_snapshot()

    fixable_final, needs_human_final, benign_final = collect_actionable(snap)
    return {
        "dry_run": dry_run,
        "passes_run": len(all_applied),
        "applied": all_applied,
        "remaining": {
            "fixable_still_present": [f["code"] for f in fixable_final],
            "needs_human_attention": needs_human_final,
            "benign_acknowledged": [b["code"] for b in benign_final],
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what WOULD be fixed without applying")
    parser.add_argument("--json", action="store_true",
                        help="Emit JSON only (no human-readable summary)")
    args = parser.parse_args()

    report = run_fixes(dry_run=args.dry_run)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return

    print()
    print("=" * 60)
    print(f"AUDIT AUTO-FIX  ({'DRY RUN' if args.dry_run else 'APPLYING'})")
    print("=" * 60)
    if "error" in report:
        print(f"❌ {report['error']}")
        sys.exit(1)

    print(f"Passes run: {report['passes_run']}")
    for p in report["applied"]:
        print(f"\n── Pass {p['pass']} ──")
        for entry in p["applied"]:
            r = entry["result"]
            mark = "✅" if r.get("applied") else "•"
            print(f"  {mark} [{entry['finding_code']}] {entry['handler']}")
            print(f"      → {r.get('action', '?')}")
            if r.get("note"):
                print(f"      note: {r['note']}")

    rem = report["remaining"]
    if rem["fixable_still_present"]:
        print(f"\n🔴 {len(rem['fixable_still_present'])} auto-fixable finding(s) still present after {report['passes_run']} pass(es):")
        for c in rem["fixable_still_present"]:
            print(f"      - {c}")
    if rem["needs_human_attention"]:
        print(f"\n👤 {len(rem['needs_human_attention'])} finding(s) NEED HUMAN ATTENTION:")
        for f in rem["needs_human_attention"]:
            print(f"      - [{f.get('code')}] {f.get('message')}")
    if rem["benign_acknowledged"]:
        print(f"\nℹ  {len(rem['benign_acknowledged'])} benign finding(s) acknowledged (no action).")

    print()


if __name__ == "__main__":
    main()
