"""The Screener page: one row per loaded company with ~70 numbers, so the page can filter, sort and rank
all of them at once (the filtering itself happens in the app window: 500 rows are instant there).

Everything comes from the local database: the latest daily valuation, the latest TTM / FY metrics, the TTM
financials, price returns from stored closes, dividends and the analyst snapshot. Building the table takes a
few seconds for the S&P 500, so it is kept in memory (see build) and only the price-based numbers are
recalculated with live prices (see reprice).

FIELDS is the catalog the page shows: (key, label, group, format, explanation). Formats: pct (a fraction),
x (multiple), usd, usdps (per share), num, int, days (calendar days from today), text.
"""
import bisect
import datetime as dt
import re
import threading
import time

from . import db, dividends
from .mapping import INDUSTRY_SIC
from .metrics import NEEDS_POSITIVE_BASE

FIELDS = [
    # profile
    ("sector", "Sector", "Profile", "text", "Sector (Yahoo)"),
    ("industry", "Industry", "Profile", "text", "Industry (Yahoo)"),
    ("employees", "Employees", "Profile", "int", "Full-time employees"),
    ("beta", "Beta", "Profile", "num", "How much the stock moves with the market (1 = the same as the market)"),
    # size
    ("market_cap", "Market cap", "Size", "usd", "Share price x shares outstanding"),
    ("enterprise_value", "Enterprise value", "Size", "usd", "Market cap + debt - cash"),
    ("revenue", "Revenue", "Size", "usd", "Revenue, last 12 months"),
    ("net_income", "Net income", "Size", "usd", "Net income, last 12 months"),
    ("fcf", "Free cash flow", "Size", "usd", "Operating cash flow - capex, last 12 months"),
    # price
    ("price", "Price", "Price & returns", "usdps", "Last price (live during market hours)"),
    ("change", "Today", "Price & returns", "pct", "Price change today (live), or of the last session in the database"),
    ("r_1m", "Return 1M", "Price & returns", "pct", "Price change over 1 month (without dividends)"),
    ("r_3m", "Return 3M", "Price & returns", "pct", "Price change over 3 months (without dividends)"),
    ("r_6m", "Return 6M", "Price & returns", "pct", "Price change over 6 months (without dividends)"),
    ("r_ytd", "Return YTD", "Price & returns", "pct", "Price change since the last close of last year"),
    ("r_1y", "Return 1Y", "Price & returns", "pct", "Price change over 1 year (without dividends)"),
    ("r_3y", "Return 3Y", "Price & returns", "pct", "Price change over 3 years (without dividends)"),
    ("r_5y", "Return 5Y", "Price & returns", "pct", "Price change over 5 years (without dividends)"),
    ("from_high", "From 52W high", "Price & returns", "pct", "How far below its highest close of the last 52 weeks"),
    ("from_low", "From 52W low", "Price & returns", "pct", "How far above its lowest close of the last 52 weeks"),
    # valuation
    ("pe", "P/E", "Valuation", "x", "Price / earnings per share, last 12 months (empty when earnings are negative)"),
    ("fwd_pe", "Forward P/E", "Valuation", "x", "Price / analysts' expected earnings per share"),
    ("ps", "P/S", "Valuation", "x", "Market cap / revenue, last 12 months"),
    ("pfcf", "P/FCF", "Valuation", "x", "Market cap / free cash flow, last 12 months"),
    ("pb", "P/B", "Valuation", "x", "Market cap / book value (equity)"),
    ("ev_ebitda", "EV/EBITDA", "Valuation", "x", "Enterprise value / EBITDA, last 12 months"),
    ("ev_sales", "EV/Sales", "Valuation", "x", "Enterprise value / revenue, last 12 months"),
    ("earnings_yield", "Earnings yield", "Valuation", "pct", "Earnings / market cap (the inverse of P/E)"),
    ("fcf_yield", "FCF yield", "Valuation", "pct", "Free cash flow / market cap"),
    ("peg", "PEG ratio", "Valuation", "x", "P/E divided by the expected yearly EPS growth (Yahoo)"),
    ("p_ffo", "P/FFO", "Valuation", "x", "Price / funds from operations (REITs only)"),
    # growth
    ("revenue_yoy", "Revenue growth", "Growth", "pct", "Revenue, last 12 months vs the 12 months before"),
    ("gross_profit_yoy", "Gross profit growth", "Growth", "pct", "Gross profit, last 12 months vs the 12 months before"),
    ("operating_income_yoy", "Operating income growth", "Growth", "pct", "Operating income, last 12 months vs the 12 months before"),
    ("net_income_yoy", "Net income growth", "Growth", "pct", "Net income, last 12 months vs the 12 months before"),
    ("eps_diluted_yoy", "EPS growth", "Growth", "pct", "Diluted EPS, last 12 months vs the 12 months before"),
    ("fcf_yoy", "FCF growth", "Growth", "pct", "Free cash flow, last 12 months vs the 12 months before"),
    ("shares_diluted_yoy", "Share count change", "Growth", "pct", "Change in diluted shares over a year (negative = buybacks)"),
    ("revenue_cagr_3y", "Revenue CAGR 3Y", "Growth", "pct", "Yearly revenue growth over the last 3 fiscal years"),
    ("revenue_cagr_5y", "Revenue CAGR 5Y", "Growth", "pct", "Yearly revenue growth over the last 5 fiscal years"),
    ("revenue_cagr_10y", "Revenue CAGR 10Y", "Growth", "pct", "Yearly revenue growth over the last 10 fiscal years"),
    ("operating_income_cagr_5y", "Operating income CAGR 5Y", "Growth", "pct", "Yearly operating income growth over 5 fiscal years"),
    ("eps_diluted_cagr_5y", "EPS CAGR 5Y", "Growth", "pct", "Yearly EPS growth over the last 5 fiscal years"),
    ("eps_diluted_cagr_10y", "EPS CAGR 10Y", "Growth", "pct", "Yearly EPS growth over the last 10 fiscal years"),
    ("fcf_cagr_5y", "FCF CAGR 5Y", "Growth", "pct", "Yearly free cash flow growth over the last 5 fiscal years"),
    ("rev_growth_next", "Est. revenue growth", "Growth", "pct", "Analysts' expected revenue growth for the next fiscal year"),
    ("eps_growth_next", "Est. EPS growth", "Growth", "pct", "Analysts' expected EPS growth for the next fiscal year"),
    # margins
    ("gross_margin", "Gross margin", "Margins", "pct", "Gross profit / revenue, last 12 months"),
    ("operating_margin", "Operating margin", "Margins", "pct", "Operating income / revenue, last 12 months"),
    ("ebitda_margin", "EBITDA margin", "Margins", "pct", "EBITDA / revenue, last 12 months"),
    ("net_margin", "Net margin", "Margins", "pct", "Net income / revenue, last 12 months"),
    ("fcf_margin", "FCF margin", "Margins", "pct", "Free cash flow / revenue, last 12 months"),
    ("rnd_to_revenue", "R&D / revenue", "Margins", "pct", "Research and development spending / revenue"),
    ("sbc_to_revenue", "Stock comp / revenue", "Margins", "pct", "Stock-based compensation / revenue"),
    # returns
    ("roe", "ROE", "Returns on capital", "pct", "Return on equity: net income / average equity (empty when equity is negative)"),
    ("roa", "ROA", "Returns on capital", "pct", "Return on assets: net income / total assets"),
    ("roic", "ROIC", "Returns on capital", "pct", "Return on invested capital"),
    ("rotce", "ROTCE", "Returns on capital", "pct", "Return on tangible equity (banks)"),
    # balance sheet
    ("net_cash", "Net cash (debt)", "Balance sheet", "usd", "Cash and short-term investments - total debt (negative = net debt)"),
    ("net_debt_ebitda", "Net debt / EBITDA", "Balance sheet", "x", "Years of EBITDA needed to pay off the net debt (negative = net cash)"),
    ("debt_to_equity", "Debt / equity", "Balance sheet", "x", "Total debt / equity (empty when equity is negative)"),
    ("current_ratio", "Current ratio", "Balance sheet", "x", "Current assets / current liabilities"),
    # dividends
    ("div_yield", "Dividend yield", "Dividends", "pct", "Forward: the latest regular dividend x payments a year / price (specials left out)"),
    ("div_yield_ttm", "Dividend yield (TTM)", "Dividends", "pct", "Dividends paid in the last 12 months / price"),
    ("payout_ratio", "Payout ratio", "Dividends", "pct", "Dividend per share of the last 12 months / diluted EPS (REITs: / FFO per share), as on the Dividends tab"),
    ("div_cagr_5y", "Dividend growth 5Y", "Dividends", "pct", "Yearly growth of the dividend over 5 years"),
    ("div_years_growth", "Years of dividend growth", "Dividends", "int", "Years in a row the dividend went up"),
    ("div_years_paid", "Years of dividends", "Dividends", "int", "Years in a row with a dividend"),
    # analysts
    ("target_upside", "Price target upside", "Analysts", "pct", "Upside to the analysts' average price target"),
    ("rating", "Analyst rating", "Analysts", "num", "Average rating: 1 = strong buy, 3 = hold, 5 = strong sell"),
    ("analysts", "Analysts", "Analysts", "int", "Number of analysts with a rating"),
    ("earnings_in", "Next earnings (days)", "Analysts", "days", "Days until the next results"),
    # data
    ("quality", "Data quality", "Data", "pct", "Share of the data checks this company passes (100% = all)"),
    ("yahoo_match", "Matches Yahoo", "Data", "pct",
     "Share of its main annual numbers (last 4 years) within 1% of Yahoo Finance"),
]
FIELD_KEYS = [f[0] for f in FIELDS]

