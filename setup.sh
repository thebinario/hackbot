#!/usr/bin/env bash
# Clones rag-cyber-frameworks and auto-bug-bounty as sibling folders of this
# repo, sets up a venv + dependencies for each, and pulls the Ollama models
# the RAG pipeline needs. Safe to re-run: skips any step already done.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(cd "$ROOT_DIR/.." && pwd)"

RAG_REPO="$PARENT_DIR/rag-cyber-frameworks"
RECON_REPO="$PARENT_DIR/auto-bug-bounty"

GIT_USER="${HACKBOT_GITHUB_USER:-thebinario}"

clone_if_missing() {
  local dir="$1" name="$2"
  if [ -d "$dir/.git" ]; then
    echo "[skip] $name already cloned at $dir"
  else
    echo "[clone] $name -> $dir"
    git clone "https://github.com/$GIT_USER/$name.git" "$dir"
  fi
}

setup_venv() {
  local dir="$1" name="$2"
  if [ ! -f "$dir/requirements.txt" ]; then
    echo "[warn] $name has no requirements.txt, skipping venv setup"
    return
  fi
  if [ ! -d "$dir/.venv" ]; then
    echo "[venv] creating $name/.venv"
    python3 -m venv "$dir/.venv"
  fi
  echo "[deps] installing $name requirements"
  "$dir/.venv/bin/pip" install --quiet --upgrade pip
  "$dir/.venv/bin/pip" install --quiet -r "$dir/requirements.txt"
}

echo "== Cloning sibling repos =="
clone_if_missing "$RAG_REPO" "rag-cyber-frameworks"
clone_if_missing "$RECON_REPO" "auto-bug-bounty"

echo "== Python environments =="
setup_venv "$RAG_REPO" "rag-cyber-frameworks"
setup_venv "$RECON_REPO" "auto-bug-bounty"

echo "== Ollama models =="
if ! command -v ollama >/dev/null 2>&1; then
  echo "[warn] Ollama not found on PATH. Install it from https://ollama.com and re-run this script."
else
  for model in qwen2.5:7b-instruct nomic-embed-text; do
    echo "[ollama] pulling $model"
    ollama pull "$model"
  done
fi

cat <<EOF

Setup finished. Layout:
  $PARENT_DIR/
    hackbot/                 (this repo)
    rag-cyber-frameworks/
    auto-bug-bounty/

Next: ./run_pipeline.sh --host <host> --program <program> --platform <hackerone|intigriti>
EOF
