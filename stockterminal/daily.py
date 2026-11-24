"""Daily update of every loaded ticker (run by the Windows scheduled task, or by hand):

    python -m stockterminal.daily            # all loaded tickers
    python -m stockterminal.daily AAPL MSFT  # only these
    python -m stockterminal.daily --force    # again, although it already ran today

1. prices for all tickers in one batched Yahoo request (last month; full history only after a split)
   + daily valuation recalculated where the price changed
2. analyst snapshot per ticker (Yahoo has no batch endpoint for this)
3. SEC: the daily filing index shows who filed a new 10-K / 10-Q / 20-F / 40-F; only those companies
   get the full download (per-company check of the recent-filings list on a first run or after a long gap)
4. shared company list (companies.txt on GitHub, see shared.py): upload companies added here, load
   companies someone else added

Output goes to the console and to data/logs/daily_YYYY-MM-DD.log.
"""
import datetime as dt
import os
import sys
import time

from . import update as up
from .config import DATA_DIR

LOG_DIR = DATA_DIR / "logs"
LOCK = LOG_DIR / "daily.lock"          # one run at a time (scheduled task, app start, by hand)
LAST = LOG_DIR / "daily_last_run.txt"  # date of the last completed run of all tickers
STALE_LOCK_H = 2


def last_run():
    try:
        return LAST.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def due_today():
    """True when the full daily update has not completed today and no run is in progress."""
    return last_run() != dt.date.today().isoformat() and not _locked()


def _locked():
    try:
        return (time.time() - LOCK.stat().st_mtime) < STALE_LOCK_H * 3600
    except OSError:
        return False


def _acquire():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if LOCK.exists() and not _locked():
        LOCK.unlink(missing_ok=True)              # left behind by a crashed run
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    return True


ALERTS = DATA_DIR / "reports" / "quality_alerts.md"


def _quality(conn, t):
    """(score, {"check: period", ...}) of a ticker's last consistency check, or None."""
    import json
    r = conn.execute("SELECT q.score, q.details FROM data_quality q JOIN companies c ON c.cik=q.cik "
                     "WHERE c.ticker=?", (t,)).fetchone()
    if not r:
        return None
    issues = json.loads(r[1] or "{}").get("issues", [])
    return r[0], {f"{i['text']}: {w}" for i in issues for w in i.get("where", [])}


def _write_alerts(alerts):
    """Newest first: which numbers of a new filing did not pass a check (to look at by hand)."""
    lines = [f"## {dt.date.today().isoformat()}", ""]
    for t, b, a, new in alerts:
        lines.append(f"- **{t}** quality {b if b is not None else '-'}% -> {a}%")
        lines += [f"  - {x}" for x in new[:12]]
    old = ALERTS.read_text(encoding="utf-8").split("\n", 2)[2] if ALERTS.exists() else ""
    ALERTS.parent.mkdir(parents=True, exist_ok=True)
    ALERTS.write_text("# New data warnings after daily updates\n\n" + "\n".join(lines) + "\n\n" + old,
                      encoding="utf-8")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    force = "--force" in argv
    argv = [a for a in argv if not a.startswith("--")]
    if not argv and not force:
        from . import settings
        if not settings.read().get("daily_update", True):
            print("The daily update is switched off (Settings, Data & accounts). Nothing to do.")
            return
    if not argv and not force and last_run() == dt.date.today().isoformat():
        print("Already updated today (use --force to run again).")
        return
    if not _acquire():
        print("Another daily update is running.")
        return
    try:
        _run(argv)
    finally:
        LOCK.unlink(missing_ok=True)


