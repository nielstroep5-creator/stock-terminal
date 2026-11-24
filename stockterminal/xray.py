"""ETF X-ray: look through your ETFs to the companies inside and add them to the shares you hold yourself,
so you see where your money really is.

- What an ETF holds comes from its SEC holdings report (N-PORT, see holdings.py). A European (UCITS) ETF
  does not report to the SEC, so the report of a US "twin" on the same index is used (allocation.TWINS:
  VWCE -> VT, CSPX -> VOO, ...). Close for the big names; small positions can differ a little.
- Reports are quarterly with a few weeks delay; kept a month in pf_fund_holdings.
- A company is matched to your own shares by ISIN, else by its cleaned-up name. Share classes of one
  company (Alphabet A and C) count as one company.
- An ETF that cannot be looked through stays one line ("not looked through"), never guessed.
"""
import json
import re
import time

from . import allocation, db, portfolio

FUND_DAYS = 30
TOP_ROWS = 150          # companies sent to the screen (the rest is summed as one line)
ETF_TOP = 25            # largest positions shown per ETF

_SUFFIX = re.compile(r"\b(incorporated|inc|corporation|corp|company|co|plc|nv|n\.v|sa|s\.a|se|ag|ab|asa|a/s|as|oyj|spa|"
                     r"s\.p\.a|ltd|limited|holdings?|group|the|class [a-z]|cl [a-z]|shs|ord|adr|reg|registered|"
                     r"[a-z]? ?shares?)\b\.?")


def key(name):
    """A company name reduced to what stays the same across sources: 'NVIDIA CORP' and 'NVIDIA' -> 'nvidia'."""
    n = (name or "").lower().replace("&", " and ")
    n = re.sub(r"[^a-z0-9/ .]", " ", n)
    n = _SUFFIX.sub(" ", n)
    return re.sub(r"[ .]+", " ", n).strip()


def nice(name):
    """'ALPHABET INC' -> 'Alphabet Inc' (reports are often in capitals)."""
    name = (name or "").strip()
    if name.isupper() and len(name) > 4:
        name = " ".join(w.capitalize() if len(w) > 3 or w in ("INC", "LTD", "CO", "THE", "COS") else w for w in name.split())
    return re.sub(r"\s+(class|cl)\s+[a-z]$", "", name, flags=re.I)


def _from_sec(conn, ticker):
    from . import holdings
    xml = holdings.latest_nport(conn, ticker)
    if not xml:
        return None, []
    as_of, _net, rows = holdings.parse_nport(xml)
    out = [[r["name"] or r["title"], r["isin"], r["weight"]] for r in rows
           if r["weight"] and r["weight"] > 0 and r["asset"] in (None, "EC", "EP")]
    return as_of, out


def fund_holdings(conn, ticker, refresh, notes):
    """(as_of, [[name, isin, weight], ...]) of a US ETF, from pf_fund_holdings or the SEC (a month old at most)."""
    row = conn.execute("SELECT as_of, rows, fetched FROM pf_fund_holdings WHERE symbol=?", (ticker,)).fetchone()
    month_ago = time.strftime("%Y-%m-%d", time.localtime(time.time() - FUND_DAYS * 86400))
    if row and (not refresh or row[2] > month_ago):
        return row[0], json.loads(row[1])
    if not refresh:
        return None, []
    try:
        as_of, rows = _from_sec(conn, ticker)
    except Exception as e:  # noqa: BLE001  offline: keep what we had
        notes.append(f"{ticker}: SEC holdings report not available ({e})")
        return (row[0], json.loads(row[1])) if row else (None, [])
    if not rows:
        return (row[0], json.loads(row[1])) if row else (None, [])
    conn.execute("INSERT OR REPLACE INTO pf_fund_holdings VALUES (?,?,?,?)",
                 (ticker, as_of, json.dumps(rows), time.strftime("%Y-%m-%d")))
    conn.commit()
    return as_of, rows


def _positions(conn, refresh):
    page = portfolio.build(live=refresh)
    positions = [p for p in page.get("positions") or [] if p.get("value")]
    if refresh:
        portfolio.resolve_symbols(conn, positions)
    else:
        from .quote import yahoo_symbol
        known = dict(conn.execute("SELECT t212_ticker, yahoo FROM pf_symbols"))
        for p in positions:
            p.setdefault("yahoo", yahoo_symbol(p["ticker"]) if p["ticker"] else known.get(p["t212_ticker"]))
    return page, positions


