param(
  [string]$Project = "vascular_aging_demo",
  [string]$InstallDir = "$PSScriptRoot",
  [int]$Port = 8801,
  [int]$StartupTimeoutSeconds = 30,
  [switch]$StartBackends,
  [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Python = Join-Path $InstallDir "runtime\python\python.exe"
$AppDir = Join-Path $InstallDir "app"
$LogDir = Join-Path $InstallDir "logs"
$OutLog = Join-Path $LogDir "targetcompass-v5.out.log"
$ErrLog = Join-Path $LogDir "targetcompass-v5.err.log"
$LaunchLog = Join-Path $LogDir "targetcompass-v5-launch.log"

function Write-LaunchLog([string]$Message) {
  if (!(Test-Path -LiteralPath $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
  }
  $line = "$(Get-Date -Format o) $Message"
  Add-Content -LiteralPath $LaunchLog -Value $line -Encoding UTF8
  Write-Host $Message
}

function Find-AvailablePort([int]$PreferredPort) {
  for ($candidate = $PreferredPort; $candidate -lt ($PreferredPort + 30); $candidate++) {
    $listener = $null
    try {
      $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Parse("127.0.0.1"), $candidate)
      $listener.Start()
      return $candidate
    } catch {
    } finally {
      if ($listener) { $listener.Stop() }
    }
  }
  throw "No available local port found from $PreferredPort to $($PreferredPort + 29)."
}

function Test-HttpReady([string]$Url) {
  try {
    $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
    return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500)
  } catch {
    return $false
  }
}

function Show-StartupFailure([string]$Url) {
  $errTail = ""
  $outTail = ""
  if (Test-Path -LiteralPath $ErrLog) {
    $errTail = (Get-Content -LiteralPath $ErrLog -Tail 60 -ErrorAction SilentlyContinue) -join "`r`n"
  }
  if (Test-Path -LiteralPath $OutLog) {
    $outTail = (Get-Content -LiteralPath $OutLog -Tail 40 -ErrorAction SilentlyContinue) -join "`r`n"
  }
  $message = @"
TargetCompass V5 did not become reachable at:
$Url

InstallDir:
$InstallDir

Please send these log files to the developer:
$LaunchLog
$ErrLog
$OutLog

Recent stderr:
$errTail

Recent stdout:
$outTail
"@
  Write-LaunchLog "Startup failed. Logs: $LogDir"
  [System.Windows.Forms.MessageBox]::Show($message, "TargetCompass V5 startup failed", "OK", "Error") | Out-Null
}

if (!(Test-Path -LiteralPath $Python)) {
  throw "Python runtime not found: $Python"
}
if (!(Test-Path -LiteralPath (Join-Path $AppDir "tc_lite.py"))) {
  throw "TargetCompass app not found: $AppDir"
}
if (!(Test-Path -LiteralPath $LogDir)) {
  New-Item -ItemType Directory -Path $LogDir | Out-Null
}
Add-Type -AssemblyName System.Windows.Forms

$Port = Find-AvailablePort $Port
$url = "http://127.0.0.1:$Port/"
$healthUrl = "http://127.0.0.1:$Port/healthz"
Write-LaunchLog "Launching TargetCompass V5 from $InstallDir on $url"

if ($StartBackends) {
  $BackendScript = Join-Path $AppDir "projects\$Project\infra\local_backends\start_local_backends.ps1"
  if (Test-Path -LiteralPath $BackendScript) {
    powershell -NoProfile -ExecutionPolicy Bypass -File $BackendScript
    & $Python "tc_lite.py" "v5-backends-activate" "--project" $Project | Out-Null
  } else {
    Write-Warning "Backend script not found. Run local-backends-prepare first."
  }
}

$existing = Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like "*tc_lite.py serve*" -and $_.CommandLine -like "*--port $Port*" } |
  Select-Object -First 1

if (!$existing) {
  if (Test-Path -LiteralPath $OutLog) { Remove-Item -LiteralPath $OutLog -Force -ErrorAction SilentlyContinue }
  if (Test-Path -LiteralPath $ErrLog) { Remove-Item -LiteralPath $ErrLog -Force -ErrorAction SilentlyContinue }
  Start-Process -FilePath $Python `
    -ArgumentList @("tc_lite.py", "serve", "--project", $Project, "--host", "127.0.0.1", "--port", "$Port") `
    -WorkingDirectory $AppDir `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -WindowStyle Hidden
}

for ($i = 0; $i -lt $StartupTimeoutSeconds; $i++) {
  if (Test-HttpReady $healthUrl) {
    Write-LaunchLog "Service is ready: $url"
    if (!$NoBrowser) {
      Start-Process $url
    }
    Write-Host "TargetCompass V5 is running: $url"
    Write-Host "Logs: $LogDir"
    exit 0
  }
  Start-Sleep -Seconds 1
}

Show-StartupFailure $url
if (!$NoBrowser) {
  Start-Process $LogDir
}
exit 1
