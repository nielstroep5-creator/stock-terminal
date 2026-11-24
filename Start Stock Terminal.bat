@echo off
rem Double-click to open Stock Terminal.
rem Before starting it keeps everything up to date by itself:
rem   1. gets the latest code from GitHub (skipped when offline, without git, or when local changes block it)
rem   2. installs new packages when requirements.txt changed since the last install
rem   3. after an update: runs the self-test; if the new version fails it, goes back to the version
rem      that worked and says so (details in data\logs\selftest.log)
rem   4. sets up the daily data update at 07:00 once (see "Set up daily update.bat")
cd /d "%~dp0"

rem Step 1 is one block that ends by restarting this file: git may have just replaced it, and Windows
rem reads a running .bat line by line, so the rest must come from the new version.
if not "%~1"=="updated" (
  set "ST_BEFORE="
  where git >nul 2>nul && (
    git config core.hooksPath .githooks
    git rev-parse --abbrev-ref --symbolic-full-name @{u} >nul 2>nul || git branch --set-upstream-to=origin/master >nul 2>nul
    for /f %%h in ('git rev-parse HEAD 2^>nul') do set "ST_BEFORE=%%h"
    echo Checking for updates...
    git pull --ff-only --quiet >nul 2>nul || echo Could not get the latest code from GitHub, starting the current version.
  )
  "%~f0" updated
)

fc /b requirements.txt ".venv\requirements.installed" >nul 2>nul || (
  echo Installing new packages...
  ".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt && copy /y requirements.txt ".venv\requirements.installed" >nul
)

set "ST_NOW="
for /f %%h in ('git rev-parse HEAD 2^>nul') do set "ST_NOW=%%h"
if defined ST_BEFORE if not "%ST_NOW%"=="%ST_BEFORE%" call :check_update

schtasks /query /tn "Stock Terminal daily update" >nul 2>nul || call "Set up daily update.bat" quiet
schtasks /query /tn "Stock Terminal earnings watch" >nul 2>nul || call "Set up daily update.bat" quiet

start "" ".venv\Scripts\pythonw.exe" -m stockterminal.app
exit /b


:check_update
echo New version downloaded, running the self-test...
if not exist "data\logs" mkdir "data\logs"
".venv\Scripts\python.exe" -m stockterminal.selftest > "data\logs\selftest.log" 2>&1
if not errorlevel 1 exit /b
git reset --keep %ST_BEFORE% >nul 2>nul
echo The new version failed the self-test; went back to the previous version.
powershell -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('The latest version from GitHub failed the self-test, so the previous working version is opened instead. Details: data\logs\selftest.log. Let whoever pushed it know; once it is fixed, the next start picks it up by itself.', 'Stock Terminal') | Out-Null"
exit /b
