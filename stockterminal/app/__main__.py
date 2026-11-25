"""Start Stock Terminal: local server + native window.

    python -m stockterminal.app            # open the app window
    python -m stockterminal.app --serve    # only run the server (open http://127.0.0.1:8765 in a browser)
    python -m stockterminal.app --serve --port 8799    # a second copy next to an open app (testing)
    python -m stockterminal.app --after PID            # used by 'Update now': open once process PID has closed
"""
import socket
import sys
import threading
import time

import uvicorn


def free_port(preferred=8765):
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue


def serve(port):
    from ..datasetup import apply_incoming
    apply_incoming()                           # company data prepared in Settings: swap it in before the database opens
    from .server import app
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    return server


def main():
    if "--serve" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv[:-1] else 8765
        print(f"Stock Terminal running at http://127.0.0.1:{port}")
        serve(port).run()
        return

    if "--after" in sys.argv[:-1]:             # started by "Update now": wait until the old window has closed
        from ..appupdate import wait_for_exit
        wait_for_exit(sys.argv[sys.argv.index("--after") + 1])

    import webview  # pywebview: native window using Edge WebView2

    if sys.stderr is None:                     # started without a console (pythonw): keep errors in a log file
        from ..config import DATA_DIR
        (DATA_DIR / "logs").mkdir(parents=True, exist_ok=True)
        log = DATA_DIR / "logs" / "app.log"
        if log.exists() and log.stat().st_size > 1_000_000:
            log.replace(log.with_suffix(".old.log"))
        sys.stdout = sys.stderr = open(log, "a", encoding="utf-8", buffering=1)
        print(f"--- started {time.strftime('%Y-%m-%d %H:%M:%S')}")

    from ..config import FROZEN
    if FROZEN:                                 # the installed app sets up its own daily update / earnings watch
        from ..schedule import ensure
        threading.Thread(target=ensure, daemon=True).start()

    port = free_port()
    server = serve(port)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):                       # wait until the server answers
        if server.started:
            break
        time.sleep(0.05)
    from ..config import FROZEN               # a code copy says so in the title: two copies can't be mixed up
    window = webview.create_window("Stock Terminal" if FROZEN else "Stock Terminal (test copy)", f"http://127.0.0.1:{port}/", width=1440, height=920,
                                   min_size=(1000, 650))
    from . import server as srv
    srv.close_window = window.destroy          # "Restart now" after an update closes this window

    def pick(file=False):                      # Settings > Data: choose another copy's folder or a starter pack
        kinds = getattr(webview, "FileDialog", None)
        if file:
            kind = kinds.OPEN if kinds else webview.OPEN_DIALOG
            got = window.create_file_dialog(kind, file_types=("Starter pack (*.zip)",))
        else:
            got = window.create_file_dialog(kinds.FOLDER if kinds else webview.FOLDER_DIALOG)
        if not got:
            return None
        return got[0] if isinstance(got, (list, tuple)) else got
    srv.pick_path = pick
    webview.start()
    server.should_exit = True


if __name__ == "__main__":
    main()