def build(refresh=False):
    conn = db.connect()
    try:
        page, positions = _positions(conn, refresh)
        if not positions:
            return dict(empty=True)
        total = sum(p["value"] for p in positions)
        notes = []

        companies = {}          # key -> {name, isin, direct, via{etf_id: w}, own}
        by_isin = {}            # isin -> company key (from ETF reports)
        etfs, blind = [], []

        def company(k, name, isin):
            c = companies.get(k)
            if not c:
                c = companies[k] = dict(name=nice(name), isin=isin, direct=0.0, via={}, t212_ticker=None)
            if isin:
                by_isin.setdefault(isin, k)
            return c

        # 1. the ETFs, looked through
        directs = []
        for p in positions:
            w = p["value"] / total
            info = portfolio._info(conn, p["yahoo"], refresh) if p.get("yahoo") else {}
            if (info.get("quoteType") or "").upper() not in ("ETF", "MUTUALFUND"):
                directs.append((p, w))
                continue
            etf_id = p["t212_ticker"]
            tw = allocation.twin(p["name"], p.get("ticker"))
            as_of, rows = fund_holdings(conn, tw, refresh, notes) if tw else (None, [])
            if not rows:
                blind.append(dict(id=etf_id, name=p["name"], weight=w))
                if not tw:
                    notes.append(f"{p['name']}: no US twin known for its index, shown as one line")
                continue
            tot_eq = sum(r[2] for r in rows)
            scale = 1 / tot_eq if tot_eq > 1 else 1.0          # never more than 100% of the ETF
            own_rows = {}
            for name, isin, weight in rows:
                k = by_isin.get(isin) or key(name) or (isin or name)
                c = company(k, name, isin)
                c["via"][etf_id] = c["via"].get(etf_id, 0) + w * weight * scale
                own_rows[k] = own_rows.get(k, 0) + weight * scale
            etfs.append(dict(id=etf_id, name=p["name"], weight=w, twin=tw, as_of=as_of, n=len(own_rows),
                             same=tw == p.get("ticker"), cash=max(0.0, 1 - sum(own_rows.values())), _rows=own_rows))

        # 2. your own shares, matched by ISIN, else by name
        for p, w in directs:
            k = by_isin.get(p.get("isin")) or key(p["name"]) or p["t212_ticker"]
            c = company(k, p["name"], p.get("isin"))
            c["name"] = p["name"] or c["name"]               # your broker's name reads best
            c["direct"] += w
            c["t212_ticker"] = p["t212_ticker"]

        for c in companies.values():
            c["total"] = c["direct"] + sum(c["via"].values())
        ranked = sorted(companies.items(), key=lambda kv: -kv[1]["total"])
        cash_in_etfs = sum(e["weight"] * e["cash"] for e in etfs)
        weights = [c["total"] for _, c in ranked]
        looked = sum(weights)
        eff = 1 / sum((x / looked) ** 2 for x in weights) if looked else None

        rows = [dict(key=k, name=c["name"], isin=c["isin"], total=c["total"], direct=c["direct"], via=c["via"],
                     t212_ticker=c["t212_ticker"]) for k, c in ranked[:TOP_ROWS]]
        rest = ranked[TOP_ROWS:]
        double = [dict(name=c["name"], direct=c["direct"], via=sum(c["via"].values()), total=c["total"],
                       t212_ticker=c["t212_ticker"]) for _, c in ranked if c["direct"] and c["via"]]
        hidden = [dict(name=c["name"], total=c["total"]) for _, c in ranked[:20] if not c["direct"]][:5]

        # 3. overlap between ETFs: the part of A that is also in B (sum of the smaller weight per company)
        overlap = []
        for i, a in enumerate(etfs):
            for b in etfs[i + 1:]:
                ov = sum(min(wa, b["_rows"].get(k, 0)) for k, wa in a["_rows"].items())
                overlap.append(dict(a=a["id"], b=b["id"], overlap=ov))

        # 4. per ETF: its largest positions, yours marked
        for e in etfs:
            top = sorted(e.pop("_rows").items(), key=lambda kv: -kv[1])[:ETF_TOP]
            e["top"] = [dict(name=companies[k]["name"], weight=w, own=bool(companies[k]["direct"]),
                             t212_ticker=companies[k]["t212_ticker"]) for k, w in top]

        return dict(empty=False, total=total, account_currency=page.get("account_currency"),
                    rows=rows, rest=dict(count=len(rest), weight=sum(c["total"] for _, c in rest)),
                    cash_in_etfs=cash_in_etfs, blind=blind, etfs=etfs, overlap=overlap,
                    stats=dict(companies=len(companies), effective=eff, top10=sum(weights[:10]),
                               direct=sum(w for _, w in directs), looked=looked),
                    double=double, hidden=hidden, notes=list(dict.fromkeys(notes)))
    finally:
        conn.close()
