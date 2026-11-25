"""Quick self-test: does the app still start and answer? Run before every push.

    python -m stockterminal.selftest           # or double-click "Run self-test.bat"

Checks (about 10-20 seconds, no internet needed):
  1. every Python file compiles and every module imports
  2. no data, contact email or PC-specific path is committed to git
  3. the screen (stockterminal/app/static/new, built from ui/) is complete and not older than its source
  4. the local server starts and its pages / API answer without errors; checks that need data
     (a stock, an ETF) use whatever is in this PC's database and are skipped when it is empty

Exit code 0 = all good, 1 = something is broken (the lines marked FAIL say what).
"""
import hashlib
import importlib
import json
import pkgutil
import py_compile
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "stockterminal"
results = []


def check(name, fn):
    t = time.time()
    try:
        note = fn()
        results.append((True, name))
        print(f"  ok    {name}" + (f"  ({note})" if note else "") + f"  [{time.time() - t:.1f}s]")
    except Exception as e:  # noqa: BLE001
        results.append((False, name))
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")


def version_notes():
    """The version in stockterminal/__init__.py is MAJOR.MINOR.PATCH (optionally -dev, -rc1, ...) and
    CHANGELOG.md has a section for it, so every release ships with its notes."""
    from stockterminal import __version__ as v
    m = re.fullmatch(r"(\d+\.\d+\.\d+)(-(dev|rc\d+))?", v)
    if not m:
        raise RuntimeError(f"version {v!r} is not like 1.2.3, 1.2.3-dev or 1.2.3-rc1")
    notes = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if not re.search(rf"^## {re.escape(m.group(1))}\b", notes, re.M):
        raise RuntimeError(f"CHANGELOG.md has no '## {m.group(1)}' section")
    if not (ROOT / "LICENSE").exists():
        raise RuntimeError("LICENSE file missing")
    return v


def compile_all():
    files = list(PKG.rglob("*.py"))
    for f in files:
        py_compile.compile(str(f), doraise=True)
    return f"{len(files)} files"


def import_all():
    n = 0
    for m in pkgutil.walk_packages([str(PKG)], "stockterminal."):
        if m.name.endswith(".selftest"):
            continue
        importlib.import_module(m.name)
        n += 1
    return f"{n} modules"


