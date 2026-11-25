"""Your own trades: a portfolio without Trading 212.

You type in your buys and sells (or import them from your broker's CSV export) on the Portfolio page. They
are kept in pf_own_trades, on this PC only. rebuild() turns them into the same rows a Trading 212 sync
makes (pf_trades, pf_positions, pf_dividends, pf_meta), so the whole Portfolio page works unchanged: the
holdings, the Performance chart against the S&P 500, the tabs, alerts and the morning summary.

- Prices, splits and exchange rates: Yahoo's daily closes (pf_history.refresh_prices, kept in pf_prices).
- Dividends: worked out from Yahoo's dividend history and the shares you held the day before each
  ex-dividend date (before tax, booked on the ex-date: Yahoo has no payment dates for past dividends).
- Amounts in your own currency (USD or EUR, your choice), at each day's exchange rate. Cost is the average
  cost: a sale takes its share of what you paid, the rest is the realised result.
- Cash is not tracked: there is no account behind the trades.

One source at a time: while Trading 212 is connected, its sync owns the portfolio tables and your own
trades wait (they are kept). A Trading 212 sync replaces what rebuild() made.

    python -m stockterminal.own          # rebuild from your own trades now (fetches Yahoo history)
"""
import bisect
import csv
import datetime as dt
import io
import json
import re
import sys
import threading
import time

from . import db, pf_history, portfolio, trading212

CURRENCIES = ("USD", "EUR")          # your own currency; the € / $ switch shows the other one
DIV_REFRESH_HOURS = 12
MAX_ROWS = 5000                      # per CSV import
_lock = threading.RLock()


class OwnError(Exception):
    """A message that can be shown to the user as is."""


# ------------------------------------------------------------------------------------------ symbols
def key_for(symbol, currency):
    """The code a holding gets in the portfolio tables. US shares look like Trading 212's (AAPL_US_EQ,
    BRK_B_US_EQ), so links to the stock page and logos work as they do for a Trading 212 portfolio; any
    other listing keeps its Yahoo symbol (ASML.AS)."""
    if currency == "USD" and re.fullmatch(r"[A-Z][A-Z0-9]*(-[A-Z])?", symbol or ""):
        return symbol.replace("-", "_") + "_US_EQ"
    return symbol


def _clean_symbol(text):
    s = (text or "").strip().upper().replace(" ", "")
    return s.removeprefix("$")


