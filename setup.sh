#!/usr/bin/env bash
# Sets up hackbot itself (venv + deps), pulls the Ollama models it needs, and clones
# auto-bug-bounty as a sibling folder — the recon side still lives there until its
# hunt_rag.py bridge is validated enough to migrate into this repo too.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(cd "$ROOT_DIR/.." && pwd)"
RECON_REPO="$PARENT_DIR/auto-bug-bounty"
GIT_USER="${HACKBOT_GITHUB_USER:-thebinario}"

echo "== hackbot's own environment =="
if [ ! -d "$ROOT_DIR/.venv" ]; then
  echo "[venv] creating .venv"
  python3 -m venv "$ROOT_DIR/.venv"
fi
echo "[deps] installing requirements.txt"
"$ROOT_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$ROOT_DIR/.venv/bin/pip" install --quiet -r "$ROOT_DIR/requirements.txt"

echo "== Ollama models =="
if ! command -v ollama >/dev/null 2>&1; then
  echo "[warn] Ollama not found on PATH. Install it from https://ollama.com and re-run this script."
else
  for model in qwen2.5:7b-instruct nomic-embed-text; do
    echo "[ollama] pulling $model"
    ollama pull "$model"
  done
fi

echo "== Recon lab (auto-bug-bounty, still experimental) =="
if [ -d "$RECON_REPO/.git" ]; then
  echo "[skip] auto-bug-bounty already cloned at $RECON_REPO"
else
  echo "[clone] auto-bug-bounty -> $RECON_REPO"
  git clone "https://github.com/$GIT_USER/auto-bug-bounty.git" "$RECON_REPO"
fi
if [ -f "$RECON_REPO/requirements.txt" ]; then
  if [ ! -d "$RECON_REPO/.venv" ]; then
    python3 -m venv "$RECON_REPO/.venv"
  fi
  "$RECON_REPO/.venv/bin/pip" install --quiet --upgrade pip
  "$RECON_REPO/.venv/bin/pip" install --quiet -r "$RECON_REPO/requirements.txt"
fi

cat <<EOF

Setup finished.
  $ROOT_DIR          <- hackbot (RAG de writeups, roda sozinho)
  $RECON_REPO  <- recon experimental, chama o hackbot via HTTP local

Próximo passo: ./run_pipeline.sh --host <host> --program <program> --platform <hackerone|intigriti>
EOF
