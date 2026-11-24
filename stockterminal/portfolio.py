"""Portfolio page: your Trading 212 positions with totals (see trading212.py for the link and the sync).

All amounts come in the account currency (what Trading 212 reports, usually EUR). The screen converts
them to the other currency with `fx` (today's rate) when you switch between EUR and USD; prices per share
stay in the currency the share trades in.

live=1: positions and account summary straight from Trading 212 (and Yahoo for today's change; non-US
holdings are matched to their Yahoo symbol by resolve_symbols). live=0 or offline: the snapshot of the last sync, from the database only.
"""
import json
import time

from . import db, trading212

PAIR = {"EUR": "USD", "USD": "EUR"}      # the € / $ switch: the account currency and the other one


def app_ticker(t212_ticker, isin):
    """Ticker this app uses for a US share/ETF (AAPL_US_EQ -> AAPL, BRK_B_US_EQ -> BRK.B), else None."""
    if not t212_ticker or not t212_ticker.endswith("_US_EQ"):
        return None
    return t212_ticker[: -len("_US_EQ")].replace("_", ".").upper()


# ------------------------------------------------------------------ Yahoo symbols for non-US holdings
# Trading 212 codes end in a small letter for the exchange (ASMLa_EQ = Amsterdam, VWRLl_EQ = London). The
# letter is only a hint: a candidate is accepted when Yahoo quotes it in the same currency at a price within
# MATCH of Trading 212's own price, so a wrong guess can never be used. Found symbols are kept in pf_symbols.
SUFFIX_HINT = {"l": [".L"], "a": [".AS"], "d": [".DE", ".F"], "p": [".PA"], "e": [".MC"], "m": [".MI"],
               "b": [".BR"], "s": [".SW"], "h": [".HE"], "k": [".CO"], "o": [".OL"], "v": [".VI"], "w": [".WA"],
               "i": [".IR"], "t": [".TO"], "x": [".ST"]}
BY_CURRENCY = {"EUR": [".AS", ".DE", ".PA", ".MI", ".MC", ".BR", ".VI", ".HE", ".LS", ".IR", ".F"],
               "GBX": [".L"], "GBP": [".L"], "USD": [".L", ""], "CHF": [".SW"], "SEK": [".ST"], "DKK": [".CO"],
               "NOK": [".OL"], "PLN": [".WA"], "CAD": [".TO"], "CZK": [".PR"], "HUF": [".BD"]}
MATCH = 0.04
RETRY_DAYS = 1
_qcache = {}       # yahoo symbol -> (time, raw Yahoo quote or None)


def _major(price, cur):
    """(price, currency) in major units: pence (GBX, Yahoo's GBp) become pounds."""
    if price is None:
        return None, cur
    if cur in ("GBX", "GBp"):
        return price / 100, "GBP"
    return price, cur


def _yahoo_quotes(symbols):
    """{symbol: raw Yahoo quote or None}, symbols used exactly as given, cached 30 s. Never raises."""
    now, out, need = time.time(), {}, []
    for s in dict.fromkeys(symbols):
        hit = _qcache.get(s)
        if hit and now - hit[0] < 30:
            out[s] = hit[1]
        else:
            need.append(s)
    for i in range(0, len(need), 50):
        part = need[i:i + 50]
        try:
            from . import quote
            got = quote._fetch(part, False)
        except Exception:  # noqa: BLE001  offline: nothing for now
            got = None
        for s in part:
            q = (got or {}).get(s)
            out[s] = q
            if got is not None:
                _qcache[s] = (now, q)
    return out


def _search_isin(isin):
    """Yahoo symbols listed for an ISIN (Yahoo's search), [] when unknown or offline."""
    if not isin:
        return []
    try:
        from . import quote
        r = quote._yf().get(url="https://query2.finance.yahoo.com/v1/finance/search",
                            params={"q": isin, "quotesCount": 10, "newsCount": 0, "listsCount": 0}, timeout=10)
        return [q["symbol"] for q in r.json().get("quotes") or [] if q.get("symbol")]
    except Exception:  # noqa: BLE001
        return []


