"""Company data for a new install, in one click: the S&P 500 starter pack from the download page, or the data
of another copy on this computer (for example the code copy in C:\\Stock Terminal). Settings > Data and accounts,
and the welcome screen of a fresh install.

The app has its database open while it runs, so a new database is first prepared next to it
(data/incoming/) and swapped in when the app starts again (apply_incoming, called by app/__main__.py before
anything opens the database). The old database is kept once in data/backup/.

    python -m stockterminal.datasetup              # status
    python -m stockterminal.datasetup PATH         # prepare the data of another copy / a starter pack zip
"""
import json
import shutil
import sqlite3
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from . import __version__
from .config import DATA_DIR, DB_PATH

INCOMING = DATA_DIR / "incoming"
READY = INCOMING / "ready.json"            # written last: only a complete preparation is ever swapped in
BACKUP = DATA_DIR / "backup"
PACK_PREFIX = "stock-terminal-data"
OTHER_FILES = ("settings.json", "trading212.json")   # your settings and Trading 212 link, from another copy
CACHE_S = 3600

_lock = threading.Lock()
_job = {}                                    # the running / last preparation: {state, step, done, total, error}
_pack_cache = {}


# ------------------------------------------------------------------ what is there
def _companies(path=DB_PATH):
    if not Path(path).exists():
        return 0
    try:
        c = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=5)
        try:
            return c.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        finally:
            c.close()
    except sqlite3.Error:
        return 0


