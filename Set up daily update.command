#!/bin/bash
# Mac: double-click once to make macOS run the daily update every morning at 07:00, and the earnings watch
# every 5 minutes (it only goes online on a day a company in the terminal reports; see
# stockterminal/earnings_watch.py). The Mac twin of "Set up daily update.bat". A Mac that was asleep at
# 07:00 runs it when it wakes up; one that was off catches up when you open the app. To remove them again:
#   launchctl unload ~/Library/LaunchAgents/com.stockterminal.daily.plist && rm ~/Library/LaunchAgents/com.stockterminal.daily.plist
#   launchctl unload ~/Library/LaunchAgents/com.stockterminal.earnings.plist && rm ~/Library/LaunchAgents/com.stockterminal.earnings.plist
# "Start Stock Terminal.command" calls this once by itself (with "quiet") when it is not set up yet.
cd "$(dirname "$0")" || exit 1
DIR="$(pwd)"
PLIST="$HOME/Library/LaunchAgents/com.stockterminal.daily.plist"
mkdir -p "$HOME/Library/LaunchAgents" data/logs
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.stockterminal.daily</string>
  <key>ProgramArguments</key><array>
    <string>$DIR/.venv/bin/python</string><string>-m</string><string>stockterminal.daily</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>$DIR/data/logs/launchd_daily.log</string>
  <key>StandardErrorPath</key><string>$DIR/data/logs/launchd_daily.log</string>
</dict></plist>
PLISTEOF
launchctl unload "$PLIST" >/dev/null 2>&1
launchctl load "$PLIST" && echo "Done: the daily update now runs every morning at 07:00."

EPLIST="$HOME/Library/LaunchAgents/com.stockterminal.earnings.plist"
cat > "$EPLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.stockterminal.earnings</string>
  <key>ProgramArguments</key><array>
    <string>$DIR/.venv/bin/python</string><string>-m</string><string>stockterminal.earnings_watch</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>StartInterval</key><integer>300</integer>
  <key>StandardOutPath</key><string>$DIR/data/logs/launchd_earnings.log</string>
  <key>StandardErrorPath</key><string>$DIR/data/logs/launchd_earnings.log</string>
</dict></plist>
PLISTEOF
launchctl unload "$EPLIST" >/dev/null 2>&1
launchctl load "$EPLIST" && echo "Done: the earnings watch now looks for new results every 5 minutes."
if [ "$1" != "quiet" ]; then read -r -p "Press Enter to close..." _; fi
