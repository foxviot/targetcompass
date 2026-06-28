param(
  [switch]$Quiet
)

$ErrorActionPreference = "Stop"
$InstallDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ShortcutDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\TargetCompass V4"
$DesktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "TargetCompass V4.lnk"

if (!$Quiet) {
  Write-Host "This will uninstall TargetCompass V4 from:"
  Write-Host "  $InstallDir"
  $answer = Read-Host "Continue? [y/N]"
  if ($answer -notin @("y", "Y", "yes", "YES")) {
    Write-Host "Uninstall cancelled."
    exit 0
  }
}

Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like "*tc_lite.py serve*" -and $_.CommandLine -like "*$InstallDir*" } |
  ForEach-Object {
    try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {}
  }

if (Test-Path -LiteralPath $ShortcutDir) {
  Remove-Item -LiteralPath $ShortcutDir -Recurse -Force
}
if (Test-Path -LiteralPath $DesktopShortcut) {
  Remove-Item -LiteralPath $DesktopShortcut -Force
}
if (Test-Path -LiteralPath $InstallDir) {
  Remove-Item -LiteralPath $InstallDir -Recurse -Force
}

Write-Host "TargetCompass V4 has been uninstalled."
