#!/bin/bash
# Mac: double-click to check that the app still works (the Mac twin of "Run self-test.bat").
cd "$(dirname "$0")" || exit 1
.venv/bin/python -m stockterminal.selftest
echo
read -r -p "Press Enter to close..." _
