"""Analyst data for the app: reads the snapshots written by analyst.py (tables analyst_estimates,
analyst_actions, earnings_surprises) and turns Yahoo's relative periods (0q, +1q, 0y, +1y) into real
fiscal periods (Q1 FY27, FY2027).

Yahoo does not roll its rows consistently after a report: per section (EPS, revenue) and even per row
a row can still be the period just reported, or be rolled while its "year ago" still points one period
back. So the period of every row is found on its own (see _map_section), anchored on the first period
that is not reported yet in `financials`; stale rows are hidden and never shift the other section.
"""
import datetime as dt
import sqlite3

from ..config import DB_PATH

YEARS, QUARTERS = ("0y", "+1y"), ("0q", "+1q")
TREND = [("current", "current"), ("7daysAgo", "d7"), ("30daysAgo", "d30"), ("60daysAgo", "d60"), ("90daysAgo", "d90")]
REC = ["strongBuy", "buy", "hold", "sell", "strongSell"]


def _q(sql, params=()):
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(sql, params)]
    finally:
        c.close()


def qlabel(fy, fq):
    return f"Q{fq} FY{str(fy)[2:]}"


# --------------------------------------------------------------------------- loading
def snapshots(ticker):
    """{snapshot_date: {section: {period: {field: value or text}}}}, oldest first."""
    out = {}
    for r in _q("SELECT snapshot_date d, section s, period p, field f, value v, text t FROM analyst_estimates "
                "WHERE ticker=? ORDER BY snapshot_date", (ticker,)):
        sec = out.setdefault(r["d"], {}).setdefault(r["s"], {})
        # sections without periods (summary, calendar, price_target) are stored flat: {field: value}
        (sec.setdefault(r["p"], {}) if r["p"] else sec)[r["f"]] = r["v"] if r["v"] is not None else r["t"]
    return out


def reported(cik):
    """Reported revenue / EPS per fiscal year and quarter from the SEC financials."""
    fy, qs = {}, {}
    if not cik:
        return fy, qs
    for r in _q("SELECT item, period_type, fiscal_year, fiscal_quarter, end_date, value, "
                "COALESCE(original_filed, filed) filed FROM financials "
                "WHERE cik=? AND item IN ('revenue','eps_diluted') AND period_type IN ('FY','Q')", (cik,)):
        k = "revenue" if r["item"] == "revenue" else "eps"
        if r["period_type"] == "FY":
            d = fy.setdefault(r["fiscal_year"], {"end": r["end_date"]})
        else:
            d = qs.setdefault((r["fiscal_year"], r["fiscal_quarter"]), {"end": r["end_date"]})
        d[k] = r["value"]
        if r["filed"]:
            d["filed"] = min(d.get("filed") or r["filed"], r["filed"])
    return fy, qs


def _match(table, rev_ago, eps_ago, latest, tol=0.005):
    """Key of the reported period whose revenue (or else EPS) is closest to Yahoo's 'year ago' number.
    Only periods from a year before the latest reported one on count, so a lookalike from years
    back cannot win."""
    for k, ago in (("revenue", rev_ago), ("eps", eps_ago)):
        if not isinstance(ago, (int, float)) or not ago:
            continue
        # the closest match wins, not the latest: two quarters in a row can be within the tolerance
        # (AMD Q4 2025 10.270B and Q1 2026 10.253B), and picking the later one shifts every period
        hits = [(abs(d[k] / ago - 1), p) for p, d in table.items() if d.get(k) and abs(d[k] / ago - 1) < tol and p >= latest]
        if hits:
            best = min(h[0] for h in hits)
            return max(p for e, p in hits if e == best)
    return None


def _fallback_fy(fye, today):
    """Fiscal year (named after the calendar year it ends in) that contains `today`."""
    m, d = (int(fye[:2]), int(fye[2:])) if fye and len(fye) == 4 else (12, 31)
    for y in (today.year, today.year + 1):
        try:
            end = dt.date(y, m, min(d, 28 if m == 2 else d))
        except ValueError:
            end = dt.date(y, m, 28)
        if end >= today:
            return y
    return today.year + 1


def _same(a, b):
    return isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) <= 1e-3 * max(abs(b), 0.01)


