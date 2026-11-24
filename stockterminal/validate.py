"""Validation report for the standardized financials.

Usage:
    python -m stockterminal.validate AAPL MSFT

Writes data/reports/validation_report.md and prints a summary.
Checks:
  1. Balance sheet: total assets = liabilities + equity (incl. noncontrolling interest)
  2. Q1+Q2+Q3+Q4 = FY for every flow item
  3. Gross profit = revenue - cost of revenue (where all three are reported)
  4. Net income: income statement vs cash-flow starting line, and pretax - tax
  5. No gaps in annual/quarterly revenue and net income from 2009 on
  6. Independent cross-check of the last 4 fiscal years against Yahoo Finance
"""
import re
import sys
from datetime import date

import pandas as pd

from . import db
from .config import DATA_DIR
from .mapping import ITEM_BY_KEY, ITEMS

REPORT = DATA_DIR / "reports" / "validation_report.md"

# standard item -> (Yahoo statement, Yahoo row, sign)
YAHOO_MAP = {
    "revenue": ("income_stmt", "Total Revenue", 1),
    "cost_of_revenue": ("income_stmt", "Cost Of Revenue", 1),
    "gross_profit": ("income_stmt", "Gross Profit", 1),
    "rnd": ("income_stmt", "Research And Development", 1),
    # Yahoo's "Operating Income" leaves out one-offs (restructuring, impairments); the as-reported line is
    # the company's own GAAP number, like ours. "a | b": the first row Yahoo has.
    "operating_income": ("income_stmt", "Total Operating Income As Reported | Operating Income", 1),
    "pretax_income": ("income_stmt", "Pretax Income", 1),
    "income_tax": ("income_stmt", "Tax Provision", 1),
    "net_income": ("income_stmt", "Net Income", 1),
    "eps_diluted": ("income_stmt", "Diluted EPS", 1),
    "shares_diluted": ("income_stmt", "Diluted Average Shares", 1),
    "cash": ("balance_sheet", "Cash And Cash Equivalents", 1),
    "cash_and_sti": ("balance_sheet", "Cash Cash Equivalents And Short Term Investments", 1),
    "receivables": ("balance_sheet", "Accounts Receivable", 1),
    "inventory": ("balance_sheet", "Inventory", 1),
    "total_current_assets": ("balance_sheet", "Current Assets", 1),
    "goodwill": ("balance_sheet", "Goodwill", 1),
    "total_assets": ("balance_sheet", "Total Assets", 1),
    "total_current_liabilities": ("balance_sheet", "Current Liabilities", 1),
    "long_term_debt": ("balance_sheet", "Long Term Debt", 1),
    # Yahoo's "Total Debt" mixes in lease obligations inconsistently between companies,
    # so we compare debt excluding all leases on both sides.
    "borrowings": ("balance_sheet", "Total Debt - Capital Lease Obligations", 1),
    "total_liabilities": ("balance_sheet", "Total Liabilities Net Minority Interest", 1),
    "total_equity": ("balance_sheet", "Stockholders Equity", 1),
    "shares_outstanding": ("balance_sheet", "Ordinary Shares Number", 1),
    "operating_cf": ("cashflow", "Operating Cash Flow", 1),
    "capex": ("cashflow", "Capital Expenditure", -1),
    "fcf": ("cashflow", "Free Cash Flow", 1),
    "d_and_a": ("cashflow", "Depreciation And Amortization", 1),
    "sbc": ("cashflow", "Stock Based Compensation", 1),
    "dividends_paid": ("cashflow", "Cash Dividends Paid", -1),
    "buybacks": ("cashflow", "Repurchase Of Capital Stock", -1),
}


def pct(a, b):
    return None if b in (0, None) or pd.isna(b) or pd.isna(a) else (a - b) / abs(b) * 100


def load(conn, ticker):
    cik = conn.execute("SELECT cik FROM companies WHERE ticker=?", (ticker,)).fetchone()[0]
    fin = pd.read_sql_query("SELECT * FROM financials WHERE cik=?", conn, params=(cik,))
    return cik, fin


