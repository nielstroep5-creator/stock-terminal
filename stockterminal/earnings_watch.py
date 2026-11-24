"""Earnings watch: notice a company's results within minutes, load the official numbers as soon as they are
filed, and alert you with the numbers against what analysts expected.

    python -m stockterminal.earnings_watch          # one run (the scheduled task does this every 5 minutes)
    python -m stockterminal.earnings_watch --status # what is being watched now

Runs every 5 minutes as its own scheduled task ("Stock Terminal earnings watch" on Windows, launchd
com.stockterminal.earnings on a Mac), so it works with the app closed; the computer has to be on. A run on a
day nobody reports reads the local database only, plus (weekdays, while markets are open) one batched Yahoo quote
request for your price limits and big moves (alerts.check_live).

1. Plan (no internet): every company in the terminal with an earnings date (Yahoo's calendar, kept by the daily
   analyst snapshot) from two days ago to tomorrow gets a row in earnings_watch. What analysts expect (EPS and
   revenue for the quarter) is saved with it BEFORE the report: right after a report Yahoo moves its estimates
   to the next quarter, so "vs expected" is only possible from a copy made earlier.
2. SEC, only on the report day and the day after: the company's list of filings (data.sec.gov/submissions,
   one small request) every 5 minutes around the expected time (before the US market opens and after it
   closes), every 30 minutes otherwise.
   - an 8-K with item 2.02 (results of operations) = the results are out: saved with a link to the press release.
   - a 10-Q / 10-K / 20-F / 40-F = the official numbers: loaded right away (update.update_sec). When the SEC's
     companyfacts is not updated yet, the numbers are read from the filing itself (filings.fill_missing).
3. Yahoo, once the results are out (or, for a company that files no 8-K, from the expected time): the reported
   EPS (adjusted, like the estimates) and the quarter's revenue. These are "preliminary" until the 10-Q is in.
4. Alert (alerts.py, kind "earnings"; portfolio and watchlist companies only): revenue and EPS against the
   expectation with the beat or miss in %, and the share price since the close. One phone message; when the
   official numbers come later, the alert in the app is updated without a second message.
"""
import datetime as dt
import json
import os
import re
import sys
import time
from zoneinfo import ZoneInfo

from . import db
from .config import DATA_DIR

NY = ZoneInfo("America/New_York")
LOG_DIR = DATA_DIR / "logs"
LOCK = LOG_DIR / "earnings.lock"
STALE_LOCK_MIN = 30
FAST_MIN, SLOW_MIN = 5, 30            # how often the SEC is asked: around the expected time / otherwise
YAHOO_MIN = 10                        # how often Yahoo is asked for the reported numbers once results are out
PERIODIC = ("10-Q", "10-K", "20-F", "40-F", "10-Q/A", "10-K/A")
KEEP_DAYS = 14                        # a row stays visible this long (stock page bar), then it is history
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{name}"


# ------------------------------------------------------------------------------------------ small helpers
def _now():
    return dt.datetime.now(dt.timezone.utc)


