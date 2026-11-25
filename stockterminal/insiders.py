"""Insider trades (SEC Form 4): what directors, officers and 10% owners bought and sold, for the stock page's
Ownership tab and the "insider bought" alert.

Cheap and incremental: the company's recent-filings list (one request, cached a few hours) names every Form 4;
only Form 4s that are not stored yet are downloaded (one small XML each), newest first, at most `max_new` per call.
A filing is stored once and never asked again (`insider_filings`), so opening a page again costs one request at most.

Amounts are in US dollars: Form 4 prices are per share in dollars. Only the stock table of a Form 4 is read
(bought, sold, awarded, exercised...); options and other derivatives are left out.
"""
import datetime as dt
import re
import xml.etree.ElementTree as ET

from . import db, sec
from .config import RAW_DIR

YEARS = 2                     # how far back the tab goes
MAX_NEW = 40                  # Form 4s downloaded per call (about 5 seconds at the SEC's rate limit)
LIST_HOURS = 4                # the company's filing list is asked again after this
FORMS = ("4", "4/A")
DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{doc}"

# Transaction codes of Form 4 (SEC instructions, General Instruction 8)
CODES = {
    "P": "Buy", "S": "Sale", "A": "Award", "M": "Option exercise", "X": "Option exercise", "C": "Conversion",
    "F": "Tax withholding", "G": "Gift", "D": "Back to company", "J": "Other", "I": "Discretionary",
    "W": "Inheritance", "Z": "Trust", "E": "Option expired", "H": "Option expired", "O": "Option exercise",
    "K": "Swap", "L": "Small purchase", "U": "Tender", "V": "Voluntary report",
}
MARKET = ("P", "S")           # open-market buys and sales: what people mean by "insiders buy / sell"



# ------------------------------------------------------------------------------------------ reading a Form 4
def _t(el, path):
    """Text of el/path, also when the value sits in a <value> child (Form 4 does both)."""
    if el is None:
        return None
    node = el.find(path)
    if node is None:
        return None
    v = node.find("value")
    txt = (v.text if v is not None else node.text) or ""
    txt = txt.strip()
    return txt or None


def _num(v):
    try:
        return float(str(v).replace(",", "")) if v not in (None, "") else None
    except ValueError:
        return None


def _yes(v):
    return str(v or "").strip().lower() in ("1", "true", "y", "yes")


TITLES = [(r"\bchief executive|\bceo\b", "CEO"), (r"\bchief financial|\bcfo\b", "CFO"),
          (r"\bchief operating|\bcoo\b", "COO"), (r"\bpresident\b", "President"), (r"\bchair", "Chair"),
          (r"\bgeneral counsel|\bchief legal|\bclo\b", "General counsel"), (r"\bchief accounting|\bcao\b", "CAO"),
          (r"\bchief technology|\bcto\b", "CTO")]


def role_of(rel):
    """A short role from the reporting-owner relationship: "CEO", "Director", "10% owner", "EVP, Sales"..."""
    out = []
    title = (rel.get("title") or "").strip()
    if rel.get("officer"):
        short = [name for pat, name in TITLES if re.search(pat, title, re.I)]
        out.append(" & ".join(short[:2]) if short else (title[:40] or "Officer"))
    if rel.get("director") and "Director" not in out:
        out.append("Director")
    if rel.get("ten"):
        out.append("10% owner")
    if not out and rel.get("other"):
        out.append((rel.get("other_text") or "Other")[:40])
    return ", ".join(out) or None


def owner_name(name):
    """SEC names are "COOK TIMOTHY D" (last name first, capitals): "Timothy D Cook". Firms stay as they are."""
    if not name:
        return name
    n = " ".join(name.split())
    if re.search(r"\b(LLC|L\.?P\.?|INC|CORP|TRUST|FUND|PARTNERS|CAPITAL|HOLDINGS|GROUP|LTD|CO|FOUNDATION|MANAGEMENT|BANK)\b\.?",
                 n, re.I):
        return n if not n.isupper() else n.title().replace("Llc", "LLC").replace("L.P.", "L.P.")
    parts = n.split()
    if n.isupper() and len(parts) >= 2:
        last, rest = parts[0], parts[1:]
        suffix = [p for p in rest if p.rstrip(".").upper() in ("JR", "SR", "II", "III", "IV")]
        rest = [p for p in rest if p not in suffix]
        return " ".join(w.capitalize() if len(w.rstrip(".")) > 1 else w for w in rest + [last] + suffix)
    return n