def git_files():
    git = shutil.which("git")
    if not git or not (ROOT / ".git").exists():
        return "skipped: not a git folder"
    files = subprocess.run([git, "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.splitlines()
    bad = [f for f in files if f.startswith("data/") or f == "sec_contact.txt" or f.endswith((".db", ".zip"))]
    if bad:
        raise RuntimeError("must not be in git: " + ", ".join(bad[:5]))
    # paths of one PC (C:\Users\..., /home/..., /Users/...) in code
    pc = re.compile(r"[A-Za-z]:\\\\?Users\\\\?|/home/[a-z]|/Users/[A-Za-z]")
    hits = []
    for f in files:
        if (f.endswith((".py", ".js", ".ts", ".tsx", ".bat", ".command", ".html", ".css")) and "vendor/" not in f
                and "static/new/" not in f and not f.endswith("selftest.py")):      # static/new is built from ui/
            text = (ROOT / f).read_text(encoding="utf-8", errors="ignore")
            if pc.search(text):
                hits.append(f)
    if hits:
        raise RuntimeError("a path of one PC in: " + ", ".join(hits))
    # secrets and personal email addresses: the repository is public
    secret = re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[0-9A-Z]{16}|xox[abp]-[A-Za-z0-9-]{10,}"
                        r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
                        r"|[A-Za-z0-9._%+-]+@(gmail|hotmail|outlook|live|yahoo|icloud|proton|protonmail)\.[a-z.]{2,}", re.I)
    leaks = []
    for f in files:
        if f.endswith((".png", ".ico", ".icns", ".woff", ".woff2", ".svg")) or f.endswith("selftest.py"):
            continue
        p = ROOT / f
        if p.is_file() and _has_match(p, secret):
            leaks.append(f)
    if leaks:
        raise RuntimeError("a key, token or personal email address in: " + ", ".join(leaks[:5]))
    return f"{len(files)} tracked files"


def _has_match(path, pattern):
    try:
        return bool(pattern.search(path.read_text(encoding="utf-8", errors="ignore")))
    except OSError:
        return False


NEW_UI = PKG / "app" / "static" / "new"
UI_INPUTS = ("src", "index.html", "package.json", "vite.config.ts")     # the same list as ui/scripts/hash.mjs


def ui_source_hash():
    """The fingerprint ui/scripts/hash.mjs writes into build.json, computed the same way."""
    ui = ROOT / "ui"
    files = []
    for i in UI_INPUTS:
        p = ui / i
        files += [f for f in p.rglob("*") if f.is_file()] if p.is_dir() else [p]
    h = hashlib.sha256()
    for rel, f in sorted((f.relative_to(ui).as_posix(), f) for f in files):
        h.update((rel + "\n").encode())
        h.update((f.read_bytes().decode("utf-8").replace("\r", "") + "\n").encode())
    return h.hexdigest()


def new_ui():
    """The new design is served from built files that are in git (so nobody needs Node.js to start the app).
    They must be complete, and rebuilt after every change in ui/ (cd ui, npm install, npm run build)."""
    page = NEW_UI / "index.html"
    if not page.exists():
        raise RuntimeError("stockterminal/app/static/new/index.html is missing: run `npm run build` in ui/")
    refs = re.findall(r'(?:src|href)="/static/new/([^"]+)"', page.read_text(encoding="utf-8"))
    missing = [r for r in refs if not (NEW_UI / r).exists()]
    if not refs or missing:
        raise RuntimeError("the page points at files that are missing: " + (", ".join(missing) or "no files at all"))
    if not (ROOT / "ui" / "src").exists():
        return f"{len(refs)} files, source folder ui/ not here: freshness not checked"
    built = json.loads((NEW_UI / "build.json").read_text(encoding="utf-8")).get("source")
    if built != ui_source_hash():
        raise RuntimeError("ui/ changed after the last build: run `npm run build` in ui/ and commit stockterminal/app/static/new")
    return f"{len(refs)} files, built from the current ui/ source"


def server():
    import requests
    import uvicorn

    from .app.server import app
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.05)
    if not srv.started:
        raise RuntimeError("server did not start")
    base = f"http://127.0.0.1:{port}"
    done = []

    def get(path, timeout=30):
        try:
            r = requests.get(base + path, timeout=timeout)
        except requests.exceptions.ReadTimeout:
            raise RuntimeError(f"{path} took longer than {timeout} s") from None
        if r.status_code != 200:
            raise RuntimeError(f"{path} -> HTTP {r.status_code}: {r.text[:200]}")
        done.append(path)
        return r

    try:
        get("/")
        if get("/old").url.endswith("/old"):                 # bookmarks of the removed old screen
            raise RuntimeError("/old does not lead to the app")
        page = get("/new").text                              # the screen and the files it loads
        refs = re.findall(r'(?:src|href)="(/static/new/[^"]+)"', page)
        if not refs:
            raise RuntimeError("/new does not give the new design")
        for r in refs:
            get(r)
        get("/api/settings")
        get("/api/about")                    # Settings > About: version
        get("/api/t212/sync")                # its sync status (also no internet)
        pf = get("/api/portfolio").json()    # Portfolio page from the database (live=0: no internet)
        if pf.get("positions"):              # and one holding's own page
            get("/api/portfolio/holding/" + pf["positions"][0]["t212_ticker"])
        get("/api/portfolio/history?refresh=0")   # its chart, from stored prices only
        get("/api/portfolio/own")            # your own trades (without Trading 212)
        get("/api/portfolio/allocation")     # its allocation tab, from stored data only
        get("/api/portfolio/xray")           # its X-ray tab, from stored data only
        get("/api/portfolio/calendar")       # its calendar tab, from stored data only
        get("/api/portfolio/costs")          # its costs tab, from stored data only
        get("/api/portfolio/analysis")       # its analysis tab, from stored data only
        get("/api/tax")                      # the box 3 page, from stored data only
        get("/api/alerts")                   # the Alerts page and the bell, from stored data only
        get("/api/alerts?light=1&limit=8&q=a&kind=earnings&origin=watchlist&unread=1")   # the bell, Home, the feed's filters
        get("/api/alerts/stocks")            # Rules: Your stocks
        get("/api/alerts/stock/AAPL")        # the bell on a stock page
        get("/api/t212/status")              # Trading 212 link (no internet: only reads the local file)
        cos = get("/api/companies").json()
        get("/api/etfs")
        get("/api/fields")
        # the Screener page, from stored data only. Its first answer builds the table of every company (about
        # 8 s for the S&P 500, much longer while the PC is busy starting the app), so it gets more time
        get("/api/screener", timeout=180)
        get("/api/sectors")                  # the Sectors page, from the same table
        for wl in get("/api/watchlists").json()["lists"][:3]:   # your watchlists, from stored data only
            get(f"/api/watchlists/{wl['id']}/rows")
            get(f"/api/watchlists/{wl['id']}/events")
        stock = next((c["ticker"] for c in cos if c.get("ticker")), None)
        if stock:                                   # pages of one company from this PC's database
            for p in (f"/api/company/{stock}", f"/api/statements/{stock}?period=FY", f"/api/metrics/{stock}?period=FY",
                      f"/api/valuation/{stock}", f"/api/dcf/{stock}", f"/api/prices/{stock}", f"/api/analyst/{stock}",
                      f"/api/fields?tickers={stock}", f"/api/series?tickers={stock}&field=item:revenue&period=FY",
                      f"/api/estimates_annual?tickers={stock}", f"/api/compare_table?tickers={stock}", f"/api/earnings/{stock}"):
                get(p)
        etfs = get("/api/etfs").json()
        etf = next((e.get("ticker") for e in etfs if isinstance(e, dict) and e.get("ticker")), None)
        if etf:
            get(f"/api/etf/{etf}")
        note = f"{len(done)} requests" + (f", stock {stock}" if stock else ", no companies in the database: data checks skipped")
        return note + (f", ETF {etf}" if etf else "")
    finally:
        srv.should_exit = True


def exports():
    """The PDF writer makes a valid file, and the box 3 export works on this PC's data (in memory, no files written)."""
    from . import pdf, tax
    doc = pdf.Pdf("selftest")
    doc.heading("Test € é")
    doc.table([("A", 100, "l"), ("B", 50, "r")], [["x", "1"]] * 80, bold_rows={0})
    raw = doc.output()
    assert raw.startswith(b"%PDF-") and raw.rstrip().endswith(b"%%EOF") and raw.count(b"/Type /Page ") >= 2, "PDF writer output is broken"
    d = tax.build()
    if d.get("empty"):
        return "PDF writer ok, no Trading 212 data: box 3 export skipped"
    year = max(int(y) for y in d["years"])
    for fmt in ("csv", "pdf"):
        name, raw = tax.export(year, fmt, {})
        assert raw and name, f"empty {fmt} export"
    return f"PDF writer ok, box 3 {year} as CSV and PDF"