def wide(fin, ptype, col="value"):
    f = fin[fin.period_type == ptype]
    idx = ["fiscal_year"] if ptype == "FY" else ["fiscal_year", "fiscal_quarter"]
    return f.pivot_table(index=idx, columns="item", values=col, aggfunc="first")


def reported(fin, ptype):
    """Same as wide() but only values reported directly (method='reported')."""
    return wide(fin[fin.method == "reported"], ptype)


# --------------------------------------------------------------------------- coverage
def coverage(fin):
    rows = []
    fy = fin[fin.period_type == "FY"]
    q = fin[fin.period_type == "Q"]
    for it in ITEMS:
        k = it["key"]
        a = fy[fy.item == k]
        if a.empty:
            rows.append((k, "-", 0, "-", "NOT FOUND", "-"))
            continue
        yrs = sorted(a.fiscal_year.astype(int))
        first = max(yrs[0], 2009)
        gaps = [y for y in range(first, yrs[-1] + 1) if y not in yrs]
        src = a.source.value_counts()
        src_txt = "; ".join(f"{s} ({n})" for s, n in src.items())
        nq = len(q[q.item == k])
        rows.append((k, f"{yrs[0]}-{yrs[-1]}", len(yrs), ",".join(map(str, gaps)) or "none",
                     src_txt, nq))
    return pd.DataFrame(rows, columns=["item", "FY range", "FY count", "gaps since 2009",
                                       "source (FY count)", "quarters"])


# --------------------------------------------------------------------------- checks
def fy_versions(conn, cik, source, start, end):
    """All values ever filed for a single-tag annual figure."""
    if not source or " " in source:
        return []
    tax, tag = source.split(":", 1) if ":" in source else ("us-gaap", source)
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT value FROM xbrl_facts WHERE cik=? AND taxonomy=? AND tag=? "
        "AND start_date=? AND end_date=?", (cik, tax, tag, start, end))]


