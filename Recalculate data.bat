@echo off
rem Double-click to recalculate all loaded companies from the data already on this PC (no downloads)
cd /d "%~dp0"
".venv\Scripts\python.exe" -m stockterminal.standardize
echo.
pause
