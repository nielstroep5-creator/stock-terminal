"""Portfolio analysis: your portfolio seen as one company, and its risk.

As one company (Yahoo for every holding, so all numbers come from one source):
- P/E, forward P/E and P/S are combined the way fund managers do: the earnings (or sales) your money owns
  divided by what you paid for them, i.e. a weighted *harmonic* mean. One share at P/E 200 then counts
  as the small slice of earnings it is, instead of pulling a plain average up. A loss-maker lowers the
  earnings (its earnings yield is negative) instead of being skipped.
- Dividend yield: weighted by value. Growth and margins: weighted by the sales your money owns (value / P/S),
  so the result is "all your companies' sales and profits added up". ETFs have no growth or margins at Yahoo:
  those use your shares only, and each figure says how much of the portfolio it covers.
- Next to the FTSE All-World (VWCE at Yahoo) where Yahoo has the fund's figure.

Risk (last year of daily prices, in your account currency; pf_prices as for the chart):
- volatility (yearly) and max drop of your actual return (time-weighted, from pf_history), beta and
  correlation against the FTSE All-World;
- today's holdings: each one's volatility and beta, its share of the portfolio's risk (risk contribution:
  weight x covariance with the portfolio / portfolio variance), the correlation matrix, and concentration
  (largest holding, top 5, "effective number of holdings" = 1 / sum of squared weights).
"""
import math
import time

from . import db, pf_history, portfolio

BENCH = ("FTSE All-World", "VWCE.DE")
DAYS = 365
HIGH_CORR = 0.7


# ------------------------------------------------------------------------------------------ as one company
def _f(v):
    return v if isinstance(v, (int, float)) and math.isfinite(v) else None


def earnings_yield(info, currency):
    """1 / P/E, or EPS / price for a loss-maker (negative). None when unknown."""
    pe = _f(info.get("trailingPE"))
    if pe and pe > 0:
        return 1 / pe
    eps, price = _f(info.get("trailingEps")), _f(info.get("currentPrice") or info.get("regularMarketPrice"))
    fin = info.get("financialCurrency")
    if eps is not None and price and (not fin or fin == portfolio._major(1, currency)[1] or fin == info.get("currency")):
        return eps / price
    return None


def dividend_yield(info):
    for key in ("trailingAnnualDividendYield", "yield"):         # both fractions at Yahoo
        v = _f(info.get(key))
        if v is not None:
            return v
    v = _f(info.get("dividendYield"))                             # this one is in percent nowadays
    return None if v is None else v / 100 if v > 0.3 else v


def one_company(rows):
    """rows: [{weight, info, currency, is_fund}] -> {metric: (value, coverage)}."""
    out = {}

    def harmonic(get):
        num = cov = 0.0
        for r in rows:
            y = get(r)
            if y is not None:
                num += r["weight"] * y
                cov += r["weight"]
        return (cov / num if num > 0 else None, cov) if cov else (None, 0.0)

    out["pe"] = harmonic(lambda r: earnings_yield(r["info"], r["currency"]))
    out["forward_pe"] = harmonic(lambda r: (1 / _f(r["info"].get("forwardPE"))) if (_f(r["info"].get("forwardPE")) or 0) > 0 else None)
    out["ps"] = harmonic(lambda r: (1 / _f(r["info"].get("priceToSalesTrailing12Months")))
                         if (_f(r["info"].get("priceToSalesTrailing12Months")) or 0) > 0 else None)
    num = cov = 0.0
    for r in rows:
        y = dividend_yield(r["info"])
        if y is not None:
            num += r["weight"] * y
            cov += r["weight"]
    out["dividend_yield"] = (num / cov if cov else None, cov)

    def by_sales(key):
        num = den = cov = 0.0
        for r in rows:
            v, ps = _f(r["info"].get(key)), _f(r["info"].get("priceToSalesTrailing12Months"))
            if v is None or r["is_fund"]:
                continue
            sales = r["weight"] / ps if ps and ps > 0 else None
            if sales is None:
                continue
            num += sales * v
            den += sales
            cov += r["weight"]
        return (num / den if den else None, cov)

    for key, name in (("revenueGrowth", "revenue_growth"), ("earningsGrowth", "earnings_growth"),
                      ("profitMargins", "net_margin"), ("operatingMargins", "operating_margin")):
        out[name] = by_sales(key)
    return out


