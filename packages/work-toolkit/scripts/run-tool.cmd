@echo off
rem run-tool.cmd - call one work-toolkit tool from the command line.
rem Picks a Python that actually has the deps, and caches the path.
rem
rem   scripts\run-tool.cmd work_status
rem   echo {"keyword":"notice"} | scripts\run-tool.cmd work_wechat_search
rem
rem Keep this file ASCII-only: cmd.exe parses batch files in the OEM codepage,
rem UTF-8 Chinese comments here corrupt the script before it can run.
rem Console CP must be UTF-8: doctor --pick prints a UTF-8 path, and
rem a user profile with non-ASCII chars would be mangled by CP 936.
chcp 65001 >nul 2>nul
setlocal enabledelayedexpansion
cd /d "%~dp0.."

set "CACHE=workspace\.pyexe"
set "WORKPY="

if exist "%CACHE%" set /p WORKPY=<"%CACHE%"
if defined WORKPY if not exist "!WORKPY!" set "WORKPY="
if defined WORKPY goto :run

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY where py >nul 2>nul && set "PY=py -3"
if not defined PY goto :nopython

for /f "usebackq delims=" %%p in (`%PY% tools\doctor.py --pick 2^>nul`) do set "WORKPY=%%p"
if not defined WORKPY goto :nodeps
> "%CACHE%" echo !WORKPY!

:run
"!WORKPY!" tools\run_tool.py %*
exit /b %ERRORLEVEL%

:nopython
echo [x] Python not found. Install Python 3.9+ and tick "Add to PATH".
exit /b 1

:nodeps
echo [x] No Python with mcp / python-docx / openpyxl was found.
echo     Diagnose with: python tools\doctor.py
exit /b 1
