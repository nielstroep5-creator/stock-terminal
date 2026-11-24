"""Shared paths and settings.

Two ways the app runs:
- from a copy of the code (git clone / zip): ROOT = that folder, data in ROOT/data (as always);
- as the installed app (Stock Terminal.exe / .app, built with PyInstaller, see packaging/): ROOT = the unpacked app
  files (read-only, replaced by every update), data in the user's own folder (DATA_DIR below), so updating or
  reinstalling never touches it.
STOCKTERMINAL_DATA overrides the data folder in both cases.
"""
import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))          # True in the installed app
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def _user_data_dir():
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Stock Terminal" / "data"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Stock Terminal" / "data"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "stock-terminal" / "data"


DATA_DIR = Path(os.environ["STOCKTERMINAL_DATA"]) if os.environ.get("STOCKTERMINAL_DATA") else (
    _user_data_dir() if FROZEN else ROOT / "data")


def module_cmd(module, *args, unbuffered=False):
    """The command that runs one of the app's modules in its own process (daily update, loading companies, ...):
    `python -m module` from a code copy; the app's own helper program in the installed app (no Python there)."""
    if FROZEN:
        helper = Path(sys.executable).with_name("stockterminal-run.exe" if os.name == "nt" else "stockterminal-run")
        return [str(helper), module, *args]
    return [sys.executable, *(["-u"] if unbuffered else []), "-m", module, *args]
RAW_DIR = DATA_DIR / "raw"
DB_PATH = DATA_DIR / "terminal.db"

# SEC requires automated tools to identify themselves WITH a contact email,
# otherwise it may answer 403 Forbidden. Each user enters theirs in the app's Settings
# (saved in data/settings.json). Also accepted: the STOCKTERMINAL_CONTACT environment variable,
# or a file named sec_contact.txt in the project folder (older setup; not committed to git).

_contact_file = ROOT / "sec_contact.txt"
PLACEHOLDER_CONTACT = "stockterminal@example.com"


def sec_contact():
    from . import settings          # read on every call: an email saved in Settings applies at once
    return (os.environ.get("STOCKTERMINAL_CONTACT")
            or settings.read()["sec_email"]
            or (_contact_file.read_text(encoding="utf-8").strip() if _contact_file.exists() else "")
            or PLACEHOLDER_CONTACT)


def sec_user_agent():
    return f"StockTerminal personal research tool {sec_contact()}"


SEC_MAX_REQUESTS_PER_SEC = 9     # SEC allows 10 per second

# How long cached raw files are considered fresh (hours).
CACHE_HOURS_TICKERS = 24 * 7
CACHE_HOURS_COMPANY = 24
