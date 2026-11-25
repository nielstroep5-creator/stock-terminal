"""Local web API for the Stock Terminal app (served only on 127.0.0.1)."""
import datetime as dt
import difflib
import json
import re
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import alerts, allocation, analysis, costs, db, quality, dividends, etf, events, market, own, pf_history, portfolio, quote, screener, sectors, settings, tax, trading212, watchlist, xray
from ..config import DATA_DIR, DB_PATH, ROOT, module_cmd
from ..mapping import INDUSTRY_SIC, ITEMS
from ..metrics import NEEDS_POSITIVE_BASE
from . import estimates

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Stock Terminal")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
db.connect().close()  # apply schema migrations / name cleanup once at startup
try:
    alerts.migrate_settings()  # per-stock alert settings of before 2026-11-18 -> one key per stock (alerts.py)
except Exception:  # noqa: BLE001  the old keys are still read as they are
    pass


def _catch_up():
    """If the daily update (normally the 07:00 scheduled task) has not run today, run it now in the
    background, a little after start so the first page loads first. daily.py's lock file prevents two runs."""
    from .. import daily
    time.sleep(20)
    if daily.due_today():
        subprocess.Popen(module_cmd("stockterminal.daily"), cwd=ROOT,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


_ticker_list_lock = threading.Lock()


def _ensure_ticker_list():
    """Search needs the SEC's list of every company (table tickers). A fresh install has none until something
    loads it, so search found nothing ("NVDA: not found"). Load it now when it is missing (one ~1 MB download)."""
    if not _ticker_list_lock.acquire(blocking=False):
        return
    try:
        conn = db.connect()
        try:
            if conn.execute("SELECT 1 FROM tickers LIMIT 1").fetchone() is None:
                from .. import sec
                sec.load_tickers(conn)
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 - offline or SEC refused: tried again at the next start or email change
        print(f"SEC company list: {e}")
    finally:
        _ticker_list_lock.release()


@app.on_event("startup")
def _start_catch_up():
    threading.Thread(target=_ensure_ticker_list, daemon=True).start()
    threading.Thread(target=_catch_up, daemon=True).start()
    threading.Thread(target=_ratios_upgrade, daemon=True).start()


def _ratios_upgrade():
    """Data from a starter pack or copy made by older code gets its ratios recalculated once (metrics.py)."""
    try:
        from .. import metrics
        if metrics.recalc_if_outdated():
            screener._cache["rows"] = None          # the Screener / Sectors table is built again
    except Exception as e:  # noqa: BLE001
        print(f"ratios upgrade: {e}")

# --------------------------------------------------------------------------- catalogs
METRICS = [
    # (key, label, group, format)
    ("revenue_yoy", "Revenue growth", "Growth", "pct"),
    ("gross_profit_yoy", "Gross profit growth", "Growth", "pct"),
    ("operating_income_yoy", "Operating income growth", "Growth", "pct"),
    ("ebitda_yoy", "EBITDA growth", "Growth", "pct"),
    ("net_income_yoy", "Net income growth", "Growth", "pct"),
    ("eps_diluted_yoy", "EPS growth", "Growth", "pct"),
    ("operating_cf_yoy", "Operating cash flow growth", "Growth", "pct"),
    ("fcf_yoy", "Free cash flow growth", "Growth", "pct"),
    ("dps_yoy", "Dividend per share growth", "Growth", "pct"),
    ("shares_diluted_yoy", "Share count change", "Growth", "pct"),
    ("revenue_cagr_3y", "Revenue CAGR 3Y", "Growth (CAGR)", "pct"),
    ("revenue_cagr_5y", "Revenue CAGR 5Y", "Growth (CAGR)", "pct"),
    ("revenue_cagr_10y", "Revenue CAGR 10Y", "Growth (CAGR)", "pct"),
    ("operating_income_cagr_5y", "Operating income CAGR 5Y", "Growth (CAGR)", "pct"),
    ("net_income_cagr_5y", "Net income CAGR 5Y", "Growth (CAGR)", "pct"),
    ("eps_diluted_cagr_3y", "EPS CAGR 3Y", "Growth (CAGR)", "pct"),
    ("eps_diluted_cagr_5y", "EPS CAGR 5Y", "Growth (CAGR)", "pct"),
    ("eps_diluted_cagr_10y", "EPS CAGR 10Y", "Growth (CAGR)", "pct"),
    ("fcf_cagr_5y", "FCF CAGR 5Y", "Growth (CAGR)", "pct"),
    ("dps_cagr_5y", "Dividend CAGR 5Y", "Growth (CAGR)", "pct"),
    ("gross_margin", "Gross margin", "Margins", "pct"),
    ("operating_margin", "Operating margin", "Margins", "pct"),
    ("ebitda_margin", "EBITDA margin", "Margins", "pct"),
    ("net_margin", "Net margin", "Margins", "pct"),
    ("fcf_margin", "FCF margin", "Margins", "pct"),
    ("rnd_to_revenue", "R&D / revenue", "Margins", "pct"),
    ("sbc_to_revenue", "Stock comp / revenue", "Margins", "pct"),
    ("capex_to_revenue", "Capex / revenue", "Margins", "pct"),
    ("effective_tax_rate", "Effective tax rate", "Margins", "pct"),
    ("roe", "Return on equity (ROE)", "Returns", "pct"),
    ("roa", "Return on assets (ROA)", "Returns", "pct"),
    ("roic", "Return on invested capital (ROIC)", "Returns", "pct"),
    ("net_cash", "Net cash (debt)", "Balance sheet", "usd"),
    ("debt_to_equity", "Debt / equity", "Balance sheet", "x"),
    ("current_ratio", "Current ratio", "Balance sheet", "x"),
    ("fcf_per_share", "FCF per share", "Per share", "usdps"),
    ("revenue_per_share", "Revenue per share", "Per share", "usdps"),
    ("book_value_per_share", "Book value per share", "Per share", "usdps"),
    ("capital_returned", "Buybacks + dividends", "Capital return", "usd"),
    ("capital_returned_to_fcf", "Capital returned / FCF", "Capital return", "pct"),
    ("payout_ratio", "Dividend payout ratio", "Capital return", "pct"),
    # banks only (computed only when the company is a bank)
    ("net_interest_income_yoy", "Net interest income growth", "Bank", "pct"),
    ("noninterest_income_yoy", "Fee income growth", "Bank", "pct"),
    ("pre_provision_profit_yoy", "Pre-provision profit growth", "Bank", "pct"),
    ("efficiency_ratio", "Efficiency ratio (costs / revenue)", "Bank", "pct"),
    ("nii_share_of_revenue", "Net interest income / revenue", "Bank", "pct"),
    ("nii_to_assets", "Net interest income / assets", "Bank", "pct"),
    ("provision_to_loans", "Provisions / loans", "Bank", "pct"),
    ("loans_to_deposits", "Loans / deposits", "Bank", "pct"),
    ("equity_to_assets", "Equity / assets", "Bank", "pct"),
    ("rotce", "Return on tangible equity (ROTCE)", "Bank", "pct"),
    ("tangible_book_value_per_share", "Tangible book value per share", "Bank", "usdps"),
    # insurers only
    ("loss_ratio", "Loss ratio (claims / premiums)", "Insurance", "pct"),
    ("premiums_earned_yoy", "Premiums earned growth", "Insurance", "pct"),
    ("investment_income_to_revenue", "Investment income / revenue", "Insurance", "pct"),
    # REITs only
    ("ffo_yoy", "FFO growth", "REIT", "pct"),
    ("ffo_per_share_yoy", "FFO per share growth", "REIT", "pct"),
    ("ffo_margin", "FFO / revenue", "REIT", "pct"),
    ("ffo_payout_ratio", "Dividends / FFO", "REIT", "pct"),
]
VALUATION = [
    ("pe_ttm", "P/E", "x"), ("ps_ttm", "P/S", "x"), ("pfcf_ttm", "P/FCF", "x"),
    ("ev_ebitda_ttm", "EV/EBITDA", "x"), ("ev_sales_ttm", "EV/Sales", "x"), ("pb", "P/B", "x"),
    ("earnings_yield", "Earnings yield", "pct"), ("fcf_yield", "FCF yield", "pct"),
    ("dividend_yield", "Dividend yield", "pct"), ("market_cap", "Market cap", "usd"),
    ("enterprise_value", "Enterprise value", "usd"), ("p_ffo_ttm", "P/FFO (REIT)", "x"),
]
ANALYST_FIELDS = [
    ("forward_pe", "Forward P/E", "x"), ("target_upside", "Price target upside", "pct"),
    ("rev_growth_next", "Est. revenue growth, next FY", "pct"), ("eps_growth_next", "Est. EPS growth, next FY", "pct"),
    ("peg", "PEG ratio", "x"), ("rating_mean", "Analyst rating (1 = strong buy, 5 = strong sell)", "num"),
]
KIND_FMT = {"flow": "usd", "instant": "usd", "shares": "shares", "shares_instant": "shares",
            "eps": "usdps", "per_share": "usdps"}
BOLD = {"net_interest_income", "pre_provision_profit", "deposits", "loans", "revenue", "gross_profit", "operating_income", "net_income", "eps_diluted", "total_assets",
        "total_liabilities", "total_equity", "operating_cf", "fcf", "total_debt", "cash_and_sti"}
STATEMENT_NAMES = {"income": "Income statement", "balance": "Balance sheet", "cashflow": "Cash flow"}
BREAKDOWNS = {"product": "Products & services", "business_segment": "Business segments",
              "geography": "Geography"}


def q(sql, params=()):
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(sql, params)]
    finally:
        c.close()


def company(ticker):
    rows = q("SELECT * FROM companies WHERE ticker=?", (ticker.upper(),))
    if rows:
        rows[0]["name"] = rows[0].get("display_name") or db.readable_name(rows[0]["name"])
        rows[0]["sector"] = db.sector_of(rows[0].get("sector"), rows[0].get("sic"))
    return rows[0] if rows else None


def cik_of(ticker):
    c = company(ticker)
    if not c:
        raise HTTPException(404, f"{ticker} is not loaded")
    return c["cik"]


def cq_date(cq):
    """'2026Q3' -> '2026-09-30' (calendar quarter end)."""
    y, n = int(cq[:4]), int(cq[-1])
    return f"{y}-{['03-31', '06-30', '09-30', '12-31'][n - 1]}"


def period_label(ptype, fy, fq):
    if ptype == "FY":
        return f"FY{fy}"
    if fq and fq >= 5:      # a quarter of a transition period (change of fiscal year: Ferguson 2025, FedEx 2026)
        return f"{'TTM ' if ptype == 'TTM' else ''}Q{fq - 4} transition FY{str(fy)[2:]}"
    if ptype == "Q":
        return f"Q{fq} FY{str(fy)[2:]}"
    return f"TTM Q{fq} FY{str(fy)[2:]}"


# --------------------------------------------------------------------------- data-quality flags
_flag_cache = {}


def flags_for(cik):
    """{(item, ptype, fy, fq): message} from the validation checks (cached per database change)."""
    stamp = DB_PATH.stat().st_mtime
    hit = _flag_cache.get(cik)
    if hit and hit[0] == stamp:
        return hit[1]
    from .. import validate
    c = db.connect()
    fin = pd.read_sql_query("SELECT * FROM financials WHERE cik=?", c, params=(cik,))
    flags = {}
    if not fin.empty:
        ch = validate.checks(c, cik, fin)
        for r in ch[~ch.ok].itertuples():
            m = re.match(r"FY (\d{4}) (\w+)", r.period)
            if r.check.startswith("Q1..Q4") and m:
                y, item = int(m.group(1)), m.group(2)
                msg = f"Quarters do not add up to the full year ({r.detail}), usually a restatement"
                flags[(item, "FY", y, None)] = msg
                for k in (1, 2, 3, 4):
                    flags[(item, "Q", y, k)] = msg
            m2 = re.match(r"(FY|Q) \(?(\d{4})(?:, (\d))?", r.period)
            if m2 and not r.check.startswith("Q1..Q4"):
                ptype, y, fq = m2.group(1), int(m2.group(2)), m2.group(3)
                items = {"GP = Rev - COGS": ["gross_profit"], "NI = pretax - tax": ["net_income"],
                         "NI (IS) = NI (cash flow)": ["net_income"], "no gaps": []}.get(
                    r.check, ["total_assets", "total_liabilities", "total_equity"])
                for it in items:
                    flags[(it, ptype, y, int(fq) if fq else None)] = f"Check '{r.check}' failed ({r.detail})"
        _, m = validate.segment_check(c, cik)
        if len(m):
            for r in m[(m.item == "revenue") & (m["diff_%"].abs() > 0.5)].to_dict("records"):
                fq = None if pd.isna(r["fiscal_quarter"]) else int(r["fiscal_quarter"])
                flags[(f"seg:{r['breakdown']}", r["period_type"], int(r["fiscal_year"]), fq)] = (
                    f"Segments add up to {r['sum'] / 1e9:,.1f}B vs total revenue {r['total'] / 1e9:,.1f}B "
                    f"({r['diff_%']:+.1f}%), usually a reorganisation or unallocated corporate revenue")
    c.close()
    _flag_cache[cik] = (stamp, flags)
    return flags


