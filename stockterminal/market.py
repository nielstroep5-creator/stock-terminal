"""Market overview for the home screen: the main US indexes, live quote plus a small chart.

Display only, nothing is stored. Charts are cached in memory (short for intraday, longer for history).
"""
import threading
import time

from . import quote

INDEXES = [("SPY", "S&P 500"), ("QQQ", "Nasdaq 100"), ("DIA", "Dow Jones")]   # ETFs: they also trade pre-market, after hours and overnight
# range -> (yfinance period, interval, cache seconds)
RANGES = {"1D": ("1d", "5m", 120), "5D": ("5d", "30m", 300), "1M": ("1mo", "1d", 3600),
          "1Y": ("1y", "1d", 3600), "5Y": ("5y", "1wk", 3600)}

_cache = {}
_lock = threading.Lock()


def _history(symbol, rng):
    period, interval, ttl = RANGES[rng]
    key = (symbol, rng)
    with _lock:
        hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        import yfinance as yf
        h = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=False)
        h = h.dropna(subset=["Close"])
        intraday = interval.endswith("m")
        pts = dict(t=[int(ts.timestamp()) for ts in h.index] if intraday else [ts.strftime("%Y-%m-%d") for ts in h.index],
                   close=[round(float(v), 2) for v in h["Close"]])
    except Exception:
        pts = None
    with _lock:
        # a failed download is retried after 20 seconds instead of the full cache time
        _cache[key] = (time.time() if pts else time.time() - ttl + 20, pts)
    return pts


def overview(rng="1D"):
    rng = rng if rng in RANGES else "1D"
    qs = quote.get_quotes([s for s, _ in INDEXES])
    out = []
    for sym, name in INDEXES:
        out.append(dict(symbol=sym, name=name, quote=qs.get(sym) or _stored_quote(sym),
                        chart=_history(sym, rng) or _stored_chart(sym)))
    return dict(range=rng, indexes=out)


def _stored_quote(sym):
    """No connection: the last two closes in the database (SPY/QQQ/DIA come with every install), shaped
    like a live quote, so the cards show the last close instead of a dash."""
    try:
        from . import db
        conn = db.connect()
        try:
            rows = conn.execute("SELECT date, close FROM prices WHERE ticker=? AND close IS NOT NULL "
                                "ORDER BY date DESC LIMIT 2", (sym,)).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return None
    if len(rows) < 2:
        return None
    (d, c), (_, prev) = rows
    return dict(symbol=sym, market_state=None, extended=None, stored=True,
                regular=dict(price=c, change=c - prev, change_pct=c / prev - 1, time=None,
                             previous_close=prev, date=d))


def _stored_chart(sym, days=5):
    """No connection: the last few closes in the database, so the card still has a small chart
    (daily points, labelled as such)."""
    try:
        from . import db
        conn = db.connect()
        try:
            rows = conn.execute("SELECT date, close FROM prices WHERE ticker=? AND close IS NOT NULL "
                                "ORDER BY date DESC LIMIT ?", (sym, days + 1)).fetchall()[::-1]
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return None
    if len(rows) < 2:
        return None
    return dict(t=[d for d, _ in rows], close=[round(c, 2) for _, c in rows], daily=True)
