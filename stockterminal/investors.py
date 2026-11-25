"""Big investors of a stock, for the stock page's Ownership tab:

1. The largest institutional holders and how much insiders / institutions own (Yahoo, from the 13F filings;
   cached HOLDERS_DAYS per stock in holders_cache).
2. Famous investors (FAMOUS: Berkshire Hathaway, Pershing Square, Scion...): their own 13F filings at the SEC, the
   latest two quarters, so the tab shows what they hold of this stock and what they bought or sold. One shared
   download for all stocks (about 3 requests per investor per quarter), checked at most once every CHECK_DAYS.

13F filings name stocks by CUSIP, not ticker. A company's CUSIP comes from the Schedule 13G / 13D filings about
it (in its own filing list at the SEC, one or two small requests, kept in issuer_cusips); as a fallback, the
company name is matched. All values are in US dollars (13F values are dollars since 2023, thousands before).
"""
import datetime as dt
import json
import re
import threading
import time
import xml.etree.ElementTree as ET

from . import db, sec

HOLDERS_DAYS = 3
CHECK_DAYS = 7
DOLLARS_FROM = "2023-01-03"          # 13F filings before this date give values in thousands
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/"

# (SEC CIK of the 13F filer, who people know it by). Shared by everybody, not one person's list.
FAMOUS = [
    (1067983, "Warren Buffett"),            # Berkshire Hathaway
    (1336528, "Bill Ackman"),               # Pershing Square
    (1649339, "Michael Burry"),             # Scion Asset Management
    (1536411, "Stanley Druckenmiller"),     # Duquesne Family Office
    (1656456, "David Tepper"),              # Appaloosa
    (1061768, "Seth Klarman"),              # Baupost
    (1040273, "Dan Loeb"),                  # Third Point
    (1079114, "David Einhorn"),             # Greenlight Capital
    (921669, "Carl Icahn"),                 # Icahn Enterprises / Carl C. Icahn
    (1709323, "Li Lu"),                     # Himalaya Capital
    (1029160, "George Soros"),              # Soros Fund Management
    (1350694, "Ray Dalio"),                 # Bridgewater
    (1167483, "Chase Coleman"),             # Tiger Global
    (1135730, "Philippe Laffont"),          # Coatue
    (1061165, "Stephen Mandel"),            # Lone Pine
    (1103804, "Andreas Halvorsen"),         # Viking Global
    (1697748, "Cathie Wood"),               # ARK Investment Management
    (1569205, "Terry Smith"),               # Fundsmith
    (1096343, "Tom Gayner"),                # Markel
    (1112520, "Chuck Akre"),                # Akre Capital
    (1166559, "Gates Foundation"),          # Bill & Melinda Gates Foundation Trust
    (1037389, "Jim Simons"),                # Renaissance Technologies
]
LABEL = dict(FAMOUS)

# ------------------------------------------------------------------------------------------ names and CUSIPs
STOP = {"INC", "CORP", "CORPORATION", "CO", "COMPANY", "COS", "LTD", "LIMITED", "PLC", "NV", "SA", "AG", "SE", "LP",
        "LLC", "THE", "OF", "AND", "DEL", "NEW", "COM", "HLDGS", "HOLDINGS", "HOLDING", "HLDG", "GROUP", "GRP", "CL",
        "CLASS", "INCORPORATED", "ORD", "SHS", "CAP", "STK", "N", "V", "A", "B", "C", "THE", "TR"}
ABBR = {"AMER": "AMERICA", "AMERN": "AMERICAN", "PETE": "PETROLEUM", "INTL": "INTERNATIONAL", "TECHNOLOGIES": "TECH",
        "TECHNOLOGY": "TECH", "TECHNOLOGIE": "TECH", "SYS": "SYSTEMS", "MTRS": "MOTORS", "FINL": "FINANCIAL",
        "SVCS": "SERVICES", "SVC": "SERVICE", "PHARMACEUTICALS": "PHARMA", "PHARMACEUTICAL": "PHARMA",
        "MGMT": "MANAGEMENT", "COMMUNICATIONS": "COMM", "COMMUNICATION": "COMM", "COMMUNICATNS": "COMM",
        "ENTMT": "ENTERTAINMENT", "BANCORPORATION": "BANCORP", "LABS": "LABORATORIES", "INDS": "INDUSTRIES",
        "PPTYS": "PROPERTIES", "RLTY": "REALTY", "RES": "RESOURCES", "SOLUTIONS": "SOLUTION", "ENERGY": "ENERGY"}