def latest_pack():
    """The newest starter pack on the download page (a release asset named stock-terminal-data-*.zip), or None.
    {name, size, url, date}. Cached an hour."""
    hit = _pack_cache.get("v")
    if hit is not None and time.time() - _pack_cache["at"] < CACHE_S:
        return hit
    from .appupdate import RELEASES_REPO
    req = urllib.request.Request(f"https://api.github.com/repos/{RELEASES_REPO}/releases?per_page=30",
                                 headers={"Accept": "application/vnd.github+json",
                                          "User-Agent": f"StockTerminal/{__version__}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        releases = json.load(r)
    packs = [dict(name=a["name"], size=a.get("size") or 0, url=a["browser_download_url"],
                  date=(a.get("updated_at") or rel.get("published_at") or "")[:10])
             for rel in releases if not rel.get("draft")
             for a in rel.get("assets") or []
             if a.get("name", "").startswith(PACK_PREFIX) and a["name"].endswith(".zip")]
    best = max(packs, key=lambda p: p["name"]) if packs else None
    _pack_cache.update(at=time.time(), v=best)
    return best


LOG = DATA_DIR / "logs" / "datasetup.log"


def last_import():
    """The last time data was taken over from elsewhere (logs/datasetup.log): {at, source} or None."""
    try:
        lines = LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):        # "2026-11-25 01:32:24 Now using Data from C:\Stock Terminal\data: 503 companies."
        msg = line[20:]
        if msg.startswith("Now using "):
            return dict(at=line[:16], source=msg[len("Now using "):].rsplit(":", 1)[0])
    return None


def status():
    """{companies, waiting (a prepared database waits for a restart), job, pack (or pack_error)}"""
    res = dict(companies=_companies(), waiting=READY.exists(), job=dict(_job) or None)
    if READY.exists():
        try:
            res["waiting_info"] = json.loads(READY.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    try:
        res["pack"] = latest_pack()
    except Exception as e:  # noqa: BLE001 - offline: the other ways still work
        res["pack"], res["pack_error"] = None, f"Could not reach the download page: {str(e)[:150]}"
    return res


# ------------------------------------------------------------------ preparing
def find_source(path):
    """What is at `path`: ('zip', file) for a starter pack, ('copy', data folder) for another copy's data.
    Accepts the app folder of a code copy, its data folder, the terminal.db file or a starter pack .zip."""
    p = Path(str(path).strip().strip('"')).expanduser()
    if p.is_file() and p.suffix.lower() == ".zip":
        return "zip", p
    if p.is_file() and p.name.lower().endswith(".db"):
        return "copy", p.parent
    for d in (p, p / "data"):
        if (d / "terminal.db").is_file():
            if d.resolve() == DATA_DIR.resolve():
                raise ValueError("That is the data this app already uses.")
            return "copy", d
    if p.is_dir():
        packs = sorted(p.glob(f"{PACK_PREFIX}*.zip"))
        if packs:
            return "zip", packs[-1]
    raise ValueError(f"No Stock Terminal data found in {p}. Choose the Stock Terminal folder, its data "
                     "folder, or a starter pack (.zip).")


def _set(**kw):
    _job.update(kw)


def _download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": f"StockTerminal/{__version__}"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        _set(step="Downloading the starter pack", done=0, total=total)
        done = 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            _set(done=done)


def _unzip(zpath, dest_db):
    with zipfile.ZipFile(zpath) as z:
        if "terminal.db" not in z.namelist():
            raise ValueError(f"{zpath.name} is not a Stock Terminal starter pack")
        info = z.getinfo("terminal.db")
        _set(step="Unpacking", done=0, total=info.file_size)
        with z.open(info) as f, open(dest_db, "wb") as out:
            done = 0
            while chunk := f.read(16 << 20):
                out.write(chunk)
                done += len(chunk)
                _set(done=done)


def _copy_db(src_dir, dest_db):
    """A consistent copy, also while the other copy's app or daily update is using its database."""
    src = sqlite3.connect(f"file:{(src_dir / 'terminal.db').as_posix()}?mode=ro", uri=True, timeout=60)
    dst = sqlite3.connect(dest_db)
    try:
        def progress(_status, remaining, total):
            _set(done=total - remaining, total=total)
        _set(step="Copying the data", done=0, total=0)
        src.backup(dst, pages=4096, progress=progress)
    finally:
        dst.close()
        src.close()


def _prepare(kind, src, label):
    try:
        if INCOMING.exists():
            shutil.rmtree(INCOMING)
        INCOMING.mkdir(parents=True)
        db = INCOMING / "terminal.db"
        if kind == "download":
            z = INCOMING / "pack.zip"
            _download(src, z)
            _unzip(z, db)
            z.unlink()
        elif kind == "zip":
            _unzip(src, db)
        else:
            _copy_db(src, db)
            for name in OTHER_FILES:
                if (src / name).is_file():
                    shutil.copy2(src / name, INCOMING / name)
        c = sqlite3.connect(db)                  # one plain file (no -wal next to it) before it is moved
        c.execute("PRAGMA journal_mode=DELETE")
        c.close()
        n = _companies(db)
        if not n:
            raise ValueError("The new data has no companies, so it was not used.")
        keep_personal = kind != "copy"           # a starter pack has no portfolio / watchlists: keep yours
        READY.write_text(json.dumps(dict(source=label, companies=n, keep_personal=keep_personal,
                                         at=time.strftime("%Y-%m-%d %H:%M"))), encoding="utf-8")
        _set(state="ready", step="Ready: restart the app to use it", companies=n)
    except Exception as e:  # noqa: BLE001 - shown on the page; the current data is untouched
        shutil.rmtree(INCOMING, ignore_errors=True)
        _set(state="error", error=str(e)[:300])


def start(source, path=None):
    """source 'download' (the starter pack from the download page) or 'path' (another copy / a .zip).
    Runs in the background; status() shows the progress."""
    with _lock:
        if _job.get("state") == "running":
            raise ValueError("Already busy getting data.")
        if source == "download":
            pack = latest_pack()
            if not pack:
                raise ValueError("There is no starter pack on the download page yet.")
            kind, src, label = "download", pack["url"], f"Starter pack {pack['date']}"
        else:
            kind, src = find_source(path or "")
            label = f"Starter pack {src.name}" if kind == "zip" else f"Data from {src}"
        _job.clear()
        _set(state="running", step="Starting", done=0, total=0, source=label)
        threading.Thread(target=_prepare, args=(kind, src, label), daemon=True).start()
        return dict(_job)


def cancel_waiting():
    shutil.rmtree(INCOMING, ignore_errors=True)
    _job.clear()


# ------------------------------------------------------------------ swapping in (at start)
def _copy_personal(new_db, old_db):
    """Portfolio, watchlists and alerts (every pf_ table) of the old database into the new one."""
    c = sqlite3.connect(new_db)
    try:
        c.execute("ATTACH DATABASE ? AS old", (str(old_db),))
        tables = [r[0] for r in c.execute("SELECT name FROM old.sqlite_master WHERE type='table' AND name LIKE 'pf\\_%' ESCAPE '\\'")]
        for t in tables:
            sql = c.execute("SELECT sql FROM old.sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()[0]
            c.execute(f'DROP TABLE IF EXISTS main."{t}"')
            c.execute(sql)
            c.execute(f'INSERT INTO main."{t}" SELECT * FROM old."{t}"')
        c.commit()
        c.execute("DETACH DATABASE old")
    finally:
        c.close()


def _move(src, dst, tries=20):
    for i in range(tries):                       # the daily update / earnings watch may hold it a moment
        try:
            src.replace(dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.5)


def apply_incoming():
    """At start, before the database is opened: swap in a prepared database. Returns a message or None."""
    if not READY.exists():
        return None
    try:
        info = json.loads(READY.read_text(encoding="utf-8"))
        new = INCOMING / "terminal.db"
        BACKUP.mkdir(parents=True, exist_ok=True)
        old = BACKUP / "terminal-before-import.db"
        for f in BACKUP.glob("terminal-before-import.db*"):
            f.unlink()
        if DB_PATH.exists():
            c = sqlite3.connect(DB_PATH, timeout=30)          # fold the WAL into the file before moving it
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            c.close()
            _move(DB_PATH, old)
            for ext in ("-wal", "-shm"):
                Path(str(DB_PATH) + ext).unlink(missing_ok=True)
            if info.get("keep_personal"):
                _copy_personal(new, old)
        _move(new, DB_PATH)
        for name in OTHER_FILES:
            if (INCOMING / name).is_file():
                if (DATA_DIR / name).is_file():
                    shutil.copy2(DATA_DIR / name, BACKUP / name)
                shutil.copy2(INCOMING / name, DATA_DIR / name)
        shutil.rmtree(INCOMING, ignore_errors=True)
        msg = f"Now using {info.get('source')}: {info.get('companies')} companies."
    except Exception as e:  # noqa: BLE001 - never stop the app from starting; keep the prepared data for a retry
        msg = f"Could not switch to the new data: {e}"
    (DATA_DIR / "logs").mkdir(parents=True, exist_ok=True)
    with open(DATA_DIR / "logs" / "datasetup.log", "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    print(msg)
    return msg


if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(start("path", sys.argv[1]))
        while _job.get("state") == "running":
            time.sleep(1)
        print(_job)
    else:
        print(json.dumps(status(), indent=2))
