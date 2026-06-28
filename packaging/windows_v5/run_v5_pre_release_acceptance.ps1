param(
  [string]$Project = "demo",
  [int]$QuestionCount = 5
)
$ErrorActionPreference = "Stop"
Set-Location "C:\Users\ASUS\Documents\target"
python tc_lite.py v5-doctor --project $Project
python tc_lite.py test-suite --suite quick --project $Project
python tc_lite.py test-suite --suite full --project $Project
python tc_lite.py test-suite --suite e2e --project $Project
python tc_lite.py v5-real-question-validation --project $Project --question-count $QuestionCount --isolated-projects
python tc_lite.py v5-release-acceptance --project $Project --question-count $QuestionCount
python tc_lite.py v5-production-acceptance --project $Project --target all
