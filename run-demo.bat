@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.10+ from python.org and tick "Add Python to PATH".
  pause
  exit /b 1
)
echo ============================================================
echo  RECORDED / SIMULATED DEMO
echo  Replays saved kubectl output. No Kubernetes, Docker, kubectl
echo  or internet is used, and nothing is ever applied anywhere.
echo ============================================================
echo.
python triage.py --scenario all-four --set env.APP_MODE=staging
if errorlevel 1 (
  echo.
  echo Something went wrong - see the message above.
  pause
  exit /b 1
)
echo.
echo Opening the report folder...
start "" "reports\all-four"
pause