def _is_isin(s):
    return bool(re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", s or ""))


def _quote_info(symbol, q):
    price, _ = portfolio._major(q.get("regularMarketPrice"), q.get("currency"))
    return dict(symbol=symbol, name=q.get("longName") or q.get("shortName") or symbol, currency=q.get("currency"),
                price=q.get("regularMarketPrice"), type=q.get("quoteType"), exchange=q.get("fullExchangeName"))


def _stored_info(tries):
    """{symbol, name, currency, price} of a US share whose prices are in this app (no Yahoo needed), or None."""
    from . import db
    conn = db.connect()
    try:
        for t in tries:
            row = conn.execute("""SELECT c.ticker, COALESCE(c.display_name, c.name), c.exchange,
                                         (SELECT close FROM prices p WHERE p.ticker=c.ticker ORDER BY date DESC LIMIT 1)
                                  FROM companies c WHERE c.ticker=?""", (t,)).fetchone()
            if row and row[3]:
                return dict(symbol=row[0], name=db.readable_name(row[1]) or row[0], currency="USD", price=row[3],
                            type="EQUITY", exchange=row[2], stored=True)
    finally:
        conn.close()
    return None


def lookup(text, currency=None, quotes=None):
    """{symbol, name, currency, price} for what you typed: a Yahoo symbol (AAPL, ASML.AS), the app's way
    (BRK.B) or an ISIN. With `currency` (from a CSV) a listing in that currency wins. Raises OwnError."""
    s = _clean_symbol(text)
    if not s:
        raise OwnError("Fill in a ticker, for example AAPL or ASML.AS.")
    quotes_of = quotes or portfolio._yahoo_quotes
    want = portfolio._major(1, currency)[1] if currency else None
    tries = [s]
    if re.fullmatch(r"[A-Z0-9]+\.[A-Z]", s):                 # BRK.B -> BRK-B (a share class, not an exchange)
        tries.insert(0, s.replace(".", "-"))
    if _is_isin(s):
        tries = portfolio._search_isin(s) or tries
    got = quotes_of(tries)
    if not any(got.values()) and not quotes_of(["SPY"]).get("SPY"):
        local = _stored_info(tries)                           # offline: a US share this app already has works anyway
        if local and (not want or want == "USD"):
            return local
        raise OwnError("Could not reach Yahoo to check the ticker. Check your internet connection and try again. "
                       "Offline, only US shares that are already in the app can be added.")
    found = [_quote_info(t, got[t]) for t in tries if got.get(t)]
    if want:
        same = [f for f in found if portfolio._major(1, f["currency"])[1] == want]
        if not same:                                          # e.g. ASML in EUR: try the European listings
            base = s.split(".")[0]
            more = [base + suf for suf in portfolio.BY_CURRENCY.get(currency, portfolio.BY_CURRENCY.get(want, []))]
            more = [m for m in dict.fromkeys(more) if m not in tries]
            extra = quotes_of(more) if more else {}
            same = [_quote_info(t, extra[t]) for t in more if extra.get(t)
                    and portfolio._major(1, extra[t].get("currency"))[1] == want]
        found = same or found
    if not found:
        raise OwnError(f"Yahoo doesn't know \"{text.strip()}\". Use Yahoo's symbol: AAPL for a US share, "
                       "ASML.AS for Amsterdam, VWRL.L for London, SAP.DE for Xetra.")
    return found[0]


# ------------------------------------------------------------------------------------------ the trades
def _meta(conn):
    return trading212.meta(conn)


def currency(conn):
    c = _meta(conn).get("own_currency")
    return c if c in CURRENCIES else "USD"


def trades(conn):
    cols = ("id", "date", "symbol", "name", "currency", "side", "quantity", "price", "fees", "note")
    return [dict(zip(cols, r)) for r in conn.execute(
        f"SELECT {', '.join(cols)} FROM pf_own_trades ORDER BY date DESC, id DESC")]


def state(conn):
    """For the screen: your own trades and whether they are what the Portfolio page shows."""
    m = _meta(conn)
    t = trades(conn)
    return dict(trades=t, currency=currency(conn), currencies=list(CURRENCIES),
                active=m.get("source") == "own", t212=bool(trading212.status().get("connected")),
                t212_data=bool(m.get("last_sync")) and m.get("source") != "own",
                warnings=m.get("own_warnings") or [], updated=m.get("last_sync") if m.get("source") == "own" else None,
                example_csv=EXAMPLE_CSV)


def _day(text):
    try:
        d = dt.date.fromisoformat(str(text).strip()[:10])
    except ValueError:
        raise OwnError("Use a date like 2024-03-15.")
    if d > dt.date.today():
        raise OwnError("The date is in the future.")
    if d.year < 1970:
        raise OwnError("The date is too long ago.")
    return d.isoformat()


def _num(v, what, positive=True, allow_none=False):
    if v in (None, ""):
        if allow_none:
            return None
        raise OwnError(f"Fill in the {what}.")
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise OwnError(f"The {what} is not a number.")
    if x != x or (positive and x <= 0) or x < 0:
        raise OwnError(f"The {what} must be more than 0." if positive else f"The {what} can't be negative.")
    return x


def _close_on(conn, symbol, day):
    r = conn.execute("SELECT close FROM pf_prices WHERE symbol=? AND date<=? AND close IS NOT NULL "
                     "ORDER BY date DESC LIMIT 1", (symbol, day)).fetchone()
    return r[0] if r else None


def add(conn, date, symbol, side, quantity, price=None, fees=None, note=None, download=None, info=None):
    """Add one trade. Without a price, that day's close from Yahoo is used. Returns the new trade's id."""
    side = (side or "").upper()
    if side not in ("BUY", "SELL"):
        raise OwnError("Choose buy or sell.")
    day = _day(date)
    qty = _num(quantity, "number of shares")
    fee = _num(fees, "fees", positive=False, allow_none=True)
    info = info or lookup(symbol)
    sym, cur = info["symbol"], info["currency"]
    p = _num(price, "price", allow_none=True)
    if p is None or cur in ("GBp", "GBX"):
        pf_history.refresh_prices(conn, [sym], pf_history._days_before(day, 10), [],
                                  download or pf_history._download)
        close = _close_on(conn, sym, day)
        if close is None and cur == "USD":                    # offline: the app's own daily prices of that share
            r = conn.execute("SELECT date, close FROM prices WHERE ticker=? AND date<=? AND close IS NOT NULL "
                             "ORDER BY date DESC LIMIT 1", (sym, day)).fetchone()
            close = r[1] * _split_factor(_splits(conn, sym), r[0]) if r else None
        if p is None:
            if not close:
                raise OwnError(f"No price from Yahoo for {sym} on {day}. Fill in the price you paid.")
            p = round(close, 4)
        elif close and p < close / 30:                         # London in pounds while Yahoo quotes pence
            p, fee = p * 100, (fee * 100 if fee else fee)
    with _lock:
        cur_ = conn.execute("""INSERT INTO pf_own_trades (date, symbol, name, currency, side, quantity, price, fees, note, added)
                               VALUES (?,?,?,?,?,?,?,?,?,?)""",
                            (day, sym, info.get("name"), cur, side, qty, p, fee, (note or "").strip()[:200] or None,
                             time.strftime("%Y-%m-%d %H:%M")))
        conn.commit()
    return cur_.lastrowid


def delete(conn, trade_id):
    with _lock:
        conn.execute("DELETE FROM pf_own_trades WHERE id=?", (int(trade_id),))
        conn.commit()


def set_currency(conn, cur):
    if cur not in CURRENCIES:
        raise OwnError("Choose USD or EUR.")
    trading212._set_meta(conn, own_currency=cur)


# ------------------------------------------------------------------------------------------ CSV import
FIELDS = {
    "date": ("date", "trade date", "time", "datetime", "date/time", "transaction date", "run date", "execution date",
             "booking date", "datum"),
    "symbol": ("symbol", "ticker", "ticker symbol", "instrument", "security", "code", "product", "stock", "isin"),
    "isin": ("isin", "isin code"),
    "side": ("side", "action", "type", "transaction type", "buy/sell", "activity", "transaction", "direction",
             "actie", "transactie", "soort"),
    "quantity": ("quantity", "shares", "qty", "units", "no. of shares", "number of shares", "amount of shares",
                 "aantal", "quantity traded"),
    "price": ("price", "price per share", "share price", "trade price", "price / share", "unit price", "koers",
              "execution price", "fill price"),
    "currency": ("currency", "currency (price / share)", "price currency", "trade currency", "valuta"),
    "fees": ("fees", "fee", "commission", "commissions", "costs", "transaction costs", "fees & comm",
             "transaction fee", "brokerage", "kosten"),
}


def _norm(h):
    return re.sub(r"\s+", " ", (h or "").strip().strip("﻿").lower())


def _columns(header):
    """{field: column index} from a header row; an exact name wins over a name that starts with it."""
    names = [_norm(h) for h in header]
    out = {}
    for field, aliases in FIELDS.items():
        for exact in (True, False):
            for a in aliases:
                for i, n in enumerate(names):
                    if i in out.values() and field != "isin":
                        continue
                    if n == a or (not exact and (n.startswith(a + " ") or n.startswith(a + " ("))):
                        out.setdefault(field, i)
            if field in out:
                break
    if out.get("isin") == out.get("symbol"):                # one ISIN column: the symbol comes from it
        out.pop("isin", None)
    return out


def _number(text):
    """1,234.56 / 1.234,56 / $1,234 / (12.50) / -3 -> float, or None."""
    s = (text or "").strip()
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[^\d,.\-]", "", s)
    if not s or s in "-.,":
        return None
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif "," in s:
        parts = s.split(",")
        s = s.replace(",", "") if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3 and parts[0] not in ("", "0", "-0")) \
            else s.replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        return None
    return -abs(v) if neg else v


