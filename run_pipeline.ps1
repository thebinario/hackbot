# Runs the full local hackbot pipeline for one host:
#   1. starts the writeups API (rag-cyber-frameworks) in the background, if not already up
#   2. runs hunt.py (auto-bug-bounty) to build hunt_profile.json from existing recon results
#   3. runs hunt_rag.py (auto-bug-bounty) to get writeup-grounded hypotheses from the local API
#
# All network traffic stays on 127.0.0.1. This script never scans the target host itself --
# it only reads recon results that auto-bug-bounty's own CI already committed.

param(
    [Parameter(Mandatory = $true)]
    [string]$TargetHost,

    [Parameter(Mandatory = $true)]
    [string]$Program,

    [Parameter(Mandatory = $true)]
    [ValidateSet("hackerone", "intigriti")]
    [string]$Platform,

    [int]$TopK = 4,

    [string]$ApiUrl = "http://127.0.0.1:8765"
)

$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ReconRepo = Join-Path $RootDir "external\auto-bug-bounty"

if (-not (Test-Path $ReconRepo)) {
    Write-Error "Expected submodule missing: $ReconRepo (run .\setup.ps1 or 'git submodule update --init --recursive')"
    exit 1
}

function Test-ApiHealth {
    try {
        Invoke-RestMethod -Uri "$ApiUrl/health" -TimeoutSec 2 | Out-Null
        return $true
    } catch {
        return $false
    }
}

Write-Host "== Step 1/3: writeups API (this repo) =="
if (Test-ApiHealth) {
    Write-Host "[ok] API already running at $ApiUrl"
} else {
    Write-Host "[start] launching serve_api.py in the background"
    $pythonExe = Join-Path $RootDir ".venv\Scripts\python.exe"
    Start-Process -FilePath $pythonExe -ArgumentList "scripts\serve_api.py" -WorkingDirectory $RootDir -WindowStyle Hidden
    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        if (Test-ApiHealth) { $ready = $true; break }
    }
    if (-not $ready) {
        Write-Error "API did not come up in time."
        exit 1
    }
    Write-Host "[ok] API is up"
}

Write-Host "== Step 2/3: hunt.py =="
& "$ReconRepo\.venv\Scripts\python.exe" "$ReconRepo\scripts\recon\hunt.py" --host $TargetHost --program $Program --platform $Platform

Write-Host "== Step 3/3: hunt_rag.py =="
& "$ReconRepo\.venv\Scripts\python.exe" "$ReconRepo\scripts\recon\hunt_rag.py" --host $TargetHost --program $Program --platform $Platform --api-url $ApiUrl --top-k $TopK

Write-Host ""
Write-Host "Done. See:"
Write-Host "  $ReconRepo\results\$Program\$TargetHost\hunt_rag_analysis.md"
