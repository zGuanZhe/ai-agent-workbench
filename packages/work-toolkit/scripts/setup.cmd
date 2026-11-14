@echo off
rem setup.cmd - first-run installer: deps -> MCP registration -> build -> verify.
rem Double-click this after downloading the project.
rem
rem ASCII-only on purpose: cmd.exe parses batch files in the OEM codepage.
rem All Chinese output comes from Python (scripts\setup.py), not from here.
chcp 65001 >nul 2>nul
setlocal
cd /d "%~dp0.."

rem Probe by actually running the interpreter, not by `where python`:
rem Windows ships a Store alias named python.exe that `where` finds but that
rem exits 9009, and `py -3` may point at an older 3.x. Both would slip through.
set "PY="
python -c "import sys" >nul 2>nul && set "PY=python"
if not defined PY py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY py -3.12 -c "import sys" >nul 2>nul && set "PY=py -3.12"

if not defined PY (
  echo [x] No usable Python found.
  echo     If you installed from Microsoft Store, turn off the "App execution alias"
  echo     for python.exe, or install from https://www.python.org/downloads/windows/
  echo     and tick "Add python.exe to PATH".
  pause
  exit /b 1
)

for /f "delims=" %%v in ('%PY% -c "import sys;print(sys.version_info>=(3,10))"') do set "PYOK=%%v"
if /i not "%PYOK%"=="True" (
  echo [x] Python too old. This project needs 3.10 or newer ^(the mcp library requires it^).
  echo     Found:
  %PY% --version
  echo     Install a newer one from https://www.python.org/downloads/windows/
  pause
  exit /b 1
)

%PY% scripts\setup.py %*
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo [ok] Done. Reload WorkBuddy, then type /work ^<your request^>.
  echo      No web page and no MCP trust click are required.
) else (
  echo [x] Something failed, exit code %RC%. Run: %PY% tools\doctor.py
)
echo.
pause
exit /b %RC%