METRICS_TTM = ["revenue_yoy", "gross_profit_yoy", "operating_income_yoy", "net_income_yoy", "eps_diluted_yoy",
               "fcf_yoy", "shares_diluted_yoy", "gross_margin", "operating_margin", "ebitda_margin", "net_margin",
               "fcf_margin", "rnd_to_revenue", "sbc_to_revenue", "roe", "roa", "roic", "rotce", "net_cash",
               "debt_to_equity", "current_ratio", "payout_ratio"]
METRICS_FY = ["revenue_cagr_3y", "revenue_cagr_5y", "revenue_cagr_10y", "operating_income_cagr_5y",
              "eps_diluted_cagr_5y", "eps_diluted_cagr_10y", "fcf_cagr_5y"]
FIN_TTM = ["revenue", "net_income", "fcf", "ebitda"]
FIN_Q = ["cash_and_sti", "total_debt", "total_equity"]
VAL = {"close": "close", "market_cap": "market_cap", "enterprise_value": "enterprise_value", "pe_ttm": "pe",
       "ps_ttm": "ps", "pfcf_ttm": "pfcf", "pb": "pb", "ev_ebitda_ttm": "ev_ebitda", "ev_sales_ttm": "ev_sales",
       "earnings_yield": "earnings_yield", "fcf_yield": "fcf_yield", "dividend_yield": "div_yield",
       "p_ffo_ttm": "p_ffo"}
