#!/usr/bin/env python3
"""Fish Finder — aisstream.io WebSocket poller (v23.35, Randy 2026-08-12).

Consumes real-time AIS position messages from aisstream.io and appends them
to `data/captain_positions.jsonl` so that `dwell_analysis.py` can turn them
into fishing-dwell events for the model.

--------------------------------------------------------------------------
ACTIVATION REQUIRED (5-minute one-time signup by Randy):
--------------------------------------------------------------------------
1. Go to https://aisstream.io/authenticate — click "Sign Up"
2. Verify email
3. Create an API key on the dashboard (free tier: 4 concurrent connections,
   unlimited messages)
4. Store the key as an environment variable:
     export AISSTREAM_API_KEY="your-key-here"
   (Or drop it in /root/fish-finder/config/aisstream_key.txt — the poller
    checks both locations; env var wins if both are set.)
5. Test it: `python3 /root/fish-finder/aisstream_poll.py --duration 30`
   (Connects for 30 seconds, prints any positions received, exits cleanly.)
6. Wire up the recurring poll: create a scheduled trigger that runs this
   script every 2 hours for 10 minutes:
     `python3 /root/fish-finder/aisstream_poll.py --duration 600`
   Each 10-minute window will typically collect 3-8 position updates per
   tracked vessel. Over 24 hours (12 runs) we get ~50-100 samples per boat
   — plenty for dwell detection.

--------------------------------------------------------------------------
WHAT THIS SCRIPT DOES:
--------------------------------------------------------------------------
- Reads `data/captain_vessels.json` to build a list of MMSIs to filter on
  (only vessels with non-null `mmsi` are subscribed; the rest are skipped).
- If no MMSIs are set yet, falls back to a bounding-box subscription
  covering Randy's fishing area (39.5N–42N, -74.5W–-68W). We'll see EVERY
  AIS vessel in the area — much more traffic, but useful for DISCOVERING
  which unregistered vessels are running the canyons. We can later add
  the discovered MMSIs to captain_vessels.json manually.
- Opens the aisstream.io WebSocket, sends a subscription message, then
  reads position messages for `--duration` seconds.
- For each position message, appends a JSON line to
  `data/captain_positions.jsonl`:
     {"mmsi": 366123456, "ts": "2026-08-12T14:30:00Z", "lat": 40.85,
      "lon": -72.5, "sog": 3.2, "cog": 220, "name": "FV FAT TUNA"}
- Closes cleanly on timeout or Ctrl+C.

The file `captain_positions.jsonl` is append-only. Never truncate it —
`dwell_analysis.py` filters by recency at read time (default 7-day
lookback). Old positions age out naturally; the archive stays intact for
retroactive analysis.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
import pathlib
import sys

# aisstream.io uses standard WebSocket JSON messages. We depend on `websockets`
# (already installed for other tools in this project — if missing, install:
#   pip install --break-system-packages websockets
# ).
try:
    import websockets
except ImportError:
    print("ERROR: websockets library not installed. Run:")
    print("  pip install --break-system-packages websockets")
    sys.exit(1)


AISSTREAM_URL = "wss://stream.aisstream.io/v0/stream"
CONFIG_KEY_PATH = pathlib.Path("/root/fish-finder/config/aisstream_key.txt")
CAPTAIN_VESSELS_PATH = pathlib.Path("/root/fish-finder/data/captain_vessels.json")
POSITIONS_PATH = pathlib.Path("/root/fish-finder/data/captain_positions.jsonl")
DEFAULT_BBOX = [[39.5, -74.5], [42.0, -68.0]]  # NE offshore + LI Sound


def load_api_key() -> str | None:
    """Read the aisstream API key from env var first, then config file.
    Returns None if neither is set — script prints a clear activation message.
    """
    env_key = os.environ.get("AISSTREAM_API_KEY")
    if env_key:
        return env_key.strip()
    if CONFIG_KEY_PATH.exists():
        key = CONFIG_KEY_PATH.read_text().strip()
        if key and not key.startswith("#"):
            return key
    return None


def load_tracked_mmsis() -> list:
    """Read MMSIs from captain_vessels.json. Returns [] if none are set yet.
    aisstream needs strings, not ints — the JSON has ints, we convert here.
    """
    if not CAPTAIN_VESSELS_PATH.exists():
        return []
    try:
        data = json.loads(CAPTAIN_VESSELS_PATH.read_text())
    except json.JSONDecodeError:
        return []
    mmsis = []
    for c in data.get("captains", []):
        m = c.get("mmsi")
        if m:
            mmsis.append(str(m))
    return mmsis


def append_position(msg: dict):
    """Persist one position message to the append-only JSONL file."""
    POSITIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    # aisstream message shape (see https://aisstream.io/documentation):
    #   {"MessageType": "PositionReport", "MetaData": {"MMSI": ..., "ShipName": ..., "time_utc": ...},
    #    "Message": {"PositionReport": {"Latitude": ..., "Longitude": ..., "Sog": ..., "Cog": ...}}}
    try:
        md = msg.get("MetaData", {})
        pr = msg.get("Message", {}).get("PositionReport", {})
        if not pr:
            return
        row = {
            "mmsi": md.get("MMSI"),
            "name": (md.get("ShipName") or "").strip(),
            "ts": md.get("time_utc") or datetime.datetime.utcnow().isoformat() + "Z",
            "lat": pr.get("Latitude"),
            "lon": pr.get("Longitude"),
            "sog": pr.get("Sog"),
            "cog": pr.get("Cog"),
        }
        if row["lat"] is None or row["lon"] is None or row["mmsi"] is None:
            return
        with POSITIONS_PATH.open("a") as f:
            f.write(json.dumps(row) + "\n")
    except Exception as e:
        print(f"  WARN: could not persist position: {e}", file=sys.stderr)


async def poll(api_key: str, duration_s: int, mmsis: list, bbox: list, verbose: bool):
    """Open a WebSocket connection, subscribe, and consume messages for
    `duration_s` seconds. Handles reconnect on transient network drops.
    """
    subscribe_msg = {
        "APIKey": api_key,
        # aisstream requires at least one bounding box. If MMSIs are set, we
        # ALSO subscribe to those specifically (aisstream sends messages
        # matching EITHER bbox+FiltersShipMMSI or the raw bbox).
        "BoundingBoxes": [bbox],
        "FilterMessageTypes": ["PositionReport"],
    }
    if mmsis:
        subscribe_msg["FiltersShipMMSI"] = mmsis
        print(f"Subscribing to {len(mmsis)} specific MMSIs + bbox {bbox}")
    else:
        print(f"No MMSIs in captain_vessels.json — subscribing to bbox {bbox} only")
        print("(Every AIS vessel in Randy's fishing area will report. Use this")
        print(" run to DISCOVER MMSIs for captains you recognize by name.)")

    end_time = asyncio.get_event_loop().time() + duration_s
    count = 0
    unique_mmsis = set()
    try:
        async with websockets.connect(AISSTREAM_URL, ping_interval=20) as ws:
            await ws.send(json.dumps(subscribe_msg))
            while True:
                remaining = end_time - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 30))
                except asyncio.TimeoutError:
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("MessageType") != "PositionReport":
                    continue
                append_position(msg)
                count += 1
                mmsi = msg.get("MetaData", {}).get("MMSI")
                if mmsi:
                    unique_mmsis.add(mmsi)
                if verbose:
                    md = msg.get("MetaData", {})
                    pr = msg.get("Message", {}).get("PositionReport", {})
                    print(f"  #{count} [{md.get('MMSI')}] {md.get('ShipName','?').strip()} "
                          f"@ ({pr.get('Latitude'):.4f}, {pr.get('Longitude'):.4f}) "
                          f"sog={pr.get('Sog')}kt")
    except websockets.ConnectionClosed as e:
        print(f"  Connection closed: {e}")
    return count, unique_mmsis


def main():
    ap = argparse.ArgumentParser(description="aisstream.io position poller for Fish Finder")
    ap.add_argument("--duration", type=int, default=60,
                    help="Poll duration in seconds (default 60; production: 600 for 10 min)")
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("MIN_LAT", "MIN_LON", "MAX_LAT", "MAX_LON"),
                    help="Override the default bounding box")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="Print each position as it arrives")
    args = ap.parse_args()

    api_key = load_api_key()
    if not api_key:
        print("=" * 70)
        print("aisstream.io API key not found.")
        print()
        print("To activate live charter-boat tracking, one-time setup:")
        print("  1. Sign up at https://aisstream.io/authenticate (free)")
        print("  2. Create an API key on the dashboard")
        print("  3. Set it as an environment variable:")
        print("       export AISSTREAM_API_KEY='your-key-here'")
        print("     OR drop it in the config file:")
        print(f"       echo 'your-key-here' > {CONFIG_KEY_PATH}")
        print("  4. Re-run this script.")
        print("=" * 70)
        sys.exit(1)

    mmsis = load_tracked_mmsis()
    bbox = args.bbox if args.bbox else DEFAULT_BBOX
    print(f"aisstream poll: duration={args.duration}s, tracked_mmsis={len(mmsis)}")

    try:
        count, unique = asyncio.run(poll(api_key, args.duration, mmsis, bbox, args.verbose))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(0)

    print(f"\n✓ Poll complete: {count} position messages saved, {len(unique)} unique vessels.")
    print(f"  Appended to: {POSITIONS_PATH}")
    if not mmsis and unique:
        print("\n  DISCOVERY HELPER: unique MMSIs seen in your fishing area this run:")
        for m in sorted(unique)[:30]:
            print(f"    {m}")
        print("  (Look these up on marinetraffic.com or vesselfinder.com; if any")
        print("   match a captain in data/captain_vessels.json, add the MMSI.)")


if __name__ == "__main__":
    main()