def candidates(t212_ticker, isin, currency):
    """Yahoo symbols to try for a non-US Trading 212 code, the likeliest first."""
    code = (t212_ticker or "").removesuffix("_EQ")
    base = code.rstrip("abcdefghijklmnopqrstuvwxyz")
    letter = code[len(base):]
    out = [base + suf for suf in SUFFIX_HINT.get(letter, [])]
    out += [base + suf for suf in BY_CURRENCY.get(currency, [])]
    if "." in base or "_" in base:                       # e.g. a share class: BT_A -> BT-A
        out += [base.replace("_", "-").replace(".", "-") + suf for suf in SUFFIX_HINT.get(letter, [])]
    return list(dict.fromkeys(out))


def pick(cands, quotes, price, currency, hinted=()):
    """The candidate quoted in the same currency within MATCH of Trading 212's price, else None."""
    want, want_cur = _major(price, currency)
    best = None
    for s in cands:
        q = quotes.get(s) or {}
        got, got_cur = _major(q.get("regularMarketPrice"), q.get("currency"))
        if not got or not want or got_cur != want_cur:
            continue
        diff = abs(got / want - 1)
        if diff > MATCH:
            continue
        key = (s not in hinted, diff)                     # the exchange letter's own guess wins a tie
        if best is None or key < best[0]:
            best = (key, s)
    return best[1] if best else None


def resolve_symbols(conn, positions):
    """Fill p["yahoo"] for every holding: US ones from their ticker, others from pf_symbols or by looking
    them up (only for holdings not checked in the last RETRY_DAYS)."""
    from .quote import yahoo_symbol
    known = {t: (y, c) for t, y, c in conn.execute("SELECT t212_ticker, yahoo, checked FROM pf_symbols")}
    today = time.strftime("%Y-%m-%d")
    stale = time.strftime("%Y-%m-%d", time.localtime(time.time() - RETRY_DAYS * 86400))
    todo = []
    for p in positions:
        if p["ticker"]:
            p["yahoo"] = yahoo_symbol(p["ticker"])
        elif p["t212_ticker"] in known and (known[p["t212_ticker"]][0] or known[p["t212_ticker"]][1] > stale):
            p["yahoo"] = known[p["t212_ticker"]][0]
        elif p["current_price"]:
            todo.append(p)
    if not todo:
        return
    cands = {p["t212_ticker"]: candidates(p["t212_ticker"], p["isin"], p["currency"]) for p in todo}
    quotes = _yahoo_quotes([s for c in cands.values() for s in c])
    if not any(quotes.values()):                          # Yahoo unreachable: try again next time
        return
    rows = []
    for p in todo:
        c = cands[p["t212_ticker"]]
        letter = (p["t212_ticker"] or "").removesuffix("_EQ")
        hinted = {c_ for c_ in c if any(c_.endswith(x) for x in SUFFIX_HINT.get(letter[-1:], []))}
        found = pick(c, quotes, p["current_price"], p["currency"], hinted)
        if not found:                                     # not a usual listing: ask Yahoo by ISIN
            extra = [s for s in _search_isin(p["isin"]) if s not in c]
            if extra:
                found = pick(extra, _yahoo_quotes(extra), p["current_price"], p["currency"])
        p["yahoo"] = found
        rows.append((p["t212_ticker"], found, today))
    conn.executemany("INSERT OR REPLACE INTO pf_symbols VALUES (?,?,?)", rows)
    conn.commit()


def _rate(src, dst, positions):
    """src -> dst: Yahoo's live rate, else the rate inside Trading 212's own numbers, else None."""
    if not src or not dst or src == dst:
        return 1.0, "same"
    try:
        from . import quote
        qt = quote.get_quote(f"{src}{dst}=X") or {}
        price = (qt.get("regular") or {}).get("price")
        if price:
            return price, "Yahoo"
    except Exception:  # noqa: BLE001  offline: use Trading 212's own rate below
        pass
    # a position priced in dst, valued in src: value_src = quantity * price_dst / rate
    for p in positions:
        if p["currency"] == dst and p["value"] and p["quantity"] and p["current_price"]:
            return p["quantity"] * p["current_price"] / p["value"], "Trading 212"
    return None, None