def norm(name):
    """A company name in a form that 13F names and SEC names share: "Bank of America Corp" and "BANK AMER CORP"
    both become "BANK AMERICA"."""
    n = re.sub(r"[^A-Z0-9 ]", " ", str(name or "").upper().replace("&", " AND ").replace(".COM", "COM"))
    words = [ABBR.get(w, w) for w in n.split()]
    return " ".join(w for w in words if w not in STOP)


def cusip_ok(c):
    """True when the last digit of a 9-character CUSIP is right (its check digit)."""
    c = (c or "").upper()
    if not re.fullmatch(r"[0-9A-Z]{8}[0-9]", c):
        return False
    total = 0
    for i, ch in enumerate(c[:8]):
        v = int(ch) if ch.isdigit() else ord(ch) - 55
        if i % 2:
            v *= 2
        total += v // 10 + v % 10
    return (10 - total % 10) % 10 == int(c[8])


CUSIP_XML = re.compile(rb"<(?:\w+:)?\w*cusip\w*>\s*([0-9A-Za-z]{9})\s*<", re.I)
_C = rb"([0-9A-Z]{6})[ \t-]?([0-9A-Z]{2})[ \t-]?([0-9])"
CUSIP_TEXT = [re.compile(rb"CUSIP\s*(?:No\.?|Number|#)?[^0-9A-Za-z]{0,60}?" + _C + rb"(?![0-9A-Za-z])", re.I),
              re.compile(rb"(?<![0-9A-Za-z])" + _C + rb"(?:<[^>]*>|[^0-9A-Za-z<]){0,80}\(?CUSIP", re.I)]


def cusips_in(doc: bytes):
    """Every valid CUSIP named in a Schedule 13G / 13D (XML since December 2024, text or HTML before)."""
    found = [m.group(1).decode().upper() for m in CUSIP_XML.finditer(doc)]
    if not found:
        text = re.sub(rb"&nbsp;|&#160;", b" ", doc[:300_000])
        found = [b"".join(m.groups()).decode().upper() for rx in CUSIP_TEXT for m in rx.finditer(text)]
    return list(dict.fromkeys(c for c in found if cusip_ok(c)))


SCHEDULES = ("SCHEDULE 13G", "SCHEDULE 13G/A", "SCHEDULE 13D", "SCHEDULE 13D/A", "SC 13G", "SC 13G/A", "SC 13D", "SC 13D/A")


def issuer_cusips(conn, cik):
    """The CUSIPs of a company's shares (from the Schedule 13G / 13D filings about it), stored for half a year."""
    cik = int(cik)
    row = conn.execute("SELECT cusips, checked FROM issuer_cusips WHERE cik=?", (cik,)).fetchone()
    if row:
        got = json.loads(row[0] or "[]")
        age = (dt.date.today() - dt.date.fromisoformat(row[1][:10])).days
        if age < (180 if got else 30):
            return got
    name = f"CIK{cik:010d}.json"
    data, _ = sec._cached_json(sec.SUBMISSIONS_URL.format(name=name), sec.RAW_DIR / "submissions" / name, 24)
    r = (data.get("filings") or {}).get("recent") or {}
    found = []
    tries = 0
    for accn, form, doc in zip(r.get("accessionNumber", []), r.get("form", []), r.get("primaryDocument", [])):
        if form not in SCHEDULES or not doc or tries >= 3:
            continue
        tries += 1
        url = ARCHIVE.format(cik=cik, folder=accn.replace("-", "")) + doc.rsplit("/", 1)[-1]
        try:
            found += cusips_in(sec.get_bytes(url))
        except Exception:  # noqa: BLE001
            continue
        if found:
            break
    found = list(dict.fromkeys(found))
    conn.execute("INSERT OR REPLACE INTO issuer_cusips VALUES (?,?,?)", (cik, json.dumps(found), db.now_iso()))
    conn.commit()
    return found


