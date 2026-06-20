$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root
python scripts\check_install.py
python tc_lite.py demo --project vascular_aging_demo
python tc_lite.py adapter-audit --project vascular_aging_demo
python tc_lite.py export-package --project vascular_aging_demo