def build(live=False, show_hidden=False):
    conn = db.connect()
    conn.row_factory = None
    try:
        st = trading212.status()
        m = trading212.meta(conn)
        own_trades = m.get("source") == "own" and not st["connected"]   # made from your own trades (own.py)
        if not st["connected"] and not m.get("last_sync"):
            return dict(connected=False, source=None)
        out = dict(connected=st["connected"], source="own" if own_trades else "t212", synced_at=m.get("last_sync"),
                   live=False, errors=[])

        rows, summary = None, None
        if live and own_trades:
            from . import own
            rows = own.positions_live(conn)
            out["live"] = True
        elif live and st["connected"]:
            try:
                rows = trading212.positions_live(conn)
                out["live"] = True
            except trading212.T212Error as e:
                out["errors"].append(str(e))
            try:
                summary = trading212.summary()
            except trading212.T212Error as e:
                if str(e) not in out["errors"]:
                    out["errors"].append(str(e))
        if rows is None:
            rows = conn.execute("SELECT * FROM pf_positions").fetchall()
        summary = summary or m.get("summary") or {}
        acc = summary.get("currency") or m.get("currency") or "EUR"
        out["as_of"] = time.strftime("%H:%M") if out["live"] else m.get("last_sync")

        divs = {}
        for isin, tick, amount in conn.execute("SELECT isin, t212_ticker, SUM(amount) FROM pf_dividends GROUP BY isin, t212_ticker"):
            divs[isin or tick] = divs.get(isin or tick, 0) + (amount or 0)
        loaded = {r[0] for r in conn.execute("SELECT ticker FROM companies WHERE ticker IS NOT NULL")}
        etfs = {r[0] for r in conn.execute("SELECT ticker FROM etfs")}

        positions = []
        for r in rows:
            (t212, isin, name, cur, qty, avg, price, _wcur, value, cost, pl, fx, opened, _raw) = r
            t = app_ticker(t212, isin)
            positions.append(dict(
                t212_ticker=t212, ticker=t, isin=isin, name=name, currency=cur, quantity=qty,
                avg_price=avg, current_price=price, value=value, cost=cost, pl=pl,
                pl_pct=pl / cost if pl is not None and cost else None, fx_impact=fx,
                dividends=divs.get(isin) or divs.get(t212) or 0, opened_at=opened,
                link="etf" if t in etfs else "stock" if t in loaded else None))

        # today's change from Yahoo, for US and European holdings alike. Also when Trading 212 itself was not
        # asked (stored positions): the change of the day doesn't depend on the sync, so "Today" is never empty
        # during market hours just because the page showed the last sync.
        if st["connected"] or own_trades:
            try:
                resolve_symbols(conn, positions)
                qs = _yahoo_quotes([p["yahoo"] for p in positions if p.get("yahoo")])
                for p in positions:
                    q = qs.get(p.get("yahoo")) or {}
                    if q.get("regularMarketChangePercent") is not None:
                        p["today_pct"] = q["regularMarketChangePercent"] / 100
            except Exception:  # noqa: BLE001  the page still works without today's change
                pass

        invested = sum(p["value"] or 0 for p in positions)
        for p in positions:
            p["weight"] = (p["value"] or 0) / invested if invested else None
        positions.sort(key=lambda p: -(p["value"] or 0))

        today = [p for p in positions if p.get("today_pct") is not None and p["value"]]
        today_pl = sum(p["value"] * p["today_pct"] / (1 + p["today_pct"]) for p in today)
        cash = summary.get("cash") or {}
        inv = summary.get("investments") or {}
        cost = sum(p["cost"] or 0 for p in positions)
        upl = sum(p["pl"] or 0 for p in positions)
        flows = dict(conn.execute("SELECT type, SUM(amount) FROM pf_cash GROUP BY type").fetchall())

        out.update(
            account_currency=acc,
            other_currency=PAIR.get(acc),
            positions=positions,
            totals=dict(
                total_value=summary.get("totalValue"),
                invested=invested,
                cost=cost,
                unrealized_pl=upl,
                unrealized_pct=upl / cost if cost else None,
                fx_impact=sum(p["fx_impact"] or 0 for p in positions),
                realized_pl=inv.get("realizedProfitLoss"),
                dividends=sum(divs.values()),
                cash=cash.get("availableToTrade"),
                deposits=flows.get("DEPOSIT"),
                withdrawals=flows.get("WITHDRAW"),
                interest=(flows.get("INTEREST_ON_FREE_CASH") or 0) + (flows.get("LENDING_INTEREST") or 0) or None,
                today_pl=today_pl if today else None,
                today_pct=today_pl / (sum(p["value"] for p in today) - today_pl) if today else None,
                today_count=len(today),
            ),
        )
        other = PAIR.get(acc)
        rate, src = _rate(acc, other, positions) if other else (None, None)
        out["fx"] = dict(rate=rate, source=src, pair=f"{acc}/{other}" if other else None)
        # holdings you hid: out of the list (the totals above still count them); everything you ever held,
        # for the Settings list where you choose
        hide = hidden()
        if hide and not show_hidden:
            out["positions"] = [p for p in positions if p["t212_ticker"] not in hide]
            out["hidden_count"] = sum(1 for p in positions if p["t212_ticker"] in hide)
        names = {t: n for t, n in conn.execute(
            "SELECT t212_ticker, MAX(name) FROM pf_trades WHERE t212_ticker IS NOT NULL GROUP BY t212_ticker")}
        names.update({p["t212_ticker"]: p["name"] for p in positions})
        is_open = {p["t212_ticker"] for p in positions}
        out["instruments"] = sorted((dict(t212_ticker=t, name=n or t, open=t in is_open, hidden=t in hide)
                                     for t, n in names.items()), key=lambda r: (not r["open"], (r["name"] or "").lower()))
        return out
    finally:
        conn.close()