# ------------------------------------------------------------------------------------------ 13F filings
def _local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_13f(xml_bytes, filed):
    """A 13F information table -> {cusip: dict(issuer, cls, shares, value)}: shares only (no options, no bonds),
    rows of the same CUSIP added up (one per manager), values in dollars."""
    root = ET.fromstring(xml_bytes)
    mult = 1000 if (filed or "9999") < DOLLARS_FROM else 1
    out = {}
    for el in root.iter():
        if _local(el.tag) != "infoTable":
            continue
        f = {}
        for c in el.iter():
            k = _local(c.tag)
            if k not in f and c.text and c.text.strip():
                f[k] = c.text.strip()
        if f.get("putCall") or (f.get("sshPrnamtType") or "SH").upper() != "SH":
            continue
        cusip = (f.get("cusip") or "").upper()
        try:
            shares, value = float(f.get("sshPrnamt") or 0), float(f.get("value") or 0) * mult
        except ValueError:
            continue
        if not cusip:
            continue
        h = out.setdefault(cusip, dict(issuer=f.get("nameOfIssuer"), cls=f.get("titleOfClass"), shares=0.0, value=0.0))
        h["shares"] += shares
        h["value"] += value
    sh, val = sum(h["shares"] for h in out.values()), sum(h["value"] for h in out.values())
    if mult == 1 and sh and val / sh < 1:      # some filers still give thousands after 2023 (Baupost, Duquesne):
        for h in out.values():                 # an average price under $1 a share is impossible for these funds
            h["value"] *= 1000
    return out


def fix_thousands(conn):
    """Stored filings parsed before the check above: values in thousands taken as dollars. Idempotent."""
    bad = [a for (a,) in conn.execute("SELECT accn FROM inv_holdings GROUP BY accn "
                                      "HAVING SUM(shares) > 0 AND SUM(value) / SUM(shares) < 1")]
    for a in bad:
        conn.execute("UPDATE inv_holdings SET value = value * 1000 WHERE accn=?", (a,))
        conn.execute("UPDATE inv_filings SET total_value = total_value * 1000 WHERE accn=?", (a,))
    if bad:
        conn.commit()
    return len(bad)


OLD_DAYS = 200        # latest 13F quarter older than this: the investor stopped filing or files under another name


def _info_table_url(cik, accn):
    """The XML information table of a 13F filing (the filing's file list names it)."""
    base = ARCHIVE.format(cik=cik, folder=accn.replace("-", ""))
    items = (sec._get_json(base + "index.json").get("directory") or {}).get("item") or []
    xml = [i for i in items if str(i.get("name", "")).lower().endswith(".xml") and i.get("name") != "primary_doc.xml"]
    if not xml:
        return None
    xml.sort(key=lambda i: ("info" not in i["name"].lower() and "table" not in i["name"].lower(), -int(i.get("size") or 0)))
    return base + xml[0]["name"]


