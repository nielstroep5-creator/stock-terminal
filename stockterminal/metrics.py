"""Ratios, growth rates and daily valuation computed from the standardized financials.

Definitions (all computed the same way for every company):
  growth      *_yoy: vs. same period one year earlier (FY vs prior FY, quarter vs same quarter
              last year, TTM vs TTM a year ago). Like-for-like: the number as first published
              divided by the year-ago number from that same filing, so an accounting change that
              restates only some periods cannot distort it.
              *_cagr_Ny: compound annual growth over N fiscal years.
  margins     item / revenue
  returns     ROE = net income / avg equity; ROA = net income / avg assets;
              ROIC = operating income x (1 - effective tax rate) / avg invested capital,
              invested capital = total debt + equity (cash is not subtracted: for cash-rich companies
              that left almost nothing and gave ROICs of 300-1500%).
              (FY and TTM only; averages use the value one year earlier.)
  valuation   uses only financial data that was public on that date (no look-ahead).
"""
import numpy as np
import pandas as pd

from . import db

GROWTH_ITEMS = ["revenue", "gross_profit", "operating_income", "ebitda", "net_income",
                "eps_diluted", "operating_cf", "fcf", "dps"]
CAGR_ITEMS = ["revenue", "operating_income", "net_income", "eps_diluted", "fcf", "dps"]


def _wide(fin, ptype, col="value"):
    w = fin[fin.period_type == ptype]
    if w.empty:
        return None
    idx = ["end_date", "fiscal_year", "fiscal_quarter", "calendar_quarter"]
    w = w.assign(fiscal_quarter=w.fiscal_quarter.fillna(0))
    p = w.pivot_table(index=idx, columns="item", values=col, aggfunc="first", dropna=False)
    # dropna=False keeps items without any value, but it also makes every combination of the index columns
    # (each end date x each fiscal year x ...). Keep only the periods that exist, the same ones for value,
    # original_value and prior_year_value, so the three tables line up row for row.
    w = p[p.index.isin(pd.MultiIndex.from_frame(w[idx].drop_duplicates()))].reset_index()
    w["end"] = pd.to_datetime(w.end_date)
    return w.sort_values("end").reset_index(drop=True)


def _lag(w, years):
    """Row of `w` whose end date is ~`years` years before each row (within 20 days)."""
    left = pd.DataFrame({"target": w.end - pd.Timedelta(days=round(365.25 * years))})
    right = w.rename(columns={"end": "lag_end"}).sort_values("lag_end")
    m = pd.merge_asof(left, right, left_on="target", right_on="lag_end",
                      direction="nearest", tolerance=pd.Timedelta(days=20))
    return m


def _col(w, c):
    return w[c] if c in w else pd.Series(np.nan, index=w.index)


def _div(a, b):
    b = b.where(b != 0)
    return a / b


# Ratios left blank when their base is zero or below (see _per). Where they show "the latest value", only a
# value of the company's newest period counts, so a blank never falls back to an older number.
NEEDS_POSITIVE_BASE = ("roe", "debt_to_equity", "payout_ratio", "ffo_payout_ratio", "effective_tax_rate",
                       "capital_returned_to_fcf")


def _per(a, b):
    """a / b only where b is above zero: a share of profit, equity or cash flow means nothing when that is a
    loss or negative (a payout of -150% or a tax rate of 300% on a pre-tax loss is not a number to compare)."""
    return a / b.where(b > 0)