def hidden():
    """Holdings you chose to hide from the lists (Trading 212 tickers), from your own settings (prefs group
    "pf_hidden"): personal, never in git. Hidden only from what you look at; totals, returns, the chart and
    box 3 still count them, so the numbers keep matching Trading 212."""
    from . import settings
    try:
        return {t for t, on in ((settings.read().get("prefs") or {}).get("pf_hidden") or {}).items() if on}
    except Exception:  # noqa: BLE001
        return set()


# ------------------------------------------------------------------ one holding (its own page)
INFO_FIELDS = ("longName", "shortName", "sector", "industry", "country", "website", "fullExchangeName", "exchange",
               "currency", "marketCap", "trailingPE", "forwardPE", "dividendYield", "fiftyTwoWeekLow", "fiftyTwoWeekHigh",
               "beta", "longBusinessSummary", "quoteType", "fullTimeEmployees", "netExpenseRatio", "annualReportExpenseRatio",
               "trailingEps", "forwardEps", "currentPrice", "regularMarketPrice", "priceToSalesTrailing12Months",
               "trailingAnnualDividendYield", "yield", "revenueGrowth", "earningsGrowth", "profitMargins", "operatingMargins",
               "returnOnEquity", "financialCurrency")
INFO_DAYS = 7
INFO_VERSION = 3       # raise when INFO_FIELDS grows: older stored profiles are then fetched again


def _info(conn, symbol, refresh):
    """Yahoo profile of a symbol, kept a week in pf_info. {} when unknown or offline."""
    row = conn.execute("SELECT info, fetched FROM pf_info WHERE symbol=?", (symbol,)).fetchone()
    week_ago = time.strftime("%Y-%m-%d", time.localtime(time.time() - INFO_DAYS * 86400))
    current = row and json.loads(row[0]).get("_v") == INFO_VERSION
    if row and (not refresh or (current and row[1] > week_ago)):
        return json.loads(row[0])
    if not refresh:
        return {}
    try:
        import yfinance as yf
        raw = yf.Ticker(symbol).info or {}
        info = {k: raw.get(k) for k in INFO_FIELDS if raw.get(k) not in (None, "")}
        info["_v"] = INFO_VERSION
    except Exception:  # noqa: BLE001  offline or unknown: keep what we had
        return json.loads(row[0]) if row else {}
    conn.execute("INSERT OR REPLACE INTO pf_info VALUES (?,?,?)", (symbol, json.dumps(info), time.strftime("%Y-%m-%d")))
    conn.commit()
    return info


