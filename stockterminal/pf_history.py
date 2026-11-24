"""Portfolio over time: value and return of your Trading 212 holdings, next to the S&P 500 and the FTSE All-World.

Built from your own trades (pf_trades) and dividends (pf_dividends), valued every day with Yahoo's daily
closes (kept in pf_prices, on this PC only) and converted with Yahoo's daily exchange rates.

- Value: what your holdings were worth each day, plus "dividends" (received so far) and "invested": the money you put into them
  (buys minus sells, as Trading 212 booked them in your account currency).
- Return: time-weighted, so buying or selling does not count as gain or loss; only price moves and
  dividends do. That makes it comparable with an index:  r = (V_today + dividends) / (V_yesterday + net buys) - 1
- Benchmarks with dividends reinvested: SPY for the S&P 500, VWCE (Xetra) for the FTSE All-World.

Every series comes in the account currency and in the other one of EUR / USD (each day's own rate), so
the € / $ switch shows what a euro or a dollar investor would have seen. Cash is left out.

Splits: Yahoo's closes are split-adjusted to today's shares, so every trade's quantity is scaled by the
splits after it (Trading 212's own STOCK_SPLIT entries are skipped). The final quantities are checked
against Trading 212's positions; a holding that does not add up is named in the warnings.
"""
import threading
import time

from . import db, portfolio, trading212

BENCHMARKS = [("sp500", "S&P 500", "SPY", "USD"), ("ftse", "FTSE All-World", "VWCE.DE", "EUR")]
REFRESH_HOURS = 3          # Yahoo history is fetched again at most this often per symbol
_lock = threading.Lock()


# ------------------------------------------------------------------------------------------ Yahoo history
def _download(symbol, start):
    """[(date, close, adj_close, split)] from Yahoo, or raises."""
    import pandas as pd
    import yfinance as yf
    h = yf.Ticker(symbol).history(start=start, auto_adjust=False, actions=True)
    if h is None or h.empty:
        raise RuntimeError(f"Yahoo has no history for {symbol}")
    num = lambda v: None if v is None or pd.isna(v) else float(v)  # noqa: E731
    return [(pd.Timestamp(i).strftime("%Y-%m-%d"), num(r.get("Close")), num(r.get("Adj Close")),
             num(r.get("Stock Splits")) or None) for i, r in h.iterrows()]


def refresh_prices(conn, symbols, start, errors, download=_download):
    """Bring pf_prices up to date for these symbols from `start` (YYYY-MM-DD). Only the last days are
    fetched again, unless a new split appeared (then the whole history: older closes change with it)."""
    fetched = trading212.meta(conn).get("prices_fetched") or {}
    now = time.time()
    for s in dict.fromkeys(symbols):
        if fetched.get(s) and now - fetched[s] < REFRESH_HOURS * 3600:
            continue
        first, last = conn.execute("SELECT MIN(date), MAX(date) FROM pf_prices WHERE symbol=?", (s,)).fetchone()
        full = not first or first > start
        try:
            if full:
                rows = download(s, start)
            else:
                since = time.strftime("%Y-%m-%d", time.strptime(last, "%Y-%m-%d"))
                rows = download(s, _days_before(since, 10))
                known = {d for d, in conn.execute("SELECT date FROM pf_prices WHERE symbol=? AND split IS NOT NULL", (s,))}
                if any(sp and d not in known for d, _, _, sp in rows):      # a new split: refetch everything
                    rows, full = download(s, start), True
            if full:
                conn.execute("DELETE FROM pf_prices WHERE symbol=?", (s,))
            conn.executemany("INSERT OR REPLACE INTO pf_prices VALUES (?,?,?,?,?)", [(s, *r) for r in rows])
            conn.commit()
            fetched[s] = now
        except Exception as e:  # noqa: BLE001  shown as a warning; the chart uses what it has
            errors.append(f"{s}: {e}")
    trading212._set_meta(conn, prices_fetched=fetched)


def _days_before(day, n):
    return time.strftime("%Y-%m-%d", time.localtime(time.mktime(time.strptime(day, "%Y-%m-%d")) - n * 86400))


