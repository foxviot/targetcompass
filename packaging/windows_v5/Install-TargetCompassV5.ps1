param(
  [string]$InstallDir = "$env:LOCALAPPDATA\TargetCompassV5",
  [string]$PythonVersion = "3.13.5",
  [int]$Port = 8801,
  [int]$DependencyTimeoutSeconds = 300,
  [int]$DemoTimeoutSeconds = 300,
  [switch]$SkipDependencyInstall,
  [switch]$SkipShortcutInstall,
  [switch]$LaunchAfterInstall,
  [switch]$SkipDemoInit
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PayloadZip = Join-Path $ScriptDir "payload\targetcompass_v5_local_bundle.zip"
$RuntimeCache = Join-Path $ScriptDir "runtime_cache"
$Wheelhouse = Join-Path $ScriptDir "wheelhouse"
$RuntimeDir = Join-Path $InstallDir "runtime"
$PythonDir = Join-Path $RuntimeDir "python"
$AppDir = Join-Path $InstallDir "app"
$LogDir = Join-Path $InstallDir "logs"
$ShortcutDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\TargetCompass V5"
$DesktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "TargetCompass V5.lnk"

function Write-Step([string]$Message) {
  Write-Host "[TargetCompass V5] $Message"
}

function Invoke-CheckedProcess([string]$FilePath, [string[]]$ArgumentList, [int]$TimeoutSeconds, [string]$Label, [switch]$AllowFailure) {
  Write-Step "$Label"
  $process = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory (Get-Location).Path -PassThru -WindowStyle Hidden
  if (!$process.WaitForExit($TimeoutSeconds * 1000)) {
    try { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue } catch {}
    $message = "$Label timed out after $TimeoutSeconds seconds"
    if ($AllowFailure) {
      Write-Step "WARN: $message"
      return $false
    }
    throw $message
  }
  if ($process.ExitCode -ne 0) {
    $message = "$Label failed with exit code $($process.ExitCode)"
    if ($AllowFailure) {
      Write-Step "WARN: $message"
      return $false
    }
    throw $message
  }
  return $true
}

function Ensure-Directory([string]$Path) {
  if (!(Test-Path -LiteralPath $Path)) {
    New-Item -ItemType Directory -Path $Path | Out-Null
  }
}

function Download-Or-UseCache([string]$Url, [string]$OutFile, [string]$CacheName) {
  $cached = Join-Path $RuntimeCache $CacheName
  if (Test-Path -LiteralPath $cached) {
    Write-Step "Using cached runtime file: $cached"
    Copy-Item -LiteralPath $cached -Destination $OutFile -Force
    return
  }
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
    Write-Step "Embedded Python runtime already exists."
    return
  }
  Ensure-Directory $PythonDir
  $zip = Join-Path $RuntimeDir "python-embed.zip"
  $cacheName = "python-$PythonVersion-embed-amd64.zip"
  $url = "https://www.python.org/ftp/python/$PythonVersion/$cacheName"
  Download-Or-UseCache $url $zip $cacheName
  Expand-Archive -LiteralPath $zip -DestinationPath $PythonDir -Force
  Enable-EmbeddedPythonSite $PythonDir
}

function Ensure-PipAndDependencies {
  if ($SkipDependencyInstall) {
    Write-Step "Skipping Python dependency installation. Use Repair-TargetCompassV5.ps1 when optional dependencies are needed."
    return
  }
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
    Download-Or-UseCache "https://bootstrap.pypa.io/get-pip.py" $getPip "get-pip.py"
    Invoke-CheckedProcess $python @($getPip) $DependencyTimeoutSeconds "Bootstrapping pip" | Out-Null
  }
  $wheelFiles = @()
  if (Test-Path -LiteralPath $Wheelhouse) {
    $wheelFiles = @(Get-ChildItem -LiteralPath $Wheelhouse -Filter "*.whl" -File -ErrorAction SilentlyContinue)
  }
  if ($wheelFiles.Count -gt 0) {
    Write-Step "Installing Python dependencies from wheelhouse when available."
    Invoke-CheckedProcess $python @("-m", "pip", "install", "--no-index", "--find-links", $Wheelhouse, "python-docx>=1.1.0") $DependencyTimeoutSeconds "Installing Python dependencies from wheelhouse" | Out-Null
  } else {
    Invoke-CheckedProcess $python @("-m", "pip", "install", "python-docx>=1.1.0") $DependencyTimeoutSeconds "Installing Python dependencies from network" | Out-Null
  }
}