def _quarter_before(date, fye):
    """(fiscal year, quarter) of the last fiscal quarter that ended at least ~10 days before `date`."""
    m = int(fye[:2]) if fye and len(fye) == 4 else 12
    for back in range(0, 7):
        y, mo = divmod(date.year * 12 + date.month - 1 - back, 12)
        mo += 1
        end = dt.date(y + (mo == 12), 1 if mo == 12 else mo + 1, 1) - dt.timedelta(days=1)  # month end
        k = (mo - m) % 12
        if k % 3 or (date - end).days < 10:
            continue
        fq = 4 if k == 0 else k // 3
        fy = y if mo <= m or k == 0 else y + 1
        return (fy, fq)
    return None


def _guess_q0(snap, q_rep, fye):
    """(fy, fq) of Yahoo's 0q: from its "year ago" revenue / EPS, else from the next earnings date."""
    rev, eps = snap.get("revenue_estimate", {}), snap.get("eps_estimate", {})
    last_q = max(q_rep, key=lambda k: q_rep[k]["end"]) if q_rep else None
    m = _match(q_rep, rev.get("0q", {}).get("yearAgoRevenue"), eps.get("0q", {}).get("yearAgoEps"),
               (last_q[0] - 1, last_q[1])) if q_rep else None
    cal = snap.get("calendar", {})
    if m is not None:
        return (m[0] + 1, m[1])
    if _same(cal.get("Earnings Average"), eps.get("0q", {}).get("avg")) and isinstance(cal.get("Earnings Date"), str):
        # the next report is for 0q: the fiscal quarter that ended last before that date
        try:
            return _quarter_before(dt.date.fromisoformat(cal["Earnings Date"][:10]), fye)
        except ValueError:          # Yahoo sometimes gives an empty or odd date (ANT, Nov 2026)
            return None
    return None


# Periods are handled as integer indexes: a fiscal year is its year, a quarter is fy * 4 + fq - 1, so
# "the period before" is idx - 1 and "a year earlier" is idx - YB[kind].
YB = {"year": 1, "quarter": 4}
SECTIONS = (("eps", "eps_estimate", "yearAgoEps"), ("revenue", "revenue_estimate", "yearAgoRevenue"))


def qidx(k):
    return k[0] * 4 + k[1] - 1


