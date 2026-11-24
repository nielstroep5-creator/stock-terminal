@echo off
rem Double-click to update prices, valuation, analyst data and (when due) SEC filings for every loaded company.
rem The same update runs every morning at 07:00 as a Windows scheduled task. Log: data\logs\daily_YYYY-MM-DD.log
cd /d "%~dp0"
".venv\Scripts\python.exe" -m stockterminal.daily %*
echo.
pause
