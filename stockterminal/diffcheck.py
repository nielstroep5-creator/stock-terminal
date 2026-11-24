"""Before/after check for data fixes: which numbers did a change move, for every company?

    python -m stockterminal.diffcheck snapshot [name]   # save today's key numbers (before a change)
    python -m stockterminal.diffcheck [name]            # compare the database now with a snapshot
                                                        # (default: the newest one)
    python -m stockterminal.diffcheck list              # the saved snapshots

Typical use: snapshot -> change mapping/standardize -> `python -m stockterminal.standardize` (or a few
tickers) -> diffcheck. The report lists every number that moved by more than 0.5%, appeared or disappeared,
grouped by item and by company, so one fix can be checked against all companies at once and side effects
show up before the change is pushed.

Snapshots live in data/snapshots/ (one compressed CSV each, ~10 MB for the S&P 500; never in git).
Writes data/reports/diffcheck.md and diffcheck.csv (every changed number).
"""
import sys
from datetime import datetime

import pandas as pd

from . import db
from .config import DATA_DIR

SNAP_DIR = DATA_DIR / "snapshots"
REPORT = DATA_DIR / "reports" / "diffcheck.md"
ITEMS = ["revenue", "gross_profit", "operating_income", "pretax_income", "income_tax", "net_income",
         "eps_diluted", "shares_diluted", "shares_outstanding", "dps", "ebitda", "d_and_a",
         "cash", "total_assets", "total_liabilities", "total_equity", "total_debt",
         "operating_cf", "capex", "fcf", "dividends_paid", "buybacks"]
TOL = 0.5          # % change that counts as "moved"
KEY = ["ticker", "item", "period_type", "fiscal_year", "fiscal_quarter"]


def current(conn, tickers=None):
    """The key numbers in the database now: one row per ticker/item/period, plus the quality score and the
    latest market cap as pseudo-items."""
    marks = ",".join("?" * len(ITEMS))
    fin = pd.read_sql_query(
        f"SELECT c.ticker, f.item, f.period_type, f.fiscal_year, COALESCE(f.fiscal_quarter, 0) fiscal_quarter, "
        f"f.value FROM financials f JOIN companies c ON c.cik=f.cik "
        f"WHERE c.ticker IS NOT NULL AND f.item IN ({marks}) AND f.period_type IN ('FY','Q')", conn, params=ITEMS)
    q = pd.read_sql_query("SELECT c.ticker, 'quality_score' item, 'NOW' period_type, 0 fiscal_year, "
                          "0 fiscal_quarter, q.score value FROM data_quality q JOIN companies c ON c.cik=q.cik "
                          "WHERE c.ticker IS NOT NULL", conn)
    mc = pd.read_sql_query("SELECT v.ticker, 'market_cap' item, 'NOW' period_type, 0 fiscal_year, "
                           "0 fiscal_quarter, v.market_cap value FROM valuation_daily v JOIN "
                           "(SELECT ticker, MAX(date) d FROM valuation_daily GROUP BY ticker) m "
                           "ON m.ticker=v.ticker AND m.d=v.date", conn)
    df = pd.concat([fin, q, mc], ignore_index=True)
    df["fiscal_year"] = df.fiscal_year.fillna(0).astype(int)
    df["fiscal_quarter"] = df.fiscal_quarter.fillna(0).astype(int)
    if tickers:
        df = df[df.ticker.isin(tickers)]
    # a period can appear twice (e.g. transition years); keep the first so keys are unique
    return df.drop_duplicates(KEY).reset_index(drop=True)


def snapshots():
    return sorted(SNAP_DIR.glob("*.csv.gz"), key=lambda p: p.stat().st_mtime)


def snapshot(conn, name=None):
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    name = name or datetime.now().strftime("%Y-%m-%d_%H%M")
    path = SNAP_DIR / f"{name}.csv.gz"
    df = current(conn)
    df.to_csv(path, index=False)
    print(f"Saved {len(df):,} numbers of {df.ticker.nunique()} companies -> {path}")
    return path


def _period(r):
    if r.period_type == "NOW":
        return "now"
    return f"FY{r.fiscal_year}" if r.period_type == "FY" else f"Q{r.fiscal_quarter} FY{r.fiscal_year}"


