param(
  [string]$InstallDir = "$env:LOCALAPPDATA\TargetCompassV4",
  [string]$PythonVersion = "3.13.5",
  [switch]$LaunchAfterInstall
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PayloadZip = Join-Path $ScriptDir "payload\targetcompass_v4_local_bundle.zip"
$RuntimeDir = Join-Path $InstallDir "runtime"
$PythonDir = Join-Path $RuntimeDir "python"
$AppDir = Join-Path $InstallDir "app"
$LogDir = Join-Path $InstallDir "logs"
$ShortcutDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\TargetCompass V4"
$DesktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "TargetCompass V4.lnk"

function Write-Step([string]$Message) {
  Write-Host "[TargetCompass V4] $Message"
}

function Ensure-Directory([string]$Path) {
  if (!(Test-Path -LiteralPath $Path)) {
    New-Item -ItemType Directory -Path $Path | Out-Null
  }
}

function Download-File([string]$Url, [string]$OutFile) {
  Write-Step "Downloading $Url"
  Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing
}

function Enable-EmbeddedPythonSite([string]$Dir) {
  $pth = Get-ChildItem -LiteralPath $Dir -Filter "python*._pth" | Select-Object -First 1
  if (!$pth) { return }
  $lines = Get-Content -LiteralPath $pth.FullName
  $updated = @()
  $hasSitePackages = $false
  $hasImportSite = $false
  foreach ($line in $lines) {
    if ($line.Trim() -eq "Lib\site-packages") { $hasSitePackages = $true }
    if ($line.Trim() -eq "import site" -or $line.Trim() -eq "#import site") {
      $updated += "import site"
      $hasImportSite = $true
    } else {
      $updated += $line
    }
  }
  if (!$hasSitePackages) { $updated += "Lib\site-packages" }
  if (!$hasImportSite) { $updated += "import site" }
  Set-Content -LiteralPath $pth.FullName -Value $updated -Encoding ASCII
  Ensure-Directory (Join-Path $Dir "Lib\site-packages")
}

function Add-AppPathToEmbeddedPython([string]$AppPath) {
  $pth = Get-ChildItem -LiteralPath $PythonDir -Filter "python*._pth" | Select-Object -First 1
  if (!$pth) { return }
  $lines = Get-Content -LiteralPath $pth.FullName
  if ($lines -notcontains $AppPath) {
    $lines += $AppPath
    Set-Content -LiteralPath $pth.FullName -Value $lines -Encoding ASCII
  }
}

function Ensure-PythonRuntime {
  Ensure-Directory $RuntimeDir
  if (Test-Path -LiteralPath (Join-Path $PythonDir "python.exe")) {
    Write-Step "Python runtime already exists."
    return
  }
  Ensure-Directory $PythonDir
  $zip = Join-Path $RuntimeDir "python-embed.zip"
  $url = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
  Download-File $url $zip
  Expand-Archive -LiteralPath $zip -DestinationPath $PythonDir -Force
  Enable-EmbeddedPythonSite $PythonDir
}

function Ensure-PipAndDependencies {
  $python = Join-Path $PythonDir "python.exe"
  $pipOk = $false
  try {
    & $python -m pip --version 2>$null | Out-Null
    $pipOk = $LASTEXITCODE -eq 0
  } catch {
    $pipOk = $false
  }
  if (!$pipOk) {
    $getPip = Join-Path $RuntimeDir "get-pip.py"
    Download-File "https://bootstrap.pypa.io/get-pip.py" $getPip
    & $python $getPip
    if ($LASTEXITCODE -ne 0) { throw "pip bootstrap failed" }
  }
  $wheelhouse = Join-Path $ScriptDir "wheelhouse"
  if (Test-Path -LiteralPath $wheelhouse) {
    & $python -m pip install --no-index --find-links $wheelhouse "python-docx>=1.1.0"
  } else {
    & $python -m pip install "python-docx>=1.1.0"
  }
  if ($LASTEXITCODE -ne 0) { throw "dependency installation failed" }
}

function Install-AppPayload {
  if (!(Test-Path -LiteralPath $PayloadZip)) {
    throw "Missing payload zip: $PayloadZip"
  }
  if (Test-Path -LiteralPath $AppDir) {
    Remove-Item -LiteralPath $AppDir -Recurse -Force
  }
  Ensure-Directory $AppDir
  Expand-Archive -LiteralPath $PayloadZip -DestinationPath $AppDir -Force
}

function Prepare-InstalledApp {
  $python = Join-Path $PythonDir "python.exe"
  Push-Location $AppDir
  try {
    & $python "tc_lite.py" "local-v4-prepare" "--project" "vascular_aging_demo"
    if ($LASTEXITCODE -ne 0) { throw "local-v4-prepare failed" }
  } finally {
    Pop-Location
  }
}

function New-Shortcut([string]$Path, [string]$Target, [string]$Arguments, [string]$WorkingDirectory) {
  $shell = New-Object -ComObject WScript.Shell
  $shortcut = $shell.CreateShortcut($Path)
  $shortcut.TargetPath = $Target
  $shortcut.Arguments = $Arguments
  $shortcut.WorkingDirectory = $WorkingDirectory
  $shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,220"
  $shortcut.Save()
}

function Install-Shortcuts {
  Ensure-Directory $ShortcutDir
  $launcher = Join-Path $InstallDir "Launch-TargetCompassV4.ps1"
  $uninstaller = Join-Path $InstallDir "Uninstall-TargetCompassV4.ps1"
  Copy-Item -LiteralPath (Join-Path $ScriptDir "Launch-TargetCompassV4.ps1") -Destination $launcher -Force
  Copy-Item -LiteralPath (Join-Path $ScriptDir "Uninstall-TargetCompassV4.ps1") -Destination $uninstaller -Force
  New-Shortcut -Path (Join-Path $ShortcutDir "TargetCompass V4.lnk") -Target "powershell.exe" -Arguments "-NoProfile -ExecutionPolicy Bypass -File `"$launcher`"" -WorkingDirectory $InstallDir
  New-Shortcut -Path (Join-Path $ShortcutDir "Uninstall TargetCompass V4.lnk") -Target "powershell.exe" -Arguments "-NoProfile -ExecutionPolicy Bypass -File `"$uninstaller`"" -WorkingDirectory $InstallDir
  New-Shortcut -Path $DesktopShortcut -Target "powershell.exe" -Arguments "-NoProfile -ExecutionPolicy Bypass -File `"$launcher`"" -WorkingDirectory $InstallDir
}

Ensure-Directory $InstallDir
Ensure-Directory $LogDir
Write-Step "Installing to $InstallDir"
Ensure-PythonRuntime
Ensure-PipAndDependencies
Install-AppPayload
Add-AppPathToEmbeddedPython $AppDir
Prepare-InstalledApp
Install-Shortcuts

$manifest = @{
  schema_version = "v4.windows_app_install/0.1"
  install_dir = $InstallDir
  app_dir = $AppDir
  python = (Join-Path $PythonDir "python.exe")
  installed_at = (Get-Date).ToString("o")
  launch_script = (Join-Path $InstallDir "Launch-TargetCompassV4.ps1")
  uninstall_script = (Join-Path $InstallDir "Uninstall-TargetCompassV4.ps1")
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $InstallDir "install_manifest.json") -Encoding UTF8

Write-Step "Installation completed."
Write-Step "Start Menu: TargetCompass V4"
Write-Step "Desktop shortcut: $DesktopShortcut"

if ($LaunchAfterInstall) {
  & (Join-Path $InstallDir "Launch-TargetCompassV4.ps1")
}
