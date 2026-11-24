"""Market cap checks, so a wrong market cap shows up as a warning instead of going unnoticed.

1. Yahoo comparison: our share count (SEC filings) against Yahoo's market cap / price, from the
   Yahoo snapshot the daily update saves anyway (analyst.py, section "summary"). More than 10% off
   gets a warning with the likely reason: several share classes (sources count them differently),
   an old share count (shares changed since the last filing: merger, share issue, buyback) or other.
2. Jump detector: days where the market cap is 10x lower or higher than the weeks around it, or
   zero / negative. Those are nearly always a share count filed in the wrong unit.

Runs after every recalculation (standardize.run) and at the end of the daily update. Results are in
the `mcap_check` table and shown on the stock page (next to Market cap and in the Data quality card).
Nothing here changes a number.
"""
import json
import sys

import pandas as pd

from . import db
from .config import DATA_DIR

LIMIT = 0.10          # warn when our share count differs more than this from Yahoo's
STALE_DAYS = 135      # share count older than this (period end -> today) counts as old
REPORT = DATA_DIR / "reports" / "market_cap_warnings.md"


def jumps(conn, ticker):
    """[{from, to, days}]: stretches of days with an impossible market cap."""
    v = pd.read_sql_query("SELECT date, market_cap FROM valuation_daily WHERE ticker=? AND market_cap IS NOT NULL "
                          "ORDER BY date", conn, params=(ticker,))
    if len(v) < 10:
        return []
    m = v.market_cap
    med = m.rolling(41, center=True, min_periods=5).median()
    bad = (m <= 0) | (m < 0.1 * med) | (m > 10 * med)
    out = []
    run = None
    for d, b in zip(v.date, bad):
        if b:
            run = run or {"from": d, "days": 0}
            run.update(to=d, days=run["days"] + 1)
        elif run:
            out.append(run)
            run = None
    if run:
        out.append(run)
    # a stock split the SEC numbers were not put on the new basis for: the whole stretch before the split is
    # off by the split ratio (an 8-for-1 split stays under the 10x rule above)
    days = list(v.date)
    for d, r in conn.execute("SELECT date, ratio FROM splits WHERE ticker=? AND ratio > 0 AND ratio != 1", (ticker,)):
        i = next((k for k, x in enumerate(days) if x >= d), None)
        if not i:
            continue
        jump = m.iloc[i] / m.iloc[i - 1] if m.iloc[i - 1] else 0
        if jump and 0.75 < jump / r < 1.33 and not 0.75 < jump < 1.33:
            start = i - 1                       # back to where the numbers stepped onto the old basis
            while start > max(0, i - 260) and not (m.iloc[start] and 0.75 < m.iloc[start - 1] / m.iloc[start] / r < 1.33):
                start -= 1
            out.append({"from": days[start], "to": days[i - 1], "days": i - start, "split": d})
    return out


def _yahoo(conn, ticker):
    """(snapshot date, market cap, price) from the newest Yahoo snapshot, or None."""
    rows = conn.execute("SELECT snapshot_date, field, value FROM analyst_estimates WHERE ticker=? AND section='summary' "
                        "AND field IN ('marketCap','currentPrice','regularMarketPrice') AND snapshot_date="
                        "(SELECT MAX(snapshot_date) FROM analyst_estimates WHERE ticker=? AND section='summary' "
                        "AND field='marketCap')", (ticker, ticker)).fetchall()
    f = {k: v for _, k, v in rows if v}
    price = f.get("currentPrice") or f.get("regularMarketPrice")
    if not f.get("marketCap") or not price:
        return None
    return rows[0][0], f["marketCap"], price


def _share_count_end(conn, cik, date):
    """Period end of the newest share count that was public on `date`."""
    r = conn.execute("SELECT MAX(end_date) FROM financials WHERE cik=? AND item IN ('shares_outstanding','shares_diluted') "
                     "AND period_type IN ('Q','FY') AND original_filed<=?", (cik, date)).fetchone()
    return r[0] if r else None


def _several_classes(conn, cik):
    r = conn.execute("SELECT COUNT(DISTINCT tag) FROM xbrl_facts WHERE cik=? AND taxonomy='class' "
                     "AND tag LIKE 'EntityCommonStockSharesOutstanding|%'", (cik,)).fetchone()
    return bool(r and r[0] >= 2)


