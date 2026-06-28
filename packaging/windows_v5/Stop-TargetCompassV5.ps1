param(
  [int]$Port = 8801,
  [switch]$Force
)

$ErrorActionPreference = "Stop"

$matches = Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like "*tc_lite.py serve*" -and $_.CommandLine -like "*--port $Port*" }

if (!$matches) {
  Write-Host "No TargetCompass V5 service found on port $Port."
  exit 0
}

foreach ($proc in $matches) {
  Write-Host "Stopping TargetCompass V5 service PID $($proc.ProcessId) on port $Port"
  if ($Force) {
    Stop-Process -Id $proc.ProcessId -Force
  } else {
    Stop-Process -Id $proc.ProcessId
  }
}

Write-Host "TargetCompass V5 service stopped."