def checks(conn, cik, fin):
    res = []   # (check, period, detail, ok)
    for ptype in ("FY", "Q"):
        w = wide(fin, ptype)
        for idx, r in w.iterrows():
            label = f"{ptype} {idx}"
            A, LE = r.get("total_assets"), r.get("liabilities_and_equity")
            L, E = r.get("total_liabilities"), r.get("total_equity_incl_nci")
            T = r.get("temporary_equity")           # mezzanine equity sits between L and E
            T = T if pd.notna(T) else 0
            if pd.notna(A) and pd.notna(LE) and A:
                d = pct(LE, A)
                res.append(("A = L&E (reported)", label, f"{d:+.3f}%", abs(d) < 0.1))
            if pd.notna(A) and pd.notna(L) and pd.notna(E) and A:
                d = pct(L + T + E, A)
                if T and abs(pct(L + E, A)) < abs(d):
                    d = pct(L + E, A)      # the company's own "total liabilities" already includes it
                res.append(("A = L + E incl. NCI", label, f"{d:+.3f}%", abs(d) < 0.1))

    # quarters sum to FY. If they only match the ORIGINALLY reported annual figure, the company
    # restated that year later but never restated its quarters -> reported as 'restated', not a failure.
    fyw, qw = wide(fin, "FY"), wide(fin, "Q")
    fyo, fys = wide(fin, "FY", "original_value"), wide(fin, "FY", "source")
    fystart, fyend = wide(fin, "FY", "start_date"), wide(fin, "FY", "end_date")
    restated = set()   # (year, item)
    qsrc = wide(fin, "Q", "source")
    qmeth, fymeth = wide(fin, "Q", "method"), wide(fin, "FY", "method")
    mixed = set()      # (year, item): year and quarters come from different tags / formulas
    disagree = set()   # (year, item): the company's own quarterly and annual figures do not add up
    # dividends per share are not checked: the year is often the declared total and the quarters the paid
    # amounts (or the other way round), and special dividends fall in either
    # net income to common is not additive: the share allocated to participating securities is
    # recalculated every quarter (same reason EPS is not additive)
    flow_items = sorted((i["key"] for i in ITEMS if i["kind"] == "flow"
                         and i["key"] != "net_income_common"),
                        key=lambda k: not ITEM_BY_KEY[k]["tags"])   # derived items last
    for y in fyw.index:
        if y not in qw.index.get_level_values(0):
            continue
        qs = qw.loc[y]
        if len(qs) != 4:
            continue
        for k in flow_items:
            if k not in fyw or k not in qw or pd.isna(fyw.at[y, k]) or qs[k].isna().any():
                continue
            d = pct(qs[k].sum(), fyw.at[y, k])
            if d is None:
                continue
            # filings round to millions: four rounded quarters can be a few million off the year
            # (a computed line, e.g. gross profit = revenue - costs, carries the rounding of each input)
            computed = " " in str(fys.at[y, k] or "")
            rounding = ITEM_BY_KEY[k]["kind"] == "flow" and \
                abs(qs[k].sum() - fyw.at[y, k]) <= (5e6 if computed else 2.5e6)
            if abs(d) < 0.01 or rounding:
                res.append(("Q1..Q4 = FY", f"FY {y} {k}", f"{d:+.3f}%", True))
                continue
            d0 = pct(qs[k].sum(), fyo.at[y, k])
            vers = fy_versions(conn, cik, fys.at[y, k], fystart.at[y, k], fyend.at[y, k])
            dv = [pct(qs[k].sum(), v) for v in vers]
            ops = set(re.findall(r"[a-z][a-z0-9_]*", fys.at[y, k] or "")) if " " in (fys.at[y, k] or "") else set()
            if (d0 is not None and abs(d0) < 0.01) or any(x is not None and abs(x) < 0.01 for x in dv):
                restated.add((y, k))
                res.append(("Q1..Q4 = FY (restated year)", f"FY {y} {k}",
                            f"quarters match an earlier filed FY; FY later restated by {-d:+.2f}%", True))
            elif set(qsrc.loc[y][k]) != {fys.at[y, k]}:
                mixed.add((y, k))
                res.append(("Q1..Q4 = FY (restated year)", f"FY {y} {k}",
                            f"{d:+.3f}%: FY from {fys.at[y, k]} (restated), quarters only "
                            f"available as {', '.join(sorted(set(qsrc.loc[y][k])))}", True))
            elif ops and any((y, o) in restated for o in ops):
                res.append(("Q1..Q4 = FY (restated year)", f"FY {y} {k}",
                            f"{d:+.3f}%: derived from restated inputs", True))
            elif (fymeth.at[y, k] == "reported" and k in qmeth
                  and all(m == "reported" for m in qmeth.loc[y][k])):
                # all four quarters and the year are figures the company itself reported: nothing
                # was derived, its filings simply disagree (information, not a data error of ours)
                disagree.add((y, k))
                res.append(("Q1..Q4 = FY (company's filings disagree)", f"FY {y} {k}",
                            f"{d:+.3f}%: the four quarters the company reported do not add up to its annual figure", True))
            else:
                res.append(("Q1..Q4 = FY", f"FY {y} {k}", f"{d:+.3f}%", False))

    # A restatement (e.g. a sold business moved to "discontinued operations") changes many lines
    # of a statement at once, but a line that is only tagged in the later filing has no earlier
    # version to compare with. If another line of the same statement was provably restated that
    # year, treat the remaining mismatches of that statement and year as restated too.
    stmt_of = {i["key"]: i["statement"] for i in ITEMS}
    restated_stmts = {(y, stmt_of[k]) for y, k in restated}
    for y in fyw.index:
        for k in flow_items:
            if k in fyw and k in fyo and pd.notna(fyw.at[y, k]) and pd.notna(fyo.at[y, k]):
                d = pct(fyw.at[y, k], fyo.at[y, k])
                if d is not None and abs(d) >= 1:          # a real restatement, not rounding
                    restated_stmts.add((y, stmt_of[k]))
    for n, (check, period, detail, ok) in enumerate(res):
        if check == "Q1..Q4 = FY" and not ok:
            _, y, k = period.split()
            if (int(y), stmt_of[k]) in restated_stmts:
                res[n] = ("Q1..Q4 = FY (restated year)", period,
                          f"{detail}: other lines of this statement were restated for this year", True)
                continue
            # a derived line (e.g. operating expenses = gross profit - operating income) whose
            # inputs were found restated for that year further down the list
            src = fys.at[int(y), k] if k in fys and int(y) in fys.index else None
            ops = set(re.findall(r"[a-z][a-z0-9_]*", src or "")) if " " in (src or "") else set()
            if ops and any((int(y), o) in restated | mixed for o in ops):
                res[n] = ("Q1..Q4 = FY (restated year)", period, f"{detail}: derived from restated inputs", True)
            elif ops and any((int(y), o) in disagree for o in ops):
                res[n] = ("Q1..Q4 = FY (company's filings disagree)", period,
                          f"{detail}: derived from figures whose quarters and year disagree in the company's filings", True)

    # gross profit
    for ptype in ("FY", "Q"):
        w = reported(fin, ptype)
        if {"revenue", "cost_of_revenue", "gross_profit"} <= set(w.columns):
            for idx, r in w.dropna(subset=["revenue", "cost_of_revenue", "gross_profit"]).iterrows():
                d = pct(r.revenue - r.cost_of_revenue, r.gross_profit)
                if d is None:
                    continue
                res.append(("GP = Rev - COGS", f"{ptype} {idx}", f"{d:+.3f}%", abs(d) < 0.1))

    # net income consistency. A line that a later filing restated (e.g. tax after an accounting
    # change, discontinued operations after a sale) next to a line that was not restated in the
    # data does not tie with the latest numbers, but does with the numbers as first reported:
    # that is reported as 'restated' (information), not as a failure.
    def ni_checks(r):
        ni = r.get("net_income")
        if pd.isna(ni):
            return {}
        z = lambda k: r.get(k) if pd.notna(r.get(k)) else 0   # noqa: E731
        out = {}
        # cash-flow statement starts from profit INCLUDING the minority owners' share
        if pd.notna(r.get("net_income_incl_nci")):
            out["NI (IS) = NI (cash flow)"] = (pct(ni + z("nci_income"), r.net_income_incl_nci), 1)
        if pd.notna(r.get("pretax_income")) and pd.notna(r.get("income_tax")):
            # below the tax line: sold-off businesses, extraordinary items and (for some
            # companies) income from minority stakes in other companies. Some companies deduct
            # the minority owners' share above the tax line (Oracle): then it ties to net income.
            base = r.pretax_income - r.income_tax + z("discontinued_ops") + z("extraordinary_items")
            target = r.net_income_incl_nci if pd.notna(r.get("net_income_incl_nci")) else ni + z("nci_income")
            # REITs show gains on property sales below the pretax line (still part of the result)
            bases = [base, base + z("equity_method_income")]
            bases += [b + z("real_estate_gains") for b in bases] if z("real_estate_gains") else []
            ds = [pct(b, t) for b in bases for t in (target, ni)]
            # the company's own subtotal "income from continuing operations": pretax - tax ties to it;
            # what lies between it and net income (REIT property-sale gains, ...) is not modelled
            if pd.notna(r.get("income_continuing_incl_nci")):
                b0 = r.pretax_income - r.income_tax
                b1 = [b0, b0 + z("equity_method_income")]
                b1 += [b + z("real_estate_gains") for b in b1] if z("real_estate_gains") else []
                ds += [pct(b, r.income_continuing_incl_nci) for b in b1]
            ds = [x for x in ds if x is not None]
            if ds:
                out["NI = pretax - tax"] = (min(ds, key=abs), 1)
        return out

    for ptype in ("FY", "Q"):
        w, wo = wide(fin, ptype), wide(fin, ptype, "original_value")
        for idx, r in w.iterrows():
            orig = ni_checks(wo.loc[idx]) if idx in wo.index else {}
            for name, (d, tol) in ni_checks(r).items():
                if d is None:
                    continue
                if abs(d) < tol:
                    res.append((name, f"{ptype} {idx}", f"{d:+.3f}%", True))
                elif name in orig and orig[name][0] is not None and abs(orig[name][0]) < tol:
                    res.append((name + " (restated)", f"{ptype} {idx}",
                                f"{d:+.3f}% with the latest numbers; ties as first reported "
                                f"({orig[name][0]:+.3f}%): one line was restated later, the other not", True))
                else:
                    res.append((name, f"{ptype} {idx}", f"{d:+.3f}%", False))

    # gaps
    # Periods before the company's first machine-readable 10-Q (or 10-K for years) cannot be missing:
    # small companies only started tagging their filings in 2011, a company that moved to the US
    # (Amcor) or was listed later (SPAC, spin-off) has older years only as comparatives, often without
    # quarters or a full statement. Counted from the end of the period that first filing covers.
    first_filing = {}
    for ptype, forms in (("FY", ("10-K", "10-KT", "20-F", "40-F")), ("Q", ("10-Q",))):
        r = conn.execute(f"SELECT MIN(e) FROM (SELECT MAX(end_date) e FROM xbrl_facts WHERE cik=? AND form IN "
                         f"({','.join('?' * len(forms))}) AND taxonomy='us-gaap' AND start_date<>'' GROUP BY accn)",
                         (cik, *forms)).fetchone() if conn is not None else None
        first_filing[ptype] = r[0] if r and r[0] else None
    ends = {}
    for r in fin[["period_type", "fiscal_year", "fiscal_quarter", "end_date"]].drop_duplicates().itertuples():
        idx = r.fiscal_year if r.period_type == "FY" else (r.fiscal_year, r.fiscal_quarter)
        ends.setdefault((r.period_type, idx), r.end_date)

    def _before_first(ptype, idx):
        f = first_filing.get(ptype)
        if f is None:
            return False
        e = ends.get((ptype, idx if ptype == "Q" else (idx[0] if isinstance(idx, tuple) else idx)))
        if e is None:     # the whole period is missing: judge by its year
            y = idx[0] if isinstance(idx, tuple) else idx
            return y < int(f[:4])
        return str(e)[:10] < str(pd.Timestamp(f) - pd.Timedelta(days=10))[:10]

    for ptype, start in (("FY", 2009), ("Q", 2010)):
        w = wide(fin, ptype)
        if w.empty:
            continue      # no quarter reported yet (a new spin-off before its first full quarter)
        if ptype == "Q" and not any(c in w and w[c].notna().any() for c in ("revenue", "net_income", "operating_cf")):
            continue      # reports once a year (foreign filer on 20-F / 40-F, e.g. ASML): no quarters exist
        bank = "deposits" in fin.item.values      # banks have no operating income line
        first_of = {c: (w[c].dropna().index.min() if c in w and w[c].notna().any() else None)
                    for c in ("net_income", "revenue")}
        for k in ("revenue", "net_income", "operating_income", "operating_cf", "eps_diluted"):
            if bank and k == "operating_income":
                continue
            if k not in w:
                # many companies (insurers, REITs, oil majors, ...) present no operating income
                # line at all; that is a presentation choice, not missing data
                if k != "operating_income":
                    res.append(("no gaps", f"{ptype} {k}", "item missing", False))
                continue
            s = w[k]
            s = s[s.index.get_level_values(0) >= start]
            # periods before the company's reported history starts (before its IPO, or before the
            # filings that are available) cannot be missing: count from its first reported period
            first = [i for i in (first_of.get(c) for c in ("net_income", "revenue")) if i is not None]
            first = min(first) if first else None
            if first is not None:
                s = s[[i >= first for i in s.index]]
            if k == "operating_income" and s.notna().any():
                # a company that stopped showing an operating income line (J&J 2015, GE 2013)
                # changed its presentation: only gaps before its last reported figure count
                s = s[[i <= s.dropna().index.max() for i in s.index]]
            missing = list(s[s.isna()].index)
            if ptype == "Q":
                yrs = sorted(set(s.index.get_level_values(0)))
                have = set(s.dropna().index)
                last = max(have) if have else None
                missing = [(y, q) for y in yrs for q in (1, 2, 3, 4)
                           if (y, q) not in have and last and (y, q) < last
                           and (first is None or (y, q) >= first)]
            missing = [m for m in missing if not _before_first(ptype, m)]
            res.append(("no gaps", f"{ptype} {k} since {start}",
                        f"missing: {missing}" if missing else "complete", not missing))
    return pd.DataFrame(res, columns=["check", "period", "detail", "ok"])


