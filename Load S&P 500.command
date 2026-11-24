#!/bin/bash
# Mac: double-click to load every S&P 500 company (the Mac twin of "Load S&P 500.bat"; about 4 hours for the
# first pass, about 8 in total). Safe to stop and start again: it continues where it stopped.
# The same can be started from the app: Home, "Get started" (on a new install).
cd "$(dirname "$0")" || exit 1
.venv/bin/python -m stockterminal.load_index sp500 "$@"
echo
read -r -p "Press Enter to close..." _