DATE_RE = re.compile(r"(\d{1,4})[-/.](\d{1,2})[-/.](\d{1,4})")


def _date_order(values):
    """'ymd', 'dmy' or 'mdy' for the dates in one file (decided on the whole column, not per row)."""
    a = b = other = False
    for v in values:
        m = DATE_RE.search(v or "")
        if not m or len(m.group(1)) == 4:
            continue
        other = True
        x, y = int(m.group(1)), int(m.group(2))
        a |= x > 12
        b |= y > 12
    if not other:
        return "ymd"                                          # only 2024-01-05 style dates (or none)
    if b and not a:
        return "mdy"
    return "dmy"


def _parse_date(text, order):
    m = DATE_RE.search(text or "")
    if not m:
        return None
    x, y, z = m.groups()
    if len(x) == 4:
        yy, mm, dd = int(x), int(y), int(z)
    else:
        yy = int(z) + (2000 if len(z) == 2 else 0)
        dd, mm = (int(x), int(y)) if order == "dmy" else (int(y), int(x))
    try:
        return dt.date(yy, mm, dd).isoformat()
    except ValueError:
        return None


def _side(text, qty):
    t = (text or "").lower()
    if re.search(r"\b(buy|bought|purchase|koop|aankoop|reinvest)", t):
        return "BUY"
    if re.search(r"\b(sell|sold|sale|verkoop)", t):
        return "SELL"
    if not t.strip() and qty:
        return "BUY" if qty > 0 else "SELL"
    return None