def refresh_filer(conn, cik, keep=2):
    """Download the latest `keep` quarters of one investor's 13F that are not stored yet."""
    data = sec._get_json(sec.SUBMISSIONS_URL.format(name=f"CIK{int(cik):010d}.json"))
    r = (data.get("filings") or {}).get("recent") or {}
    seen, want = set(), []
    for accn, form, filed, period in zip(r.get("accessionNumber", []), r.get("form", []), r.get("filingDate", []),
                                         r.get("reportDate", [])):
        if form == "13F-HR" and period and period not in seen:
            seen.add(period)
            want.append((accn, filed, period))
    want = sorted(want, key=lambda x: x[2], reverse=True)[:keep]
    have = {a for (a,) in conn.execute("SELECT accn FROM inv_filings WHERE cik=?", (cik,))}
    for accn, filed, period in want:
        if accn in have:
            continue
        url = _info_table_url(cik, accn)
        rows = parse_13f(sec.get_bytes(url), filed) if url else {}
        conn.execute("DELETE FROM inv_holdings WHERE accn=?", (accn,))
        conn.executemany("INSERT INTO inv_holdings VALUES (?,?,?,?,?,?)",
                         [(accn, c, h["issuer"], h["cls"], h["shares"], h["value"]) for c, h in rows.items()])
        conn.execute("INSERT OR REPLACE INTO inv_filings VALUES (?,?,?,?,?,?)",
                     (accn, cik, period, filed, sum(h["value"] for h in rows.values()), len(rows)))
        conn.commit()
    keep_accns = [a for a, _, _ in want]
    old = [a for (a,) in conn.execute("SELECT accn FROM inv_filings WHERE cik=?", (cik,)) if a not in keep_accns]
    for a in old:
        conn.execute("DELETE FROM inv_holdings WHERE accn=?", (a,))
        conn.execute("DELETE FROM inv_filings WHERE accn=?", (a,))
    conn.execute("INSERT OR REPLACE INTO inv_filers VALUES (?,?,?)",
                 (cik, db.clean_name(data.get("name")) or LABEL.get(cik), db.now_iso()))
    conn.commit()


_lock = threading.Lock()
_state = dict(running=False, error=None, done=0)


def stale(conn):
    """The famous investors not checked in the last CHECK_DAYS."""
    checked = dict(conn.execute("SELECT cik, checked FROM inv_filers").fetchall())
    limit = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=CHECK_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [c for c, _ in FAMOUS if (checked.get(c) or "") < limit]


def refresh(say=None):
    """Check every famous investor that is due (one at a time; a failure is tried again next time)."""
    if not _lock.acquire(blocking=False):
        return
    _state.update(running=True, error=None, done=0)
    conn = db.connect()
    try:
        for cik in stale(conn):
            try:
                refresh_filer(conn, cik)
                _state["done"] += 1
            except Exception as e:  # noqa: BLE001  offline / one filer changed: the others still load
                _state["error"] = str(e)[:200]
                if say:
                    say(f"13F {LABEL.get(cik, cik)}: {e}")
    finally:
        conn.close()
        _state["running"] = False
        _lock.release()


def refresh_in_background(conn):
    """Start refresh() in a thread when something is due; {running, missing, error}."""
    due = stale(conn)
    if due and not _state["running"] and (not _state.get("tried") or time.time() - _state["tried"] > 600):
        _state["tried"] = time.time()
        threading.Thread(target=refresh, daemon=True).start()
        _state["running"] = True
    have = conn.execute("SELECT COUNT(*) FROM inv_filers").fetchone()[0]
    return dict(running=_state["running"], loaded=have, total=len(FAMOUS), error=_state["error"])


