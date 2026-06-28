param(
  [string]$Project = "vascular_aging_demo",
  [string]$InstallDir = "$PSScriptRoot",
  [int]$Port = 8801,
  [switch]$StartBackends,
  [switch]$NoBrowser,
  [switch]$ForceStop
)

$ErrorActionPreference = "Stop"
$StopScript = Join-Path $PSScriptRoot "Stop-TargetCompassV5.ps1"
$LaunchScript = Join-Path $PSScriptRoot "Launch-TargetCompassV5.ps1"

if (!(Test-Path -LiteralPath $StopScript)) {
  throw "Stop script not found: $StopScript"
}
if (!(Test-Path -LiteralPath $LaunchScript)) {
  throw "Launch script not found: $LaunchScript"
}

$stopArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $StopScript, "-Port", "$Port")
if ($ForceStop) { $stopArgs += "-Force" }
& powershell @stopArgs

$launchArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $LaunchScript, "-Project", $Project, "-InstallDir", $InstallDir, "-Port", "$Port")
if ($StartBackends) { $launchArgs += "-StartBackends" }
if ($NoBrowser) { $launchArgs += "-NoBrowser" }
& powershell @launchArgs
