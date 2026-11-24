"""Company logos: downloaded once per company, kept in data/logos/ (each install its own, never in git).

    python -m stockterminal.logos AAPL MSFT     # fetch these (again); -v shows every try
    python -m stockterminal.logos --missing     # fetch every company / ETF in the terminal that has none yet

Where a logo comes from, first hit wins:
1. A free public logo set on GitHub, by ticker (github.com/nvstly/icons, ~250 px PNG, ~8 KB).
2. The icon of the company's own website (the `website` saved with the company): the site's own app icon
   (/apple-touch-icon.png, ~180 px), else Google's icon service.
   An ETF uses the website of its fund company (iShares, Vanguard, ...).
Nothing found: the screen just shows the ticker, and the app asks again after MISS_DAYS.

The GitHub set is made for dark screens, so dark logos were turned white there (Apple, Visa, IBM). Those would be
invisible on a light screen. With Pillow installed, each logo is also saved in a version for a light background
(white parts that touch the edge recoloured dark) and for a dark one (black parts recoloured light), only when that
changes anything. Each file is shrunk to SIZE px: ~2-6 KB, a few MB for 500 companies.
"""
import datetime as dt
import io
import re
import sys
import threading
import time
from urllib.parse import urlparse

from .config import DATA_DIR

DIR = DATA_DIR / "logos"
SIZE = 96                     # shown at 16-40 px; 96 stays sharp on a high-resolution screen
MISS_DAYS = 30
VARIANTS = 3                  # raise when the light / dark recolour rules change: every install remakes them once
GITHUB = "https://raw.githubusercontent.com/nvstly/icons/main/ticker_icons/{}.png"
FAVICON = "https://www.google.com/s2/favicons?domain={}&sz=128"
TIMEOUT = 6
# some company sites refuse anything that doesn't look like a browser (403)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
      "Accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8"}
VALID = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,11}$")

# Fund company (Yahoo's fundFamily) -> its website, for ETF logos. Matched on the start of the name.
FAMILIES = {
    "ishares": "ishares.com", "blackrock": "blackrock.com", "vanguard": "vanguard.com", "spdr": "ssga.com",
    "state street": "ssga.com", "invesco": "invesco.com", "schwab": "schwab.com", "charles schwab": "schwab.com",
    "first trust": "ftportfolios.com", "jpmorgan": "jpmorgan.com", "j.p. morgan": "jpmorgan.com",
    "proshares": "proshares.com", "global x": "globalxetfs.com", "wisdomtree": "wisdomtree.com",
    "vaneck": "vaneck.com", "direxion": "direxion.com", "ark": "ark-funds.com", "fidelity": "fidelity.com",
    "dimensional": "dimensional.com", "pimco": "pimco.com", "goldman sachs": "gsam.com", "franklin": "franklintempleton.com",
    "xtrackers": "dws.com", "dws": "dws.com", "pacer": "paceretfs.com", "amplify": "amplifyetfs.com",
    "american century": "americancentury.com", "capital group": "capitalgroup.com", "janus": "janushenderson.com",
    "neos": "neosfunds.com", "yieldmax": "yieldmaxetfs.com", "grayscale": "grayscale.com", "sprott": "sprott.com",
    "avantis": "avantisinvestors.com", "jpmorgan chase": "jpmorgan.com", "defiance": "defianceetfs.com",
}

_locks, _locks_guard = {}, threading.Lock()


def clean(ticker):
    """Upper case, BRK-B and BRK.B as one name (one file)."""
    t = (ticker or "").strip().upper().replace("-", ".")
    return t if VALID.match(t) else None


def _files(t):
    return {"": DIR / f"{t}.png", "light": DIR / f"{t}.light.png", "dark": DIR / f"{t}.dark.png", "miss": DIR / f"{t}.miss"}


def _lock(t):
    with _locks_guard:
        return _locks.setdefault(t, threading.Lock())


def cached(t, bg="light"):
    """The saved file for a light or dark background, or None. Never downloads."""
    f = _files(t)
    if f[""].exists():
        return f[bg] if bg in ("light", "dark") and f[bg].exists() else f[""]
    return None


def recently_missed(t):
    m = _files(t)["miss"]
    return m.exists() and time.time() - m.stat().st_mtime < MISS_DAYS * 86400


def get(ticker, bg="light", download=True):
    """Path of the logo for a light / dark background, downloading it the first time. None = no logo."""
    t = clean(ticker)
    if not t:
        return None
    _upgrade()
    p = cached(t, bg)
    if p or not download or recently_missed(t):
        return p
    with _lock(t):                                  # the screen asks for the same logo from several places at once
        p = cached(t, bg)
        if p or recently_missed(t):
            return p
        fetch(t)
        return cached(t, bg)


