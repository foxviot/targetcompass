$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Url = "http://127.0.0.1:8781/"
Set-Location $Root

python scripts\check_install.py | Out-Host

try {
  $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
  if ($response.StatusCode -eq 200) {
    Start-Process $Url
    exit 0
  }
} catch {
  # Server is not running yet.
}

Start-Process -FilePath python `
  -ArgumentList @("tc_lite.py", "serve", "--project", "vascular_aging_demo", "--port", "8781") `
  -WorkingDirectory $Root `
  -WindowStyle Hidden `
  -RedirectStandardOutput (Join-Path $Root "webapp.out.log") `
  -RedirectStandardError (Join-Path $Root "webapp.err.log")

for ($i = 0; $i -lt 30; $i++) {
  Start-Sleep -Milliseconds 500
  try {
    $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
    if ($response.StatusCode -eq 200) {
      Start-Process $Url
      exit 0
    }
  } catch {
  }
}

Write-Host "TargetCompass did not start within 15 seconds. Check webapp.err.log."
exit 1