def parse_csv(text):
    """(rows, skipped, order): rows = [{line, date, symbol, isin, side, quantity, price, currency, fees}]."""
    text = (text or "").lstrip("﻿")
    if not text.strip():
        raise OwnError("The file is empty.")
    sample = text[:5000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    lines = list(csv.reader(io.StringIO(text), dialect))
    head = next((i for i, r in enumerate(lines[:20]) if {"date", "symbol", "quantity"} <= set(_columns(r))), None)
    if head is None:
        raise OwnError("Couldn't find the columns. The file needs at least a date, a ticker (or ISIN), "
                       "the number of shares and buy/sell; a price is used when there is one. Download the "
                       "example file to see the layout.")
    col = _columns(lines[head])
    body = [(head + 2 + i, r) for i, r in enumerate(lines[head + 1:]) if any((c or "").strip() for c in r)]
    if len(body) > MAX_ROWS:
        raise OwnError(f"That's more than {MAX_ROWS} rows. Split the file and import the parts one by one.")
    get = lambda r, f: (r[col[f]] if f in col and col[f] < len(r) else "").strip()  # noqa: E731
    order = _date_order([get(r, "date") for _, r in body])
    rows, skipped = [], []
    for line, r in body:
        qty = _number(get(r, "quantity"))
        side = _side(get(r, "side"), qty) if "side" in col else ("BUY" if (qty or 0) > 0 else "SELL")
        if side is None:
            skipped.append(dict(line=line, reason=f"not a buy or sell ({get(r, 'side') or 'empty'})"))
            continue
        day = _parse_date(get(r, "date"), order)
        sym = get(r, "symbol")
        isin = get(r, "isin")
        price = _number(get(r, "price"))
        fees = _number(get(r, "fees"))
        if not day:
            skipped.append(dict(line=line, reason=f"no date ({get(r, 'date') or 'empty'})"))
        elif not (sym or isin):
            skipped.append(dict(line=line, reason="no ticker"))
        elif not qty:
            skipped.append(dict(line=line, reason="no number of shares"))
        else:
            rows.append(dict(line=line, date=day, symbol=sym, isin=isin, side=side, quantity=abs(qty),
                             price=abs(price) if price else None, currency=get(r, "currency").upper() or None,
                             fees=abs(fees) if fees else None))
    return rows, skipped, order


def _looks_like_name(text):
    """A company name in the ticker column ("APPLE INC", "Vanguard FTSE All-World") rather than a ticker."""
    t = (text or "").strip()
    return " " in t or len(t) > 12


def import_csv(conn, text, download=None, quotes=None):
    """Add the trades in a broker's CSV. Trades already there (same day, ticker, side, shares and price)
    are skipped, so importing the same file twice adds nothing."""
    rows, skipped, order = parse_csv(text)
    found, failed = {}, {}
    for r in rows:
        k = (r["symbol"] or r["isin"], r["currency"])
        if k in found or k in failed:
            continue
        err = None
        tries = [x for x in (r["symbol"], r["isin"]) if x]
        if r["isin"] and r["symbol"] and _looks_like_name(r["symbol"]):
            tries.reverse()                                   # DEGIRO: "Product" = APPLE INC, the ISIN says which share
        for attempt in tries:
            try:
                found[k] = lookup(attempt, r["currency"], quotes)
                break
            except OwnError as e:
                err = str(e)
                if "Could not reach Yahoo" in err:
                    raise
        else:
            failed[k] = err
    have = {(d, s, sd, round(q, 6), round(p, 4)) for d, s, sd, q, p in
            conn.execute("SELECT date, symbol, side, quantity, price FROM pf_own_trades")}
    added = 0
    for r in rows:
        k = (r["symbol"] or r["isin"], r["currency"])
        if k in failed:
            skipped.append(dict(line=r["line"], reason=f"unknown ticker {k[0]}"))
            continue
        try:
            if r["date"] > dt.date.today().isoformat():
                raise OwnError("the date is in the future")
            info = found[k]
            if r["price"] is not None and r["currency"] and portfolio._major(1, r["currency"])[1] == \
                    portfolio._major(1, info["currency"])[1] and r["currency"] != info["currency"]:
                # the file in pounds, Yahoo in pence (or the other way round)
                f = 100 if info["currency"] in ("GBp", "GBX") else 0.01
                r["price"] *= f
                r["fees"] = r["fees"] * f if r["fees"] else r["fees"]
            elif r["fees"] and r["currency"] and portfolio._major(1, r["currency"])[1] != portfolio._major(1, info["currency"])[1]:
                r["fees"] = None                               # fees in another currency than the share: left out
            p = r["price"]
            if p is not None and (r["date"], info["symbol"], r["side"], round(r["quantity"], 6), round(p, 4)) in have:
                skipped.append(dict(line=r["line"], reason="already there"))
                continue
            add(conn, r["date"], None, r["side"], r["quantity"], p, r["fees"], download=download, info=info)
            added += 1
        except OwnError as e:
            skipped.append(dict(line=r["line"], reason=str(e).rstrip(".")))
    skipped.sort(key=lambda s: s["line"])
    return dict(added=added, skipped=skipped,
                dates={"dmy": "day/month", "mdy": "month/day"}.get(order, "year-month-day"))


EXAMPLE_CSV = """date,ticker,side,shares,price,currency,fees
2023-01-17,AAPL,buy,10,135.94,USD,1
2023-06-01,ASML.AS,buy,2,600.50,EUR,2
2024-02-12,AAPL,sell,4,187.15,USD,1
"""


# ------------------------------------------------------------------------------------------ dividends
def _download_divs(symbol):
    """[(ex-date, amount per share)] from Yahoo (split-adjusted), or raises."""
    import yfinance as yf
    d = yf.Ticker(symbol).dividends
    return [(i.strftime("%Y-%m-%d"), float(v)) for i, v in d.items() if v == v and v]


def refresh_divs(conn, symbols, errors, download=_download_divs):
    fetched = _meta(conn).get("own_divs_fetched") or {}
    now = time.time()
    for s in dict.fromkeys(symbols):
        if fetched.get(s) and now - fetched[s] < DIV_REFRESH_HOURS * 3600:
            continue
        try:
            rows = download(s)
        except Exception as e:  # noqa: BLE001  shown as a warning; what was stored is used
            errors.append(f"dividends {s}: {e}")
            continue
        conn.execute("DELETE FROM pf_own_divs WHERE symbol=?", (s,))
        conn.executemany("INSERT OR REPLACE INTO pf_own_divs VALUES (?,?,?)", [(s, d, a) for d, a in rows])
        conn.commit()
        fetched[s] = now
    trading212._set_meta(conn, own_divs_fetched=fetched)


# ------------------------------------------------------------------------------------------ rebuild
DERIVED = ("pf_trades", "pf_dividends", "pf_cash", "pf_positions")


class _Prices:
    """Closes and splits per symbol from pf_prices, looked up by day."""

    def __init__(self, conn, symbols):
        self.d = {}
        for s in symbols:
            rows = conn.execute("SELECT date, close, split FROM pf_prices WHERE symbol=? ORDER BY date", (s,)).fetchall()
            self.d[s] = ([r[0] for r in rows if r[1]], [r[1] for r in rows if r[1]], [(r[0], r[2]) for r in rows if r[2]])

    def close(self, s, day=None):
        dates, closes, _ = self.d.get(s) or ([], [], [])
        if not dates:
            return None
        i = len(dates) if day is None else bisect.bisect_right(dates, day)
        return closes[i - 1] if i else None

    def last_date(self, s):
        dates = (self.d.get(s) or ([],))[0]
        return dates[-1] if dates else None

    def later_splits(self, s, day):
        f = 1.0
        for d, r in (self.d.get(s) or ([], [], []))[2]:
            if d > day:
                f *= r
        return f


def active(conn):
    return _meta(conn).get("source") == "own"


def rebuild(conn=None, refresh=True, say=lambda *_: None, download=None, div_download=None):
    """Make the portfolio tables from your own trades. Does nothing while Trading 212 is connected."""
    own = conn is None
    conn = conn or db.connect()
    try:
        with _lock:
            return _rebuild(conn, refresh, say, download or pf_history._download, div_download or _download_divs)
    finally:
        if own:
            conn.close()


def _splits(conn, sym):
    return conn.execute("SELECT date, ratio FROM splits WHERE ticker=? AND ratio > 0", (sym,)).fetchall()


def _split_factor(splits, day):
    """The app's own closes are split-adjusted; times this factor gives the price as it was traded that day."""
    f = 1.0
    for d, r in splits:
        if d > day:
            f *= r
    return f


def _stored_prices(conn, symbols, start):
    """Offline (or Yahoo had nothing yet): a US share without portfolio prices gets the app's own daily closes,
    so its value and return still show. Yahoo's history replaces them at the next refresh that works."""
    for s in symbols:
        if conn.execute("SELECT 1 FROM pf_prices WHERE symbol=? LIMIT 1", (s,)).fetchone():
            continue
        rows = conn.execute("SELECT date, close, adj_close FROM prices WHERE ticker=? AND date>=? AND close IS NOT NULL",
                            (s, pf_history._days_before(start, 7))).fetchall()
        if rows:
            sp = _splits(conn, s)
            dates = sorted(d for d, _, _ in rows)
            on = {}                                            # each split on the first stored day from its date
            for d, r in sp:
                first = next((x for x in dates if x >= d), None)
                if first and d >= dates[0]:
                    on[first] = on.get(first, 1.0) * r
            conn.executemany("INSERT OR REPLACE INTO pf_prices VALUES (?,?,?,?,?)",
                             [(s, d, c * _split_factor(sp, d), a, on.get(d)) for d, c, a in rows])
            conn.commit()


def _rebuild(conn, refresh, say, download, div_download):
    if trading212.status().get("connected"):
        return dict(skipped="Trading 212 is connected")
    rows = conn.execute("""SELECT id, date, symbol, name, currency, side, quantity, price, fees, added
                           FROM pf_own_trades ORDER BY date, id""").fetchall()
    m = _meta(conn)
    if not rows:
        if m.get("source") == "own":                           # the last trade was deleted: an empty portfolio
            for t in DERIVED:
                conn.execute(f"DELETE FROM {t}")
            conn.execute("DELETE FROM pf_meta WHERE key IN ('source','account_id','currency','summary','last_sync',"
                         "'totals','last_error','own_warnings')")
            conn.commit()
        return dict(trades=0)
    acc = currency(conn)
    errors, warnings = [], []
    start = rows[0][1]
    symbols = list(dict.fromkeys(r[2] for r in rows))
    curs = {s: c for _, _, s, _, c, *_ in rows}
    fx = {c: f"{acc}{c}=X" for c in {portfolio._major(1, c)[1] for c in curs.values()} if c and c != acc}
    if refresh:
        pf_history.refresh_prices(conn, symbols + list(fx.values()), pf_history._days_before(start, 7), errors, download)
        refresh_divs(conn, symbols, errors, div_download)
    _stored_prices(conn, [s for s in symbols if curs.get(s) == "USD"], start)
    P = _Prices(conn, symbols + list(fx.values()))

    def rate(cur, day=None):                                   # units of `cur` per 1 `acc`
        c = portfolio._major(1, cur)[1]
        if not c or c == acc:
            return 1.0
        return P.close(fx[c], day) or P.close(fx[c])           # before the first rate: the earliest one known

    pos = {}                     # key -> running state
    trade_rows = []
    for tid, day, sym, name, cur, side, q, price, fees, added in rows:
        key = key_for(sym, cur)
        st = pos.setdefault(key, dict(symbol=sym, name=name or sym, currency=cur, shares=0.0, cost=0.0, cost_inst=0.0,
                                      opened=day, realised=0.0))
        st["name"] = name or st["name"]
        r = rate(cur, day)
        if not r:
            warnings.append(f"No {acc}/{portfolio._major(1, cur)[1]} rate from Yahoo yet: {st['name']} left out")
            continue
        gross, _ = portfolio._major(q * price, cur)
        fee, _ = portfolio._major(fees or 0, cur)
        n = q * P.later_splits(sym, day)                       # in today's shares
        realised = None
        if side == "BUY":
            net = (gross + fee) / r
            if st["shares"] <= 1e-9:
                st["opened"] = day
            st["shares"] += n
            st["cost"] += net
            st["cost_inst"] += gross + fee
        else:
            net = (gross - fee) / r
            if st["shares"] <= 1e-9:
                warnings.append(f"{st['name']}: sold on {day} without shares bought before it")
                part = 0.0
            else:
                part = min(1.0, n / st["shares"])
                if n > st["shares"] * 1.0001:
                    warnings.append(f"{st['name']}: sold more shares on {day} than you had")
            out = st["cost"] * part
            realised = net - out
            st["realised"] += realised
            st["cost"] -= out
            st["cost_inst"] -= st["cost_inst"] * part
            st["shares"] = max(0.0, st["shares"] - n)
            if st["shares"] <= 1e-9:
                st["shares"] = st["cost"] = st["cost_inst"] = 0.0
        fee_acc = fee / r if fee else None
        raw = dict(source="own", id=tid, symbol=sym,
                   fill=dict(walletImpact=dict(taxes=[dict(name="TRANSACTION_FEE", quantity=round(fee_acc, 4), currency=acc)]
                                               if fee_acc else [])))
        trade_rows.append((f"own:{tid}", tid, tid, key, None, st["name"], cur, side, "MARKET", "FILLED", "TRADE", q, price,
                           f"{day}T12:00:00Z", added, acc, round(net, 2), r, round(realised, 2) if realised is not None else None,
                           round(fee_acc, 2) if fee_acc else None, "OWN", json.dumps(raw)))

    # dividends: shares held at the close before each ex-date (today's share basis) x Yahoo's amount
    div_rows = []
    held_on = {}
    for tid, day, sym, name, cur, side, q, price, fees, added in rows:
        held_on.setdefault(sym, []).append((day, (1 if side == "BUY" else -1) * q * P.later_splits(sym, day)))
    for sym in symbols:
        steps = held_on.get(sym) or []
        cur = curs[sym]
        key = key_for(sym, cur)
        for d, per_share in conn.execute("SELECT date, amount FROM pf_own_divs WHERE symbol=? AND date>? ORDER BY date",
                                         (sym, start)):
            shares = sum(n for day, n in steps if day < d)
            if shares <= 1e-9 or not per_share:
                continue
            r = rate(cur, d)
            if not r:
                continue
            amount = portfolio._major(shares * per_share, cur)[0] / r
            div_rows.append((f"own:{key}:{d}", key, None, pos[key]["name"], cur, round(shares, 6), round(amount, 2), acc,
                             None, per_share, f"{d}T00:00:00Z", "ORDINARY", json.dumps(dict(source="own", symbol=sym))))
            pos[key]["divs"] = pos[key].get("divs", 0) + amount

    # open positions, valued at the last close
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    pos_rows = []
    for key, st in pos.items():
        if st["shares"] <= 1e-9:
            continue
        sym, cur = st["symbol"], st["currency"]
        last = P.close(sym)
        if last is None:                                       # no Yahoo history yet: the last trade's price
            last = next((r[7] for r in reversed(rows) if r[2] == sym), None)
            warnings.append(f"{st['name']}: no price from Yahoo yet, valued at your last trade's price")
        pos_rows.append(_position(key, st, last, rate(cur), acc, now))
    if not trade_rows:
        warnings.append("No trade could be used yet (Yahoo prices or exchange rates missing).")

    for t in DERIVED:
        conn.execute(f"DELETE FROM {t}")
    conn.executemany(f"INSERT OR REPLACE INTO pf_trades VALUES ({','.join('?' * 22)})", trade_rows)
    conn.executemany(f"INSERT OR REPLACE INTO pf_dividends VALUES ({','.join('?' * 13)})", div_rows)
    conn.executemany(f"INSERT OR REPLACE INTO pf_positions VALUES ({','.join('?' * 14)})", pos_rows)
    today = time.strftime("%Y-%m-%d")
    conn.executemany("INSERT OR REPLACE INTO pf_symbols VALUES (?,?,?)",
                     [(k, st["symbol"], today) for k, st in pos.items() if not k.endswith("_US_EQ")])
    value = sum(r[8] or 0 for r in pos_rows)
    realised = sum(st["realised"] for st in pos.values())
    totals = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in DERIVED}
    trading212._set_meta(conn, source="own", account_id=None, currency=acc, last_sync=time.strftime("%Y-%m-%d %H:%M"),
                         last_error=None, totals=totals, own_warnings=list(dict.fromkeys(warnings + errors))[:20],
                         summary=dict(currency=acc, totalValue=round(value, 2), cash={},
                                      investments=dict(realizedProfitLoss=round(realised, 2))))
    say(f"own trades: {len(trade_rows)} trades, {len(pos_rows)} open positions, {len(div_rows)} dividends")
    return dict(trades=len(trade_rows), positions=len(pos_rows), dividends=len(div_rows), warnings=warnings + errors)


