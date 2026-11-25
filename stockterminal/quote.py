"""Live quotes incl. pre-market, after-hours and overnight (24-hour session) prices from Yahoo.

Display only: valuation statistics (P/E, market cap, ...) stay on the regular-session price.

Yahoo's v7 quote endpoint returns the overnight fields only when `overnightPrice=true` is passed, and
that response then drops the pre/post fields, so two batched calls are made: one plain, one overnight.
"""
import threading
import time

TTL = 30            # seconds a quote stays cached
URL = "https://query1.finance.yahoo.com/v7/finance/quote"
SESSIONS = {"PRE": "pre", "PREPRE": "pre", "POST": "post", "POSTPOST": "post", "OVERNIGHT": "overnight"}

_cache = {}         # ticker -> (fetched_at, quote or None)
_lock = threading.Lock()
_data = None
_health = {"ok": 0.0, "fail": 0.0}   # when Yahoo last answered / last gave nothing (top bar: live or offline)


def status():
    """'live' when Yahoo answered last time, 'offline' when it gave nothing, 'unknown' before the first try."""
    ok, fail = _health["ok"], _health["fail"]
    return "unknown" if not ok and not fail else ("live" if ok >= fail else "offline")


def _yf():
    global _data
    if _data is None:
        from yfinance.data import YfData   # shares yfinance's cookie + crumb handling
        _data = YfData(session=None)
    return _data


def yahoo_symbol(ticker):
    return ticker.upper().replace(".", "-")     # BRK.B -> BRK-B


def _fetch(symbols, overnight):
    params = {"symbols": ",".join(symbols)}
    if overnight:
        params["overnightPrice"] = "true"
    r = _yf().get(url=URL, params=params, timeout=10)
    return {q["symbol"]: q for q in r.json().get("quoteResponse", {}).get("result") or []}


def _fetch_many(symbols, overnight, chunk=100):
    """Many symbols (the Screener asks for 500): parallel requests of 100 instead of one huge one, which
    took about 12 seconds. A failed chunk only loses its own symbols."""
    from concurrent.futures import ThreadPoolExecutor
    parts = [symbols[i:i + chunk] for i in range(0, len(symbols), chunk)]

    def one(part):
        try:
            return _fetch(part, overnight)
        except Exception:  # noqa: BLE001
            return {}
    if len(parts) == 1:
        return one(parts[0])
    out = {}
    with ThreadPoolExecutor(min(6, len(parts))) as ex:
        for got in ex.map(one, parts):
            out.update(got)
    return out


def _build(plain, night):
    p, n = plain or {}, night or {}
    state = n.get("marketState") or p.get("marketState")
    session = SESSIONS.get(state)
    ext = None
    if session == "overnight" and n.get("overnightMarketPrice") is not None:
        ext = dict(price=n["overnightMarketPrice"], change=n.get("overnightMarketChange"),
                   change_pct=_pct(n.get("overnightMarketChangePercent")), time=n.get("overnightMarketTime"))
    elif session in ("pre", "post") and p.get(f"{session}MarketPrice") is not None:
        k = f"{session}Market"
        ext = dict(price=p[f"{k}Price"], change=p.get(f"{k}Change"),
                   change_pct=_pct(p.get(f"{k}ChangePercent")), time=p.get(f"{k}Time"))
    if ext:
        ext["session"], ext["live"] = session, True
    src = p or n
    if not ext and state != "REGULAR":
        # no extended session running (e.g. Friday 8 PM to Sunday 8 PM ET): keep showing the latest
        # extended-hours trade made after the last close, so the weekend shows where it ended and it
        # hands over naturally to Monday's pre-market
        reg_time = src.get("regularMarketTime") or 0
        cands = []
        for q, k, sess in ((n, "overnightMarket", "overnight"), (p, "postMarket", "post"), (n, "postMarket", "post"),
                           (p, "preMarket", "pre"), (n, "preMarket", "pre")):
            if q.get(f"{k}Price") is not None and (q.get(f"{k}Time") or 0) > reg_time:
                cands.append((q[f"{k}Time"], dict(price=q[f"{k}Price"], change=q.get(f"{k}Change"),
                                                  change_pct=_pct(q.get(f"{k}ChangePercent")), time=q[f"{k}Time"],
                                                  session=sess, live=False)))
        if cands:
            ext = max(cands, key=lambda c: c[0])[1]
    if src.get("regularMarketPrice") is None:
        return None
    return dict(symbol=src.get("symbol"), market_state=state, extended=ext,
                regular=dict(price=src.get("regularMarketPrice"), change=src.get("regularMarketChange"),
                             change_pct=_pct(src.get("regularMarketChangePercent")),
                             time=src.get("regularMarketTime"), previous_close=src.get("regularMarketPreviousClose")))


def _pct(v):
    return None if v is None else v / 100          # Yahoo gives percent units; the app uses fractions


def get_quotes(tickers, refresh=False):
    """{ticker: quote or None}. Never raises; cached for TTL seconds (refresh=True bypasses the cache)."""
    tickers = [t.upper() for t in tickers if t]
    now = time.time()
    with _lock:
        need = [t for t in tickers if refresh or t not in _cache or now - _cache[t][0] > TTL]
    if need:
        syms = {t: yahoo_symbol(t) for t in need}
        plain = _fetch_many(list(syms.values()), False)
        _health["ok" if plain else "fail"] = time.time()
        # the overnight call is only needed outside the day sessions (Yahoo reports POSTPOST/PREPRE/CLOSED
        # there without the flag); during pre / regular / post one request is enough
        night = {}
        if not plain or any(q.get("marketState") not in ("PRE", "REGULAR", "POST") for q in plain.values()):
            night = _fetch_many(list(syms.values()), True)
        with _lock:
            for t, s in syms.items():
                try:
                    q = _build(plain.get(s), night.get(s)) if (s in plain or s in night) else None
                except Exception:
                    q = None
                # failures are cached briefly too, so a dead connection does not stall every request
                _cache[t] = (now if q else now - TTL + 10, q)
    with _lock:
        return {t: _cache.get(t, (0, None))[1] for t in tickers}


def get_quote(ticker, refresh=False):
    return get_quotes([ticker], refresh).get(ticker.upper())


def cached(ticker):
    """Quote from the cache only (no network), or None."""
    with _lock:
        hit = _cache.get(ticker.upper())
    return hit[1] if hit and time.time() - hit[0] <= TTL * 4 else None