def compare(before, after):
    """Rows that changed: KEY + before, after, change_% and what happened (changed / new / gone)."""
    m = before.merge(after, on=KEY, how="outer", suffixes=("_before", "_after"))
    b, a = m.value_before, m.value_after
    m["change_%"] = (a - b) / b.abs() * 100
    m.loc[(b == 0) & (a != 0), "change_%"] = float("inf")
    m["what"] = None
    m.loc[b.notna() & a.isna(), "what"] = "gone"
    m.loc[b.isna() & a.notna(), "what"] = "new"
    m.loc[b.notna() & a.notna() & (m["change_%"].abs() > TOL), "what"] = "changed"
    out = m[m.what.notna()].rename(columns={"value_before": "before", "value_after": "after"})
    out = out.assign(a=out["change_%"].abs().fillna(float("inf"))).sort_values(["ticker", "item", "a"],
                                                                                ascending=[True, True, False])
    out["period"] = [_period(r) for r in out.itertuples()]
    return out.drop(columns="a").reset_index(drop=True)


def report(ch, before, after, name):
    n_co = after.ticker.nunique()
    lines = [f"# What changed since snapshot '{name}' ({datetime.now():%Y-%m-%d %H:%M})", ""]
    if ch.empty:
        lines += [f"Nothing moved by more than {TOL}% for any of the {n_co} companies."]
        return "\n".join(lines)
    lines += [f"**{ch.ticker.nunique()} of {n_co} companies changed; {len(ch):,} numbers "
              f"({(ch.what == 'changed').sum():,} changed by more than {TOL}%, {(ch.what == 'new').sum():,} new, "
              f"{(ch.what == 'gone').sum():,} gone).**", ""]
    gone_co = sorted(set(before.ticker) - set(after.ticker))
    if gone_co:
        lines += [f"Companies no longer in the database: {', '.join(gone_co)}", ""]
    by_item = ch.pivot_table(index="item", columns="what", values="ticker", aggfunc="count", fill_value=0)
    by_item["companies"] = ch.groupby("item").ticker.nunique()
    lines += ["## Per item", "", by_item.sort_values("companies", ascending=False).to_markdown(), ""]
    per_co = ch.groupby("ticker").agg(numbers=("item", "size"), items=("item", lambda s: ", ".join(sorted(set(s)))))
    per_co = per_co.sort_values("numbers", ascending=False)
    lines += ["## Per company", "", per_co.to_markdown(), "", "## Details (up to 8 per company and item)", ""]
    for (t, item), g in ch.groupby(["ticker", "item"], sort=True):
        parts = []
        for r in g.head(8).to_dict("records"):
            if r["what"] == "changed":
                c = r["change_%"]
                pct = "from zero" if c == float("inf") else f"{c:+.1f}%"
                parts.append(f"{r['period']} {r['before']:,.4g} → {r['after']:,.4g} ({pct})")
            else:
                parts.append(f"{r['period']} {r['what']}")
        more = f" … +{len(g) - 8}" if len(g) > 8 else ""
        lines.append(f"- **{t} {item}**: " + "; ".join(parts) + more)
    return "\n".join(lines)


def main(argv):
    conn = db.connect()
    if argv[:1] == ["snapshot"]:
        snapshot(conn, argv[1] if len(argv) > 1 else None)
        return
    snaps = snapshots()
    if argv[:1] == ["list"]:
        for p in snaps:
            print(f"{p.name[:-7]}  ({datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d %H:%M})")
        return
    if argv:
        path = SNAP_DIR / f"{argv[0]}.csv.gz"
        if not path.exists():
            sys.exit(f"No snapshot '{argv[0]}' in {SNAP_DIR}")
    elif snaps:
        path = snaps[-1]
    else:
        sys.exit("No snapshot yet: run `python -m stockterminal.diffcheck snapshot` before the change.")
    name = path.name[:-7]
    before = pd.read_csv(path)
    after = current(conn)
    ch = compare(before, after)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(report(ch, before, after, name), encoding="utf-8")
    ch[["ticker", "item", "period", "before", "after", "change_%", "what"]].to_csv(
        REPORT.with_suffix(".csv"), index=False)
    print(f"{ch.ticker.nunique()} companies, {len(ch):,} numbers changed since '{name}' -> {REPORT}")


if __name__ == "__main__":
    main(sys.argv[1:])
