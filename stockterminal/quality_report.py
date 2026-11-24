"""Data-quality report for many companies at once (written at the end of every batch load).

Usage:
    python -m stockterminal.quality_report                 # every loaded company
    python -m stockterminal.quality_report AAPL MSFT       # only these
    python -m stockterminal.quality_report --out data/reports/my_report.md AAPL MSFT

Uses what every load already stores (data_quality, financials, valuation, analyst data, segments);
no downloads. Per company: score, failing checks, template (normal / bank / insurer / REIT), first
fiscal year, and anomalies (no segments, no analyst data, blank or absurd multiples, stale prices).
"""
import argparse
import json
import re
from collections import Counter
from datetime import date, timedelta

from . import db
from .config import DATA_DIR
from .mapping import INDUSTRY_SIC

DEFAULT_OUT = DATA_DIR / "reports" / "quality_report.md"


def template_of(conn, cik, sic):
    items = {i for (i,) in conn.execute("SELECT DISTINCT item FROM financials WHERE cik=? "
                                        "AND item IN ('deposits','net_interest_income')", (cik,))}
    if {"deposits", "net_interest_income"} <= items:
        return "bank"
    return next((k for k, pat in INDUSTRY_SIC.items() if re.match(pat, str(sic or ""))), "normal")


def company_row(conn, ticker):
    c = conn.execute("SELECT cik, name, sic FROM companies WHERE ticker=?", (ticker,)).fetchone()
    if not c:
        return dict(ticker=ticker, status="not loaded", anomalies=["not in database"])
    cik, name, sic = c
    r = dict(ticker=ticker, name=name or "", status="ok", anomalies=[], issues=[])
    nfin = conn.execute("SELECT COUNT(*) FROM financials WHERE cik=?", (cik,)).fetchone()[0]
    if not nfin:
        r.update(status="no financials", anomalies=["no SEC financial statements (fund, ETF or foreign filer?)"])
        return r
    r["template"] = template_of(conn, cik, sic)
    q = conn.execute("SELECT score, checks, failed, details FROM data_quality WHERE cik=?", (cik,)).fetchone()
    if q:
        r.update(score=q[0], checks=q[1], failed=q[2])
        det = json.loads(q[3] or "{}")
        r["issues"] = [(i["text"], i.get("count", 1), (i.get("where") or [""])[0]) for i in det.get("issues", [])]
        r["first_fy"] = (det.get("history") or {}).get("first_fy")
    else:
        r["anomalies"].append("no quality score")
    a = r["anomalies"]
    if r.get("first_fy") and r["first_fy"] > 2010:
        a.append(f"history starts FY{r['first_fy']}")
    if not conn.execute("SELECT 1 FROM segment_financials WHERE cik=? LIMIT 1", (cik,)).fetchone():
        a.append("no segments")
    if not conn.execute("SELECT 1 FROM analyst_estimates WHERE ticker=? LIMIT 1", (ticker,)).fetchone():
        a.append("no analyst data")
    v = conn.execute("SELECT date, market_cap, enterprise_value, pe_ttm, ev_ebitda_ttm, ps_ttm, pb "
                     "FROM valuation_daily WHERE ticker=? ORDER BY date DESC LIMIT 1", (ticker,)).fetchone()
    if not v:
        a.append("no valuation")
        return r
    d, mcap, ev, pe, ev_ebitda, ps, pb = v
    r["market_cap_b"] = mcap / 1e9 if mcap else None
    if d < (date.today() - timedelta(days=7)).isoformat():
        a.append(f"valuation stale ({d})")
    if not mcap:
        a.append("market cap blank")
    if pe is not None and (pe > 500 or pe < 0):
        a.append(f"P/E {pe:.0f}")
    if r["template"] == "normal":
        if ev is not None and ev < 0:
            a.append("EV negative")
        if ev_ebitda is None:
            a.append("EV/EBITDA blank")
        elif ev_ebitda > 300:
            a.append(f"EV/EBITDA {ev_ebitda:.0f}")
    if ps is None:
        a.append("P/S blank")
    return r


def build(conn, tickers, title="Data quality", extra=None):
    rows = [company_row(conn, t) for t in tickers]
    scored = [r for r in rows if r.get("score") is not None]
    out = [f"# {title} ({date.today()})\n"]
    if extra:
        out.append(extra + "\n")
    if scored:
        s = sorted(r["score"] for r in scored)
        out.append(f"{len(scored)} of {len(rows)} companies scored. Median score {s[len(s) // 2]:.1f}%, "
                   f"{sum(x >= 99 for x in s)} at 99% or more, {sum(x < 95 for x in s)} below 95%.\n")
    not_ok = [r for r in rows if r["status"] != "ok"]
    if not_ok:
        out.append("**Not loaded / no financial statements:** " +
                   ", ".join(f"{r['ticker']} ({r['status']})" for r in not_ok) + "\n")
    # problems across companies
    by_issue = Counter()
    who = {}
    for r in scored:
        for text, n, _ in r["issues"]:
            by_issue[text] += 1
            who.setdefault(text, []).append(r["ticker"])
    anom = Counter(re.sub(r"\s*[\d(].*$", "", x) for r in rows for x in r["anomalies"])
    if by_issue or anom:
        out.append("## Most common problems\n")
        out.append("| Problem | Companies | Examples |\n|---|---|---|")
        for text, n in by_issue.most_common():
            out.append(f"| {text} | {n} | {', '.join(who[text][:8])} |")
        for text, n in anom.most_common():
            ex = [r["ticker"] for r in rows if any(x.startswith(text) for x in r["anomalies"])][:8]
            out.append(f"| {text} | {n} | {', '.join(ex)} |")
        out.append("")
    out.append("## Per company (lowest score first)\n")
    out.append("| Ticker | Name | Template | Score | Passed/Checks | First FY | Failing checks (example) | Anomalies |")
    out.append("|---|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda r: (r.get("score") is not None, r.get("score") or 0)):
        fails = "; ".join(f"{t} x{n}" + (f" (e.g. {w})" if w else "") for t, n, w in r.get("issues", []))
        sc = f"{r['score']:.1f}" if r.get("score") is not None else "-"
        pc = f"{r['checks'] - r['failed']}/{r['checks']}" if r.get("checks") else "-"
        out.append(f"| {r['ticker']} | {r.get('name', '')[:30]} | {r.get('template', '-')} | {sc} | {pc} | "
                   f"{r.get('first_fy') or '-'} | {fails.replace('|', '/') or '-'} | {', '.join(r['anomalies']) or '-'} |")
    return "\n".join(out) + "\n", rows


def write(conn, tickers, path=DEFAULT_OUT, **kw):
    text, rows = build(conn, tickers, **kw)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path, rows


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("tickers", nargs="*")
    p.add_argument("--out", default=None)
    a = p.parse_args(argv)
    conn = db.connect()
    tickers = [t.upper() for t in a.tickers] or [t for (t,) in conn.execute("SELECT ticker FROM companies ORDER BY ticker")]
    from pathlib import Path
    path, rows = write(conn, tickers, Path(a.out) if a.out else DEFAULT_OUT)
    print(f"{len(rows)} companies -> {path}")


if __name__ == "__main__":
    main()
