"""Accuracy check: our main annual numbers next to Yahoo's, kept per company in the `accuracy_check` table.

The quality score (quality.py) only says whether a company's numbers agree with each other; this says whether
they agree with an outside source. Yahoo shows the last 4 fiscal years. A number "matches" within 1%.

Runs for a company when it gets a new filing (daily update), and for everyone in the data-quality baseline
(baseline.py stores its results here too). Shown in the Data quality card on the stock page, as a Screener
column ("Matches Yahoo") and as a rule on the Data health page.

Yahoo is not always right: it adjusts some numbers (operating income for one-offs) and defines some lines
differently (debt, capex). So a difference is shown as something to look at, not as an error.

    python -m stockterminal.accuracy AAPL MSFT     # check these now
    python -m stockterminal.accuracy --import      # load data/reports/quality_baseline_yahoo.csv
"""
import sys
from datetime import datetime

import pandas as pd

from . import db

ITEMS = ["revenue", "gross_profit", "operating_income", "net_income", "eps_diluted", "operating_cf", "capex",
         "fcf", "total_assets", "total_equity", "shares_diluted", "cash", "borrowings"]
LABELS = {"revenue": "Revenue", "gross_profit": "Gross profit", "operating_income": "Operating income",
          "net_income": "Net income", "eps_diluted": "EPS (diluted)", "operating_cf": "Operating cash flow",
          "capex": "Capex", "fcf": "Free cash flow", "total_assets": "Total assets", "total_equity": "Equity",
          "shares_diluted": "Diluted shares", "cash": "Cash", "borrowings": "Debt (excl. leases)"}
TOL = 1.0


def store(conn, df):
    """Save compare rows (ticker, item, period_end, ours, yahoo, diff_%) and replace each ticker's old rows."""
    df = df[df.item.isin(ITEMS) & df.yahoo.notna()].rename(columns={"diff_%": "diff"})
    now = datetime.now().isoformat(timespec="seconds")
    for t in df.ticker.unique():
        conn.execute("DELETE FROM accuracy_check WHERE ticker=?", (t,))
    conn.executemany(
        "INSERT OR REPLACE INTO accuracy_check (ticker, item, period_end, ours, yahoo, diff, checked_at) "
        "VALUES (?,?,?,?,?,?,?)",
        [(r.ticker, r.item, r.period_end, None if pd.isna(r.ours) else float(r.ours), float(r.yahoo),
          None if pd.isna(r.diff) else float(r.diff), now) for r in df.itertuples()])
    conn.commit()


def refresh_ours(conn, ticker, cik):
    """After a recalculation: put our new numbers next to the Yahoo numbers already stored (no download)."""
    rows = conn.execute("SELECT item, period_end, yahoo FROM accuracy_check WHERE ticker=?", (ticker,)).fetchall()
    if not rows:
        return 0
    fy = pd.read_sql_query("SELECT item, end_date, value FROM financials WHERE cik=? AND period_type='FY'",
                           conn, params=(cik,))
    fy["end"] = pd.to_datetime(fy.end_date)
    upd = []
    for item, end, yahoo in rows:
        m = fy[(fy.item == item) & ((fy.end - pd.Timestamp(end)).abs() <= pd.Timedelta(days=7))]
        ours = float(m.value.iloc[0]) if len(m) and pd.notna(m.value.iloc[0]) else None
        diff = (ours - yahoo) / abs(yahoo) * 100 if ours is not None and yahoo else None
        upd.append((ours, diff, ticker, item, end))
    conn.executemany("UPDATE accuracy_check SET ours=?, diff=? WHERE ticker=? AND item=? AND period_end=?", upd)
    conn.commit()
    return len(upd)


def check(conn, tickers):
    """Compare with Yahoo now (a few seconds per company) and store. Returns the compare rows."""
    from . import spotcheck
    df = spotcheck.compare(conn, list(tickers), ITEMS)
    if len(df):
        store(conn, df[["ticker", "item", "period_end", "ours", "yahoo", "diff_%"]])
    return df


def summary(rows):
    """Rows of one ticker (dicts from the table) -> what the stock page shows:
    {checked_at, compared, match, missing, off: [{item, label, years, worst, periods}]}."""
    if not rows:
        return None
    comp = [r for r in rows if r["ours"] is not None and r["diff"] is not None]
    ok = [r for r in comp if abs(r["diff"]) <= TOL]
    off = {}
    for r in rows:
        if r["ours"] is not None and r["diff"] is not None and abs(r["diff"]) <= TOL:
            continue
        o = off.setdefault(r["item"], dict(item=r["item"], label=LABELS.get(r["item"], r["item"]), periods=[],
                                           worst=None, missing=0))
        if r["ours"] is None or r["diff"] is None:
            o["missing"] += 1
            o["periods"].append(dict(end=r["period_end"], ours=None, yahoo=r["yahoo"], diff=None))
        else:
            o["periods"].append(dict(end=r["period_end"], ours=r["ours"], yahoo=r["yahoo"], diff=r["diff"]))
            if o["worst"] is None or abs(r["diff"]) > abs(o["worst"]):
                o["worst"] = r["diff"]
    for o in off.values():
        o["periods"].sort(key=lambda p: p["end"])
    order = {k: i for i, k in enumerate(ITEMS)}
    return dict(checked_at=max(r["checked_at"] for r in rows), compared=len(comp), match=len(ok),
                missing=sum(1 for r in rows if r["ours"] is None),
                off=sorted(off.values(), key=lambda o: order.get(o["item"], 99)))


def match_share(conn):
    """ticker -> share of compared numbers within 1% of Yahoo (for the Screener / Data health)."""
    out = {}
    for t, n, m in conn.execute("SELECT ticker, COUNT(*), SUM(CASE WHEN ABS(diff) <= ? THEN 1 ELSE 0 END) "
                                "FROM accuracy_check WHERE ours IS NOT NULL AND diff IS NOT NULL GROUP BY ticker",
                                (TOL,)):
        if n:
            out[t] = (m or 0) / n
    return out


def main(argv):
    conn = db.connect()
    if "--import" in argv:
        from .baseline import YAHOO_CSV
        df = pd.read_csv(YAHOO_CSV)
        store(conn, df[["ticker", "item", "period_end", "ours", "yahoo", "diff_%"]])
        print(f"Imported {df.ticker.nunique()} companies from {YAHOO_CSV}")
        return
    df = check(conn, [t.upper() for t in argv])
    print(f"{int(df.ok.sum())} of {int(df['diff_%'].notna().sum())} numbers within {TOL}% of Yahoo")


if __name__ == "__main__":
    main(sys.argv[1:])
