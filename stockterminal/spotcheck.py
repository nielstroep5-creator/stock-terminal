"""Spot-check: compare the most-used numbers of random loaded companies with Yahoo Finance.

    python -m stockterminal.spotcheck            # 30 random S&P 500 companies
    python -m stockterminal.spotcheck 50 --seed 7
    python -m stockterminal.spotcheck AAPL MSFT  # these tickers

Yahoo shows the last 4 fiscal years. A number "matches" when it is within 1% of Yahoo.
Writes data/reports/spotcheck.md (summary per item + every mismatch, largest first).
"""
import random
import sys
from datetime import date

import pandas as pd

from . import db, validate
from .config import DATA_DIR

REPORT = DATA_DIR / "reports" / "spotcheck.md"
ITEMS = ["revenue", "gross_profit", "operating_income", "net_income", "eps_diluted",
         "operating_cf", "capex", "fcf", "total_assets", "total_equity", "shares_diluted", "cash", "borrowings"]
TOL = 1.0


def one(conn, t, items=ITEMS):
    """Our annual numbers next to Yahoo's for one ticker (last 4 fiscal years): ticker, item, period_end,
    ours, yahoo, diff_%. Rows where Yahoo has no number are left out."""
    _, fin = validate.load(conn, t)
    y = validate.yahoo_check(fin, t)
    y = y[y["item"].isin(items) & y["yahoo"].notna()].copy()
    y["diff_%"] = pd.to_numeric(y["diff_%"], errors="coerce")       # None when we have no number
    y.insert(0, "ticker", t)
    return y


def compare(conn, tickers, items=ITEMS, done=None, on_result=None):
    """one() for many tickers, printing progress. `done`: results already fetched (skipped here);
    `on_result(df)` is called after each ticker (used to save progress)."""
    rows = [done] if done is not None and len(done) else []
    skip = set(done.ticker) if done is not None and len(done) else set()
    todo = [t for t in tickers if t not in skip]
    for i, t in enumerate(todo, 1):
        try:
            y = one(conn, t, items)
        except Exception as e:                       # noqa: BLE001
            print(f"{t}: failed ({e})", flush=True)
            continue
        rows.append(y)
        if on_result:
            on_result(y)
        n = y["diff_%"].notna().sum()
        print(f"[{i}/{len(todo)}] {t}: {(y['diff_%'].abs() <= TOL).sum()}/{n} match", flush=True)
    df = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(
        columns=["ticker", "item", "period_end", "ours", "yahoo", "diff_%", "note"])
    df["ok"] = df["diff_%"].abs() <= TOL
    return df


def main(argv):
    seed = 1
    if "--seed" in argv:
        i = argv.index("--seed")
        seed = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    conn = db.connect()
    if argv and not argv[0].isdigit():
        tickers = [t.upper() for t in argv]
    else:
        n = int(argv[0]) if argv else 30
        pool = sorted(t for (t,) in conn.execute(
            "SELECT c.ticker FROM companies c JOIN data_quality q ON q.cik=c.cik WHERE c.ticker IS NOT NULL"))
        tickers = random.Random(seed).sample(pool, min(n, len(pool)))
    df = compare(conn, tickers)
    df = df[df["diff_%"].notna()]
    summ = df.groupby("item").agg(compared=("ok", "size"), match=("ok", "sum"))
    summ["match_%"] = (100 * summ.match / summ.compared).round(1)
    summ = summ.reindex([i for i in ITEMS if i in summ.index])
    bad = df[~df.ok].assign(a=lambda d: d["diff_%"].abs()).sort_values("a", ascending=False)
    out = [f"# Spot-check against Yahoo ({date.today()})", "",
           f"{len(tickers)} companies: {', '.join(tickers)}", "",
           f"**{df.ok.sum()} of {len(df)} numbers within {TOL}% of Yahoo ({100 * df.ok.mean():.1f}%).**", "",
           summ.to_markdown(), "", "## Mismatches (largest first)", "",
           bad[["ticker", "item", "period_end", "ours", "yahoo", "diff_%"]].to_markdown(index=False,
                                                                                       floatfmt=".4g")]
    REPORT.write_text("\n".join(out), encoding="utf-8")
    print(f"\n{df.ok.sum()}/{len(df)} within {TOL}% ({100 * df.ok.mean():.1f}%) -> {REPORT}")


if __name__ == "__main__":
    main(sys.argv[1:])
