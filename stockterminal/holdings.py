"""Full ETF holdings.

Sources, all free:
- SEC N-PORT: every US ETF reports all its holdings (shares, value, weight, country, asset type) to the SEC.
  Public with a delay: the newest report is usually 2 to 5 months old. Works for any fund.
- State Street (SPDR funds such as SPY, DIA, XLK): a daily holdings file, used instead when available.
N-PORT has no tickers, only CUSIP codes; OpenFIGI (free, no key) turns them into tickers. Answers are
cached in the cusip_map table, so each security is looked up only once.

On the page the weights of an N-PORT list are re-priced with today's prices (shares x price), so they
stay close to the fund's real weights between reports.

    python -m stockterminal.holdings SPY QQQ DIA
"""
import io
import json
import re
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

from . import db, sec
from .config import RAW_DIR

MF_URL = "https://www.sec.gov/files/company_tickers_mf.json"
FEED_URL = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={id}&type=NPORT-P"
            "&dateb=&owner=include&count=5&output=atom")
SSGA_URL = "https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/holdings-daily-us-en-{t}.xlsx"
FIGI_URL = "https://api.openfigi.com/v3/mapping"
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
MAX_AGE_DAYS = 7

ASSET_NAMES = {"EC": "Stocks", "EP": "Preferred stock", "DBT": "Bonds", "STIV": "Cash & money market",
               "ABS-MBS": "Mortgage bonds", "ABS-O": "Asset-backed", "ABS-CBDO": "Asset-backed", "LON": "Loans",
               "RE": "Real estate", "COMM": "Commodities", "DE": "Derivatives", "DIR": "Derivatives",
               "DFE": "Derivatives", "DCR": "Derivatives", "DCO": "Derivatives", "DO": "Derivatives"}
COUNTRY_NAMES = {"US": "United States", "GB": "United Kingdom", "IE": "Ireland", "NL": "Netherlands", "CA": "Canada",
                 "CH": "Switzerland", "JE": "Jersey", "BM": "Bermuda", "KY": "Cayman Islands", "LU": "Luxembourg",
                 "IL": "Israel", "DE": "Germany", "FR": "France", "JP": "Japan", "CN": "China", "TW": "Taiwan",
                 "KR": "South Korea", "IN": "India", "AU": "Australia", "SG": "Singapore", "HK": "Hong Kong",
                 "BR": "Brazil", "MX": "Mexico", "SE": "Sweden", "DK": "Denmark", "NO": "Norway", "FI": "Finland",
                 "ES": "Spain", "IT": "Italy", "BE": "Belgium", "PR": "Puerto Rico", "UY": "Uruguay", "AR": "Argentina",
                 "PA": "Panama", "LR": "Liberia", "CW": "Curacao", "GG": "Guernsey", "ZA": "South Africa"}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- SEC N-PORT
def fund_ids(conn, ticker):
    """(cik, series id or None) of an ETF at the SEC. The fund ticker list covers most ETFs;
    some old trusts (SPY, DIA) are only in the company list and have no series."""
    path = RAW_DIR / "company_tickers_mf.json"
    data, _ = sec._cached_json(MF_URL, path, 24 * 7)
    for cik, series, _cls, sym in data.get("data", []):
        if sym and sym.upper() == ticker:
            return int(cik), series
    r = conn.execute("SELECT cik FROM tickers WHERE ticker=?", (ticker,)).fetchone()
    return (int(r[0]), None) if r else (None, None)


def latest_nport(conn, ticker):
    """Raw XML of the newest N-PORT report of the fund, or None. Reports never change: cached on disk."""
    cik, series = fund_ids(conn, ticker)
    if not cik:
        return None
    feed = sec.get_bytes(FEED_URL.format(id=series or f"{cik:010d}")).decode("latin-1")
    m = re.search(r"<filing-href>https://www\.sec\.gov/Archives/edgar/data/(\d+)/(\d+)/", feed)
    if not m:
        return None
    folder = RAW_DIR / "nport"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{m.group(2)}.xml"
    if not path.exists():
        path.write_bytes(sec.get_bytes(f"https://www.sec.gov/Archives/edgar/data/{m.group(1)}/{m.group(2)}/primary_doc.xml"))
    return path.read_bytes()


