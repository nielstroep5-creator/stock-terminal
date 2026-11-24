"""Everything for the Dividends tab of a stock page: yield, annual dividend, frequency, growth, streaks,
payout ratios and the full payment history.

Payments (ex-dividend date + amount per share, split-adjusted) come from the `dividends` table, which the
price download fills from Yahoo. Payout ratios come from the SEC financials. The next announced dividend
(ex-date and pay date) is asked from Yahoo only when the page asks for it (live=1), and kept in memory.

Rules, so the numbers do not jump around:
- a special (one-off) dividend is a payment far above (1.5x) or below (0.4x) the regular ones on both sides; it counts in the
  history and the per-year totals, never in the annual dividend, the growth or the streaks
- "dividend rate" at a date = the last 4 regular payments before it for a quarterly payer (12 monthly, 2
  semi-annual, 1 annual): a payment that slips into the next year does not give a year with 3 or 5
  payments. No payment for 400 days = the dividend stopped (rate 0).
"""
import datetime as dt
import statistics
import threading
import time

FREQ = [(10, "Monthly", 12), (3, "Quarterly", 4), (2, "Semi-annual", 2), (1, "Annual", 1)]
_next_cache, _next_lock = {}, threading.Lock()
NEXT_HOURS = 12


def _d(s):
    return dt.date.fromisoformat(s[:10])


def mark_special(rows):
    """[(date, amount)] -> [(date, amount, special)]. Special (or a double entry in Yahoo's data) = more than
    1.5x the regular payments on both sides of it (the median of the 2 before and of the 2 after, the larger
    of the two), so a raised dividend, which the payments after it repeat, is never taken for a special one.
    The latest payment has nothing after it: then it needs 3x the ones before. A payment under 0.4x the ones
    on both sides is an odd one too (a small extra payment), kept out of the regular dividend."""
    out = []
    amts = [a for _, a in rows]
    for i, (d, a) in enumerate(rows):
        before = [x for x in amts[max(0, i - 2):i] if x > 0]
        after = [x for x in amts[i + 1:i + 3] if x > 0]
        sides = [statistics.median(x) for x in (before, after) if x]
        both = len(sides) == 2
        special = len(rows) >= 4 and bool(sides) and (a > (1.5 if both else 3) * max(sides)
                                                      or (both and a < 0.4 * min(sides)))
        out.append((d, a, special))
    return out


def _per_year_n(n):
    """Payments counted in a year -> the payment pattern: 12, 4, 2 or 1 a year."""
    return 12 if n >= 10 else 4 if n >= 3 else 2 if n == 2 else 1


def _rate_at(regular, day):
    """The yearly dividend at `day`: the last N regular payments on or before it, N = how often the company
    paid in the year before (12, 4, 2 or 1). Counting payments instead of days: a payment that slips into
    the next year never gives a year with 3 or 5 payments. No payment for 400 days = stopped (0)."""
    past = [(d, a) for d, a in regular if d <= day]
    if not past:
        return None
    last = past[-1][0]
    if (day - last).days > 400:
        return 0.0
    n = _per_year_n(sum(1 for d, _ in past if d > last - dt.timedelta(days=360)))
    return sum(a for _, a in past[-n:])


def _frequency(regular):
    if not regular:
        return None, None
    last = regular[-1][0]
    n = sum(1 for d, _ in regular if d > last - dt.timedelta(days=360))
    for minimum, label, per_year in FREQ:
        if n >= minimum:
            return label, per_year
    return None, None


def _cagr(now, then, years):
    if not now or not then or now <= 0 or then <= 0:
        return None
    return (now / then) ** (1 / years) - 1


def clean_rows(raw):
    """[(date, amount)] -> [(date, amount, special)] without Yahoo's double entries (a "special" a few days
    from a regular payment, KO Sept 2001)."""
    rows = mark_special(raw)
    regs = [d for d, _, s in rows if not s]
    return [(d, a, s) for d, a, s in rows if not (s and any(abs((d - r).days) <= 21 for r in regs))]


