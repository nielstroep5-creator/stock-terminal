"""ETFs: fund profile from Yahoo (costs, size, yield, top holdings, sector and asset mix) plus returns,
dividends and risk calculated from the stored daily prices.

    python -m stockterminal.etf SPY QQQ DIA     # load / refresh these ETFs (prices + profile)

One Yahoo request for the profile (yfinance info + funds data); prices use prices.load_prices.
Yahoo only publishes the 10 largest holdings; the weights of everything else are not available for free.
"""
import json
import math
import sys
from datetime import date, datetime, timedelta, timezone

from . import db

PROFILE_MAX_AGE_DAYS = 7       # holdings and sector weights move slowly: refreshed weekly by the daily update

SECTOR_NAMES = {
    "technology": "Technology", "consumer_cyclical": "Consumer cyclical", "communication_services": "Communication",
    "healthcare": "Healthcare", "financial_services": "Financials", "industrials": "Industrials",
    "consumer_defensive": "Consumer defensive", "energy": "Energy", "utilities": "Utilities",
    "realestate": "Real estate", "basic_materials": "Materials",
}
ASSET_NAMES = {"stockPosition": "Stocks", "bondPosition": "Bonds", "cashPosition": "Cash",
               "preferredPosition": "Preferred", "convertiblePosition": "Convertibles", "otherPosition": "Other"}

# Well-known ETFs a new user can load in one click (ETFs page) and find by name in the search before they
# are loaded (the SEC's company list doesn't have most funds)
POPULAR = {
    "VOO": "Vanguard S&P 500 ETF", "IVV": "iShares Core S&P 500 ETF", "VTI": "Vanguard Total Stock Market ETF",
    "QQQM": "Invesco NASDAQ 100 ETF", "VT": "Vanguard Total World Stock ETF", "VXUS": "Vanguard Total International Stock ETF",
    "VEA": "Vanguard FTSE Developed Markets ETF", "VWO": "Vanguard FTSE Emerging Markets ETF",
    "SCHD": "Schwab US Dividend Equity ETF", "VYM": "Vanguard High Dividend Yield ETF",
    "VIG": "Vanguard Dividend Appreciation ETF", "JEPI": "JPMorgan Equity Premium Income ETF",
    "VGT": "Vanguard Information Technology ETF", "XLK": "Technology Select Sector SPDR Fund",
    "SMH": "VanEck Semiconductor ETF", "XLF": "Financial Select Sector SPDR Fund",
    "XLV": "Health Care Select Sector SPDR Fund", "XLE": "Energy Select Sector SPDR Fund",
    "VUG": "Vanguard Growth ETF", "VTV": "Vanguard Value ETF", "IWM": "iShares Russell 2000 ETF",
    "RSP": "Invesco S&P 500 Equal Weight ETF", "VNQ": "Vanguard Real Estate ETF",
    "BND": "Vanguard Total Bond Market ETF", "TLT": "iShares 20+ Year Treasury Bond ETF", "GLD": "SPDR Gold Shares",
}


