#!/bin/bash
# Mac: double-click to recalculate all loaded companies from the data already on this computer
# (the Mac twin of "Recalculate data.bat"; no downloads)
cd "$(dirname "$0")" || exit 1
.venv/bin/python -m stockterminal.standardize
echo
read -r -p "Press Enter to close..." _