def earnings():
    """The earnings watch reads revenue and EPS from a press release table, keeps only numbers that fit the
    expectation, and writes the alert text (offline, no database)."""
    from . import earnings_watch as ew
    html = ("<p>($ in millions, except per share data)</p><table><tr><td>Revenue</td><td>$</td><td>57,006</td></tr>"
            "<tr><td>Diluted earnings per share</td><td>$</td><td>1.30</td></tr></table>"
            "<p>Non-GAAP</p><table><tr><td>Revenue</td><td>$ 57,006</td></tr><tr><td>Diluted earnings per share</td><td>$ 1.32</td></tr></table>")
    exp = dict(eps=1.25, revenue=54.9e9)
    got = ew.press_numbers(ew.parse_press_release(html), exp)
    assert got == dict(revenue=57006e6, eps_adj=1.32, eps_gaap=1.30), got
    assert "revenue" not in ew.press_numbers(ew.parse_press_release(html), dict(eps=1.25, revenue=5.49e9)), "wrong unit accepted"
    s = ew.summary(dict(expect=exp, result={f"pr_{k}": v for k, v in got.items()}, official=None))
    assert s and s[0] == "beat" and "+3.8%" in s[1] and "+5.6%" in s[1], s
    return "press release table read, wrong unit refused, alert text ok"


def alert_checks():
    """The alert checks with made-up prices (in memory, no internet, nothing saved): price limits fire once and re-arm,
    daily moves (close and during the day), the dividend rule (a smaller interim dividend is no cut), old per-stock
    settings keys, quiet hours across midnight, the phone: one message per stock per day except limits and results, and
    when the morning summary is due."""
    import datetime as dt
    import sqlite3
    from . import alerts as al
    from . import db as dbm
    conn = sqlite3.connect(":memory:")
    conn.executescript(dbm.SCHEMA)
    dbm._migrate(conn)
    assert al.normalize_stock_prefs({"AAPL_US_EQ|below": 150, "wl:AAPL|below": 140, "wl:tsla|off": "move", "VWCEd_EQ|above": 9,
                                     "BRK_B_US_EQ|above": 500, "NVDA|below": None}) == \
        {"AAPL|below": 150, "TSLA|off": "move", "VWCEd_EQ|above": 9, "BRK.B|above": 500}, "old settings keys"
    rules = dict(al.DEFAULTS, ntfy_topic="test", quiet_on=False)
    per = {"NVDA": {"below": 100}, "KO": {"move": 2}}
    now = dt.datetime(2026, 11, 18, 15, 40)
    new = []
    add = al._adder(conn, per, now, new)
    t = dict(t212_ticker="NVDA_US_EQ", sid="NVDA", ticker="NVDA", name="NVIDIA", yahoo="NVDA", currency="USD", price=120, etf=False,
             origin="portfolio", live=dict(price=95, change=-0.07, day="2026-11-18", state="REGULAR", at="15:39"))
    al._limits(conn, t, rules, per, now, add)
    al._limits(conn, t, rules, per, now + dt.timedelta(minutes=5), add)
    assert [n["kind"] for n in new] == ["limit"] and "$95.00 at 15:39" in new[0]["body"], new
    t["live"]["price"] = 101                          # back, but not 2% past the limit: still not armed
    al._limits(conn, t, rules, per, now + dt.timedelta(minutes=10), add)
    t["live"]["price"] = 103                          # armed again
    al._limits(conn, t, rules, per, now + dt.timedelta(minutes=15), add)
    t["live"]["price"] = 99
    al._limits(conn, t, rules, per, now + dt.timedelta(minutes=20), add)
    assert [n["kind"] for n in new] == ["limit", "limit"], f"re-arming: {new}"
    al._move_today(t, rules, per, add)
    assert new[-1]["kind"] == "move" and "today" in new[-1]["title"], new[-1]
    t["live"]["state"] = "POST"                       # the morning check: same day, same key, nothing new
    conn.execute("INSERT INTO pf_prices VALUES ('NVDA','2026-11-17',103,103,NULL), ('NVDA','2026-11-18',95.79,95.79,NULL)")
    n = len(new)
    al._move(conn, t, rules, per, "2026-11-19", add)
    assert len(new) == n, "the close repeated the move of the day"
    ko = dict(t, t212_ticker="KO_US_EQ", sid="KO", ticker="KO", name="Coca-Cola", yahoo="KO",
              live=dict(price=60, change=-0.025, day="2026-11-18", state="POST", at="22:00"))
    al._move(conn, dict(ko, origin="watchlist"), rules, per, "2026-11-18", add)
    assert new[-1]["title"].startswith("Coca-Cola −2.5%") and "for this stock" in new[-1]["body"], "own move % per stock"
    interim = [["2024-05-01", 0.5], ["2024-11-01", 1.0], ["2025-05-01", 0.5], ["2025-11-01", 1.0], ["2026-05-01", 0.55]]
    assert al.dividend_change(interim)[0] == 0, "a smaller interim dividend counted as a cut"
    cut = [["2025-02-01", 0.51], ["2025-05-01", 0.51], ["2025-08-01", 0.51], ["2025-11-01", 0.51], ["2026-02-01", 0.43]]
    assert al.dividend_change(cut)[0] < -0.15, "a real cut was missed"
    r = dict(rules, quiet_on=True, quiet_from="22:00", quiet_to="07:00")
    assert al._quiet(r, dt.datetime(2026, 1, 1, 23, 30)) and al._quiet(r, dt.datetime(2026, 1, 1, 6, 59)) \
        and not al._quiet(r, dt.datetime(2026, 1, 1, 7, 0)), "quiet hours across midnight"
    sent = []
    real_push = al.push
    al.push = lambda rules, title, body, **k: sent.append(title)
    try:
        conn.execute("UPDATE pf_alerts SET pushed='2026-11-18 09:00' WHERE kind='limit'")   # one message today already
        conn.execute("""INSERT INTO pf_alerts (key, created, kind, t212_ticker, name, title, body, level, push) VALUES
                        ('earnings|NVDA_US_EQ|2026-11-18','2026-11-18 22:10','earnings','NVDA_US_EQ','NVIDIA','NVIDIA results: beat','x',2,1)""")
        conn.execute("UPDATE pf_alerts SET pushed=NULL WHERE kind='move'")
        al.send_pending(conn, rules, dt.datetime(2026, 11, 18, 22, 15), [])
        assert "NVIDIA results: beat" in sent and not any(x.startswith("NVIDIA −") for x in sent), f"phone cap: {sent}"
        assert conn.execute("SELECT pushed FROM pf_alerts WHERE kind='move' AND t212_ticker='NVDA_US_EQ'").fetchone()[0] == "no"
    finally:
        al.push = real_push
    assert al._results_alert(conn, "NVDA_US_EQ", "2026-11-19"), "results alert a day apart not found"
    e = dict(earnings=["2026-11-20"])
    al._earnings(conn, dict(t, name="Tesla", t212_ticker="wl:TSLA"), e, rules, "2026-11-18", add)
    assert new[-1]["body"].startswith("No analyst estimates"), new[-1]
    from . import summary                             # morning summary: when it is due (summary.py)
    r = dict(rules, sum_daily=True, sum_weekly=True, sum_weekday=0, quiet_on=True, quiet_from="22:00", quiet_to="07:00")
    mon = dt.datetime(2026, 11, 23, 7, 5)
    assert summary.due(conn, r, mon) == "weekly" and summary.due(conn, r, mon.replace(hour=6)) is None, "summary: weekly / quiet hours"
    al._set_state(conn, "summary_week", "2026-W48")
    assert summary.due(conn, r, mon) == "daily", "summary: daily after the weekly"
    al._set_state(conn, "summary_day", "2026-11-23")
    assert summary.due(conn, r, mon) is None and summary.due(conn, dict(r, sum_weekly=False), dt.datetime(2026, 11, 28, 8)) is None, \
        "summary: twice a day, or on a Saturday"
    return "limits and re-arming, moves, dividend rule, old settings, quiet hours, phone cap, summary timing ok"


