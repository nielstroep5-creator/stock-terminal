"""Data-quality baseline: one report on how good the numbers of all loaded companies are.

    python -m stockterminal.baseline              # everything, incl. Yahoo for every company (~20-30 min)
    python -m stockterminal.baseline --no-yahoo   # only what is in the database (a minute)
    python -m stockterminal.baseline --fresh      # fetch Yahoo again instead of reusing today's results
    python -m stockterminal.baseline --name midcap T1 T2 ...   # only these tickers, own report file

Four views, because the quality score alone only says whether a company's numbers agree with each other:
1. Accuracy: our annual numbers next to Yahoo's (last 4 fiscal years, 1% tolerance), and what kind of
   difference each mismatch is (we miss it, sign, x1000, a few %, far off; one year or all years).
2. Completeness: which key line items each company has (latest year, and years since 2010).
3. Consistency: the quality score spread and which checks fail most.
4. Data health: how many companies each rule of the Data health page flags.
Ends with problem groups ranked by how many companies they touch, so the biggest causes get fixed first.

Writes data/reports/quality_baseline.md, plus CSVs next to it (quality_baseline_yahoo.csv: every compared
number, reused when run again the same day, so a stopped run continues where it was).
"""
import json
import sys
from collections import Counter
from datetime import date, datetime

import pandas as pd

from . import db, spotcheck
from .config import DATA_DIR

OUT = DATA_DIR / "reports"
REPORT = OUT / "quality_baseline.md"
YAHOO_CSV = OUT / "quality_baseline_yahoo.csv"
COVER_CSV = OUT / "quality_baseline_coverage.csv"

ACC_ITEMS = spotcheck.ITEMS + ["d_and_a", "dividends_paid", "buybacks"]
TOL = spotcheck.TOL

# items every company of a kind should have ("" = ordinary company)
KEY_ITEMS = {
    "": ["revenue", "gross_profit", "operating_income", "pretax_income", "net_income", "eps_diluted",
         "shares_diluted", "total_assets", "total_liabilities", "total_equity", "cash", "total_debt",
         "operating_cf", "capex", "fcf", "d_and_a", "ebitda"],
    "bank": ["revenue", "net_interest_income", "pretax_income", "net_income", "eps_diluted", "shares_diluted",
             "total_assets", "total_equity", "deposits", "loans", "operating_cf"],
    "insurer": ["revenue", "premiums_earned", "pretax_income", "net_income", "eps_diluted", "shares_diluted",
                "total_assets", "total_equity", "operating_cf"],
    "reit": ["revenue", "operating_income", "net_income", "eps_diluted", "shares_diluted", "total_assets",
             "total_equity", "total_debt", "operating_cf", "capex", "ffo"],
}
NOT_EXPECTED = {   # a missing item is normal for these: no gross profit for most service companies etc.
    "gross_profit": "many service companies and utilities have no cost-of-revenue line",
}


def _md(df, **kw):
    return df.to_markdown(**kw) if len(df) else "_none_"


# --------------------------------------------------------------------------- companies
def _companies(conn):
    """ticker -> dict(cik, name, kind, sector, quality) from the Screener's table (no internet)."""
    from . import screener
    rows = screener.build(conn)
    ciks = dict(conn.execute("SELECT ticker, cik FROM companies WHERE ticker IS NOT NULL"))
    out = {}
    for r in rows:
        if r["ticker"] in ciks:
            out[r["ticker"]] = dict(cik=ciks[r["ticker"]], name=r.get("name"), kind=r.get("kind") or "",
                                    sector=r.get("sector"), quality=r.get("quality"))
    return out, rows


# --------------------------------------------------------------------------- 1. accuracy
def _yahoo(conn, tickers, fresh):
    OUT.mkdir(parents=True, exist_ok=True)
    done = None
    if YAHOO_CSV.exists() and not fresh and date.fromtimestamp(YAHOO_CSV.stat().st_mtime) == date.today():
        done = pd.read_csv(YAHOO_CSV)
        done = done[done.ticker.isin(tickers)]
        print(f"Reusing today's Yahoo results for {done.ticker.nunique()} companies", flush=True)
    elif YAHOO_CSV.exists():
        YAHOO_CSV.unlink()

    def save(y):
        y.to_csv(YAHOO_CSV, mode="a", header=not YAHOO_CSV.exists(), index=False)

    return spotcheck.compare(conn, tickers, ACC_ITEMS, done=done, on_result=save)


