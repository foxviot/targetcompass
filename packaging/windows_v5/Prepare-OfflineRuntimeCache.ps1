param(
  [string]$RInstaller = "",
  [string]$RPortableZip = "",
  [string]$JreArchive = "",
  [string]$NextflowBinary = "",
  [string[]]$DockerImageTar = @(),
  [switch]$WriteManifestOnly
)

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$RuntimeCache = Join-Path $Here "runtime_cache"
$RCache = Join-Path $RuntimeCache "r_packages"
$NextflowCache = Join-Path $RuntimeCache "nextflow"
$DockerCache = Join-Path $RuntimeCache "docker_images"

function Ensure-Dir([string]$Path) {
  if (!(Test-Path -LiteralPath $Path)) {
    New-Item -ItemType Directory -Path $Path | Out-Null
  }
}

function Copy-IfSet([string]$Source, [string]$DestinationDir, [string]$Label) {
  if (!$Source) { return $null }
  if (!(Test-Path -LiteralPath $Source)) { throw "$Label not found: $Source" }
  Ensure-Dir $DestinationDir
  $dest = Join-Path $DestinationDir (Split-Path -Leaf $Source)
  Copy-Item -LiteralPath $Source -Destination $dest -Force
  return $dest
}

function File-Info([string]$Path) {
  if (!$Path -or !(Test-Path -LiteralPath $Path)) { return $null }
  $item = Get-Item -LiteralPath $Path
  $hash = Get-FileHash -LiteralPath $Path -Algorithm SHA256
  return [ordered]@{
    path = $Path.Replace("\", "/")
    name = $item.Name
    size_bytes = $item.Length
    sha256 = $hash.Hash.ToLowerInvariant()
  }
}

Ensure-Dir $RuntimeCache
Ensure-Dir $RCache
Ensure-Dir $NextflowCache
Ensure-Dir $DockerCache

$copied = @()
if (!$WriteManifestOnly) {
  $copied += Copy-IfSet $RInstaller $RCache "R installer"
  $copied += Copy-IfSet $RPortableZip $RCache "R portable zip"
  $copied += Copy-IfSet $JreArchive $NextflowCache "JRE archive"
  $copied += Copy-IfSet $NextflowBinary $NextflowCache "Nextflow binary"
  foreach ($tar in $DockerImageTar) {
    $copied += Copy-IfSet $tar $DockerCache "Docker image archive"
  }
}

$manifest = [ordered]@{
  schema_version = "v5.offline_runtime_cache/0.1"
  generated_at = (Get-Date).ToString("o")
  cache_root = $RuntimeCache.Replace("\", "/")
  policy = [ordered]@{
    python = "Embedded Python and wheelhouse are bundled."
    r = "Place R installer or portable R zip under runtime_cache/r_packages. Installer does not silently install R system-wide."
    nextflow = "Place nextflow/nextflow.bat and a Java 17+ archive under runtime_cache/nextflow."
    docker = "Place docker image tar/tar.gz archives under runtime_cache/docker_images. Docker Desktop itself is not redistributed."
  }
  python = [ordered]@{
    embedded_zip = @(Get-ChildItem -LiteralPath $RuntimeCache -Filter "python-*-embed-amd64.zip" -File -ErrorAction SilentlyContinue | ForEach-Object { File-Info $_.FullName })
    get_pip = File-Info (Join-Path $RuntimeCache "get-pip.py")
    wheelhouse = @(Get-ChildItem -LiteralPath (Join-Path $Here "wheelhouse") -Filter "*.whl" -File -ErrorAction SilentlyContinue | ForEach-Object { File-Info $_.FullName })
  }
  r = [ordered]@{
    installers = @(Get-ChildItem -LiteralPath $RCache -File -ErrorAction SilentlyContinue | ForEach-Object { File-Info $_.FullName })
    status = if (@(Get-ChildItem -LiteralPath $RCache -File -ErrorAction SilentlyContinue).Count -gt 0) { "CACHE_PRESENT" } else { "NOT_CACHED" }
  }
  nextflow = [ordered]@{
    files = @(Get-ChildItem -LiteralPath $NextflowCache -File -ErrorAction SilentlyContinue | ForEach-Object { File-Info $_.FullName })
    nextflow_binary_present = [bool](@(Get-ChildItem -LiteralPath $NextflowCache -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -in @("nextflow", "nextflow.bat") }).Count)
    java_archive_present = [bool](@(Get-ChildItem -LiteralPath $NextflowCache -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -match "jre|jdk|java" }).Count)
  }
  docker = [ordered]@{
    image_archives = @(Get-ChildItem -LiteralPath $DockerCache -File -Include "*.tar","*.tar.gz" -ErrorAction SilentlyContinue | ForEach-Object { File-Info $_.FullName })
    status = if (@(Get-ChildItem -LiteralPath $DockerCache -File -Include "*.tar","*.tar.gz" -ErrorAction SilentlyContinue).Count -gt 0) { "CACHE_PRESENT" } else { "NOT_CACHED" }
    note = "Docker Desktop installer is not bundled. Use archives with docker load after Docker Desktop is installed."
  }
  copied = @($copied | Where-Object { $_ })
}

$manifestPath = Join-Path $RuntimeCache "offline_runtime_cache_manifest.json"
$manifest | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
Write-Host $manifestPath