def own_trades():
    """A portfolio from your own trades (own.py) with made-up Yahoo prices (in memory, no internet, nothing saved):
    a split, a sale at average cost, a euro share in a dollar portfolio, dividends from the shares held, and a
    broker CSV with European numbers, day/month dates and lines that are not trades."""
    import sqlite3
    from . import db as dbm
    from . import own, tax, trading212
    conn = sqlite3.connect(":memory:")
    conn.executescript(dbm.SCHEMA)
    dbm._migrate(conn)
    days = ["2024-01-02", "2024-03-01", "2024-05-10", "2024-06-03", "2024-07-01", "2024-08-12", "2024-09-02"]
    aapl = dict(zip(days, [50, 55, 58, 60, 60, 65, 70]))          # already split-adjusted, as Yahoo gives them
    hist = {"AAPL": [(d, c, c, 2.0 if d == "2024-06-03" else None) for d, c in aapl.items()],
            "ASML.AS": [(d, 600 if d < "2024-09-02" else 700, None, None) for d in days],
            "USDEUR=X": [(d, 0.9 if d < "2024-09-02" else 0.92, None, None) for d in days]}
    download = lambda s, start: [r for r in hist[s] if r[0] >= start]            # noqa: E731
    divs = {"AAPL": [("2024-05-10", 0.25), ("2024-08-12", 0.25)], "ASML.AS": []}
    info = {"AAPL": dict(symbol="AAPL", name="Apple", currency="USD"), "ASML.AS": dict(symbol="ASML.AS", name="ASML", currency="EUR")}
    real_status = trading212.status
    trading212.status = lambda: dict(connected=False)
    try:
        own.add(conn, "2024-01-02", None, "buy", 10, 100, 1, info=info["AAPL"], download=download)
        own.add(conn, "2024-07-01", None, "sell", 4, 60, None, info=info["AAPL"], download=download)
        own.add(conn, "2024-03-01", None, "buy", 2, 600, None, info=info["ASML.AS"], download=download)
        r = own.rebuild(conn, download=download, div_download=lambda s: divs[s])
        assert r["trades"] == 3 and r["positions"] == 2 and r["dividends"] == 2, r
        pos = {p[0]: p for p in conn.execute("SELECT * FROM pf_positions")}
        a, e = pos["AAPL_US_EQ"], pos["ASML.AS"]
        assert abs(a[4] - 16) < 1e-9 and abs(a[8] - 1120) < 0.01 and abs(a[9] - 800.8) < 0.01, f"split / average cost: {a[:12]}"
        assert abs(e[8] - 1400 / 0.92) < 0.01 and abs(e[9] - 1200 / 0.9) < 0.01, f"euro share: {e[:12]}"
        realised = conn.execute("SELECT realised_pl FROM pf_trades WHERE side='SELL'").fetchone()[0]
        assert abs(realised - 39.8) < 0.01, f"realised {realised}"
        assert [round(x, 2) for x, in conn.execute("SELECT amount FROM pf_dividends ORDER BY paid_on")] == [5.0, 4.0], "dividends"
        assert trading212.meta(conn)["source"] == "own" and conn.execute("SELECT yahoo FROM pf_symbols WHERE t212_ticker='ASML.AS'").fetchone()
        # the box 3 page on these trades: in euros (the portfolio is in dollars), no cash, Yahoo's gross dividends
        t = tax.build(conn)
        Y = t["years"][2024]
        assert t["source"] == "own" and t["account_currency"] == "EUR" and not t["withheld_known"], t
        assert abs(Y["realised_total"] - 39.8 * 0.9) < 0.01 and abs(Y["dividends"]["gross"] - 9 * 0.9) < 0.01, Y
        assert Y["cash_start"] == 0 and Y["interest"] == 0 and Y["dividends"]["unknown_gross"] == 0, Y
        assert [r["country"] for r in Y["dividends_by_country"]] == ["United States"], Y["dividends_by_country"]
        for fmt in ("csv", "pdf"):
            name, raw = tax.export(2024, fmt, {}, conn)
            assert "own trades" in name and raw and (fmt == "pdf" or "Trading 212" not in raw.decode("utf-8")), name
        rows, skipped, order = own.parse_csv("Datum;Ticker;Actie;Aantal;Koers;Valuta;Kosten\n"
                                             "15/03/2024;AAPL;Koop;3;1.234,50;USD;0,50\n02/04/2024;AAPL;Dividend;;;USD;\n"
                                             "20/04/2024;AAPL;Verkoop;1;180;USD;\n")
        assert order == "dmy" and [(x["date"], x["side"], x["quantity"], x["price"], x["fees"]) for x in rows] == \
            [("2024-03-15", "BUY", 3, 1234.5, 0.5), ("2024-04-20", "SELL", 1, 180, None)] and len(skipped) == 1, (rows, skipped)
        assert own.parse_csv("Date,Ticker,Side,Quantity,Price\n2024-03-15,AAPL,buy,3,100\n")[2] == "ymd"
        assert own._looks_like_name("APPLE INC") and not own._looks_like_name("AAPL")
        # offline: the app's own (split-adjusted) closes become the prices as traded, with the split on its day
        conn.executemany("INSERT INTO prices (ticker, date, close, adj_close) VALUES (?,?,?,?)",
                         [("ZZZ", "2024-06-07", 12.0, 12.0), ("ZZZ", "2024-06-10", 12.5, 12.5)])
        conn.execute("INSERT INTO splits VALUES ('ZZZ', '2024-06-08', 10)")
        own._stored_prices(conn, ["ZZZ"], "2024-06-07")
        got = conn.execute("SELECT date, close, split FROM pf_prices WHERE symbol='ZZZ' ORDER BY date").fetchall()
        assert got == [("2024-06-07", 120.0, None), ("2024-06-10", 12.5, 10.0)], got
        res = own.import_csv(conn, own.EXAMPLE_CSV, download=download,
                             quotes=lambda syms: {s: (dict(regularMarketPrice=1, currency=info[s]["currency"], longName=s) if s in info else None) for s in syms})
        again = own.import_csv(conn, own.EXAMPLE_CSV, download=download,
                               quotes=lambda syms: {s: (dict(regularMarketPrice=1, currency=info[s]["currency"], longName=s) if s in info else None) for s in syms})
        assert res["added"] == 3 and again["added"] == 0, (res, again)
        for i, in conn.execute("SELECT id FROM pf_own_trades").fetchall():
            own.delete(conn, i)
        own.rebuild(conn, refresh=False)
        assert not conn.execute("SELECT 1 FROM pf_positions").fetchone() and "source" not in trading212.meta(conn), "empty again"
    finally:
        trading212.status = real_status
    return "split, average cost, euro share, dividends, box 3 in euros, CSV import (twice), offline prices, empty again ok"