def forward_annual(raw, today=None):
    """The annual dividend now: the latest regular payment x how often the company pays. Specials left out;
    0 when the company stopped paying (nothing for 400 days); None without dividends."""
    if not raw:
        return None
    today = today or dt.date.today()
    if (today - raw[-1][0]).days > 400:
        return 0.0
    regular = [(d, a) for d, a, s in clean_rows(raw) if not s]
    _label, per_year = _frequency(regular)
    return regular[-1][1] * per_year if regular and per_year else None


def forward_yield(conn, ticker, price):
    """Forward dividend yield at `price` (the yield shown everywhere in the app); None without dividends."""
    raw = [(_d(d), a) for d, a in conn.execute(
        "SELECT date, amount FROM dividends WHERE ticker=? AND amount > 0 ORDER BY date", (ticker.upper(),))]
    annual = forward_annual(raw)
    if annual is None or not price:
        return None
    return annual / price


def summary(conn, ticker, cik=None, price=None):
    t = ticker.upper()
    raw = [(_d(d), a) for d, a in conn.execute(
        "SELECT date, amount FROM dividends WHERE ticker=? AND amount > 0 ORDER BY date", (t,)).fetchall()]
    if price is None:
        r = conn.execute("SELECT close FROM prices WHERE ticker=? AND close IS NOT NULL ORDER BY date DESC LIMIT 1", (t,)).fetchone()
        price = r[0] if r else None
    out = dict(ticker=t, price=price, has_dividends=bool(raw))
    if not raw:
        return out
    rows = clean_rows(raw)
    regular = [(d, a) for d, a, s in rows if not s]
    today = dt.date.today()
    label, per_year = _frequency(regular)
    freq_n = per_year
    last_d, last_a = regular[-1] if regular else raw[-1]
    stopped = (today - raw[-1][0]).days > 400
    annual = None if stopped or not per_year else last_a * per_year      # forward: latest regular payment x frequency
    ttm = 0.0 if stopped else _rate_at(regular, today)

    # growth of the dividend rate (1, 3, 5, 10 years), measured at the latest payment
    growth = {}
    base_day = last_d
    now_rate = _rate_at(regular, base_day)
    for y in (1, 3, 5, 10):
        then = _rate_at(regular, base_day - dt.timedelta(days=round(365.25 * y)))
        growth[f"{y}y"] = _cagr(now_rate, then, y) if then else None

    # per calendar year: total paid (specials too) and the year-end rate (for the streaks)
    years = {}
    for d, a, s in rows:
        y = years.setdefault(d.year, dict(year=d.year, amount=0.0, regular=0.0, special=0.0, payments=0))
        y["amount"] += a
        y["special" if s else "regular"] += a
        y["payments"] += 1
    per_year = []
    prev_rate = None
    growth_streak = paid_streak = 0
    first_year = raw[0][0].year
    # the current run of payments: after a pause of more than two years (Apple 1995-2012) "paying since" is the
    # restart, and the earlier run is mentioned on its own
    since, earlier = first_year, None
    for (d0, _), (d1, _) in zip(raw, raw[1:]):
        if (d1 - d0).days > 730:
            earlier, since = (since, d0.year), d1.year
    for y in range(first_year, today.year + 1):
        e = years.get(y, dict(year=y, amount=0.0, regular=0.0, special=0.0, payments=0))
        partial = y == today.year
        rate = _rate_at(regular, min(dt.date(y, 12, 31), today))
        prev_e = per_year[-1] if per_year else None
        # growth of the annual dividend (year-end rate), not of what was paid in the calendar year: a payment
        # that slips into January would otherwise show as a cut and then a jump
        e.update(partial=partial, rate=rate,
                 growth=(rate / prev_e["rate"] - 1) if prev_e and prev_e.get("rate") and rate and y > since + 1 else None)
        per_year.append(e)
        if not partial:
            paid_streak = paid_streak + 1 if e["regular"] > 0 else 0
            if prev_rate is not None and rate is not None and rate > prev_rate * 1.0001:
                growth_streak += 1
            elif prev_rate is not None:
                growth_streak = 0
            prev_rate = rate
    # the first year of the history is usually incomplete: its growth is not a real one
    if per_year and per_year[0]["year"] == first_year:
        per_year[0]["growth"] = None

    history = []
    prev = None
    for d, a, s in rows:
        history.append(dict(date=d.isoformat(), amount=a, special=s,
                            change=(a / prev - 1) if prev and not s else None))
        if not s:
            prev = a
    history.reverse()

    out.update(
        frequency=label, per_year_count=freq_n,
        annual=annual, ttm=ttm, yield_fwd=annual / price if annual and price else None,
        yield_ttm=ttm / price if ttm and price else None,
        last_ex=last_d.isoformat(), last_amount=last_a, stopped=stopped,
        first_year=since, earlier_run=earlier, years_paid=paid_streak, years_growth=growth_streak, growth=growth,
        per_year=per_year, history=history,
        specials=sum(1 for _, _, s in rows if s),
    )
    out.update(_payout(conn, cik, regular, ttm))
    out["yield_history"] = _yield_history(conn, t, regular)
    return out


