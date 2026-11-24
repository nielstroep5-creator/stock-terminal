"""Per-user settings, saved in data/settings.json (each install keeps its own; data/ is not in git).

Saved on disk rather than in the browser: the app window runs in private mode and forgets browser
storage every time it closes. New setting: add a default (and its allowed values) here and a row in
the Settings page (ROWS in ui/src/pages/Settings.tsx).
"""
import json
import os
import re
import threading

from .config import DATA_DIR

PATH = DATA_DIR / "settings.json"
DEFAULTS = {
    "theme": "light",            # light | dark | system (follow Windows)
    "sec_email": "",             # contact email the SEC requires on every download
    "default_period": "FY",      # first period shown on Financials / Ratios / Segments
    "price_range": "1Y",         # first range of the price chart
    "show_extended": True,       # pre-market / after-hours / overnight price under the quote
    "range_change": "always",    # % change bubble of the price chart range: always (selected range) | hover
    "compare_years": "fy",       # Compare, annual: fiscal years (by the year they end in) | calendar years (Jan-Dec)
    "page_width": "full",        # pages use the whole window (full) or a centred column (centered)
    "daily_update": True,        # the 07:00 update (prices, new filings, analyst data) runs; off = the scheduled task skips it
    "earnings_watch": True,      # every 5 minutes on a company's results day: notice the results, load the 10-Q, alert (earnings_watch.py)
    "chart_style": "line",       # price charts of a stock or ETF: line | candles (open, high, low, close per day)
    "show_logos": True,          # company logos next to tickers (logos.py, downloaded once into data/logos)
    "update_check": True,        # at start, look for a newer version of the app and offer it (pop-up; stockterminal/appupdate.py)
    "logo_style": "dark",        # plain (no background) | dark (dark app-icon tile, like fiscal.ai) | soft (tile in the page colour) | round
    "prefs": {},                 # the last choice of every view switch (chart ranges, tabs, Compare...), see useSettings in ui/src/lib/settings.tsx
}
CHOICES = {
    "theme": {"light", "dark", "system"},
    "default_period": {"FY", "Q", "TTM"},
    "price_range": {"1M", "3M", "6M", "YTD", "1Y", "3Y", "5Y", "10Y", "Max"},
    "show_extended": {True, False},
    "range_change": {"always", "hover"},
    "compare_years": {"fy", "cal"},
    "page_width": {"full", "centered"},
    "daily_update": {True, False},
    "earnings_watch": {True, False},
    "chart_style": {"line", "candles"},
    "show_logos": {True, False},
    "logo_style": {"plain", "dark", "soft", "round"},
    "update_check": {True, False},
}
_lock = threading.Lock()     # the server handles requests in parallel: one save at a time
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# Size limits for the remembered view choices. Generous on purpose: saved Screener screens and Compare charts
# are kept as one text each, and a list of them grows with every one you save.
PREF_GROUPS, PREF_KEYS, PREF_TEXT = 200, 2000, 200_000


def _simple(v):
    return v is None or isinstance(v, (bool, int, float)) or (isinstance(v, str) and len(v) <= PREF_TEXT)


def _clean_prefs(value):
    """{name: value} or {group: {name: value}} with plain values. Entries that don't fit are dropped one by one
    (never the whole block), so one oversized choice can't stop every other choice from being saved."""
    if not isinstance(value, dict):
        return None
    out = {}
    for k, v in list(value.items())[:PREF_GROUPS]:
        if not isinstance(k, str) or len(k) > 64:
            continue
        if isinstance(v, dict):
            out[k] = {a: b for a, b in list(v.items())[:PREF_KEYS] if isinstance(a, str) and len(a) <= 64 and _simple(b)}
        elif _simple(v):
            out[k] = v
    return out


def _valid_prefs(value):
    return _clean_prefs(value) == value


def _valid(key, value):
    if key == "prefs":
        return _valid_prefs(value)
    if key in CHOICES:
        return value in CHOICES[key] and type(value) is type(DEFAULTS[key])
    if key == "sec_email":
        return isinstance(value, str) and (value == "" or bool(EMAIL.match(value)))
    return False


BROKEN = PATH.with_name("settings.broken.json")


def read():
    try:
        saved = json.loads(PATH.read_text(encoding="utf-8"))
    except OSError:
        saved = {}
    except ValueError:
        # a damaged file (edited by hand, disk full): start from the defaults, but keep a copy first, or
        # the next saved choice would overwrite it and every remembered setting would be gone for good
        _keep_broken()
        saved = {}
    out = {k: saved[k] if k in saved and _valid(k, saved[k]) else d for k, d in DEFAULTS.items()}
    if "prefs" in saved and out["prefs"] is not saved["prefs"]:
        out["prefs"] = _clean_prefs(saved["prefs"]) or {}     # keep what is still good
    return out


def _keep_broken():
    try:
        text = PATH.read_text(encoding="utf-8")
        if not BROKEN.exists() or BROKEN.read_text(encoding="utf-8") != text:
            BROKEN.write_text(text, encoding="utf-8")
            print(f"settings.json could not be read: a copy is kept in {BROKEN}; using the default settings")
    except OSError:
        pass


def broken_copy():
    """The kept copy of a damaged settings file of the last 7 days (else None): the Settings page says so."""
    import time
    try:
        if BROKEN.exists() and time.time() - BROKEN.stat().st_mtime < 7 * 86400:
            return str(BROKEN)
    except OSError:
        pass
    return None


def update(changes):
    """Validate and save; raises ValueError naming the bad setting."""
    with _lock:
        return _update(changes)


def _update(changes):
    cur = read()
    for k, v in changes.items():
        if k not in DEFAULTS:
            raise ValueError(f"unknown setting: {k}")
        if isinstance(v, str):
            v = v.strip()
        if k == "prefs":
            v = _clean_prefs(v)
            if v is None:
                raise ValueError("invalid value for prefs")
        elif not _valid(k, v):
            raise ValueError("That doesn't look like an email address." if k == "sec_email" else f"invalid value for {k}")
        cur[k] = v
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(f".{os.getpid()}.tmp")   # another process (daily update) never shares it
    tmp.write_text(json.dumps(cur, indent=2), encoding="utf-8")
    tmp.replace(PATH)
    return cur