# --------------------------------------------------------------------------- search
_tickers = {"stamp": None, "rows": []}


def _ticker_rows():
    stamp = DB_PATH.stat().st_mtime
    if _tickers["stamp"] != stamp:
        rows = q("SELECT ticker, name, cik FROM tickers")
        sec_listed = {r["ticker"] for r in rows}
        # loaded = read from the SEC (companies), or is a fund / non-SEC ticker with prices. A company in the SEC list with prices
        # only (a load that could not reach the SEC) counts as not loaded, so opening it loads it properly.
        loaded = {r["ticker"] for r in q("SELECT ticker FROM companies WHERE ticker IS NOT NULL")} | \
                 {r["ticker"] for r in q("SELECT ticker FROM etfs")} | \
                 ({r["ticker"] for r in q("SELECT DISTINCT ticker FROM prices")} - sec_listed)
        nice = {r["ticker"]: r["display_name"] for r in q("SELECT ticker, display_name FROM companies "
                                                         "WHERE display_name IS NOT NULL AND ticker IS NOT NULL")}
        for r in rows:
            r["name"] = nice.get(r["ticker"]) or db.readable_name(r["name"])
            r["loaded"] = r["ticker"] in loaded
            r["_n"] = (r["name"] or "").lower()
        known = {r["ticker"] for r in rows}
        etfs = {r["ticker"]: r["name"] for r in q("SELECT ticker, name FROM etfs")}
        for t in loaded - known:               # e.g. ETFs loaded with prices only
            rows.append(dict(ticker=t, name=etfs.get(t) or t, cik=None, loaded=True, _n=(etfs.get(t) or t).lower()))
            known.add(t)
        for t, n in etf.POPULAR.items():        # well-known funds, findable by name before they are loaded
            if t not in known:
                rows.append(dict(ticker=t, name=n, cik=None, loaded=False, _n=n.lower(), popular=True))
        for r in rows:
            r["etf"] = r["ticker"] in etfs or bool(r.get("popular"))
            if etfs.get(r["ticker"]):      # Yahoo's fund name reads better than the SEC's upper case
                r["name"] = etfs[r["ticker"]]
                r["_n"] = r["name"].lower()
        _tickers.update(stamp=stamp, rows=rows)
    return _tickers["rows"]


@app.get("/api/find")
def find(q: str = "", limit: int = 12):
    return _search(q, limit)


_SUFFIX = re.compile(r"[,.]|\b(inc|corp|corporation|co|company|ltd|limited|plc|holdings?|group|the|n\.?v|s\.?a|llc|lp)\b")


# brand names people type that are not in the company's official name
ALIASES = {"google": "GOOGL", "youtube": "GOOGL", "facebook": "META", "instagram": "META", "whatsapp": "META",
           "jp morgan": "JPM", "jpmorgan": "JPM", "coca cola": "KO", "coke": "KO", "pepsi": "PEP", "walmart": "WMT",
           "tsmc": "TSM", "berkshire": "BRK-B", "disney": "DIS", "mcdonalds": "MCD", "exxon": "XOM",
           "j&j": "JNJ", "johnson and johnson": "JNJ", "p&g": "PG", "amd": "AMD", "intel": "INTC", "tesla": "TSLA"}


def _core_name(n):
    return " ".join(_SUFFIX.sub(" ", n).split())


def _search(text, limit):
    text = text.strip()
    rows = _ticker_rows()
    if not text:
        return [dict(ticker=r["ticker"], name=r["name"], loaded=True, etf=r["etf"]) for r in rows if r["loaded"]][:limit]
    t, low = text.upper(), text.lower()
    core = _core_name(low)
    m = re.fullmatch(r"([A-Z]{1,5})\s*[./\s]\s*([A-Z]{1,2})", t)
    if m and any(r["ticker"] == f"{m[1]}-{m[2]}" for r in rows):
        t = f"{m[1]}-{m[2]}"                     # BRK.B, BRK/B or "brk b": how other sites write BRK-B
        low = t.lower()
    scored = []
    name_hit = False
    alias = ALIASES.get(" ".join(low.replace("-", " ").replace("'", "").split()))
    for r in rows:
        s = 0
        if r["ticker"] == t:
            s = 100
        elif alias and r["ticker"] == alias:
            s = 95
        elif r["ticker"].startswith(t):
            s = 80 - len(r["ticker"])
        elif r["_n"].startswith(low):
            s = 70
        elif f" {low}" in f" {r['_n']}":
            s = 60
        elif low in r["_n"]:
            s = 45
        elif len(core) >= 4:
            # typos ("nvdia"): compare without words like "inc" that every name has ("apple inc" is not "people inc")
            ratio = difflib.SequenceMatcher(None, core, _core_name(r["_n"])[:len(core) + 3]).ratio()
            if ratio > 0.75:
                s = 30 * ratio
                name_hit = name_hit or ratio > 0.85          # a clear typo of a name ("nvdia")
        if s:
            name_hit = name_hit or 60 <= s <= 70 or s == 95   # the text is (the start of) a word in a name
            scored.append((s + (25 if r["loaded"] else 0), s, r))
    if any(base >= 60 for _, base, _ in scored):
        # real hits found: leave out the middle of a word ("apple" in "Pineapple") and the typo guesses
        scored = [x for x in scored if x[1] >= 60]
    scored = [(sc, r) for sc, _, r in scored]
    scored.sort(key=lambda x: -x[0])
    out = [dict(ticker=r["ticker"], name=r["name"], loaded=r["loaded"], etf=r["etf"]) for _, r in scored[:limit]]
    # offer an unknown ticker only when the text looks like one, not when it is part of a company name
    # (US tickers have at most 5 letters plus a share class: "GOOGLE" is a name, not a ticker)
    if re.fullmatch(r"[A-Z]{1,5}([.\-][A-Z]{1,2})?", t) and not name_hit and not any(o["ticker"] == t for o in out):
        out.append(dict(ticker=t, name=f"Not found. Load {t} as an ETF or fund (prices only)", loaded=False))
    return out


def _norm_ticker(ticker):
    """BRK.B -> BRK-B when that is the ticker we know (SEC and Yahoo write share classes with a dash)."""
    t = ticker.strip().upper()
    m = re.fullmatch(r"([A-Z]{1,5})\s*[./\s]\s*([A-Z]{1,2})", t)
    if m and any(r["ticker"] == f"{m[1]}-{m[2]}" for r in _ticker_rows()):
        return f"{m[1]}-{m[2]}"
    return t


# --------------------------------------------------------------------------- loading new tickers
_jobs = {}


def _run_job(ticker, module="stockterminal.fetch"):
    job = _jobs[ticker]
    cmd = module_cmd(module, ticker, unbuffered=True)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    p = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, creationflags=flags)
    for line in p.stdout:
        job["lines"].append(line.rstrip())
    p.wait()
    job["state"] = "done" if p.returncode == 0 else "error"
    job["finished"] = time.time()
    if job["state"] == "done" and module == "stockterminal.fetch":
        _share_list()


def _share_list():
    """A company was added: put it on the shared list on GitHub (see stockterminal/shared.py)."""
    from .. import shared
    conn = db.connect()
    try:
        shared.sync(conn, say=lambda m: None, load_missing=False)
    finally:
        conn.close()


@app.post("/api/client-error")
def client_error(body: dict):
    """An error on a page of the screen (it shows "Something went wrong" instead of a blank window):
    kept in data/logs/app_errors.log, newest at the bottom, so it can be looked up afterwards."""
    log = DATA_DIR / "logs" / "app_errors.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    if log.exists() and log.stat().st_size > 1_000_000:          # keep it small: start again after ~1 MB
        log.replace(log.with_suffix(".old.log"))
    with log.open("a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {str(body.get('page', ''))[:200]}\n"
                f"  {str(body.get('message', ''))[:1000]}\n{str(body.get('stack', ''))[:6000]}\n\n")
    return {"ok": True}


@app.exception_handler(Exception)
async def _server_error(request, exc):
    """Any request that fails: logged for "Report a problem" (stockterminal/report.py), then the usual 500."""
    from fastapi.responses import JSONResponse
    try:
        from .. import report
        report.log_error(f"{request.method} {request.url.path}", exc)
    except Exception:  # noqa: BLE001 - logging must never hide the real error
        pass
    return JSONResponse({"detail": f"{type(exc).__name__}: {str(exc)[:300]}"}, status_code=500)


@app.get("/api/report")
def problem_report(page: str = "", note: str = ""):
    """Settings > About > Report a problem: the report text to read, copy or open as a GitHub issue. Never sent by itself."""
    from .. import report
    return report.build(page or None, note or None)


@app.post("/api/load/{ticker}")
def load(ticker: str):
    t = _norm_ticker(ticker)
    if _jobs.get(t, {}).get("state") == "running":
        return _jobs[t]
    _jobs[t] = dict(ticker=t, state="running", lines=[], started=time.time())
    threading.Thread(target=_run_job, args=(t,), daemon=True).start()
    return _jobs[t]


_index_jobs = {}


@app.post("/api/load-index/{name}")
def load_index_start(name: str):
    """Home "Get started": load a list of companies in the background, the same job as "Load S&P 500.bat"
    (resumable: starting it again continues where it stopped)."""
    if name not in ("popular", "sp500"):
        raise HTTPException(404, "unknown list")
    j = _index_jobs.get(name)
    if not (j and j["proc"].poll() is None):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        args = module_cmd("stockterminal.load_index", name, *(["--no-backfill"] if name == "popular" else []), unbuffered=True)
        _index_jobs[name] = dict(proc=subprocess.Popen(args, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                       creationflags=flags), started=time.time())
    return load_index_status(name)


@app.get("/api/load-index/{name}")
def load_index_status(name: str):
    from .. import load_index as li
    try:
        tickers = li.index_tickers(name) if name in li.BUILTIN or (li.INDEX_DIR / f"{name}.csv").exists() else []
    except SystemExit:
        tickers = []
    counts = li.Progress(name).counts(tickers) if tickers else {}
    j = _index_jobs.get(name)
    done = sum(v for k, v in counts.items() if k not in ("todo", "failed"))
    return dict(name=name, running=bool(j and j["proc"].poll() is None), total=len(tickers), done=done,
                failed=counts.get("failed", 0))


@app.get("/api/job/{ticker}")
def job(ticker: str):
    return _jobs.get(_norm_ticker(ticker), dict(state="none", lines=[]))


# --------------------------------------------------------------------------- keeping a ticker up to date
_last_update = {}  # ticker -> the result of its last update (for a click that had to wait for it)
_updating = {}     # ticker -> lock, so two clicks / tabs never update the same ticker at once


@app.post("/api/update/{ticker}")
def update_one(ticker: str, force: int = 0):
    """force=1 (refresh button): live quote (cache bypassed), prices + daily valuation, analyst snapshot.
    force=0 (opening a page): only what is stale - prices older than the last trading day, analyst data
    older than 6 h. Both start the SEC check in the background when a new filing is due."""
    from concurrent.futures import ThreadPoolExecutor
    from .. import update as up
    t = ticker.upper()
    lock = _updating.setdefault(t, threading.Lock())
    if not lock.acquire(blocking=False):
        lock.acquire()              # someone else is updating: wait for it and report its result
        lock.release()
        # the refresh button pressed while the page's automatic check ran: that check may have skipped the
        # prices, so run the forced update now instead of reporting nothing (it looked like nothing happened)
        if force:
            return update_one(ticker, force)
        return {**_last_update.get(t, dict(ticker=t, steps={})), "waited": True, "sec": _sec_state(t)}
    try:
        t0 = time.time()
        qt = quote.get_quote(t, refresh=bool(force))
        conn = up.connect()
        try:
            do_prices = force or up.prices_due(conn, t, qt)
            do_analyst = bool(company(t)) and (force or up.analyst_due(conn, t))
            sec_due, sec_reason = up.sec_due(conn, t)
        finally:
            conn.close()

        def run(name, fn):
            c = up.connect()
            try:
                return name, up.step(c, t, name, lambda: fn(c, t))
            finally:
                c.close()
        jobs = ([("prices", up.update_prices)] if do_prices else []) + \
               ([("analyst", up.update_analyst)] if do_analyst else [])
        with ThreadPoolExecutor(max_workers=2) as ex:      # the two Yahoo sources in parallel
            steps = dict(ex.map(lambda j: run(*j), jobs))
        if sec_due and _jobs.get(t, {}).get("state") != "running":
            _jobs[t] = dict(ticker=t, state="running", lines=[], started=time.time(), kind="sec")
            threading.Thread(target=_run_job, args=(t, "stockterminal.update"), daemon=True).start()
        _last_update[t] = dict(ticker=t, steps=steps, seconds=round(time.time() - t0, 2))
        return dict(ticker=t, steps=steps, seconds=round(time.time() - t0, 2),
                    sec=dict(due=sec_due, reason=sec_reason, **_sec_state(t)))
    finally:
        lock.release()


