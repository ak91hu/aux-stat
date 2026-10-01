@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
if exist ".venv\Scripts\python.exe" goto run
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 goto missingpython
py -3 -m venv .venv
if errorlevel 1 goto failed
:run
if exist ".venv\probe-deps-installed" goto launch
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
type nul > ".venv\probe-deps-installed"
:launch
".venv\Scripts\python.exe" acfreedom_energy_probe.py %*
set "PROBE_EXIT=%ERRORLEVEL%"
echo.
if "%PROBE_EXIT%"==2 echo Nem talalt felismerheto energiaadatot. Lasd az output mappat.
pause
exit /b %PROBE_EXIT%
:missingpython
echo Python 3.11 vagy ujabb es a Python Launcher szukseges.
echo Telepites: https://www.python.org/downloads/windows/
pause
exit /b 1
:failed
echo A kornyezet vagy a csomagok telepitese sikertelen. Lasd a fenti hibat.
pause
exit /b 1