def _kind_of_diff(ours, yahoo, d):
    if pd.isna(ours):
        return "we have no number"
    if yahoo and ours and (ours > 0) != (yahoo > 0) and abs(abs(ours) - abs(yahoo)) <= abs(yahoo) * TOL / 100:
        return "opposite sign"
    r = abs(ours / yahoo) if yahoo else None
    if r and any(abs(r / f - 1) < 0.02 for f in (1000, 1e6, 0.001, 1e-6)):
        return "scale (x1000)"
    a = abs(d)
    return "1-5% off" if a <= 5 else "5-25% off" if a <= 25 else "more than 25% off"


def accuracy(df, cos):
    """Summary tables + per (company, item) patterns."""
    df = df.copy()
    df["kind"] = df.ticker.map(lambda t: cos.get(t, {}).get("kind", ""))
    df["diff"] = [None if ok else _kind_of_diff(o, y, d)
                  for o, y, d, ok in zip(df.ours, df.yahoo, df["diff_%"], df.ok)]
    comp = df[df.ours.notna()]
    order = [i for i in ACC_ITEMS if i in set(df.item)]
    by_item = comp.groupby("item").agg(compared=("ok", "size"), match=("ok", "sum")).reindex(order)
    by_item["we have no number"] = df[df.ours.isna()].groupby("item").size()
    by_item["companies with a mismatch"] = df[~df.ok].groupby("item").ticker.nunique()
    by_item = by_item.fillna(0).astype(int)
    by_item["match_%"] = (100 * by_item.match / by_item.compared.where(by_item.compared > 0)).round(1)
    by_kind = comp.groupby("kind").agg(compared=("ok", "size"), match=("ok", "sum"))
    by_kind["match_%"] = (100 * by_kind.match / by_kind.compared).round(1)
    by_kind.index = [k or "ordinary" for k in by_kind.index]
    # per company and item: wrong in every compared year (likely the wrong line) or only some (restatement,
    # one-off, a single bad filing)
    years = df.groupby(["ticker", "item"]).size().rename("years")
    bad = df[~df.ok]
    per = bad.groupby(["ticker", "item"]).agg(bad=("ok", "size"), worst=("diff_%", lambda s: s.abs().max()),
                                              diff=("diff", lambda s: Counter(s).most_common(1)[0][0]))
    per = per.join(years).reset_index()
    per["pattern"] = ["every year" if b == y else "some years" for b, y in zip(per.bad, per.years)]
    return df, by_item, by_kind, per


# --------------------------------------------------------------------------- 2. completeness
def completeness(conn, cos):
    fy = pd.read_sql_query("SELECT cik, item, fiscal_year, end_date, value FROM financials "
                           "WHERE period_type='FY' AND value IS NOT NULL", conn)
    q = pd.read_sql_query("SELECT cik, MAX(end_date) last_q FROM financials WHERE period_type='Q' GROUP BY cik", conn)
    last_fy = fy.groupby("cik").fiscal_year.max()
    first_fy = fy.groupby("cik").fiscal_year.min()
    have = fy.groupby(["cik", "item"]).fiscal_year.apply(set)
    rows = []
    for t, c in cos.items():
        cik, kind = c["cik"], c["kind"]
        if cik not in last_fy.index:
            continue
        ly, fy0 = int(last_fy[cik]), max(int(first_fy[cik]), 2010)
        span = set(range(fy0, ly + 1))
        for it in KEY_ITEMS.get(kind, KEY_ITEMS[""]):
            yrs = have.get((cik, it), set())
            rows.append(dict(ticker=t, kind=kind or "ordinary", item=it, latest=ly in yrs,
                             years_missing=len(span - yrs), years=len(span)))
    cov = pd.DataFrame(rows)
    summ = cov.groupby(["kind", "item"]).agg(companies=("ticker", "size"), have_latest=("latest", "sum"),
                                             years_missing=("years_missing", "sum"), years=("years", "sum"))
    summ["latest_%"] = (100 * summ.have_latest / summ.companies).round(1)
    summ["history_%"] = (100 * (1 - summ.years_missing / summ.years)).round(1)
    summ = summ.drop(columns=["years_missing", "years"])
    missing = cov[~cov.latest].groupby(["kind", "item"]).ticker.apply(lambda s: ", ".join(sorted(s)))
    # stale: the newest quarter is old although the company keeps filing
    q["ticker"] = q.cik.map({c["cik"]: t for t, c in cos.items()})
    q = q[q.ticker.notna()]
    q["days"] = (pd.Timestamp(date.today()) - pd.to_datetime(q.last_q)).dt.days
    stale = q[q.days > 200].sort_values("days", ascending=False)[["ticker", "last_q", "days"]]
    return cov, summ, missing, stale


