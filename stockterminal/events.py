"""Upcoming company events from Yahoo: earnings dates (with the analysts' expected EPS and revenue),
ex-dividend and dividend payment dates, plus the last dividend per share (for an estimate).

Generic on purpose: fetch(symbol) works for any Yahoo symbol, so a general calendar page can reuse it.
The Portfolio calendar (portfolio_calendar) keeps its results in pf_events, which is personal like every
pf_ table (it shows what you hold): a general calendar should keep its own cache.

An earnings date Yahoo gives as a range (two dates) is not confirmed yet. Dates not announced yet within
the next AHEAD_DAYS are added as expected=True, from the company's own rhythm (results: 52 weeks after the
same quarter last year; ex-dividend: a year after last year's, or the usual gap for monthly payers), so the
calendar shows everything coming up; the screen marks them as expected.
"""
import datetime as dt
import json
import time

from . import db, portfolio

CACHE_HOURS = 20
VERSION = 3          # raise when fetch() returns more: older stored results are then fetched again
PAST_DAYS = 183      # and the last 6 months
AHEAD_DAYS = 92      # the calendar shows the next 3 months
NEAR_DAYS = 45       # an expected results date this close to a known one is the same event


def _iso(v):
    if v is None:
        return None
    if isinstance(v, (dt.date, dt.datetime)):
        return v.strftime("%Y-%m-%d")
    try:
        import pandas as pd
        return pd.Timestamp(v).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return None


def _num(v):
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def fetch(symbol):
    """{earnings: [dates], eps_avg, eps_low, eps_high, revenue_avg, ex_dividend, dividend_date,
    last_dividend, last_dividend_date, dividends_per_year} for one Yahoo symbol. Raises when Yahoo fails."""
    import yfinance as yf
    tk = yf.Ticker(symbol)
    cal = tk.calendar or {}
    if not isinstance(cal, dict):                       # very old yfinance: a DataFrame
        cal = {k: v for k, v in cal.to_dict().get(0, {}).items()} if hasattr(cal, "to_dict") else {}
    earn = cal.get("Earnings Date")
    earn = [d for d in (_iso(x) for x in (earn if isinstance(earn, (list, tuple)) else [earn])) if d]
    out = dict(earnings=sorted(set(earn)), eps_avg=_num(cal.get("Earnings Average")), eps_low=_num(cal.get("Earnings Low")),
               eps_high=_num(cal.get("Earnings High")), revenue_avg=_num(cal.get("Revenue Average")),
               ex_dividend=_iso(cal.get("Ex-Dividend Date")), dividend_date=_iso(cal.get("Dividend Date")))
    out["version"] = VERSION
    out["problems"] = []
    out.update(_analyst(tk, out["problems"]))
    try:
        divs = tk.dividends
        if divs is not None and len(divs):
            out["last_dividend"] = float(divs.iloc[-1])
            out["last_dividend_date"] = _iso(divs.index[-1])
            import pandas as pd
            year_ago = divs.index[-1] - pd.Timedelta(days=370)
            out["dividends_per_year"] = int((divs.index > year_ago).sum())
            out["dividend_history"] = [[_iso(d), float(v)] for d, v in divs.iloc[-8:].items()]
    except Exception:  # noqa: BLE001  dividends are a nice extra
        pass
    return out


def _cell(df, row, col):
    try:
        return _num(df.loc[row, col])
    except Exception:  # noqa: BLE001
        return None