def _sec_state(t):
    j = _jobs.get(t) or {}
    return dict(running=j.get("state") == "running" and j.get("kind") == "sec")


# --------------------------------------------------------------------------- company & prices
def latest_by_item(table, col, cik, ptype, keys):
    ph = ",".join("?" * len(keys))
    rows = q(f"SELECT {col} AS k, value, end_date FROM {table} t WHERE cik=? AND period_type=? AND {col} IN ({ph}) "
             f"AND end_date=(SELECT MAX(end_date) FROM {table} t2 WHERE t2.cik=t.cik AND t2.{col}=t.{col} "
             f"AND t2.period_type=t.period_type)", (cik, ptype, *keys))
    # an item the company stopped reporting keeps its last (old) value: show nothing rather than
    # a number from years ago next to this year's numbers (Berkshire's last tagged cash is from 2017)
    newest = q(f"SELECT MAX(end_date) d FROM {table} WHERE cik=? AND period_type=?", (cik, ptype))
    if newest and newest[0]["d"]:
        cutoff = (dt.date.fromisoformat(newest[0]["d"]) - dt.timedelta(days=400)).isoformat()
        rows = [r for r in rows if r["end_date"] >= cutoff and
                (r["k"] not in NEEDS_POSITIVE_BASE or r["end_date"] == newest[0]["d"])]
    return {r["k"]: r["value"] for r in rows}


# --------------------------------------------------------------------------- live (regular-session) price
# Multiples whose numerator is the price / market cap scale with price; yields scale inversely.
SCALE = ("close", "market_cap", "pe_ttm", "ps_ttm", "pfcf_ttm", "pb", "p_ffo_ttm")
INVERSE = ("earnings_yield", "fcf_yield", "dividend_yield")


def regular_price(qt):
    """(price, NY date, live?) of the REGULAR session from a quote. Never pre/post/overnight."""
    r = (qt or {}).get("regular") or {}
    if not r.get("price") or not r.get("time"):
        return None
    from ..update import last_trading_day
    return r["price"], last_trading_day(qt), qt.get("market_state") == "REGULAR"


def live_valuation(row, price):
    """Stored valuation row re-priced at `price`: the denominators (EPS, sales, book, FCF, debt, cash)
    stay as of that row, only the price changes. Exact, because every stored ratio is linear in price."""
    if not row or not row.get("close") or not price:
        return row
    f = price / row["close"]
    out = dict(row)
    for k in SCALE:
        if out.get(k) is not None:
            out[k] = out[k] * f
    for k in INVERSE:
        if out.get(k) is not None:
            out[k] = out[k] / f
    if row.get("enterprise_value") is not None and row.get("market_cap") is not None:
        ev = row["enterprise_value"] + out["market_cap"] - row["market_cap"]
        for k in ("ev_ebitda_ttm", "ev_sales_ttm"):
            if row.get(k) is not None and row["enterprise_value"]:
                out[k] = row[k] * ev / row["enterprise_value"] if ev > 0 else None
        out["enterprise_value"] = ev
    return out


@app.get("/api/company/{ticker}")
def company_info(ticker: str, live: int = 0):
    """live=0: database only (instant). live=1: plus the live regular-session price (Yahoo, cached 30 s)
    and every price-based statistic recalculated with it."""
    t = ticker.upper()
    c = company(t) or dict(ticker=t, name=t, cik=None)
    px = q("SELECT date, close FROM prices WHERE ticker=? AND close IS NOT NULL ORDER BY date DESC LIMIT 2", (t,))
    val = q("SELECT * FROM valuation_daily WHERE ticker=? ORDER BY date DESC LIMIT 1", (t,))
    out = dict(company=c, price=None, change=None, valuation=val[0] if val else None,
               is_etf=bool(q("SELECT 1 FROM etfs WHERE ticker=?", (t,))),
               ttm={}, metrics={}, has_financials=False, has_segments=False, has_prices=bool(px),
               has_dividends=bool(q("SELECT 1 FROM dividends WHERE ticker=? AND amount > 0 LIMIT 1", (t,))))
    # an SEC company stored with prices only (loaded while the SEC could not be reached): the page loads it properly
    out["needs_sec"] = c.get("cik") is None and not out["is_etf"] and bool(q("SELECT 1 FROM tickers WHERE ticker=?", (t,)))
    if px:
        out["price"], out["price_date"] = px[0]["close"], px[0]["date"]
        if len(px) > 1 and px[1]["close"]:
            out["change"] = px[0]["close"] / px[1]["close"] - 1
        if val and screener.stale_valuation(val[0]["date"], px[0]["date"]):
            # the same rule as the Screener: market cap and multiples from years ago are not shown
            out["valuation_stale"] = val[0]["date"]
            out["valuation"] = dict(date=px[0]["date"], close=px[0]["close"])
    qt = quote.get_quote(t) if live else None
    rp = regular_price(qt)
    if qt:
        out["quote"] = qt
    if rp and px:
        price, day, is_live = rp
        out.update(price=price, price_date=day, price_live=is_live, change=qt["regular"].get("change_pct"))
        if out["valuation"]:
            out["valuation"] = {**live_valuation(out["valuation"], price), "date": day}
    if out["valuation"] and out["has_dividends"]:
        # forward yield everywhere (the same as the Dividends tab); trailing kept as dividend_yield_ttm
        v = out["valuation"] = dict(out["valuation"])
        v["dividend_yield_ttm"] = v.get("dividend_yield")
        conn = db.connect()
        try:
            fy = dividends.forward_yield(conn, t, out["price"])
        finally:
            conn.close()
        if fy is not None:
            v["dividend_yield"] = fy
    if c.get("cik"):
        cik = c["cik"]
        out["ttm"] = latest_by_item("financials", "item", cik, "TTM",
                                    ["revenue", "gross_profit", "operating_income", "net_income", "eps_diluted",
                                     "fcf", "ebitda", "dps", "shares_diluted"])
        bal = latest_by_item("financials", "item", cik, "Q",
                             ["cash_and_sti", "total_debt", "total_equity", "shares_outstanding", "total_assets"])
        out["balance"] = bal
        out["metrics"] = latest_by_item("metrics", "metric", cik, "TTM",
                                        ["revenue_yoy", "eps_diluted_yoy", "gross_margin", "operating_margin",
                                         "net_margin", "fcf_margin", "roe", "roic", "net_cash", "debt_to_equity",
                                         "current_ratio", "efficiency_ratio", "rotce", "loans_to_deposits",
                                         "tangible_book_value_per_share", "nii_to_assets"])
        # banks get bank key stats instead of FCF / margins / EV (see mapping.BANK_MARKERS)
        out["is_bank"] = bool(q("SELECT 1 FROM financials WHERE cik=? AND item='deposits' LIMIT 1", (cik,)))
        if out["is_bank"]:
            out["ttm"].update(latest_by_item("financials", "item", cik, "TTM", ["net_interest_income"]))
            out["balance"].update(latest_by_item("financials", "item", cik, "Q", ["deposits", "loans"]))
        # insurers (SEC industry code 63xx, see mapping.INDUSTRY_SIC) get insurer key stats instead of
        # FCF / margins / EV / net cash: the float (premiums held until claims are paid) distorts those
        out["is_insurer"] = not out["is_bank"] and bool(re.match(INDUSTRY_SIC["insurer"], str(c.get("sic") or "")))
        if out["is_insurer"]:
            out["ttm"].update(latest_by_item("financials", "item", cik, "TTM", ["premiums_earned", "net_investment_income"]))
            out["metrics"].update(latest_by_item("metrics", "metric", cik, "TTM",
                                                 ["loss_ratio", "premiums_earned_yoy", "book_value_per_share"]))
        out["has_financials"] = bool(out["ttm"])
        out["currency"] = reporting_currency(cik)
        out["annual_only"] = annual_only(cik)
        out["converted_from"] = converted_from(cik)
        out["has_segments"] = bool(q("SELECT 1 FROM segment_financials WHERE cik=? LIMIT 1", (cik,)))
        rng = q("SELECT MIN(fiscal_year) a, MAX(fiscal_year) b FROM financials WHERE cik=? AND period_type='FY'", (cik,))
        out["fy_range"] = rng[0] if rng else None
        dq = q("SELECT checked_at, score, checks, failed, details FROM data_quality WHERE cik=?", (cik,))
        if dq:
            out["quality"] = {**dq[0], "details": quality.tidy(json.loads(dq[0]["details"] or "{}"))}
        from .. import accuracy
        out["accuracy"] = accuracy.summary(q("SELECT * FROM accuracy_check WHERE ticker=?", (t,)))
        mc = q("SELECT date, ours, yahoo, diff, reason, text, jumps FROM mcap_check WHERE ticker=?", (t,))
        if mc:
            out["mcap_check"] = {**mc[0], "jumps": json.loads(mc[0]["jumps"] or "[]")}
    out["analyst"] = estimates.brief(t, c, out["price"])
    return out


@app.get("/api/peers/{ticker}")
def peers(ticker: str, n: int = 5):
    """Similar companies for the Overview: same industry (else same sector), closest in market cap, with a few
    numbers from the Screener table (kept in memory, so this is instant once the Screener has been built)."""
    t = ticker.upper()
    rows = screener.rows(DB_PATH.stat().st_mtime)
    me = next((r for r in rows if r["ticker"] == t), None)
    if not me:
        return dict(ticker=t, by=None, peers=[])
    import math
    size = lambda r: abs(math.log((r.get("market_cap") or 1) / (me.get("market_cap") or 1)))   # noqa: E731
    out, by = [], None
    for key in ("industry", "sector"):
        if me.get(key):
            same = [r for r in rows if r["ticker"] != t and r.get(key) == me[key] and r.get("market_cap")]
            if len(same) >= 2 or key == "sector":
                out, by = sorted(same, key=size)[:n], key
                if out:
                    break
    keep = ("ticker", "name", "market_cap", "pe", "revenue_yoy", "operating_margin", "r_1y", "roe", "pb", "kind")
    return dict(ticker=t, by=by, group=me.get(by) if by else None,
                me={k: me.get(k) for k in keep}, peers=[{k: r.get(k) for k in keep} for r in out])


@app.get("/api/chart-events/{ticker}")
def chart_events(ticker: str):
    """Markers for the price chart: the days results were filed with the SEC (10-Q / 10-K, with the reported
    EPS where known) and every dividend (ex-date and amount)."""
    t = ticker.upper()
    c = company(t) or {}
    earn = []
    if c.get("cik"):
        eps = {r["quarter_end"]: r["eps_actual"] for r in q(
            "SELECT quarter_end, eps_actual FROM earnings_surprises WHERE ticker=?", (t,))}
        for r in q("SELECT filing_date, report_date, form FROM filings WHERE cik=? AND form IN ('10-Q', '10-K') "
                   "AND filing_date IS NOT NULL ORDER BY filing_date", (c["cik"],)):
            earn.append(dict(date=r["filing_date"], form=r["form"], period=r["report_date"],
                             eps=eps.get(r["report_date"])))
    divs = q("SELECT date, amount FROM dividends WHERE ticker=? ORDER BY date", (t,))
    return dict(earnings=earn, dividends=divs)


@app.get("/api/dividends/{ticker}")
def dividend_info(ticker: str, live: int = 0):
    """Dividends tab: yield, annual dividend, growth, streaks, payout ratios, per year and every payment
    (database). live=1: with the live price and the next announced dividend (Yahoo, cached)."""
    t = ticker.upper()
    c = company(t) or {}
    price = None
    if live:
        rp = regular_price(quote.get_quote(t))
        price = rp[0] if rp else None
    conn = db.connect()
    try:
        out = dividends.summary(conn, t, c.get("cik"), price)
    finally:
        conn.close()
    if live and out.get("has_dividends") and not out.get("stopped"):
        nxt = dividends.next_dividend(t)
        if nxt and nxt.get("ex_date") and out.get("last_ex") and nxt["ex_date"] > out["last_ex"]:
            out["next"] = nxt                                 # announced, not in the payment history yet
        elif nxt and nxt.get("pay_date") and nxt.get("ex_date") == out.get("last_ex"):
            out["last_pay"] = nxt["pay_date"]
    return out


@app.get("/api/insiders/{ticker}")
def insider_trades(ticker: str, refresh: int = 0):
    """Ownership tab: insider trades from the SEC's Form 4 filings (stockterminal/insiders.py). refresh=1 downloads
    the Form 4s not stored yet (at most insiders.MAX_NEW per call; `left` says how many are still to come)."""
    from .. import insiders
    t = ticker.upper()
    c = company(t) or {}
    if not c.get("cik"):
        return dict(available=False, reason="Insider trades come from the SEC; this stock has no SEC filings in the app.")
    conn = db.connect()
    try:
        got, error = dict(new=0, left=0), None
        if refresh:
            try:
                got = insiders.update(conn, c["cik"])
            except Exception as e:  # noqa: BLE001  offline: what is stored
                error = "Could not reach the SEC" if "403" not in str(e) else "The SEC refused the request (enter your email in Settings)"
        out = insiders.summary(conn, c["cik"])
    finally:
        conn.close()
    foreign = annual_only(c["cik"])
    return dict(out, available=True, left=got.get("left", 0), error=error, foreign=foreign)


