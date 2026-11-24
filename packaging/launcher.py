"""Entry point of the installed app: Stock Terminal.exe (Windows) / Stock Terminal.app (macOS).

Built with PyInstaller (stock-terminal.spec). Opens the app window, the same as `python -m stockterminal.app`.
"""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from stockterminal.app.__main__ import main
    main()