def famous_for(conn, cik, names):
    """Which famous investors hold this company, from their latest 13F, with the change against the quarter before.
    names: the company's names (SEC, Yahoo) for when its CUSIP is not known."""
    try:
        cus = issuer_cusips(conn, cik)
    except Exception:  # noqa: BLE001  offline: match by name only
        row = conn.execute("SELECT cusips FROM issuer_cusips WHERE cik=?", (int(cik),)).fetchone()
        cus = json.loads(row[0]) if row and row[0] else []
    pre = {c[:6] for c in cus}
    want = {norm(n) for n in names if n and norm(n)}
    # a CUSIP found in the company's filings can be another company's (Amazon's filings gave 008470106): keep
    # only the ones some 13F actually uses, and match by name when none is
    pre = {p for p in pre if conn.execute("SELECT 1 FROM inv_holdings WHERE substr(cusip, 1, 6)=? LIMIT 1",
                                          (p,)).fetchone()}
    fix_thousands(conn)
    old_before = (dt.date.today() - dt.timedelta(days=OLD_DAYS)).isoformat()
    filings = {}
    for accn, fcik, period, filed, total in conn.execute("SELECT accn, cik, period, filed, total_value FROM inv_filings"):
        filings.setdefault(fcik, []).append(dict(accn=accn, period=period, filed=filed, total=total))
    out = []
    for fcik, fl in filings.items():
        fl.sort(key=lambda f: f["period"], reverse=True)
        sums = []
        for f in fl[:2]:
            s = dict(shares=0.0, value=0.0, classes=set())
            for cusip, issuer, cls, shares, value in conn.execute(
                    "SELECT cusip, issuer, cls, shares, value FROM inv_holdings WHERE accn=?", (f["accn"],)):
                if cusip[:6] in pre or (not pre and norm(issuer) in want):
                    s["shares"] += shares
                    s["value"] += value
                    s["classes"].add(cls or "")
            sums.append(s)
        cur, prev = sums[0], (sums[1] if len(sums) > 1 else None)
        if not cur["shares"] and not (prev and prev["shares"]):
            continue
        name = conn.execute("SELECT name FROM inv_filers WHERE cik=?", (fcik,)).fetchone()
        change = None
        if prev is not None:
            change = "new" if not prev["shares"] else ("sold" if not cur["shares"] else cur["shares"] / prev["shares"] - 1)
        out.append(dict(cik=fcik, investor=LABEL.get(fcik), fund=name[0] if name else None, period=fl[0]["period"],
                        filed=fl[0]["filed"], old=fl[0]["period"] < old_before, shares=cur["shares"], value=cur["value"],
                        weight=cur["value"] / fl[0]["total"] if fl[0]["total"] else None, change=change,
                        prev_shares=prev["shares"] if prev else None, prev_period=fl[1]["period"] if len(fl) > 1 else None,
                        url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={fcik}&type=13F-HR"))
    out.sort(key=lambda r: (r["old"], -r["value"]))       # investors without a recent report last
    return dict(holders=out, matched_by="cusip" if pre else "name")


# ------------------------------------------------------------------------------------------ Yahoo: largest holders
def _records(df):
    try:
        return [] if df is None or getattr(df, "empty", True) else df.reset_index().to_dict("records")
    except Exception:  # noqa: BLE001
        return []


def _f(v):
    try:
        x = float(v)
        return None if x != x else x
    except (TypeError, ValueError):
        return None


def yahoo_fetch(symbol):
    """{insiders, institutions, institutions_float, institutions_count, top: [...]} from Yahoo."""
    import yfinance as yf
    tk = yf.Ticker(symbol)
    out = dict(insiders=None, institutions=None, institutions_float=None, institutions_count=None, top=[])
    for r in _records(tk.major_holders):
        vals = list(r.values())
        key, val = (str(vals[0]), vals[-1]) if len(vals) >= 2 else (None, None)
        k = {"insidersPercentHeld": "insiders", "institutionsPercentHeld": "institutions",
             "institutionsFloatPercentHeld": "institutions_float", "institutionsCount": "institutions_count"}.get(key)
        if k:
            out[k] = _f(val)
    for r in _records(tk.institutional_holders)[:10]:
        d = r.get("Date Reported")
        out["top"].append(dict(
            holder=r.get("Holder"), shares=_f(r.get("Shares")), value=_f(r.get("Value")),
            pct=_f(r.get("pctHeld") if "pctHeld" in r else r.get("% Out")), change=_f(r.get("pctChange")),
            date=d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else (str(d)[:10] if d else None)))
    out["top"] = [t for t in out["top"] if t["holder"]]
    return out


def yahoo_holders(conn, symbol, refresh=True):
    """yahoo_fetch, kept HOLDERS_DAYS in holders_cache; offline the stored one (stale=True)."""
    row = conn.execute("SELECT data, fetched FROM holders_cache WHERE symbol=?", (symbol,)).fetchone()
    if row and (not refresh or time.time() - row[1] < HOLDERS_DAYS * 86400):
        return dict(json.loads(row[0]), fetched=row[1])
    if not refresh:
        return None
    try:
        data = yahoo_fetch(symbol)
    except Exception as e:  # noqa: BLE001
        return dict(json.loads(row[0]), fetched=row[1], stale=True) if row else dict(error=str(e)[:200], top=[])
    now = time.time()
    conn.execute("INSERT OR REPLACE INTO holders_cache VALUES (?,?,?)", (symbol, json.dumps(data), now))
    conn.commit()
    return dict(data, fetched=now)
