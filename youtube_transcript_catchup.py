#!/usr/bin/env python3
"""v24.111 — YouTube transcript catch-up (runs on Randy's local machine).

**Why this exists.** Cloud sandboxes (like the one that runs the 6pm nightly)
are IP-blocked by YouTube's transcript API — every fetch returns `IpBlocked`.
Randy's home IP is a residential connection YouTube doesn't block. Solution:
run the transcript fetching FROM RANDY'S COMPUTER via a device-bound scheduled
task instead of from the cloud nightly.

**How it fits.**
  1. The 6pm CLOUD nightly (`build-inlined.py`) discovers up to ~50-60 videos
     across ~35 fishing channels via RSS feeds (no auth needed for those).
     RSS gives us title + description + video_id. Zone/species/bait extraction
     runs on that text alone — that's what the model has been using.

  2. THIS SCRIPT runs on Randy's DEVICE (per a device-bound scheduled task)
     each morning at ~8:30am ET when his laptop is likely on. It:
       - Reads the latest archive snapshot's youtube_full_corpus for video_ids
       - For each video without a usable cached transcript, calls
         youtube-transcript-api from his home IP
       - Writes the transcript to cache/yt_transcripts/{video_id}.json
       - Cache is durable — the next 6pm nightly's transcript re-extract picks it up

  3. On the next 6pm cloud nightly, when build-inlined.py rebuilds the YouTube
     corpus, it will now find populated transcript caches. The extraction runs
     transcript-first (already coded in youtube_harvest.py at v23.12; just
     nothing was ever making it past the IpBlocked wall until now).

**Idempotent.** Only fetches videos where the cache entry is missing OR marked
`unavailable: True` AND is older than 7 days (in case YouTube's transcript
availability changed). Never re-fetches a successful cache hit.

**Failure modes.**
  - If it's ALSO running on a cloud IP, it'll IP-block just like the nightly.
    In that case it logs the failure and exits without touching the cache
    (so it doesn't overwrite prior successful hits with negative entries).
  - If it can't reach YouTube at all (offline), it exits cleanly.
  - If the archive is stale (no fresh nightly snapshot), it walks back up to
    3 snapshots to find one with a corpus to work on.

**Usage:** just `python3 youtube_transcript_catchup.py`. Optionally
`--dry-run` to see what it WOULD fetch without touching the cache.
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys
import time

BASE = pathlib.Path(__file__).parent
ARCHIVE_DIR = BASE / "archive"
CACHE_DIR = BASE / "cache" / "yt_transcripts"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 40K char cap on stored transcripts (matches youtube_harvest.py's cap so we
# don't blow archive sizes with 90-min video transcripts).
_TRANSCRIPT_CAP = 40_000

# Only re-try a "unavailable" cache entry if it's older than this
_RETRY_STALE_DAYS = 7


def _latest_archive_with_corpus() -> tuple[str, dict] | tuple[None, None]:
    """Walk back up to 5 snapshots to find one that carries a youtube corpus."""
    files = sorted(ARCHIVE_DIR.glob("2*.json"))
    for p in reversed(files[-5:]):
        try:
            d = json.loads(p.read_text())
            if ((d.get("raw_intel") or {}).get("youtube_full_corpus") or {}).get("channels"):
                return p.stem, d
        except Exception:
            continue
    return None, None


def _load_video_ids(snap: dict) -> list[tuple[str, str, str]]:
    """Extract (video_id, title, channel) from every channel's all_videos list."""
    chans = ((snap.get("raw_intel") or {}).get("youtube_full_corpus") or {}).get("channels", [])
    out = []
    for ch in chans:
        chan_name = ch.get("name") or ch.get("id") or "?"
        for v in ch.get("all_videos", []) or []:
            vid = v.get("video_id")
            if vid:
                out.append((vid, (v.get("title") or "")[:80], chan_name))
    return out


def _cache_status(video_id: str) -> tuple[str, dict | None]:
    """Return (status, cached_dict) where status is one of:
       'usable'       — has non-empty text
       'unavailable'  — marked unavailable, not stale
       'stale_retry'  — marked unavailable but old enough to retry
       'missing'      — no cache file
    """
    fp = CACHE_DIR / f"{video_id}.json"
    if not fp.exists():
        return "missing", None
    try:
        d = json.loads(fp.read_text())
    except Exception:
        return "missing", None
    if d.get("text") and len(d["text"]) > 100:
        return "usable", d
    if d.get("unavailable"):
        # Check age of the miss
        fetched = d.get("fetched_at", "")
        try:
            when = datetime.datetime.fromisoformat(fetched.replace("Z", "+00:00"))
            age_days = (datetime.datetime.now(datetime.timezone.utc) - when).days
            if age_days > _RETRY_STALE_DAYS:
                return "stale_retry", d
        except Exception:
            pass
        return "unavailable", d
    return "missing", d


