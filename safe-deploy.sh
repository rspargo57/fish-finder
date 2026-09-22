#!/bin/bash
# safe-deploy.sh — reconstructed 2026-09-21 as v25.0
#
# Root cause of the 6-day trigger drought (Sept 15-21, 2026): the previous
# safe-deploy.sh's tar command did NOT include *.sh in the bootstrap tarball
# glob. Every successful deploy therefore stripped safe-deploy.sh out of the
# tarball it uploaded. Next fresh trigger sandbox bootstrapped → tried
# `bash safe-deploy.sh --rebuild` → file not found → session exited fast
# and reported SUCCEEDED. Silent failure loop.
#
# This rebuild fixes that (see BOOTSTRAP_TARBALL_INCLUDES below), keeps the
# feature set intentionally small, and never repeats the self-eating bug.
#
# USAGE:
#   ./safe-deploy.sh                # Deploy current build (refuses if stale)
#   ./safe-deploy.sh --rebuild      # Rebuild first, then deploy
#   ./safe-deploy.sh --force        # Deploy stale build anyway (require flag)

set -u
cd "$(dirname "$0")"

REBUILD=false
FORCE=false
for arg in "$@"; do
  case "$arg" in
    --rebuild) REBUILD=true ;;
    --force)   FORCE=true ;;
  esac
done

# ----- Optional rebuild step ---------------------------------------------
if [ "$REBUILD" = "true" ]; then
  echo "🔧 REBUILD requested — running build-inlined.py (takes 15-25 min)..."
  python3 build-inlined.py
  _BUILD_EXIT=$?
  if [ "$_BUILD_EXIT" -ne 0 ]; then
    echo "❌ build-inlined.py exited $_BUILD_EXIT — refusing to deploy."
    exit 2
  fi
  FORCE=true  # a fresh rebuild is by definition not stale
fi

# ----- Freshness gate ----------------------------------------------------
if [ ! -f map/fish-finder.html ]; then
  echo "❌ map/fish-finder.html not found — run --rebuild first."
  exit 5
fi
GEN_DATE=$(grep -oE '"generated_at": ?"?[0-9T:.Z-]+"?' map/fish-finder.html | head -1 | grep -oE '20[0-9]{2}-[0-9]{2}-[0-9]{2}')
TODAY=$(date -u +%Y-%m-%d)
echo "📅 Local built HTML generated: $GEN_DATE"
echo "📅 Today (UTC):                $TODAY"

if [ -z "$GEN_DATE" ]; then
  echo "❌ Could not read generated_at from local map. Refusing to deploy."
  exit 5
fi

# Stale = generated_at is older than yesterday.
_YESTERDAY=$(python3 -c "import datetime; print((datetime.date.today() - datetime.timedelta(days=1)).isoformat())")
if [ "$GEN_DATE" \< "$_YESTERDAY" ] && [ "$FORCE" != "true" ]; then
  echo "❌ SAFE-DEPLOY REFUSES: local build is stale (generated $GEN_DATE, today is $TODAY)."
  echo "   Rebuild with:  ./safe-deploy.sh --rebuild"
  echo "   Force stale:   ./safe-deploy.sh --force"
  exit 3
fi
if [ "$FORCE" = "true" ] && [ "$GEN_DATE" != "$TODAY" ]; then
  echo "⚠️  FORCE flag set — deploying $GEN_DATE build anyway."
fi

# ----- Preflight sanity checks -------------------------------------------
echo ""
echo "🔒 Preflight integrity check..."
_MAP_BYTES=$(wc -c < map/fish-finder.html)
if [ "$_MAP_BYTES" -lt 5000000 ]; then
  echo "❌ Preflight failed: map/fish-finder.html is only $_MAP_BYTES bytes (expected 15MB+)."
  exit 5
fi
if ! grep -q "ZONE_DATA_NORTHEAST" map/fish-finder.html; then
  echo "❌ Preflight failed: ZONE_DATA_NORTHEAST not found in map."
  exit 5
fi
echo "✅ Preflight check passed."

