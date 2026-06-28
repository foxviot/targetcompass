@echo off
cd /d "C:\Users\ASUS\Documents\target"
powershell -ExecutionPolicy Bypass -File "C:\Users\ASUS\Documents\target\packaging\windows_v5\run_v5_pre_release_acceptance.ps1" -Project %1