def news_items():
    """News items in both shapes Yahoo has used are read, and items without a title or a safe link are left out (offline)."""
    from . import news
    new = {"id": "a", "content": {"title": "Results beat", "provider": {"displayName": "Reuters"}, "pubDate": "2026-11-20T21:05:00Z",
                                  "canonicalUrl": {"url": "https://finance.yahoo.com/a"}, "clickThroughUrl": {"url": "https://reuters.com/a"},
                                  "thumbnail": {"resolutions": [{"url": "https://i/1200.jpg", "width": 1200}, {"url": "https://i/170.jpg", "width": 170}]}}}
    old = {"uuid": "b", "title": "Old shape", "publisher": "Fool", "link": "https://fool.com/b", "providerPublishTime": 1763700000}
    a, b = news.item(new), news.item(old)
    assert (a["url"], a["source"], a["published"], a["thumb"]) == ("https://reuters.com/a", "Reuters", "2026-11-20T21:05:00Z", "https://i/170.jpg"), a
    assert b["title"] == "Old shape" and b["published"].startswith("2025-11-21"), b
    assert news.item({"title": "x", "link": "javascript:alert(1)"}) is None and news.item({"content": {"title": "no link"}}) is None, "unsafe links"
    return "both Yahoo shapes read, unsafe links left out"


FORM4 = b"""<?xml version="1.0"?>
<ownershipDocument><schemaVersion>X0508</schemaVersion><documentType>4</documentType>
<periodOfReport>2026-11-18</periodOfReport><aff10b5One>0</aff10b5One>
<issuer><issuerCik>0000320193</issuerCik><issuerName>Apple Inc.</issuerName><issuerTradingSymbol>AAPL</issuerTradingSymbol></issuer>
<reportingOwner><reportingOwnerId><rptOwnerCik>0001214156</rptOwnerCik><rptOwnerName>COOK TIMOTHY D</rptOwnerName></reportingOwnerId>
<reportingOwnerRelationship><isDirector>1</isDirector><isOfficer>1</isOfficer><officerTitle>Chief Executive Officer</officerTitle></reportingOwnerRelationship></reportingOwner>
<nonDerivativeTable>
<nonDerivativeTransaction><securityTitle><value>Common Stock</value></securityTitle><transactionDate><value>2026-11-18</value></transactionDate>
<transactionCoding><transactionFormType>4</transactionFormType><transactionCode>P</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>1000</value></transactionShares><transactionPricePerShare><value>150.00</value></transactionPricePerShare>
<transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
<postTransactionAmounts><sharesOwnedFollowingTransaction><value>5000</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
<ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature></nonDerivativeTransaction>
<nonDerivativeTransaction><securityTitle><value>Common Stock</value></securityTitle><transactionDate><value>2026-11-18</value></transactionDate>
<transactionCoding><transactionCode>P</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>1000</value></transactionShares><transactionPricePerShare><value>152.00</value></transactionPricePerShare>
<transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
<postTransactionAmounts><sharesOwnedFollowingTransaction><value>6000</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
<ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature></nonDerivativeTransaction>
<nonDerivativeTransaction><securityTitle><value>Common Stock</value></securityTitle><transactionDate><value>2026-11-19</value></transactionDate>
<transactionCoding><transactionCode>F</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>300</value></transactionShares><transactionPricePerShare><value>151</value></transactionPricePerShare>
<transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode></transactionAmounts>
<postTransactionAmounts><sharesOwnedFollowingTransaction><value>5700</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
<ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature></nonDerivativeTransaction>
</nonDerivativeTable>
<footnotes><footnote id="F1">Weighted average price.</footnote></footnotes></ownershipDocument>"""