# ------------------------------------------------------------------------------------------ sources
def _domain(url):
    if not url:
        return None
    u = url if "://" in url else "https://" + url
    host = (urlparse(u).hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    return host if "." in host else None


def _base(host):
    """my.xcelenergy.com -> xcelenergy.com (keeps co.uk-style endings)."""
    parts = host.split(".")
    n = 3 if len(parts) > 2 and len(parts[-1]) == 2 and parts[-2] in ("co", "com", "ac", "or", "ne") else 2
    return ".".join(parts[-n:])


def _info(t):
    """(website, fund family) from the database: companies, ETFs, then the portfolio's Yahoo profiles."""
    import json
    from . import db
    try:
        conn = db.connect()
    except Exception:  # noqa: BLE001
        return None, None
    try:
        r = conn.execute("SELECT website FROM companies WHERE ticker=? AND website IS NOT NULL LIMIT 1", (t,)).fetchone()
        if r and r[0]:
            return r[0], None
        r = conn.execute("SELECT family FROM etfs WHERE ticker=?", (t,)).fetchone()
        if r:
            return None, r[0]
        r = conn.execute("SELECT info FROM pf_info WHERE symbol=?", (t,)).fetchone()
        if r and r[0]:
            i = json.loads(r[0])
            return i.get("website"), i.get("fundFamily")
    except Exception:  # noqa: BLE001  an old database without one of the tables
        pass
    finally:
        conn.close()
    return None, None


def family_domain(family):
    f = (family or "").lower()
    for k in sorted(FAMILIES, key=len, reverse=True):
        if f.startswith(k):
            return FAMILIES[k]
    return None


def _get(url, why=None):
    import requests
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=UA)
    except Exception as e:  # noqa: BLE001  offline, timeout
        if why is not None:
            why.append(f"{url}: {type(e).__name__}")
        return None
    ok = r.status_code == 200 and r.headers.get("content-type", "").startswith("image/") and len(r.content) >= 100
    if why is not None:
        why.append(f"{url}: {r.status_code} {r.headers.get('content-type', '')} {len(r.content)} bytes")
    return r.content if ok else None


def _candidates(t):
    """(where, url, minimum size in px) in the order they are tried."""
    names = [t] + ([t.replace(".", "-"), t.replace(".", "")] if "." in t else [])
    out = [("logo set", GITHUB.format(n), 0) for n in dict.fromkeys(names)]
    website, family = _info(t)
    if not website and not family and "." in t:          # the database may spell it BRK-B
        website, family = _info(t.replace(".", "-"))
    if not website and not family:                       # not loaded (a watchlist ETF): ask Yahoo once
        website, family = _yahoo(t)
    d = _domain(website) or family_domain(family)
    if d:
        hosts = [d, _base(d)] if _base(d) != d else [d]
        for h in hosts:
            for host in (h, "www." + h):
                out.append(("website icon", f"https://{host}/apple-touch-icon.png", 48))
        for h in hosts:                                   # Google answers a 16 px globe for a site without an icon
            out.append(("website icon", FAVICON.format(h), 32))
    return out


def _yahoo(t):
    """(website, fund family) from Yahoo for a ticker the database doesn't know."""
    try:
        import yfinance as yf
        i = yf.Ticker(t.replace(".", "-")).info or {}
        return i.get("website"), i.get("fundFamily")
    except Exception:  # noqa: BLE001  offline, unknown ticker
        return None, None


def fetch(t, say=None, why=None):
    """Download the logo of one ticker. True when one was saved. `why`: a list that gets every try (for -v)."""
    DIR.mkdir(parents=True, exist_ok=True)
    f = _files(t)
    if why is not None:
        why.append(f"database: website / fund company = {_info(t)}")
    for where, url, min_px in _candidates(t):
        raw = _get(url, why)
        if not raw:
            continue
        saved = _save(t, raw, min_px)
        if why is not None and not saved:
            why.append("  not used (too small or not a picture)")
        if saved:
            f["miss"].unlink(missing_ok=True)
            if say:
                say(f"logo {t}: {where}")
            return True
    f["miss"].write_text(dt.date.today().isoformat(), encoding="utf-8")
    if say:
        say(f"logo {t}: none found")
    return False