def _payout(conn, cik, regular, rate_ps=None):
    """Payout ratios, latest TTM and per fiscal year: dividends paid / net income, / free cash flow, and for a
    REIT / FFO. When the cash flow statement has no dividends paid line, the per-share version: dividends per
    share (the payments above) / diluted EPS."""
    if not cik:
        return {}
    fin = {}
    for item, ptype, fy, start, end, v in conn.execute(
            "SELECT item, period_type, fiscal_year, start_date, end_date, value FROM financials "
            "WHERE cik=? AND item IN ('dividends_paid','net_income','fcf','ffo','eps_diluted') "
            "AND period_type IN ('FY','TTM') AND value IS NOT NULL", (cik,)).fetchall():
        fin.setdefault((ptype, item), {})[end] = (fy, start, v)

    def val(ptype, item, end):
        r = fin.get((ptype, item), {}).get(end)
        return r[2] if r else None

    def ratio(num, den):
        return abs(num) / den if num is not None and den and den > 0 else None

    def paid_ps(start, end):        # dividends per share with an ex-date inside the period
        if not start:
            return None
        s0, e0 = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
        got = [a for d, a in regular if s0 <= d <= e0]
        return sum(got) if got else None

    def one(ptype, end):
        paid = val(ptype, "dividends_paid", end)
        r = dict(paid=abs(paid) if paid is not None else None,
                 payout=ratio(paid, val(ptype, "net_income", end)),
                 fcf_payout=ratio(paid, val(ptype, "fcf", end)),
                 ffo_payout=ratio(paid, val(ptype, "ffo", end)), basis="cash")
        if paid is None:
            eps = val(ptype, "eps_diluted", end)
            start = (fin.get((ptype, "eps_diluted"), {}).get(end) or (None, None))[1]
            r.update(payout=ratio(paid_ps(start, end), eps), basis="per_share")
        return r

    def ends(ptype):
        return sorted(set(fin.get((ptype, "dividends_paid"), {})) | set(fin.get((ptype, "eps_diluted"), {})))

    ttm = ends("TTM")
    out = dict(one("TTM", ttm[-1])) if ttm else {}
    if "paid" in out:
        out["paid_ttm"] = out.pop("paid")
    out["payout_end"] = ttm[-1] if ttm else None
    # Now: the dividend per share of the last 12 months (the payments) against the latest TTM EPS, FCF and FFO
    # per share. The cash flow line can hold 5 payments in 12 months when a company pays one early
    # (Coca-Cola, Dec 2024), which makes the ratio jump; per share it does not.
    if rate_ps and ttm:
        def latest(item):
            e = sorted(fin.get(("TTM", item), {}))
            return fin[("TTM", item)][e[-1]][2] if e else None
        shares = None
        sh = conn.execute("SELECT value FROM financials WHERE cik=? AND item='shares_diluted' AND period_type='TTM' "
                          "AND value > 0 ORDER BY end_date DESC LIMIT 1", (cik,)).fetchone()
        shares = sh[0] if sh else None
        eps, fcf, ffo = latest("eps_diluted"), latest("fcf"), latest("ffo")
        if eps is not None:
            out.update(payout=ratio(rate_ps, eps), basis="per_share")
        if shares:
            out["fcf_payout"] = ratio(rate_ps * shares, fcf) if fcf is not None else None
            if ffo is not None:
                out["ffo_payout"] = ratio(rate_ps * shares, ffo)
    years = []
    for end in ends("FY"):
        fy = (fin.get(("FY", "dividends_paid"), {}).get(end) or fin.get(("FY", "eps_diluted"), {}).get(end))[0]
        r = one("FY", end)
        if r["payout"] is not None or r["fcf_payout"] is not None:
            years.append(dict(fy=fy, end=end, **r))
    out["payout_years"] = years[-15:]
    return out


