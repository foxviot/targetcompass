param(
  [string]$InstallDir = "$PSScriptRoot",
  [switch]$KeepProjects,
  [switch]$KeepRuntime,
  [switch]$PreserveInstallerRoot
)

$ErrorActionPreference = "Stop"

if (!(Test-Path -LiteralPath $InstallDir)) {
  Write-Host "Install directory not found: $InstallDir"
  exit 0
}

$ShortcutDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\TargetCompass V5"
$DesktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "TargetCompass V5.lnk"
$AppDir = Join-Path $InstallDir "app"

$processes = Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like "*tc_lite.py serve*" -and $_.CommandLine -like "*TargetCompassV5*" }
foreach ($process in $processes) {
  try {
    Stop-Process -Id $process.ProcessId -Force
  } catch {
    Write-Warning "Could not stop process $($process.ProcessId): $($_.Exception.Message)"
  }
}

if ($KeepProjects -and (Test-Path -LiteralPath (Join-Path $AppDir "projects"))) {
  $Backup = Join-Path ([Environment]::GetFolderPath("Desktop")) ("TargetCompassV5_projects_backup_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
  New-Item -ItemType Directory -Force -Path $Backup | Out-Null
  Copy-Item -Recurse -Force -LiteralPath (Join-Path $AppDir "projects") -Destination $Backup
  Write-Host "Projects backed up to: $Backup"
}

if (Test-Path -LiteralPath $ShortcutDir) {
  Remove-Item -Recurse -Force -LiteralPath $ShortcutDir
}
if (Test-Path -LiteralPath $DesktopShortcut) {
  Remove-Item -Force -LiteralPath $DesktopShortcut
}

if ($KeepRuntime) {
  $KeepDir = Join-Path $InstallDir "runtime"
  $TempRuntime = Join-Path ([System.IO.Path]::GetTempPath()) ("TargetCompassV5_runtime_" + [guid]::NewGuid().ToString("N"))
  if (Test-Path -LiteralPath $KeepDir) {
    Move-Item -LiteralPath $KeepDir -Destination $TempRuntime
  }
  Remove-Item -Recurse -Force -LiteralPath $InstallDir
  New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
  if (Test-Path -LiteralPath $TempRuntime) {
    Move-Item -LiteralPath $TempRuntime -Destination (Join-Path $InstallDir "runtime")
  }
} else {
  if ($PreserveInstallerRoot) {
    foreach ($child in @("app", "runtime", "logs", "install_manifest.json", "runtime_check.json")) {
      $path = Join-Path $InstallDir $child
      if (Test-Path -LiteralPath $path) {
        Remove-Item -Recurse -Force -LiteralPath $path
      }
    }
  } else {
    Remove-Item -Recurse -Force -LiteralPath $InstallDir
  }
}

Write-Host "TargetCompass V5 has been uninstalled."
