param(
  [string]$Project = "vascular_aging_demo",
  [int]$Port = 8781
)

$ErrorActionPreference = "Stop"
$InstallDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $InstallDir "runtime\python\python.exe"
$AppDir = Join-Path $InstallDir "app"
$LogDir = Join-Path $InstallDir "logs"
$OutLog = Join-Path $LogDir "targetcompass.out.log"
$ErrLog = Join-Path $LogDir "targetcompass.err.log"

if (!(Test-Path -LiteralPath $Python)) {
  throw "Python runtime not found: $Python"
}
if (!(Test-Path -LiteralPath (Join-Path $AppDir "tc_lite.py"))) {
  throw "TargetCompass app not found: $AppDir"
}
if (!(Test-Path -LiteralPath $LogDir)) {
  New-Item -ItemType Directory -Path $LogDir | Out-Null
}

$existing = Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like "*tc_lite.py serve*" -and $_.CommandLine -like "*--port $Port*" } |
  Select-Object -First 1

if (!$existing) {
  Start-Process -FilePath $Python `
    -ArgumentList @("tc_lite.py", "serve", "--project", $Project, "--port", "$Port") `
    -WorkingDirectory $AppDir `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -WindowStyle Hidden
  Start-Sleep -Seconds 3
}

$url = "http://127.0.0.1:$Port/"
Start-Process $url
Write-Host "TargetCompass V4 is running: $url"
Write-Host "Logs: $LogDir"