# --------------------------------------------------------------------------- 3. consistency
def consistency(conn, cos):
    by_cik = {c["cik"]: t for t, c in cos.items()}
    scores, fails = [], Counter()
    worst = []
    for cik, score, details in conn.execute("SELECT cik, score, details FROM data_quality"):
        t = by_cik.get(cik)
        if t is None or score is None:
            continue
        s = score / 100 if score > 1 else score
        scores.append(s)
        try:
            issues = (json.loads(details or "{}") or {}).get("issues", [])
        except ValueError:
            issues = []
        for i in issues:
            fails[i.get("text")] += 1
        worst.append((t, round(100 * s, 2), "; ".join(f"{i.get('text')} ({i.get('count')})" for i in issues)))
    sc = pd.Series(scores)
    spread = pd.DataFrame([("companies", len(sc)), ("average %", round(100 * sc.mean(), 2)),
                           ("median %", round(100 * sc.median(), 2)), ("at 100%", int((sc >= 0.99995).sum())),
                           ("below 99%", int((sc < 0.99).sum())), ("below 95%", int((sc < 0.95).sum()))],
                          columns=["", "value"]).set_index("")
    top = pd.DataFrame(fails.most_common(), columns=["failing check", "companies"])
    worst = pd.DataFrame(sorted(worst, key=lambda r: r[1])[:25], columns=["ticker", "score %", "issues"])
    return spread, top, worst


# --------------------------------------------------------------------------- 4. data health
def data_health(rows):
    from . import health
    found = health.scan(rows)
    c = Counter()
    who = {}
    for co in found:
        for p in co["problems"]:
            c[p["why"]] += 1
            who.setdefault(p["why"], []).append(co["ticker"])
    return pd.DataFrame([(w, n, ", ".join(sorted(who[w])[:15]) + (" ..." if n > 15 else ""))
                         for w, n in c.most_common()], columns=["rule", "companies", "examples"]), len(found)


# --------------------------------------------------------------------------- ranking
def ranking(per, cov, health_df, top_fail):
    """Problem groups, most companies first."""
    groups = []
    if per is not None and len(per):
        for (item, diff, pattern), g in per.groupby(["item", "diff", "pattern"]):
            if diff == "1-5% off" and pattern == "some years":
                continue               # small one-year differences: mostly Yahoo's own adjustments
            groups.append(("Yahoo", f"{item}: {diff}, {pattern}", g.ticker.nunique(),
                           ", ".join(sorted(g.ticker.unique())[:12])))
    if len(cov):
        miss = cov[~cov.latest]
        for (kind, item), g in miss.groupby(["kind", "item"]):
            note = f" ({NOT_EXPECTED[item]})" if item in NOT_EXPECTED else ""
            groups.append(("Missing", f"{item} missing in latest year ({kind}){note}", len(g),
                           ", ".join(sorted(g.ticker)[:12])))
    for _, r in health_df.iterrows():
        groups.append(("Data health", r.rule, r.companies, r.examples))
    for _, r in top_fail.iterrows():
        groups.append(("Checks", r["failing check"], r.companies, ""))
    out = pd.DataFrame(groups, columns=["source", "problem", "companies", "examples"])
    return out.sort_values("companies", ascending=False).reset_index(drop=True)


