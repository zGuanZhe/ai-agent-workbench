@echo off
rem open-console.cmd - build the console page and start the local server.
rem
rem This python is only used to start scripts\launch.py (stdlib only).
rem launch.py then picks the interpreter that really has the deps - which is
rem often NOT the one on PATH. All Chinese output comes from Python, so this
rem file stays ASCII (cmd.exe reads batch files in the OEM codepage).
chcp 65001 >nul
setlocal
cd /d "%~dp0.."

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY where py >nul 2>nul && set "PY=py -3"

if not defined PY (
  echo [x] Python not found. Install Python 3.9+ and tick "Add to PATH".
  echo     https://www.python.org/downloads/windows/
  pause
  exit /b 1
)

%PY% scripts\launch.py %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo [x] Launch failed, exit code %RC%. Diagnose: python tools\doctor.py
  echo.
  pause
)
exit /b %RC%