function Install-AppPayload {
  if (!(Test-Path -LiteralPath $PayloadZip)) {
    throw "Missing payload zip: $PayloadZip"
  }
  if (Test-Path -LiteralPath $AppDir) {
    Remove-Item -LiteralPath $AppDir -Recurse -Force
  }
  Ensure-Directory $AppDir
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  [System.IO.Compression.ZipFile]::ExtractToDirectory($PayloadZip, $AppDir)
}

function Test-CommandAvailable([string]$Name) {
  $cmd = Get-Command $Name -ErrorAction SilentlyContinue
  return [bool]$cmd
}

function Write-RuntimeCheck {
  $python = Join-Path $PythonDir "python.exe"
  $dockerAvailable = Test-CommandAvailable "docker"
  $dockerRunning = $false
  if ($dockerAvailable) {
    try {
      docker info 2>$null | Out-Null
      $dockerRunning = $LASTEXITCODE -eq 0
    } catch {
      $dockerRunning = $false
    }
  }
  $checks = [ordered]@{
    schema_version = "v5.windows_runtime_check/0.1"
    generated_at = (Get-Date).ToString("o")
    embedded_python = (Test-Path -LiteralPath $python)
    python_path = $python
    rscript_available = (Test-CommandAvailable "Rscript")
    nextflow_available = (Test-CommandAvailable "nextflow")
    docker_cli_available = $dockerAvailable
    docker_daemon_running = $dockerRunning
    offline_runtime_cache_present = (Test-Path -LiteralPath $RuntimeCache)
    wheelhouse_present = (Test-Path -LiteralPath $Wheelhouse)
    notes = @(
      "R/Nextflow/Docker are optional for basic UI and canonical control-plane demo.",
      "Docker daemon is required to activate PostgreSQL/MinIO local backends.",
      "Nextflow is required for production Nextflow module execution.",
      "Rscript is required for R-based downstream analysis modules when enabled."
    )
  }
  $checks | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $InstallDir "runtime_check.json") -Encoding UTF8
}

