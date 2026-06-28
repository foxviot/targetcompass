param(
  [string]$InstallDir = "$PSScriptRoot",
  [ValidateSet("doctor", "python", "nextflow", "docker", "rscript", "all")]
  [string]$Action = "doctor",
  [string]$Project = "vascular_aging_demo"
)

$ErrorActionPreference = "Stop"
$Python = Join-Path $InstallDir "runtime\python\python.exe"
$AppDir = Join-Path $InstallDir "app"
$LogDir = Join-Path $InstallDir "logs"

function Write-Step([string]$Message) {
  Write-Host "[TargetCompass V5 Repair] $Message"
}

function Ensure-App {
  if (!(Test-Path -LiteralPath $Python)) { throw "Embedded Python not found: $Python" }
  if (!(Test-Path -LiteralPath (Join-Path $AppDir "tc_lite.py"))) { throw "App not found: $AppDir" }
  if (!(Test-Path -LiteralPath $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
}

function Run-Doctor {
  Ensure-App
  Push-Location $AppDir
  try {
    & $Python "tc_lite.py" "v5-doctor" "--project" $Project
  } finally {
    Pop-Location
  }
}

function Repair-Python {
  Ensure-App
  $wheelhouse = Join-Path $InstallDir "wheelhouse"
  Push-Location $AppDir
  try {
    $wheelFiles = @()
    if (Test-Path -LiteralPath $wheelhouse) {
      $wheelFiles = @(Get-ChildItem -LiteralPath $wheelhouse -Filter "*.whl" -File -ErrorAction SilentlyContinue)
    }
    if ($wheelFiles.Count -gt 0) {
      & $Python -m pip install --no-index --find-links $wheelhouse "python-docx>=1.1.0"
    } else {
      & $Python -m pip install "python-docx>=1.1.0"
    }
    if ($LASTEXITCODE -ne 0) { throw "Python dependency repair failed" }
  } finally {
    Pop-Location
  }
}

function Repair-Nextflow {
  Ensure-App
  Push-Location $AppDir
  try {
    & $Python "tc_lite.py" "nextflow-bootstrap" "--project" $Project "--download" "--install-runtime"
  } finally {
    Pop-Location
  }
}

function Repair-Docker {
  Ensure-App
  Push-Location $AppDir
  try {
    & $Python "tc_lite.py" "local-backends-prepare" "--project" $Project
    Write-Step "If Docker Desktop is not running, start it now, then rerun this action."
    & $Python "tc_lite.py" "v5-backends-activate" "--project" $Project
  } finally {
    Pop-Location
  }
}

function Repair-Rscript {
  Write-Step "Rscript repair requires installing R 4.x system-wide or setting its path in the v5 Setup Wizard."
  Write-Step "Open the UI, go to Setup, and set the Rscript path after R is installed."
}

switch ($Action) {
  "doctor" { Run-Doctor }
  "python" { Repair-Python; Run-Doctor }
  "nextflow" { Repair-Nextflow; Run-Doctor }
  "docker" { Repair-Docker; Run-Doctor }
  "rscript" { Repair-Rscript; Run-Doctor }
  "all" { Repair-Python; Repair-Nextflow; Repair-Docker; Repair-Rscript; Run-Doctor }
}