def _iso(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if t else None


def _parse(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc) if s else None


def _minutes_since(s, now):
    return 1e9 if not s else (now - _parse(s)).total_seconds() / 60


def pct(act, est):
    """Surprise as a fraction of the estimate (None without both)."""
    if act is None or est is None or est == 0:
        return None
    return (act - est) / abs(est)


def word(p, tol=0.005):
    """beat / in line / miss for a surprise fraction."""
    return None if p is None else "beat" if p > tol else "miss" if p < -tol else "in line"


def money(v):
    a = abs(v)
    s = f"{a / 1e12:,.2f}T" if a >= 1e12 else f"{a / 1e9:,.2f}B" if a >= 1e9 else f"{a / 1e6:,.1f}M" if a >= 1e6 else f"{a:,.0f}"
    return ("-" if v < 0 else "") + "$" + s


def _signed(p):
    return f"{p * 100:+.1f}%".replace("-", "−")


def _ny_bounds(day, hh_from, hh_to):
    d = dt.date.fromisoformat(day)
    return (dt.datetime.combine(d, dt.time(hh_from[0], hh_from[1]), NY), dt.datetime.combine(d, dt.time(hh_to[0], hh_to[1]), NY))


# ------------------------------------------------------------------------------------------ 1. plan
def _snapshots(conn, since):
    """{ticker: [(snapshot_date, {field: value/text})]} of the calendar and quarter estimates, newest first."""
    rows = conn.execute(
        """SELECT ticker, snapshot_date, section, period, field, value, text FROM analyst_estimates
           WHERE snapshot_date >= ? AND (
             (section='calendar' AND field IN ('Earnings Date','Earnings Average','Earnings Low','Earnings High','Revenue Average'))
             OR (section='summary' AND field IN ('earningsTimestamp','earningsTimestampStart','isEarningsDateEstimate'))
             OR (section IN ('eps_estimate','revenue_estimate') AND period='0q' AND field IN ('avg','numberOfAnalysts')))""",
        (since,)).fetchall()
    out = {}
    for t, snap, sec, per, field, v, txt in rows:
        key = field if sec in ("calendar", "summary") else f"{sec}_{field}"
        out.setdefault(t, {}).setdefault(snap, {})[key] = v if v is not None else txt
    return {t: sorted(s.items(), reverse=True) for t, s in out.items()}


def _dates(snap):
    """Earnings dates in one calendar snapshot (Yahoo gives one date, or two when not confirmed yet)."""
    txt = snap.get("Earnings Date")
    return sorted({p.strip()[:10] for p in str(txt or "").replace("[", "").replace("]", "").replace("'", "").split(",")
                   if re.match(r"\d{4}-\d\d-\d\d", p.strip()[:10])})


def _expect(snap, day):
    """What analysts expected for the report on `day`, from one snapshot."""
    def num(k):
        v = snap.get(k)
        return v if isinstance(v, (int, float)) else None
    eps = num("Earnings Average") if num("Earnings Average") is not None else num("eps_estimate_avg")
    rev = num("Revenue Average") or num("revenue_estimate_avg")
    at = num("earningsTimestamp") or num("earningsTimestampStart")
    at_day = dt.datetime.fromtimestamp(at, NY).date().isoformat() if at else None
    return dict(eps=eps, eps_low=num("Earnings Low"), eps_high=num("Earnings High"), revenue=rev,
                analysts=num("eps_estimate_numberOfAnalysts"), at=int(at) if at and at_day == day else None)


def plan(conn, now=None):
    """Add a row for every report from two days ago to tomorrow (New York dates); refresh what analysts expect
    from snapshots made before the report day. Local database only. Returns the rows added."""
    now = now or _now()
    today = now.astimezone(NY).date()
    lo, hi = (today - dt.timedelta(days=2)).isoformat(), (today + dt.timedelta(days=1)).isoformat()
    known = {(t, d): json.loads(e or "{}") for t, d, e in conn.execute(
        "SELECT ticker, date, expect FROM earnings_watch WHERE date >= ?", (lo,))}
    cos = {t: c for t, c in conn.execute("SELECT ticker, cik FROM companies WHERE ticker IS NOT NULL")}
    added = []
    for t, snaps in _snapshots(conn, (today - dt.timedelta(days=6)).isoformat()).items():
        if t not in cos:
            continue
        days = {d for _, s in snaps[:1] for d in _dates(s) if lo <= d <= hi}
        days |= {d for (tt, d) in known if tt == t}
        for d in days:
            # the newest snapshot made before the report day; on the day itself only when nothing older exists
            # (a row made on the report day: the 07:00 snapshot comes before US results)
            before = [(s, x) for s, x in snaps if s < d and d in _dates(x)] or \
                     [(s, x) for s, x in snaps if s <= d and d in _dates(x)][-1:]
            if not before:
                continue
            snap_date, snap = before[0]
            exp = dict(_expect(snap, d), snapshot=snap_date, confirmed=len(_dates(snap)) == 1)
            if (t, d) not in known:
                conn.execute("INSERT INTO earnings_watch (ticker, date, cik, expect, created) VALUES (?,?,?,?,?)",
                             (t, d, cos[t], json.dumps(exp), _iso(now)))
                added.append((t, d))
            elif snap_date < d and known[(t, d)].get("snapshot", "") < snap_date:
                conn.execute("UPDATE earnings_watch SET expect=? WHERE ticker=? AND date=?", (json.dumps(exp), t, d))
    conn.commit()
    return added


# ------------------------------------------------------------------------------------------ 2. when to ask
def _windows(row):
    """(fast windows, whole watch window) in New York time for one report."""
    d = row["date"]
    at = (row["expect"] or {}).get("at")
    day_start, _ = _ny_bounds(d, (5, 0), (5, 0))
    end = day_start + dt.timedelta(days=2, hours=1)          # until 06:00 the day after tomorrow (NY)
    if at:
        t = dt.datetime.fromtimestamp(at, NY)
        fast = [(t - dt.timedelta(minutes=30), t + dt.timedelta(hours=4))]
    else:                                                     # before the open, and after the close
        fast = [_ny_bounds(d, (6, 0), (9, 45)), _ny_bounds(d, (15, 55), (20, 0))]
    return fast, (day_start, end)


def _due(row, now, last, minutes_fast, minutes_slow):
    fast, (start, end) = _windows(row)
    if not start <= now <= end:
        return False
    every = minutes_fast if any(a <= now <= b for a, b in fast) else minutes_slow
    return _minutes_since(last, now) >= every - 0.5


# ------------------------------------------------------------------------------------------ 3. SEC
def recent_filings(cik):
    """The company's latest filings from the SEC: [{accn, form, filed, accepted, items, doc}], newest first."""
    from . import sec
    data = sec._get_json(sec.SUBMISSIONS_URL.format(name=f"CIK{int(cik):010d}.json"))
    r = data.get("filings", {}).get("recent", {})
    n = min(len(r.get("accessionNumber", [])), 40)
    col = lambda k: (r.get(k) or [""] * n)  # noqa: E731
    return [dict(accn=col("accessionNumber")[i], form=col("form")[i], filed=col("filingDate")[i],
                 accepted=col("acceptanceDateTime")[i], items=col("items")[i], doc=col("primaryDocument")[i])
            for i in range(n)]


def find_results(filings, day):
    """(8-K with results, periodic report) filed on or after the day before `day` (either may be None)."""
    lo = (dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat()
    recent = [f for f in filings if (f["filed"] or "") >= lo]
    k8 = next((f for f in reversed(recent) if f["form"] in ("8-K", "8-K/A") and "2.02" in str(f["items"] or "")), None)
    rep = next((f for f in reversed(recent) if f["form"] in PERIODIC), None)
    return k8, rep


def press_release(cik, accn):
    """Link to the press release of an 8-K: its exhibit 99.1 (from the filing's index page), else the index page."""
    from . import sec
    acc = accn.replace("-", "")
    page = ARCHIVE.format(cik=int(cik), acc=acc, name=f"{accn}-index.htm")
    try:
        html = sec._get(page).text
    except Exception:  # noqa: BLE001  the filing's page is fine too
        return page
    best = None
    for tr in re.findall(r"(?is)<tr.*?</tr>", html):
        m = re.search(r'href="([^"]+\.html?)"', tr)
        typ = re.search(r">\s*(EX-99[.\d]*)\s*<", tr)
        if m and typ and "/ix?doc=" not in m.group(1):
            rank = 0 if typ.group(1) in ("EX-99.1", "EX-99.01", "EX-99") else 1
            if best is None or rank < best[0]:
                best = (rank, m.group(1))
        elif m and typ is None and "ix?doc=" in m.group(1) and re.search(r">\s*EX-99", tr):
            best = best or (1, m.group(1).split("ix?doc=")[-1])
    if not best:
        return page
    href = best[1]
    return href if href.startswith("http") else "https://www.sec.gov" + href


# ------------------------------------------------------------------------------------------ 3b. the press release
UNIT = [(r"in\s+billions", 1e9), (r"in\s+millions|\$\s*millions|\(millions", 1e6), (r"in\s+thousands|\$\s*thousands|\(thousands|000s", 1e3)]
REV_ROW = re.compile(r"^(consolidated\s+)?(total\s+)?(net\s+)?(revenues?|sales|net\s+sales|revenues?,?\s+net|operating\s+revenues?"
                     r"|sales\s+and\s+(other\s+)?(operating\s+)?revenues?|net\s+revenues?)(\s*\(\w+\))?(\s*\$)?$", re.I)
EPS_ROW = re.compile(r"(diluted.{0,40}(earnings|income|eps|loss).{0,30}share|(earnings|income|eps).{0,40}per\s+diluted\s+share"
                     r"|^diluted\s*(eps|earnings per share)?\s*(\(\w+\))?$|^eps\s*[-–]?\s*diluted|^diluted net income per share)", re.I)
ADJ = re.compile(r"non[-\s]?gaap|adjusted|excluding|core\b|operating\s+eps|normalized", re.I)


def _text(html):
    import html as h
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    return re.sub(r"[\s\u200b\xa0]+", " ", h.unescape(t)).strip()


def _numbers(cells):
    """Every number in a row's cells (after the label), in order: '57,006' -> 57006, '(1.20)' -> -1.2, '$ 1.30' -> 1.3.
    Percentages and empty cells are skipped."""
    out = []
    for c in cells:
        c = c.replace("$", "").replace(" ", "")
        if re.fullmatch(r"\(?-?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?\)?", c) and any(ch.isdigit() for ch in c):
            neg = c.startswith("(") or c.startswith("-")
            v = float(c.strip("()").replace(",", "").lstrip("-"))
            out.append(-v if neg else v)
    return out


def _current_column(heading_rows):
    """Which number of a row is the latest period: tables put the newest year first or last. From the last heading
    row with years in it: the position of the first newest year (0 when the table has no years)."""
    for cells in reversed(heading_rows):
        years = [int(y) for c in cells for y in re.findall(r"\b(20\d\d)\b", c)]
        if len(years) >= 2:
            return years.index(max(years))
    return 0


def parse_press_release(html):
    """Revenue and EPS from an earnings press release, conservatively: [(kind, value, unit or None, adjusted)] in
    the order they appear. kind 'revenue' (value in the table's unit; flag 1 = a "Total ..." row) or 'eps'; adjusted 2 = the row says so
    (non-GAAP, adjusted, core), 1 = only the table around it does, 0 = GAAP. The value is the one in the
    latest period's column. Every company lays its tables out differently, so the caller checks the numbers
    against the expectation before using them."""
    out = []
    for m in re.finditer(r"(?is)<table.*?</table>", html):
        table = m.group(0)
        before = _text(html[max(0, m.start() - 1500):m.start()])[-400:]
        whole = _text(table)
        unit = next((u for pat, u in UNIT if re.search(pat, whole[:600], re.I)), None) or \
            next((u for pat, u in UNIT if re.search(pat, before, re.I)), None)
        adj_table = bool(ADJ.search(before[-200:]) or ADJ.search(whole[:300]))
        headings = []
        for tr in re.findall(r"(?is)<tr.*?</tr>", table):
            cells = [c for c in (_text(x) for x in re.findall(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>", tr)) if c]
            if not cells:
                continue
            label = cells[0].rstrip(":").strip()
            nums = _numbers(cells[1:])
            if not nums or re.fullmatch(r"[\d\s,]*", label):
                headings.append(cells)                  # a heading row (periods, years)
                continue
            col = _current_column(headings)
            v = nums[col] if col < len(nums) else nums[0]
            if REV_ROW.match(label):
                if v > 0:                                # 1: "Total revenue(s)" (wins over "Net sales", e.g. Costco)
                    out.append(("revenue", v, unit, 1 if re.match(r"(consolidated\s+)?total", label, re.I) else 0))
            elif EPS_ROW.search(label) and not re.search(r"basic(?!\s+and\s+diluted)|weighted|shares used", label, re.I):
                if abs(v) < 1000:
                    lvl = 2 if ADJ.search(label) else 0 if re.search(r"\bGAAP\b", label) else 1 if adj_table else 0
                    out.append(("eps", v, None, lvl))
    return out


def press_numbers(found, expect, last_revenue=None):
    """Pick revenue (in dollars) and EPS from parse_press_release, accepting only numbers that fit the
    expectation (or, without one, the last quarter): a wrong table, column or unit is thrown away, never shown.
    Tested on 42 S&P 500 press releases (2026-11-17): revenue 33 right, 8 not found, 1 wrong (Costco: Net sales, fixed after); adjusted EPS 7 right,
    2 off (PG, IBM), the rest not found. Yahoo's EPS replaces it when it comes, the 10-Q replaces revenue."""
    out = {}
    ref = expect.get("revenue")
    lo, hi = (0.85, 1.18) if ref else (0.7, 1.5)          # revenue rarely surprises by more than 15%
    ref = ref or last_revenue
    fits = []
    for kind, v, unit, total in found:
        if kind != "revenue" or not ref:
            continue
        for scale in ([unit] if unit else []) + [1e6, 1e3, 1e9, 1]:
            if lo <= v * scale / ref <= hi:
                fits.append((v * scale, scale, total))
                break
    if fits:                                # a "Total revenues" row first, then the most precise ($115,844M, not $115.8B)
        out["revenue"] = min(fits, key=lambda f: (-f[2], f[1]))[0]
    gaap = next((v for kind, v, _, a in found if kind == "eps" and a == 0), None)
    est = expect.get("eps")
    if gaap is not None and (est is None or abs(gaap) <= max(5 * abs(est), 1)):
        out["eps_gaap"] = gaap
    # adjusted EPS: a row that says so first, then one in an adjusted table; never the GAAP number again
    # (a GAAP row inside a reconciliation table looks adjusted), and close to the estimate
    if est is not None and est > 0.05:
        for lvl in (2, 1):
            adj = next((v for kind, v, _, a in found if kind == "eps" and a == lvl and v != gaap and 0.7 <= v / est <= 1.4), None)
            if adj is not None:
                out["eps_adj"] = adj
                break
    return out


def _read_press(conn, row, url):
    """Revenue / EPS from the press release that fit the expectation, or {}."""
    if not re.search(r"\.html?$", url) or url.endswith("-index.htm"):
        return {}
    try:
        from . import sec
        html = sec._get(url).text
        last = conn.execute("SELECT value FROM financials WHERE cik=? AND item='revenue' AND period_type='Q' "
                            "ORDER BY end_date DESC LIMIT 1", (row["cik"],)).fetchone()
        return press_numbers(parse_press_release(html), row["expect"] or {}, last[0] if last else None)
    except Exception:  # noqa: BLE001  Yahoo and the 10-Q still come
        return {}


# ------------------------------------------------------------------------------------------ 4. Yahoo
def yahoo_results(ticker, day):
    """{eps_act, eps_est, revenue, quarter_end} Yahoo has for the report on `day` (keys missing = not there yet)."""
    import yfinance as yf
    from .quote import yahoo_symbol
    tk = yf.Ticker(yahoo_symbol(ticker))
    out = {}
    d0 = dt.date.fromisoformat(day)
    try:
        ed = tk.get_earnings_dates(limit=8)
        for idx, r in (ed.iterrows() if ed is not None else []):
            d = idx.tz_convert(NY).date() if getattr(idx, "tzinfo", None) else idx.date()
            if abs((d - d0).days) <= 1:
                act, est = r.get("Reported EPS"), r.get("EPS Estimate")
                if act == act and act is not None:
                    out["eps_act"] = float(act)
                if est == est and est is not None:
                    out["eps_est"] = float(est)
    except Exception:  # noqa: BLE001  the next run tries again
        pass
    try:
        q = tk.quarterly_income_stmt
        if q is not None and not q.empty and "Total Revenue" in q.index:
            col = max(q.columns)
            end = col.date() if hasattr(col, "date") else dt.date.fromisoformat(str(col)[:10])
            v = q.loc["Total Revenue", col]
            # the reported quarter ended 0-100 days before the report
            if v == v and v is not None and 0 <= (d0 - end).days <= 100:
                out["revenue"], out["quarter_end"] = float(v), end.isoformat()
    except Exception:  # noqa: BLE001
        pass
    return out


def official(conn, cik, day):
    """Revenue and EPS of the quarter in the 10-Q / 10-K filed for the report on `day`, from the terminal's own
    numbers (after update_sec). {} when the filing is not in yet."""
    lo = (dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat()
    out = {}
    for item, key in (("revenue", "revenue"), ("eps_diluted", "eps")):
        r = conn.execute("""SELECT value, end_date, original_filed FROM financials WHERE cik=? AND item=? AND period_type='Q'
                            ORDER BY end_date DESC LIMIT 1""", (cik, item)).fetchone()
        if r and r[0] is not None and (r[2] or "") >= lo:
            out[key], out["quarter_end"] = r[0], r[1]
    return out


# ------------------------------------------------------------------------------------------ the text
def summary(row):
    """(title words, body, step -2..2, numbers) for a report: what was reported against what was expected.
    None while there is no reported number yet."""
    exp, res = row["expect"] or {}, row["result"] or {}
    off = row.get("official") or {}
    first = lambda *v: next((x for x in v if x is not None), None)  # noqa: E731
    rev = first(off.get("revenue"), res.get("revenue"), res.get("pr_revenue"))
    eps = first(res.get("eps_act"), res.get("pr_eps_adj"))   # adjusted EPS: the same basis as the estimate
    gaap = first(off.get("eps"), res.get("pr_eps_gaap"))
    eps_est = first(res.get("eps_est"), exp.get("eps"))
    if rev is None and eps is None and gaap is None:
        return None
    rp, ep = pct(rev, exp.get("revenue")), pct(eps, eps_est)
    lines, short = [], []
    if rev is not None:
        lines.append(f"Revenue {money(rev)}" + (f" vs {money(exp['revenue'])} expected ({_signed(rp)}, {word(rp)})" if rp is not None else ""))
        if rp is not None:
            short.append(f"revenue {_signed(rp)}")
    else:
        lines.append("Revenue: follows with the 10-Q")
    if eps is not None:
        lines.append(f"EPS {eps:.2f}" + (f" vs {eps_est:.2f} expected ({_signed(ep)}, {word(ep)})" if ep is not None else ""))
        if ep is not None:
            short.append(f"EPS {_signed(ep)}")
    elif gaap is not None:
        lines.append(f"EPS {gaap:.2f} (GAAP; the adjusted EPS analysts estimate follows)")
    if gaap is not None and eps is not None:
        lines.append(f"GAAP EPS {gaap:.2f}" + (" (10-Q)" if off.get("eps") is not None else ""))
    # step: EPS counts most (like the existing result rating), revenue adds or takes one
    from .events import rate_surprise
    step = rate_surprise(eps_est, eps)["step"] if eps is not None and eps_est is not None else 0
    if rp is not None:
        step = max(-2, min(2, step + (1 if rp > 0.01 else -1 if rp < -0.01 else 0)))
    beats = [w for w in (word(rp), word(ep)) if w]
    verdict = ("beat" if all(w == "beat" for w in beats) else "miss" if all(w == "miss" for w in beats)
               else "mixed" if beats else "out")
    nums = dict(revenue=rev, revenue_est=exp.get("revenue"), revenue_pct=rp, eps=eps, eps_est=eps_est, eps_pct=ep,
                verdict=verdict, official=bool(off), short=", ".join(short))
    return verdict, "\n".join(lines), step, nums


# ------------------------------------------------------------------------------------------ 5. the alert
def _alert(conn, row, now, say):
    """Add (or update) the Results alert of a portfolio / watchlist company. Returns 'new', 'updated' or None."""
    from . import alerts
    s = summary(row)
    if not s:
        return None
    verdict, body, step, nums = s
    rules, per = alerts.config()
    if not rules["earn_on"]:
        return None
    try:
        watch, _ = alerts.targets(conn, False, rules, per)
    except Exception as e:  # noqa: BLE001  no portfolio connected: the watchlists still count
        say(f"alerts: portfolio not readable ({e})")
        watch = alerts._watched(conn, False, []) if rules["wl_on"] else []
    t = next((w for w in watch if (w.get("ticker") or "").upper() == row["ticker"]), None)
    if not t or not alerts._kinds_on(t, rules, per)["earn"]:
        return None
    reaction = _reaction(row["ticker"])
    if reaction:
        body += f"\nShare price {_signed(reaction[0])} {reaction[1]}"
    if row.get("press_url"):
        body += f"\nPress release: {row['press_url']}"
    if not nums["official"]:
        body += "\nPreliminary: the official numbers come with the 10-Q."
    from .alerts import rate_word
    title = f"{t['name']} results: {verdict}" + (f" ({nums['short']})" if nums["short"] else "") + f" {rate_word(step).split(' ')[-1]}"
    # the morning check may have stored this report a day apart (Yahoo's date): update that one, no second message
    key = alerts._results_alert(conn, t["t212_ticker"], row["date"]) or f"earnings|{t['t212_ticker']}|{row['date']}"
    cur = conn.execute("""INSERT OR IGNORE INTO pf_alerts (key, created, kind, t212_ticker, ticker, name, title, body, level, push)
                          VALUES (?,?,?,?,?,?,?,?,?,?)""",
                       (key, now.astimezone().strftime("%Y-%m-%d %H:%M"), "earnings", t["t212_ticker"], t.get("ticker"), t["name"],
                        title, body, step, 1 if rules["earn_push"] else 0))
    if cur.rowcount:
        conn.commit()
        return "new"
    old = conn.execute("SELECT title, body FROM pf_alerts WHERE key=?", (key,)).fetchone()
    if old and (old[0], old[1]) != (title, body):          # better numbers since (the 10-Q): no second message
        conn.execute("UPDATE pf_alerts SET title=?, body=?, level=?, read=0 WHERE key=?", (title, body, step, key))
        conn.commit()
        return "updated"
    return None


def _reaction(ticker):
    """(change, words) of the share price since the last close: after hours when trading, else the day's move."""
    try:
        from . import quote
        q = quote.get_quote(ticker, refresh=True) or {}
        ext, reg = q.get("extended") or {}, q.get("regular") or {}
        if ext.get("change_pct") is not None and ext.get("session") in ("post", "pre", "overnight"):
            return ext["change_pct"], {"post": "after hours", "pre": "before the open", "overnight": "overnight"}[ext["session"]]
        if reg.get("change_pct") is not None:
            return reg["change_pct"], "today"
    except Exception:  # noqa: BLE001  the alert works without it
        pass
    return None


# ------------------------------------------------------------------------------------------ run
def rows(conn, since=None, ticker=None):
    q = "SELECT * FROM earnings_watch WHERE date >= ?" + (" AND ticker=?" if ticker else "") + " ORDER BY date, ticker"
    cur = conn.execute(q, (since or "0000",) + ((ticker,) if ticker else ()))
    names = [c[0] for c in cur.description]
    out = []
    for r in cur.fetchall():
        d = dict(zip(names, r))
        for k in ("expect", "result", "official"):
            d[k] = json.loads(d[k]) if d.get(k) else ({} if k != "official" else None)
        out.append(d)
    return out


def _save(conn, row, **changes):
    for k, v in changes.items():
        row[k] = v
    sets = ", ".join(f"{k}=?" for k in changes)
    vals = [json.dumps(v) if k in ("expect", "result", "official") and v is not None else v for k, v in changes.items()]
    conn.execute(f"UPDATE earnings_watch SET {sets} WHERE ticker=? AND date=?", (*vals, row["ticker"], row["date"]))
    conn.commit()


def run(now=None, say=print, fast=FAST_MIN, slow=SLOW_MIN):
    """One pass: plan, ask the SEC / Yahoo for the reports that are due, load filings, alert. Returns what happened."""
    from . import settings, update
    now = now or _now()
    conn = update.connect()
    done = []
    try:
        plan(conn, now)
        try:                                         # price limits and big moves while markets are open (alerts.py)
            from . import alerts
            alerts.check_live(now, say=say)
        except Exception as e:  # noqa: BLE001  never stops the watch
            say(f"prices: failed: {e}")
        if not settings.read().get("earnings_watch", True):
            return done
        today = now.astimezone(NY).date()
        active = [r for r in rows(conn, (today - dt.timedelta(days=2)).isoformat()) if not r["done"]]
        for row in active:
            t, cik = row["ticker"], row["cik"]
            before = (json.dumps(row["result"], sort_keys=True), json.dumps(row.get("official"), sort_keys=True))
            _, (start, end) = _windows(row)
            if now > end:                                    # the watch is over; the daily update takes it from here
                _save(conn, row, done=_iso(now))
                continue
            if cik and not row["filed_at"] and _due(row, now, row["checked_sec"], fast, slow):
                try:
                    k8, rep = find_results(recent_filings(cik), row["date"])
                    _save(conn, row, checked_sec=_iso(now))
                except Exception as e:  # noqa: BLE001  try again next time
                    say(f"{t}: SEC not reachable ({str(e)[:120]})")
                    _save(conn, row, checked_sec=_iso(now))
                    k8 = rep = None
                if k8 and not row["out_at"]:
                    url = press_release(cik, k8["accn"])
                    _save(conn, row, out_at=_iso(now), out_accn=k8["accn"], press_url=url)
                    say(f"{t}: results are out (8-K {k8['accepted'] or k8['filed']}), press release {url}")
                    pr = _read_press(conn, row, url)
                    if pr:
                        _save(conn, row, result=dict(row["result"] or {}, **{f"pr_{k}": v for k, v in pr.items()}))
                        say(f"{t}: from the press release: " + ", ".join(f"{k} {v:,.2f}" for k, v in pr.items()))
                    done.append((t, "out"))
                if rep:
                    say(f"{t}: {rep['form']} filed {rep['filed']}: loading it now")
                    r = update.step(conn, t, "sec", lambda: update.update_sec(conn, t))
                    off = official(conn, cik, row["date"]) if r["ok"] else {}
                    if r["ok"]:
                        _save(conn, row, filed_at=_iso(now), filed_accn=rep["accn"], official=off or None,
                              out_at=row["out_at"] or _iso(now))
                    say(f"{t}: {rep['form']} {'loaded' if r['ok'] else 'failed: ' + r.get('error', '')} ({r['seconds']}s)"
                        + (f", revenue {money(off['revenue'])}" if off.get("revenue") is not None else ""))
                    done.append((t, "official"))
            # Yahoo: once the results are out (or, for a company without 8-Ks, from the expected time on)
            # (a foreign company files a 6-K, not an 8-K: Yahoo once the expected time is well past)
            fast_w, _ = _windows(row)
            ready = row["out_at"] or ((not cik or now > fast_w[-1][1]) and _due(row, now, None, fast, slow))
            res = row["result"] or {}
            if ready and (res.get("eps_act") is None or res.get("revenue") is None) and \
                    _minutes_since(row["checked_yahoo"], now) >= (YAHOO_MIN if row["out_at"] else slow) - 0.5:
                got = yahoo_results(t, row["date"])
                new = dict(res, **{k: v for k, v in got.items() if v is not None})
                _save(conn, row, result=new, checked_yahoo=_iso(now))
                if new != res:
                    say(f"{t}: Yahoo " + ", ".join(f"{k} {v}" for k, v in got.items()))
                    if res.get("eps_act") is None and new.get("eps_act") is not None:
                        from . import analyst
                        update.step(conn, t, "analyst", lambda: analyst.load_analyst(conn, t))   # surprises table
            changed = before != (json.dumps(row["result"], sort_keys=True), json.dumps(row.get("official"), sort_keys=True))
            a = _alert(conn, row, now, say) if summary(row) and (changed or not row["alerted"]) else None
            if a:
                say(f"{t}: alert {a}")
                if a == "new":
                    _save(conn, row, alerted=_iso(now))
            if row["filed_at"] and (row["result"] or {}).get("eps_act") is not None:
                _save(conn, row, done=_iso(now))
        # phone: the new results message, and one that could not go earlier (no internet, quiet hours)
        from . import alerts
        rules, _ = alerts.config()
        problems = []
        n = alerts.send_pending(conn, rules, now.astimezone().replace(tzinfo=None), problems)
        if n or (problems and done):
            say(f"phone: {n} sent" + (f", problems: {'; '.join(problems)}" if problems else ""))
    finally:
        conn.close()
    return done


# ------------------------------------------------------------------------------------------ for the app
def has_numbers(conn, ticker, day):
    """True when the watch has numbers for this report (alerts.py then leaves the Results alert to the watch)."""
    d0 = dt.date.fromisoformat(day)
    lo, hi = (d0 - dt.timedelta(days=1)).isoformat(), (d0 + dt.timedelta(days=1)).isoformat()
    try:
        return any(summary(r) for r in rows(conn, lo, ticker.upper()) if r["date"] <= hi)
    except Exception:  # noqa: BLE001  no table yet
        return False


def for_ticker(ticker):
    """The latest report of a company in the last KEEP_DAYS days, for the stock page bar. None if there is none."""
    conn = db.connect()
    try:
        since = (dt.date.today() - dt.timedelta(days=KEEP_DAYS)).isoformat()
        rs = [r for r in rows(conn, since, ticker.upper()) if r["out_at"] or summary(r)]
    finally:
        conn.close()
    if not rs:
        return None
    r = rs[-1]
    s = summary(r)
    return dict(date=r["date"], out_at=r["out_at"], press_url=r.get("press_url"), official=bool(r.get("official")),
                filed_at=r["filed_at"], expect=r["expect"], result=r["result"], numbers=s[3] if s else None,
                verdict=s[0] if s else "out")


# ------------------------------------------------------------------------------------------ command line
def _locked():
    try:
        return (time.time() - LOCK.stat().st_mtime) < STALE_LOCK_MIN * 60
    except OSError:
        return False


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--status" in argv:
        conn = db.connect()
        today = dt.datetime.now(NY).date()
        plan(conn)
        for r in rows(conn, (today - dt.timedelta(days=2)).isoformat()):
            s = summary(r)
            print(f"{r['date']} {r['ticker']:6} expected EPS {r['expect'].get('eps')} revenue {r['expect'].get('revenue')}"
                  f" | out {r['out_at'] or '-'} | 10-Q {r['filed_at'] or '-'} | done {r['done'] or '-'}"
                  + (f"\n    {s[1].replace(chr(10), ' / ')}" if s else ""))
        conn.close()
        return 0
    from .config import DB_PATH
    if not DB_PATH.exists():                               # not set up yet: the app's first start does that
        return 0
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if LOCK.exists() and not _locked():
        LOCK.unlink(missing_ok=True)
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return 0                                           # the previous run is still busy
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    log = None
    try:
        def say(msg):
            nonlocal log
            line = f"{dt.datetime.now().strftime('%H:%M:%S')} {msg}"
            print(line)
            if log is None:
                log = open(LOG_DIR / f"earnings_{dt.date.today().isoformat()}.log", "a", encoding="utf-8")
            log.write(line + "\n")
            log.flush()
        run(say=say)
    except Exception as e:  # noqa: BLE001  a scheduled run must never show an error window
        say(f"failed: {e}")
    finally:
        LOCK.unlink(missing_ok=True)
        if log:
            log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
