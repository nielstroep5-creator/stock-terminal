"""Free SEC EDGAR data: ticker list, company profile/filings, and all XBRL facts.

Raw JSON is cached under data/raw/ so re-runs don't hit the SEC unnecessarily.
"""
import json
import threading
import time
from pathlib import Path

import requests

from . import db
from .config import (CACHE_HOURS_COMPANY, CACHE_HOURS_TICKERS, RAW_DIR,
                     SEC_MAX_REQUESTS_PER_SEC, sec_user_agent)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/{name}"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
FRAMES_URL = "https://data.sec.gov/api/xbrl/frames/us-gaap/{tag}/USD/{frame}.json"
FOREVER = 24 * 365 * 50      # cache age for data that never changes (a predecessor's filings)

_session = requests.Session()
_session.headers.update({"Accept-Encoding": "gzip, deflate"})
_last_request = 0.0
_lock = threading.Lock()      # downloads run in several threads; the rate limit is shared


def _get_json(url: str) -> dict:
    return _get(url).json()


def get_bytes(url: str) -> bytes:
    return _get(url).content


def _throttle():
    """Wait for this request's turn: at most SEC_MAX_REQUESTS_PER_SEC over all threads."""
    global _last_request
    with _lock:
        now = time.time()
        slot = max(now, _last_request + 1.0 / SEC_MAX_REQUESTS_PER_SEC)
        _last_request = slot
    if slot > now:
        time.sleep(slot - now)


def _get(url: str):
    """GET with rate limiting (shared by all threads) and retries on throttling / network errors."""
    resp = None
    for attempt in range(5):
        _throttle()
        try:
            resp = _session.get(url, timeout=120, headers={"User-Agent": sec_user_agent()})
        except requests.RequestException:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)
            continue
        if resp.status_code == 200:
            return resp
        if resp.status_code in (429, 500, 502, 503, 504):
            time.sleep(2 ** attempt * (5 if resp.status_code == 429 else 1))
            continue
        break
    if resp is not None and resp.status_code == 403:
        raise RuntimeError(f"SEC refused the request (403). Enter your email in Settings (gear icon, top right): {url}")
    resp.raise_for_status()
    raise RuntimeError(f"SEC request failed: {url}")


