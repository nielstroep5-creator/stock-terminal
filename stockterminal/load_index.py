"""Load a whole index (or any list of tickers) in one go: resumable, one company at a time.

Usage:
    python -m stockterminal.load_index sp500                  # S&P 500 (list from Wikipedia)
    python -m stockterminal.load_index sp400                  # S&P MidCap 400
    python -m stockterminal.load_index popular                # about 30 well-known companies (a quick start)
    python -m stockterminal.load_index my_list.txt            # tickers from a file (one per line / comma separated)
    python -m stockterminal.load_index sp500 --limit 20       # only the first 20 not loaded yet (a test run)
    python -m stockterminal.load_index sp500 --no-backfill    # skip phase 2
    python -m stockterminal.load_index sp500 --retry-failed   # try the failed ones again
    python -m stockterminal.load_index sp500 --status         # show progress, load nothing

Phase 1: every company with the last RECENT_YEARS years of filings (prices, financials, valuation,
         analyst data and recent segments: usable in the app right away).
Phase 2: the older filings of every company (segments and company-specific lines back to 2009).
Then a quality report: data/reports/<name>_quality.md

Stop it any time (Ctrl+C or close the window) and start it again: it continues where it stopped.
Companies already in the database are skipped. Each company runs in its own process with a time
limit, so one company that hangs or crashes cannot stop the rest; it is retried once and then
listed as failed in data/logs/load_<name>_progress.json (and in the report).
"""
import argparse
import io
import json
import subprocess
import sys
import time
from datetime import date, datetime

import requests

from . import db
from .config import DATA_DIR, module_cmd
from .fetch import norm_ticker

RECENT_YEARS = 5
TIMEOUT_S = {"load": 20 * 60, "backfill": 30 * 60}
ATTEMPTS = 2
LOG_DIR = DATA_DIR / "logs"
INDEX_DIR = DATA_DIR / "indexes"
WIKI = {"sp500": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        "sp400": "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies"}
# a quick start for a new install (Home "Get started"): about 30 well-known companies, a few minutes
BUILTIN = {"popular": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "BRK-B", "TSLA", "AVGO", "JPM", "V", "MA",
                       "UNH", "LLY", "JNJ", "PG", "KO", "PEP", "COST", "WMT", "HD", "MCD", "XOM", "CVX", "ABBV",
                       "MRK", "ORCL", "NFLX", "DIS", "O"]}
LIST_MAX_AGE_DAYS = 7


# --------------------------------------------------------------------------- ticker lists
def index_tickers(name):
    """Tickers of a named index (cached for a week) or of a text file."""
    from pathlib import Path
    p = Path(name)
    if p.suffix and p.exists():
        raw = p.read_text(encoding="utf-8").replace(",", "\n").split()
        return list(dict.fromkeys(norm_ticker(t) for t in raw if t.strip()))
    if name in BUILTIN:
        return list(BUILTIN[name])
    if name not in WIKI:
        raise SystemExit(f"Unknown index '{name}'. Known: {', '.join([*WIKI, *BUILTIN])} (or give a .txt file)")
    cache = INDEX_DIR / f"{name}.csv"
    fresh = cache.exists() and (time.time() - cache.stat().st_mtime) < LIST_MAX_AGE_DAYS * 86400
    if not fresh:
        try:
            import pandas as pd
            html = requests.get(WIKI[name], timeout=60,
                                headers={"User-Agent": "StockTerminal/1.0 (personal research tool)"}).text
            table = pd.read_html(io.StringIO(html))[0]
            col = next(c for c in table.columns if str(c).lower() in ("symbol", "ticker"))
            name_col = next((c for c in table.columns if "security" in str(c).lower()), None)
            out = table[[col] + ([name_col] if name_col else [])]
            out.columns = ["ticker"] + (["name"] if name_col else [])
            if len(out) < 350:
                raise RuntimeError(f"only {len(out)} rows")
            INDEX_DIR.mkdir(parents=True, exist_ok=True)
            out.to_csv(cache, index=False)
        except Exception as e:  # noqa: BLE001
            if not cache.exists():
                raise SystemExit(f"Could not download the {name} list: {e}")
            print(f"! could not refresh the {name} list ({e}); using the saved one from "
                  f"{datetime.fromtimestamp(cache.stat().st_mtime):%Y-%m-%d}")
    lines = cache.read_text(encoding="utf-8").splitlines()[1:]
    return list(dict.fromkeys(norm_ticker(l.split(",")[0]) for l in lines if l.strip()))