RETURNS = [("r_1m", 30), ("r_3m", 91), ("r_6m", 182), ("r_1y", 365), ("r_3y", 1096), ("r_5y", 1826)]
STALE_DAYS = 400          # a number older than this (vs the company's newest) is left out, like the stock page

_cache = {"rows": None, "at": 0.0, "stamp": None}
_lock = threading.Lock()
KEEP_SECONDS = 15 * 60    # the database changes for small things too (a watchlist edit): rebuild at most this often


VALUATION_STALE_DAYS = 30


def stale_valuation(val_date, price_date) -> bool:
    """True when the stored valuation (market cap, multiples) stopped long before the last price: no current
    share count, so its numbers are years old (Kinder Morgan's stopped in 2021). Shown as price only."""
    return bool(val_date and price_date and
                val_date < (dt.date.fromisoformat(price_date[:10]) - dt.timedelta(days=VALUATION_STALE_DAYS)).isoformat())


def _latest(conn, table, key, ptype, keys):
    """{cik: {key: value}}: the newest value of each key, dropping values far older than the company's newest."""
    ph = ",".join("?" * len(keys))
    rows = conn.execute(
        f"SELECT cik, {key}, value, MAX(end_date) FROM {table} WHERE period_type=? AND {key} IN ({ph}) "
        f"GROUP BY cik, {key}", (ptype, *keys)).fetchall()
    # the company's newest period of this type, over every key (an item it stopped reporting keeps an old value)
    newest = dict(conn.execute(f"SELECT cik, MAX(end_date) FROM {table} WHERE period_type=? GROUP BY cik", (ptype,)))
    out = {}
    for cik, k, v, end in rows:
        cutoff = (dt.date.fromisoformat(newest[cik]) - dt.timedelta(days=STALE_DAYS)).isoformat()
        if end >= cutoff and (k not in NEEDS_POSITIVE_BASE or end == newest[cik]):
            out.setdefault(cik, {})[k] = v
    return out