def check(conn, ticker):
    """Run both checks for one ticker and store the result. Returns the stored row (dict) or None."""
    t = ticker.upper()
    co = conn.execute("SELECT cik FROM companies WHERE ticker=?", (t,)).fetchone()
    last = conn.execute("SELECT date, close, shares_outstanding, market_cap, shares_source FROM valuation_daily "
                        "WHERE ticker=? ORDER BY date DESC LIMIT 1", (t,)).fetchone()
    if not co or not last:
        conn.execute("DELETE FROM mcap_check WHERE ticker=?", (t,))
        return None
    cik = co[0]
    date, _close, shares, ours, source = last
    row = dict(ticker=t, checked_at=db.now_iso(), date=date, ours=ours, yahoo=None, diff=None,
               reason=None, text=None, jumps=jumps(conn, t))
    y = _yahoo(conn, t)
    if source == "yahoo":
        end = _share_count_end(conn, cik, date)
        row.update(yahoo=y[1] if y else None, diff=0.0, reason="yahoo",
                   text=f"Share count from Yahoo: the latest SEC share count{f' (from {end})' if end else ''} "
                        f"is out of date or far off, e.g. after a merger or share issue. Back to SEC numbers "
                        f"after the next filing.")
    elif y and shares:
        _snap, ymcap, yprice = y
        diff = shares / (ymcap / yprice) - 1          # compare share counts: no price-date mismatch
        row.update(yahoo=ymcap, diff=diff)
        if abs(diff) > LIMIT:
            end = _share_count_end(conn, cik, date)
            age = (pd.Timestamp(date) - pd.Timestamp(end)).days if end else None
            pct = f"{diff * 100:+.0f}%"
            if _several_classes(conn, cik):
                row.update(reason="share_classes",
                           text=f"Share count {pct} vs Yahoo. The company has several share classes, "
                                f"which sources count differently.")
            elif age is not None and age > STALE_DAYS:
                row.update(reason="stale",
                           text=f"Share count {pct} vs Yahoo. Our share count is from {end} (the latest "
                                f"filing); the number of shares has probably changed since.")
            else:
                row.update(reason="other", text=f"Share count {pct} vs Yahoo. Check the latest filing.")
    conn.execute("INSERT OR REPLACE INTO mcap_check (ticker, checked_at, date, ours, yahoo, diff, reason, text, jumps) "
                 "VALUES (?,?,?,?,?,?,?,?,?)",
                 (t, row["checked_at"], date, ours, row["yahoo"], row["diff"], row["reason"], row["text"],
                  json.dumps(row["jumps"])))
    conn.commit()
    return row


def warning_text(row):
    """One line for a warning, or '' when the market cap looks fine."""
    parts = []
    if row.get("text"):
        parts.append(row["text"])
    j = row.get("jumps") or []
    if j:
        days = sum(x["days"] for x in j)
        parts.append(f"{days} day{'s' if days > 1 else ''} with an impossible market cap "
                     f"({', '.join(x['from'] + (' to ' + x['to'] if x['to'] != x['from'] else '') for x in j[:3])}"
                     f"{', ...' if len(j) > 3 else ''}).")
    return " ".join(parts)


def run(conn, tickers, report=False):
    """Check many tickers. report=True also writes data/reports/market_cap_warnings.md. Returns warnings."""
    warn = []
    for t in tickers:
        try:
            r = check(conn, t)
        except Exception as e:  # noqa: BLE001 - a check must never stop an update
            db.log(conn, t, "mcap_check", "error", None, str(e)[:500])
            continue
        if r and warning_text(r) and (r.get("reason") != "yahoo" or r["jumps"]):
            warn.append((t, warning_text(r)))
    if report:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"# Market cap warnings ({pd.Timestamp.today():%Y-%m-%d})", "",
                 f"{len(warn)} of {len(tickers)} companies. Shown on each stock page too.", ""]
        lines += [f"- **{t}**: {w}" for t, w in sorted(warn)]
        REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return warn


if __name__ == "__main__":
    # python -m stockterminal.mcapcheck [TICKER ...]   (no tickers: every company, writes the report)
    _conn = db.connect()
    _t = [a.upper() for a in sys.argv[1:]] or [t for (t,) in _conn.execute(
        "SELECT ticker FROM companies WHERE ticker IS NOT NULL ORDER BY ticker")]
    _w = run(_conn, _t, report=not sys.argv[1:])
    for _tk, _txt in _w:
        print(f"{_tk}: {_txt}")
    print(f"{len(_w)} of {len(_t)} companies with a market cap warning")