# --------------------------------------------------------------------------- Yahoo cross-check
def yahoo_check(fin, ticker):
    import yfinance as yf      # only needed for this optional cross-check
    t = yf.Ticker(ticker)
    stmts = {"income_stmt": t.income_stmt, "balance_sheet": t.balance_sheet, "cashflow": t.cashflow}
    fy = fin[fin.period_type == "FY"]
    rows = []
    for k, (st, row, sign) in YAHOO_MAP.items():
        df = stmts[st]
        if df is not None and " | " in row:            # first of several Yahoo rows that has numbers
            opts = row.split(" | ")
            row = next((r for r in opts if r in df.index and df.loc[r].notna().any()), opts[-1])
        if df is not None and " - " in row:            # difference of two Yahoo rows
            a, b = row.split(" - ")
            if a in df.index:
                df = df.copy()
                df.loc[row] = df.loc[a] - (df.loc[b].fillna(0) if b in df.index else 0)
        if df is None or df.empty or row not in df.index:
            rows.append((k, None, None, None, None, f"Yahoo has no '{row}'"))
            continue
        for col in df.columns:
            yv = df.at[row, col]
            if pd.isna(yv):
                continue
            end = pd.Timestamp(col)
            ours = fy[(fy.item == k) & ((pd.to_datetime(fy.end_date) - end).abs() <= pd.Timedelta(days=7))]
            ov = ours.value.iloc[0] if len(ours) else None
            d = pct(ov, sign * yv) if ov is not None else None
            rows.append((k, end.strftime("%Y-%m-%d"), ov, sign * float(yv), d, ""))
    return pd.DataFrame(rows, columns=["item", "period_end", "ours", "yahoo", "diff_%", "note"])