def _price_history(conn, tickers):
    """{ticker: ([dates], [closes])} for the last 5 years and a bit."""
    since = (dt.date.today() - dt.timedelta(days=1840)).isoformat()
    out = {}
    for t, d, c in conn.execute("SELECT ticker, date, close FROM prices WHERE date>=? AND close IS NOT NULL "
                                "ORDER BY ticker, date", (since,)):
        if t in tickers:
            h = out.setdefault(t, ([], []))
            h[0].append(d)
            h[1].append(c)
    return out


def _close_on(hist, day):
    i = bisect.bisect_right(hist[0], day)
    return hist[1][i - 1] if i else None


def _dividend_stats(conn):
    """{ticker: dict(div_cagr_5y, div_years_growth, div_years_paid)}: the light part of dividends.summary."""
    pays = {}
    for t, d, a in conn.execute("SELECT ticker, date, amount FROM dividends WHERE amount > 0 ORDER BY ticker, date"):
        pays.setdefault(t, []).append((dt.date.fromisoformat(d[:10]), a))
    today = dt.date.today()
    out = {}
    for t, raw in pays.items():
        regular = [(d, a) for d, a, s in dividends.clean_rows(raw) if not s]
        if not regular or (today - raw[-1][0]).days > 400:
            out[t] = dict(div_years_growth=0, div_years_paid=0, div_annual=0.0)
            continue
        last = regular[-1][0]
        now = dividends._rate_at(regular, last)
        then = dividends._rate_at(regular, last - dt.timedelta(days=round(365.25 * 5)))
        growth = paid = 0
        prev = None
        years = {d.year for d, _ in regular}
        for y in range(raw[0][0].year, today.year):        # completed calendar years, like the Dividends tab
            rate = dividends._rate_at(regular, dt.date(y, 12, 31))
            paid = paid + 1 if y in years else 0
            if prev is not None and rate is not None and rate > prev * 1.0001:
                growth += 1
            elif prev is not None:
                growth = 0
            prev = rate
        out[t] = dict(div_cagr_5y=dividends._cagr(now, then, 5) if then else None,
                      div_years_growth=growth, div_years_paid=paid, div_annual=dividends.forward_annual(raw, today))
    return out