def _cached_json(url: str, path: Path, max_age_hours: float, refresh: bool = False):
    """Return (data, from_cache). Fetch only when the cache is missing or stale."""
    if not refresh and path.exists() and (time.time() - path.stat().st_mtime) < max_age_hours * 3600:
        return json.loads(path.read_text(encoding="utf-8")), True
    data = _get_json(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data, False


# ---------------------------------------------------------------- tickers
def load_tickers(conn, refresh=False) -> int:
    data, cached = _cached_json(TICKERS_URL, RAW_DIR / "company_tickers.json",
                                CACHE_HOURS_TICKERS, refresh)
    rows = [(v["ticker"].upper(), int(v["cik_str"]), db.clean_name(v["title"])) for v in data.values()]
    conn.executemany("INSERT OR REPLACE INTO tickers (ticker, cik, name) VALUES (?,?,?)", rows)
    conn.commit()
    db.log(conn, None, "sec_tickers", "cached" if cached else "ok", len(rows))
    return len(rows)


def lookup_cik(conn, ticker: str):
    if conn.execute("SELECT 1 FROM tickers LIMIT 1").fetchone() is None:
        # a fresh install has no SEC company list yet: without it every company was loaded as "prices only"
        load_tickers(conn)
    row = conn.execute("SELECT cik FROM tickers WHERE ticker=?", (ticker.upper(),)).fetchone()
    if row is None:  # SEC uses '-' where others sometimes use '.' (BRK.B vs BRK-B)
        row = conn.execute("SELECT cik FROM tickers WHERE ticker=?",
                           (ticker.upper().replace(".", "-"),)).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------- submissions
def _filing_rows(cik: int, block: dict):
    return [
        (cik, accn, form, fdate, rdate or None, doc or None)
        for accn, form, fdate, rdate, doc in zip(
            block.get("accessionNumber", []), block.get("form", []),
            block.get("filingDate", []), block.get("reportDate", []),
            block.get("primaryDocument", []))
    ]


# ---------------------------------------------------------------- new-filing detection (small requests)
PERIODIC = {"10-K", "10-Q", "20-F", "40-F", "10-K/A", "10-Q/A", "20-F/A", "40-F/A"}
DAILY_INDEX_URL = "https://www.sec.gov/Archives/edgar/daily-index/{y}/QTR{q}/form.{d}.idx"


def _known_accns(conn, cik):
    return {a for (a,) in conn.execute("SELECT accn FROM filings WHERE cik=?", (cik,))}


def new_periodic_filings(conn, cik: int) -> list:
    """10-K / 10-Q / 20-F / 40-F (and amendments) the SEC lists for this company that are not in the
    database yet: one small request (the company's recent-filings list), no companyfacts download."""
    data = _get_json(SUBMISSIONS_URL.format(name=f"CIK{cik:010d}.json"))
    r = data.get("filings", {}).get("recent", {})
    known = _known_accns(conn, cik)
    return [(a, f, d) for a, f, d in zip(r.get("accessionNumber", []), r.get("form", []), r.get("filingDate", []))
            if f in PERIODIC and a not in known]


def daily_index(day) -> list:
    """[(cik, form, accn, date)] of every periodic report filed on `day` (one request for ALL companies).
    Returns None when there is no index for that day (weekend / holiday / not published yet)."""
    url = DAILY_INDEX_URL.format(y=day.year, q=(day.month - 1) // 3 + 1, d=day.strftime("%Y%m%d"))
    try:
        text = _get(url).text
    except Exception as e:  # noqa: BLE001
        if "404" in str(e) or "403" in str(e):
            return None
        raise
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] in PERIODIC and parts[-1].startswith("edgar/data/"):
            accn = parts[-1].rsplit("/", 1)[-1].removesuffix(".txt")
            out.append((int(parts[-3]), parts[0], accn, parts[-2]))
    return out


def load_submissions(conn, ticker: str, cik: int, refresh=False) -> int:
    name = f"CIK{cik:010d}.json"
    data, cached = _cached_json(SUBMISSIONS_URL.format(name=name),
                                RAW_DIR / "submissions" / name, CACHE_HOURS_COMPANY, refresh)
    exchanges = data.get("exchanges") or []
    addr = (data.get("addresses") or {}).get("business") or {}
    db.upsert_company(
        conn, cik,
        ticker=ticker.upper(),
        name=db.clean_name(data.get("name")),
        exchange=exchanges[0] if exchanges else None,
        sic=data.get("sic") or None,
        sic_description=data.get("sicDescription") or None,
        fiscal_year_end=data.get("fiscalYearEnd") or None,
        state=data.get("stateOfIncorporation") or addr.get("stateOrCountry") or None,
        website=data.get("website") or None,
    )

    rows = _filing_rows(cik, data.get("filings", {}).get("recent", {}))
    # Older filings live in extra pages.
    for f in data.get("filings", {}).get("files", []):
        extra, _ = _cached_json(SUBMISSIONS_URL.format(name=f["name"]),
                                RAW_DIR / "submissions" / f["name"], CACHE_HOURS_COMPANY, refresh)
        rows += _filing_rows(cik, extra)

    conn.executemany(
        "INSERT OR REPLACE INTO filings (cik, accn, form, filing_date, report_date, primary_doc) "
        "VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    db.log(conn, ticker, "sec_submissions", "cached" if cached else "ok", len(rows))
    return len(rows)


# ---------------------------------------------------------------- XBRL facts
def load_facts(conn, ticker: str, cik: int, refresh=False) -> int:
    """Store EVERY fact from every taxonomy (us-gaap, dei, srt, ifrs-full, invest, ...)."""
    data, cached = _cached_json(FACTS_URL.format(cik=cik),
                                RAW_DIR / "companyfacts" / f"CIK{cik:010d}.json",
                                CACHE_HOURS_COMPANY, refresh)
    n = _store_facts(conn, cik, data)
    db.log(conn, ticker, "sec_facts", "cached" if cached else "ok", n)
    return n


def _store_facts(conn, cik: int, data: dict) -> int:
    """Write one companyfacts file into xbrl_facts under `cik`."""
    rows = []
    for taxonomy, tags in (data.get("facts") or {}).items():
        for tag, info in tags.items():
            label = info.get("label")
            for unit, facts in (info.get("units") or {}).items():
                for f in facts:
                    rows.append((
                        # as float: a few filings contain integers too large for SQLite (eBay)
                        cik, taxonomy, tag, label, unit,
                        float(f["val"]) if isinstance(f.get("val"), int) else f.get("val"),
                        f.get("start") or "", f.get("end"), f.get("fy"), f.get("fp"),
                        f.get("form"), f.get("filed"), f.get("accn"), f.get("frame"),
                    ))
    conn.executemany(
        "INSERT OR REPLACE INTO xbrl_facts (cik, taxonomy, tag, label, unit, value, start_date, "
        "end_date, fy, fp, form, filed, accn, frame) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    return len(rows)


# ---------------------------------------------------------------- predecessor companies
# The SEC files everything under a registration number (CIK). After a legal reorganisation a
# company continues under a NEW number (Marvell 2021, Alphabet 2015, Disney 2019 ...) and its older
# history stays under the old one. We find the old registrant automatically: the new company's
# first reports repeat the old company's numbers as prior-year comparison, so the old registrant is
# the one that reported exactly the same values for the same dates and then stopped filing.
PROBE_TAGS = ("Assets", "StockholdersEquity", "Liabilities", "LiabilitiesAndStockholdersEquity",
              "NetIncomeLoss", "OperatingIncomeLoss", "CashAndCashEquivalentsAtCarryingValue")
FIRST_XBRL_FOR_ALL = "2011-12-31"     # by then every listed company was filing XBRL


def _find_predecessor(conn, cik: int, first_filed: str, exclude=()):
    """Return (predecessor_cik, name, matches) or None. `first_filed`: first XBRL filing date."""
    ph = ",".join("?" * len(PROBE_TAGS))
    # Only periods the old registrant can have reported itself: the new company's first own
    # report covers a period ending up to ~3 months before its first filing, so stay clear of that.
    cutoff = _plus_days(first_filed, -100)
    rows = conn.execute(
        f"SELECT tag, end_date, value, frame FROM xbrl_facts WHERE cik=? AND taxonomy='us-gaap' "
        f"AND tag IN ({ph}) AND unit='USD' AND frame IS NOT NULL AND value!=0 AND end_date<? "
        f"GROUP BY tag, end_date ORDER BY end_date DESC",
        (cik, *PROBE_TAGS, cutoff)).fetchall()
    dates = sorted({r[1] for r in rows}, reverse=True)[:2]       # the two latest such dates
    probes = [r for r in rows if r[1] in dates][:10]
    votes, names = {}, {}
    for tag, end, value, frame in probes:
        try:
            data = _get_json(FRAMES_URL.format(tag=tag, frame=frame))
        except Exception:  # noqa: BLE001 - a missing frame just means one probe less
            continue
        for d in data.get("data", []):
            c = int(d["cik"])
            if c == cik or c in exclude or d.get("end") != end:
                continue
            if abs(d["val"] - value) <= 0.002 * abs(value):
                # count each (date, value) once: Assets and "liabilities and equity" are the same
                # number, so one coincidence would otherwise count twice
                votes.setdefault(c, set()).add((end, round(value)))
                names[c] = d.get("entityName")
    own_sic = _sic_of(conn, cik)
    for c, hits in sorted(votes.items(), key=lambda kv: -len(kv[1])):
        if len(hits) < MIN_MATCHES:
            break
        # A predecessor stopped filing around the hand-over. (A subsidiary that files the same
        # numbers alongside its parent is still filing today and is not a predecessor.)
        sub, _ = _cached_json(SUBMISSIONS_URL.format(name=f"CIK{c:010d}.json"),
                              RAW_DIR / "submissions" / f"CIK{c:010d}.json", FOREVER)
        recent = sub.get("filings", {}).get("recent", {})
        last = max((d for f, d in zip(recent.get("form", []), recent.get("filingDate", []))
                    if f in ("10-K", "10-Q", "20-F")), default="")
        if not (last and last[:4] <= str(int(first_filed[:4]) + 1) and last < _plus_days(first_filed, 200)):
            continue
        if not _plausible(len(hits), own_sic, sub.get("sic")):
            continue
        return c, names[c] or sub.get("name"), len(hits)
    return None


# Two equal numbers can be a coincidence (Robinhood's first balance sheet "matched" a life insurer,
# SpaceX one of Honda's finance arms). So with only a few matching values the old registrant must
# also be in the same line of business (same 2-digit SIC industry code); with many it is accepted.
MIN_MATCHES = 2          # distinct (date, value) pairs, never fewer
SURE_MATCHES = 4         # this many: accepted whatever the industry code says


def _sic_of(conn, cik):
    r = conn.execute("SELECT sic FROM companies WHERE cik=?", (cik,)).fetchone()
    return (r[0] or "").strip() if r else ""


def _plausible(hits: int, own_sic, pred_sic) -> bool:
    if hits >= SURE_MATCHES:
        return True
    if hits < MIN_MATCHES:
        return False
    a, b = str(own_sic or "").strip(), str(pred_sic or "").strip()
    return bool(a and b and a[:2] == b[:2])


def _drop_predecessor(conn, cik: int, pred: int):
    """Undo a wrong link: remove the old registrant's facts, filings and segments stored under `cik`."""
    pred_accns = "SELECT accn FROM filings WHERE cik=?"
    conn.execute(f"DELETE FROM xbrl_facts WHERE cik=? AND accn IN ({pred_accns})", (cik, pred))
    conn.execute(f"DELETE FROM segment_facts WHERE cik=? AND accn IN ({pred_accns})", (cik, pred))
    conn.execute("DELETE FROM parsed_filings WHERE cik=?", (pred,))
    if not conn.execute("SELECT 1 FROM companies WHERE cik=?", (pred,)).fetchone():
        conn.execute("DELETE FROM filings WHERE cik=?", (pred,))     # unless it is a company of its own
    conn.execute("DELETE FROM predecessors WHERE cik=? AND predecessor_cik=?", (cik, pred))


def _recheck_known(conn, ticker, cik, known) -> bool:
    """Links found by an older, looser rule: drop those that fail today's rule. True if any dropped."""
    own_sic, dropped = _sic_of(conn, cik), False
    for pred, name, matched in known:
        if not pred or (matched or 0) >= SURE_MATCHES:
            continue
        name_ = f"CIK{pred:010d}.json"
        try:
            sub, _ = _cached_json(SUBMISSIONS_URL.format(name=name_), RAW_DIR / "submissions" / name_, FOREVER)
        except Exception:  # noqa: BLE001 - cannot check now (offline): keep the link
            continue
        if _plausible(matched or 0, own_sic, sub.get("sic")):
            continue
        _drop_predecessor(conn, cik, pred)
        db.log(conn, ticker, "sec_predecessor", "ok", 0,
               f"removed wrong earlier registrant {name} (CIK {pred}): other industry, only {matched} matching values")
        dropped = True
    return dropped


def _plus_days(date: str, days: int) -> str:
    from datetime import date as _d, timedelta
    return (_d.fromisoformat(date) + timedelta(days=days)).isoformat()


def _load_predecessor(conn, cik: int, pred: int):
    """Store the predecessor's facts under `cik` and its filing list under its own number.
    Returns the predecessor's first XBRL filing date."""
    name = f"CIK{pred:010d}.json"
    sub, _ = _cached_json(SUBMISSIONS_URL.format(name=name), RAW_DIR / "submissions" / name, FOREVER)
    rows = _filing_rows(pred, sub.get("filings", {}).get("recent", {}))
    for f in sub.get("filings", {}).get("files", []):
        extra, _ = _cached_json(SUBMISSIONS_URL.format(name=f["name"]),
                                RAW_DIR / "submissions" / f["name"], FOREVER)
        rows += _filing_rows(pred, extra)
    conn.executemany(
        "INSERT OR REPLACE INTO filings (cik, accn, form, filing_date, report_date, primary_doc) "
        "VALUES (?,?,?,?,?,?)", rows)
    data, _ = _cached_json(FACTS_URL.format(cik=pred), RAW_DIR / "companyfacts" / name, FOREVER)
    n = _store_facts(conn, cik, data)
    filed = [f.get("filed") for tags in (data.get("facts") or {}).values() for info in tags.values()
             for facts in (info.get("units") or {}).values() for f in facts
             if f.get("form") in ("10-K", "10-Q") and f.get("filed")]
    return n, (min(filed) if filed else None)


def load_predecessors(conn, ticker: str, cik: int):
    """Find (once) and load earlier registrants of this company. Returns [(cik, name), ...]."""
    known = conn.execute("SELECT predecessor_cik, name, matched FROM predecessors WHERE cik=? ORDER BY rowid",
                         (cik,)).fetchall()
    if known and _recheck_known(conn, ticker, cik, known):
        conn.execute("DELETE FROM predecessors WHERE cik=?", (cik,))   # search again with today's rule
        conn.commit()
        known = []
    known = [(c, n) for c, n, _ in known]
    if known:                                   # already searched: just make sure the data is loaded
        preds = [(c, n) for c, n in known if c]
        for c, _ in preds:
            _load_predecessor(conn, cik, c)
        return preds

    # Own filings only: facts of a predecessor loaded earlier are stored under this cik too.
    # Only filings with real numbers count: a merger vehicle often files as an empty shell first
    # (Linde plc 2017, before it took over Praxair's business in 2018).
    own = ("FROM xbrl_facts WHERE cik=? AND taxonomy='us-gaap' AND form IN ('10-K','10-Q') "
           "AND accn IN (SELECT accn FROM filings WHERE cik=?)")
    top = conn.execute(f"SELECT MAX(value) {own} AND tag='Assets'", (cik, cik)).fetchone()[0] or 0
    first = conn.execute(f"SELECT MIN(filed) {own} AND tag='Assets' AND value >= ?",
                         (cik, cik, top * 0.001)).fetchone()[0] if top else None
    first = first or conn.execute(f"SELECT MIN(filed) {own}", (cik, cik)).fetchone()[0]
    preds = []
    while first and first > FIRST_XBRL_FOR_ALL and len(preds) < 3:
        found = _find_predecessor(conn, cik, first, exclude=[c for c, _ in preds])
        if not found:
            break
        pred, name, hits = found
        n, pred_first = _load_predecessor(conn, cik, pred)
        conn.execute("INSERT OR REPLACE INTO predecessors (cik, predecessor_cik, name, handover, matched, "
                     "found_at) VALUES (?,?,?,?,?,?)", (cik, pred, name, first, hits, db.now_iso()))
        db.log(conn, ticker, "sec_predecessor", "ok", n, f"{name} (CIK {pred}), {hits} matching values")
        preds.append((pred, name))
        if not pred_first or pred_first >= first:
            break
        first = pred_first
    conn.execute("INSERT OR REPLACE INTO predecessors (cik, predecessor_cik, name, handover, matched, found_at) "
                 "VALUES (?,0,NULL,NULL,0,?)", (cik, db.now_iso()))      # marker: search done
    conn.commit()
    return preds