def holding(t212_ticker, live=False):
    """Everything the holding page shows: your position, trades, dividends, price history and Yahoo profile."""
    page = build(live=live, show_hidden=True)            # its own page still works for a hidden holding
    pos = next((p for p in page.get("positions") or [] if p["t212_ticker"] == t212_ticker), None)
    conn = db.connect()
    try:
        trades = [dict(zip(("date", "side", "type", "quantity", "price", "currency", "amount", "fees", "fx_rate",
                            "realised_pl", "name", "isin"), r)) for r in conn.execute(
            """SELECT substr(filled_at, 1, 10), side, fill_type, quantity, price, instrument_currency, net_value, fees,
                      fx_rate, realised_pl, name, isin
               FROM pf_trades WHERE t212_ticker=? AND fill_id IS NOT NULL ORDER BY filled_at DESC""", (t212_ticker,))]
        divs = [dict(zip(("date", "quantity", "amount", "per_share", "type"), r)) for r in conn.execute(
            """SELECT substr(paid_on, 1, 10), quantity, amount, gross_per_share, type FROM pf_dividends
               WHERE t212_ticker=? ORDER BY paid_on DESC""", (t212_ticker,))]
        if not pos and not trades:
            return dict(found=False)
        first = trades[-1] if trades else {}
        name = (pos or {}).get("name") or first.get("name") or t212_ticker
        currency = (pos or {}).get("currency") or first.get("currency")
        isin = (pos or {}).get("isin") or first.get("isin")
        us = app_ticker(t212_ticker, isin)
        symbol = (pos or {}).get("yahoo")
        if not symbol:
            if us:
                from .quote import yahoo_symbol
                symbol = yahoo_symbol(us)
            else:
                r = conn.execute("SELECT yahoo FROM pf_symbols WHERE t212_ticker=?", (t212_ticker,)).fetchone()
                symbol = r[0] if r else None
                if not symbol and pos and live:                  # not matched yet: try now
                    resolve_symbols(conn, [pos])
                    symbol = pos.get("yahoo")

        prices = dict(dates=[], close=[])
        if symbol:
            from . import pf_history
            start = time.strftime("%Y-%m-%d", time.localtime(time.time() - 10 * 365.25 * 86400))
            if live:
                pf_history.refresh_prices(conn, [symbol], start, [])
            rows = conn.execute("SELECT date, close, split FROM pf_prices WHERE symbol=? AND close IS NOT NULL ORDER BY date",
                                (symbol,)).fetchall()
            scale = 0.01 if currency in ("GBX", "GBp") else 1.0
            prices = dict(dates=[r[0] for r in rows], close=[round(r[1] * scale, 4) for r in rows])
            splits = [(d, sp) for d, _, sp in rows if sp]
            for t in trades:                                     # trade prices on today's share basis, for the chart
                f = 1.0
                for d, sp in splits:
                    if d > t["date"]:
                        f *= sp
                t["chart_price"] = round(t["price"] * scale / f, 4) if t["price"] and t["type"] == "TRADE" else None

        info = _info(conn, symbol, refresh=live) if symbol else {}
        loaded = None
        if us:
            loaded = "etf" if conn.execute("SELECT 1 FROM etfs WHERE ticker=?", (us,)).fetchone() else \
                "stock" if conn.execute("SELECT 1 FROM companies WHERE ticker=?", (us,)).fetchone() else "no"
        return dict(found=True, hidden=t212_ticker in hidden(), t212_ticker=t212_ticker, name=name, isin=isin, currency=currency, symbol=symbol,
                    us_ticker=us, loaded=loaded, position=pos, trades=trades, dividends=divs, prices=prices, info=info,
                    account_currency=page.get("account_currency"), other_currency=page.get("other_currency"),
                    fx=page.get("fx"), live=page.get("live"), as_of=page.get("as_of"), errors=page.get("errors") or [],
                    first_bought=first.get("date"))
    finally:
        conn.close()
