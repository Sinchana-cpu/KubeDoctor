@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
  python webapp.py
  goto :eof
)
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 webapp.py
  goto :eof
)
echo Python 3.10 or newer was not found.
echo Install Python from https://www.python.org/downloads/ and enable "Add Python to PATH".
pause