# --------------------------------------------------------------------------- progress
class Progress:
    def __init__(self, name):
        self.path = LOG_DIR / f"load_{name}_progress.json"
        try:
            self.d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.d = {}

    def get(self, t):
        return self.d.setdefault(t, {"status": "todo", "attempts": 0})

    def save(self):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.d, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def counts(self, tickers):
        c = {}
        for t in tickers:
            s = self.d.get(t, {}).get("status", "todo")
            c[s] = c.get(s, 0) + 1
        return c


def state_in_db(conn, ticker):
    """'loaded' (has financial statements), 'no_sec' (prices only / no SEC data) or None."""
    row = conn.execute("SELECT cik FROM companies WHERE ticker=?", (ticker,)).fetchone()
    if row and conn.execute("SELECT 1 FROM financials WHERE cik=? LIMIT 1", (row[0],)).fetchone():
        return "loaded"
    if conn.execute("SELECT 1 FROM prices WHERE ticker=? LIMIT 1", (ticker,)).fetchone():
        return "no_sec"
    return None


def run_one(args, log, timeout):
    """Run `python -m stockterminal.fetch <args>` in its own process. Returns (ok, seconds, last line)."""
    t0 = time.time()
    cmd = module_cmd("stockterminal.fetch", *args)
    log.write(f"\n### {datetime.now():%Y-%m-%d %H:%M:%S} {' '.join(args)}\n")
    log.flush()
    try:
        r = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=timeout,
                           env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
        ok, msg = r.returncode == 0, f"exit code {r.returncode}"
    except subprocess.TimeoutExpired:
        ok, msg = False, f"timeout after {timeout // 60} min"
    log.flush()
    return ok, time.time() - t0, msg