# ------------------------------------------------------------------------------------------ symbols
def _symbols(conn, instruments, errors, download=_download):
    """{t212_ticker: Yahoo symbol} for every instrument ever traded. Open positions come from the
    Portfolio page's matching (portfolio.resolve_symbols); sold ones are matched here: same currency on
    Yahoo and a close within 6% of the trade price on the trade day."""
    from .quote import yahoo_symbol
    known = dict(conn.execute("SELECT t212_ticker, yahoo FROM pf_symbols WHERE yahoo IS NOT NULL"))
    out = {}
    for t, inst in instruments.items():
        us = portfolio.app_ticker(t, inst["isin"])
        if us:
            out[t] = yahoo_symbol(us)
        elif t in known:
            out[t] = known[t]
    todo = [t for t in instruments if t not in out]
    if not todo:
        return out
    cands = {t: portfolio.candidates(t, instruments[t]["isin"], instruments[t]["currency"]) for t in todo}
    quotes = portfolio._yahoo_quotes([s for c in cands.values() for s in c])
    for t in todo:
        inst = instruments[t]
        want_cur = portfolio._major(1, inst["currency"])[1]
        for s in cands[t]:
            q = quotes.get(s) or {}
            if portfolio._major(1, q.get("currency"))[1] != want_cur:
                continue
            try:
                rows = download(s, _days_before(inst["date"], 7))
            except Exception:  # noqa: BLE001
                continue
            close = next((c for d, c, _, _ in reversed(rows) if d <= inst["date"] and c), None)
            ref = portfolio._major(inst["price"], inst["currency"])[0]
            got = portfolio._major(close, q.get("currency"))[0]
            if got and ref and abs(got / ref - 1) < 0.06:      # (an old split can still spoil this; rare)
                out[t] = s
                conn.execute("INSERT OR REPLACE INTO pf_symbols VALUES (?,?,?)", (t, s, time.strftime("%Y-%m-%d")))
                conn.commit()
                break
        else:
            errors.append(f"no Yahoo symbol found for {inst['name'] or t}")
    return out


# ------------------------------------------------------------------------------------------ build
def build(refresh=True, download=_download, snapshots=()):
    """snapshots: dates (YYYY-MM-DD) for which to also return what you held at that day's close (holdings_on)."""
    import pandas as pd
    with _lock:
        conn = db.connect()
        try:
            return _build(conn, refresh, download, pd, snapshots)
        finally:
            conn.close()


