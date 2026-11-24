@echo off
rem Double-click to load every S&P 500 company (about 4 hours for the first pass, about 8 in total).
rem Safe to stop (close the window) and start again: it continues where it stopped.
rem Progress: data\logs\load_sp500_progress.json   Report at the end: data\reports\sp500_quality.md
cd /d "%~dp0"
".venv\Scripts\python.exe" -m stockterminal.load_index sp500 %*
echo.
pause