# The payout ratio shown as "the" payout of a company (Dividends tab, Screener, ...) is decided here only:
# dividends per share of the last 12 months (regular payments, specials left out) / latest TTM diluted EPS,
# for a REIT / FFO per share instead. Change PAYOUT_BASIS (or this function) and every page follows.
# The per-year bars (Dividends tab) and the Ratios table stay "dividends paid / net income" per fiscal year.
PAYOUT_BASIS = "per_share"


def current_payout(conn, ticker, cik):
    """The payout ratio of now (see PAYOUT_BASIS), or None (no dividend, a loss, no EPS)."""
    if not cik:
        return None
    raw = [(_d(d), a) for d, a in conn.execute(
        "SELECT date, amount FROM dividends WHERE ticker=? AND amount > 0 ORDER BY date", (ticker.upper(),))]
    if not raw:
        return None
    regular = [(d, a) for d, a, s in clean_rows(raw) if not s]
    stopped = (dt.date.today() - raw[-1][0]).days > 400
    return headline_payout(_payout(conn, cik, regular, 0.0 if stopped else _rate_at(regular, dt.date.today())))


def headline_payout(p):
    """From a _payout() result: the one number to show (a REIT's FFO payout, else the payout)."""
    return p.get("ffo_payout") if p.get("ffo_payout") is not None else p.get("payout")


def _yield_history(conn, t, regular):
    """Dividend yield (the regular annual dividend at that time / price), one point a week (the last trading
    day of each week), from the first payment on. Special dividends left out, so a one-off does not show as
    a year-long jump in the yield."""
    if not regular:
        return []
    weekly, week = [], None
    start = regular[0][0] + dt.timedelta(days=365)          # the first year has no full annual dividend yet
    for d, close in conn.execute("SELECT date, close FROM prices WHERE ticker=? AND date >= ? AND close > 0 ORDER BY date",
                                 (t, start.isoformat())).fetchall():
        w = _d(d).isocalendar()[:2]
        if w == week:
            weekly[-1] = (d, close)
        else:
            weekly.append((d, close))
            week = w
    out = []
    for d, close in weekly:
        rate = _rate_at(regular, _d(d))
        if rate is not None:
            out.append([d, rate / close])
    return out


def next_dividend(ticker):
    """The next announced dividend from Yahoo: {ex_date, pay_date}. Kept in memory for NEXT_HOURS.
    Only dates Yahoo gives; an ex-date in the past is the last one, not the next."""
    t = ticker.upper()
    with _next_lock:
        hit = _next_cache.get(t)
        if hit and time.time() - hit[0] < NEXT_HOURS * 3600:
            return hit[1]
    res = None
    try:
        import yfinance as yf
        cal = yf.Ticker(t).calendar or {}
        if isinstance(cal, dict):
            iso = lambda v: v.isoformat()[:10] if hasattr(v, "isoformat") else None  # noqa: E731
            res = dict(ex_date=iso(cal.get("Ex-Dividend Date")), pay_date=iso(cal.get("Dividend Date")))
    except Exception:  # noqa: BLE001  offline or Yahoo changed: the page works without it
        res = None
    with _next_lock:
        _next_cache[t] = (time.time(), res)
    return res