def _build(conn, refresh, download, pd, snapshots=()):
    m = trading212.meta(conn)
    acc = m.get("currency") or "EUR"
    other = portfolio.PAIR.get(acc)
    warnings, errors = [], []
    trades = conn.execute("""SELECT t212_ticker, isin, name, instrument_currency, side, fill_type, quantity, price,
                                    substr(filled_at, 1, 10), net_value
                             FROM pf_trades WHERE fill_id IS NOT NULL AND filled_at IS NOT NULL AND quantity IS NOT NULL
                             ORDER BY filled_at""").fetchall()
    if not trades:
        # connected, but no filled orders in the database: the trade history was never synced (or the
        # positions came in by a transfer, without orders)
        return dict(empty=True, acc=acc, other=other, reason="no_trades", last_sync=m.get("last_sync"))
    start = trades[0][8]

    instruments = {}
    for t, isin, name, cur, side, ftype, qty, price, day, _ in trades:
        if t not in instruments:
            instruments[t] = dict(isin=isin, name=name, currency=cur, price=price, date=day)
    symbols = _symbols(conn, instruments, errors, download) if refresh else \
        {t: s for t, s in conn.execute("SELECT t212_ticker, yahoo FROM pf_symbols WHERE yahoo IS NOT NULL")}
    from .quote import yahoo_symbol
    for t in instruments:                                         # US ones are known without a lookup
        us = portfolio.app_ticker(t, instruments[t]["isin"])
        if us:
            symbols[t] = yahoo_symbol(us)

    currencies = {portfolio._major(1, i["currency"])[1] for i in instruments.values()} | {b[3] for b in BENCHMARKS}
    fx_sym = {c: f"{acc}{c}=X" for c in currencies | ({other} if other else set()) if c and c != acc}
    need = list(dict.fromkeys(list(symbols.values()) + [b[2] for b in BENCHMARKS] + list(fx_sym.values())))
    if refresh:
        refresh_prices(conn, need, _days_before(start, 7), errors, download)

    rows = conn.execute(f"SELECT symbol, date, close, adj_close, split FROM pf_prices WHERE date >= ? AND symbol IN "
                        f"({','.join('?' * len(need))})", (_days_before(start, 7), *need)).fetchall()
    if not rows:
        return dict(empty=True, acc=acc, other=other, reason="no_prices",
                    warnings=errors or ["No price history from Yahoo yet."])
    px = pd.DataFrame(rows, columns=["symbol", "date", "close", "adj", "split"])
    close = px.pivot(index="date", columns="symbol", values="close").sort_index()
    adj = px.pivot(index="date", columns="symbol", values="adj").sort_index()
    held = [s for s in dict.fromkeys(symbols.values()) if s in close.columns]
    days = close[held].dropna(how="all").index if held else close.index
    days = days[days >= start]
    close, adj = close.ffill().loc[days], adj.ffill().loc[days]
    splits = {s: [(d, r) for d, r in px[(px.symbol == s) & px.split.notna()][["date", "split"]].itertuples(index=False)]
              for s in held}

    def fx(cur):        # units of `cur` per 1 account-currency unit, per day
        if cur == acc:
            return pd.Series(1.0, index=days)
        s = fx_sym.get(cur)
        return close[s].bfill() if s in close.columns else None

    # quantities (in today's shares) and money flows per instrument and day
    qty = pd.DataFrame(0.0, index=days, columns=held)
    flow = pd.Series(0.0, index=days)
    skipped = set()
    for t, isin, name, cur, side, ftype, q, price, day, net in trades:
        s = symbols.get(t)
        if s not in held:
            skipped.add(name or t)
            continue
        if ftype == "STOCK_SPLIT":
            continue
        i = days.searchsorted(day)                    # the trade's day, or the next trading day
        if i >= len(days):
            continue
        sign = (1 if side == "BUY" else -1) if ftype == "TRADE" else (1 if q >= 0 else -1)
        n = abs(q) * sign
        for d, ratio in splits.get(s, []):
            if d > day:
                n *= ratio
        qty.iloc[i:, qty.columns.get_loc(s)] += n
        if ftype == "TRADE" and net is not None:
            flow.iloc[i] += abs(net) * sign
    if skipped:
        warnings.append("Left out (no Yahoo prices): " + ", ".join(sorted(skipped)))

    value = pd.Series(0.0, index=days)
    sym_inst = {}
    for t, inst in instruments.items():
        if symbols.get(t) in held:
            sym_inst.setdefault(symbols[t], inst)
    for s, inst in sym_inst.items():
        cur_major = portfolio._major(1, inst["currency"])[1]
        rate = fx(cur_major)
        if rate is None:
            warnings.append(f"No {acc}/{cur_major} rate: {inst['name'] or s} left out")
            continue
        scale = 0.01 if inst["currency"] in ("GBX", "GBp") else 1.0
        value += (qty[s] * close[s] * scale / rate).fillna(0)

    # what you held at the close of each snapshot day (shares and price as they were on that day, before later splits)
    holdings_on = {}
    for snap in snapshots:
        i = days.searchsorted(snap, side="right") - 1
        if i < 0:
            holdings_on[snap] = []
            continue
        d = days[i]
        rows_on = []
        for s, inst in sym_inst.items():
            q = qty[s].iloc[i]
            c = close[s].iloc[i]
            if abs(q) < 1e-9 or c != c:
                continue
            later = 1.0
            for sd, ratio in splits.get(s, []):
                if sd > d:
                    later *= ratio
            cur_major = portfolio._major(1, inst["currency"])[1]
            rate = fx(cur_major)
            scale = 0.01 if inst["currency"] in ("GBX", "GBp") else 1.0
            r = rate.iloc[i] if rate is not None else None
            rows_on.append(dict(name=inst["name"], isin=inst["isin"], symbol=s, currency=inst["currency"],
                                quantity=float(q / later), price=float(c * later), price_date=d,
                                value=float(q * c * scale / r) if r else None))
        holdings_on[snap] = sorted(rows_on, key=lambda x: -(x["value"] or 0))

    # check the final quantities against Trading 212's open positions
    pos = dict(conn.execute("SELECT t212_ticker, quantity FROM pf_positions"))
    for t, inst in instruments.items():
        s = symbols.get(t)
        if s in held:
            have, want = qty[s].iloc[-1], pos.get(t, 0) or 0
            if abs(have - want) > max(0.02 * abs(want), 1e-4):
                warnings.append(f"{inst['name'] or t}: {have:.4g} shares from the trades, Trading 212 says {want:.4g}")

    divs = pd.Series(0.0, index=days)
    for day, amount in conn.execute("SELECT substr(paid_on, 1, 10), amount FROM pf_dividends WHERE paid_on IS NOT NULL"):
        i = days.searchsorted(day)
        if i < len(days) and amount:
            divs.iloc[i] += amount

    def twr(v, f, d):
        prev = v.shift(1).fillna(0)
        base = prev + f
        r = ((v + d) / base - 1).where(base > 1e-9, 0.0)
        return (1 + r).cumprod()

    def series(rate):
        v, f, d = value * rate, flow * rate, divs * rate
        return dict(value=_r(v, 2), invested=_r(f.cumsum(), 2), dividends=_r(d.cumsum(), 2), twr=_r(twr(v, f, d), 6))

    out = dict(empty=False, acc=acc, other=other, dates=list(days), warnings=warnings + errors, series={},
               bench=[], updated=time.strftime("%Y-%m-%d %H:%M"), holdings_on=holdings_on)
    out["series"][acc] = series(pd.Series(1.0, index=days))
    x = fx(other) if other else None
    if x is not None:
        out["series"][other] = series(x)
    for key, label, sym, cur in BENCHMARKS:
        if sym not in adj.columns:
            continue
        b = dict(key=key, label=label, symbol=sym)
        r = fx(cur)
        if r is not None:
            b[acc] = _r(adj[sym] / r, 6)
            if x is not None:
                b[other] = _r(adj[sym] / r * x, 6)
            out["bench"].append(b)
    return out


def _r(s, n):
    return [None if v != v else round(float(v), n) for v in s]      # v != v: NaN