def _fetch_transcript(video_id: str) -> dict:
    """Blocking transcript fetch. Returns a dict shaped like our cache format."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return {
            "video_id": video_id,
            "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "unavailable": True,
            "error": "youtube-transcript-api not installed",
        }
    try:
        ytt = YouTubeTranscriptApi()
        transcript = ytt.fetch(video_id)
        text = " ".join(seg.text for seg in transcript.snippets)
        if len(text) > _TRANSCRIPT_CAP:
            text = text[:_TRANSCRIPT_CAP] + " …[truncated]"
        return {
            "video_id": video_id,
            "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "text": text,
            "chars": len(text),
        }
    except Exception as e:
        return {
            "video_id": video_id,
            "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "unavailable": True,
            "error": f"{type(e).__name__}: {str(e)[:200]}",
        }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="show what would fetch, no writes")
    ap.add_argument("--limit", type=int, default=100, help="max videos to fetch this run")
    ap.add_argument("--delay", type=float, default=3.0, help="seconds between fetches to be polite (v24.112: 3s default — reduces YouTube rate-limit trips on residential IPs)")
    ap.add_argument("--cooldown", type=float, default=90.0, help="seconds to sleep after an IP-block before trying ONE more fetch as a probe")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    date, snap = _latest_archive_with_corpus()
    if not snap:
        print("No archive snapshot with a youtube_full_corpus found in the last 5. Nothing to catch up on.")
        return 0

    ids = _load_video_ids(snap)
    print(f"Loaded {len(ids)} video_ids from archive/{date}.json")
    if not ids:
        return 0

    stats = {"usable": 0, "unavailable": 0, "stale_retry": 0, "missing": 0}
    to_fetch = []
    for vid, title, chan in ids:
        status, _ = _cache_status(vid)
        stats[status] += 1
        if status in ("missing", "stale_retry"):
            to_fetch.append((vid, title, chan))

    print(f"Cache health: {stats}")
    print(f"Will attempt: {len(to_fetch)} fetches (limited to {args.limit})")
    if args.dry_run:
        for vid, title, chan in to_fetch[:10]:
            print(f"  [DRY] {vid}  {chan[:30]:30}  {title[:60]}")
        return 0

    to_fetch = to_fetch[: args.limit]
    if not to_fetch:
        print("Nothing to fetch. Cache is up to date.")
        return 0

    ok = 0
    fail = 0
    ip_blocked_hit = False
    cooldown_used = False  # v24.112: allow ONE cooldown-retry per run
    i = 0
    while i < len(to_fetch):
        vid, title, chan = to_fetch[i]
        i += 1
        if args.verbose:
            print(f"  [{i}/{len(to_fetch)}] fetching {vid} ({chan[:30]}) — {title[:50]}")
        result = _fetch_transcript(vid)
        fp = CACHE_DIR / f"{vid}.json"
        err = (result.get("error") or "").lower()

        if "ipblocked" in err or "ip blocked" in err or "429" in err:
            # v24.112: don't hard-stop on the first IpBlock. YouTube rate-limits
            # burst residential traffic too — a 60-90s pause usually lets us
            # resume the batch. But only try the cooldown ONCE per run: if the
            # second block hits, YouTube has decided our IP is throttled for
            # real and further attempts just waste time + risk a longer ban.
            if not cooldown_used:
                cooldown_used = True
                print(f"  ⏸  IP-block at video {i}/{len(to_fetch)} — cooling down {args.cooldown:.0f}s then retrying...")
                time.sleep(args.cooldown)
                # Retry this same video after cooldown
                i -= 1
                continue
            ip_blocked_hit = True
            print(f"  🚫 Second IP-block after cooldown — stopping to preserve cache.")
            print(f"     Tomorrow's run will pick up where this one left off (idempotent).")
            break

        fp.write_text(json.dumps(result, indent=2))
        if result.get("text"):
            ok += 1
            if args.verbose:
                print(f"     ✓ {result['chars']} chars")
        else:
            fail += 1
            if args.verbose:
                print(f"     × {result.get('error','?')[:60]}")
        time.sleep(args.delay)

    total = ok + fail
    print()
    print(f"Attempted: {total}  ·  Usable: {ok}  ·  Unavailable: {fail}")
    if ip_blocked_hit:
        # v24.112: what we DID fetch before the block is safely cached — only the
        # attempts AFTER the block were skipped. Report both so the caller knows
        # exactly where we stand.
        remaining = len(to_fetch) - i
        print(f"⚠  IP-blocked exit. {ok} usable this run · {remaining} unattempted (tomorrow's run picks them up).")
        return 2
    if total > 0:
        pct = ok / total * 100
        print(f"Hit rate: {pct:.0f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
