#!/bin/bash
# Mac: double-click to open Stock Terminal (the Mac twin of "Start Stock Terminal.bat").
# Before starting it keeps everything up to date by itself:
#   1. gets the latest code from GitHub (skipped when offline or when local changes block it)
#   2. installs new packages when requirements.txt changed since the last install
#   3. after an update: runs the self-test; if the new version fails it, goes back to the version
#      that worked and says so (details in data/logs/selftest.log)
#   4. sets up the daily data update at 07:00 once (see "Set up daily update.command")
cd "$(dirname "$0")" || exit 1
PY=".venv/bin/python"

before=""
if command -v git >/dev/null 2>&1 && git rev-parse --git-dir >/dev/null 2>&1; then
  git config core.hooksPath .githooks
  git rev-parse --abbrev-ref --symbolic-full-name @{u} >/dev/null 2>&1 || \
    git branch --set-upstream-to="origin/$(git branch --show-current)" >/dev/null 2>&1
  before=$(git rev-parse HEAD 2>/dev/null)
  echo "Checking for updates..."
  git pull --ff-only --quiet >/dev/null 2>&1 || echo "Could not get the latest code from GitHub, starting the current version."
fi

if ! cmp -s requirements.txt .venv/requirements.installed; then
  echo "Installing new packages..."
  "$PY" -m pip install --quiet --disable-pip-version-check -r requirements.txt && cp requirements.txt .venv/requirements.installed
fi

now=$(git rev-parse HEAD 2>/dev/null)
if [ -n "$before" ] && [ "$now" != "$before" ]; then
  echo "New version downloaded, running the self-test..."
  mkdir -p data/logs
  if ! "$PY" -m stockterminal.selftest > data/logs/selftest.log 2>&1; then
    git reset --keep "$before" >/dev/null 2>&1
    echo "The new version failed the self-test; went back to the previous version."
    osascript -e 'display dialog "The latest version from GitHub failed the self-test, so the previous working version is opened instead. Details: data/logs/selftest.log. Let whoever pushed it know; once it is fixed, the next start picks it up by itself." with title "Stock Terminal" buttons {"OK"} default button 1' >/dev/null 2>&1
  fi
fi

[ -f "$HOME/Library/LaunchAgents/com.stockterminal.daily.plist" ] && [ -f "$HOME/Library/LaunchAgents/com.stockterminal.earnings.plist" ] \
  || "./Set up daily update.command" quiet

echo "Starting Stock Terminal..."
nohup "$PY" -m stockterminal.app >/dev/null 2>&1 &
disown
# close this Terminal window (the app keeps running)
(sleep 1; osascript -e 'tell application "Terminal" to close (every window whose name contains "Start Stock Terminal")' >/dev/null 2>&1) &
exit 0