# --------------------------------------------------------------------------- main
def main(argv=None):
    p = argparse.ArgumentParser(description="Load a whole index, resumable.")
    p.add_argument("index", help="sp500, or a .txt file with tickers")
    p.add_argument("--limit", type=int, help="load at most N companies in phase 1 (test run)")
    p.add_argument("--no-backfill", action="store_true", help="skip phase 2 (older filings)")
    p.add_argument("--retry-failed", action="store_true", help="try failed companies again")
    p.add_argument("--status", action="store_true", help="only show progress")
    a = p.parse_args(argv)

    name = a.index.rsplit(".", 1)[0].replace("\\", "/").split("/")[-1]
    tickers = index_tickers(a.index)
    prog = Progress(name)
    conn = db.connect()
    print(f"{name}: {len(tickers)} tickers. Progress: {prog.counts(tickers)}")
    if a.status:
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = open(LOG_DIR / f"load_{name}_{date.today()}.log", "a", encoding="utf-8")
    t_start = time.time()

    # Several tickers of ONE company (share classes: GOOGL/GOOG, FOXA/FOX, NWSA/NWS) share one SEC
    # number and one set of statements: load the company once, under the ticker already loaded,
    # else the last one alphabetically (the A shares: GOOGL, FOXA, NWSA).
    from . import sec
    sec.load_tickers(conn)
    by_cik = {}
    for t in tickers:
        cik = sec.lookup_cik(conn, t)
        if cik:
            by_cik.setdefault(cik, []).append(t)
    twin_of = {}
    for cik, ts in by_cik.items():
        if len(ts) > 1:
            row = conn.execute("SELECT ticker FROM companies WHERE cik=?", (cik,)).fetchone()
            keep = row[0] if row and row[0] in ts else sorted(ts)[-1]
            twin_of.update({t: keep for t in ts if t != keep})

    # ---- phase 1
    todo = []
    for t in tickers:
        st = prog.get(t)
        if t in twin_of:
            st.update(status="same_company", note=f"same company as {twin_of[t]} (other share class)")
            continue
        if st["status"] in ("loaded", "backfilled", "no_sec", "not_found"):
            continue
        if st["status"] == "failed" and not a.retry_failed:
            continue
        have = state_in_db(conn, t)
        if have == "loaded":                       # loaded earlier (by hand or a previous run)
            st.update(status="loaded", note="already in database")
            continue
        if a.retry_failed and st["status"] == "failed":
            st["attempts"] = 0
        todo.append(t)
    prog.save()
    if a.limit:
        todo = todo[:a.limit]
    done_s = []
    for i, t in enumerate(todo, 1):
        st = prog.get(t)
        while True:
            st["attempts"] += 1
            ok, secs, msg = run_one([t, "--recent-years", str(RECENT_YEARS)], log, TIMEOUT_S["load"])
            have = state_in_db(conn, t)
            if have == "loaded":
                if not conn.execute("SELECT 1 FROM prices WHERE ticker=? LIMIT 1", (t,)).fetchone():
                    if st["attempts"] < ATTEMPTS:      # Yahoo throttled us: wait, then once more
                        time.sleep(60)
                        continue
                    st["note"] = "no prices yet (Yahoo); the daily update adds them"
                st.update(status="loaded", seconds=round(secs), error=None)
                done_s.append(secs)
                break
            if ok and have == "no_sec":
                st.update(status="no_sec", seconds=round(secs),
                          error="no SEC financial statements (not in SEC list, fund/ETF or foreign filer); prices only")
                break
            if ok and have is None:                # nothing anywhere: unknown ticker, no point retrying
                st.update(status="not_found", seconds=round(secs), error="not found at SEC or Yahoo")
                break
            st.update(status="failed", seconds=round(secs), error=msg if not ok else "no financial statements stored")
            if st["attempts"] >= ATTEMPTS:
                break
            time.sleep(30)                         # e.g. Yahoo / SEC throttling: wait, then once more
        prog.save()
        avg = sum(done_s) / len(done_s) if done_s else 0
        left = (len(todo) - i) * avg
        print(f"[{i}/{len(todo)}] {t}: {st['status']} in {st.get('seconds', 0)}s"
              f"{' - ' + st['error'] if st.get('error') else ''}"
              f"   (avg {avg:.0f}s, ~{left / 60:.0f} min left in phase 1)", flush=True)

    # ---- phase 2: older filings
    if not a.no_backfill and not a.limit:
        todo2 = [t for t in tickers if prog.get(t)["status"] == "loaded"]
        for i, t in enumerate(todo2, 1):
            st = prog.get(t)
            ok, secs, msg = run_one([t, "--backfill"], log, TIMEOUT_S["backfill"])
            if ok:
                st.update(status="backfilled", backfill_seconds=round(secs))
            else:
                st["backfill_error"] = msg        # stays 'loaded': usable, retried on the next run
            prog.save()
            print(f"[backfill {i}/{len(todo2)}] {t}: {'ok' if ok else msg} in {secs:.0f}s", flush=True)

    # ---- report
    from . import quality_report
    c = prog.counts(tickers)
    failed = [f"{t} ({prog.d[t].get('error')})" for t in tickers if prog.d.get(t, {}).get("status") == "failed"]
    extra = (f"Load progress: {c}. This run took {(time.time() - t_start) / 60:.0f} min."
             + (f"\n\n**Failed:** {'; '.join(failed)}" if failed else ""))
    loaded = [t for t in tickers if state_in_db(conn, t)]
    path, _ = quality_report.write(conn, loaded, DATA_DIR / "reports" / f"{name}_quality.md",
                                   title=f"{name} data quality", extra=extra)
    print(f"\nDone. Progress: {c}\nReport: {path}")
    log.close()


if __name__ == "__main__":
    main()
