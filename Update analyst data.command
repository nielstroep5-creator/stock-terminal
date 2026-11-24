#!/bin/bash
# Mac: double-click to download the latest analyst data (estimates, price targets, ratings) for all companies
# (the Mac twin of "Update analyst data.bat")
cd "$(dirname "$0")" || exit 1
.venv/bin/python -m stockterminal.analyst
echo
read -r -p "Press Enter to close..." _
