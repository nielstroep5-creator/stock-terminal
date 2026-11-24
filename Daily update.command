#!/bin/bash
# Mac: double-click to run the daily update now (the Mac twin of "Daily update.bat").
cd "$(dirname "$0")" || exit 1
.venv/bin/python -m stockterminal.daily "$@"
echo
read -r -p "Press Enter to close..." _
