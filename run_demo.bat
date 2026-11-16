@echo off
rem M021 DEMO for the supervisor meeting: no ESP32, no M021-RIG Wi-Fi needed.
rem Starts the dashboard in DEMO mode; it starts tools\fake_rig.py itself and opens the
rem browser on http://127.0.0.1:8000/ . Close this window (or Ctrl+C) to stop both.
rem Demo CSV logs: dashboard\logs\demo\ (never mixed with rig logs).
cd /d "%~dp0"
if not exist dashboard\.venv\Scripts\python.exe (
  echo dashboard\.venv is missing. See README.md, "Python setup".
  exit /b 1
)
dashboard\.venv\Scripts\python.exe dashboard\app.py --demo %*