@app.get("/api/investors/{ticker}")
def big_investors(ticker: str, refresh: int = 0):
    """Ownership tab: the largest holders (Yahoo) and the famous investors that hold this stock (their 13F filings
    at the SEC, stockterminal/investors.py; downloaded in the background once a week)."""
    from .. import investors
    from ..quote import yahoo_symbol
    t = ticker.upper()
    c = company(t) or {}
    conn = db.connect()
    try:
        yahoo = investors.yahoo_holders(conn, yahoo_symbol(t), refresh=bool(refresh))
        famous, state = None, None
        if c.get("cik"):
            state = investors.refresh_in_background(conn) if refresh else None
            names = [c.get("name"), c.get("display_name")] + [r[0] for r in conn.execute("SELECT name FROM tickers WHERE ticker=?", (t,))]
            famous = investors.famous_for(conn, c["cik"], names)
    finally:
        conn.close()
    return dict(yahoo=yahoo, famous=famous, state=state, followed=[dict(cik=k, name=v) for k, v in investors.FAMOUS])


@app.get("/api/analyst/{ticker}")
def analyst(ticker: str, live: int = 0):
    t = ticker.upper()
    c = company(t)
    px = q("SELECT close FROM prices WHERE ticker=? AND close IS NOT NULL ORDER BY date DESC LIMIT 1", (t,))
    rp = regular_price(quote.get_quote(t)) if live and px else None
    return estimates.full(t, c, rp[0] if rp else (px[0]["close"] if px else None))


@app.get("/api/quote/{ticker}")
def live_quote(ticker: str):
    """Live regular + extended-hours (pre / post / overnight) price. Display only, may be null."""
    return dict(ticker=ticker.upper(), quote=quote.get_quote(ticker))


@app.get("/api/quotes")
def live_quotes(tickers: str = ""):
    return quote.get_quotes([t for t in tickers.split(",") if t][:100])


@app.get("/api/market")
def market_overview(range: str = "1D"):
    """SPY, QQQ and DIA (S&P 500, Nasdaq 100, Dow) with a live quote and a small chart."""
    return market.overview(range)


@app.get("/api/intraday")
def intraday(tickers: str, range: str = "1D"):
    """Intraday prices for Compare (1D: 5-minute bars, 5D: 30-minute bars), straight from Yahoo and only
    kept in memory for a few minutes (see market.py). {ticker: {t: [unix seconds], close: [...]} or null}."""
    if range not in ("1D", "5D"):
        raise HTTPException(400, "range must be 1D or 5D")
    return {t: market._history(t, range) for t in [x for x in tickers.upper().split(",") if x][:10]}


@app.get("/api/logo/{ticker}")
def logo(ticker: str, bg: str = "light"):
    """A company's or ETF's logo (logos.py), downloaded the first time it is asked for. None: an empty 1x1
    picture, and the screen shows the ticker on its own. bg: the version for a light or a dark background."""
    from .. import logos
    if not settings.read().get("show_logos", True):
        raise HTTPException(404, "logos are off")
    p = logos.get(ticker, bg if bg in ("light", "dark") else "light")
    if not p:      # an empty 1x1 picture instead of a 404, so the browser console stays clean; the screen sees the size
        return Response(_NO_LOGO, media_type="image/png", headers={"Cache-Control": "max-age=3600", "X-No-Logo": "1"})
    return FileResponse(p, media_type="image/png", headers={"Cache-Control": "max-age=86400"})


_NO_LOGO = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944"
                         "4154789c6360606060000000050001a5f645400000000049454e44ae426082")


@app.get("/api/etf/{ticker}")
def etf_info(ticker: str, live: int = 0):
    """ETF page: fund profile, returns, risk and dividends (database), plus the live quote when live=1."""
    conn = db.connect()
    try:
        out = etf.summary(conn, ticker)
    finally:
        conn.close()
    if live:
        qt = quote.get_quote(out["ticker"])
        rp = regular_price(qt)
        if qt:
            out["quote"] = qt
        if rp and out.get("has_prices"):
            out.update(price=rp[0], price_date=rp[1], price_live=rp[2], change=qt["regular"].get("change_pct"))
    return out


@app.get("/api/etf/{ticker}/holdings")
def etf_holdings(ticker: str, live: int = 0):
    """Every holding of an ETF with today's price. A list from the SEC report (months old) is re-weighted
    with today's prices: shares x price now, compared with the value on the report date."""
    t = ticker.upper()
    e = q("SELECT name, category, total_assets, pe, holdings_as_of, holdings_source, sectors, countries, asset_mix "
          "FROM etfs WHERE ticker=?", (t,))
    if not e:
        raise HTTPException(404, "not an ETF in the database")
    e = e[0]
    rows = q("""SELECT h.rank, h.ticker, h.name, h.shares, h.value, h.weight, h.asset, h.country,
                       COALESCE(c.display_name, c.name, k.name) AS sec_name, c.sector, c.ticker IS NOT NULL AS loaded
                FROM etf_holdings h LEFT JOIN companies c ON c.ticker=h.ticker LEFT JOIN tickers k ON k.ticker=h.ticker
                WHERE h.etf=? ORDER BY h.rank""", (t,))
    for r in rows:
        sec_name = r.pop("sec_name")
        if sec_name and (r["name"] or "").isupper():     # the daily files write names in capitals
            r["name"] = db.readable_name(sec_name)
        r["loaded"] = bool(r["loaded"])
    reweighted = False
    if live and rows:
        qs = quote.get_quotes([r["ticker"] for r in rows if r["ticker"]])
        for r in rows:
            rq = qs.get(r["ticker"]) if r["ticker"] else None
            if rq and rq.get("regular"):
                r["price"], r["change"] = rq["regular"].get("price"), rq["regular"].get("change_pct")
        if e["holdings_source"] == "sec":
            # value now = value on the report date x (price now / price then); lines without a price keep theirs
            vals = [r["value"] * (r["price"] / (r["value"] / r["shares"]))
                    if r.get("price") and r["value"] and r["shares"] else (r["value"] or 0) for r in rows]
            tot_then = sum(r["value"] or 0 for r in rows)
            tot_now = sum(vals)
            priced = sum(r["value"] or 0 for r in rows if r.get("price"))
            if tot_now and tot_then and priced / tot_then > 0.9:
                scale = sum(r["weight"] for r in rows)      # keep the report's total (incl. cash) at the same level
                for r, v in zip(rows, vals):
                    r["weight_report"] = r["weight"]
                    r["weight"] = v / tot_now * scale
                rows.sort(key=lambda r: -r["weight"])
                reweighted = True
    # sectors from our own company data when it covers the fund, otherwise Yahoo's
    sec_w, covered = {}, 0
    for r in rows:
        if r["sector"]:
            sec_w[r["sector"]] = sec_w.get(r["sector"], 0) + r["weight"]
            covered += r["weight"]
    sectors = {k: v / covered for k, v in sec_w.items()} if covered > 0.9 else json.loads(e["sectors"] or "{}")
    stocks = [r for r in rows if r["asset"] != "STIV"]           # not counted: cash and money market funds
    return dict(ticker=t, name=e["name"], category=e["category"], total_assets=e["total_assets"], pe=e["pe"],
                as_of=e["holdings_as_of"], source=e["holdings_source"], reweighted=reweighted,
                count=len(stocks) or len(rows), top10=sum(r["weight"] for r in rows[:10]),
                sectors=sectors, sectors_source="own" if covered > 0.9 else "yahoo",
                countries=json.loads(e["countries"] or "{}"), assets=json.loads(e["asset_mix"] or "{}"),
                holdings=rows)


@app.get("/api/etfs")
def etf_list(live: int = 0):
    rows = q("""SELECT e.ticker, e.name, e.family, e.category, e.expense_ratio, e.total_assets, e.yield_ AS yield,
                (SELECT close FROM prices p WHERE p.ticker=e.ticker AND p.close IS NOT NULL ORDER BY date DESC LIMIT 1) AS close,
                (SELECT close FROM prices p WHERE p.ticker=e.ticker AND p.close IS NOT NULL ORDER BY date DESC LIMIT 1 OFFSET 1) AS prev,
                (SELECT MAX(date) FROM prices p WHERE p.ticker=e.ticker) AS date
                FROM etfs e ORDER BY e.total_assets DESC""")
    for r in rows:                 # without a live quote: the change of the last stored day (the Today column says which)
        prev = r.pop("prev")
        r["change"] = r["close"] / prev - 1 if r["close"] and prev else None
    if live:
        qs = quote.get_quotes([r["ticker"] for r in rows])
        for r in rows:
            rp = regular_price(qs.get(r["ticker"]))
            if rp:
                r.update(close=rp[0], live=rp[2], change=qs[r["ticker"]]["regular"].get("change_pct"))
    return rows


@app.get("/api/etfs/popular")
def etf_popular():
    """Well-known ETFs that are not loaded yet (the ETFs page offers them in one click)."""
    have = {r["ticker"] for r in q("SELECT ticker FROM etfs")} | {r["ticker"] for r in q("SELECT DISTINCT ticker FROM prices")}
    return [dict(ticker=t, name=n) for t, n in etf.POPULAR.items() if t not in have]


@app.get("/api/prices/{ticker}")
def prices(ticker: str, ohlc: int = 0):
    """Daily closes; ohlc=1 adds open, high and low (the candlestick chart)."""
    rows = q("SELECT date, open, high, low, close, adj_close, volume FROM prices WHERE ticker=? AND close IS NOT NULL ORDER BY date", (ticker.upper(),))
    out = dict(dates=[r["date"] for r in rows], close=[r["close"] for r in rows],
               adj=[r["adj_close"] for r in rows], volume=[r["volume"] for r in rows])
    if ohlc:
        out.update(open=[r["open"] for r in rows], high=[r["high"] for r in rows], low=[r["low"] for r in rows])
    return out


@app.get("/api/companies")
def companies(live: int = 0):
    # in_screener: the same companies as the Screener (a ticker and financials), so counts agree
    rows = q("""SELECT c.ticker, COALESCE(c.display_name, c.name) AS name, c.sector, v.close, v.market_cap, v.pe_ttm, v.date,
                (c.ticker IS NOT NULL AND c.cik IN (SELECT DISTINCT cik FROM financials)) AS in_screener
                FROM companies c LEFT JOIN valuation_daily v ON v.ticker=c.ticker
                AND v.date=(SELECT MAX(date) FROM valuation_daily WHERE ticker=c.ticker)
                ORDER BY v.market_cap DESC""")
    for r in rows:
        r["name"] = db.readable_name(r["name"])
    if live:   # one batched quote request for the whole list
        qs = quote.get_quotes([r["ticker"] for r in rows if r["ticker"]])
        for r in rows:
            rp = regular_price(qs.get(r["ticker"]))
            if rp and r["close"]:
                r.update({**live_valuation(r, rp[0]), "date": rp[1], "live": rp[2],
                          "change": qs[r["ticker"]]["regular"].get("change_pct")})
        if not any(r.get("change") is not None for r in rows):
            # no live prices (offline, Yahoo down): the change of the last session in the database
            c = db.connect()
            try:
                for r in rows:
                    two = c.execute("SELECT close FROM prices WHERE ticker=? AND close IS NOT NULL ORDER BY date DESC LIMIT 2",
                                    (r["ticker"],)).fetchall() if r["ticker"] else []
                    if len(two) == 2 and two[0][0] and two[1][0]:
                        r["change"] = two[0][0] / two[1][0] - 1
            finally:
                c.close()
    return rows


# --------------------------------------------------------------------------- statements & metrics
def _table(rows, key_col, ptype, flags):
    periods = {}
    for r in rows:
        k = r["end_date"]
        periods[k] = dict(key=k, end=r["end_date"], fy=r["fiscal_year"], fq=r["fiscal_quarter"],
                          cq=r["calendar_quarter"], label=period_label(ptype, r["fiscal_year"], r["fiscal_quarter"]))
    values = {}
    for r in rows:
        cell = dict(v=r["value"])
        if "source" in r:
            cell.update(src=r["source"], m=r["method"], filed=r["filed"])
            if r["original_value"] is not None and r["value"] and \
                    abs(r["original_value"] - r["value"]) > 1e-6 * max(1, abs(r["value"])):
                cell["orig"] = r["original_value"]
        f = flags.get((r[key_col], ptype, r["fiscal_year"], r["fiscal_quarter"]))
        if f:
            cell["flag"] = f
        values.setdefault(r[key_col], {})[r["end_date"]] = cell
    return sorted(periods.values(), key=lambda p: p["end"]), values


def reporting_currency(cik):
    """Currency of the statements: USD, or EUR / DKK / ... for a foreign company (ASML)."""
    r = q("SELECT unit FROM financials WHERE cik=? AND item='revenue' AND unit IS NOT NULL LIMIT 1", (cik,))
    return r[0]["unit"] if r else "USD"


