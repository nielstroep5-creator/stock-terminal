"""Keep one ticker (or all) up to date: prices + daily valuation, analyst snapshot, and new SEC filings.

Used by the app (refresh button / opening a stock page) and by the daily task (stockterminal.daily).
Every step fails soft: an error is logged in fetch_log and the other steps still run.
"""
import datetime as dt
import sqlite3
import time
from zoneinfo import ZoneInfo

import pandas as pd

from . import db
from .config import DB_PATH

NY = ZoneInfo("America/New_York")
ANALYST_MAX_AGE_H = 6        # opening a stock page refreshes analyst data older than this
SEC_RECHECK_H = 12           # do not ask the SEC again for the same company within this time
SEC_STALE_DAYS = 100         # fallback: latest reported period older than this -> look for a new filing
SEC_WINDOW_DAYS = 60         # stop looking this long after an earnings date (avoid endless checks)


def _q(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()


def _cik(conn, t):
    r = _q(conn, "SELECT cik FROM companies WHERE ticker=?", (t,))
    return r[0][0] if r else None


def _last_log(conn, t, source):
    r = _q(conn, "SELECT MAX(fetched_at) FROM fetch_log WHERE ticker=? AND source=? AND status IN ('ok','empty')",
           (t, source))
    if not r or not r[0][0]:
        return None
    return dt.datetime.strptime(r[0][0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def _hours_since(ts):
    return 1e9 if ts is None else (dt.datetime.now(dt.timezone.utc) - ts).total_seconds() / 3600


def last_trading_day(quote):
    """New York date of the last regular-session price in a live quote (None without a quote)."""
    if not quote or not quote.get("regular") or not quote["regular"].get("time"):
        return None
    return dt.datetime.fromtimestamp(quote["regular"]["time"], NY).date().isoformat()


def prices_due(conn, t, quote):
    last = _q(conn, "SELECT MAX(date) FROM prices WHERE ticker=?", (t,))[0][0]
    day = last_trading_day(quote)
    return last is None or (day is not None and last < day)


def analyst_due(conn, t):
    return _hours_since(_last_log(conn, t, "yahoo_analyst")) > ANALYST_MAX_AGE_H


def sec_due(conn, t):
    """(due, reason): is there a new 10-K / 10-Q / 20-F / 40-F that is not in the database yet?
    Asks the SEC's recent-filings list for the company (one small request, no companyfacts download),
    at most once per SEC_RECHECK_H. If the SEC cannot be reached, falls back to guessing from the
    earnings calendar (see _sec_due_guess)."""
    from . import sec
    cik = _cik(conn, t)
    if cik is None:
        return False, "not an SEC filer"
    last = max((x for x in (_last_log(conn, t, "sec_check"), _last_log(conn, t, "sec_facts")) if x), default=None)
    if _hours_since(last) < SEC_RECHECK_H:
        return False, "checked recently"
    try:
        new = sec.new_periodic_filings(conn, cik)
    except Exception as e:  # noqa: BLE001
        db.log(conn, t, "sec_check", "error", None, str(e)[:300])
        return _sec_due_guess(conn, t, cik)
    db.log(conn, t, "sec_check", "ok", len(new), "; ".join(f"{f} {d}" for _, f, d in new)[:300] or "no new filing")
    if new:
        return True, "new " + ", ".join(f"{f} filed {d}" for _, f, d in new[:3])
    return False, "no new filing"


def _sec_due_guess(conn, t, cik):
    """Fallback without the SEC: an earnings date has passed with no filing since, results were reported
    for a later quarter, or the latest period is more than SEC_STALE_DAYS old."""
    end, filed = _q(conn, "SELECT MAX(end_date), MAX(filed) FROM financials WHERE cik=?", (cik,))[0]
    if not end:
        return False, "no financials"
    today = dt.date.today().isoformat()
    dates = set()
    for (txt,) in _q(conn, "SELECT text FROM analyst_estimates WHERE ticker=? AND section='calendar' "
                           "AND field='Earnings Date'", (t,)):
        for part in str(txt or "").split(","):
            part = part.strip()[:10]
            if len(part) == 10 and part[4] == "-":
                dates.add(part)
    window = (dt.date.today() - dt.timedelta(days=SEC_WINDOW_DAYS)).isoformat()
    for d in sorted(dates, reverse=True):
        if window <= d <= today and (filed or "") < d:
            return True, f"earnings on {d}, no filing since (SEC not reachable)"
    # Yahoo rolls the calendar to the next date after a report, so also use the reported surprises:
    # a quarter Yahoo has results for that ends well after our latest period = filing still missing
    rep = _q(conn, "SELECT MAX(quarter_end) FROM earnings_surprises WHERE ticker=?", (t,))[0][0]
    if rep and rep > (dt.date.fromisoformat(end) + dt.timedelta(days=20)).isoformat():
        return True, f"results for quarter ending {rep} reported, latest filing period {end} (SEC not reachable)"
    if end < (dt.date.today() - dt.timedelta(days=SEC_STALE_DAYS)).isoformat():
        return True, f"latest period {end} is over {SEC_STALE_DAYS} days old (SEC not reachable)"
    return False, "up to date"


def sec_due_all(conn, tickers, max_days=14):
    """Daily task: which of `tickers` have a new periodic report? Reads the SEC daily index (one request
    per day for ALL companies) for the days since the last run. Returns ({ticker: reason}, tickers that
    still need a per-company check because the index could not cover them)."""
    from . import sec
    cos = {cik: t for t, cik in _q(conn, "SELECT ticker, cik FROM companies WHERE ticker IS NOT NULL")
           if t in set(tickers)}
    last = _q(conn, "SELECT MAX(message) FROM fetch_log WHERE source='sec_daily_index' AND status='ok'")[0][0]
    today = dt.datetime.now(NY).date()
    start = dt.date.fromisoformat(last) + dt.timedelta(days=1) if last else today - dt.timedelta(days=max_days)
    if (today - start).days > max_days:                  # too long ago: check each company instead
        return {}, sorted(cos.values())
    due, d = {}, start
    while d <= today:
        try:
            rows = sec.daily_index(d)
        except Exception as e:  # noqa: BLE001
            db.log(conn, None, "sec_daily_index", "error", None, f"{d}: {e}"[:300])
            return due, sorted(set(cos.values()) - set(due))
        if rows is not None:
            for cik, form, accn, filed in rows:
                if cik in cos and accn not in sec._known_accns(conn, cik):
                    due[cos[cik]] = f"new {form} filed {filed[:4]}-{filed[4:6]}-{filed[6:]}"
            if d < today:                                 # today's index can still grow
                db.log(conn, None, "sec_daily_index", "ok", len(rows), d.isoformat())
        d += dt.timedelta(days=1)
    return due, []


def update_prices(conn, t):
    """Full daily history (one Yahoo request), then the daily valuation for that ticker."""
    from . import metrics, prices
    before = _q(conn, "SELECT date, close FROM prices WHERE ticker=? AND close IS NOT NULL ORDER BY date DESC LIMIT 1", (t,))
    splits_before = _splits(conn, t)
    n = prices.load_prices(conn, t)
    cik = _cik(conn, t)
    if cik is not None:
        if _splits(conn, t) != splits_before:
            after_split(conn, t)
        else:
            metrics.compute_valuation(conn, cik, t)
    after = _q(conn, "SELECT date, close FROM prices WHERE ticker=? AND close IS NOT NULL ORDER BY date DESC LIMIT 1", (t,))
    return dict(rows=n, changed=before != after)


def _splits(conn, t):
    return set(_q(conn, "SELECT date, ratio FROM splits WHERE ticker=?", (t,)))


def after_split(conn, t):
    """A new stock split: the prices are adjusted by the download, but the SEC numbers (shares, EPS, book value
    per share) are only put on the new share basis when the company is recalculated. Without this the market
    cap jumps on the split day and P/E is wrong until the next quarterly report."""
    from . import standardize
    db.log(conn, t, "split", "ok", None, "new split: company recalculated")
    standardize.run(conn, [t])


def update_analyst(conn, t):
    from . import analyst
    n = analyst.load_analyst(conn, t)
    return dict(values=n)


def update_sec(conn, t):
    """SEC data only (no Yahoo), then recalculate. Slow (10-30 s): call in the background."""
    from . import fetch, standardize, starter
    if not starter.ensure_raw(conn, t, _cik(conn, t)):   # (a starter-pack company gets everything there)
        fetch.fetch_ticker(conn, t, refresh=True, with_prices=False)
    standardize.run(conn, [t])
    return dict(ok=True)


def step(conn, t, name, fn):
    t0 = time.time()
    try:
        res = fn()
        out = res if isinstance(res, dict) else {"result": res}     # e.g. holdings.load returns a count
        out["ok"] = True
    except Exception as e:  # noqa: BLE001 - one failing source must not stop the rest
        db.log(conn, t, f"update_{name}", "error", None, str(e)[:500])
        out = dict(ok=False, error=str(e)[:200])
    out["seconds"] = round(time.time() - t0, 2)
    return out


# --------------------------------------------------------------------------- batched prices (daily task)
def batch_prices(conn, tickers):
    """Last month of prices for many tickers in ONE Yahoo request. Tickers with a new split (whole
    history changes) or no data fall back to a full single-ticker download. Returns {ticker: changed}."""
    import yfinance as yf
    from . import metrics, prices
    out = {}
    try:
        df = yf.download(tickers, period="1mo", auto_adjust=False, actions=True, group_by="ticker",
                         progress=False, threads=True)
    except Exception:  # noqa: BLE001
        df = None
    for t in tickers:
        try:
            sub = df[t] if df is not None and t in df.columns.get_level_values(0) else None
            sub = sub.dropna(subset=["Close"]) if sub is not None else None
            known_splits = {d for (d,) in _q(conn, "SELECT date FROM splits WHERE ticker=?", (t,))}
            new_split = sub is not None and "Stock Splits" in sub and any(
                prices._date(d) not in known_splits for d, v in sub["Stock Splits"].items() if v)
            before = _q(conn, "SELECT date, close FROM prices WHERE ticker=? AND close IS NOT NULL ORDER BY date DESC LIMIT 1", (t,))
            if sub is None or sub.empty or new_split:
                prices.load_prices(conn, t)
            else:
                conn.executemany(
                    "INSERT OR REPLACE INTO prices (ticker, date, open, high, low, close, adj_close, volume) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    [(t, prices._date(i), prices._num(r.get("Open")), prices._num(r.get("High")),
                      prices._num(r.get("Low")), prices._num(r.get("Close")), prices._num(r.get("Adj Close")),
                      None if pd.isna(r.get("Volume")) else int(r.get("Volume"))) for i, r in sub.iterrows()])
                if "Dividends" in sub:
                    conn.executemany("INSERT OR REPLACE INTO dividends (ticker, date, amount) VALUES (?,?,?)",
                                     [(t, prices._date(d), float(v)) for d, v in sub["Dividends"].items() if v])
                from . import splitbasis
                splitbasis.fix_new_rows(conn, t, prices._date(sub.index.min()))
                conn.commit()
                db.log(conn, t, "yahoo_prices", "ok", len(sub), "batched update (1 month)")
            after = _q(conn, "SELECT date, close FROM prices WHERE ticker=? AND close IS NOT NULL ORDER BY date DESC LIMIT 1", (t,))
            cik = _cik(conn, t)
            if cik is not None and new_split:
                after_split(conn, t)
            elif cik is not None and before != after:
                metrics.compute_valuation(conn, cik, t)
            out[t] = before != after
        except Exception as e:  # noqa: BLE001
            db.log(conn, t, "yahoo_prices", "error", None, str(e)[:500])
            out[t] = None
    return out


def all_tickers(conn):
    """Every loaded ticker: companies (also ones without data yet, e.g. before an IPO) + price-only tickers."""
    return sorted({t for (t,) in _q(conn, "SELECT ticker FROM companies WHERE ticker IS NOT NULL")} |
                  {t for (t,) in _q(conn, "SELECT DISTINCT ticker FROM prices")})


def connect():
    conn = db.connect()
    conn.execute("PRAGMA busy_timeout=30000")   # app and daily task may write at the same time
    return conn


def read_only():
    c = sqlite3.connect(DB_PATH, timeout=30)
    return c


if __name__ == "__main__":
    # python -m stockterminal.update AAPL   -> download new SEC filings for AAPL and recalculate
    import sys
    _conn = connect()
    for _t in sys.argv[1:]:
        print(f"{_t.upper()}: checking the SEC for new filings")
        print(step(_conn, _t.upper(), "sec", lambda: update_sec(_conn, _t.upper())))
    _conn.close()