def _run(argv):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"daily_{dt.date.today().isoformat()}.log"
    f = open(path, "a", encoding="utf-8")

    def say(msg):
        line = f"{dt.datetime.now():%H:%M:%S} {msg}"
        print(line, flush=True)
        f.write(line + "\n")
        f.flush()
        try:                     # still alive: a long first run (hours of SEC downloads) keeps its lock
            os.utime(LOCK)
        except OSError:
            pass

    t0 = time.time()
    conn = up.connect()
    tickers = [t.upper() for t in argv] or up.all_tickers(conn)
    companies = {t for (t,) in conn.execute("SELECT ticker FROM companies WHERE ticker IS NOT NULL")}
    say(f"Daily update of {len(tickers)} tickers")

    s = time.time()
    changed = up.batch_prices(conn, tickers)
    failed = [t for t, v in changed.items() if v is None]   # e.g. not trading yet (IPO pending)
    say(f"prices: {sum(1 for v in changed.values() if v)} updated, {len(failed)} failed "
        f"{failed if failed else ''}({time.time() - s:.1f}s)")

    s = time.time()
    traded = {t for (t,) in conn.execute("SELECT DISTINCT ticker FROM prices")}
    for t in tickers:
        if t not in companies or t not in traded:     # no analyst data before a stock trades
            continue
        r = up.step(conn, t, "analyst", lambda: up.update_analyst(conn, t))
        if not r["ok"]:
            say(f"analyst {t}: failed - {r['error']}")
    say(f"analyst snapshots: done ({time.time() - s:.1f}s)")

    try:                                             # Yahoo's company names, once for companies that have none yet
        from . import prices
        prices.fill_display_names(conn, say)
        prices.fill_missing_sectors(conn, say)
    except Exception as e:  # noqa: BLE001
        say(f"Company names / sectors: skipped - {e}")

    try:                                             # logos of companies / ETFs added since the last run
        from . import logos, settings
        if settings.read().get("show_logos", True):
            logos.fill_missing(say, limit=600)
    except Exception as e:  # noqa: BLE001  a logo never stops the update
        say(f"logos: skipped - {e}")

    from . import etf                                # ETF holdings / sectors: once a week is enough
    for t in etf.all_etfs(conn):
        if t in tickers and etf.profile_due(conn, t):
            r = up.step(conn, t, "etf", lambda: etf.load_profile(conn, t))
            say(f"ETF {t}: {'profile updated' if r['ok'] else 'failed - ' + r['error']}")
    from . import holdings                           # full holdings: daily files daily, SEC reports weekly
    for t in etf.all_etfs(conn):
        if t in tickers and holdings.due(conn, t):
            r = up.step(conn, t, "etf_holdings", lambda: holdings.load(conn, t))
            say(f"ETF {t} holdings: {'updated' if r['ok'] else 'failed - ' + r['error']}")

    # SEC: the daily filing index (one request per day for all companies) says who filed a new 10-K /
    # 10-Q / 20-F / 40-F; only those companies get the full (large) companyfacts download
    s = time.time()
    due, todo = up.sec_due_all(conn, tickers)
    for t in todo:                                   # index not usable (first run / long gap): per company
        d, why = up.sec_due(conn, t)
        if d:
            due[t] = why
    alerts = []
    for t, why in sorted(due.items()):
        say(f"SEC {t}: {why} - downloading")
        before = _quality(conn, t)
        r = up.step(conn, t, "sec", lambda: up.update_sec(conn, t))
        say(f"SEC {t}: {'ok' if r['ok'] else 'failed - ' + r['error']} ({r['seconds']}s)")
        # the consistency checks ran on the new numbers (every load does); report what got worse
        after = _quality(conn, t)
        if after:
            new = sorted(after[1] - (before[1] if before else set()))
            say(f"quality {t}: {before[0] if before else '-'}% -> {after[0]}%"
                + (f", new warnings: {'; '.join(new[:5])}" if new else ""))
            if new:
                alerts.append((t, before[0] if before else None, after[0], new))
    if alerts:
        _write_alerts(alerts)
        say(f"{len(alerts)} companies with new data warnings -> {ALERTS}")
    say(f"SEC: {len(due)} new filings, {len(todo)} companies checked one by one ({time.time() - s:.1f}s)")

    # accuracy check: the new numbers of companies that filed, next to Yahoo's (accuracy.py)
    if due:
        from . import accuracy
        s = time.time()
        try:
            acc = accuracy.check(conn, sorted(set(due) & companies))
            say(f"accuracy check: {int(acc.ok.sum())} of {int(acc['diff_%'].notna().sum())} numbers match Yahoo "
                f"({time.time() - s:.1f}s)")
        except Exception as e:  # noqa: BLE001
            say(f"accuracy check: skipped - {e}")

    # market cap checks (new prices, new Yahoo snapshot, new filings): warnings only, see mcapcheck.py
    s = time.time()
    try:
        from . import mcapcheck
        warn = mcapcheck.run(conn, sorted(set(tickers) & companies), report=not argv)
        say(f"market cap check: {len(warn)} warnings ({time.time() - s:.1f}s)"
            + (f" -> {mcapcheck.REPORT}" if not argv else ""))
    except Exception as e:  # noqa: BLE001  a check never stops the rest of the daily update
        say(f"market cap check: failed: {e}")

    if not argv:                                     # shared company list (companies.txt on GitHub)
        s = time.time()
        try:
            from . import shared
            r = shared.sync(conn, say)
            say(f"shared list: {len(r['uploaded'])} uploaded, {len(r['loaded'])} loaded ({time.time() - s:.1f}s)")
        except Exception as e:  # noqa: BLE001  no internet / git problem: the portfolio and alerts still run
            say(f"shared list: failed: {e}")

    if not argv:                                     # your own Trading 212 portfolio, when connected
        from . import own, trading212
        if trading212.status()["connected"]:
            s = time.time()
            try:
                r = trading212.sync(conn, say)
                say(f"Trading 212: {r} ({time.time() - s:.1f}s)")
                from . import pf_history
                h = pf_history.build()                   # price history for the Portfolio chart, ready at start
                say(f"portfolio history: {len(h.get('dates') or [])} days, {len(h.get('warnings') or [])} warnings")
            except Exception as e:  # noqa: BLE001  never stops the rest of the daily update
                say(f"Trading 212: sync failed: {e}")
        elif own.active(conn):                       # or your own trades (own.py): fresh prices and dividends
            try:
                own.rebuild(conn, say=say)
                from . import pf_history
                pf_history.build()
            except Exception as e:  # noqa: BLE001
                say(f"own trades: update failed: {e}")
        if trading212.status()["connected"] or own.active(conn):
            try:                                     # alerts (Alerts page, phone): after the sync, with fresh prices
                from . import alerts
                alerts.check(refresh=True, say=say)
            except Exception as e:  # noqa: BLE001
                say(f"alerts: failed: {e}")

    conn.close()
    if not argv:
        LAST.write_text(dt.date.today().isoformat(), encoding="utf-8")
    say(f"Done in {time.time() - t0:.1f}s")
    f.close()


if __name__ == "__main__":
    main()
