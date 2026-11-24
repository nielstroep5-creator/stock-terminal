"""Windows scheduled tasks of the installed app: the daily update at 07:00 and the earnings watch every 5 minutes.

A code copy sets these up with "Set up daily update.bat" (called by "Start Stock Terminal.bat"). The installed app
has no .bat files, so it registers the same two tasks itself on every start (cheap; keeps the path right after the
app moved or was updated), pointing at its helper program stockterminal-run.exe. The uninstaller removes them.
"""
import subprocess
import sys

from .config import FROZEN, module_cmd

TASKS = {
    "Stock Terminal daily update": ("stockterminal.daily",
                                    "New-ScheduledTaskTrigger -Daily -At 07:00",
                                    "-StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3)"),
    "Stock Terminal earnings watch": ("stockterminal.earnings_watch",
                                      "New-ScheduledTaskTrigger -Once -At (Get-Date).Date "
                                      "-RepetitionInterval (New-TimeSpan -Minutes 5)",
                                      "-MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
                                      "-ExecutionTimeLimit (New-TimeSpan -Hours 1)"),
}


def _ps_quote(s):
    return "'" + str(s).replace("'", "''") + "'"


def ensure():
    """Register (or refresh) both tasks. Installed app on Windows only; errors are only logged."""
    if not (FROZEN and sys.platform == "win32"):
        return
    for name, (module, trigger, settings) in TASKS.items():
        exe, *args = module_cmd(module)
        # a task of a code copy on this PC ("Set up daily update.bat", python.exe) is left alone: it updates that
        # copy's data folder, and taking it over would make it update the installed app's data instead
        script = (f"$old = Get-ScheduledTask -TaskName {_ps_quote(name)} -ErrorAction SilentlyContinue; "
                  f"if ($old -and ($old.Actions[0].Execute -notlike '*stockterminal-run*')) {{ exit 0 }}; "
                  f"$a = New-ScheduledTaskAction -Execute {_ps_quote(exe)} -Argument {_ps_quote(' '.join(args))}; "
                  f"$t = {trigger}; $s = New-ScheduledTaskSettingsSet {settings}; "
                  f"Register-ScheduledTask -TaskName {_ps_quote(name)} -Action $a -Trigger $t -Settings $s -Force | Out-Null")
        try:
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                           timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:  # noqa: BLE001
            print(f"scheduled task {name}: {e}")
