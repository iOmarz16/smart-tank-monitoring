@echo off
rem M021 dashboard: one command, opens the browser on http://127.0.0.1:8000/
rem   run_dashboard.bat                                    (real rig, laptop joined to M021-RIG)
rem   run_dashboard.bat --rig ws://127.0.0.1:8765/         (tools\fake_rig.py)
cd /d "%~dp0"
if not exist dashboard\.venv\Scripts\python.exe (
  echo dashboard\.venv is missing. See README.md, "Python setup".
  exit /b 1
)
dashboard\.venv\Scripts\python.exe dashboard\app.py %*
