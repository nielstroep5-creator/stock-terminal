"""Sector and industry pages: every sector and industry with its companies, the median margins, growth and
valuation of the group, and where each company ranks inside it ("#3 of 24").

Everything is built from the Screener table (screener.rows: one row per company, kept in memory), so it needs
no internet and no extra data. Values are in US dollars like the rest of the Screener. Sector = Yahoo's sector
(or one from the SEC industry code), industry = Yahoo's industry.

Rules for the numbers:
- A median needs at least MIN_MEDIAN companies with the number; with fewer it is left out.
- A multiple where lower is better (P/E, EV/EBITDA, debt / equity) only counts when it is above zero: a
  negative P/E is not "cheap", so it neither ranks first nor pulls the median down.
- Rank 1 = the best in the group. Companies with the same value share a rank.
"""
import statistics

OTHER = "Other"
MIN_MEDIAN = 2

# (key, label, group, format, better): better is "high" or "low"
METRICS = [
    ("pe", "P/E", "Valuation", "x", "low"),
    ("fwd_pe", "Forward P/E", "Valuation", "x", "low"),
    ("ps", "P/S", "Valuation", "x", "low"),
    ("ev_ebitda", "EV/EBITDA", "Valuation", "x", "low"),
    ("pfcf", "P/FCF", "Valuation", "x", "low"),
    ("pb", "P/B", "Valuation", "x", "low"),
    ("fcf_yield", "FCF yield", "Valuation", "pct", "high"),
    ("div_yield", "Dividend yield", "Valuation", "pct", "high"),
    ("gross_margin", "Gross margin", "Margins", "pct", "high"),
    ("operating_margin", "Operating margin", "Margins", "pct", "high"),
    ("net_margin", "Net margin", "Margins", "pct", "high"),
    ("fcf_margin", "FCF margin", "Margins", "pct", "high"),
    ("revenue_yoy", "Revenue growth", "Growth", "pct", "high"),
    ("eps_diluted_yoy", "EPS growth", "Growth", "pct", "high"),
    ("revenue_cagr_5y", "Revenue CAGR 5Y", "Growth", "pct", "high"),
    ("roe", "ROE", "Returns", "pct", "high"),
    ("roic", "ROIC", "Returns", "pct", "high"),
    ("debt_to_equity", "Debt / equity", "Returns", "x", "low"),
    ("r_1y", "Return 1Y", "Returns", "pct", "high"),
]
KEYS = [m[0] for m in METRICS]
BETTER = {m[0]: m[4] for m in METRICS}


def _value(r, k):
    """The number as it counts for medians and ranks (None when it does not count)."""
    v = r.get(k)
    if not isinstance(v, (int, float)) or v != v:      # None, text or NaN
        return None
    if BETTER[k] == "low" and v <= 0:
        return None
    return float(v)


def sector(r):
    return r.get("sector") or OTHER


def industry(r):
    return r.get("industry") or OTHER


def medians(rows):
    out = {}
    for k in KEYS:
        xs = [x for x in (_value(r, k) for r in rows) if x is not None]
        out[k] = statistics.median(xs) if len(xs) >= MIN_MEDIAN else None
    return out


def ranks(rows):
    """{ticker: {key: [rank, of]}} inside this group of rows."""
    out = {r["ticker"]: {} for r in rows}
    for k in KEYS:
        vals = [(r["ticker"], _value(r, k)) for r in rows]
        xs = sorted(x for _, x in vals if x is not None)
        n = len(xs)
        if n < 2:
            continue
        for t, x in vals:
            if x is None:
                continue
            better = sum(1 for y in xs if (y > x if BETTER[k] == "high" else y < x))
            out[t][k] = [better + 1, n]
    return out


def _summary(name, rows):
    return dict(name=name, count=len(rows), market_cap=sum(r.get("market_cap") or 0 for r in rows) or None,
                med=medians(rows))


def _groups(rows, key):
    by = {}
    for r in rows:
        by.setdefault(key(r), []).append(r)
    return by


def metrics_info():
    return [dict(key=k, label=l, group=g, fmt=f, better=b) for k, l, g, f, b in METRICS]


def overview(rows):
    """Every sector: number of companies, total market cap, number of industries and the medians."""
    out = []
    for name, rs in _groups(rows, sector).items():
        s = _summary(name, rs)
        s["industries"] = len({industry(r) for r in rs})
        s["top"] = [r["ticker"] for r in sorted(rs, key=lambda r: -(r.get("market_cap") or 0))[:3]]
        out.append(s)
    out.sort(key=lambda s: (s["name"] == OTHER, -(s["market_cap"] or 0)))
    return dict(metrics=metrics_info(), sectors=out, count=len(rows), med=medians(rows))


def names(rows):
    """Every sector and industry with its number of companies (the search finds them)."""
    out = []
    for name, rs in _groups(rows, sector).items():
        out.append(dict(sector=name, industry=None, n=len(rs)))
        for ind, irs in _groups(rs, industry).items():
            if ind:
                out.append(dict(sector=name, industry=ind, n=len(irs)))
    return out


def _company(r, rk):
    keep = ("ticker", "name", "industry", "market_cap", "kind", "price", "change")
    return {**{k: r.get(k) for k in keep}, **{k: r.get(k) for k in KEYS}, "ranks": rk.get(r["ticker"], {})}


def group(rows, sector_name, industry_name=None):
    """One sector (with its industries) or one industry: summary, medians and every company with its ranks.
    None when there is no such group."""
    rs = [r for r in rows if sector(r) == sector_name]
    if industry_name is not None:
        rs = [r for r in rs if industry(r) == industry_name]
    if not rs:
        return None
    rs.sort(key=lambda r: -(r.get("market_cap") or 0))
    out = _summary(industry_name or sector_name, rs)
    out.update(sector=sector_name, industry=industry_name, metrics=metrics_info(), market=medians(rows))
    if industry_name is None:
        inds = [_summary(n, x) for n, x in _groups(rs, industry).items()]
        inds.sort(key=lambda s: (s["name"] == OTHER, -(s["market_cap"] or 0)))
        out["industries"] = inds
    else:
        out["sector_med"] = medians([r for r in rows if sector(r) == sector_name])
        # the other industries of the same sector, to move between them
        out["siblings"] = sorted({industry(r) for r in rows if sector(r) == sector_name})
    rk = ranks(rs)
    out["companies"] = [_company(r, rk) for r in rs]
    return out


def position(rows, ticker):
    """Where one company ranks in its industry and its sector, metric by metric (the stock page card)."""
    t = ticker.upper()
    me = next((r for r in rows if r["ticker"] == t), None)
    if not me:
        return None
    sec = [r for r in rows if sector(r) == sector(me)]
    ind = [r for r in sec if industry(r) == industry(me)]
    ri, rs = ranks(ind).get(t, {}), ranks(sec).get(t, {})
    mi, ms = medians(ind), medians(sec)
    items = [dict(key=k, value=me.get(k), ind_med=mi[k], ind_rank=ri.get(k), sec_med=ms[k], sec_rank=rs.get(k))
             for k in KEYS]
    return dict(ticker=t, kind=me.get("kind"), sector=sector(me), industry=industry(me), sector_n=len(sec),
                industry_n=len(ind), metrics=metrics_info(), items=items)