def compute_for(w, ptype, w_orig=None, w_prior=None):
    g = lambda c: _col(w, c)  # noqa: E731
    lag1 = _lag(w, 1)
    l1 = lambda c: _col(lag1, c)  # noqa: E731
    out = {}

    def yoy(c):
        prev = l1(c)
        plain = _div(g(c), prev.where(prev > 0)) - 1
        if w_orig is None or w_prior is None:
            return plain
        first, comparable = _col(w_orig, c), _col(w_prior, c)
        like = _div(first, comparable.where(comparable > 0)) - 1
        return like.where(comparable.notna(), plain)

    for c in GROWTH_ITEMS:
        out[f"{c}_yoy"] = yoy(c)
    if ptype == "FY":
        for n in (3, 5, 10):
            lagn = _lag(w, n)
            for c in CAGR_ITEMS:
                a, b = g(c), _col(lagn, c)
                ok = (a > 0) & (b > 0)
                out[f"{c}_cagr_{n}y"] = ((a / b) ** (1 / n) - 1).where(ok)

    rev = g("revenue")
    for name, c in [("gross_margin", "gross_profit"), ("operating_margin", "operating_income"),
                    ("ebitda_margin", "ebitda"), ("net_margin", "net_income"), ("fcf_margin", "fcf"),
                    ("rnd_to_revenue", "rnd"), ("sbc_to_revenue", "sbc"), ("capex_to_revenue", "capex")]:
        out[name] = _div(g(c), rev)

    tax_rate = _per(g("income_tax"), g("pretax_income"))
    out["effective_tax_rate"] = tax_rate
    if ptype in ("FY", "TTM"):
        avg = lambda c: ((g(c) + l1(c)) / 2).fillna(g(c))  # noqa: E731
        # negative equity (years of buybacks: McDonald's, Starbucks) makes ROE meaningless; the latest equity
        # has to be positive too, or a year that crosses zero shows a giant ROE
        out["roe"] = _per(g("net_income"), avg("total_equity").where(g("total_equity") > 0))
        out["roa"] = _div(g("net_income"), avg("total_assets"))
        ic = g("total_debt").fillna(0) + g("total_equity")
        ic_prev = l1("total_debt").fillna(0) + l1("total_equity")
        avg_ic = ((ic + ic_prev) / 2).fillna(ic)
        t = tax_rate.clip(0, 0.5).fillna(0.21)
        out["roic"] = _div(g("operating_income") * (1 - t), avg_ic.where(avg_ic > 0))

    out["net_cash"] = g("cash_and_sti") - g("total_debt").fillna(0)
    out["debt_to_equity"] = _per(g("total_debt"), g("total_equity"))
    out["current_ratio"] = _div(g("total_current_assets"), g("total_current_liabilities"))
    out["fcf_per_share"] = _div(g("fcf"), g("shares_diluted"))
    out["revenue_per_share"] = _div(g("revenue"), g("shares_diluted"))
    out["book_value_per_share"] = _div(g("total_equity"), g("shares_outstanding"))
    out["payout_ratio"] = _per(g("dividends_paid").abs(), g("net_income"))
    ret = g("buybacks").fillna(0) + g("dividends_paid").fillna(0)
    out["capital_returned"] = ret.where(g("buybacks").notna() | g("dividends_paid").notna())
    out["capital_returned_to_fcf"] = _per(out["capital_returned"], g("fcf"))
    out["shares_diluted_yoy"] = yoy("shares_diluted")

    # banks (only present when the company is a bank, see mapping.BANK_MARKERS)
    if "deposits" in w:
        out["efficiency_ratio"] = _div(g("noninterest_expense"), rev)        # lower = better
        out["nii_share_of_revenue"] = _div(g("net_interest_income"), rev)
        out["loans_to_deposits"] = _div(g("loans"), g("deposits"))
        out["equity_to_assets"] = _div(g("total_equity"), g("total_assets"))
        tbv = g("total_equity") - g("goodwill").fillna(0) - g("intangibles").fillna(0)
        out["tangible_book_value_per_share"] = _div(tbv, g("shares_outstanding"))
        for c in ("net_interest_income", "noninterest_income", "pre_provision_profit"):
            out[f"{c}_yoy"] = yoy(c)
        if ptype in ("FY", "TTM"):
            avg = lambda c: ((g(c) + l1(c)) / 2).fillna(g(c))  # noqa: E731
            # net interest income / average total assets (a simple stand-in for net interest margin)
            out["nii_to_assets"] = _div(g("net_interest_income"), avg("total_assets"))
            out["provision_to_loans"] = _div(g("provision_credit_losses"), avg("loans"))
            out["rotce"] = _div(g("net_income_common").fillna(g("net_income")),
                                ((tbv + (_col(lag1, "total_equity") - _col(lag1, "goodwill").fillna(0)
                                         - _col(lag1, "intangibles").fillna(0))) / 2).fillna(tbv))
        for k in ("roic", "current_ratio", "debt_to_equity", "net_cash", "gross_margin", "operating_margin", "ebitda_margin"):
            # meaningless for a bank: deposits and debt are its raw material, and it has no cost of goods
            # or operating line (Citigroup showed an operating margin above 100%)
            out.pop(k, None)

    # insurers (premiums_earned is only kept for insurers, see mapping.INDUSTRY_SIC)
    if "premiums_earned" in w:
        out["loss_ratio"] = _div(g("claims_incurred"), g("premiums_earned"))   # lower = better
        out["premiums_earned_yoy"] = yoy("premiums_earned")
        out["investment_income_to_revenue"] = _div(g("net_investment_income"), rev)
        for k in ("roic", "current_ratio", "gross_margin", "operating_margin", "ebitda_margin"):
            out.pop(k, None)   # no classified balance sheet, no cost of goods, float is not debt

    # REITs (ffo is only kept for REITs)
    if "ffo" in w:
        out["ffo_yoy"] = yoy("ffo")
        out["ffo_per_share_yoy"] = yoy("ffo_per_share")
        out["ffo_margin"] = _div(g("ffo"), rev)
        out["ffo_payout_ratio"] = _per(g("dividends_paid").abs(), g("ffo"))
        # a REIT pays out of FFO, not net income (depreciation makes profit small): Realty Income showed
        # a 265% payout. Its payout ratio is the FFO payout wherever FFO is known.
        out["payout_ratio"] = out["ffo_payout_ratio"].fillna(out["payout_ratio"])
    return out