# ------------------------------------------------------------------------------------------ risk
def _returns(series):
    out = {}
    prev = None
    for d, v in series:
        if v is not None and prev:
            out[d] = v / prev - 1
        if v is not None:
            prev = v
    return out


def _stats(xs, ys=None):
    n = len(xs)
    if n < 20:
        return None
    mx = sum(xs) / n
    vx = sum((x - mx) ** 2 for x in xs) / (n - 1)
    if ys is None:
        return vx
    my = sum(ys) / n
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (n - 1)


def risk(conn, positions, acc, refresh, notes):
    start = time.strftime("%Y-%m-%d", time.localtime(time.time() - (DAYS + 10) * 86400))
    held = [p for p in positions if p.get("yahoo") and p.get("value")]
    curs = {portfolio._major(1, p["currency"])[1] for p in held} | {"EUR"}
    fx = {c: f"{acc}{c}=X" for c in curs if c and c != acc}
    syms = [p["yahoo"] for p in held] + [BENCH[1]] + list(fx.values())
    if refresh:
        pf_history.refresh_prices(conn, syms, start, notes)

    def series(sym, col="close"):
        return conn.execute(f"SELECT date, {col} FROM pf_prices WHERE symbol=? AND date >= ? ORDER BY date", (sym, start)).fetchall()

    rates = {c: dict(series(s)) for c, s in fx.items()}

    def in_acc(sym, cur, col="close"):
        c = portfolio._major(1, cur)[1]
        scale = 0.01 if cur in ("GBX", "GBp") else 1.0
        out, last_rate = [], None
        for d, v in series(sym, col):
            if c != acc:
                last_rate = rates.get(c, {}).get(d) or last_rate
                if not last_rate:
                    continue
                out.append((d, v * scale / last_rate if v else None))
            else:
                out.append((d, v * scale if v else None))
        return out

    bench = _returns(in_acc(BENCH[1], "EUR", "adj_close"))
    rets = {p["t212_ticker"]: _returns(in_acc(p["yahoo"], p["currency"])) for p in held}
    short = [p["name"] for p in held if len(rets[p["t212_ticker"]]) < 60]
    if short:
        notes.append("Left out of the risk numbers (less than 3 months of prices): " + ", ".join(short))
    held = [p for p in held if len(rets[p["t212_ticker"]]) >= 60]
    if not held:
        return None
    total = sum(p["value"] for p in held)
    w = [p["value"] / total for p in held]
    # common days: every holding traded (missing days of one exchange are left out)
    days = sorted(set.intersection(*(set(rets[p["t212_ticker"]]) for p in held)))
    R = [[rets[p["t212_ticker"]][d] for d in days] for p in held]
    n = len(held)
    cov = [[_stats(R[i], R[j]) for j in range(n)] for i in range(n)]
    port_var = sum(w[i] * w[j] * cov[i][j] for i in range(n) for j in range(n))
    port_days = [sum(w[i] * R[i][k] for i in range(n)) for k in range(len(days))]
    bdays = [d for d in days if d in bench]
    bvals = [bench[d] for d in bdays]
    bvar = _stats(bvals) if len(bvals) >= 20 else None
    idx = {d: k for k, d in enumerate(days)}

    def beta_of(xs_by_day):
        if not bvar:
            return None
        xs = [xs_by_day[idx[d]] for d in bdays]
        return _stats(xs, bvals) / bvar

    hold = []
    for i, p in enumerate(held):
        mc = sum(w[j] * cov[i][j] for j in range(n))
        hold.append(dict(name=p["name"], t212_ticker=p["t212_ticker"], weight=w[i], vol=math.sqrt(cov[i][i] * 252),
                         beta=beta_of(R[i]), risk_share=w[i] * mc / port_var if port_var else None))
    corr = [[cov[i][j] / math.sqrt(cov[i][i] * cov[j][j]) if cov[i][i] and cov[j][j] else None for j in range(n)] for i in range(n)]
    pairs = sorted([(held[i]["name"], held[j]["name"], corr[i][j]) for i in range(n) for j in range(i + 1, n)
                    if corr[i][j] is not None and corr[i][j] >= HIGH_CORR], key=lambda x: -x[2])

    # the actual return series (time-weighted), for volatility and max drop as you lived them
    actual = None
    try:
        h = pf_history.build(refresh=False)
        if not h.get("empty"):
            tw = [(d, v) for d, v in zip(h["dates"], h["series"][acc]["twr"]) if d >= start]
            ar = _returns(tw)
            av = list(ar.values())
            peak, dd = -1e18, 0.0
            for _, v in tw:
                if v:
                    peak = max(peak, v)
                    dd = min(dd, v / peak - 1)
            ab = [d for d in ar if d in bench]
            b = _stats([ar[d] for d in ab], [bench[d] for d in ab]) / _stats([bench[d] for d in ab]) if len(ab) >= 20 else None
            cor = None
            if len(ab) >= 20:
                cv = _stats([ar[d] for d in ab], [bench[d] for d in ab])
                cor = cv / math.sqrt(_stats([ar[d] for d in ab]) * _stats([bench[d] for d in ab]))
            actual = dict(vol=math.sqrt(_stats(av) * 252) if len(av) >= 20 else None, max_drop=dd, beta=b, corr=cor)
    except Exception as e:  # noqa: BLE001
        notes.append(f"return history: {e}")

    # today's holdings through the past year: biggest drop and correlation (used when the actual return is missing)
    level, peak, t_dd = 1.0, 1.0, 0.0
    for x in port_days:
        level *= 1 + x
        peak = max(peak, level)
        t_dd = min(t_dd, level / peak - 1)
    t_corr = None
    if bvar:
        xs = [port_days[idx[d]] for d in bdays]
        vx = _stats(xs)
        t_corr = _stats(xs, bvals) / math.sqrt(vx * bvar) if vx else None
    weights = sorted(w, reverse=True)
    return dict(
        days=len(days), since=days[0] if days else None,
        actual=actual,
        today=dict(vol=math.sqrt(port_var * 252) if port_var > 0 else None, beta=beta_of(port_days), corr=t_corr, max_drop=t_dd),
        world=dict(vol=math.sqrt(bvar * 252) if bvar else None),
        holdings=sorted(hold, key=lambda x: -(x["risk_share"] or 0)),
        corr=dict(names=[p["name"] for p in held], matrix=[[None if c is None else round(c, 3) for c in row] for row in corr]),
        high_pairs=[dict(a=a, b=b, corr=c) for a, b, c in pairs],
        concentration=dict(top1=weights[0], top5=sum(weights[:5]), effective_n=1 / sum(x * x for x in w), n=n))


