# Sets up hackbot itself (venv + deps), pulls the Ollama models it needs, and clones
# auto-bug-bounty as a sibling folder -- the recon side still lives there until its
# hunt_rag.py bridge is validated enough to migrate into this repo too.

$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ParentDir = Split-Path -Parent $RootDir
$ReconRepo = Join-Path $ParentDir "auto-bug-bounty"
$GitUser = if ($env:HACKBOT_GITHUB_USER) { $env:HACKBOT_GITHUB_USER } else { "thebinario" }

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

Write-Host "== Recon lab (auto-bug-bounty, still experimental) =="
if (Test-Path (Join-Path $ReconRepo ".git")) {
    Write-Host "[skip] auto-bug-bounty already cloned at $ReconRepo"
} else {
    Write-Host "[clone] auto-bug-bounty -> $ReconRepo"
    git clone "https://github.com/$GitUser/auto-bug-bounty.git" $ReconRepo
}
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
Write-Host "  $ReconRepo  <- recon experimental, chama o hackbot via HTTP local"
Write-Host ""
Write-Host "Proximo passo: .\run_pipeline.ps1 -TargetHost <host> -Program <program> -Platform <hackerone|intigriti>"