# --------------------------------------------------------------------------- segments
def segment_check(conn, cik):
    """Per breakdown: do the main-split members add up to total revenue / operating income?"""
    seg = pd.read_sql_query("SELECT * FROM segment_financials WHERE cik=? AND in_partition=1 "
                            "AND period_type IN ('FY','Q')", conn, params=(cik,))
    tot = pd.read_sql_query("SELECT item, period_type, end_date, value AS total FROM financials "
                            "WHERE cik=? AND item IN ('revenue','operating_income')", conn, params=(cik,))
    if seg.empty:
        return pd.DataFrame(), pd.DataFrame()
    sums = seg.groupby(["breakdown", "item", "period_type", "fiscal_year", "fiscal_quarter", "end_date"],
                       dropna=False).value.agg(["sum", "count"]).reset_index()
    m = sums.merge(tot, on=["item", "period_type", "end_date"], how="left")
    m["diff_%"] = (m["sum"] - m.total) / m.total.abs() * 100
    summ = m.groupby(["breakdown", "item", "period_type"]).agg(
        periods=("diff_%", "count"),
        years=("fiscal_year", lambda x: f"{int(x.min())}-{int(x.max())}"),
        within_0_5pct=("diff_%", lambda x: int((x.abs() <= 0.5).sum())),
        max_abs_diff_pct=("diff_%", lambda x: round(x.abs().max(), 2)),
    ).reset_index()
    return summ, m