INFOTABLE = b"""<?xml version="1.0"?><informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
<infoTable><nameOfIssuer>APPLE INC</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>037833100</cusip><value>60000000000</value>
<shrsOrPrnAmt><sshPrnamt>300000000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt><investmentDiscretion>DFND</investmentDiscretion></infoTable>
<infoTable><nameOfIssuer>APPLE INC</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>037833100</cusip><value>2000000000</value>
<shrsOrPrnAmt><sshPrnamt>10000000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt><investmentDiscretion>DFND</investmentDiscretion></infoTable>
<infoTable><nameOfIssuer>APPLE INC</nameOfIssuer><titleOfClass>CALL</titleOfClass><cusip>037833100</cusip><value>5</value>
<shrsOrPrnAmt><sshPrnamt>1</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt><putCall>Call</putCall></infoTable>
<infoTable><nameOfIssuer>BANK AMER CORP</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>060505104</cusip><value>38000000000</value>
<shrsOrPrnAmt><sshPrnamt>700000000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable>
</informationTable>"""


def ownership():
    """Insider trades and big investors (in memory, no internet): a Form 4 is read (a buy in two parts is one buy at the
    average price, the CEO's name and role), the insider-buy alert, a 13F table (classes added up, options left out,
    values in dollars), CUSIP check digits and the match of a famous investor's holding to the stock."""
    import datetime as dt
    import sqlite3
    from . import alerts as al, db as dbm, insiders, investors
    conn = sqlite3.connect(":memory:")
    conn.executescript(dbm.SCHEMA)
    dbm._migrate(conn)
    got = insiders.parse(FORM4)
    assert (got["owner"], got["role"], got["issuer_cik"], len(got["trades"])) == ("Timothy D Cook", "CEO, Director", 320193, 3), got
    assert insiders.raw_doc("xslF345X05/wk-form4_1.xml") == "wk-form4_1.xml" and insiders.raw_doc("x.txt") is None
    insiders.store(conn, 320193, "0001-26-000001", "2026-11-20", got)
    s = insiders.summary(conn, 320193, today=dt.date(2026, 11, 24))
    buy = [g for g in s["trades"] if g["code"] == "P"]
    assert len(buy) == 1 and buy[0]["shares"] == 2000 and abs(buy[0]["price"] - 151) < 1e-9 and buy[0]["value"] == 302000, buy
    assert abs(buy[0]["change"] - 0.5) < 1e-9 and s["buys_12m"]["value"] == 302000 and s["sells_12m"]["n"] == 0, s["buys_12m"]
    assert s["months"][-1]["buy"] == 302000 and len(s["months"]) == 25, s["months"][-2:]
    new = []
    add = al._adder(conn, {}, dt.datetime(2026, 11, 24, 7, 0), new)
    t = dict(t212_ticker="AAPL_US_EQ", sid="AAPL", ticker="AAPL", name="Apple", origin="portfolio", etf=False)
    rules = dict(al.DEFAULTS)
    al.insider_alerts(conn, t, 320193, rules, "2026-11-24", add)
    al.insider_alerts(conn, t, 320193, rules, "2026-11-24", add)
    assert len(new) == 1 and new[0]["title"] == "Apple: Timothy D Cook bought $302K" and "+50% on what they had" in new[0]["body"], new
    al.insider_alerts(conn, t, 320193, dict(rules, ins_min=500000), "2026-11-25", add)
    al.insider_alerts(conn, t, 320193, rules, "2026-12-10", add)
    assert len(new) == 1, "a small or old buy alerted"
    assert investors.cusip_ok("037833100") and investors.cusip_ok("02079K305") and not investors.cusip_ok("037833101")
    assert investors.cusips_in(b"<issuerCusip>037833100</issuerCusip>") == ["037833100"]
    assert investors.cusips_in(b"CUSIP No. 037833 10 0 ...") == ["037833100"], investors.cusips_in(b"CUSIP No. 037833 10 0 ...")
    assert investors.norm("Bank of America Corp") == investors.norm("BANK AMER CORP") == "BANK AMERICA"
    rows = investors.parse_13f(INFOTABLE, "2026-11-14")
    assert rows["037833100"]["shares"] == 310_000_000 and rows["037833100"]["value"] == 62e9 and len(rows) == 2, rows
    assert investors.parse_13f(INFOTABLE, "2022-11-14")["060505104"]["value"] == 38e12, "values in thousands before 2023"
    for accn, period, aapl in (("q3", "2026-09-30", 310e6), ("q2", "2026-06-30", 400e6)):
        conn.execute("INSERT INTO inv_filings VALUES (?,?,?,?,?,?)", (accn, 1067983, period, "2026-11-14", 100e9, 2))
        conn.execute("INSERT INTO inv_holdings VALUES (?,?,?,?,?,?)", (accn, "037833100", "APPLE INC", "COM", aapl, aapl * 200))
    conn.execute("INSERT INTO issuer_cusips VALUES (320193, '[\"037833100\"]', ?)", (dt.date.today().isoformat(),))
    f = investors.famous_for(conn, 320193, ["Apple Inc."])["holders"]
    assert len(f) == 1 and f[0]["investor"] == "Warren Buffett" and abs(f[0]["change"] + 0.225) < 1e-9 and abs(f[0]["weight"] - 0.62) < 1e-9, f
    return "Form 4 read and grouped, insider-buy alert once, 13F table, CUSIPs, famous investor matched"