def qkey(i):
    return (i // 4, i % 4 + 1)


def _close(a, b, tol):
    return isinstance(a, (int, float)) and isinstance(b, (int, float)) and b != 0 and abs(a / b - 1) < tol


def _num(x):
    return x if isinstance(x, (int, float)) else None


def _map_section(sec, ago_field, kind, anchor, rep):
    """Real fiscal period of each Yahoo row ('0y', '+1y' or '0q', '+1q') of one section.

    Yahoo does not roll its rows consistently after a report: a row can still be the period that
    was just reported (stale: dropped), its average can be rolled while its "year ago" still points
    one period back (lagged: year-ago and growth are rebased on the right period), and the EPS and
    revenue tables can be at different stages. So every row is placed on its own:
      - `anchor` is the first period that is not reported yet (and that Yahoo's 0q is in);
      - the first row is `anchor`, unless it is stale: its year-ago is the reported value of the
        period before last and its average is (about) the period that was just reported;
      - the next row follows the first one, or, after a stale row, is `anchor` again only when its
        year-ago chains to that stale period.
    -> ({idx: row}, [dropped rows])"""
    periods = YEARS if kind == "year" else QUARTERS
    yb = YB[kind]
    out, dropped, est = {}, [], {}
    base = lambda j: [v for v in (rep.get(j), est.get(j)) if v is not None]
    prev = None  # ('ok', idx) or ('stale', avg) for the row before
    for i, p in enumerate(periods):
        r = sec.get(p)
        if not r:
            prev = None
            continue
        avg, ago = _num(r.get("avg")), _num(r.get(ago_field))
        chained = False
        if i == 0 or prev is None:
            idx = anchor + i
        elif prev[0] == "ok":
            idx = prev[1] + 1
        else:  # the row before was stale: Yahoo has not rolled this table, or only partly
            chained = _close(ago, rep.get(anchor - yb), 0.03 if kind == "year" else 0.005) or \
                (kind == "year" and _close(ago, prev[1], 0.01))
            idx = anchor if chained else anchor + 1
        exact = any(_close(ago, v, 0.005) for v in base(idx - yb))
        lag_tol = 0.005 if i == 0 or kind == "quarter" else 0.03
        lagged = ago is not None and not exact and any(_close(ago, v, lag_tol) for v in base(idx - yb - 1))
        # stale only when the year-ago points one period back too: a flat estimate whose year-ago
        # uses another definition must not be mistaken for the reported period
        if i == 0 and lagged and _close(avg, rep.get(anchor - 1), 0.03):
            dropped.append(dict(period=p, kind=kind, idx=anchor - 1, avg=avg))
            prev = ("stale", avg)
            continue
        if idx in rep:  # never show a reported period as an estimate
            dropped.append(dict(period=p, kind=kind, idx=idx, avg=avg))
            prev = None
            continue
        rebased = lagged or chained
        if rebased:
            b = next(iter(base(idx - yb)), None)
            year_ago, growth = b, (avg / b - 1 if b and b > 0 and avg is not None else None)
        else:
            year_ago, growth = ago, _num(r.get("growth"))
        est[idx] = avg
        out[idx] = dict(period=p, idx=idx, avg=avg, low=_num(r.get("low")), high=_num(r.get("high")),
                        analysts=_num(r.get("numberOfAnalysts")), year_ago=year_ago, growth=growth,
                        yahoo_ago=ago, rebased=rebased, derived=False)
        prev = ("ok", idx)
    # a year without its own row whose consensus Yahoo still shows as the base of the year after it
    # (e.g. the 0y row is stale while +1y already is the year after next)
    if kind == "year":
        for idx, r in sorted(out.items()):
            j = idx - 1
            if not r["derived"] and not r["rebased"] and r["yahoo_ago"] and j >= anchor and j not in out and j not in rep:
                b = next(iter(base(j - 1)), None)
                out[j] = dict(period=None, idx=j, avg=r["yahoo_ago"], low=None, high=None, analysts=None,
                              year_ago=b, growth=r["yahoo_ago"] / b - 1 if b and b > 0 else None,
                              yahoo_ago=None, rebased=False, derived=True)
    return out, dropped


def resolve(snap, fy_rep, q_rep, fye, snap_date):
    """Map one snapshot's rows to real fiscal periods, per section (EPS and revenue separately).
    -> dict(anchor={'year': fy, 'quarter': idx}, rows={kind: {key: {idx: row}}}, dropped=[...], rep=...)"""
    last_fy = max(fy_rep) if fy_rep else None
    last_q = max(qidx(k) for k in q_rep) if q_rep else None
    q0 = _guess_q0(snap, q_rep, fye)
    qa = qidx(q0) if q0 else None
    if last_q is not None:
        qa = max(qa if qa is not None else last_q + 1, last_q + 1)
    ya = last_fy + 1 if last_fy else _fallback_fy(fye, dt.date.fromisoformat(snap_date))
    if qa is not None and qkey(qa)[0] == ya + 1:  # Yahoo already in the next year before its 10-K is in
        ya += 1
    rep = {"year": {k: {y: d[k] for y, d in fy_rep.items() if d.get(k) is not None} for k, _, _ in SECTIONS},
           "quarter": {k: {qidx(q): d[k] for q, d in q_rep.items() if d.get(k) is not None} for k, _, _ in SECTIONS}}
    rows, dropped = {"year": {}, "quarter": {}}, []
    for kind, anchor in (("year", ya), ("quarter", qa)):
        for key, section, ago_field in SECTIONS:
            if anchor is None:
                rows[kind][key] = {}
                continue
            rows[kind][key], d = _map_section(snap.get(section, {}), ago_field, kind, anchor, rep[kind][key])
            dropped += [dict(x, key=key) for x in d]
    return dict(anchor={"year": ya, "quarter": qa}, rows=rows, dropped=dropped, rep=rep)


def plabel(kind, idx):
    return f"FY{idx}" if kind == "year" else qlabel(*qkey(idx))


def period_of(res, key, kind, yahoo_period):
    """Real period idx of a Yahoo period in one section, None when that row is stale / missing."""
    return next((i for i, r in res["rows"][kind][key].items() if r["period"] == yahoo_period), None)


def _rel(kind, idx, anchor):
    n = idx - anchor
    if kind == "quarter":
        return "this quarter" if n == 0 else "next quarter" if n == 1 else f"in {n} quarters"
    return "this year" if n == 0 else "next year" if n == 1 else f"in {n} years"


# --------------------------------------------------------------------------- snapshot metrics
def _next_fy(res):
    """First fiscal year with an estimate (EPS or revenue)."""
    ys = [i for k in ("eps", "revenue") for i, r in res["rows"]["year"][k].items() if not r["derived"]]
    return min(ys) if ys else None


def mismatch(res, fy_rep, key):
    """Analysts' base year differs from the reported number (another revenue / EPS definition,
    e.g. operating earnings for Berkshire): -> {fy, analysts, reported}, else None."""
    rows = res["rows"]["year"][key]
    for idx in sorted(rows):
        r = rows[idx]
        if r["derived"] or r["rebased"]:
            continue
        rep = fy_rep.get(idx - 1, {}).get(key)
        if r["yahoo_ago"] and rep and abs(r["yahoo_ago"] / rep - 1) > 0.03:
            return dict(fy=f"FY{idx - 1}", analysts=r["yahoo_ago"], reported=rep)
        return None
    return None


# --------------------------------------------------------------------------- adjusted EPS base
# Reported EPS comes from the SEC (GAAP); analysts estimate adjusted (non-GAAP) EPS. Growth from the
# last reported year to the first estimate year must compare like with like, so that year's adjusted
# EPS is looked up (never invented) and only used when it passes the sanity checks in _check.
SURPRISE_DAYS = 20  # Yahoo dates a quarter by calendar month end; the fiscal quarter can end a few days earlier


def _splits(ticker):
    return [(r["date"], r["ratio"]) for r in _q("SELECT date, ratio FROM splits WHERE ticker=? AND ratio>0", (ticker,))]


def _surprise_sums(ticker, q_rep):
    """{fy: sum of Yahoo's actual (adjusted) EPS of the 4 quarters} for fiscal years whose 4 quarter
    ends (from the SEC financials) each match exactly one earnings-history row."""
    rows = [(dt.date.fromisoformat(r["quarter_end"][:10]), r["eps_actual"]) for r in
            _q("SELECT quarter_end, eps_actual FROM earnings_surprises WHERE ticker=? AND eps_actual IS NOT NULL", (ticker,))]
    out = {}
    if not rows:
        return out
    for fy in sorted({k[0] for k in q_rep}):
        ends = [q_rep.get((fy, q), {}).get("end") for q in (1, 2, 3, 4)]
        if None in ends:
            continue
        hit = []
        for e in ends:
            e = dt.date.fromisoformat(e)
            near = [v for qe, v in rows if abs((qe - e).days) <= SURPRISE_DAYS]
            if len(near) != 1:
                break
            hit.append(near[0])
        if len(hit) == 4:
            out[fy] = round(sum(hit), 4)
    return out


def _until(table, date):
    return {k: d for k, d in table.items() if (d.get("filed") or "") <= date}


def _yearago_candidates(snaps, fy_rep, q_rep, fye):
    """{fy: (value, snapshot date)}: Yahoo's year-ago EPS of the first estimate year, from every
    snapshot in which that year-ago really is the last reported fiscal year (as known on that day:
    only filings public by then count). Later snapshots win."""
    out = {}
    for date, snap in sorted(snaps.items()):
        fr, qr = _until(fy_rep, date), _until(q_rep, date)
        if not fr:
            continue
        res = resolve(snap, fr, qr, fye, date)
        rows = {i: r for i, r in res["rows"]["year"]["eps"].items() if not r["derived"]}
        if not rows:
            continue
        first = min(rows)
        r = rows[first]
        if r["rebased"] or r["yahoo_ago"] is None or first - 1 != max(fr):
            continue
        out[first - 1] = (r["yahoo_ago"], date)
    return out


def _check(v, gaap, end, splits):
    """Reason to reject adjusted EPS `v` next to GAAP EPS `gaap` of a year ending `end`, else None."""
    if not gaap:
        return None
    ratio = v / gaap
    prod = 1.0
    for d, f in sorted(x for x in splits if x[0] > end):
        prod *= f
        for x in {f, prod}:
            if x != 1 and (abs(ratio / x - 1) < 0.05 or abs(ratio * x - 1) < 0.05):
                return f"{v:.2f} vs GAAP {gaap:.2f} looks like the {f:g}:1 stock split of {d} is not applied"
    near_zero = gaap <= 0 or abs(gaap) < 0.1 * abs(v)  # loss or break-even years legitimately differ a lot
    if not near_zero and not 0.5 <= ratio <= 2:
        return f"{v:.2f} is {ratio:.2f}x GAAP {gaap:.2f} (outside 0.5x-2x)"
    return None


def adjusted_eps(ticker, snaps, fy_rep, q_rep, fye):
    """{fy: dict(value, gaap, source, reason, quarters_sum)} per reported fiscal year with an adjusted-EPS
    candidate. value is None when the candidates were rejected (reason says why)."""
    cands = _yearago_candidates(snaps, fy_rep, q_rep, fye)
    sums = {fy: v for fy, v in _surprise_sums(ticker, q_rep).items() if fy in fy_rep}
    splits = _splits(ticker)
    out = {}
    for fy in sorted(set(cands) | set(sums)):
        gaap, end = fy_rep[fy].get("eps"), fy_rep[fy]["end"]
        ya, su = cands.get(fy), sums.get(fy)
        a = out[fy] = dict(fy=fy, value=None, gaap=gaap, source=None, reason=None, quarters_sum=su)
        if ya:
            bad = _check(ya[0], gaap, end, splits)
            if not bad and su is not None and not _close(ya[0], su, 0.05):
                # cannot tell which one is wrong
                a["reason"] = f"Yahoo's year-ago EPS {ya[0]:.2f} disagrees with the sum of its 4 quarterly actuals {su:.2f}"
                continue
            if not bad:
                a.update(value=ya[0], source=f"Yahoo year-ago EPS, snapshot {ya[1]}"
                         + (f" (4 quarterly actuals add up to {su:.2f})" if su is not None else ""))
                continue
            a["reason"] = f"Yahoo's year-ago EPS rejected: {bad}"
        if su is not None:
            bad = _check(su, gaap, end, splits)
            if bad:
                a["reason"] = (a["reason"] + "; " if a["reason"] else "") + f"sum of quarterly actuals rejected: {bad}"
            else:
                a.update(value=su, source="sum of the 4 quarterly actual EPS (Yahoo earnings history)", reason=None)
    return out


def eps_base(res, fy_rep, adj):
    """Base of the growth to the first estimate year: adjusted EPS of the year before it when known
    and sane, else GAAP. -> dict(fy, value, gaap, adjusted, kind 'adjusted'|'gaap', source, reason)."""
    fy = _next_fy(res)
    if fy is None or fy - 1 not in fy_rep:
        return None
    gaap = fy_rep[fy - 1].get("eps")
    a = adj.get(fy - 1) or {}
    if a.get("value") is not None:
        return dict(fy=fy - 1, value=a["value"], gaap=gaap, adjusted=a["value"], kind="adjusted", source=a["source"], reason=None)
    reason = a.get("reason") or ("Yahoo's year-ago EPS does not refer to that year and its quarterly actuals "
                                 "do not cover all 4 quarters")
    return dict(fy=fy - 1, value=gaap, gaap=gaap, adjusted=None, kind="gaap", source="SEC (GAAP)", reason=reason)


def _est_growth(res, fy_rep, key, fy, adj=None):
    """Estimated growth of fiscal year `fy` vs the year before it: the reported year (for EPS its
    adjusted EPS when known, see eps_base) or else the estimate before it. When analysts use another
    revenue definition, compare with their own base instead."""
    rows = res["rows"]["year"][key]
    r = rows.get(fy)
    if not r or r["avg"] is None:
        return None
    prev_est = rows.get(fy - 1, {}).get("avg")
    own = r["yahoo_ago"] if not r["rebased"] else None
    if key == "eps":
        a = (adj or {}).get(fy - 1) or {}
        base = a["value"] if a.get("value") is not None else fy_rep.get(fy - 1, {}).get(key)
        base = base or prev_est or own
    elif mismatch(res, fy_rep, key):
        base = prev_est or own
    else:
        base = fy_rep.get(fy - 1, {}).get(key) or prev_est or own
    return r["avg"] / base - 1 if base and base > 0 else None


def brief_from(snap, res, fy_rep, price=None, adj=None):
    s, pt = snap.get("summary", {}), snap.get("price_target", {})
    cur = price or _num(pt.get("current"))
    mean = _num(pt.get("mean")) or _num(s.get("targetMeanPrice"))
    fy = _next_fy(res)
    fpe = _num(s.get("forwardPE"))
    if fpe is None and cur and _num(s.get("forwardEps")):
        fpe = cur / s["forwardEps"] if s["forwardEps"] > 0 else None
    return dict(
        forward_pe=fpe, forward_eps=_num(s.get("forwardEps")),
        peg=_num(s.get("trailingPegRatio")) or _num(s.get("pegRatio")),
        target_mean=mean, target_upside=(mean / cur - 1) if mean and cur else None,
        rating_mean=_num(s.get("recommendationMean")), rating_key=s.get("recommendationKey"),
        analysts=_num(s.get("numberOfAnalystOpinions")),
        next_fy=f"FY{fy}" if fy else None,
        rev_growth_next=_est_growth(res, fy_rep, "revenue", fy) if fy else None,
        eps_growth_next=_est_growth(res, fy_rep, "eps", fy, adj) if fy else None,
        **_calendar(snap))


def _calendar(snap):
    c = snap.get("calendar", {})
    d = c.get("Earnings Date")
    d = d.split(",")[0].strip() if isinstance(d, str) else None
    return dict(earnings_date=d, earnings_upcoming=bool(d and d >= dt.date.today().isoformat()),
                earnings_eps=_num(c.get("Earnings Average")), earnings_revenue=_num(c.get("Revenue Average")))


def brief(ticker, company, price=None):
    """Small summary for the overview / compare table, or None without coverage."""
    snaps = snapshots(ticker)
    if not snaps:
        return None
    date = max(snaps)
    fy_rep, q_rep = reported(company.get("cik") if company else None)
    fye = (company or {}).get("fiscal_year_end")
    res = resolve(snaps[date], fy_rep, q_rep, fye, date)
    adj = adjusted_eps(ticker.upper(), snaps, fy_rep, q_rep, fye)
    return {**brief_from(snaps[date], res, fy_rep, price, adj), "snapshot_date": date}


def snapshot_series(ticker, company, metric):
    """[[date, value, label]] of one brief metric over all snapshots (for the compare chart)."""
    snaps = snapshots(ticker)
    fy_rep, q_rep = reported(company.get("cik") if company else None)
    fye = (company or {}).get("fiscal_year_end")
    adj = adjusted_eps(ticker.upper(), snaps, fy_rep, q_rep, fye) if "eps" in metric else None
    out = []
    for date, snap in snaps.items():
        res = resolve(snap, fy_rep, q_rep, fye, date)
        b = brief_from(snap, res, fy_rep, adj=adj)
        if b.get(metric) is not None:
            out.append([date, b[metric], f"snapshot {date}" + (f" · {b['next_fy']}" if "growth" in metric else "")])
    return out


def _trend_period(res, snap, kind, p):
    """Real period of an eps_trend / eps_revisions row. The trend's "current" normally equals the
    EPS estimate of the same Yahoo period; when it does not, it can be a stale row of its own
    (e.g. the pre-report consensus of the year just reported) -> None."""
    idx = period_of(res, "eps", kind, p)
    cur = _num(snap.get("eps_trend", {}).get(p, {}).get("current"))
    rows = res["rows"][kind]["eps"]
    if cur is None or (idx is not None and _close(cur, rows[idx]["avg"], 0.005)):
        return idx
    hit = next((i for i, r in rows.items() if not r["derived"] and _close(cur, r["avg"], 0.005)), None)
    if hit is not None:
        return hit
    just = res["anchor"][kind] - 1 if res["anchor"][kind] is not None else None
    if just is not None and _close(cur, res["rep"][kind]["eps"].get(just), 0.03):
        return None
    return idx


# --------------------------------------------------------------------------- full page
def full(ticker, company, price=None):
    t = ticker.upper()
    snaps = snapshots(t)
    if not snaps:
        return dict(has_coverage=False)
    date = max(snaps)
    snap = snaps[date]
    cik = (company or {}).get("cik")
    fye = (company or {}).get("fiscal_year_end")
    fy_rep, q_rep = reported(cik)
    res = resolve(snap, fy_rep, q_rep, fye, date)
    adj = adjusted_eps(t, snaps, fy_rep, q_rep, fye)
    base = eps_base(res, fy_rep, adj)
    # the EPS row right after the last reported year: year-ago and growth on that base (see eps_base)
    for idx, row in res["rows"]["year"]["eps"].items():
        if base and idx - 1 == base["fy"]:
            row.update(year_ago=base["value"], growth=_est_growth(res, fy_rep, "eps", idx, adj))
    s, pt = snap.get("summary", {}), snap.get("price_target", {})
    cur = price or _num(pt.get("current"))

    # price targets
    targets = {k: _num(pt.get(k)) or _num(s.get(f"target{k.title()}Price")) for k in ("low", "mean", "median", "high")}
    target = dict(**targets, current=cur, analysts=_num(s.get("numberOfAnalystOpinions")),
                  upside={k: (v / cur - 1) if v and cur else None for k, v in targets.items()})

    # ratings per month (0m = month of the snapshot)
    sd = dt.date.fromisoformat(date)
    recs = []
    for p, r in sorted(snap.get("recommendations", {}).items(), key=lambda kv: int(kv[0].rstrip("m") or 0)):
        n = -int(p.rstrip("m") or 0)
        y, m = divmod(sd.year * 12 + sd.month - 1 - n, 12)
        recs.append(dict(period=p, label=dt.date(y, m + 1, 1).strftime("%b %Y"), **{k: _num(r.get(k)) or 0 for k in REC}))

    # estimates per real fiscal period: the EPS and revenue of one line are always the same period
    keep = ("avg", "low", "high", "analysts", "year_ago", "growth", "rebased", "derived")
    est = []
    for kind in ("quarter", "year"):
        rows = res["rows"][kind]
        for idx in sorted(set(rows["eps"]) | set(rows["revenue"])):
            e, r = rows["eps"].get(idx), rows["revenue"].get(idx)
            fy, fq = (idx, None) if kind == "year" else qkey(idx)
            est.append(dict(period=(e or {}).get("period") or (r or {}).get("period"), label=plabel(kind, idx), kind=kind,
                            rel=_rel(kind, idx, res["anchor"][kind]), fy=fy, fq=fq,
                            eps={k: e[k] for k in keep} if e else None, revenue={k: r[k] for k in keep} if r else None))
    dropped = [dict(key=d["key"], period=d["period"], label=plabel(d["kind"], d["idx"]), avg=d["avg"]) for d in res["dropped"]]

    def mapped(section):
        out = []
        for p, d in snap.get(section, {}).items():
            if p not in YEARS + QUARTERS:
                continue
            kind = "year" if p in YEARS else "quarter"
            idx = _trend_period(res, snap, kind, p)
            if idx is not None:
                out.append((kind, idx, p, d))
        return sorted(out, key=lambda x: (x[0] == "year", x[1]))

    trend = [dict(period=p, label=plabel(k, i), **{n: _num(d.get(f)) for f, n in TREND}) for k, i, p, d in mapped("eps_trend")]
    revisions = [dict(period=p, label=plabel(k, i), up7=_num(d.get("upLast7days")), up30=_num(d.get("upLast30days")),
                      down7=_num(d.get("downLast7Days")), down30=_num(d.get("downLast30days")))
                 for k, i, p, d in mapped("eps_revisions")]
    # expected growth vs the index; Yahoo's stock growth is measured from its own (possibly lagged) base
    growth = []
    for p, d in snap.get("growth", {}).items():
        if p == "LTG":
            growth.append(dict(period=p, label="Next 5 years (per year)", stock=_num(d.get("stockTrend")),
                               index=_num(d.get("indexTrend")), order=(2, 0)))
            continue
        if p not in YEARS + QUARTERS:
            continue
        kind = "year" if p in YEARS else "quarter"
        idx = period_of(res, "eps", kind, p)
        if idx is None:
            continue
        row = res["rows"][kind]["eps"][idx]
        growth.append(dict(period=p, label=plabel(kind, idx), stock=row["growth"] if row["rebased"] else _num(d.get("stockTrend")),
                           index=_num(d.get("indexTrend")), order=(int(kind == "year"), idx)))
    growth.sort(key=lambda g: g["order"])
    for g in growth:
        del g["order"]

    # annual chart: last 6 reported fiscal years + estimated years not yet reported
    annual = [dict(fy=y, label=f"FY{y}", est=False, revenue=d.get("revenue"), eps=d.get("eps"),
                   eps_adjusted=(adj.get(y) or {}).get("value"))
              for y, d in sorted(fy_rep.items())[-6:]]
    yr = res["rows"]["year"]
    for fy in sorted(set(yr["eps"]) | set(yr["revenue"])):
        if fy in fy_rep:
            continue
        e, r = yr["eps"].get(fy) or {}, yr["revenue"].get(fy) or {}
        annual.append(dict(fy=fy, label=f"FY{fy}", est=True, revenue=r.get("avg"), eps=e.get("avg"),
                           revenue_low=r.get("low"), revenue_high=r.get("high"), eps_low=e.get("low"), eps_high=e.get("high"),
                           analysts=r.get("analysts") or e.get("analysts"),
                           revenue_derived=bool(r.get("derived")), eps_derived=bool(e.get("derived"))))
    for k in ("revenue", "eps"):
        for i, a in enumerate(annual):
            if a["est"]:
                a[f"{k}_growth"] = _est_growth(res, fy_rep, k, a["fy"], adj)
                continue
            prev = annual[i - 1].get(k) if i else None
            a[f"{k}_growth"] = a[k] / prev - 1 if a.get(k) is not None and prev and prev > 0 else None
    basis = {"revenue": mismatch(res, fy_rep, "revenue")}  # EPS: see eps_base

    # earnings surprises, labelled with the fiscal quarter that ended closest to Yahoo's calendar date
    surprises = []
    for r in _q("SELECT quarter_end, eps_estimate, eps_actual, surprise_pct FROM earnings_surprises "
                "WHERE ticker=? ORDER BY quarter_end DESC LIMIT 12", (t,)):
        qe = dt.date.fromisoformat(r["quarter_end"][:10])
        near = [(abs((dt.date.fromisoformat(d["end"]) - qe).days), k) for k, d in q_rep.items()]
        near = min(near) if near else None
        surprises.append(dict(quarter_end=r["quarter_end"][:10], label=qlabel(*near[1]) if near and near[0] <= 45 else r["quarter_end"][:7],
                              estimate=r["eps_estimate"], actual=r["eps_actual"], surprise=r["surprise_pct"]))

    actions = _q("SELECT date, firm, to_grade, from_grade, action, pt_action, pt_current, pt_prior FROM analyst_actions "
                 "WHERE ticker=? ORDER BY date DESC, firm LIMIT 25", (t,))

    # consensus over time per real fiscal period (one point per snapshot)
    history = {}
    for d, sn in snaps.items():
        r2 = resolve(sn, fy_rep, q_rep, fye, d)
        pts = {}
        for kind in ("quarter", "year"):
            for key in ("eps", "revenue"):
                for idx, row in r2["rows"][kind][key].items():
                    if not row["derived"]:
                        pts.setdefault(plabel(kind, idx), dict(date=d, eps=None, revenue=None))[key] = row["avg"]
        for lab, point in pts.items():
            history.setdefault(lab, []).append(point)

    return dict(has_coverage=True, snapshot_date=date, snapshots=len(snaps), price=cur, target=target,
                rating=dict(mean=_num(s.get("recommendationMean")), key=s.get("recommendationKey"),
                            text=s.get("averageAnalystRating"), analysts=_num(s.get("numberOfAnalystOpinions"))),
                recommendations=recs, estimates=est, dropped=dropped, eps_trend=trend, revisions=revisions, growth=growth,
                calendar=_calendar(snap), summary=brief_from(snap, res, fy_rep, price, adj), annual=annual, basis=basis, eps_base=base,
                surprises=surprises, actions=actions, history=history)