# Raise when a change to how ratios are worked out must reach data made by older code (a starter pack, another
# copy): at the next start every company's ratios and valuation are recalculated once, in the background.
# 2 = the 3/5/10-year growth (CAGR) and ROE/ROA averages fix of 2026-11-16.
METRICS_VERSION = 2


RECALC = {"done": 0, "total": 0}     # progress of recalc_if_outdated, shown in the top bar while it runs


def recalc_if_outdated(say=print):
    """Ratios made by older code (stored version below METRICS_VERSION): recalculate them for every company,
    from the stored financials (no download, also works for starter-pack companies). Takes a few minutes."""
    from . import db
    conn = db.connect()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS metrics_version (v INTEGER)")
        v = (conn.execute("SELECT MAX(v) FROM metrics_version").fetchone() or [None])[0] or 0
        if v >= METRICS_VERSION:
            return 0
        cos = conn.execute("SELECT cik, ticker FROM companies WHERE cik IS NOT NULL AND ticker IS NOT NULL").fetchall()
        if not cos:                       # empty database: nothing old to fix
            conn.execute("DELETE FROM metrics_version")
            conn.execute("INSERT INTO metrics_version VALUES (?)", (METRICS_VERSION,))
            conn.commit()
            return 0
        say(f"Ratios made by an older version: recalculating {len(cos)} companies")
        n = 0
        RECALC.update(done=0, total=len(cos))
        for cik, t in cos:
            RECALC["done"] += 1
            try:
                compute_metrics(conn, cik)
                compute_valuation(conn, cik, t)
                n += 1
            except Exception as e:  # noqa: BLE001 - one company must not stop the rest
                db.log(conn, t, "metrics_upgrade", "error", None, str(e)[:300])
        conn.execute("DELETE FROM metrics_version")
        conn.execute("INSERT INTO metrics_version VALUES (?)", (METRICS_VERSION,))
        conn.commit()
        db.log(conn, None, "metrics_upgrade", "ok", n, f"ratios recalculated (version {METRICS_VERSION})")
        return n
    finally:
        RECALC.update(done=0, total=0)
        conn.close()