def converted_from(cik):
    """The company's own reporting currency when its statements were converted to US dollars (ASML: EUR)."""
    if reporting_currency(cik) != "USD":
        return None
    r = q("SELECT unit FROM xbrl_facts WHERE cik=? AND taxonomy='us-gaap' AND tag='Assets' LIMIT 1", (cik,))
    return r[0]["unit"] if r and r[0]["unit"] != "USD" else None


def annual_only(cik):
    """True for a company that reports once a year (foreign filers on 20-F / 40-F): no quarterly numbers."""
    return bool(q("SELECT 1 FROM financials WHERE cik=? AND period_type='FY' LIMIT 1", (cik,))) and \
        not q("SELECT 1 FROM financials WHERE cik=? AND period_type='Q' AND item IN ('revenue','net_income') LIMIT 1", (cik,))


@app.get("/api/statements/{ticker}")
def statements(ticker: str, period: str = "FY"):
    cik = cik_of(ticker)
    rows = q("SELECT * FROM financials WHERE cik=? AND period_type=?", (cik, period))
    periods, values = _table(rows, "item", period, flags_for(cik))
    out = []
    for i in ITEMS:
        if i["key"] in values:
            out.append(dict(key=i["key"], label=i["label"], statement=i["statement"],
                            fmt=KIND_FMT[i["kind"]], bold=i["key"] in BOLD,
                            derived=not i["tags"], values=values[i["key"]]))
    return dict(periods=periods, rows=out, statements=STATEMENT_NAMES, currency=reporting_currency(cik),
                converted_from=converted_from(cik), annual_only=annual_only(cik))


@app.get("/api/metrics/{ticker}")
def metrics(ticker: str, period: str = "FY"):
    cik = cik_of(ticker)
    rows = q("SELECT * FROM metrics WHERE cik=? AND period_type=?", (cik, period))
    periods, values = _table(rows, "metric", period, {})
    out = [dict(key=k, label=l, group=g, fmt=f, values=values[k]) for k, l, g, f in METRICS if k in values]
    return dict(periods=periods, rows=out, currency=reporting_currency(cik),
                converted_from=converted_from(cik), annual_only=annual_only(cik))


@app.get("/api/segments/{ticker}")
def segments(ticker: str, period: str = "FY", breakdown: str = "", item: str = "revenue"):
    cik = cik_of(ticker)
    avail = q("SELECT breakdown, item, COUNT(*) n FROM segment_financials WHERE cik=? AND in_partition=1 "
              "GROUP BY breakdown, item", (cik,))
    bds = [b for b in BREAKDOWNS if any(a["breakdown"] == b and a["item"] == "revenue" for a in avail)]
    if not bds:
        return dict(breakdowns=[], members=[], periods=[])
    bd = breakdown if breakdown in bds else bds[0]
    rows = q("SELECT * FROM segment_financials WHERE cik=? AND breakdown=? AND item=? AND period_type=?",
             (cik, bd, item, period))
    flags = flags_for(cik)
    periods = {}
    members = {}
    for r in rows:
        periods[r["end_date"]] = dict(key=r["end_date"], end=r["end_date"], fy=r["fiscal_year"],
                                      fq=r["fiscal_quarter"], cq=r["calendar_quarter"],
                                      label=period_label(period, r["fiscal_year"], r["fiscal_quarter"]),
                                      flag=flags.get((f"seg:{bd}", period, r["fiscal_year"], r["fiscal_quarter"])))
        m = members.setdefault(r["member_key"], dict(key=r["member_key"], label=r["member_label"], values={}))
        m["values"][r["end_date"]] = dict(v=r["value"], part=r["in_partition"], m=r["method"], src=r["source"])
    tot = {r["end_date"]: r["value"] for r in
           q("SELECT end_date, value FROM financials WHERE cik=? AND item=? AND period_type=?", (cik, item, period))}
    mem = sorted(members.values(), key=lambda m: -(m["values"][max(m["values"])]["v"] or 0))
    return dict(breakdowns=[dict(key=b, label=BREAKDOWNS[b]) for b in bds], breakdown=bd,
                items=sorted({a["item"] for a in avail if a["breakdown"] == bd}),
                periods=sorted(periods.values(), key=lambda p: p["end"]), members=mem, totals=tot)


@app.get("/api/valuation/{ticker}")
def valuation(ticker: str, live: int = 0):
    rows = q("SELECT * FROM valuation_daily WHERE ticker=? ORDER BY date", (ticker.upper(),))
    if not rows:
        return dict(dates=[], series={}, fields=[])
    rp = regular_price(quote.get_quote(ticker)) if live else None
    if rp:   # today's point at the live regular-session price (replaces a stored row for the same day)
        price, day, _ = rp
        last = rows[-1]
        if day and day >= last["date"]:
            row = {**live_valuation(last, price), "date": day}
            rows = rows[:-1] + [row] if day == last["date"] else rows + [row]
    return dict(dates=[r["date"] for r in rows],
                series={k: [r[k] for r in rows] for k, _, _ in VALUATION + [("close", "", "")]},
                fields=[dict(key=k, label=l, fmt=f) for k, l, f in VALUATION])


@app.get("/api/dcf/{ticker}")
def dcf(ticker: str, live: int = 0):
    """Everything the DCF on the Valuation tab starts from: free cash flow (TTM and per fiscal year), stock-based
    pay, shares, debt, cash and the growth rates the defaults come from. The model itself runs in the screen,
    so moving a number answers at once. Shares and net debt are the ones behind the app's market cap and EV."""
    t = ticker.upper()
    c = company(t)
    if not c or not c.get("cik"):
        return dict(ok=False, reason="no_financials")
    cik = c["cik"]
    info = company_info(t, live)
    val = info.get("valuation") or {}
    kind = "bank" if info.get("is_bank") else "insurer" if info.get("is_insurer") else None
    ttm = latest_by_item("financials", "item", cik, "TTM", ["fcf", "revenue", "sbc", "operating_cf", "capex"])
    bal = latest_by_item("financials", "item", cik, "Q",
                         ["cash_and_sti", "total_debt", "noncontrolling_interest", "shares_outstanding"])
    if not ttm.get("fcf") and not bal:      # annual-only filers (20-F): the latest fiscal year instead
        bal = latest_by_item("financials", "item", cik, "FY",
                             ["cash_and_sti", "total_debt", "noncontrolling_interest", "shares_outstanding"])
    rows = q("SELECT fiscal_year fy, item, value, end_date FROM financials WHERE cik=? AND period_type='FY' "
             "AND item IN ('fcf','revenue','sbc') ORDER BY fiscal_year", (cik,))
    years = {}
    for r in rows:
        y = years.setdefault(r["fy"], dict(fy=r["fy"], end=r["end_date"]))
        y[r["item"]] = r["value"]
    history = [y for _, y in sorted(years.items()) if y.get("fcf") is not None][-10:]
    if not ttm.get("fcf") and history:
        last = history[-1]
        ttm = dict(fcf=last.get("fcf"), revenue=last.get("revenue"), sbc=last.get("sbc"), from_fy=last["fy"])

    def cagr(key, n=5):
        h = [y for y in history if y.get(key) is not None]
        if len(h) <= n:
            n = len(h) - 1
        if n < 3:
            return None
        a, b = h[-1 - n][key], h[-1][key]
        return (b / a) ** (1 / n) - 1 if a and a > 0 and b and b > 0 else None

    # analysts: revenue growth of the coming fiscal years, and their EPS growth per year for the next 5 years
    est = estimates.full(t, c) or {}
    rev_est = [dict(fy=a["fy"], growth=a.get("revenue_growth")) for a in est.get("annual", [])
               if a.get("est") and a.get("revenue_growth") is not None]
    ltg = next((g.get("stock") for g in est.get("growth", []) if g.get("period") == "LTG"), None)

    price = info.get("price")
    shares = val.get("shares_outstanding") or bal.get("shares_outstanding")
    debt, cash, nci = bal.get("total_debt") or 0, bal.get("cash_and_sti") or 0, bal.get("noncontrolling_interest") or 0
    return dict(ok=True, ticker=t, kind=kind, price=price, price_date=info.get("price_date"),
                market_cap=val.get("market_cap"), shares=shares, debt=debt, cash=cash, minority=nci,
                net_debt=debt - cash + nci, ttm=ttm, history=history,
                cagr=dict(revenue=cagr("revenue"), fcf=cagr("fcf")), revenue_est=rev_est[:3], ltg=ltg)


# --------------------------------------------------------------------------- compare / chart builder
@app.get("/api/fields")
def fields(tickers: str = ""):
    groups = [dict(group="Financials: " + STATEMENT_NAMES[s], fields=[
        dict(key=f"item:{i['key']}", label=i["label"], fmt=KIND_FMT[i["kind"]]) for i in ITEMS if i["statement"] == s])
        for s in STATEMENT_NAMES]
    mg = {}
    for k, l, g, f in METRICS:
        mg.setdefault(g, []).append(dict(key=f"metric:{k}", label=l, fmt=f))
    groups += [dict(group=g, fields=v) for g, v in mg.items()]
    groups.append(dict(group="Valuation (at quarter end)",
                       fields=[dict(key=f"val:{k}", label=l, fmt=f) for k, l, f in VALUATION]))
    groups.append(dict(group="Analyst estimates (daily snapshots)", fields=[
        dict(key=f"est:{k}", label=l, fmt=f) for k, l, f in ANALYST_FIELDS]))
    seg = []
    for t in [x for x in tickers.upper().split(",") if x]:
        c = company(t)
        if not c:
            continue
        for r in q("SELECT DISTINCT breakdown, member_key, member_label FROM segment_financials "
                   "WHERE cik=? AND in_partition=1 AND item='revenue' AND period_type='FY' "
                   "AND fiscal_year >= (SELECT MAX(fiscal_year)-1 FROM segment_financials WHERE cik=?)",
                   (c["cik"], c["cik"])):
            seg.append(dict(key=f"seg:{r['breakdown']}:{r['member_key']}:revenue",
                            label=f"{t}: {r['member_label']} revenue", fmt="usd"))
    if seg:
        groups.append(dict(group="Segments (revenue)", fields=seg))
    ts = [x for x in tickers.upper().split(",") if x]
    if ts:
        # only the fields at least one of these companies has data for (no bank lines for Apple and
        # Microsoft, no financials at all for ETFs)
        have = _fields_with_data(ts)
        groups = [dict(g, fields=[f for f in g["fields"] if f["key"] in have or f["key"].startswith("seg:")])
                  for g in groups]
        groups = [g for g in groups if g["fields"]]
    return groups


def _fields_with_data(ts):
    ph = ",".join("?" * len(ts))
    ciks = [r["cik"] for r in q(f"SELECT cik FROM companies WHERE ticker IN ({ph})", ts)]
    have = set()
    if ciks:
        cph = ",".join("?" * len(ciks))
        have |= {"item:" + r["k"] for r in q(f"SELECT DISTINCT item k FROM financials WHERE cik IN ({cph})", ciks)}
        have |= {"metric:" + r["k"] for r in q(f"SELECT DISTINCT metric k FROM metrics WHERE cik IN ({cph}) "
                                                f"AND value IS NOT NULL", ciks)}
    counts = q(f"SELECT {', '.join(f'COUNT({k}) AS {k}' for k, _, _ in VALUATION)} FROM valuation_daily "
               f"WHERE ticker IN ({ph})", ts)
    have |= {"val:" + k for k, _, _ in VALUATION if counts and counts[0][k]}
    if q(f"SELECT 1 FROM analyst_estimates WHERE ticker IN ({ph}) LIMIT 1", ts):
        have |= {"est:" + k for k, _, _ in ANALYST_FIELDS}
    return have


# metric -> the line item whose consistency check decides its warning sign
METRIC_INPUT = {"gross_margin": "gross_profit", "net_margin": "net_income", "roe": "net_income",
                "operating_margin": "operating_income", "fcf_margin": "fcf"}


def _flag_of(flags, item, period, fy, fq):
    """Warning text for one point of a chart (TTM: any of its four quarters)."""
    if period != "TTM":
        return flags.get((item, period, fy, fq))
    if fq is None:
        return None
    for _ in range(4):
        f = flags.get((item, "Q", fy, fq))
        if f:
            return f
        fy, fq = (fy, fq - 1) if fq > 1 else (fy - 1, 4)
    return None