def build(conn=None):
    """All rows from the database (no internet). A few seconds for the S&P 500."""
    from .app import estimates          # imported here: estimates imports from the app package
    own = conn is None
    conn = conn or db.connect()
    try:
        cos = [dict(zip(("cik", "ticker", "name", "sector", "industry", "sic", "employees", "beta", "fiscal_year_end"), r))
               for r in conn.execute("SELECT cik, ticker, COALESCE(display_name, name), sector, industry, sic, employees, beta, fiscal_year_end "
                                     "FROM companies WHERE ticker IS NOT NULL AND cik IN (SELECT DISTINCT cik FROM financials)")]
        for c in cos:
            c["name"] = db.readable_name(c["name"])
            c["sector"] = db.sector_of(c["sector"], c["sic"])
        tickers = {c["ticker"] for c in cos}
        cols = ", ".join(f"v.{k}" for k in VAL)
        val = {r[0]: dict(zip(("date", *VAL.values()), r[1:])) for r in conn.execute(
            f"SELECT v.ticker, v.date, {cols} FROM valuation_daily v JOIN (SELECT ticker, MAX(date) d FROM valuation_daily "
            f"GROUP BY ticker) m ON m.ticker=v.ticker AND m.d=v.date")}
        m_ttm = _latest(conn, "metrics", "metric", "TTM", METRICS_TTM)
        m_fy = _latest(conn, "metrics", "metric", "FY", METRICS_FY)
        f_ttm = _latest(conn, "financials", "item", "TTM", FIN_TTM)
        f_q = _latest(conn, "financials", "item", "Q", FIN_Q)
        banks = {r[0] for r in conn.execute("SELECT DISTINCT cik FROM financials WHERE item='deposits'")}
        quality = dict(conn.execute("SELECT cik, score FROM data_quality"))
        from . import accuracy
        ymatch = accuracy.match_share(conn)
        hist = _price_history(conn, tickers)
        divs = _dividend_stats(conn)
        rows = []
        for c in cos:
            t, cik = c["ticker"], c["cik"]
            v = val.get(t) or {}
            h = hist.get(t)
            if h and stale_valuation(v.get("date"), h[0][-1]):
                # valuation stopped long before the last price (no current share count): its market cap and
                # multiples are years old, so show only the latest price instead of an old one
                v = dict(date=h[0][-1], close=h[1][-1])
            kind = ("bank" if cik in banks else
                    "insurer" if re.match(INDUSTRY_SIC["insurer"], str(c["sic"] or "")) else
                    "reit" if str(c["sic"] or "") == "6798" else "")
            r = dict(ticker=t, name=c["name"], sector=c["sector"], industry=c["industry"], kind=kind,
                     employees=c["employees"], beta=c["beta"], as_of=v.get("date"))
            r.update({k: x for k, x in v.items() if k != "date"})
            r["price"] = r.pop("close", None)
            # without live prices "Today" is the change of the last session in the database
            if h and len(h[1]) > 1 and h[1][-2] and r["price"] and h[0][-1] == r.get("as_of"):
                r["change"] = h[1][-1] / h[1][-2] - 1
            r.update(m_ttm.get(cik, {}))
            r.update(m_fy.get(cik, {}))
            fin = {**f_ttm.get(cik, {}), **f_q.get(cik, {})}
            for k in ("revenue", "net_income", "fcf"):
                r[k] = fin.get(k)
            debt, cash, ebitda = fin.get("total_debt"), fin.get("cash_and_sti"), fin.get("ebitda")
            r["net_debt_ebitda"] = ((debt or 0) - cash) / ebitda if cash is not None and ebitda and ebitda > 0 and not kind else None
            eq = fin.get("total_equity")
            if eq is not None and eq <= 0:    # negative equity (big buybacks): D/E, ROE and P/B mean nothing
                r["debt_to_equity"] = r["roe"] = r["pb"] = None
            if kind == "bank":            # bank balance-sheet numbers do not mean the same thing
                r["net_cash"] = r["net_debt_ebitda"] = r["current_ratio"] = None
            r["quality"] = quality[cik] / 100 if quality.get(cik) is not None else None
            r["yahoo_match"] = ymatch.get(t)
            r.update(divs.get(t, dict(div_years_growth=0, div_years_paid=0)))
            r["payout_ratio"] = dividends.current_payout(conn, t, cik)      # the Dividends tab's number
            # the main yield is the forward one (latest regular dividend x frequency / price, specials left
            # out), the same number as on the Dividends tab; the trailing one is its own column
            r["div_yield_ttm"] = r.get("div_yield")
            annual = r.pop("div_annual", None)
            r["div_yield"] = annual / r["price"] if annual is not None and r.get("price") else r["div_yield_ttm"]
            if not r.get("div_yield"):
                r["div_cagr_5y"] = None
            px = r["price"]
            bases = {}
            if h and px:
                anchor = dt.date.fromisoformat(h[0][-1])     # count back from the last close, like the price chart
                for k, days in RETURNS:
                    day = (anchor - dt.timedelta(days=days)).isoformat()
                    if h[0][0] <= day:
                        bases[k] = _close_on(h, day)
                bases["r_ytd"] = _close_on(h, f"{anchor.year - 1}-12-31") if h[0][0] <= f"{anchor.year - 1}-12-31" else None
                year = [x for d, x in zip(h[0][-260:], h[1][-260:]) if d >= (anchor - dt.timedelta(days=365)).isoformat()]
                if year:
                    bases["hi"], bases["lo"] = max(year), min(year)
            r["_bases"] = bases
            try:
                a = estimates.brief(t, c, px) or {}
            except Exception:  # noqa: BLE001  one company's odd analyst data never breaks the screener
                a = {}
            r.update(fwd_pe=a.get("forward_pe"), peg=a.get("peg"), target=a.get("target_mean"),
                     rating=a.get("rating_mean"), analysts=a.get("analysts"),
                     rev_growth_next=a.get("rev_growth_next"), eps_growth_next=a.get("eps_growth_next"),
                     earnings_date=a.get("earnings_date") if a.get("earnings_upcoming") else None)
            _price_fields(r, px)
            rows.append(r)
        rows.sort(key=lambda r: -(r.get("market_cap") or 0))
        return rows
    finally:
        if own:
            conn.close()


