$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root
python scripts\check_install.py
python tc_lite.py serve --project vascular_aging_demo --port 8781