def _position(key, st, price, r, acc, now):
    """A pf_positions row. price in the share's own units (pence for GBp); r = units of its currency per 1 acc."""
    shares, cur = st["shares"], st["currency"]
    major, _ = portfolio._major(price, cur)
    value = shares * major / r if major is not None and r else None
    pl = value - st["cost"] if value is not None else None
    # currency effect: the value now minus the same value at the rates you bought at
    at_cost_rates = shares * major * st["cost"] / st["cost_inst"] if st["cost_inst"] and major is not None else None
    fx_impact = value - at_cost_rates if value is not None and at_cost_rates is not None else None
    avg = st["cost_inst"] / shares * (100 if cur in ("GBp", "GBX") else 1)
    raw = dict(source="own", symbol=st["symbol"], cost=st["cost"], cost_inst=st["cost_inst"])
    return (key, None, st["name"], cur, shares, avg, price, acc, value, st["cost"], pl, fx_impact, st["opened"], json.dumps(raw))


def positions_live(conn):
    """The stored positions with Yahoo's latest prices and exchange rates (cached 30 s by _yahoo_quotes),
    saved back. Never raises: without Yahoo the stored values stay."""
    rows = conn.execute("SELECT * FROM pf_positions").fetchall()
    acc = currency(conn)
    raws = [json.loads(r[13] or "{}") for r in rows]
    syms = [x.get("symbol") for x in raws if x.get("symbol")]
    fx = {c: f"{acc}{c}=X" for c in {portfolio._major(1, r[3])[1] for r in rows} if c and c != acc}
    q = portfolio._yahoo_quotes(syms + list(fx.values()))
    if not any(q.values()):
        return rows
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    out = []
    for r, raw in zip(rows, raws):
        quote = q.get(raw.get("symbol")) or {}
        price = quote.get("regularMarketPrice") or r[6]
        c = portfolio._major(1, r[3])[1]
        rate_ = 1.0 if c == acc else ((q.get(fx.get(c)) or {}).get("regularMarketPrice"))
        if not rate_:
            out.append(r)
            continue
        st = dict(shares=r[4], currency=r[3], cost=raw.get("cost", r[9] or 0), cost_inst=raw.get("cost_inst", 0),
                  symbol=raw.get("symbol"), name=r[2], opened=r[12])
        out.append(_position(r[0], st, price, rate_, acc, now))
    conn.executemany(f"INSERT OR REPLACE INTO pf_positions VALUES ({','.join('?' * 14)})", out)
    conn.commit()
    return out


if __name__ == "__main__":
    try:
        print(rebuild(say=print))
    except OwnError as e:
        print(e)
        sys.exit(1)
