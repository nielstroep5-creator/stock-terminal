"""The Earnings page: when the companies you watch (and hold) report next, what analysts expect, and how their
last results compared with the estimate (the surprise) and how the share price reacted.

Built on what the app already gathers, so it costs no extra data:
  - events.fetch (Yahoo calendar + reported results, about 2 years), kept CACHE_HOURS in pf_events like the
    Portfolio calendar and the Home "Coming up" strip;
  - the time of day (before the open / after the close) from the stored Yahoo summary (analyst_estimates);
  - revenue against the estimate from the earnings watch (earnings_watch.py), for results it saw coming in;
  - the share price reaction from the stored daily prices (prices, else pf_prices).

ETFs report no results and are left out.
"""
import datetime as dt
import json
import time

from . import events

AHEAD_DAYS = 120         # expected dates (from the company's rhythm) this far ahead, so every company has a next one
RESULTS = 8              # reported results per company (two years)
NEW_DAYS = 10            # a result the earnings watch saw this recently counts even before Yahoo has it


def _surprise(est, act):
    if est is None or act is None:
        return None
    return (act - est) / abs(est) if est else None


def _stored(conn, syms):
    """{symbol: (data, fresh)} from pf_events."""
    out = {}
    for s in set(syms):
        row = conn.execute("SELECT data, fetched FROM pf_events WHERE symbol=?", (s,)).fetchone()
        if row:
            d = json.loads(row[0])
            out[s] = (d, d.get("version") == events.VERSION and time.time() - row[1] < events.CACHE_HOURS * 3600)
    return out


def load(conn, syms, refresh):
    """events.fetch data per Yahoo symbol: stored, and with refresh=True fetched again when older than
    CACHE_HOURS (six at a time). Offline or unknown symbols keep what is stored."""
    have = _stored(conn, syms)
    if refresh:
        from concurrent.futures import ThreadPoolExecutor
        need = [s for s in dict.fromkeys(syms) if s not in have or not have[s][1]]

        def get(s):
            try:
                return s, events.fetch(s)
            except Exception:  # noqa: BLE001
                return s, None
        with ThreadPoolExecutor(6) as ex:
            got = [x for x in ex.map(get, need) if x[1]]
        if got:
            conn.executemany("INSERT OR REPLACE INTO pf_events VALUES (?,?,?)",
                             [(s, json.dumps(d), time.time()) for s, d in got])
            conn.commit()
            have.update({s: (d, True) for s, d in got})
    return {s: d for s, (d, _) in have.items()}


def _times(conn, tickers):
    """{ticker: (New York date, 'before' | 'after' | 'during')} from the newest stored Yahoo summary, only when
    Yahoo says the date is not an estimate and gives a real time."""
    from .earnings_watch import NY
    out = {}
    if not tickers:
        return out
    marks = ",".join("?" * len(tickers))
    try:
        rows = conn.execute(f"""SELECT ticker, snapshot_date, field, value, text FROM analyst_estimates
                                WHERE section='summary' AND field IN ('earningsTimestamp','isEarningsDateEstimate')
                                AND ticker IN ({marks}) ORDER BY snapshot_date""", list(tickers)).fetchall()
    except Exception:  # noqa: BLE001  no analyst table yet
        return out
    snap = {}
    for t, d, field, v, txt in rows:
        cur = snap.get(t)
        if not cur or cur[0] != d:
            snap[t] = cur = (d, {})
        cur[1][field] = v if v is not None else txt
    for t, (_, f) in snap.items():
        ts, est = f.get("earningsTimestamp"), f.get("isEarningsDateEstimate")
        if not isinstance(ts, (int, float)) or est in (1, 1.0, True, "True", "true"):
            continue
        at = dt.datetime.fromtimestamp(ts, NY)
        if at.hour == 0 and at.minute == 0:                         # a date without a time
            continue
        minutes = at.hour * 60 + at.minute
        out[t] = (at.date().isoformat(), "before" if minutes < 9 * 60 + 30 else "after" if minutes >= 16 * 60 else "during")
    return out


def _watched(conn, tickers, since):
    """{ticker: [summary numbers per report]} from the earnings watch: revenue and EPS against the estimate."""
    from . import earnings_watch
    out = {}
    want = set(tickers)
    try:
        rows = earnings_watch.rows(conn, since)
    except Exception:  # noqa: BLE001  no table yet
        return out
    for r in rows:
        if r["ticker"] not in want:
            continue
        s = earnings_watch.summary(r)
        if s:
            out.setdefault(r["ticker"], []).append(dict(s[3], date=r["date"], press_url=r.get("press_url")))
    return out


def _reaction(conn, ticker, symbol, day):
    """Share price from the last close before the results day to the first close after it."""
    for table, col, key in (("prices", "ticker", ticker), ("pf_prices", "symbol", symbol)):
        try:
            before = conn.execute(f"SELECT close FROM {table} WHERE {col}=? AND date < ? AND close IS NOT NULL ORDER BY date DESC LIMIT 1",
                                  (key, day)).fetchone()
            after = conn.execute(f"SELECT close FROM {table} WHERE {col}=? AND date > ? AND close IS NOT NULL ORDER BY date LIMIT 1",
                                 (key, day)).fetchone()
        except Exception:  # noqa: BLE001
            continue
        if before and after and before[0]:
            return after[0] / before[0] - 1
    return None


