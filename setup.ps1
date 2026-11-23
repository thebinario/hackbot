# Sets up hackbot itself (venv + deps), pulls the Ollama models it needs, and initializes
# the Git submodules under external/ (auto-bug-bounty + rag-cyber-frameworks). The recon
# side still lives in auto-bug-bounty until its hunt_rag.py bridge is validated enough to
# migrate into this repo too.

$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ReconRepo = Join-Path $RootDir "external\auto-bug-bounty"

Write-Host "== hackbot's own environment =="
$venv = Join-Path $RootDir ".venv"
if (-not (Test-Path $venv)) {
    Write-Host "[venv] creating .venv"
    python -m venv $venv
}
Write-Host "[deps] installing requirements.txt"
& "$venv\Scripts\pip.exe" install --quiet --upgrade pip
& "$venv\Scripts\pip.exe" install --quiet -r (Join-Path $RootDir "requirements.txt")

Write-Host "== Ollama models =="
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Host "[warn] Ollama not found on PATH. Install it from https://ollama.com and re-run this script."
} else {
    foreach ($model in @("qwen2.5:7b-instruct", "nomic-embed-text")) {
        Write-Host "[ollama] pulling $model"
        ollama pull $model
    }
}

Write-Host "== Submodules (external/) =="
git -C $RootDir submodule update --init --recursive
$reconReq = Join-Path $ReconRepo "requirements.txt"
if (Test-Path $reconReq) {
    $reconVenv = Join-Path $ReconRepo ".venv"
    if (-not (Test-Path $reconVenv)) {
        python -m venv $reconVenv
    }
    & "$reconVenv\Scripts\pip.exe" install --quiet --upgrade pip
    & "$reconVenv\Scripts\pip.exe" install --quiet -r $reconReq
}

Write-Host ""
Write-Host "Setup finished."
Write-Host "  $RootDir          <- hackbot (RAG de writeups, roda sozinho)"
Write-Host "  $ReconRepo  <- recon experimental (submodulo), chama o hackbot via HTTP local"
Write-Host ""
Write-Host "Proximo passo: .\run_pipeline.ps1 -TargetHost <host> -Program <program> -Platform <hackerone|intigriti>"