@app.get("/api/series")
def series(tickers: str, field: str, period: str = "TTM"):
    """Values per ticker on a calendar-quarter timeline: {ticker: [[date, value, label, warning], ...]}."""
    out = {}
    kind, _, name = field.partition(":")
    for t in [x for x in tickers.upper().split(",") if x]:
        c = company(t)
        pts = []
        if kind in ("item", "metric") and c:
            table, col = ("financials", "item") if kind == "item" else ("metrics", "metric")
            item = name if kind == "item" else METRIC_INPUT.get(name) or next(
                (i["key"] for i in sorted(ITEMS, key=lambda i: -len(i["key"])) if name.startswith(i["key"] + "_")), None)
            flags = flags_for(c["cik"]) if item else {}
            for r in q(f"SELECT calendar_quarter, fiscal_year, fiscal_quarter, value, end_date FROM {table} "
                       f"WHERE cik=? AND {col}=? AND period_type=? ORDER BY end_date", (c["cik"], name, period)):
                pts.append([cq_date(r["calendar_quarter"]), r["value"],
                            period_label(period, r["fiscal_year"], r["fiscal_quarter"]),
                            _flag_of(flags, item, period, r["fiscal_year"], r["fiscal_quarter"]) if item else None,
                            r["end_date"]])
        elif kind == "seg" and c:
            bd, key, item = name.split(":")
            for r in q("SELECT calendar_quarter, fiscal_year, fiscal_quarter, value, end_date FROM segment_financials "
                       "WHERE cik=? AND breakdown=? AND member_key=? AND item=? AND period_type=? ORDER BY end_date",
                       (c["cik"], bd, key, item, period)):
                pts.append([cq_date(r["calendar_quarter"]), r["value"],
                            period_label(period, r["fiscal_year"], r["fiscal_quarter"]), None, r["end_date"]])
        elif kind == "est":
            pts = estimates.snapshot_series(t, c, name)
        elif kind == "val":
            rows = q(f"SELECT date, {name} AS v FROM valuation_daily WHERE ticker=? ORDER BY date", (t,))
            if rows:
                df = pd.DataFrame(rows).dropna(subset=["v"])
                df["date"] = pd.to_datetime(df.date)
                qe = df.groupby(df.date.dt.to_period("Q")).last()
                for p, r in qe.iterrows():
                    pts.append([p.end_time.strftime("%Y-%m-%d"), float(r.v), f"{p}"])
        out[t] = [p for p in pts if p[1] is not None]
    return out


@app.get("/api/estimates_annual")
def estimates_annual(tickers: str):
    """Per ticker the yearly revenue / EPS list of the Estimates tab: reported years plus analyst estimates
    (est=True) for the coming fiscal years. Compare draws the estimates as (E) bars."""
    out = {}
    for t in [x for x in tickers.upper().split(",") if x]:
        c = company(t)
        d = estimates.full(t, c) if c else {}
        out[t] = [dict(fy=a["fy"], est=a["est"], revenue=a.get("revenue"), eps=a.get("eps"),
                       revenue_growth=a.get("revenue_growth"), eps_growth=a.get("eps_growth"))
                  for a in (d or {}).get("annual", [])]
    return out


# ---------------------------------------------------------------- export (Compare: chart as PNG, data as CSV)
# Written by the server, not downloaded by the browser: downloads often do not work inside the app window.
def export_dir():
    """The user's Downloads folder, or data/exports when there is none."""
    d = Path.home() / "Downloads"
    if not d.is_dir():
        d = DATA_DIR / "exports"
        d.mkdir(parents=True, exist_ok=True)
    return d


@app.post("/api/export")
def export_file(payload: dict):
    import base64
    kind = payload.get("kind")
    if kind not in ("png", "csv"):
        raise HTTPException(400, "kind must be png or csv")
    name = re.sub(r"[^\w\-. ,()+&]+", "_", str(payload.get("name") or "chart")).strip(" ._")[:80] or "chart"
    data = payload.get("data") or ""
    if kind == "png":
        raw = base64.b64decode(data.split(",", 1)[-1])
        if not raw.startswith(b"\x89PNG"):
            raise HTTPException(400, "not a PNG image")
    else:
        raw = ("\ufeff" + data).encode("utf-8")      # BOM: Excel then reads the text as UTF-8
    d = export_dir()
    path = d / f"{name}.{kind}"
    n = 2
    while path.exists():
        path = d / f"{name} ({n}).{kind}"
        n += 1
    path.write_bytes(raw)
    return dict(path=str(path))


@app.post("/api/tax/export")
def tax_export(payload: dict):
    """Box 3 overview of one year as CSV (Excel) or PDF, written to the Downloads folder."""
    fmt = payload.get("format")
    if fmt not in ("csv", "pdf"):
        raise HTTPException(400, "format must be csv or pdf")
    try:
        name, raw = tax.export(payload.get("year"), fmt, payload.get("page") or {})
    except ValueError as e:
        raise HTTPException(400, str(e))
    d = export_dir()
    path = d / f"{name}.{fmt}"
    n = 2
    while path.exists():
        path = d / f"{name} ({n}).{fmt}"
        n += 1
    path.write_bytes(raw)
    return dict(path=str(path))


@app.post("/api/reveal")
def reveal(payload: dict):
    """Open the export folder with the file selected (only files in the export folder)."""
    path = Path(str(payload.get("path") or ""))
    if path.parent.resolve() != export_dir().resolve() or not path.exists():
        raise HTTPException(400, "not an exported file")
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])
    return dict(ok=True)


# ---------------------------------------------------------------- screener (see stockterminal/screener.py)
def _screener_warm():
    time.sleep(8)                     # after the first page has loaded: the Screener then opens instantly
    try:
        screener.rows(DB_PATH.stat().st_mtime)
    except Exception:  # noqa: BLE001  an empty or odd database only means the page builds it when opened
        pass


@app.on_event("startup")
def _start_screener_warm():
    threading.Thread(target=_screener_warm, daemon=True).start()


@app.get("/api/data-health")
def data_health():
    """Every company with a number that cannot be right (stockterminal/health.py), worst first."""
    from .. import health
    return health.summary(screener.rows(DB_PATH.stat().st_mtime))


@app.get("/api/screener")
def screener_rows(live: int = 0):
    """Every loaded company with ~75 numbers (database only, kept in memory). live=1: live prices (one
    batched quote request) and every price-based number recalculated with them."""
    rows = screener.rows(DB_PATH.stat().st_mtime)
    out = rows
    if live and rows:
        qs = quote.get_quotes([r["ticker"] for r in rows])
        out = []
        for r in rows:
            rp = regular_price(qs.get(r["ticker"]))
            out.append(screener.reprice(r, rp[0], qs[r["ticker"]]["regular"].get("change_pct"), rp[2]) if rp else r)
    return dict(fields=[dict(key=k, label=l, group=g, fmt=f, tip=t) for k, l, g, f, t in screener.FIELDS],
                rows=[screener.public(r) for r in out], live=bool(live))


@app.get("/api/sectors")
def sectors_overview():
    """The Sectors page: every sector with its medians (stockterminal/sectors.py, from the Screener table)."""
    return sectors.overview(screener.rows(DB_PATH.stat().st_mtime))


@app.get("/api/sectors/names")
def sectors_names():
    """Every sector and industry (Ctrl+K finds them)."""
    return sectors.names(screener.rows(DB_PATH.stat().st_mtime))


@app.get("/api/sectors/group")
def sectors_group(sector: str, industry: str | None = None):
    """One sector (with its industries) or one industry: medians and every company with its ranks."""
    out = sectors.group(screener.rows(DB_PATH.stat().st_mtime), sector, industry)
    if out is None:
        raise HTTPException(404, "No companies in this group")
    return out


@app.get("/api/sectors/rank/{ticker}")
def sectors_rank(ticker: str):
    """Where one company ranks in its industry and sector (the stock page)."""
    out = sectors.position(screener.rows(DB_PATH.stat().st_mtime), ticker)
    if out is None:
        raise HTTPException(404, "Company not in the Screener table")
    return out


@app.get("/api/compare_table")
def compare_table(tickers: str, live: int = 0):
    ts = [t for t in tickers.upper().split(",") if t]
    if live:
        quote.get_quotes(ts)          # one batched request; company_info then reads the cache
    return {t: company_info(t, live) for t in ts}


# ---------------------------------------------------------------- settings (see stockterminal/settings.py)
@app.get("/api/settings")
def get_settings():
    return settings.read()


@app.get("/api/about")
def about():
    """Settings > About: the app version (stockterminal/__init__.py) and, in a git copy, the exact commit."""
    from .. import __version__
    commit = None
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                           timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode == 0:
            commit = r.stdout.strip() or None
    except Exception:  # noqa: BLE001 - no git: the version alone is enough
        pass
    from ..config import FROZEN
    from ..datasetup import last_import
    return dict(version=__version__, commit=commit, data_dir=str(DATA_DIR), imported=last_import(),
                kind="installed app" if FROZEN else "test copy", window=close_window is not None)


@app.post("/api/data/open-folder")
def open_data_folder():
    """Settings > Data folder: show the folder in Explorer / Finder (only in the app window on this computer)."""
    if close_window is None:
        return dict(opened=False)
    if sys.platform == "win32":
        os.startfile(str(DATA_DIR))  # noqa: S606 - our own data folder
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(DATA_DIR)])
    return dict(opened=True)


close_window = None   # set by app/__main__.py when the app runs in its own window (None in --serve / browser mode)


@app.get("/api/app/update")
def app_update_check(refresh: int = 0):
    """Is a newer version of the app available? (stockterminal/appupdate.py; cached an hour unless refresh=1)"""
    from .. import appupdate
    return {**appupdate.check(refresh=bool(refresh)), "window": close_window is not None}


@app.post("/api/app/update")
def app_update_install():
    """Download and check the newer version (git pull, packages, self-test; rolled back when the test fails)."""
    from .. import appupdate
    res = appupdate.install()
    if res.get("installing") and close_window is not None:     # the installer replaces the app: close this window
        threading.Timer(3, close_window).start()
    return res


@app.post("/api/app/restart")
def app_restart():
    """Open a fresh copy of the app (new code) and close this window. In a browser: nothing to restart here."""
    if close_window is None:
        return dict(restarted=False)
    from .. import appupdate
    appupdate.restart_later()
    threading.Timer(0.5, close_window).start()
    return dict(restarted=True)


pick_path = None      # set by app/__main__.py: opens the window's own folder / file chooser


_fresh = {"at": 0.0, "prices": None, "filings": None}


@app.get("/api/data/status")
def data_status():
    """Top bar: how fresh the numbers are (newest stored daily close, Yahoo live or offline) and the progress
    of a ratio recalculation that runs in the background after a starter pack or an app update."""
    from .. import metrics
    if time.time() - _fresh["at"] > 300:                   # the two dates change once a day: asked again every 5 min
        conn = db.connect()
        try:
            _fresh["prices"] = conn.execute("SELECT MAX(date) FROM prices WHERE ticker IN ('SPY','AAPL','MSFT')").fetchone()[0] \
                or conn.execute("SELECT MAX(date) FROM prices").fetchone()[0]
            _fresh["filings"] = conn.execute("SELECT MAX(filed) FROM financials").fetchone()[0]
            _fresh["at"] = time.time()
        finally:
            conn.close()
    r = metrics.RECALC
    return dict(prices_date=_fresh["prices"], filings_date=_fresh["filings"], quotes=quote.status(),
                recalc=dict(done=r["done"], total=r["total"]) if r["total"] else None)


@app.get("/api/data/setup")
def data_setup_status():
    """Settings > Data and accounts / welcome screen: companies here, the starter pack on the download page,
    and the progress of getting data (stockterminal/datasetup.py)."""
    from .. import datasetup
    return {**datasetup.status(), "window": close_window is not None, "can_browse": pick_path is not None}


@app.post("/api/data/setup")
def data_setup_start(body: dict):
    """{source: 'download'} = the S&P 500 starter pack; {source: 'path', path} = another copy or a .zip."""
    from .. import datasetup
    try:
        return datasetup.start(body.get("source"), body.get("path"))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))


@app.delete("/api/data/setup")
def data_setup_cancel():
    from .. import datasetup
    datasetup.cancel_waiting()
    return {"ok": True}


@app.post("/api/data/browse")
def data_browse(body: dict):
    """The window's chooser: a folder (another copy) or a file (a starter pack .zip). None when cancelled."""
    if pick_path is None:
        raise HTTPException(400, "Type the path instead: there is no app window to choose with.")
    return {"path": pick_path(body.get("kind") == "file")}


@app.get("/api/settings/broken")
def settings_broken():
    """A damaged settings.json was replaced by the defaults: where its copy is (shown on the Settings page)."""
    return dict(copy=settings.broken_copy())


@app.put("/api/settings")
def put_settings(changes: dict):
    try:
        out = settings.update(changes)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if changes.get("sec_email"):         # the SEC may have refused the company list without an email: try again
        threading.Thread(target=_ensure_ticker_list, daemon=True).start()
    return out


# ---------------------------------------------------------------- Trading 212 link (see stockterminal/trading212.py)
# The key and secret stay on the server side: the screen only gets status() (account, currency, last 4 of the key).
@app.get("/api/t212/status")
def t212_status():
    return trading212.status()


@app.put("/api/t212/connect")
def t212_connect(body: dict):
    try:
        out = trading212.connect(body.get("key"), body.get("secret"))
    except trading212.T212Error as e:
        raise HTTPException(400, str(e))
    trading212.sync_in_background()          # first sync right away (the whole history, can take a minute)
    return out


@app.delete("/api/t212/connect")
def t212_disconnect():
    return trading212.disconnect()


@app.get("/api/portfolio")
def portfolio_page(live: int = 0):
    """Portfolio page; live=1 asks Trading 212 (and Yahoo) for current numbers, else the last sync."""
    return portfolio.build(live=bool(live))


