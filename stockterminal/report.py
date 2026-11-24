"""Problem reports (Settings > About > Report a problem, and "Report this" on an error page).

Nothing is ever sent by itself: the app puts a short report together (version, Windows / Mac, the last errors from
data/logs) and the user reads it, then copies it or opens it as a new issue on the public download page on GitHub.
Email addresses and the user's name in folder paths are taken out first.
"""
import platform
import re
import sys
import traceback
import urllib.parse
from datetime import datetime

from . import __version__
from .config import DATA_DIR, FROZEN

ERRORS = DATA_DIR / "logs" / "app_errors.log"
ISSUE_URL_MAX = 7000                   # browsers and GitHub cut very long links

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_USER_PATH = re.compile(r"(?i)([\\/](?:Users|home)[\\/])[^\\/\s\"']+")


def scrub(text):
    """No email addresses, and no user name from the home folder in a path."""
    return _USER_PATH.sub(r"\1<you>", _EMAIL.sub("<email>", text or ""))


def log_error(where, exc):
    """A server error (any request that failed) goes to the same log as errors on the screen."""
    ERRORS.parent.mkdir(parents=True, exist_ok=True)
    if ERRORS.exists() and ERRORS.stat().st_size > 1_000_000:
        ERRORS.replace(ERRORS.with_suffix(".old.log"))
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-6000:]
    with ERRORS.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  server {where[:200]}\n  {type(exc).__name__}: {str(exc)[:1000]}\n{tb}\n")


def _last_errors(n=3):
    if not ERRORS.exists():
        return [], None
    text = ERRORS.read_text(encoding="utf-8", errors="replace")
    blocks = [b.strip() for b in re.split(r"\n\s*\n(?=\d{4}-\d\d-\d\d )", text) if b.strip()]
    last = blocks[-1][:19] if blocks else None
    return blocks[-n:], last


def build(page=None, note=None):
    """The report: {title, text, issue_url, last_error (time of the newest logged error, or None)}"""
    errors, last = _last_errors()
    system = f"{platform.system()} {platform.release()} ({platform.machine()})"
    kind = "installed app" if FROZEN else "code copy"
    lines = ["**What happened?**", (note or "").strip() or "(describe what you did and what went wrong)", "",
             f"**Stock Terminal** {__version__} ({kind}), {system}, Python {sys.version.split()[0]}"]
    if page:
        lines.append(f"**Page:** {page}")
    if errors:
        lines += ["", "**Last errors** (from data/logs/app_errors.log)", "```"]
        lines += ["\n\n".join(e[-2500:] for e in errors), "```"]
    else:
        lines += ["", "No errors in the log."]
    text = scrub("\n".join(lines))
    title = "Problem: " + scrub((errors[-1].splitlines()[1].strip() if errors and len(errors[-1].splitlines()) > 1
                                 else "describe it here"))[:90]
    from .appupdate import RELEASES_REPO
    body, cut = text, len(text)
    while True:
        url = (f"https://github.com/{RELEASES_REPO}/issues/new?"
               + urllib.parse.urlencode({"title": title, "body": body}))
        if len(url) <= ISSUE_URL_MAX or cut < 400:
            break
        cut = int(cut * 0.8)
        body = text[:cut] + ("\n```" if "```" in text[:cut] and text[:cut].count("```") % 2 else "") \
            + "\n(shortened: Copy in the app has the whole report)"
    return dict(title=title, text=text, issue_url=url, last_error=last)
