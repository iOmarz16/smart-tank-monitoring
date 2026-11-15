@echo off
rem Fake rig for testing the dashboard without hardware.
rem   run_fake_rig.bat --scenario normal|ramp|sensor_loss|wifi_outage
cd /d "%~dp0"
dashboard\.venv\Scripts\python.exe tools\fake_rig.py %*