@app.get("/api/portfolio/holding/{t212_ticker}")
def portfolio_holding(t212_ticker: str, live: int = 0):
    """One holding's page: position, trades, dividends, price history (Yahoo) and profile."""
    out = portfolio.holding(t212_ticker, live=bool(live))
    if not out.get("found"):
        raise HTTPException(404, "This holding is not in your Trading 212 portfolio.")
    return out


@app.get("/api/portfolio/allocation")
def portfolio_allocation(refresh: int = 0):
    """Sector / country / region / currency split, ETFs looked through, next to the FTSE All-World."""
    return allocation.build(refresh=bool(refresh))


@app.get("/api/portfolio/xray")
def portfolio_xray(refresh: int = 0):
    """ETF X-ray: the companies you really own, your own shares plus what is inside your ETFs."""
    return xray.build(refresh=bool(refresh))


@app.get("/api/portfolio/calendar")
def portfolio_calendar(refresh: int = 0):
    """Earnings and dividend dates of your holdings (Yahoo) and dividends received lately (Trading 212)."""
    return events.portfolio_calendar(refresh=bool(refresh))


@app.get("/api/portfolio/costs")
def portfolio_costs(refresh: int = 0):
    """Trade costs from Trading 212 (by kind, year and holding) and the yearly cost (TER) of your ETFs."""
    return costs.build(refresh=bool(refresh))


@app.get("/api/portfolio/analysis")
def portfolio_analysis(refresh: int = 0):
    """Your portfolio as one company (P/E, yield, growth, margins vs the world) and its risk."""
    return analysis.build(refresh=bool(refresh))


# ---------------------------------------------------------------- alerts (see stockterminal/alerts.py)
_alert_run = {"running": False}


@app.get("/api/alerts")
def alerts_list(limit: int = 300, offset: int = 0, ticker: str = "", q: str = "", kind: str = "", origin: str = "",
                unread: int = 0, light: int = 0):
    """Stored alerts (newest first, filtered), the unread count for the bell, and whether a check is running.
    light=1: without the rules and the watched list (the bell, Home and the stock pages ask every minute)."""
    return dict(alerts.listing(limit=max(1, min(limit, 1000)), offset=max(0, offset), ticker=ticker.strip() or None,
                               q=q.strip() or None, kind=kind or None, origin=origin or None, unread=bool(unread),
                               light=bool(light)), running=_alert_run["running"])


@app.get("/api/alerts/stocks")
def alerts_stocks():
    """Every stock with alerts (holdings, watched, others with their own settings): the Rules page's "Your stocks"."""
    return alerts.stocks()


@app.get("/api/alerts/stock/{ticker}")
def alerts_stock(ticker: str):
    """One stock for the bell on its page: its own settings, where it comes from, its latest alerts."""
    return alerts.stock(ticker)


@app.post("/api/alerts/read")
def alerts_read(payload: dict):
    """Mark alerts read: {ids: [...]}, {ticker: "NVDA"} (all of one stock) or {} (all)."""
    alerts.mark_read(payload.get("ids") or None, ticker=payload.get("ticker") or None)
    return dict(ok=True)


@app.post("/api/alerts/check")
def alerts_check():
    """Check now (in the background: Yahoo per holding can take a minute); the page polls /api/alerts."""
    if not _alert_run["running"]:
        def run():
            _alert_run["running"] = True
            try:
                alerts.check(refresh=True, say=lambda m: None)
            except Exception:  # noqa: BLE001  stored as the last check's problem, shown on the page
                pass
            finally:
                _alert_run["running"] = False
        threading.Thread(target=run, daemon=True).start()
    return dict(running=True)


@app.post("/api/alerts/test")
def alerts_test():
    """A test message to the phone (ntfy), with the topic saved in Settings."""
    rules, _ = alerts.config()
    try:
        title, body, tags = alerts.test_message()
        alerts.push(rules, title, body, priority=4, tags=tags)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))
    return dict(ok=True)


@app.get("/api/news")
def news_list(ticker: str = "", symbol: str = "", refresh: int = 0):
    """News of one stock (stockterminal/news.py): ?ticker=BRK.B (the app's ticker) or ?symbol=ASML.AS (Yahoo);
    refresh=0 answers from what is stored, refresh=1 asks Yahoo when it is older than half an hour."""
    from .. import news
    from ..quote import yahoo_symbol
    sym = symbol.strip() or (yahoo_symbol(ticker.strip()) if ticker.strip() else "")
    if not sym or len(sym) > 20:
        raise HTTPException(400, "give a ticker or a Yahoo symbol")
    return news.get(sym, refresh=bool(refresh))


@app.post("/api/alerts/summary")
def alerts_summary(payload: dict):
    """The morning summary as it would look now ({weekly: true} for the weekly one), and sent to the phone when
    {send: true} and a topic is set (stockterminal/summary.py)."""
    from .. import summary
    rules, _ = alerts.config()
    conn = db.connect()
    try:
        msg = summary.build(conn, weekly=bool(payload.get("weekly")))
    finally:
        conn.close()
    if not msg:
        raise HTTPException(400, "No portfolio yet: connect Trading 212 in Settings first.")
    sent = False
    if payload.get("send"):
        try:
            alerts.push(rules, msg[0], msg[1], priority=2, tags=["sunrise"])
            sent = True
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, str(e))
    return dict(title=msg[0], body=msg[1], sent=sent)


@app.get("/api/earnings/{ticker}")
def earnings_latest(ticker: str):
    """The company's latest results seen by the earnings watch (last 14 days): the stock page's results bar.
    {report: null} when there is none."""
    from .. import earnings_watch
    return dict(report=earnings_watch.for_ticker(ticker))


@app.get("/api/tax")
def tax_overview():
    """Box 3 rules per year and your Trading 212 numbers per year (see stockterminal/tax.py)."""
    return tax.build()


@app.get("/api/portfolio/history")
def portfolio_history(refresh: int = 1):
    """Value and time-weighted return over time plus benchmarks; refresh=1 updates Yahoo history first."""
    return pf_history.build(refresh=bool(refresh))


# ---------------------------------------------------------------- your own trades, without Trading 212 (stockterminal/own.py)
# Every change rebuilds the portfolio tables from your trades, so the Portfolio page (and its chart) shows it at once.
def _own(fn):
    conn = db.connect()
    try:
        return fn(conn)
    except own.OwnError as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()


def _own_done(conn, extra=None):
    out = own.state(conn)
    out["rebuild"] = own.rebuild(conn)
    out.update(extra or {})
    return out


@app.get("/api/portfolio/own")
def own_trades():
    return _own(own.state)


@app.post("/api/portfolio/own/trades")
def own_add(body: dict):
    return _own(lambda c: _own_done(c, dict(added=own.add(c, body.get("date"), body.get("symbol"), body.get("side"),
                                                          body.get("quantity"), body.get("price"), body.get("fees"),
                                                          body.get("note")))))


@app.delete("/api/portfolio/own/trades/{trade_id}")
def own_delete(trade_id: int):
    return _own(lambda c: (own.delete(c, trade_id), _own_done(c))[1])


@app.post("/api/portfolio/own/import")
def own_import(body: dict):
    """body: {"csv": the file's text}. Returns how many trades were added and which lines were skipped and why."""
    return _own(lambda c: (lambda r: _own_done(c, dict(imported=r)))(own.import_csv(c, body.get("csv"))))


@app.put("/api/portfolio/own/currency")
def own_currency(body: dict):
    return _own(lambda c: (own.set_currency(c, body.get("currency")), _own_done(c))[1])


@app.post("/api/portfolio/own/rebuild")
def own_rebuild():
    """Use your own trades again (after Trading 212 was disconnected), with fresh Yahoo prices."""
    return _own(_own_done)


@app.get("/api/portfolio/own/lookup")
def own_lookup(q: str):
    """What a ticker you type is on Yahoo (name, currency, price), for the trade form."""
    try:
        return own.lookup(q)
    except own.OwnError as e:
        raise HTTPException(400, str(e))


@app.get("/api/t212/sync")
def t212_sync_status():
    return trading212.sync_status()


@app.post("/api/t212/sync")
def t212_sync():
    if not trading212.status()["connected"]:
        raise HTTPException(400, "Trading 212 is not connected. Connect it in Settings.")
    return trading212.sync_in_background()


@app.get("/api/t212/summary")
def t212_summary():
    try:
        return trading212.summary()
    except trading212.T212Error as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------- watchlists (see stockterminal/watchlist.py)