function Initialize-DefaultDemo {
  if ($SkipDemoInit) {
    Write-Step "Skipping default demo initialization."
    return
  }
  $python = Join-Path $PythonDir "python.exe"
  $demoQuestion = "Are there SASP-high skeletal muscle background cells with characteristic surface markers in sarcopenia?"
  Push-Location $AppDir
  try {
    Invoke-CheckedProcess $python @("tc_lite.py", "local-backends-prepare", "--project", "vascular_aging_demo") $DemoTimeoutSeconds "Preparing local backend manifests" -AllowFailure | Out-Null
    Invoke-CheckedProcess $python @("tc_lite.py", "v5-run-local", "--project", "vascular_aging_demo", "--question", $demoQuestion, "--limit", "1", "--max-analysis-packets", "2") $DemoTimeoutSeconds "Initializing default demo run" -AllowFailure | Out-Null
    Invoke-CheckedProcess $python @("tc_lite.py", "v5-report-manifest", "--project", "vascular_aging_demo") $DemoTimeoutSeconds "Building default report manifest" -AllowFailure | Out-Null
    Invoke-CheckedProcess $python @("tc_lite.py", "v5-doctor", "--project", "vascular_aging_demo") $DemoTimeoutSeconds "Running v5 doctor" -AllowFailure | Out-Null
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
  if ($SkipShortcutInstall) {
    Write-Step "Skipping PowerShell-created shortcuts. Installer wrapper creates shortcuts separately."
    return
  }
  Ensure-Directory $ShortcutDir
  $launcher = Join-Path $InstallDir "Launch-TargetCompassV5.ps1"
  $launcherCmd = Join-Path $InstallDir "TargetCompassV5-Launcher.cmd"
  $stopper = Join-Path $InstallDir "Stop-TargetCompassV5.ps1"
  $restarter = Join-Path $InstallDir "Restart-TargetCompassV5.ps1"
  $uninstaller = Join-Path $InstallDir "Uninstall-TargetCompassV5.ps1"
  $repair = Join-Path $InstallDir "Repair-TargetCompassV5.ps1"
  Copy-Item -LiteralPath (Join-Path $ScriptDir "Launch-TargetCompassV5.ps1") -Destination $launcher -Force
  Copy-Item -LiteralPath (Join-Path $ScriptDir "TargetCompassV5-Launcher.cmd") -Destination $launcherCmd -Force
  Copy-Item -LiteralPath (Join-Path $ScriptDir "Stop-TargetCompassV5.ps1") -Destination $stopper -Force
  Copy-Item -LiteralPath (Join-Path $ScriptDir "Restart-TargetCompassV5.ps1") -Destination $restarter -Force
  Copy-Item -LiteralPath (Join-Path $ScriptDir "Repair-TargetCompassV5.ps1") -Destination $repair -Force
  Copy-Item -LiteralPath (Join-Path $ScriptDir "Uninstall-TargetCompassV5.ps1") -Destination $uninstaller -Force
  $stopArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$stopper`" -Port $Port"
  $restartArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$restarter`" -Port $Port"
  $repairArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$repair`""
  $uninstallArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$uninstaller`""
  New-Shortcut -Path (Join-Path $ShortcutDir "TargetCompass V5.lnk") -Target $launcherCmd -Arguments "" -WorkingDirectory $InstallDir
  New-Shortcut -Path (Join-Path $ShortcutDir "Stop TargetCompass V5.lnk") -Target "powershell.exe" -Arguments $stopArgs -WorkingDirectory $InstallDir
  New-Shortcut -Path (Join-Path $ShortcutDir "Restart TargetCompass V5.lnk") -Target "powershell.exe" -Arguments $restartArgs -WorkingDirectory $InstallDir
  New-Shortcut -Path (Join-Path $ShortcutDir "Repair TargetCompass V5.lnk") -Target "powershell.exe" -Arguments $repairArgs -WorkingDirectory $InstallDir
  New-Shortcut -Path (Join-Path $ShortcutDir "Uninstall TargetCompass V5.lnk") -Target "powershell.exe" -Arguments $uninstallArgs -WorkingDirectory $InstallDir
  New-Shortcut -Path $DesktopShortcut -Target $launcherCmd -Arguments "" -WorkingDirectory $InstallDir
}

Ensure-Directory $InstallDir
Ensure-Directory $LogDir
Write-Step "Installing to $InstallDir"
Ensure-PythonRuntime
Ensure-PipAndDependencies
Install-AppPayload
Add-AppPathToEmbeddedPython $AppDir
Write-RuntimeCheck
Initialize-DefaultDemo
Install-Shortcuts

$manifest = [ordered]@{
  schema_version = "v5.windows_app_install/0.2"
  install_dir = $InstallDir
  app_dir = $AppDir
  python = (Join-Path $PythonDir "python.exe")
  installed_at = (Get-Date).ToString("o")
  launch_script = (Join-Path $InstallDir "Launch-TargetCompassV5.ps1")
  launcher_cmd = (Join-Path $InstallDir "TargetCompassV5-Launcher.cmd")
  stop_script = (Join-Path $InstallDir "Stop-TargetCompassV5.ps1")
  restart_script = (Join-Path $InstallDir "Restart-TargetCompassV5.ps1")
  repair_script = (Join-Path $InstallDir "Repair-TargetCompassV5.ps1")
  uninstall_script = (Join-Path $InstallDir "Uninstall-TargetCompassV5.ps1")
  runtime_check = (Join-Path $InstallDir "runtime_check.json")
  default_project = "vascular_aging_demo"
  default_url = "http://127.0.0.1:$Port/"
  offline_cache_policy = "Place python-$PythonVersion-embed-amd64.zip, get-pip.py, and wheels under runtime_cache/ and wheelhouse/ for offline install."
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $InstallDir "install_manifest.json") -Encoding UTF8

Write-Step "Installation completed."
Write-Step "Start Menu: TargetCompass V5"
Write-Step "Desktop shortcut: $DesktopShortcut"
Write-Step "Open UI with: `"$InstallDir\TargetCompassV5-Launcher.cmd`""

if ($LaunchAfterInstall) {
  & (Join-Path $InstallDir "TargetCompassV5-Launcher.cmd") -Port $Port
}