def logos():
    """Logo names are checked, ETF fund companies map to a website, and white logos get a version for a light
    background while white enclosed by colour stays white (offline, nothing saved)."""
    from . import logos as lg
    assert lg.clean("brk.b") == "BRK.B" and lg.clean("../x") is None and lg.clean("") is None, "logo names"
    assert lg.family_domain("iShares") == "ishares.com" and lg.family_domain("Unknown Funds") is None, "fund companies"
    try:
        from PIL import Image
    except ImportError:
        return "names ok; Pillow not installed: logos are kept as they come"
    im = Image.new("RGBA", (40, 20), (0, 0, 0, 0))
    for x in range(2, 10):                          # a white letter on nothing
        for y in range(4, 16):
            im.putpixel((x, y), (255, 255, 255, 255))
    for x in range(20, 38):                         # a blue square with a white middle
        for y in range(2, 18):
            im.putpixel((x, y), (255, 255, 255, 255) if 25 <= x < 33 and 7 <= y < 13 else (0, 80, 200, 255))
    white = lambda r, g, b: min(r, g, b) > 205   # noqa: E731
    assert lg._recolour(im, white, (28, 28, 32)) is None, "a logo that is mostly colour was recoloured (Broadcom)"
    out = lg._recolour(im, white, (28, 28, 32), share=0)
    assert out and out.getpixel((5, 8))[:3] == (28, 28, 32), "white letter not recoloured"
    assert out.getpixel((28, 10))[:3] == (255, 255, 255), "enclosed white was recoloured"
    return "names, fund companies and light-background version ok"


def data_rules():
    """Small rules that once went wrong (bug hunt 2026-11-18), checked without data."""
    from . import sec
    # earlier registrant: 2 equal numbers in another industry are a coincidence (SpaceX "was" Honda Finance)
    assert not sec._plausible(2, "7370", "6141"), "predecessor from another industry accepted"
    assert sec._plausible(2, "2810", "2810") and sec._plausible(4, "7370", "6141")
    assert not sec._plausible(3, "", "7370"), "predecessor accepted without industry code"
    # ratios on a negative base mean nothing: blank, not -150% (negative equity, a loss, a pre-tax loss)
    import pandas as pd
    from . import metrics
    w = pd.DataFrame(dict(net_income=[-5.0, 10.0], total_equity=[-20.0, 40.0], total_debt=[50.0, 50.0],
                          dividends_paid=[3.0, 3.0], income_tax=[2.0, 2.0], pretax_income=[-3.0, 12.0],
                          end=pd.to_datetime(["2024-12-31", "2025-12-31"])))
    m = metrics.compute_for(w, "FY")
    for k in ("roe", "debt_to_equity", "payout_ratio", "effective_tax_rate"):
        assert pd.isna(m[k].iloc[0]) and pd.notna(m[k].iloc[1]), f"{k} not blank on a negative base"
    # search: BRK/B and "brk b" are BRK-B
    from .app import server
    if any(r["ticker"] == "BRK-B" for r in server._ticker_rows()):
        assert server._norm_ticker("brk/b") == "BRK-B" and server._norm_ticker("BRK B") == "BRK-B"
    assert not any("Not found" in r["name"] for r in server._search("google", 8)), "'google' offered as a ticker"
    rows = server._ticker_rows()
    if any(r["ticker"] == "GOOGL" for r in rows):
        assert server._search("google", 8)[0]["ticker"] == "GOOGL", "'google' finds Alphabet"
    if any(r["ticker"] == "AAPL" for r in rows):
        assert not any("ineapple" in r["name"] for r in server._search("apple", 8)), "'apple' in the middle of a word"
    from .db import readable_name as rn
    assert rn("COCA-COLA EUROPACIFIC PARTNERS plc") == "Coca-Cola Europacific Partners plc" and rn("NVIDIA Corp") == "NVIDIA Corp" \
        and rn("BANK OF AMERICA CORP /DE/") == "Bank of America Corp" and rn("JPMORGAN CHASE & CO") == "JPMorgan Chase & Co", "names"
    return "predecessor, negative bases, search, names"


_DATA_SWAP = r"""
import json, sqlite3, sys
from pathlib import Path
from stockterminal import datasetup, db
other = Path(sys.argv[1])
c = sqlite3.connect(other / "terminal.db")
c.execute("CREATE TABLE companies (cik INTEGER, ticker TEXT, name TEXT)")
c.execute("INSERT INTO companies VALUES (1, 'AAA', 'A Co')")
c.commit(); c.close()
(other / "settings.json").write_text(json.dumps({"sec_email": "you@example.com"}))
c = db.connect(); c.execute("INSERT INTO pf_meta(key, value) VALUES ('k', 'v')"); c.commit(); c.close()
datasetup.start("path", str(other))
import time
while datasetup._job.get("state") == "running":
    time.sleep(0.05)
assert datasetup._job["state"] == "ready", datasetup._job
assert "1 companies" in (datasetup.apply_incoming() or ""), "not swapped in"
c = db.connect()
assert c.execute("SELECT ticker FROM companies").fetchall() == [("AAA",)]
assert not c.execute("SELECT 1 FROM pf_meta").fetchone(), "another copy's data replaces your portfolio too"
assert (datasetup.BACKUP / "terminal-before-import.db").exists() and not datasetup.INCOMING.exists()
print("ok")
"""