def parse(xml_bytes):
    """A Form 4 XML -> dict(issuer_cik, owners=[...], role, plan, trades=[...]). Raises ValueError when it is not one."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise ValueError(f"not a Form 4 XML: {e}") from None
    if root.tag != "ownershipDocument":
        raise ValueError("not a Form 4 XML")
    owners, rels = [], []
    for ro in root.findall("reportingOwner"):
        name = _t(ro, "reportingOwnerId/rptOwnerName")
        rel = ro.find("reportingOwnerRelationship")
        rels.append(dict(director=_yes(_t(rel, "isDirector")), officer=_yes(_t(rel, "isOfficer")),
                         ten=_yes(_t(rel, "isTenPercentOwner")), other=_yes(_t(rel, "isOther")),
                         title=_t(rel, "officerTitle"), other_text=_t(rel, "otherText")))
        owners.append(dict(name=owner_name(name), cik=int(_num(_t(ro, "reportingOwnerId/rptOwnerCik")) or 0) or None))
    # one role for the filing: the first owner that is an officer/director (joint filings: a person and their fund)
    rel = next((r for r in rels if r["officer"] or r["director"]), rels[0] if rels else {})
    notes = " ".join((f.text or "") for f in root.iter("footnote"))
    plan = _yes(_t(root, "aff10b5One")) or bool(re.search(r"10b5-1", notes, re.I))
    trades = []
    for i, tr in enumerate(root.findall("nonDerivativeTable/nonDerivativeTransaction")):
        code = _t(tr, "transactionCoding/transactionCode")
        shares = _num(_t(tr, "transactionAmounts/transactionShares"))
        if not code or shares is None:
            continue
        price = _num(_t(tr, "transactionAmounts/transactionPricePerShare"))
        trades.append(dict(
            line=i, date=(_t(tr, "transactionDate") or "")[:10] or None, code=code.upper()[:1],
            ad=_t(tr, "transactionAmounts/transactionAcquiredDisposedCode"),
            shares=shares, price=price if price and price > 0 else None,
            owned_after=_num(_t(tr, "postTransactionAmounts/sharesOwnedFollowingTransaction")),
            direct=_t(tr, "ownershipNature/directOrIndirectOwnership"),
            security=_t(tr, "securityTitle")))
    main = next((o for o, r in zip(owners, rels) if r is rel), owners[0] if owners else {})
    return dict(issuer_cik=int(_num(_t(root, "issuer/issuerCik")) or 0) or None, owner=main.get("name"),
                owner_cik=main.get("cik"), role=role_of(rel) if rel else None, plan=plan, trades=trades)


def raw_doc(primary_doc):
    """The submissions list names the styled page ("xslF345X05/form4.xml"); the XML itself is the same file
    without the style folder."""
    if not primary_doc:
        return None
    doc = primary_doc.rsplit("/", 1)[-1]
    return doc if doc.lower().endswith(".xml") else None


# ------------------------------------------------------------------------------------------ downloading
def _form4_list(cik, refresh=False):
    """[(accn, filed, primary_doc)] of every Form 4 in the company's recent-filings list, newest first."""
    name = f"CIK{int(cik):010d}.json"
    data, _ = sec._cached_json(sec.SUBMISSIONS_URL.format(name=name), RAW_DIR / "submissions" / name,
                               0 if refresh else LIST_HOURS)
    r = (data.get("filings") or {}).get("recent") or {}
    out = [(a, d, doc) for a, f, d, doc in zip(r.get("accessionNumber", []), r.get("form", []), r.get("filingDate", []),
                                               r.get("primaryDocument", [])) if f in FORMS]
    out.sort(key=lambda x: x[1], reverse=True)
    return out


def update(conn, cik, since=None, max_new=MAX_NEW, refresh=False):
    """Download the Form 4s of this company filed on or after `since` (default: YEARS back) that are not stored yet,
    newest first, at most max_new. {new, left, failed}: left = how many in that window are still to download."""
    cik = int(cik)
    since = since or (dt.date.today() - dt.timedelta(days=365 * YEARS)).isoformat()
    known = {a for (a,) in conn.execute("SELECT accn FROM insider_filings WHERE cik=?", (cik,))}
    todo = [x for x in _form4_list(cik, refresh) if x[1] >= since and x[0] not in known]
    new = failed = 0
    for accn, filed, primary in todo[:max_new]:
        doc = raw_doc(primary)
        status = "empty"
        if doc:
            try:
                got = parse(sec.get_bytes(DOC_URL.format(cik=cik, folder=accn.replace("-", ""), doc=doc)))
                status = store(conn, cik, accn, filed, got)
                new += 1
            except Exception:  # noqa: BLE001  one bad filing never stops the rest; tried again next time
                failed += 1
                continue
        conn.execute("INSERT OR REPLACE INTO insider_filings VALUES (?,?,?,?,?)", (accn, cik, filed, status, db.now_iso()))
        conn.commit()
    return dict(new=new, left=max(0, len(todo) - max_new), failed=failed)


