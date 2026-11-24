"""Helper program of the installed app: stockterminal-run.exe MODULE [ARGS]

The installed app has no Python, so the jobs the app starts in their own process (daily update, earnings watch,
loading companies; see config.module_cmd) run through this program instead of `python -m MODULE`.
It is a window-less program (no black window every 5 minutes for the earnings watch), so it reconnects its output
to whatever the app passed in (the app reads the progress of a company load from it).
"""
import multiprocessing
import os
import runpy
import sys


def _reconnect_output():
    for name, handle_id in (("stdout", -11), ("stderr", -12)):
        if getattr(sys, name) is not None:
            continue
        stream = None
        if os.name == "nt":
            try:
                import ctypes
                import msvcrt
                h = ctypes.windll.kernel32.GetStdHandle(handle_id)
                if h and h != ctypes.c_void_p(-1).value:
                    fd = msvcrt.open_osfhandle(h, os.O_WRONLY)
                    stream = open(fd, "w", buffering=1, encoding="utf-8", errors="replace")
            except OSError:
                stream = None
        setattr(sys, name, stream or open(os.devnull, "w", encoding="utf-8"))


if __name__ == "__main__":
    multiprocessing.freeze_support()
    _reconnect_output()
    if len(sys.argv) < 2 or not sys.argv[1].startswith("stockterminal."):
        sys.exit("usage: stockterminal-run stockterminal.MODULE [ARGS]")
    module = sys.argv[1]
    sys.argv = [module, *sys.argv[2:]]
    runpy.run_module(module, run_name="__main__", alter_sys=True)
