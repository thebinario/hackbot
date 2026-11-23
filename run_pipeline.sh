#!/usr/bin/env bash
# Runs the full local hackbot pipeline for one host:
#   1. starts the writeups API (rag-cyber-frameworks) in the background, if not already up
#   2. runs hunt.py (auto-bug-bounty) to build hunt_profile.json from existing recon results
#   3. runs hunt_rag.py (auto-bug-bounty) to get writeup-grounded hypotheses from the local API
#
# All network traffic stays on 127.0.0.1. This script never scans the target host itself —
# it only reads recon results that auto-bug-bounty's own CI already committed.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(cd "$ROOT_DIR/.." && pwd)"
RECON_REPO="$PARENT_DIR/auto-bug-bounty"

API_URL="${HACKBOT_API_URL:-http://127.0.0.1:8765}"
API_HEALTH="$API_URL/health"

HOST=""
PROGRAM=""
PLATFORM=""
TOP_K="4"

usage() {
  echo "Usage: $0 --host <host> --program <program> --platform <hackerone|intigriti> [--top-k N]"
  exit 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --program) PROGRAM="$2"; shift 2 ;;
    --platform) PLATFORM="$2"; shift 2 ;;
    --top-k) TOP_K="$2"; shift 2 ;;
    *) usage ;;
  esac
done

[ -z "$HOST" ] || [ -z "$PROGRAM" ] || [ -z "$PLATFORM" ] && usage

if [ ! -d "$RECON_REPO" ]; then
  echo "[error] expected sibling repo missing: $RECON_REPO (run ./setup.sh first)" >&2
  exit 1
fi

echo "== Step 1/3: writeups API (this repo) =="
if curl --silent --fail --max-time 2 "$API_HEALTH" >/dev/null 2>&1; then
  echo "[ok] API already running at $API_URL"
else
  echo "[start] launching serve_api.py in the background"
  (cd "$ROOT_DIR" && nohup .venv/bin/python scripts/serve_api.py >/tmp/hackbot_api.log 2>&1 &)
  for _ in $(seq 1 30); do
    sleep 1
    if curl --silent --fail --max-time 2 "$API_HEALTH" >/dev/null 2>&1; then
      echo "[ok] API is up"
      break
    fi
  done
  if ! curl --silent --fail --max-time 2 "$API_HEALTH" >/dev/null 2>&1; then
    echo "[error] API did not come up in time, check /tmp/hackbot_api.log" >&2
    exit 1
  fi
fi

echo "== Step 2/3: hunt.py =="
(cd "$RECON_REPO" && .venv/bin/python scripts/recon/hunt.py --host "$HOST" --program "$PROGRAM" --platform "$PLATFORM")

echo "== Step 3/3: hunt_rag.py =="
(cd "$RECON_REPO" && .venv/bin/python scripts/recon/hunt_rag.py --host "$HOST" --program "$PROGRAM" --platform "$PLATFORM" --api-url "$API_URL" --top-k "$TOP_K")

echo ""
echo "Done. See:"
echo "  $RECON_REPO/results/$PROGRAM/$HOST/hunt_rag_analysis.md"