def store(conn, cik, accn, filed, got):
    rows = [(accn, t["line"], cik, filed, t["date"], got["owner"], got["owner_cik"], got["role"], t["code"], t["ad"],
             t["shares"], t["price"], t["owned_after"], t["direct"], 1 if got["plan"] else 0) for t in got["trades"]]
    conn.execute("DELETE FROM insider_trades WHERE accn=?", (accn,))
    conn.executemany("INSERT OR REPLACE INTO insider_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return "ok" if rows else "empty"


# ------------------------------------------------------------------------------------------ for the screen
def _group(rows, cik):
    """One line per filing, person and kind: a sale in 12 parts at different prices is one sale at the average."""
    out = {}
    for r in rows:
        k = (r["accn"], r["date"], r["code"], r["ad"], r["direct"])
        g = out.get(k)
        value = r["shares"] * r["price"] if r["price"] else None
        if g is None:
            out[k] = g = dict(accn=r["accn"], filed=r["filed"], date=r["date"], owner=r["owner"], role=r["role"],
                              code=r["code"], kind=CODES.get(r["code"], r["code"]), ad=r["ad"], shares=0.0, value=None,
                              owned_after=None, direct=r["direct"], plan=bool(r["plan"]), _n=0)
        g["shares"] += r["shares"]
        if value is not None:
            g["value"] = (g["value"] or 0) + value
        g["owned_after"] = r["owned_after"]          # the last line of the group
        g["_n"] += 1
    res = []
    for g in out.values():
        g["price"] = g["value"] / g["shares"] if g["value"] and g["shares"] else None
        before = None
        if g["owned_after"] is not None:
            before = g["owned_after"] - g["shares"] if g["ad"] == "A" else g["owned_after"] + g["shares"]
        g["change"] = (g["shares"] / before if g["ad"] == "A" else -g["shares"] / before) if before and before > 0 else None
        g["url"] = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{g['accn'].replace('-', '')}/"
        del g["_n"]
        res.append(g)
    res.sort(key=lambda g: (g["date"] or "", g["filed"] or ""), reverse=True)
    return res


def summary(conn, cik, today=None):
    """What the Ownership tab shows: every trade (grouped), totals of open-market buys and sales over 3 and 12
    months, and buys / sales per month (24 months)."""
    today = today or dt.date.today()
    cols = ("accn", "filed", "date", "owner", "role", "code", "ad", "shares", "price", "owned_after", "direct", "plan")
    rows = [dict(zip(cols, r)) for r in conn.execute(
        f"SELECT {', '.join(cols)} FROM insider_trades WHERE cik=? AND date >= ? ORDER BY date, accn, line",
        (int(cik), (today - dt.timedelta(days=365 * YEARS + 31)).isoformat()))]
    trades = _group(rows, cik)

    def total(days, code):
        start = (today - dt.timedelta(days=days)).isoformat()
        sel = [g for g in trades if g["code"] == code and (g["date"] or "") >= start]
        return dict(n=len(sel), people=len({g["owner"] for g in sel}), shares=sum(g["shares"] for g in sel),
                    value=sum(g["value"] or 0 for g in sel))
    months = {}
    for g in trades:
        if g["code"] in MARKET and g["date"]:
            m = months.setdefault(g["date"][:7], dict(month=g["date"][:7], buy=0.0, sell=0.0))
            m["buy" if g["code"] == "P" else "sell"] += g["value"] or 0
    first = (today.replace(day=1) - dt.timedelta(days=365 * YEARS)).strftime("%Y-%m")
    span, y, mth = [], int(first[:4]), int(first[5:])
    while f"{y:04d}-{mth:02d}" <= today.strftime("%Y-%m"):
        k = f"{y:04d}-{mth:02d}"
        span.append(months.get(k, dict(month=k, buy=0.0, sell=0.0)))
        y, mth = (y + 1, 1) if mth == 12 else (y, mth + 1)
    n_filings = conn.execute("SELECT COUNT(*) FROM insider_filings WHERE cik=?", (int(cik),)).fetchone()[0]
    return dict(trades=trades, buys_3m=total(91, "P"), sells_3m=total(91, "S"), buys_12m=total(365, "P"),
                sells_12m=total(365, "S"), months=span, filings=n_filings, years=YEARS)


def recent_buys(conn, cik, since, min_value=0.0):
    """Open-market buys of this company filed on or after `since` (for the alert), grouped per filing and person."""
    cols = ("accn", "filed", "date", "owner", "role", "code", "ad", "shares", "price", "owned_after", "direct", "plan")
    rows = [dict(zip(cols, r)) for r in conn.execute(
        f"SELECT {', '.join(cols)} FROM insider_trades WHERE cik=? AND code='P' AND filed >= ? ORDER BY date, accn, line", (int(cik), since))]
    out = {}
    for g in _group(rows, cik):
        k = (g["accn"], g["owner"])
        if k in out:
            o = out[k]
            o["shares"] += g["shares"]
            o["value"] = (o["value"] or 0) + (g["value"] or 0)
            o["date"] = max(o["date"] or "", g["date"] or "") or None
            o["owned_after"] = g["owned_after"] if g["direct"] == "D" else o["owned_after"]
        else:
            out[k] = dict(g)
    res = [g for g in out.values() if (g["value"] or 0) >= min_value]
    for g in res:
        g["price"] = g["value"] / g["shares"] if g["value"] and g["shares"] else None
    return sorted(res, key=lambda g: g["filed"] or "")