def parse_nport(xml):
    root = ET.fromstring(xml)
    for el in root.iter():                 # drop the namespaces: plain tag names are easier to read
        el.tag = el.tag.split("}", 1)[-1]
    gen = root.find(".//genInfo")
    as_of = gen.findtext("repPdDate")
    net = _num(root.findtext(".//fundInfo/netAssets"))
    rows = []
    for s in root.iter("invstOrSec"):
        if (s.findtext("payoffProfile") or "Long") != "Long":
            continue
        ids = s.find("identifiers")
        isin = ids.find("isin").get("value") if ids is not None and ids.find("isin") is not None else None
        tick = ids.find("ticker").get("value") if ids is not None and ids.find("ticker") is not None else None
        rows.append(dict(name=s.findtext("name"), title=s.findtext("title"), cusip=(s.findtext("cusip") or "").strip(),
                         isin=isin, ticker=tick, shares=_num(s.findtext("balance")), units=s.findtext("units"),
                         value=_num(s.findtext("valUSD")), weight=(_num(s.findtext("pctVal")) or 0) / 100,
                         asset=s.findtext("assetCat") or (s.find("assetConditional").get("assetCat")
                                                          if s.find("assetConditional") is not None else None),
                         country=s.findtext("invCountry")))
    return as_of, net, rows


# --------------------------------------------------------------------------- CUSIP -> ticker (OpenFIGI)
_last_figi = [0.0]


def _figi(jobs):
    """One OpenFIGI request (max 10 jobs). Free use allows 25 requests a minute."""
    wait = 2.5 - (time.time() - _last_figi[0])
    if wait > 0:
        time.sleep(wait)
    for attempt in range(3):
        _last_figi[0] = time.time()
        r = requests.post(FIGI_URL, json=jobs, timeout=30)
        if r.status_code == 429:
            time.sleep(int(r.headers.get("ratelimit-reset", 20)) + 1)
            continue
        r.raise_for_status()
        return r.json()
    return [{} for _ in jobs]


def map_cusips(conn, rows):
    """Fill row["ticker"] for US stocks / funds from CUSIP (cached; asks OpenFIGI for new ones)."""
    want = {r["cusip"]: r for r in rows if r["cusip"] and len(r["cusip"]) == 9 and r["cusip"] != "000000000"}
    known = {}
    for i in range(0, len(want), 500):
        chunk = list(want)[i:i + 500]
        known.update({c: t for c, t in conn.execute(
            f"SELECT cusip, ticker FROM cusip_map WHERE cusip IN ({','.join('?' * len(chunk))})", chunk)})
    new = [c for c in want if c not in known]
    if new:
        print(f"  looking up {len(new)} tickers (OpenFIGI, about {len(new) // 250 + 1} min the first time)")
    for i in range(0, len(new), 10):
        part = new[i:i + 10]
        try:
            res = _figi([{"idType": "ID_CUSIP", "idValue": c, "exchCode": "US"} for c in part])
            # not found by CUSIP (often foreign companies listed in the US): try the ISIN
            miss = [c for c, x in zip(part, res) if not x.get("data") and want[c].get("isin")]
            if miss:
                res2 = _figi([{"idType": "ID_ISIN", "idValue": want[c]["isin"], "exchCode": "US"} for c in miss])
                fix = dict(zip(miss, res2))
                res = [fix.get(c, x) for c, x in zip(part, res)]
        except Exception as e:
            print(f"  ! ticker lookup failed: {e}")
            break
        for c, x in zip(part, res):
            d = (x.get("data") or [{}])[0]
            t = (d.get("ticker") or "").replace("/", ".") or None
            known[c] = t
            conn.execute("INSERT OR REPLACE INTO cusip_map (cusip, ticker, name, checked_at) VALUES (?,?,?,?)",
                         (c, t, d.get("name"), _now()))
        conn.commit()
    for c, r in want.items():
        if not r.get("ticker") and known.get(c):
            r["ticker"] = known[c]


# --------------------------------------------------------------------------- State Street daily file
def _xlsx_rows(content):
    """Rows of the first sheet of an .xlsx as lists of strings (small reader, no extra package)."""
    z = zipfile.ZipFile(io.BytesIO(content))
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        shared = ["".join(t.text or "" for t in si.iter(ns + "t"))
                  for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(ns + "si")]
    out = []
    for row in ET.fromstring(z.read("xl/worksheets/sheet1.xml")).iter(ns + "row"):
        vals = {}
        for c in row.findall(ns + "c"):
            v = c.find(ns + "v")
            val = v.text if v is not None else (c.findtext(f"{ns}is/{ns}t") or "")
            if c.get("t") == "s" and val:
                val = shared[int(val)]
            idx = 0
            for ch in re.match(r"[A-Z]+", c.get("r")).group(0):
                idx = idx * 26 + ord(ch) - 64
            vals[idx - 1] = val
        out.append([vals.get(i, "") for i in range(max(vals) + 1)] if vals else [])
    return out


