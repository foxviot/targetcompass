param(
  [string]$InnoSetupCompiler = "",
  [switch]$SkipZipBuild
)

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Resolve-Path (Join-Path $Here "..\..")

if (!$SkipZipBuild) {
  Push-Location $Root
  try {
    python scripts\build_windows_installer_v5.py | Out-Host
  } finally {
    Pop-Location
  }
}

if (!$InnoSetupCompiler) {
  $candidates = @(
    (Join-Path $Root "tools\inno\Inno\ISCC.exe"),
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
  )
  foreach ($candidate in $candidates) {
    if (Test-Path -LiteralPath $candidate) {
      $InnoSetupCompiler = $candidate
      break
    }
  }
}

if (!$InnoSetupCompiler -or !(Test-Path -LiteralPath $InnoSetupCompiler)) {
  throw "Inno Setup Compiler not found. Install Inno Setup 6 or pass -InnoSetupCompiler path. Zip installer is still available in dist/."
}

Push-Location $Here
try {
  & $InnoSetupCompiler "TargetCompassV5.iss"
  if ($LASTEXITCODE -ne 0) { throw "Inno Setup build failed" }
} finally {
  Pop-Location
}
