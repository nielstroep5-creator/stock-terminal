"""Fetch everything for one or more tickers.

Usage:
    python -m stockterminal.fetch AAPL MSFT
    python -m stockterminal.fetch AAPL --refresh      (ignore cached SEC files)
    python -m stockterminal.fetch AAPL --no-prices    (SEC data only)
    python -m stockterminal.fetch AAPL --recent-years 5   (read only the last 5 years of filings
                                                           for segments; older ones later with --backfill)
    python -m stockterminal.fetch AAPL --backfill     (only the filings not read yet + recalculate;
                                                       no other downloads)

Safe to re-run: every write is an upsert.
"""
import argparse
import re
import datetime as dt
import time

from . import analyst, db, filings, prices, sec, standardize


def step(conn, ticker, source, fn):
    """Run one step; on failure log the error and keep going."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - the pipeline should continue
        print(f"  ! {source} failed: {e}")
        db.log(conn, ticker, source, "error", None, str(e)[:500])
        return None


def norm_ticker(ticker: str) -> str:
    """BRK.B / BF.B (as index lists write them) -> BRK-B / BF-B (as SEC and Yahoo write them)."""
    return ticker.strip().upper().replace(".", "-").replace("/", "-")


def since_date(recent_years):
    return (dt.date.today() - dt.timedelta(days=int(365.25 * recent_years))).isoformat() if recent_years else None


def backfill_ticker(conn, ticker: str):
    """Parse the filings that were skipped by --recent-years (own and predecessors'), nothing else."""
    ticker = norm_ticker(ticker)
    cik = sec.lookup_cik(conn, ticker)
    if cik is None:
        return
    print(f"\n== {ticker} (backfill) ==")
    r = step(conn, ticker, "sec_filings", lambda: filings.load_filings(conn, ticker, cik))
    if r is not None:
        print(f"  filings parsed: {r[0]} new ({r[1]} segment facts, {r[2]} company-specific facts)")
    for (pred,) in conn.execute("SELECT predecessor_cik FROM predecessors WHERE cik=? AND predecessor_cik>0",
                                (cik,)).fetchall():
        step(conn, ticker, "sec_filings", lambda: filings.load_filings(conn, ticker, pred, store_cik=cik))


FUND_WORDS = re.compile(r"\b(ETF|ETN|FUND|ISHARES|SPDR|INVESCO|VANGUARD|PROSHARES|DIREXION|SELECT SECTOR)\b")


def looks_like_fund(conn, ticker):
    """Cheap check before asking Yahoo: an ETF already known, or a fund-like name in the SEC list."""
    if conn.execute("SELECT 1 FROM etfs WHERE ticker=?", (ticker,)).fetchone():
        return True
    r = conn.execute("SELECT name FROM tickers WHERE ticker=?", (ticker,)).fetchone()
    return bool(r and FUND_WORDS.search((r[0] or "").upper()))


def fetch_ticker(conn, ticker: str, refresh=False, with_prices=True, since=None):
    ticker = norm_ticker(ticker)
    print(f"\n== {ticker} ==")
    cik = sec.lookup_cik(conn, ticker)
    if cik is not None and with_prices and looks_like_fund(conn, ticker):
        # some ETFs (SPY, DIA) are in the SEC list but file no company financials: ask Yahoo
        from . import etf
        if step(conn, ticker, "yahoo_etf", lambda: etf.load_profile(conn, ticker)):
            n = step(conn, ticker, "yahoo_prices", lambda: prices.load_prices(conn, ticker))
            print(f"  ETF: fund profile ok, price rows: {n}")
            etf.load_holdings(conn, ticker)
            return
    if cik is None:
        print("  ! not in SEC ticker list (ETF or non-US company?) - prices only")
        db.log(conn, ticker, "sec_lookup", "error", None, "ticker not in SEC list")
    else:
        print(f"  CIK {cik}")
        n = step(conn, ticker, "sec_submissions", lambda: sec.load_submissions(conn, ticker, cik, refresh))
        if n is not None:
            print(f"  filings: {n}")
        n = step(conn, ticker, "sec_facts", lambda: sec.load_facts(conn, ticker, cik, refresh))
        if n is not None:
            print(f"  xbrl facts: {n}")
        preds = step(conn, ticker, "sec_predecessor", lambda: sec.load_predecessors(conn, ticker, cik)) or []
        for pred_cik, pred_name in preds:
            print(f"  older history from earlier registration: {pred_name} (CIK {pred_cik})")
        r = step(conn, ticker, "sec_filings", lambda: filings.load_filings(conn, ticker, cik, since=since))
        if r is not None:
            print(f"  filings parsed: {r[0]} new ({r[1]} segment facts, {r[2]} company-specific facts)")
        for pred_cik, _ in preds:
            r = step(conn, ticker, "sec_filings",
                     lambda: filings.load_filings(conn, ticker, pred_cik, store_cik=cik, since=since))
            if r is not None and r[0]:
                print(f"  older filings parsed: {r[0]} new ({r[1]} segment facts)")

    if with_prices:
        n = step(conn, ticker, "yahoo_prices", lambda: prices.load_prices(conn, ticker))
        if n is not None:
            print(f"  price rows: {n}")
        info = None
        if cik is None:              # not an SEC company: an ETF gets its fund profile instead
            from . import etf
            ok = step(conn, ticker, "yahoo_etf", lambda: etf.load_profile(conn, ticker))
            if ok:
                print("  ETF profile: ok")
                etf.load_holdings(conn, ticker)
                return
        if cik is not None:
            info = step(conn, ticker, "yahoo_profile", lambda: prices.load_profile(conn, ticker, cik))
            if info:
                print("  profile: ok")
        n = step(conn, ticker, "yahoo_analyst", lambda: analyst.load_analyst(conn, ticker, info))
        if n is not None:
            print(f"  analyst estimates: {n} values" if n else "  analyst estimates: no coverage")


def main(argv=None):
    p = argparse.ArgumentParser(description="Fetch free SEC + Yahoo data into data/terminal.db")
    p.add_argument("tickers", nargs="+", help="e.g. AAPL MSFT")
    p.add_argument("--refresh", action="store_true", help="ignore cached SEC files")
    p.add_argument("--no-prices", action="store_true", help="skip Yahoo Finance")
    p.add_argument("--recent-years", type=float, default=None,
                   help="read only the filings of the last N years (segments); the rest with --backfill")
    p.add_argument("--backfill", action="store_true", help="only read filings not read yet, then recalculate")
    args = p.parse_args(argv)
    tickers = [norm_ticker(t) for t in args.tickers]

    conn = db.connect()
    t0 = time.time()
    if args.backfill:
        for t in tickers:
            backfill_ticker(conn, t)
    else:
        print(f"SEC ticker list: {sec.load_tickers(conn, args.refresh)} tickers")
        for t in tickers:
            fetch_ticker(conn, t, args.refresh, not args.no_prices, since_date(args.recent_years))
    print("\nStandardizing...")
    standardize.run(conn, tickers)
    conn.close()
    print(f"\nDone in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