def _price_fields(r, px):
    """Everything that follows from the price: returns, 52-week distance, target upside, days to earnings."""
    b = r.get("_bases") or {}
    for k in [k for k, _ in RETURNS] + ["r_ytd"]:
        r[k] = px / b[k] - 1 if px and b.get(k) else None
    hi, lo = (max(b["hi"], px), min(b["lo"], px)) if px and b.get("hi") else (None, None)
    r["from_high"] = px / hi - 1 if hi else None
    r["from_low"] = px / lo - 1 if lo else None
    r["target_upside"] = r["target"] / px - 1 if r.get("target") and px else None
    d = r.get("earnings_date")
    r["earnings_in"] = (dt.date.fromisoformat(d[:10]) - dt.date.today()).days if d else None


SCALE = ("market_cap", "pe", "ps", "pfcf", "pb", "p_ffo", "fwd_pe", "peg")
INVERSE = ("earnings_yield", "fcf_yield", "div_yield", "div_yield_ttm")


def reprice(r, price, change=None, live=True):
    """A row re-priced at a live price: every price-based number scales (the denominators stay)."""
    old = r.get("price")
    if not old or not price:
        return r
    f = price / old
    out = dict(r, price=price, change=change, live=live)
    for k in SCALE:
        if out.get(k) is not None:
            out[k] *= f
    for k in INVERSE:
        if out.get(k) is not None:
            out[k] /= f
    if r.get("enterprise_value") is not None and r.get("market_cap") is not None:
        ev = r["enterprise_value"] + out["market_cap"] - r["market_cap"]
        for k in ("ev_ebitda", "ev_sales"):
            if r.get(k) is not None and r["enterprise_value"]:
                out[k] = r[k] * ev / r["enterprise_value"] if ev > 0 else None
        out["enterprise_value"] = ev
    _price_fields(out, price)
    return out


def rows(stamp=None):
    """The cached table: rebuilt when the database changed (`stamp`) and the copy is older than KEEP_SECONDS,
    or always when there is none yet. Two pages asking at once build it only once."""
    with _lock:
        hit = _cache["rows"]
        fresh = hit is not None and (stamp == _cache["stamp"] or time.time() - _cache["at"] < KEEP_SECONDS)
        if fresh:
            return hit
        out = build()
        _cache.update(rows=out, at=time.time(), stamp=stamp)
        return out


def public(r):
    return {k: v for k, v in r.items() if not k.startswith("_")}