def _wl(fn, *args):
    conn = db.connect()
    try:
        return fn(conn, *args)
    except KeyError as e:
        raise HTTPException(404, str(e.args[0] if e.args else e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()


def _last_price(t):
    """Live regular price, else the last stored close, else None (for 'price when added')."""
    rp = regular_price(quote.get_quote(t))
    if rp:
        return rp[0]
    px = q("SELECT close FROM prices WHERE ticker=? AND close IS NOT NULL ORDER BY date DESC LIMIT 1", (t,))
    return px[0]["close"] if px else None


@app.get("/api/watchlists")
def wl_lists():
    return dict(lists=_wl(watchlist.lists))


@app.post("/api/watchlists")
def wl_create(payload: dict):
    """New list; with `ticker` that ticker goes straight onto it."""
    def go(conn):
        lid = watchlist.create(conn, payload.get("name") or watchlist.DEFAULT_NAME)
        if payload.get("ticker"):
            t = _norm_ticker(watchlist.clean_ticker(payload["ticker"]))
            watchlist.add(conn, lid, t, _last_price(t))
        return dict(id=lid, lists=watchlist.lists(conn))
    return _wl(go)


@app.put("/api/watchlists/order")
def wl_order(payload: dict):
    _wl(watchlist.reorder_lists, [int(i) for i in payload.get("ids") or [] if str(i).isdigit()])
    return wl_lists()


@app.put("/api/watchlists/{list_id}")
def wl_rename(list_id: int, payload: dict):
    _wl(watchlist.rename, list_id, payload.get("name"))
    return wl_lists()


@app.delete("/api/watchlists/{list_id}")
def wl_delete(list_id: int):
    _wl(watchlist.delete, list_id)
    return wl_lists()


@app.post("/api/watchlists/{list_id}/items")
def wl_add(list_id: int, payload: dict):
    def go(conn):
        t = _norm_ticker(watchlist.clean_ticker(payload.get("ticker")))
        watchlist.add(conn, list_id, t, _last_price(t))
        return dict(lists=watchlist.lists(conn))
    return _wl(go)


@app.delete("/api/watchlists/{list_id}/items/{ticker}")
def wl_remove(list_id: int, ticker: str):
    _wl(watchlist.remove, list_id, ticker)
    return wl_lists()


@app.put("/api/watchlists/{list_id}/items/{ticker}")
def wl_update(list_id: int, ticker: str, payload: dict):
    _wl(watchlist.update_item, list_id, ticker, {k: v for k, v in payload.items() if k in ("buy_below", "note")})
    return dict(ok=True)


@app.put("/api/watchlists/{list_id}/order")
def wl_reorder(list_id: int, payload: dict):
    _wl(watchlist.reorder, list_id, payload.get("tickers") or [])
    return dict(ok=True)


WL_RETURNS = (("r_1w", 7), ("r_1m", 30), ("r_3m", 91), ("r_6m", 182), ("r_1y", 365))


def _wl_history(tickers):
    """{ticker: [(date, close)]} for the last ~13 months, one query."""
    if not tickers:
        return {}
    since = (dt.date.today() - dt.timedelta(days=400)).isoformat()
    out = {}
    ph = ",".join("?" * len(tickers))
    for r in q(f"SELECT ticker, date, close FROM prices WHERE ticker IN ({ph}) AND date>=? AND close IS NOT NULL "
               "ORDER BY ticker, date", (*tickers, since)):
        out.setdefault(r["ticker"], []).append((r["date"], r["close"]))
    return out


def _close_on(hist, day):
    """Last close on or before `day` (YYYY-MM-DD), or None when the history does not reach back that far."""
    best = None
    for d, c in hist:
        if d > day:
            break
        best = c
    return best


def _wl_row(t, item, info, hist, etf_row, name_of):
    c = info["company"] if info else {}
    v = (info or {}).get("valuation") or {}
    a = (info or {}).get("analyst") or {}
    qt = quote.cached(t)
    rp = regular_price(qt)
    price, live_now, change = (info or {}).get("price"), bool((info or {}).get("price_live")), (info or {}).get("change")
    if rp and not price:                    # not loaded yet: the live quote is all there is
        price, live_now, change = rp[0], rp[2], qt["regular"].get("change_pct")
    etf = bool(etf_row) or bool((info or {}).get("is_etf"))
    name = (etf_row or {}).get("name") or (c.get("name") if c.get("name") != t else None) or name_of.get(t) or t
    row = dict(ticker=t, name=name, etf=etf, loaded=bool(hist) or bool((info or {}).get("has_financials")),
               price=price, price_live=live_now, change=change, extended=(qt or {}).get("extended"),
               market_state=(qt or {}).get("market_state"),
               market_cap=v.get("market_cap"), pe=v.get("pe_ttm"), ps=v.get("ps_ttm"), pfcf=v.get("pfcf_ttm"),
               ev_ebitda=v.get("ev_ebitda_ttm"), fcf_yield=v.get("fcf_yield"),
               div_yield=(etf_row or {}).get("yield_") if etf else v.get("dividend_yield"),
               assets=(etf_row or {}).get("total_assets"), expense_ratio=(etf_row or {}).get("expense_ratio"),
               fwd_pe=a.get("forward_pe"), target_upside=a.get("target_upside"), target=a.get("target_mean"),
               rating=a.get("rating_mean"), earnings_date=a.get("earnings_date") if a.get("earnings_upcoming") else None,
               rev_growth=((info or {}).get("metrics") or {}).get("revenue_yoy"),
               eps_growth=((info or {}).get("metrics") or {}).get("eps_diluted_yoy"),
               net_margin=((info or {}).get("metrics") or {}).get("net_margin"),
               sector=c.get("sector") or (etf_row or {}).get("category"),
               **{k: item[k] for k in ("added", "added_price", "buy_below", "note", "position")})
    if hist and price:
        # returns count back from the day of the price shown (like the price chart): today when live,
        # else the last close, so a weekend doesn't shift the start date
        today = dt.date.today() if live_now else dt.date.fromisoformat(hist[-1][0])
        for k, days in WL_RETURNS:
            base = _close_on(hist, (today - dt.timedelta(days=days)).isoformat())
            row[k] = price / base - 1 if base else None
        base = _close_on(hist, f"{today.year - 1}-12-31")
        row["r_ytd"] = price / base - 1 if base else None
        lo, hi = min(c for _, c in hist[-252:]), max(c for _, c in hist[-252:])
        row["low_52w"], row["high_52w"] = min(lo, price), max(hi, price)
        row["from_high"] = price / row["high_52w"] - 1 if row["high_52w"] else None
        spark = [c for d, c in hist if d >= (today - dt.timedelta(days=91)).isoformat()]
        step = max(1, len(spark) // 60)
        row["spark"] = spark[::step] + ([price] if spark else [])
    if price and item["added_price"]:
        row["since_added"] = price / item["added_price"] - 1
    if price and item["buy_below"]:
        row["to_buy"] = price / item["buy_below"] - 1          # <= 0: at or under your buy-below price
    return row


@app.get("/api/watchlists/{list_id}/rows")
def wl_rows(list_id: int, live: int = 0):
    """Every ticker on a list with price, returns, valuation and analyst numbers. live=1: live prices (one batched
    quote request) and the price-based numbers recalculated with them."""
    its = _wl(watchlist.items, list_id)
    ts = [i["ticker"] for i in its]
    if live and ts:
        quote.get_quotes(ts)
    hist = _wl_history(ts)
    etfs = {r["ticker"]: r for r in q(f"SELECT ticker, name, category, total_assets, expense_ratio, yield_ FROM etfs "
                                      f"WHERE ticker IN ({','.join('?' * len(ts))})", ts)} if ts else {}
    name_of = {r["ticker"]: r["name"] for r in _ticker_rows()} if ts else {}
    stored_ev = {}                          # earnings dates from the "Coming up" strip, when analysts have none
    if ts:
        today = dt.date.today().isoformat()
        for e in q(f"SELECT symbol, data FROM pf_events WHERE symbol IN ({','.join('?' * len(ts))})",
                   [quote.yahoo_symbol(t) for t in ts]):
            nxt = [d for d in (json.loads(e["data"] or "{}").get("earnings") or []) if d >= today]
            if nxt:
                stored_ev[e["symbol"]] = nxt[0]
    rows, fill = [], []
    for it in its:
        t = it["ticker"]
        try:
            info = company_info(t, live) if (t in hist or company(t)) else None
        except Exception:  # noqa: BLE001  one broken company never breaks the list
            info = None
        r = _wl_row(t, it, info, hist.get(t), etfs.get(t), name_of)
        if not r["earnings_date"] and not r["etf"]:
            r["earnings_date"] = stored_ev.get(quote.yahoo_symbol(t))
        if it["added_price"] is None and r["price"]:      # added while the price was unknown: start from now
            fill.append((r["price"], list_id, t))
        r["job"] = _jobs.get(t, {}).get("state")
        rows.append(r)
    if fill:
        def go(conn):
            conn.executemany("UPDATE pf_watch SET added_price=? WHERE list_id=? AND ticker=?", fill)
            conn.commit()
        _wl(go)
    return dict(rows=rows, as_of=dt.datetime.now().isoformat(timespec="seconds"))


WL_EVENT_DAYS = 90


@app.get("/api/watchlists/{list_id}/events")
def wl_events(list_id: int, refresh: int = 0):
    """Coming earnings, ex-dividend and dividend payment dates of a list (Yahoo, kept 20 h like the Portfolio
    calendar; refresh=0 answers from what is stored)."""
    ts = [i["ticker"] for i in _wl(watchlist.items, list_id)]
    return _ticker_events(ts, refresh, WL_EVENT_DAYS)


def _ticker_events(ts, refresh, days):
    from concurrent.futures import ThreadPoolExecutor
    syms = {t: quote.yahoo_symbol(t) for t in ts}

    def stored(conn):
        out = {}
        for t, s in syms.items():
            row = conn.execute("SELECT data, fetched FROM pf_events WHERE symbol=?", (s,)).fetchone()
            if row:
                d = json.loads(row[0])
                out[t] = (d, d.get("version") == events.VERSION and time.time() - row[1] < events.CACHE_HOURS * 3600)
        return out
    have = _wl(stored)
    if refresh:
        need = [t for t in ts if t not in have or not have[t][1]]

        def get(t):
            try:
                return t, events.fetch(syms[t])
            except Exception:  # noqa: BLE001  offline / unknown symbol: keep what is stored
                return t, None
        with ThreadPoolExecutor(6) as ex:
            got = [x for x in ex.map(get, need) if x[1]]

        def save(conn):
            conn.executemany("INSERT OR REPLACE INTO pf_events VALUES (?,?,?)",
                             [(syms[t], json.dumps(d), time.time()) for t, d in got])
            conn.commit()
        if got:
            _wl(save)
            have.update({t: (d, True) for t, d in got})
    today = dt.date.today().isoformat()
    hi = (dt.date.today() + dt.timedelta(days=days)).isoformat()
    out = []
    for t in ts:
        d = (have.get(t) or (None,))[0]
        if not d:
            continue
        dates = [x for x in d.get("earnings") or [] if today <= x <= hi]
        if dates:
            out.append(dict(ticker=t, type="earnings", date=dates[0], confirmed=len(dates) == 1,
                            eps_avg=d.get("eps_avg"), revenue_avg=d.get("revenue_avg")))
        if d.get("ex_dividend") and today <= d["ex_dividend"] <= hi:
            out.append(dict(ticker=t, type="ex_dividend", date=d["ex_dividend"], amount=d.get("last_dividend")))
        if d.get("dividend_date") and today <= d["dividend_date"] <= hi:
            out.append(dict(ticker=t, type="dividend", date=d["dividend_date"], amount=d.get("last_dividend")))
    out.sort(key=lambda e: (e["date"], e["ticker"]))
    return dict(events=out, missing=[t for t in ts if t not in have])


def _held_tickers():
    """Tickers of your holdings (Trading 212 or your own trades; US listings only), for the Home calendar and
    the Earnings page. US shares have no pf_symbols row (AAPL_US_EQ is AAPL without a lookup)."""
    try:
        rows = q("SELECT DISTINCT p.t212_ticker, s.yahoo FROM pf_positions p LEFT JOIN pf_symbols s ON s.t212_ticker=p.t212_ticker")
    except Exception:  # noqa: BLE001  no portfolio tables yet
        return []
    out = []
    for r in rows:
        us = portfolio.app_ticker(r["t212_ticker"], None)
        t = _norm_ticker(us) if us else r.get("yahoo")
        if t and re.fullmatch(r"[A-Z][A-Z0-9\-]{0,6}", t):
            out.append(t)
    return list(dict.fromkeys(out))


@app.get("/api/coming-up")
def coming_up(refresh: int = 0, days: int = 30):
    """Home: earnings and dividend dates of everything you watch or hold, the next `days` days."""
    watched = [w["ticker"] for w in _wl(watchlist.tickers)]
    held = _held_tickers()
    ts = list(dict.fromkeys(held + watched))
    out = _ticker_events(ts, refresh, max(1, min(days, 90)))
    names = {r["ticker"]: r["name"] for r in _ticker_rows()}
    for e in out["events"]:
        e["name"] = names.get(e["ticker"], e["ticker"])
        e["held"] = e["ticker"] in held
    return out


@app.get("/api/earnings-calendar")
def earnings_calendar(scope: str = "all", refresh: int = 0):
    """The Earnings page (stockterminal/earnings_cal.py): next results and past surprises of the companies on your
    watchlists and in your portfolio. scope: all | watch (every list) | held | a list id. ETFs are left out."""
    from .. import earnings_cal
    lists = _wl(watchlist.lists)
    held = set(_held_tickers())
    if scope == "held":
        ts = sorted(held)
    elif scope == "watch":
        ts = [t for x in lists for t in x["tickers"]]
    elif scope.isdigit():
        ts = next((x["tickers"] for x in lists if x["id"] == int(scope)), None)
        if ts is None:
            raise HTTPException(404, "That watchlist no longer exists")
    else:
        ts = [t for x in lists for t in x["tickers"]] + sorted(held)
    ts = list(dict.fromkeys(ts))
    etfs = {r["ticker"] for r in q(f"SELECT ticker FROM etfs WHERE ticker IN ({','.join('?' * len(ts))})", ts)} if ts else set()
    stocks = [t for t in ts if t not in etfs]
    conn = db.connect()
    try:
        out = earnings_cal.build(conn, stocks, refresh=bool(refresh))
    finally:
        conn.close()
    names = {r["ticker"]: r["name"] for r in _ticker_rows()}
    on = {}
    for x in lists:
        for t in x["tickers"]:
            on.setdefault(t, []).append(x["name"])
    out["companies"] = [dict(c, ticker=t, name=names.get(t) or t, held=t in held, lists=on.get(t, []))
                        for t, c in out["companies"].items()]
    out.update(scope=scope, etfs=sorted(etfs), held_count=len(held),
               lists=[dict(id=x["id"], name=x["name"], count=len(x["tickers"])) for x in lists])
    return out


@app.post("/api/watchlists/{list_id}/export")
def wl_export(list_id: int):
    """The list as a CSV file (Excel) in the Downloads folder."""
    name_of = {r["ticker"]: niceish(r["name"]) for r in _ticker_rows()}
    lists = {x["id"]: x["name"] for x in _wl(watchlist.lists)}
    text = _wl(watchlist.to_csv, list_id, name_of)
    name = re.sub(r"[^\w\-. ,()+&]+", "_", f"Watchlist - {lists.get(list_id, list_id)}").strip(" ._")[:80]
    d = export_dir()
    path = d / f"{name}.csv"
    n = 2
    while path.exists():
        path = d / f"{name} ({n}).csv"
        n += 1
    path.write_bytes(("﻿" + text).encode("utf-8"))
    return dict(path=str(path))


def niceish(n):
    """SEC names are upper case ('APPLE INC.'): 'Apple Inc.' reads better in a file."""
    return n.title() if n and n == n.upper() else (n or "")


@app.post("/api/watchlists/{list_id}/import")
def wl_import(list_id: int, payload: dict):
    """Tickers from a CSV file or pasted text onto a list. Returns how many were new and which were skipped."""
    rows = watchlist.parse_import(str(payload.get("text") or "")[:500_000])
    if not rows:
        raise HTTPException(400, "No tickers found. Use a CSV file with a 'Ticker' or 'Symbol' column, "
                                 "or tickers separated by commas or new lines.")
    known = {r["ticker"] for r in _ticker_rows()}
    added = _wl(watchlist.import_rows, list_id, rows,
                lambda t: (q("SELECT close FROM prices WHERE ticker=? AND close IS NOT NULL ORDER BY date DESC LIMIT 1", (t,)) or [{}])[0].get("close"))
    return dict(added=added, found=len(rows), unknown=[r["ticker"] for r in rows if r["ticker"] not in known],
                lists=_wl(watchlist.lists))


NEW_UI = STATIC / "new" / "index.html"     # the new design: built from ui/ (npm run build), the files are in git


def _page(html):
    from fastapi.responses import HTMLResponse
    theme = settings.read()["theme"]
    if theme in ("light", "dark"):           # set the saved theme before the page draws, so it never flashes
        html = html.replace('data-theme="light"', f'data-theme="{theme}"', 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/old")
def old_index():
    # the old screen was removed on 2026-11-17; an old bookmark lands on the app
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/")


@app.get("/new")
@app.get("/")
def index():
    if not NEW_UI.exists():
        from fastapi.responses import HTMLResponse
        return HTMLResponse("<p>The screen is missing (stockterminal/app/static/new). Run <code>git pull</code> or "
                            "<code>cd ui &amp;&amp; npm run build</code>.</p>", status_code=500)
    return _page(NEW_UI.read_text(encoding="utf-8"))
