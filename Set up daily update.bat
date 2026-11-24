@echo off
rem Double-click once: makes Windows run the daily update every morning at 07:00 (or as soon as the PC
rem is on, if it was off at 07:00), and the earnings watch every 5 minutes (it only goes online on a day a
rem company in the terminal reports; see stockterminal\earnings_watch.py). To remove them again:
rem   schtasks /delete /tn "Stock Terminal daily update" /f
rem   schtasks /delete /tn "Stock Terminal earnings watch" /f
rem Start Stock Terminal.bat calls this once by itself (with "quiet") when a task does not exist yet.
cd /d "%~dp0"
powershell -NoProfile -Command "$a = New-ScheduledTaskAction -Execute '%~dp0.venv\Scripts\python.exe' -Argument '-m stockterminal.daily' -WorkingDirectory '%~dp0'; $t = New-ScheduledTaskTrigger -Daily -At 07:00; $s = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3); Register-ScheduledTask -TaskName 'Stock Terminal daily update' -Action $a -Trigger $t -Settings $s -Force | Out-Null; Write-Host 'Done: the daily update now runs every morning at 07:00.'"
rem pythonw: no window pops up every 5 minutes
powershell -NoProfile -Command "$a = New-ScheduledTaskAction -Execute '%~dp0.venv\Scripts\pythonw.exe' -Argument '-m stockterminal.earnings_watch' -WorkingDirectory '%~dp0'; $t = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 5); $s = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1); Register-ScheduledTask -TaskName 'Stock Terminal earnings watch' -Action $a -Trigger $t -Settings $s -Force | Out-Null; Write-Host 'Done: the earnings watch now looks for new results every 5 minutes.'"
echo.
if /i not "%~1"=="quiet" pause