def compute_metrics(conn, cik):
    fin = pd.read_sql_query("SELECT * FROM financials WHERE cik=?", conn, params=(cik,))
    rows = []
    for ptype in ("FY", "Q", "TTM"):
        w = _wide(fin, ptype)
        if w is None:
            continue
        w_orig = _wide(fin, ptype, "original_value")
        w_prior = (_wide(fin, ptype, "prior_year_value")
                   if "prior_year_value" in fin and fin.prior_year_value.notna().any() else None)
        for name, s in compute_for(w, ptype, w_orig, w_prior).items():
            s = s.replace([np.inf, -np.inf], np.nan)
            for i in s.dropna().index:
                fq = int(w.fiscal_quarter[i]) or None
                rows.append((cik, name, ptype, int(w.fiscal_year[i]), fq,
                             w.calendar_quarter[i], w.end_date[i], float(s[i])))
    conn.execute("DELETE FROM metrics WHERE cik=?", (cik,))
    conn.executemany("INSERT INTO metrics VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    return len(rows)


# --------------------------------------------------------------------------- daily valuation
def _asof_series(fin, item, ptypes, dates, with_end=False):
    """For each date: value of `item` from the latest period that was already public."""
    r = fin[(fin.item == item) & fin.period_type.isin(ptypes)].copy()
    if r.empty:
        empty = pd.Series(np.nan, index=dates.index)
        return (empty, pd.Series(pd.NaT, index=dates.index)) if with_end else empty
    r["end"] = pd.to_datetime(r.end_date)
    r["avail"] = pd.to_datetime(r.original_filed)
    # per end date take the version that was public first (e.g. the annual EPS is known on the
    # day of the 10-K, while a separately reported Q4 EPS, needed for TTM, may only appear a year
    # later); TTM before FY when both became public on the same day
    r["pri"] = r.period_type.map({p: n for n, p in enumerate(ptypes)})
    r = r.sort_values(["end", "avail", "pri"]).drop_duplicates("end")
    r = r.sort_values(["avail", "end"])
    r = r[r.end >= r.end.cummax()]          # only rows that move the clock forward
    m = pd.merge_asof(pd.DataFrame({"date": dates}), r[["avail", "value", "end"]],
                      left_on="date", right_on="avail")
    # never carry a number forward for more than ~15 months after its period ended: if a company
    # stops tagging an item, a stale value would silently drive ratios (e.g. P/B on 5-year-old equity)
    fresh = (m.date - m.end) <= pd.Timedelta(days=460)
    if with_end:
        return m.value.where(fresh), m.end.where(fresh)
    return m.value.where(fresh)


def _trailing_dividends(div, dates):
    """Dividends per share paid over the last 12 months, for each date.

    Counted from the most recent payment backwards (350 days), not from today: a plain 365-day
    window catches 3 or 5 quarterly payments whenever a payment date shifts by a few days, which
    makes the yield jump. No payment for 400 days = dividend stopped = 0.
    """
    div = div.assign(date=pd.to_datetime(div.date)).sort_values("date").reset_index(drop=True)
    ex = div.date.values
    amt = div.amount.values
    lo = np.searchsorted(ex, ex - np.timedelta64(350, "D"), side="right")
    csum = np.concatenate([[0.0], np.cumsum(amt)])
    trailing = csum[np.arange(len(ex)) + 1] - csum[lo]            # per payment: sum of last 12 months
    i = np.searchsorted(ex, dates.values, side="right") - 1         # latest payment on/before each date
    out = np.where(i >= 0, trailing[np.clip(i, 0, None)], 0.0)
    stale = (i >= 0) & ((dates.values - ex[np.clip(i, 0, None)]) > np.timedelta64(400, "D"))
    return pd.Series(np.where(stale, 0.0, out), index=dates.index)


STALE_DAYS = 135     # a share count whose period ended longer ago than this is out of date


def _yahoo_fallback(conn, cik, ticker, d, shares, count_end):
    """Yahoo's share count (market cap / price of the daily Yahoo snapshot) for the days that have a
    snapshot, when ours is out of date: a merger or share issue after the latest filing (Kimberly-Clark,
    Waters, Skyworks in Nov 2026). Used when our count is older than STALE_DAYS, or when Yahoo is more than
    25% off for a company with one share class (with several classes sources count differently, so ours
    stays). Older days keep the SEC numbers. Returns (shares, source) with source 'yahoo' or None."""
    src = pd.Series([None] * len(d), index=d.index, dtype=object)
    ys = pd.read_sql_query(
        "SELECT snapshot_date, field, value FROM analyst_estimates WHERE ticker=? AND section='summary' "
        "AND field IN ('marketCap','currentPrice')", conn, params=(ticker,))
    if ys.empty:
        return shares, src
    ys = ys.pivot_table(index="snapshot_date", columns="field", values="value")
    if not {"marketCap", "currentPrice"} <= set(ys.columns):
        return shares, src
    ys = (ys.marketCap / ys.currentPrice).dropna().rename("y").reset_index()
    ys["date"] = pd.to_datetime(ys.snapshot_date)
    # a snapshot from before a stock split has the old share count: put it on today's basis
    for sd, ratio in conn.execute("SELECT date, ratio FROM splits WHERE ticker=? AND ratio > 0", (ticker,)):
        ys.loc[ys.date < pd.Timestamp(sd), "y"] *= ratio
    m = pd.merge_asof(pd.DataFrame({"date": d}), ys[["date", "y"]].sort_values("date"), on="date",
                      direction="nearest", tolerance=pd.Timedelta(days=4))
    y = m.y
    r = y / shares
    several = (conn.execute("SELECT COUNT(DISTINCT tag) FROM xbrl_facts WHERE cik=? AND taxonomy='class' "
                            "AND tag LIKE 'EntityCommonStockSharesOutstanding|%'", (cik,)).fetchone() or [0])[0] >= 2
    stale = (d - count_end).dt.days > STALE_DAYS
    use = y.notna() & (shares.isna() | ((r >= 0.1) & (r <= 10))) & (
        (stale & ((r - 1).abs() > 0.02)) | (not several and ((r - 1).abs() > 0.25)) | shares.isna())
    src[use] = "yahoo"
    return shares.where(~use, y), src


FX_REFRESH_DAYS = 3      # exchange rates are downloaded again when the stored ones are older than this


def reporting_currency(fin):
    """Currency the statements are in (ASML, Novo Nordisk: EUR / DKK), from the revenue rows; USD otherwise."""
    u = fin.loc[fin.item.isin(["revenue", "net_income", "total_assets"]) & fin.unit.notna(), "unit"]
    return u.mode().iloc[0] if len(u) else "USD"


def fx_history(conn, cur, needed_until=None):
    """Daily US dollars per 1 `cur` (Yahoo's rate, e.g. EURUSD=X, kept in pf_prices) as a Series by date.
    The stored history is topped up when it is older than FX_REFRESH_DAYS (or than `needed_until`);
    offline (recalculation) the stored rates are used. None when there are none."""
    sym = f"{cur}USD=X"
    last = conn.execute("SELECT MAX(date) FROM pf_prices WHERE symbol=?", (sym,)).fetchone()[0]
    until = pd.Timestamp(needed_until) if needed_until is not None else pd.Timestamp.today().normalize()
    if last is None or (until - pd.Timestamp(last)).days > FX_REFRESH_DAYS:
        try:
            from .pf_history import _download
            rows = _download(sym, "2000-01-01")
            conn.executemany("INSERT OR REPLACE INTO pf_prices VALUES (?,?,?,?,?)", [(sym, *r) for r in rows])
            conn.commit()
        except Exception as e:  # noqa: BLE001 - offline: use what is stored
            db.log(conn, None, "yahoo_fx", "error", None, f"{sym}: {e}"[:300])
    fx = pd.read_sql_query("SELECT date, close FROM pf_prices WHERE symbol=? AND close > 0 ORDER BY date",
                           conn, params=(sym,))
    if fx.empty:
        return None
    return pd.Series(fx.close.values, index=pd.to_datetime(fx.date))


def period_rate(fx, start, end):
    """US dollars per 1 unit for one period of `fx` (fx_history): the average daily rate over the period
    (at least 5 rates), else the rate on its end date (a balance-sheet date)."""
    if start is not None and not pd.isna(start):
        span = fx[(fx.index >= start) & (fx.index <= end)]
        if len(span) >= 5:
            return float(span.mean())
    before = fx[fx.index <= end]
    return float(before.iloc[-1] if len(before) else fx.iloc[0])


def fx_to_usd(conn, cur, dates):
    """US dollars per 1 `cur` on each of `dates`; NaN without stored rates, so multiples stay empty rather
    than mixing euro earnings with a dollar price."""
    fx = fx_history(conn, cur, dates.max())
    if fx is None:
        return pd.Series(np.nan, index=dates.index)
    m = pd.merge_asof(pd.DataFrame({"date": dates}), fx.rename("close").rename_axis("date").reset_index(), on="date")
    return m.close.bfill()          # days before the first stored rate: the first rate


def compute_valuation(conn, cik, ticker):
    fin = pd.read_sql_query("SELECT * FROM financials WHERE cik=?", conn, params=(cik,))
    px = pd.read_sql_query("SELECT date, close FROM prices WHERE ticker=? AND close IS NOT NULL ORDER BY date",
                           conn, params=(ticker,))
    if fin.empty or px.empty:
        return 0
    px["date"] = pd.to_datetime(px.date)
    first = pd.to_datetime(fin.original_filed).min()
    px = px[px.date >= first].reset_index(drop=True)
    d = px.date

    flow = ["TTM", "FY"]
    bal = ["Q", "FY"]
    ttm = {i: _asof_series(fin, i, flow, d) for i in
           ("revenue", "net_income", "fcf", "ebitda", "shares_diluted", "dps")}
    # EPS is not additive, so a quarter's EPS cannot be derived from the year minus 9 months and
    # TTM EPS can lag. When net income for a later period is already public, use
    # net income to common / diluted shares for that period instead of an older EPS.
    eps, eps_end = _asof_series(fin, "eps_diluted", flow, d, with_end=True)
    nic, nic_end = _asof_series(fin, "net_income_common", flow, d, with_end=True)
    sh_d = _asof_series(fin, "shares_diluted", flow, d)
    newer = nic.notna() & sh_d.notna() & (sh_d > 0) & (eps.isna() | (nic_end > eps_end))
    ttm["eps_diluted"] = eps.where(~newer, nic / sh_d)
    ffo_ps = _asof_series(fin, "ffo_per_share", flow, d)
    b = {i: _asof_series(fin, i, bal, d) for i in
         ("shares_outstanding", "total_debt", "cash_and_sti", "noncontrolling_interest", "total_equity")}

    cur = reporting_currency(fin)
    if cur != "USD":
        # statements still in another currency (standardize converts them to dollars, unless no exchange
        # rate could be downloaded): convert at each day's rate so P/E, EV etc. compare like with like
        rate = fx_to_usd(conn, cur, d)
        for k in ("revenue", "net_income", "fcf", "ebitda", "eps_diluted", "dps"):
            ttm[k] = ttm[k] * rate
        for k in ("total_debt", "cash_and_sti", "noncontrolling_interest", "total_equity"):
            b[k] = b[k] * rate
        ffo_ps = ffo_ps * rate

    shares = b["shares_outstanding"].fillna(ttm["shares_diluted"])
    # a newer cover-page count (dated weeks after the balance sheet) is used once it is public: it already
    # includes a merger or share issue after the quarter. Not when it is far below the balance sheet
    # count (then it is usually one class only) or wildly off (a unit slip).
    _so, so_end = _asof_series(fin, "shares_outstanding", bal, d, with_end=True)
    sc = pd.read_sql_query("SELECT date, filed, value FROM share_counts WHERE cik=? ORDER BY filed", conn, params=(cik,))
    if len(sc):
        # only cover counts a balance sheet confirms (the latest one before it or the first one after it is
        # within 10%): some companies count something else on the cover page every quarter (PG&E includes
        # shares its own subsidiary holds, BlackRock 2x), while a real change (L3Harris after its 2019
        # merger) shows up in the next balance sheet too
        bs = fin[(fin.item == "shares_outstanding") & fin.period_type.isin(bal)].drop_duplicates("end_date")
        bs = bs.assign(end=pd.to_datetime(bs.end_date)).sort_values("end")

        def confirmed(r):
            e = pd.Timestamp(r.date)
            before, after = bs[bs.end <= e].tail(1), bs[bs.end >= e].head(1)
            return any(abs(r.value / x - 1) <= 0.10 for x in [*before.value, *after.value] if x)
        sc = sc[sc.apply(confirmed, axis=1)]
    if len(sc):
        sc["end"] = pd.to_datetime(sc.date)
        sc["avail"] = pd.to_datetime(sc.filed)
        sc = sc.sort_values(["avail", "end"])
        sc = sc[sc.end >= sc.end.cummax()]
        m = pd.merge_asof(pd.DataFrame({"date": d}), sc[["avail", "value", "end"]], left_on="date", right_on="avail")
        cv = m.value.where((m.date - m.end) <= pd.Timedelta(days=460))
        r = cv / shares
        use = cv.notna() & (so_end.isna() | (m.end > so_end)) & (shares.isna() | ((r >= 0.8) & (r <= 10)))
        shares = shares.where(~use, cv)
        so_end = so_end.where(~use, m.end)
    shares, src = _yahoo_fallback(conn, cik, ticker, d, shares, so_end)
    mcap = px.close * shares
    ev = mcap + b["total_debt"].fillna(0) - b["cash_and_sti"].fillna(0) + b["noncontrolling_interest"].fillna(0)

    div = pd.read_sql_query("SELECT date, amount FROM dividends WHERE ticker=? ORDER BY date",
                            conn, params=(ticker,))
    dps_ttm = _trailing_dividends(div, d) if len(div) else ttm["dps"].fillna(0.0)

    pos = lambda s: s.where(s > 0)  # noqa: E731
    bank = (fin.item == "deposits").any()
    out = pd.DataFrame({
        "ticker": ticker, "date": d.dt.strftime("%Y-%m-%d"), "close": px.close,
        "shares_outstanding": shares, "shares_source": src, "market_cap": mcap, "enterprise_value": ev,
        "pe_ttm": px.close / pos(ttm["eps_diluted"]),
        "ps_ttm": mcap / pos(ttm["revenue"]),
        "pfcf_ttm": mcap / pos(ttm["fcf"]),
        "pb": mcap / pos(b["total_equity"]),
        "ev_ebitda_ttm": ev / pos(ttm["ebitda"]),
        "ev_sales_ttm": ev / pos(ttm["revenue"]),
        "earnings_yield": ttm["eps_diluted"] / px.close,
        "fcf_yield": ttm["fcf"] / mcap,
        "dividend_yield": dps_ttm / px.close,
        "p_ffo_ttm": px.close / pos(ffo_ps),
    }).replace([np.inf, -np.inf], np.nan)
    insurer = (fin.item == "premiums_earned").any()
    if bank or insurer:
        # EV, EBITDA and free cash flow have no meaning for a bank; for an insurer the float
        # (premiums held until claims are paid) makes EV, EBITDA and free cash flow misleading
        out[["enterprise_value", "pfcf_ttm", "ev_ebitda_ttm", "ev_sales_ttm", "fcf_yield"]] = np.nan
    out = out[out.market_cap.notna()]
    rows = [tuple(None if pd.isna(v) else v for v in r) for r in out.itertuples(index=False)]
    conn.execute("DELETE FROM valuation_daily WHERE ticker=?", (ticker,))
    conn.executemany(f"INSERT INTO valuation_daily ({','.join(out.columns)}) "
                     f"VALUES ({','.join('?' * len(out.columns))})", rows)
    conn.commit()
    return len(rows)