def ssga(ticker):
    """(as_of, rows) from State Street's daily file, or None when the fund is not theirs."""
    try:
        r = requests.get(SSGA_URL.format(t=ticker.lower()), headers={"User-Agent": BROWSER_UA}, timeout=30)
    except requests.RequestException:
        return None
    if r.status_code != 200 or not r.content.startswith(b"PK"):
        return None
    rows = _xlsx_rows(r.content)
    as_of, head, out = None, None, []
    for row in rows:
        if row and row[0] == "Holdings:" and len(row) > 1:
            try:
                as_of = datetime.strptime(row[1].replace("As of ", "").strip(), "%d-%b-%Y").strftime("%Y-%m-%d")
            except ValueError:
                pass
        elif row and row[0] == "Name" and "Weight" in row:
            head = {k: i for i, k in enumerate(row)}
        elif head and row and row[0]:
            g = lambda k: row[head[k]] if k in head and head[k] < len(row) else None
            w = _num(g("Weight"))
            if w is None:
                if out:
                    break                     # end of the table
                continue
            t = g("Ticker")
            out.append(dict(name=row[0], ticker=None if not t or t == "-" else t.replace("/", ".").upper(),
                            cusip=g("Identifier"), shares=_num(g("Shares Held")), weight=w / 100,
                            asset="STIV" if (t in (None, "-") and "DOLLAR" in row[0].upper()) else "EC"))
    return (as_of, out) if out else None


# --------------------------------------------------------------------------- load / store
def load(conn, ticker):
    """Download and store the full holdings of one ETF. Returns the number of holdings."""
    t = ticker.upper()
    nport = None
    try:
        xml = latest_nport(conn, t)
        nport = parse_nport(xml) if xml else None
    except Exception as e:
        print(f"  ! SEC holdings report not available: {e}")
    daily = ssga(t)
    if daily:
        as_of, rows, source = daily[0], daily[1], "ssga"
    elif nport:
        as_of, _net, rows = nport
        rows = [r for r in rows if r["weight"]]
        map_cusips(conn, rows)
        source = "sec"
    else:
        print("  ! no full holdings list found (only the top 10 from Yahoo)")
        return 0
    # countries and asset types come from the SEC report (the daily file has neither)
    countries, assets = {}, {}
    if nport:
        for r in nport[2]:
            if r["weight"]:
                c = COUNTRY_NAMES.get(r["country"], r["country"] or "Other")
                countries[c] = countries.get(c, 0) + r["weight"]
                a = ASSET_NAMES.get(r["asset"], "Other")
                assets[a] = assets.get(a, 0) + r["weight"]
        rest = 1 - sum(assets.values())
        if rest > 0.0005:
            assets["Cash & other"] = assets.get("Cash & other", 0) + rest
        by_cusip = {r["cusip"]: r["country"] for r in nport[2]}
        for r in rows:
            r.setdefault("country", by_cusip.get(r.get("cusip")))
    rows = [r for r in rows if (r["weight"] or 0) >= 1e-6]      # drop placeholder lines (e.g. "CONTRA ...")
    rows.sort(key=lambda r: -(r["weight"] or 0))
    conn.execute("DELETE FROM etf_holdings WHERE etf=?", (t,))
    conn.executemany(
        "INSERT INTO etf_holdings (etf, rank, ticker, name, cusip, shares, value, weight, asset, country) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(t, i + 1, r.get("ticker"), r.get("title") or r["name"], r.get("cusip"), r.get("shares"), r.get("value"),
          r["weight"], r.get("asset"), r.get("country")) for i, r in enumerate(rows)])
    conn.execute("UPDATE etfs SET holdings_as_of=?, holdings_source=?, holdings_count=?, countries=?, "
                 "asset_mix=?, holdings_checked=? WHERE ticker=?",
                 (as_of, source, len(rows), json.dumps(countries) if countries else None,
                  json.dumps(assets) if assets else None, _now(), t))
    conn.commit()
    db.log(conn, t, "etf_holdings", "ok", len(rows), f"{source} as of {as_of}")
    print(f"  holdings: {len(rows)} ({'State Street daily file' if source == 'ssga' else 'SEC report'}, as of {as_of})")
    return len(rows)


def due(conn, ticker):
    r = conn.execute("SELECT holdings_checked, holdings_source FROM etfs WHERE ticker=?", (ticker,)).fetchone()
    if not r or not r[0]:
        return True
    age = (datetime.now(timezone.utc) - datetime.strptime(r[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).days
    return age >= (1 if r[1] == "ssga" else MAX_AGE_DAYS)     # daily files daily, SEC reports weekly


def main(argv=None):
    conn = db.connect()
    for t in [a.upper() for a in (sys.argv[1:] if argv is None else argv)]:
        print(f"== {t} ==")
        load(conn, t)
    conn.close()


if __name__ == "__main__":
    main()