# ------------------------------------------------------------------------------------------ images
def _save(t, raw, min_px):
    f = _files(t)
    try:
        from PIL import Image
    except ImportError:                             # without Pillow: keep the file as it came (PNG only)
        if not raw.startswith(b"\x89PNG") or (min_px and len(raw) < 400):
            return False
        f[""].write_bytes(raw)
        return True
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception:  # noqa: BLE001  not a picture
        return False
    if max(im.size) < min_px:
        return False
    im = im.convert("RGBA")
    bbox = im.getchannel("A").getbbox()             # cut empty margins, so every logo fills its box the same way
    if not bbox:
        return False
    im = im.crop(bbox)
    w, h = im.size
    side = max(w, h)
    sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    sq.paste(im, ((side - w) // 2, (side - h) // 2))
    if side > SIZE:
        sq = sq.resize((SIZE, SIZE), Image.LANCZOS)
    _png(sq, f[""])
    _variants(t, sq)
    return True


def _variants(t, im):
    """(Re)make the versions for a light and a dark background from the saved logo."""
    f = _files(t)
    for p in (f["light"], f["dark"]):
        p.unlink(missing_ok=True)
    if _is_tile(im):                                # a filled square icon (SPY's green tile) works on any background
        return
    light = _recolour(im, lambda r, g, b: min(r, g, b) > 205 and max(r, g, b) - min(r, g, b) < 30, (28, 28, 32))
    if light:
        _png(light, f["light"])
    dark = _recolour(im, lambda r, g, b: max(r, g, b) < 90 and max(r, g, b) - min(r, g, b) < 30, (236, 236, 240))
    if dark:
        _png(dark, f["dark"])


def _is_tile(im):
    """True when the logo is a filled square or rounded square: opaque near all four corners. Recolouring its
    background (dark green, black) would leave blotches."""
    w, h = im.size
    px = im.load()
    m = max(1, round(min(w, h) * 0.08))             # just inside, so rounded corners count too
    return all(px[x, y][3] > 200 for x, y in ((m, m), (w - 1 - m, m), (m, h - 1 - m), (w - 1 - m, h - 1 - m)))


_upgraded = False


def _upgrade():
    """Once per install after the recolour rules change (VARIANTS): remake every light / dark version."""
    global _upgraded
    if _upgraded:
        return
    with _locks_guard:
        if _upgraded:
            return
        _upgraded = True
        mark = DIR / f".variants{VARIANTS}"
        if not DIR.exists() or mark.exists():
            return
        try:
            from PIL import Image
            for p in DIR.glob("*.png"):
                if p.name.count(".") == 1:
                    with Image.open(p) as im:
                        _variants(p.stem, im.convert("RGBA"))
            mark.write_text("", encoding="utf-8")
        except Exception:  # noqa: BLE001  without Pillow: keep what is there
            pass


def _png(im, path):
    tmp = path.with_suffix(".tmp")
    im.save(tmp, "PNG", optimize=True)
    tmp.replace(path)


def _recolour(im, match, colour, share=0.4):
    """A copy where the parts of `match` colour that touch the transparent outside (white letters on nothing)
    get `colour`. Parts enclosed by other colours stay (the white P in Paychex's blue square). None = no change."""
    w, h = im.size
    px = im.load()
    hit = [[px[x, y][3] > 20 and match(*px[x, y][:3]) for x in range(w)] for y in range(h)]
    seen = [[False] * w for _ in range(h)]
    out, changed = None, 0
    opaque = sum(1 for y in range(h) for x in range(w) if px[x, y][3] > 20) or 1
    for y0 in range(h):
        for x0 in range(w):
            if not hit[y0][x0] or seen[y0][x0]:
                continue
            comp, stack, outside = [], [(x0, y0)], False
            seen[y0][x0] = True
            while stack:
                x, y = stack.pop()
                comp.append((x, y))
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if not (0 <= nx < w and 0 <= ny < h):
                        outside = True
                    elif hit[ny][nx]:
                        if not seen[ny][nx]:
                            seen[ny][nx] = True
                            stack.append((nx, ny))
                    elif px[nx, ny][3] < 128:
                        outside = True
            if outside:
                if out is None:
                    out = im.copy()
                    op = out.load()
                for x, y in comp:
                    op[x, y] = (*colour, px[x, y][3])
                changed += len(comp)
    return out if out is not None and changed / opaque >= share else None


# ------------------------------------------------------------------------------------------ batch
def all_tickers():
    from . import db
    conn = db.connect()
    try:
        ts = [r[0] for r in conn.execute("SELECT ticker FROM companies WHERE ticker IS NOT NULL")]
        ts += [r[0] for r in conn.execute("SELECT ticker FROM etfs")]
    finally:
        conn.close()
    return sorted({c for c in map(clean, ts) if c})


def fill_missing(say=print, limit=None):
    """Fetch logos for every company / ETF without one (skipping recent misses). Returns (found, missed)."""
    todo = [t for t in all_tickers() if not cached(t) and not recently_missed(t)]
    if limit:
        todo = todo[:limit]
    found = sum(1 for t in todo if fetch(t))
    if say:
        say(f"logos: {found} new, {len(todo) - found} not found")
    return found, len(todo) - found


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--missing"]:
        fill_missing()
        return
    verbose = "-v" in argv
    for a in argv:
        t = clean(a)
        if t and a != "-v":
            why = [] if verbose else None
            _files(t)["miss"].unlink(missing_ok=True)
            fetch(t, say=print, why=why)
            for line in why or []:
                print("   ", line)


if __name__ == "__main__":
    main()