def _f(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def load_profile(conn, ticker):
    """Fetch and store the fund profile. Returns False when Yahoo says it is not an ETF / fund."""
    import yfinance as yf
    t = ticker.upper()
    tk = yf.Ticker(t.replace(".", "-"))
    info = tk.info or {}
    if info.get("quoteType") not in ("ETF", "MUTUALFUND"):
        return False
    holdings, sectors, assets, expense, turnover = [], {}, {}, None, None
    try:
        fd = tk.funds_data
        th = fd.top_holdings
        holdings = [dict(symbol=str(sym).replace("-", "."), name=r.get("Name"), weight=_f(r.get("Holding Percent")))
                    for sym, r in th.iterrows()] if th is not None else []
        sectors = {SECTOR_NAMES.get(k, k): _f(v) for k, v in (fd.sector_weightings or {}).items() if _f(v)}
        assets = {ASSET_NAMES.get(k, k): _f(v) for k, v in (fd.asset_classes or {}).items() if _f(v)}
        ops = fd.fund_operations
        if ops is not None and t in ops.columns:
            col = ops[t]
            expense = _f(col.get("Annual Report Expense Ratio"))
            turnover = _f(col.get("Annual Holdings Turnover"))
    except Exception as e:            # funds data is optional: keep the basics
        print(f"  ! fund details not available: {e}")
    if expense is None and _f(info.get("netExpenseRatio")) is not None:
        expense = _f(info["netExpenseRatio"]) / 100          # Yahoo gives this one in percent
    inception = info.get("fundInceptionDate")
    row = dict(
        ticker=t, name=info.get("longName") or info.get("shortName") or t, family=info.get("fundFamily"),
        category=info.get("category"), legal_type=info.get("legalType"), description=info.get("longBusinessSummary"),
        exchange=info.get("fullExchangeName") or info.get("exchange"),
        inception=datetime.fromtimestamp(inception, timezone.utc).strftime("%Y-%m-%d") if inception else None,
        expense_ratio=expense, total_assets=_f(info.get("totalAssets") or info.get("netAssets")),
        yield_=_f(info.get("yield")), pe=_f(info.get("trailingPE")), beta_3y=_f(info.get("beta3Year")),
        nav=_f(info.get("navPrice")), turnover=turnover, avg_volume=_f(info.get("averageVolume")),
        holdings=json.dumps(holdings), sectors=json.dumps(sectors), assets=json.dumps(assets),
        updated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    cols = list(row)
    conn.execute(f"INSERT OR REPLACE INTO etfs ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                 [row[c] for c in cols])
    conn.commit()
    db.log(conn, t, "yahoo_etf", "ok", len(holdings), f"{len(sectors)} sectors")
    return True


def load_holdings(conn, ticker):
    """Full holdings list (holdings.py). Optional: the page falls back to Yahoo's top 10."""
    from . import holdings
    try:
        return holdings.load(conn, ticker)
    except Exception as e:
        print(f"  ! full holdings not loaded: {e}")
        return 0


def profile_due(conn, ticker):
    r = conn.execute("SELECT updated_at FROM etfs WHERE ticker=?", (ticker,)).fetchone()
    if not r or not r[0]:
        return True
    age = datetime.now(timezone.utc) - datetime.strptime(r[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return age > timedelta(days=PROFILE_MAX_AGE_DAYS)


def all_etfs(conn):
    return [t for (t,) in conn.execute("SELECT ticker FROM etfs ORDER BY ticker")]


# --------------------------------------------------------------------------- calculated from prices
PERIODS = [("1M", 30), ("3M", 91), ("6M", 182), ("YTD", None), ("1Y", 365), ("3Y", 365 * 3),
           ("5Y", 365 * 5), ("10Y", 365 * 10), ("15Y", 365 * 15)]


def _at_or_before(dates, target):
    """Index of the last date <= target (dates sorted ascending), or None."""
    lo, hi = 0, len(dates) - 1
    if not dates or dates[0] > target:
        return None
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if dates[mid] <= target:
            lo = mid
        else:
            hi = mid - 1
    return lo


def returns(dates, close, adj):
    """Price and total return (dividends reinvested) per period; 3Y and longer also per year."""
    if len(dates) < 2:
        return []
    last = date.fromisoformat(dates[-1])
    out = []
    for name, days in PERIODS + [("Max", None)]:
        if name == "YTD":
            i = _at_or_before(dates, f"{last.year - 1}-12-31")
        elif name == "Max":
            i = 0
        else:
            i = _at_or_before(dates, (last - timedelta(days=days)).isoformat())
        if i is None:
            continue
        years = (last - date.fromisoformat(dates[i])).days / 365.25
        pr = close[-1] / close[i] - 1 if close[i] else None
        tr = adj[-1] / adj[i] - 1 if adj[i] and adj[-1] else None
        ann = years >= 2.9
        out.append(dict(period=name, since=dates[i], years=round(years, 2), price=pr, total=tr, annualized=ann,
                        price_ann=(1 + pr) ** (1 / years) - 1 if ann and pr is not None else None,
                        total_ann=(1 + tr) ** (1 / years) - 1 if ann and tr is not None else None))
    return out


def calendar_years(dates, adj):
    """Total return per calendar year (dividends included); the current year is year-to-date."""
    out, prev_end = [], None
    years = sorted({d[:4] for d in dates})
    for y in years:
        i_end = _at_or_before(dates, f"{y}-12-31")
        if prev_end is not None and adj[prev_end] and adj[i_end]:
            out.append(dict(year=int(y), total=adj[i_end] / adj[prev_end] - 1, partial=y == dates[-1][:4]))
        prev_end = i_end
    return out


def risk(dates, adj):
    """Volatility (1Y and 5Y, yearly), max drawdown (all and 5Y) and the current distance from the high."""
    def vol(n):
        a = adj[-(n + 1):]
        r = [a[k] / a[k - 1] - 1 for k in range(1, len(a)) if a[k - 1]]
        if len(r) < 20:
            return None
        m = sum(r) / len(r)
        return math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1)) * math.sqrt(252)

    def drawdown(a, d):
        peak, peak_d, worst = a[0], d[0], (0, None, None)
        for v, dd in zip(a, d):
            if v > peak:
                peak, peak_d = v, dd
            elif peak and v / peak - 1 < worst[0]:
                worst = (v / peak - 1, peak_d, dd)
        return dict(value=worst[0], peak=worst[1], bottom=worst[2])

    i5 = _at_or_before(dates, (date.fromisoformat(dates[-1]) - timedelta(days=365 * 5)).isoformat()) or 0
    hi = max(adj)
    return dict(vol_1y=vol(252), vol_5y=vol(252 * 5) if len(adj) > 252 * 5 else None,
                max_dd=drawdown(adj, dates), max_dd_5y=drawdown(adj[i5:], dates[i5:]),
                from_high=adj[-1] / hi - 1 if hi else None)


def dividends(conn, ticker, price):
    rows = conn.execute("SELECT date, amount FROM dividends WHERE ticker=? ORDER BY date", (ticker,)).fetchall()
    if not rows:
        return dict(ttm=None, ttm_yield=None, per_year=[], recent=[], frequency=None)
    last = date.fromisoformat(rows[-1][0])
    cut = (date.today() - timedelta(days=365)).isoformat()
    ttm = sum(a for d, a in rows if d > cut)
    per = {}
    for d, a in rows:
        per[int(d[:4])] = per.get(int(d[:4]), 0) + a
    n = len([d for d, _ in rows if d > (last - timedelta(days=360)).isoformat()])
    freq = "Monthly" if n >= 10 else "Quarterly" if n >= 3 else "Semi-annual" if n == 2 else "Annual"
    return dict(ttm=ttm or None, ttm_yield=ttm / price if ttm and price else None, frequency=freq,
                per_year=[dict(year=y, amount=v, partial=y == date.today().year) for y, v in sorted(per.items())],
                recent=[dict(date=d, amount=a) for d, a in reversed(rows[-12:])])


def summary(conn, ticker):
    t = ticker.upper()
    r =conn.execute("SELECT * FROM etfs WHERE ticker=?", (t,)).fetchone()
    cols = [c[0] for c in conn.execute("SELECT * FROM etfs LIMIT 0").description]
    prof = dict(zip(cols, r)) if r else None
    if prof:
        for k in ("holdings", "sectors", "assets"):
            prof[k] = json.loads(prof[k] or "null")
        prof["yield"] = prof.pop("yield_")
    px = conn.execute("SELECT date, close, adj_close FROM prices WHERE ticker=? AND close IS NOT NULL ORDER BY date",
                      (t,)).fetchall()
    dates = [p[0] for p in px]
    close = [p[1] for p in px]
    adj = [p[2] or p[1] for p in px]
    out = dict(ticker=t, profile=prof, has_prices=bool(px))
    if px:
        i1 = _at_or_before(dates, (date.fromisoformat(dates[-1]) - timedelta(days=365)).isoformat()) or 0
        out.update(price=close[-1], price_date=dates[-1],
                   change=close[-1] / close[-2] - 1 if len(close) > 1 and close[-2] else None,
                   high_52w=max(close[i1:]), low_52w=min(close[i1:]), first_date=dates[0],
                   returns=returns(dates, close, adj), years=calendar_years(dates, adj), risk=risk(dates, adj),
                   dividends=dividends(conn, t, close[-1]))
    # which holdings are also in this database (to link to their stock page)
    if prof and prof.get("holdings"):
        syms = [h["symbol"] for h in prof["holdings"]]
        have = {s for (s,) in conn.execute(
            f"SELECT ticker FROM companies WHERE ticker IN ({','.join('?' * len(syms))})", syms)}
        for h in prof["holdings"]:
            h["loaded"] = h["symbol"] in have
    return out


def main(argv=None):
    from . import prices
    tickers = [a.upper() for a in (sys.argv[1:] if argv is None else argv)]
    conn = db.connect()
    for t in tickers:
        print(f"== {t} ==")
        n = prices.load_prices(conn, t)
        print(f"  price rows: {n}")
        ok = load_profile(conn, t)
        print("  fund profile: " + ("ok" if ok else "not an ETF according to Yahoo"))
        if ok:
            load_holdings(conn, t)
    conn.close()


if __name__ == "__main__":
    main()