def segment_latest(conn, cik):
    return pd.read_sql_query(
        """SELECT breakdown, member_label, item, fiscal_year, ROUND(value/1e9, 2) AS value_b, in_partition
           FROM segment_financials s WHERE cik=? AND period_type='FY'
             AND fiscal_year=(SELECT MAX(fiscal_year) FROM segment_financials WHERE cik=s.cik
                              AND breakdown=s.breakdown AND period_type='FY')
           ORDER BY breakdown, item DESC, value DESC""", conn, params=(cik,))


def segment_coverage(conn, cik):
    return pd.read_sql_query(
        """SELECT breakdown, member_label, MIN(fiscal_year) AS first_fy, MAX(fiscal_year) AS last_fy,
                  COUNT(*) AS years, GROUP_CONCAT(DISTINCT source) AS tags
           FROM segment_financials WHERE cik=? AND period_type='FY' AND item='revenue'
           GROUP BY breakdown, member_key ORDER BY breakdown, last_fy DESC, first_fy""",
        conn, params=(cik,))


# --------------------------------------------------------------------------- report
def fmt_b(v, item):
    if pd.isna(v):
        return "-"
    return f"{v:.2f}" if item.startswith("eps") else f"{v / 1e9:,.1f}"


def ten_year_table(fins):
    items = ["revenue", "operating_income", "net_income", "fcf", "eps_diluted"]
    lines = []
    for t, fin in fins.items():
        w = wide(fin, "FY")
        w = w[w.index >= w.index.max() - 9]
        lines.append(f"\n**{t}** ($B, EPS in $, split-adjusted)\n")
        lines.append("| FY | " + " | ".join(items) + " |")
        lines.append("|---" * (len(items) + 1) + "|")
        for y, r in w.iterrows():
            lines.append(f"| {y} | " + " | ".join(fmt_b(r.get(i), i) for i in items) + " |")
    return "\n".join(lines)


