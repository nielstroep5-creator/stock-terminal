@echo off
rem Double-click to check that the app still works (about 10 seconds, no internet needed).
rem Claude runs the same test before every push: python -m stockterminal.selftest
cd /d "%~dp0"
".venv\Scripts\python.exe" -m stockterminal.selftest
echo.
pause
