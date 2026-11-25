"""Updates of the app itself (not of the data): "Check for updates" in Settings > About and the pop-up at start.

Two kinds of install:
- git copy (Niels, TvR, anyone who cloned the code): a new version is a newer commit on GitHub. Update = the same
  steps as "Start Stock Terminal": git pull, new packages when requirements.txt changed, the self-test, and back
  to the previous version when that fails.
- installed app (the Windows installer / Mac app, coming with v1.0): a new version is a newer release in the public
  releases of the repository (RELEASES_REPO). Checking works already; installing comes with the installer.

    python -m stockterminal.appupdate          # check now and print the result
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from . import __version__
from .config import DATA_DIR, FROZEN, ROOT

RELEASES_REPO = "nielstroep5-creator/stock-terminal"   # this repository: releases (installers, starter packs) and issues
CHECK_CACHE_S = 3600
_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_lock = threading.Lock()       # one check or update at a time
_cache = {}                    # last check: {"at": time, "result": {...}}


def _git(*args, timeout=60):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout,
                       creationflags=_FLAGS)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip()
                           else f"git {args[0]} failed")
    return r.stdout.strip()


def mode():
    """'git' when this copy came from git (and git is installed), else 'installed'."""
    if (ROOT / ".git").exists():
        try:
            _git("--version", timeout=10)
            return "git"
        except Exception:  # noqa: BLE001 - no git on this PC: treat it like an installed copy
            pass
    return "installed"


def _version_key(v):
    """'1.2.3' < '1.2.4-rc1' < '1.2.4' (a release candidate comes before its release)."""
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)(?:-(dev|rc)(\d*))?", v or "")
    if not m:
        return (0, 0, 0, 0, 0)
    tag = {None: 2, "rc": 1, "dev": 0}[m.group(4)]
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), tag, int(m.group(5) or 0))


def _remote_version(ref):
    try:
        text = _git("show", f"{ref}:stockterminal/__init__.py", timeout=20)
        m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
        return m.group(1) if m else None
    except Exception:  # noqa: BLE001
        return None


def _upstream():
    try:
        return _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", timeout=10)
    except Exception:  # noqa: BLE001 - no tracking branch set (the start file sets it): use master
        return "origin/master"


def _check_git():
    up = _upstream()
    remote = up.split("/", 1)[0]
    branch = up.split("/", 1)[1] if "/" in up else "master"
    _git("fetch", "--quiet", remote, branch, timeout=60)
    behind = int(_git("rev-list", "--count", f"HEAD..{up}", timeout=20) or 0)
    changes = []
    if behind:
        log = _git("log", "--no-merges", "--format=%s", "-n", "20", f"HEAD..{up}", timeout=20)
        changes = [c for c in log.splitlines() if c and not c.startswith("WORKING")][:12]
    latest = _remote_version(up) or __version__
    target = _git("rev-parse", "--short", up, timeout=10)
    return dict(available=behind > 0, latest=latest, target=target, changes=changes, behind=behind,
                notes=None, can_install=True)


def _check_installed():
    """Newest release in the download repository. Test versions (1.0.0-rc1) are only offered to someone who
    runs a test or development version themselves; everyone else gets finished versions only."""
    url = f"https://api.github.com/repos/{RELEASES_REPO}/releases?per_page=20"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": f"StockTerminal/{__version__}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        releases = json.load(r)
    testing = "-" in __version__
    rels = [x for x in releases if not x.get("draft") and (testing or not x.get("prerelease"))
            and (x.get("tag_name") or "").startswith("v")]          # data-... releases hold starter packs
    if not rels:
        return dict(available=False, latest=__version__, target=__version__, changes=[], notes=None,
                    can_install=False)
    rel = max(rels, key=lambda x: _version_key((x.get("tag_name") or "").lstrip("v")))
    latest = (rel.get("tag_name") or "").lstrip("v")
    newer = _version_key(latest) > _version_key(__version__)
    setup = next((a["browser_download_url"] for a in rel.get("assets") or []
                  if a.get("name", "").lower().startswith("stock-terminal-setup") and a["name"].lower().endswith(".exe")),
                 None)
    return dict(available=newer, latest=latest or __version__, target=latest, changes=[],
                notes=(rel.get("body") or "")[:4000] or None, url=rel.get("html_url"), setup_url=setup,
                can_install=bool(setup) and sys.platform == "win32")   # Mac: download the new app from the release page


def check(refresh=False):
    """Is a newer version available? {current, mode, available, latest, target, changes | notes, can_install, error}.
    'target' identifies the newer version (a commit or a release) so the pop-up can skip exactly that one."""
    with _lock:
        hit = _cache.get("result")
        if hit and not refresh and time.time() - _cache["at"] < CHECK_CACHE_S:
            return hit
        m = mode()
        base = dict(current=__version__, mode=m)
        try:
            res = {**base, **(_check_git() if m == "git" else _check_installed())}
        except Exception as e:  # noqa: BLE001 - offline, GitHub down, ...: say so, never break the page
            res = {**base, "available": False, "error": f"Could not check for updates: {str(e)[:200]}"}
        res["checked_at"] = time.strftime("%Y-%m-%d %H:%M")
        _cache.update(at=time.time(), result=res)
        return res


def install():
    """Bring this copy to the newest version. git copy: pull, packages, self-test, roll back on failure.
    Returns {ok, version, message}; the app has to restart afterwards to use the new code."""
    if mode() != "git":
        return _install_release()
    with _lock:
        before = _git("rev-parse", "HEAD", timeout=10)
        req_before = (ROOT / "requirements.txt").read_bytes()
        try:
            _git("pull", "--ff-only", "--quiet", timeout=120)
        except Exception as e:  # noqa: BLE001
            return dict(ok=False, message=f"Could not download the new version: {e}. "
                                          "Local changes in the app folder can block it.")
        if (ROOT / "requirements.txt").read_bytes() != req_before:
            r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                                "-r", "requirements.txt"], cwd=ROOT, capture_output=True, text=True, timeout=900,
                               creationflags=_FLAGS)
            if r.returncode == 0:
                marker = ROOT / ".venv" / "requirements.installed"
                if marker.parent.exists():
                    marker.write_bytes((ROOT / "requirements.txt").read_bytes())
        test = subprocess.run([sys.executable, "-m", "stockterminal.selftest"], cwd=ROOT, capture_output=True,
                              text=True, timeout=600, creationflags=_FLAGS)
        logs = DATA_DIR / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "selftest.log").write_text(test.stdout + test.stderr, encoding="utf-8")
        if test.returncode != 0:
            try:
                _git("reset", "--keep", before, timeout=60)
            except Exception:  # noqa: BLE001
                pass
            _cache.clear()
            return dict(ok=False, message="The new version failed the self-test, so you keep the version you had. "
                                          "Details: data/logs/selftest.log.")
        _cache.clear()
        return dict(ok=True, version=_remote_version("HEAD") or __version__,
                    message="Updated. Restart the app to use the new version.")


def _install_release():
    """Installed app on Windows: download the new installer and run it silently. It closes this app, replaces the
    program files (never the data folder) and opens the new version by itself (packaging/installer.iss)."""
    info = check(refresh=True)
    if not info.get("available") or not info.get("setup_url"):
        return dict(ok=False, message="No newer installer found. Download the new version from the release page.")
    import tempfile
    path = os.path.join(tempfile.gettempdir(), f"Stock-Terminal-Setup-{info['latest']}.exe")
    req = urllib.request.Request(info["setup_url"], headers={"User-Agent": f"StockTerminal/{__version__}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(path, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
    except Exception as e:  # noqa: BLE001
        return dict(ok=False, message=f"Could not download the new version: {str(e)[:200]}")
    subprocess.Popen([path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                     creationflags=0x00000008 | 0x00000200)      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    return dict(ok=True, version=info["latest"], installing=True,
                message="Installing the new version. The app closes and opens again by itself.")


def restart_later():
    """Start a new copy of the app that waits until this one has closed (the window closes right after)."""
    kw = dict(cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    if FROZEN:                                             # the installed app: Stock Terminal.exe itself
        subprocess.Popen([sys.executable, "--after", str(os.getpid())], **kw)
        return
    exe = sys.executable
    if os.name == "nt" and exe.lower().endswith("python.exe"):
        quiet = exe[:-len("python.exe")] + "pythonw.exe"   # no console window for the new copy
        exe = quiet if os.path.exists(quiet) else exe
    subprocess.Popen([exe, "-m", "stockterminal.app", "--after", str(os.getpid())], **kw)


def wait_for_exit(pid, timeout=20):
    """Used by the new copy: wait until the old one (pid) has closed, so they never run side by side."""
    end = time.time() + timeout
    if os.name == "nt":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x00100000, False, int(pid))   # SYNCHRONIZE
        if h:
            ctypes.windll.kernel32.WaitForSingleObject(h, int(timeout * 1000))
            ctypes.windll.kernel32.CloseHandle(h)
        return
    while time.time() < end:
        try:
            os.kill(int(pid), 0)
        except OSError:
            return
        time.sleep(0.2)


if __name__ == "__main__":
    print(json.dumps(check(refresh=True), indent=2))