def latest_valuation(conn, ticker):
    r = pd.read_sql_query("SELECT * FROM valuation_daily WHERE ticker=? ORDER BY date DESC LIMIT 1",
                          conn, params=(ticker,))
    return r.iloc[0] if len(r) else None


def main(tickers):
    conn = db.connect()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    out = [f"# Validation report ({date.today()})\n"]
    fins, summary = {}, []
    for t in tickers:
        t = t.upper()
        cik, fin = load(conn, t)
        fins[t] = fin
        out.append(f"\n## {t} (CIK {cik})\n\n### Coverage\n")
        out.append(coverage(fin).to_markdown(index=False))

        ch = checks(conn, cik, fin)
        out.append("\n\n### Checks\n")
        agg = ch.groupby("check").ok.agg(passed="sum", total="count").reset_index()
        out.append(agg.to_markdown(index=False))
        fails = ch[~ch.ok]
        out.append("\n\nFailures:\n")
        out.append(fails.to_markdown(index=False) if len(fails) else "none")

        yc = yahoo_check(fin, t)
        out.append("\n\n### Cross-check vs Yahoo Finance (annual)\n")
        out.append(yc.to_markdown(index=False, floatfmt=",.4g"))
        big = yc[yc["diff_%"].abs() > 1]
        summary.append((t, agg, fails, yc, big))

        summ, _ = segment_check(conn, cik)
        out.append("\n\n### Segments: members of the main split vs company total\n")
        out.append(summ.to_markdown(index=False) if len(summ) else "no segment data")
        out.append("\n\nOperating income by segment usually does NOT add up to the total: companies keep "
                   "some costs unallocated (corporate, R&D, amortization).\n")
        out.append("\n### Segments: latest fiscal year ($B)\n")
        out.append(segment_latest(conn, cik).to_markdown(index=False))
        out.append("\n\n### Segments: coverage (annual revenue)\n")
        out.append(segment_coverage(conn, cik).to_markdown(index=False))
        summary[-1] = summary[-1] + (summ,)

        v = latest_valuation(conn, t)
        if v is not None:
            out.append(f"\n\n### Latest valuation ({v.date})\n")
            out.append(v.to_frame().T.to_markdown(index=False, floatfmt=",.4g"))

    out.append("\n\n## 10-year side by side\n")
    out.append(ten_year_table(fins))
    REPORT.write_text("\n".join(out), encoding="utf-8")

    for t, agg, fails, yc, big, *rest in summary:
        print(f"\n=== {t} ===")
        print(agg.to_string(index=False))
        if len(fails):
            print("FAILURES:")
            print(fails.to_string(index=False))
        matched = yc["diff_%"].notna().sum()
        print(f"Yahoo cross-check: {matched} values compared, {len(big)} differ by more than 1%")
        if len(big):
            print(big.to_string(index=False))
        if rest and len(rest[0]):
            print("Segment sums vs total:")
            print(rest[0].to_string(index=False))
    print(f"\nFull report: {REPORT}")


if __name__ == "__main__":
    main(sys.argv[1:] or ["AAPL", "MSFT"])
