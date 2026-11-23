# Clones rag-cyber-frameworks and auto-bug-bounty as sibling folders of this
# repo, sets up a venv + dependencies for each, and pulls the Ollama models
# the RAG pipeline needs. Safe to re-run: skips any step already done.

$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ParentDir = Split-Path -Parent $RootDir

$RagRepo = Join-Path $ParentDir "rag-cyber-frameworks"
$ReconRepo = Join-Path $ParentDir "auto-bug-bounty"

$GitUser = if ($env:HACKBOT_GITHUB_USER) { $env:HACKBOT_GITHUB_USER } else { "thebinario" }

function Clone-IfMissing($Dir, $Name) {
    if (Test-Path (Join-Path $Dir ".git")) {
        Write-Host "[skip] $Name already cloned at $Dir"
    } else {
        Write-Host "[clone] $Name -> $Dir"
        git clone "https://github.com/$GitUser/$Name.git" $Dir
    }
}

function Setup-Venv($Dir, $Name) {
    $req = Join-Path $Dir "requirements.txt"
    if (-not (Test-Path $req)) {
        Write-Host "[warn] $Name has no requirements.txt, skipping venv setup"
        return
    }
    $venv = Join-Path $Dir ".venv"
    if (-not (Test-Path $venv)) {
        Write-Host "[venv] creating $Name\.venv"
        python -m venv $venv
    }
    Write-Host "[deps] installing $Name requirements"
    & "$venv\Scripts\pip.exe" install --quiet --upgrade pip
    & "$venv\Scripts\pip.exe" install --quiet -r $req
}

Write-Host "== Cloning sibling repos =="
Clone-IfMissing $RagRepo "rag-cyber-frameworks"
Clone-IfMissing $ReconRepo "auto-bug-bounty"

Write-Host "== Python environments =="
Setup-Venv $RagRepo "rag-cyber-frameworks"
Setup-Venv $ReconRepo "auto-bug-bounty"

Write-Host "== Ollama models =="
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Host "[warn] Ollama not found on PATH. Install it from https://ollama.com and re-run this script."
} else {
    foreach ($model in @("qwen2.5:7b-instruct", "nomic-embed-text")) {
        Write-Host "[ollama] pulling $model"
        ollama pull $model
    }
}

Write-Host ""
Write-Host "Setup finished. Layout:"
Write-Host "  $ParentDir\"
Write-Host "    hackbot\                 (this repo)"
Write-Host "    rag-cyber-frameworks\"
Write-Host "    auto-bug-bounty\"
Write-Host ""
Write-Host "Next: .\run_pipeline.ps1 -TargetHost <host> -Program <program> -Platform <hackerone|intigriti>"