# ------------------------------------------------------------------------------------------ page
def build(refresh=False):
    page = portfolio.build(live=refresh)
    positions = [p for p in page.get("positions") or [] if p.get("value")]
    if not positions:
        return dict(empty=True)
    acc = page.get("account_currency") or "EUR"
    conn = db.connect()
    try:
        if refresh:
            portfolio.resolve_symbols(conn, positions)
        known = dict(conn.execute("SELECT t212_ticker, yahoo FROM pf_symbols"))
        from .quote import yahoo_symbol
        for p in positions:
            p["yahoo"] = p.get("yahoo") or (yahoo_symbol(p["ticker"]) if p["ticker"] else known.get(p["t212_ticker"]))
        total = sum(p["value"] for p in positions)
        rows = []
        for p in positions:
            info = portfolio._info(conn, p["yahoo"], refresh) if p["yahoo"] else {}
            rows.append(dict(name=p["name"], weight=p["value"] / total, info=info, currency=p["currency"],
                             is_fund=(info.get("quoteType") or "").upper() in ("ETF", "MUTUALFUND")))
        mine = one_company(rows)
        binfo = portfolio._info(conn, BENCH[1], refresh)
        world = one_company([dict(weight=1.0, info=binfo, currency="EUR", is_fund=True)]) if binfo else {}
        notes = []
        rk = risk(conn, positions, acc, refresh, notes)
        return dict(empty=False, account_currency=acc, bench_label=BENCH[0],
                    company={k: dict(value=v[0], coverage=v[1], world=(world.get(k) or (None, 0))[0]) for k, v in mine.items()},
                    risk=rk, notes=list(dict.fromkeys(notes)))
    finally:
        conn.close()