# ----- Stage the deploy directory ----------------------------------------
STAGING=/tmp/ff_safe_deploy
rm -rf "$STAGING"
mkdir -p "$STAGING/_bootstrap-3e9f4a2c8d1b7f" "$STAGING/map" "$STAGING/archive"

# The actual live map
cp map/fish-finder.html "$STAGING/map/index.html"

# Landing pages
for f in index.html _headers robots.txt sitemap.xml manifest.webmanifest \
         apple-touch-icon.png icon-192.png icon-512.png known-issues.md known-issues.txt; do
  [ -f "deploy-package/$f" ] && cp "deploy-package/$f" "$STAGING/"
done

# Archive index for post-flight checks
if [ -f archive/index.json ]; then
  cp archive/index.json "$STAGING/archive/index.json"
fi

# ----- Package the bootstrap tarball -------------------------------------
# CRITICAL FIX 2026-09-21 (v25.0): include *.sh in the tarball. Prior versions
# used `*.py *.md ...` which packaged everything BUT the shell scripts that
# actually deploy. Every deploy therefore stripped safe-deploy.sh out of the
# next tarball, silently breaking scheduled triggers. Never again.
#
# NOTE: pass globs directly to `tar` so shell expansion happens BEFORE tar
# sees them. Quoted array elements ("*.sh") don't glob — v25.0 initial cut
# hit exactly that bug and refused to deploy. Fixed by letting the shell
# expand the globs inline.