def company(conn, ticker, symbol, e, today, times, watched):
    """One company's next results and its last RESULTS reported results (newest first)."""
    tday = today.isoformat()
    day = events._day
    results = []
    for r in e.get("results") or []:
        d = r.get("date")
        if d and d <= tday and r.get("eps_act") is not None:
            results.append(dict(date=d, eps_est=r.get("eps_est"), eps_act=r["eps_act"],
                                surprise=_surprise(r.get("eps_est"), r["eps_act"]), quarter_end=bool(r.get("quarter_end"))))
    for w in watched.get(ticker, []):                       # revenue from the earnings watch, or a result Yahoo lacks yet
        wd = day(w["date"])
        match = next((r for r in results if not r["quarter_end"] and abs((day(r["date"]) - wd).days) <= 3), None)
        extra = dict(revenue=w.get("revenue"), revenue_est=w.get("revenue_est"), revenue_pct=w.get("revenue_pct"),
                     press_url=w.get("press_url"), official=w.get("official"))
        if match:
            match.update({k: v for k, v in extra.items() if v is not None})
        elif w["date"] <= tday and (today - wd).days <= NEW_DAYS and w.get("eps") is not None:
            results.append(dict(date=w["date"], eps_est=w.get("eps_est"), eps_act=w.get("eps"),
                                surprise=_surprise(w.get("eps_est"), w.get("eps")), quarter_end=False, preliminary=True, **extra))
    results.sort(key=lambda r: r["date"], reverse=True)
    results = results[:RESULTS]
    for r in results:
        if not r["quarter_end"]:
            r["reaction"] = _reaction(conn, ticker, symbol, r["date"])
    reported = [day(r["date"]) for r in results if not r["quarter_end"]]

    def gone(d):                                            # a date that is already out (reported today, say)
        return any(abs((d - k).days) <= events.NEAR_DAYS for k in reported if k and k >= today - dt.timedelta(days=3))

    nxt = None
    dates = [x for x in e.get("earnings") or [] if x >= tday]
    if dates and not gone(day(dates[0])):
        nxt = dict(date=dates[0], date_to=dates[-1] if len(dates) > 1 else None, confirmed=len(dates) == 1, expected=False)
    if not nxt:                                             # a date Yahoo lists in its results table, not its calendar
        fut = sorted(r["date"] for r in e.get("results") or [] if r.get("date") and r["date"] >= tday
                     and r.get("eps_act") is None and not r.get("quarter_end"))
        if fut and not gone(day(fut[0])):
            nxt = dict(date=fut[0], date_to=None, confirmed=False, expected=False)
    if not nxt:
        known = [d for d in (day(x) for x in e.get("earnings") or []) if d] + [k for k in reported if k]
        exp = events.expected_earnings(e, today, today + dt.timedelta(days=AHEAD_DAYS), known)
        if exp:
            nxt = dict(date=exp[0].isoformat(), date_to=None, confirmed=False, expected=True)
    if nxt:
        fresh = not nxt["expected"]                          # Yahoo's estimates belong to its own next date
        nxt.update(eps_avg=e.get("eps_avg") if fresh else None, eps_low=e.get("eps_low") if fresh else None,
                   eps_high=e.get("eps_high") if fresh else None, revenue_avg=e.get("revenue_avg") if fresh else None,
                   analysts=e.get("analysts_q") if fresh else None, growth=e.get("growth_q") if fresh else None,
                   year_ago_eps=e.get("year_ago_eps_q") if fresh else None,
                   rating=events.rate_expectation(e) if fresh else None)
        tm = times.get(ticker)
        nxt["time"] = tm[1] if tm and tm[0] == nxt["date"] and nxt["confirmed"] else None
    done = [r for r in results if r["eps_est"] is not None and r["eps_act"] is not None]
    last4 = done[:4]
    return dict(next=nxt, results=results,
                beats=sum(r["eps_act"] > r["eps_est"] for r in last4), counted=len(last4),
                avg_surprise=(sum(r["surprise"] for r in last4 if r["surprise"] is not None) /
                              max(1, sum(r["surprise"] is not None for r in last4))) if last4 else None)


def build(conn, tickers, refresh=False, today=None):
    """{today, companies: {ticker: company(...)}, missing: [tickers without calendar data]} for stock tickers."""
    from .quote import yahoo_symbol
    today = today or dt.date.today()
    syms = {t: yahoo_symbol(t) for t in tickers}
    data = load(conn, list(syms.values()), refresh)
    times = _times(conn, tickers)
    watched = _watched(conn, tickers, (today - dt.timedelta(days=2 * 366)).isoformat())
    out, missing = {}, []
    for t in tickers:
        e = data.get(syms[t])
        if not e:
            missing.append(t)
            continue
        out[t] = company(conn, t, syms[t], e, today, times, watched)
    return dict(today=today.isoformat(), companies=out, missing=missing)
