"""Free price data and profile info from Yahoo Finance (via yfinance).

Yahoo is unofficial and sometimes fails; callers log errors and continue.
"""
import pandas as pd
import yfinance as yf

from . import db


def _date(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def _num(v):
    return None if v is None or pd.isna(v) else float(v)


def load_prices(conn, ticker: str) -> int:
    """Full daily history plus dividends and splits (one Yahoo request)."""
    hist = yf.Ticker(ticker).history(period="max", auto_adjust=False, actions=True)
    if hist is None or hist.empty:
        raise RuntimeError("Yahoo returned no price history")
    t = ticker.upper()
    rows = [
        (t, _date(idx), _num(r.get("Open")), _num(r.get("High")), _num(r.get("Low")),
         _num(r.get("Close")), _num(r.get("Adj Close")),
         None if pd.isna(r.get("Volume")) else int(r.get("Volume")))
        for idx, r in hist.iterrows()
        if not pd.isna(r.get("Close"))      # Yahoo sometimes sends today's row without a close: not a price
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO prices (ticker, date, open, high, low, close, adj_close, volume) "
        "VALUES (?,?,?,?,?,?,?,?)", rows)

    div = hist["Dividends"][hist["Dividends"] != 0] if "Dividends" in hist else pd.Series(dtype=float)
    spl = hist["Stock Splits"][hist["Stock Splits"] != 0] if "Stock Splits" in hist else pd.Series(dtype=float)
    conn.executemany("INSERT OR REPLACE INTO dividends (ticker, date, amount) VALUES (?,?,?)",
                     [(t, _date(d), float(v)) for d, v in div.items()])
    conn.executemany("INSERT OR REPLACE INTO splits (ticker, date, ratio) VALUES (?,?,?)",
                     [(t, _date(d), float(v)) for d, v in spl.items()])
    from . import splitbasis
    splitbasis.reset(conn, t)          # fresh Yahoo numbers: put the spin-off corrections back on
    splitbasis.apply_prices(conn, t)
    conn.commit()
    db.log(conn, t, "yahoo_prices", "ok", len(rows), f"{len(div)} dividends, {len(spl)} splits")
    return len(rows)


def load_profile(conn, ticker: str, cik: int) -> dict:
    """Company profile; returns Yahoo's info dict (reused for the analyst summary)."""
    info = yf.Ticker(ticker).info or {}
    if not info.get("longName") and not info.get("sector"):
        raise RuntimeError("Yahoo returned no profile info")
    shares = info.get("sharesOutstanding")
    db.upsert_company(
        conn, cik,
        sector=info.get("sector"),
        industry=info.get("industry"),
        description=info.get("longBusinessSummary"),
        employees=info.get("fullTimeEmployees"),
        website=info.get("website"),
        shares_outstanding=int(shares) if shares else None,
        beta=info.get("beta"),
        display_name=nice_name(info.get("longName")),
    )
    db.log(conn, ticker.upper(), "yahoo_profile", "ok", 1)
    return info


def nice_name(name):
    """Yahoo's company name as shown in the app ("Amazon.com, Inc."); None when Yahoo has none."""
    import html
    name = html.unescape(str(name or "")).strip()
    return name or None


def fill_display_names(conn, say=print):
    """Companies loaded before display names existed: get Yahoo's name for all of them in a few batched
    quote calls (100 tickers each). Runs in the daily update; does nothing once every company has one."""
    from . import quote
    todo = [t for (t,) in conn.execute("SELECT ticker FROM companies WHERE ticker IS NOT NULL AND display_name IS NULL")]
    done = 0
    for i in range(0, len(todo), 100):
        part = todo[i:i + 100]
        try:
            got = quote._fetch([quote.yahoo_symbol(t) for t in part], False)
        except Exception as e:  # noqa: BLE001
            say(f"Company names: Yahoo did not answer ({e})")
            break
        for t in part:
            qd = got.get(quote.yahoo_symbol(t)) or {}
            n = nice_name(qd.get("longName") or qd.get("shortName"))
            if n:
                conn.execute("UPDATE companies SET display_name=? WHERE ticker=?", (n, t))
                done += 1
    conn.commit()
    if todo:
        say(f"Company names: {done} of {len(todo)} filled in")
    return done


def fill_missing_sectors(conn, say=print, limit=20):
    """Companies whose profile download failed when they were loaded (no sector, so the screener and peers
    miss them): try the profile again. A few per day, so a company Yahoo doesn't know costs little."""
    todo = conn.execute("SELECT ticker, cik FROM companies WHERE ticker IS NOT NULL AND cik IS NOT NULL "
                        "AND (sector IS NULL OR sector = '') ORDER BY ticker LIMIT ?", (limit,)).fetchall()
    done = 0
    for t, cik in todo:
        try:
            if load_profile(conn, t, cik).get("sector"):
                done += 1
        except Exception:  # noqa: BLE001
            pass
    if todo:
        say(f"Sectors: {done} of {len(todo)} companies without a sector filled in")
    return done