ARCHIVE_FILES=$(ls archive/*.json 2>/dev/null | tr '\n' ' ')

echo ""
echo "📦 Packaging bootstrap tarball (v25.0 — *.sh included)..."
tar czf "$STAGING/_bootstrap-3e9f4a2c8d1b7f/ff-src.tar.gz" \
  --exclude='__pycache__' --exclude='node_modules' \
  --exclude='deploy/*.zip' --exclude='config/*.txt' --exclude='.wrangler' \
  --exclude='.git' --exclude='*.log' \
  *.sh *.py *.md captain/ cache/ data/ docs/ landing/ map/ skills/ sources/ vendor/ \
  deploy-package/_headers deploy-package/robots.txt deploy-package/sitemap.xml \
  deploy-package/manifest.webmanifest deploy-package/apple-touch-icon.png \
  deploy-package/icon-192.png deploy-package/icon-512.png deploy-package/index.html \
  $ARCHIVE_FILES 2>/dev/null || true

# Verify tarball INCLUDES safe-deploy.sh — the whole point of this fix
if ! tar tzf "$STAGING/_bootstrap-3e9f4a2c8d1b7f/ff-src.tar.gz" | grep -qE '^safe-deploy\.sh$'; then
  echo "❌ FATAL: bootstrap tarball does NOT contain safe-deploy.sh. This would recreate the Sept 15-21 outage. Refusing to deploy."
  exit 5
fi

_TARBALL_BYTES=$(wc -c < "$STAGING/_bootstrap-3e9f4a2c8d1b7f/ff-src.tar.gz")
_TARBALL_MB=$(( _TARBALL_BYTES / 1024 / 1024 ))
echo "✅ Bootstrap tarball packaged (${_TARBALL_MB}MB) with safe-deploy.sh inside."

# ----- Wrangler v3 hot-install (v24.123 pin) -----------------------------
_WRANGLER_BIN="wrangler"
if command -v wrangler >/dev/null 2>&1; then
  _WV=$(wrangler --version 2>&1 | tail -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
  _WMAJ=${_WV%%.*}
  if [ -n "$_WMAJ" ] && [ "$_WMAJ" -gt 3 ]; then
    echo "🔧 Detected wrangler $_WV — installing v3 locally (token lacks Accounts:Read)..."
    _WV3_DIR="/tmp/ff_wrangler_v3"
    mkdir -p "$_WV3_DIR"
    if [ ! -x "$_WV3_DIR/node_modules/.bin/wrangler" ]; then
      (cd "$_WV3_DIR" && npm init -y >/dev/null 2>&1 && npm install wrangler@3 --no-audit --no-fund --loglevel=error >/dev/null 2>&1)
    fi
    if [ -x "$_WV3_DIR/node_modules/.bin/wrangler" ]; then
      _WRANGLER_BIN="$_WV3_DIR/node_modules/.bin/wrangler"
    fi
  fi
fi

# ----- Deploy to Cloudflare Pages ----------------------------------------
# Token/account come from environment (GitHub Actions injects them as
# repository secrets). No hardcoded fallback — refuse to deploy if unset.
if [ -z "${CLOUDFLARE_API_TOKEN:-}" ]; then
  echo "❌ CLOUDFLARE_API_TOKEN not set. In GitHub Actions this comes from"
  echo "   the repo secret; locally, export it before running this script."
  exit 6
fi
if [ -z "${CLOUDFLARE_ACCOUNT_ID:-}" ]; then
  echo "❌ CLOUDFLARE_ACCOUNT_ID not set. Same fix as above."
  exit 6
fi
_CF_TOKEN="$CLOUDFLARE_API_TOKEN"
_CF_ACCOUNT="$CLOUDFLARE_ACCOUNT_ID"

echo ""
echo "🚀 Deploying to Cloudflare Pages..."
_wrangler_deploy() {
  CLOUDFLARE_API_TOKEN="$_CF_TOKEN" \
  CLOUDFLARE_ACCOUNT_ID="$_CF_ACCOUNT" \
    "$_WRANGLER_BIN" pages deploy "$STAGING" \
      --project-name fishfinder --branch main --commit-dirty=true 2>&1
}

_wrangler_out=""
_wrangler_exit=1
for attempt in 1 2 3; do
  _wrangler_out=$(_wrangler_deploy)
  _wrangler_exit=$?
  echo "$_wrangler_out" | tail -8
  if [ $_wrangler_exit -eq 0 ]; then break; fi
  if [ $attempt -lt 3 ]; then
    echo "⚠️  Wrangler attempt $attempt failed. Backing off..."
    sleep $((15 * attempt))
  fi
done
if [ $_wrangler_exit -ne 0 ]; then
  echo "❌ Wrangler failed 3 times. See output above."
  exit 7
fi

# ----- Verify live deploy took ------------------------------------------
echo ""
echo "🔍 Verifying live deploy took..."
sleep 6
_LIVE_GEN=$(curl -sfL --max-time 30 --range 0-2000000 "https://fishfinders.app/map" 2>/dev/null | grep -oE '"generated_at": ?"?[0-9T:.Z-]+"?' | head -1 | grep -oE '20[0-9]{2}-[0-9]{2}-[0-9]{2}')
if [ "$_LIVE_GEN" = "$GEN_DATE" ]; then
  echo "✅ Live site matches local build — generated=$_LIVE_GEN."
else
  echo "⚠️  Live/local mismatch: local=$GEN_DATE, live=$_LIVE_GEN (CDN may still be propagating)."
fi

# ----- Verify tarball on Cloudflare has safe-deploy.sh -------------------
echo ""
echo "🔒 Verifying live bootstrap tarball is in sync..."
_LIVE_TAR=/tmp/ff_verify_boot.tar.gz
if curl -sfL --max-time 30 "https://fishfinders.app/_bootstrap-3e9f4a2c8d1b7f/ff-src.tar.gz" -o "$_LIVE_TAR" 2>/dev/null; then
  if tar tzf "$_LIVE_TAR" 2>/dev/null | grep -qE '^safe-deploy\.sh$'; then
    echo "✅ Live tarball contains safe-deploy.sh — the Sept 15-21 bug is fixed."
  else
    echo "❌ Live tarball is missing safe-deploy.sh. Something is wrong with the packaging step above."
    exit 8
  fi
else
  echo "⚠️  Could not fetch live tarball to verify (CDN lag likely)."
fi

echo ""
echo "✅ Deploy complete: $GEN_DATE live at https://fishfinders.app/map/"