def _analyst(tk, problems):
    """The analyst data behind the expectation rating, and the reported results behind the result rating.
    Every part is optional: what Yahoo lacks stays out."""
    out = {}
    try:
        est = tk.earnings_estimate
        out["growth_q"] = _cell(est, "0q", "growth")              # expected EPS growth vs the same quarter a year ago
        out["year_ago_eps_q"] = _cell(est, "0q", "yearAgoEps")
        out["analysts_q"] = _cell(est, "0q", "numberOfAnalysts")
    except Exception as e:  # noqa: BLE001
        problems.append(f"estimates: {str(e)[:80]}")
    try:
        tr = tk.eps_trend
        out["trend_now"], out["trend_30d"] = _cell(tr, "0q", "current"), _cell(tr, "0q", "30daysAgo")
        # this year and next year, for the analyst alerts (alerts.py)
        out["trend_y_now"], out["trend_y_30d"] = _cell(tr, "0y", "current"), _cell(tr, "0y", "30daysAgo")
        out["trend_ny_now"], out["trend_ny_30d"] = _cell(tr, "+1y", "current"), _cell(tr, "+1y", "30daysAgo")
    except Exception as e:  # noqa: BLE001
        problems.append(f"trend: {str(e)[:80]}")
    try:
        rv = tk.eps_revisions
        out["rev_up30"], out["rev_down30"] = _cell(rv, "0q", "upLast30days"), _cell(rv, "0q", "downLast30days")
    except Exception as e:  # noqa: BLE001
        problems.append(f"revisions: {str(e)[:80]}")
    try:                                            # average advice now and a month ago (1 = strong buy .. 5 = sell)
        rs = tk.recommendations_summary
        for period, key in (("0m", "rec_now"), ("-1m", "rec_1m")):
            row = rs[rs["period"] == period].iloc[0]
            n = [float(row.get(k) or 0) for k in ("strongBuy", "buy", "hold", "sell", "strongSell")]
            if sum(n):
                out[key] = sum((i + 1) * c for i, c in enumerate(n)) / sum(n)
                out[key + "_n"] = int(sum(n))
    except Exception as e:  # noqa: BLE001
        problems.append(f"recommendations: {str(e)[:80]}")
    try:                                            # average price target of the analysts
        pt = tk.analyst_price_targets or {}
        out["target_mean"] = _num(pt.get("mean"))
    except Exception as e:  # noqa: BLE001
        problems.append(f"price targets: {str(e)[:80]}")
    try:
        ed = tk.get_earnings_dates(limit=12)
        hist = []
        for d, r in (ed.iterrows() if ed is not None else []):
            hist.append(dict(date=_iso(d), eps_est=_num(r.get("EPS Estimate")), eps_act=_num(r.get("Reported EPS"))))
        out["results"] = sorted(hist, key=lambda h: h["date"] or "")
    except Exception as e:  # noqa: BLE001
        problems.append(f"reported results: {str(e)[:120]}")
    if not out.get("results"):                          # fall back on the quarter-end history (no report date)
        try:
            eh = tk.earnings_history
            rows = []
            for q, r in (eh.iterrows() if eh is not None else []):
                rows.append(dict(date=_iso(q), eps_est=_num(r.get("epsEstimate")), eps_act=_num(r.get("epsActual")), quarter_end=True))
            if rows:
                out["results"] = sorted(rows, key=lambda h: h["date"] or "")
        except Exception as e:  # noqa: BLE001
            problems.append(f"earnings history: {str(e)[:80]}")
    return out