# --------------------------------------------------------------------------- main
def main(argv):
    yahoo, fresh = "--no-yahoo" not in argv, "--fresh" in argv
    global REPORT
    if "--name" in argv:
        i = argv.index("--name")
        REPORT = OUT / f"quality_baseline_{argv[i + 1]}.md"
        argv = argv[:i] + argv[i + 2:]
    tickers_arg = [a.upper() for a in argv if not a.startswith("--")]
    started = datetime.now()
    conn = db.connect()
    print("Reading companies ...", flush=True)
    cos, rows = _companies(conn)
    if tickers_arg:
        cos = {t: c for t, c in cos.items() if t in tickers_arg}
        rows = [r for r in rows if r["ticker"] in cos]
    print(f"{len(cos)} companies", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)

    cov, cov_summ, missing, stale = completeness(conn, cos)
    cov.to_csv(COVER_CSV, index=False)
    spread, top_fail, worst = consistency(conn, cos)
    health_df, n_flagged = data_health(rows)

    per = None
    acc_txt = ["_Skipped (--no-yahoo)._"]
    if yahoo:
        print("Comparing with Yahoo (saved as it goes; run again to continue) ...", flush=True)
        df = _yahoo(conn, sorted(cos), fresh)
        from . import accuracy as acc
        acc.store(conn, df[df.item.isin(acc.ITEMS)][["ticker", "item", "period_end", "ours", "yahoo", "diff_%"]])
        df, by_item, by_kind, per = accuracy(df, cos)
        comp = df[df.ours.notna()]
        n_co = df.ticker.nunique()
        mix = df[~df.ok].groupby("diff").size().sort_values(ascending=False).rename("numbers").to_frame()
        every = per[per.pattern == "every year"].sort_values("worst", ascending=False)
        some = per[(per.pattern == "some years") & (per.worst > 5)].sort_values("worst", ascending=False)
        acc_txt = [
            f"{n_co} companies compared. **{int(comp.ok.sum())} of {len(comp)} numbers within {TOL}% of Yahoo "
            f"({100 * comp.ok.mean():.1f}%)**; for {int(df.ours.isna().sum())} more Yahoo has a number and we "
            f"have none.", "",
            "Yahoo is not always right either: it adjusts operating income for some one-offs, and its "
            "definitions of debt and capex differ for some companies. A mismatch is a lead, not proof.", "",
            "### Per item", "", _md(by_item), "",
            "### Per kind of company", "", _md(by_kind), "",
            "### What kind of difference", "", _md(mix), "",
            f"### Wrong in every compared year ({len(every)} company-items, largest first)", "",
            "Usually the wrong line or a definition difference.", "",
            _md(every[["ticker", "item", "years", "diff", "worst"]].head(150), index=False, floatfmt=".1f"), "",
            f"### Wrong in some years only, more than 5% off ({len(some)})", "",
            "Usually a restatement, a one-off filing mistake or a tag switch.", "",
            _md(some[["ticker", "item", "years", "bad", "diff", "worst"]].head(150), index=False, floatfmt=".1f"),
        ]
        per.to_csv(OUT / "quality_baseline_mismatches.csv", index=False)

    rank = ranking(per, cov, health_df, top_fail)
    rank.to_csv(OUT / "quality_baseline_ranking.csv", index=False)
    mins = (datetime.now() - started).total_seconds() / 60
    out = [
        f"# Data quality baseline ({date.today()})", "",
        f"{len(cos)} companies, made in {mins:.0f} min. CSVs with every row are next to this file.", "",
        "## Problem groups, most companies first", "",
        _md(rank.head(60), index=False), "",
        "## 1. Accuracy against Yahoo (annual, last 4 fiscal years)", "", *acc_txt, "",
        "## 2. Completeness", "",
        "`latest_%`: companies with the item in their latest fiscal year. `history_%`: share of years since "
        "2010 (or the first year) that have it.", "",
        _md(cov_summ), "",
        "### Missing in the latest year", "",
        _md(missing.to_frame("companies"), floatfmt=".4g"), "",
        f"### Newest quarter older than 200 days ({len(stale)})", "",
        _md(stale, index=False), "",
        "## 3. Consistency (quality score)", "", _md(spread), "",
        "### Failing checks", "", _md(top_fail, index=False), "",
        "### Lowest scores", "", _md(worst, index=False), "",
        f"## 4. Data health page ({n_flagged} companies flagged)", "", _md(health_df, index=False), "",
    ]
    REPORT.write_text("\n".join(out), encoding="utf-8")
    print(f"\nDone in {mins:.0f} min -> {REPORT}")


if __name__ == "__main__":
    main(sys.argv[1:])