def data_swap():
    """Settings > Data: another copy's data is prepared next to the database and swapped in at the next start
    (in a throw-away data folder, never this PC's)."""
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "other").mkdir()
        env = {**os.environ, "STOCKTERMINAL_DATA": str(Path(tmp) / "mine")}
        r = subprocess.run([sys.executable, "-c", _DATA_SWAP, str(Path(tmp) / "other")], cwd=ROOT, env=env,
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0 or "ok" not in r.stdout:
            raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip() else "failed")
    return "prepared, swapped in, old data kept as backup"


def earnings_calendar():
    """The Earnings page with made-up Yahoo data (in memory, no internet): next date and its estimate, a date expected
    from the company's rhythm, surprises newest first, the beat count and the price reaction."""
    import datetime as dt
    import json
    import sqlite3
    import time
    from . import db as dbm, earnings_cal, events
    conn = sqlite3.connect(":memory:")
    conn.executescript(dbm.SCHEMA)
    dbm._migrate(conn)
    today = dt.date(2026, 11, 23)
    d = lambda n: (today + dt.timedelta(days=n)).isoformat()  # noqa: E731
    res = [dict(date=d(-30 - 91 * k), eps_est=1.0, eps_act=1.1 if k % 2 == 0 else 0.9) for k in range(6)]
    conn.executemany("INSERT INTO pf_events VALUES (?,?,?)", [
        ("AAA", json.dumps(dict(version=events.VERSION, earnings=[d(5)], eps_avg=1.2, results=res)), time.time()),
        ("BBB", json.dumps(dict(version=events.VERSION, earnings=[], results=res)), time.time())])
    conn.executemany("INSERT INTO prices (ticker, date, close) VALUES (?,?,?)", [("AAA", d(-31), 100.0), ("AAA", d(-29), 110.0)])
    out = earnings_cal.build(conn, ["AAA", "BBB", "CCC"], today=today)
    a, b = out["companies"]["AAA"], out["companies"]["BBB"]
    assert a["next"]["date"] == d(5) and a["next"]["eps_avg"] == 1.2 and not a["next"]["expected"], a["next"]
    assert b["next"]["expected"] and b["next"]["date"] == d(-30 - 91 * 3 + 364) and b["next"]["eps_avg"] is None, b["next"]
    assert [r["date"] for r in a["results"]] == sorted((r["date"] for r in res), reverse=True), "newest first"
    assert (a["beats"], a["counted"]) == (2, 4) and abs(a["results"][0]["surprise"] - 0.1) < 1e-9, a
    assert abs(a["results"][0]["reaction"] - 0.1) < 1e-9 and out["missing"] == ["CCC"], out["missing"]
    return "next date, expected date, surprises, beat count, price reaction"


def sector_pages():
    """Sector and industry pages with made-up companies: medians, ranks (a loss has no P/E rank), groups."""
    from . import sectors
    rows = [dict(ticker="A", sector="Tech", industry="Chips", market_cap=300, pe=30, operating_margin=0.4),
            dict(ticker="B", sector="Tech", industry="Chips", market_cap=200, pe=-5, operating_margin=0.1),
            dict(ticker="C", sector="Tech", industry="Chips", market_cap=100, pe=10, operating_margin=0.2),
            dict(ticker="D", sector="Tech", industry="Software", market_cap=50, pe=20, operating_margin=0.3),
            dict(ticker="E", sector=None, industry=None, market_cap=10, pe=None, operating_margin=None)]
    ov = sectors.overview(rows)
    assert [s["name"] for s in ov["sectors"]] == ["Tech", "Other"], ov["sectors"]
    tech = ov["sectors"][0]
    assert tech["count"] == 4 and tech["industries"] == 2 and tech["market_cap"] == 650, tech
    assert tech["med"]["pe"] == 20 and abs(tech["med"]["operating_margin"] - 0.25) < 1e-9, tech["med"]
    g = sectors.group(rows, "Tech", "Chips")
    rk = {c["ticker"]: c["ranks"] for c in g["companies"]}
    assert rk["C"]["pe"] == [1, 2] and rk["A"]["pe"] == [2, 2] and "pe" not in rk["B"], rk
    assert rk["A"]["operating_margin"] == [1, 3] and g["siblings"] == ["Chips", "Software"], g
    assert sectors.group(rows, "Tech")["industries"][0]["name"] == "Chips"
    assert sectors.group(rows, "Nope") is None
    p = sectors.position(rows, "d")
    item = next(x for x in p["items"] if x["key"] == "pe")
    assert p["industry_n"] == 1 and item["sec_rank"] == [2, 3] and item["ind_rank"] is None, p
    return "medians, ranks, losses left out, sectors and industries"


def main():
    print("Stock Terminal self-test")
    check("Python files compile", compile_all)
    check("all modules import", import_all)
    check("nothing private, secret or PC-specific in git", git_files)
    check("new design is built and current", new_ui)
    check("server starts and answers", server)
    check("exports (PDF writer, box 3 overview)", exports)
    check("earnings watch (press release, alert text)", earnings)
    check("company logos (names, light-background version)", logos)
    check("data rules (bugs fixed before)", data_rules)
    check("alert checks (made-up prices)", alert_checks)
    check("news per stock (Yahoo shapes)", news_items)
    check("insider trades and big investors (made-up filings)", ownership)
    check("earnings page (made-up results)", earnings_calendar)
    check("sector pages (made-up companies)", sector_pages)
    check("own trades without Trading 212 (made-up prices)", own_trades)
    check("version and release notes agree", version_notes)
    check("company data from another copy (swap at start)", data_swap)
    failed = [n for ok, n in results if not ok]
    print("All good." if not failed else f"{len(failed)} check(s) failed: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