# ------------------------------------------------------------------------------------------ expected dates
def _day(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def expected_earnings(e, today, hi, known):
    """Results dates not announced yet between today and hi: 52 weeks (same weekday) after the report of the
    same quarter a year ago, else every 13 weeks after the last report. Skips dates within NEAR_DAYS of a
    known one (list of dt.date)."""
    reports = sorted(d for d in (_day(r.get("date")) for r in e.get("results") or []
                                 if not r.get("quarter_end") and r.get("eps_act") is not None) if d and d < today)
    if not reports:
        return []
    cands = [d + dt.timedelta(weeks=52) for d in reports]
    if not any(today <= c <= hi for c in cands):                    # no report a year ago: quarterly rhythm
        c = reports[-1]
        while c < today:
            c += dt.timedelta(weeks=13)
        cands = [c, c + dt.timedelta(weeks=13)]
    out = []
    for c in sorted(cands):
        if today <= c <= hi and all(abs((c - k).days) > NEAR_DAYS for k in known + out):
            out.append(c)
    return out


def expected_ex_dividends(e, today, hi, known):
    """Ex-dividend dates not announced yet between today and hi, from the payment history: monthly payers
    every usual gap after the last one, others a year after last year's date. Nothing for a company that
    seems to have stopped paying. Skips dates within half a gap of a known one."""
    hist = sorted(d for d in (_day(h[0]) for h in e.get("dividend_history") or []) if d)
    if len(hist) < 2:
        return []
    gaps = sorted((b - a).days for a, b in zip(hist, hist[1:]))
    gap = gaps[len(gaps) // 2]
    if gap < 20 or (today - hist[-1]).days > max(gap, 120) * 1.5 + 30:
        return []
    if gap < 50:                                                    # monthly (or so)
        cands, c = [], hist[-1]
        while c <= hi:
            c += dt.timedelta(days=gap)
            cands.append(c)
    else:
        cands = [d + dt.timedelta(days=365) for d in hist]
    near = max(gap // 2, 10)
    out = []
    for c in sorted(cands):
        if today <= c <= hi and all(abs((c - k).days) > near for k in known + hist + out):
            out.append(c)
    return out


# ------------------------------------------------------------------------------------------ ratings
# Five steps: 2 very positive, 1 positive, 0 neutral, -1 negative, -2 very negative. Each comes with the
# reasons it is based on, shown when you hover it.
def _step(score):
    return 2 if score >= 3 else 1 if score >= 1 else 0 if score == 0 else -1 if score >= -2 else -2


def rate_expectation(e):
    """Upcoming results: expected EPS growth (-2..+2), estimate revisions in the last 30 days (-1..+1) and
    how often the company beat the estimate in its last 4 results (-1..+1). None without any of them."""
    score, reasons, parts = 0, [], 0
    g = e.get("growth_q")
    if g is not None:
        pts = 2 if g >= 0.2 else 1 if g >= 0.05 else 0 if g > -0.05 else -1 if g > -0.2 else -2
        score += pts; parts += 1
        reasons.append(f"EPS expected {g * 100:+.0f}% vs the same quarter last year ({pts:+d})")
    now, ago = e.get("trend_now"), e.get("trend_30d")
    up, down = e.get("rev_up30") or 0, e.get("rev_down30") or 0
    if now is not None and ago:
        ch = now / ago - 1 if ago > 0 else (now - ago) / abs(ago)
        pts = 1 if ch > 0.02 or (ch >= 0 and up > down) else -1 if ch < -0.02 or (ch <= 0 and down > up) else 0
        score += pts; parts += 1
        reasons.append(f"estimate {ch * 100:+.1f}% in 30 days, {int(up)} analysts raised and {int(down)} lowered ({pts:+d})")
    done = [r for r in e.get("results") or [] if r["eps_act"] is not None and r["eps_est"] is not None][-4:]
    if len(done) >= 2:
        beats = sum(r["eps_act"] > r["eps_est"] for r in done)
        pts = 1 if beats >= len(done) - 1 and beats >= 2 else -1 if beats <= 1 else 0
        score += pts; parts += 1
        reasons.append(f"beat the estimate {beats} of the last {len(done)} times ({pts:+d})")
    if not parts:
        return None
    return dict(step=_step(score), score=score, reasons=reasons)


def rate_surprise(est, act):
    """Reported results: EPS against the estimate."""
    if est is None or act is None:
        return None
    s = (act - est) / abs(est) if est else (1 if act > 0 else -1 if act < 0 else 0)
    step = 2 if s >= 0.10 else 1 if s >= 0.02 else 0 if s > -0.02 else -1 if s > -0.10 else -2
    return dict(step=step, surprise=s, reasons=[f"EPS {act:.2f} vs {est:.2f} expected ({s * 100:+.1f}%)"])


def rate_dividend(per_share, previous):
    """A dividend that was paid: against the one before it."""
    if not per_share or not previous:
        return None
    ch = per_share / previous - 1
    step = 2 if ch >= 0.10 else 1 if ch > 0.01 else 0 if ch >= -0.01 else -1 if ch > -0.10 else -2
    word = "raised" if ch > 0.01 else "cut" if ch < -0.01 else "unchanged"
    return dict(step=step, change=ch, reasons=[f"dividend {word}: {per_share:.4g} vs {previous:.4g} the time before ({ch * 100:+.1f}%)"])


def _reaction(conn, symbol, day):
    """Price change from the last close before the results day to the first close after it (covers results
    before the open and after the close), from the stored daily prices. None without them."""
    before = conn.execute("SELECT close FROM pf_prices WHERE symbol=? AND date < ? AND close IS NOT NULL ORDER BY date DESC LIMIT 1",
                          (symbol, day)).fetchone()
    after = conn.execute("SELECT close FROM pf_prices WHERE symbol=? AND date > ? AND close IS NOT NULL ORDER BY date LIMIT 1",
                         (symbol, day)).fetchone()
    return after[0] / before[0] - 1 if before and after and before[0] else None


def cached(conn, symbol, refresh):
    """fetch() kept CACHE_HOURS in pf_events; the stored one when offline or refresh=False."""
    row = conn.execute("SELECT data, fetched FROM pf_events WHERE symbol=?", (symbol,)).fetchone()
    current = row and json.loads(row[0]).get("version") == VERSION
    if row and (not refresh or (current and time.time() - row[1] < CACHE_HOURS * 3600)):
        return json.loads(row[0])
    if not refresh:
        return None
    try:
        data = fetch(symbol)
    except Exception:  # noqa: BLE001
        return json.loads(row[0]) if row else None
    conn.execute("INSERT OR REPLACE INTO pf_events VALUES (?,?,?)", (symbol, json.dumps(data), time.time()))
    conn.commit()
    return data


def portfolio_calendar(refresh=False):
    """Events of the holdings you have now, from PAST_DAYS ago to AHEAD_DAYS ahead (with expected dates for what
    is not announced yet, see expected_earnings / expected_ex_dividends), oldest first, plus the
    dividends Trading 212 actually paid you in that past window. Reported results go further back (every
    one Yahoo has, about 2 years); those before the window are marked older=True (the Results view shows them)."""
    page = portfolio.build(live=refresh)
    positions = [p for p in page.get("positions") or [] if p.get("quantity")]
    today = dt.date.today()
    lo, hi = (today - dt.timedelta(days=PAST_DAYS)).isoformat(), (today + dt.timedelta(days=AHEAD_DAYS)).isoformat()
    conn = db.connect()
    try:
        if refresh:
            portfolio.resolve_symbols(conn, positions)
        else:
            from .quote import yahoo_symbol
            known = dict(conn.execute("SELECT t212_ticker, yahoo FROM pf_symbols"))
            for p in positions:
                p.setdefault("yahoo", yahoo_symbol(p["ticker"]) if p["ticker"] else known.get(p["t212_ticker"]))
        if refresh:                                     # 3 years of prices, for the share price reaction to older results
            from . import pf_history
            pf_history.refresh_prices(conn, [p["yahoo"] for p in positions if p.get("yahoo")],
                                      (today - dt.timedelta(days=3 * 366)).isoformat(), [])
        items, missing, no_results = [], [], []
        for p in positions:
            if not p.get("yahoo"):
                missing.append(p["name"])
                continue
            e = cached(conn, p["yahoo"], refresh)
            if e is None:
                missing.append(p["name"])
                continue
            base = dict(t212_ticker=p["t212_ticker"], name=p["name"], symbol=p["yahoo"], currency=p["currency"])
            if not any(r.get("eps_act") is not None for r in e.get("results") or []):
                no_results.append(f"{p['name']}" + (f" ({'; '.join(e.get('problems') or [])})" if e.get("problems") else ""))
            ahead = []                                          # (date, date_to, confirmed, expected)
            dates = [d for d in e.get("earnings") or [] if today.isoformat() <= d <= hi]
            if dates:
                ahead.append((dates[0], dates[-1] if len(dates) > 1 else None, len(dates) == 1, False))
            known = [_day(d) for d in e.get("earnings") or [] if _day(d)]
            for r in e.get("results") or []:                    # dates Yahoo lists ahead but not in its calendar
                d = _day(r.get("date"))
                if d and today <= d <= today + dt.timedelta(days=AHEAD_DAYS) and r.get("eps_act") is None \
                        and all(abs((d - k).days) > NEAR_DAYS for k in known):
                    ahead.append((d.isoformat(), None, False, False))
                    known.append(d)
            for d in expected_earnings(e, today, today + dt.timedelta(days=AHEAD_DAYS), known):
                ahead.append((d.isoformat(), None, False, True))
            for n, (d, d_to, conf, exp) in enumerate(sorted(ahead)):
                first = n == 0                                  # Yahoo's estimates are for the next results only
                items.append(dict(base, type="earnings", date=d, date_to=d_to, confirmed=conf, expected=exp,
                                  eps_avg=e.get("eps_avg") if first else None, eps_low=e.get("eps_low") if first else None,
                                  eps_high=e.get("eps_high") if first else None, revenue_avg=e.get("revenue_avg") if first else None,
                                  rating=rate_expectation(e) if first else None, rating_kind="expected" if first else None))
            for r in e.get("results") or []:                    # every result already out (Yahoo: about 2 years)
                if r["date"] and r["date"] < today.isoformat() and r["eps_act"] is not None:
                    rt = rate_surprise(r["eps_est"], r["eps_act"])
                    reaction = None if r.get("quarter_end") else _reaction(conn, p["yahoo"], r["date"])
                    if rt and reaction is not None:
                        rt["reasons"].append(f"share price {reaction * 100:+.1f}% from the close before to the close after")
                    items.append(dict(base, type="earnings", date=r["date"], past=True, eps_est=r["eps_est"], eps_act=r["eps_act"],
                                      reaction=reaction, rating=rt, rating_kind="result", older=r["date"] < lo,
                                      quarter_end=bool(r.get("quarter_end"))))
            per = e.get("last_dividend")
            scale = 0.01 if p["currency"] in ("GBX", "GBp") else 1.0
            est = per * p["quantity"] * scale if per else None
            div_cur = portfolio._major(1, p["currency"])[1]
            hist = e.get("dividend_history") or []
            for kind, key in (("ex_dividend", "ex_dividend"), ("dividend_payment", "dividend_date")):
                d = e.get(key)
                if d and lo <= d <= hi:
                    rt = None
                    if d < today.isoformat() or kind == "dividend_payment":   # a dividend already set: rate it
                        done = [h for h in hist if h[0] <= (d if kind == "ex_dividend" else today.isoformat())]
                        if len(done) >= 2 and (kind == "dividend_payment" or done[-1][0] == d):
                            rt = rate_dividend(done[-1][1], done[-2][1])
                    items.append(dict(base, type=kind, date=d, per_share=per * scale if per else None, estimate=est,
                                      estimate_currency=div_cur, per_year=e.get("dividends_per_year"),
                                      rating=rt, rating_kind="result" if rt else None))
            known = [_day(e.get("ex_dividend"))] if _day(e.get("ex_dividend")) else []
            for d in expected_ex_dividends(e, today, today + dt.timedelta(days=AHEAD_DAYS), known):
                items.append(dict(base, type="ex_dividend", date=d.isoformat(), expected=True, per_share=per * scale if per else None,
                                  estimate=est, estimate_currency=div_cur, per_year=e.get("dividends_per_year"),
                                  rating=None, rating_kind=None))
        hidden = portfolio.hidden()                 # holdings you hid: not in the calendar
        for day, tick, name, amount, cur, gross in conn.execute(
                """SELECT substr(paid_on, 1, 10), t212_ticker, name, amount, currency, gross_per_share FROM pf_dividends
                   WHERE substr(paid_on, 1, 10) BETWEEN ? AND ?""", (lo, today.isoformat())):
            if tick in hidden:
                continue
            pos = next((p for p in positions if p["t212_ticker"] == tick), None)
            prev = conn.execute("""SELECT gross_per_share FROM pf_dividends WHERE t212_ticker=? AND paid_on < ?
                                   AND gross_per_share IS NOT NULL ORDER BY paid_on DESC LIMIT 1""", (tick, day)).fetchone()
            rt = rate_dividend(gross, prev[0] if prev else None)
            items.append(dict(type="dividend_received", date=day, t212_ticker=tick, name=(pos or {}).get("name") or name or tick,
                              amount=amount, amount_currency=cur, rating=rt, rating_kind="result" if rt else None))
        items.sort(key=lambda i: (i["date"], i["type"], i["name"] or ""))
        return dict(today=today.isoformat(), items=items, missing=missing, no_results=no_results, account_currency=page.get("account_currency"),
                    fx=page.get("fx"), other_currency=page.get("other_currency"))
    finally:
        conn.close()
