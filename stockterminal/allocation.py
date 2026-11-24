"""Portfolio allocation: how your holdings split by sector, country / region and currency, next to the
FTSE All-World (the world index), so you see where you are over- or underweight.

- Shares: sector and country from Yahoo's profile (portfolio._info).
- ETFs are looked through to what they hold: sectors from Yahoo's fund data of the ETF itself; countries
  from the SEC holdings report (N-PORT, see holdings.py) of the ETF itself when it is a US fund, else of
  a US "twin" that follows the same index (TWINS: VWCE -> VT, an S&P 500 ETF -> VOO, ...). Nothing is
  added to your ETF list or the shared company list; results are kept a month in pf_fund.
- Currency: the currency you trade it in, or the underlying one (the currency of each company's country).
- What cannot be found is shown as "Unknown", never guessed.
"""
import json
import time

from . import db, portfolio

BENCH = dict(label="FTSE All-World", symbol="VWCE.DE", name="Vanguard FTSE All-World", twin="VT")
FUND_DAYS = 30

# an index in the ETF's name -> a US ETF on the same index (only its SEC report is read, for countries)
TWINS = [("all-world", "VT"), ("all world", "VT"), ("acwi", "ACWI"), ("all country", "ACWI"), ("msci world", "URTH"),
         ("s&p 500", "VOO"), ("s&p500", "VOO"), ("nasdaq 100", "QQQ"), ("nasdaq-100", "QQQ"), ("emerging", "IEMG"),
         ("stoxx europe 600", "VGK"), ("msci europe", "VGK"), ("developed europe", "VGK"), ("ftse developed", "VEA")]

YAHOO_SECTORS = {"technology": "Technology", "consumer cyclical": "Consumer cyclical", "communication services": "Communication",
                 "healthcare": "Healthcare", "financial services": "Financials", "industrials": "Industrials",
                 "consumer defensive": "Consumer defensive", "energy": "Energy", "utilities": "Utilities",
                 "real estate": "Real estate", "basic materials": "Materials"}

REGION = {}
for _region, _countries in {
    "North America": ["United States", "Canada", "Bermuda", "Puerto Rico"],
    "Europe": ["United Kingdom", "Ireland", "Netherlands", "Switzerland", "Germany", "France", "Sweden", "Denmark", "Norway",
               "Finland", "Spain", "Italy", "Belgium", "Luxembourg", "Austria", "Portugal", "Jersey", "Guernsey", "Isle of Man",
               "Poland", "Greece", "Czech Republic", "Hungary"],
    "Asia-Pacific (developed)": ["Japan", "Australia", "Hong Kong", "Singapore", "New Zealand", "South Korea", "Israel"],
    "Emerging markets": ["China", "Taiwan", "India", "Brazil", "Mexico", "South Africa", "Saudi Arabia", "Indonesia",
                         "Thailand", "Malaysia", "Philippines", "Chile", "Turkey", "United Arab Emirates", "Qatar", "Kuwait",
                         "Peru", "Colombia", "Argentina", "Uruguay", "Egypt", "Cayman Islands"],
}.items():
    for _c in _countries:
        REGION[_c] = _region

CURRENCY = {"United States": "USD", "Puerto Rico": "USD", "Canada": "CAD", "United Kingdom": "GBP", "Jersey": "GBP",
            "Guernsey": "GBP", "Isle of Man": "GBP", "Switzerland": "CHF", "Sweden": "SEK", "Denmark": "DKK", "Norway": "NOK",
            "Poland": "PLN", "Czech Republic": "CZK", "Hungary": "HUF", "Japan": "JPY", "Australia": "AUD", "Hong Kong": "HKD",
            "Singapore": "SGD", "New Zealand": "NZD", "South Korea": "KRW", "Israel": "ILS", "China": "CNY", "Taiwan": "TWD",
            "India": "INR", "Brazil": "BRL", "Mexico": "MXN", "South Africa": "ZAR", "Saudi Arabia": "SAR",
            "Uruguay": "UYU", "Argentina": "ARS", "Chile": "CLP", "Colombia": "COP", "Peru": "PEN", "Indonesia": "IDR",
            "Thailand": "THB", "Malaysia": "MYR", "Philippines": "PHP", "Turkey": "TRY", "United Arab Emirates": "AED",
            "Qatar": "QAR", "Kuwait": "KWD", "Egypt": "EGP", "Macau": "MOP", "Bermuda": "USD", "Cayman Islands": "USD"}
EURO = {"Ireland", "Netherlands", "Germany", "France", "Spain", "Italy", "Belgium", "Luxembourg", "Austria", "Portugal",
        "Finland", "Greece"}

UNKNOWN = "Unknown"
MORE_CODES = {"AT": "Austria", "PT": "Portugal", "NZ": "New Zealand", "PL": "Poland", "GR": "Greece", "SA": "Saudi Arabia",
              "ID": "Indonesia", "TH": "Thailand", "MY": "Malaysia", "PH": "Philippines", "CL": "Chile", "TR": "Turkey",
              "AE": "United Arab Emirates", "QA": "Qatar", "KW": "Kuwait", "PE": "Peru", "CO": "Colombia", "EG": "Egypt",
              "CZ": "Czech Republic", "HU": "Hungary", "IM": "Isle of Man", "VG": "British Virgin Islands", "MO": "Macau"}


def _sector(name):
    return YAHOO_SECTORS.get((name or "").strip().lower(), name) if name else None


def _currency(country):
    if country in EURO:
        return "EUR"
    return CURRENCY.get(country, "Other" if country and country != UNKNOWN else UNKNOWN)


def _norm(d):
    """Weights that add up to 1, with what is missing as Unknown."""
    d = {k: v for k, v in (d or {}).items() if v and v > 0}
    tot = sum(d.values())
    if not tot:
        return {}
    if tot < 0.98:
        d[UNKNOWN] = d.get(UNKNOWN, 0) + (1 - tot)
        tot = 1.0
    return {k: v / tot for k, v in d.items()}


def twin(name, us_ticker=None):
    """The US ETF whose SEC report gives this ETF's countries."""
    if us_ticker:
        return us_ticker
    n = (name or "").lower()
    return next((t for key, t in TWINS if key in n), None)


def _countries_from_sec(conn, ticker):
    from . import holdings
    xml = holdings.latest_nport(conn, ticker)
    if not xml:
        return {}
    out = {}
    for r in holdings.parse_nport(xml)[2]:
        if r["weight"] and (r["asset"] in (None, "EC", "EP")):
            c = holdings.COUNTRY_NAMES.get(r["country"]) or MORE_CODES.get(r["country"], r["country"] or UNKNOWN)
            out[c] = out.get(c, 0) + r["weight"]
    return out


def _yahoo_sectors(symbol):
    import yfinance as yf
    from .etf import SECTOR_NAMES
    fd = yf.Ticker(symbol).funds_data
    return {SECTOR_NAMES.get(k, k): float(v) for k, v in (fd.sector_weightings or {}).items() if v}


def fund(conn, symbol, name, us_ticker, refresh, notes):
    """(sectors, countries) of an ETF, from pf_fund or fetched (a month old at most)."""
    row = conn.execute("SELECT sectors, countries, source, fetched FROM pf_fund WHERE symbol=?", (symbol,)).fetchone()
    month_ago = time.strftime("%Y-%m-%d", time.localtime(time.time() - FUND_DAYS * 86400))
    if row and (not refresh or row[3] > month_ago):
        return json.loads(row[0] or "{}"), json.loads(row[1] or "{}"), row[2]
    if not refresh:
        return {}, {}, None
    tw = twin(name, us_ticker)
    sectors, countries, source = {}, {}, None
    try:
        sectors = _yahoo_sectors(symbol)
        if not sectors and tw and tw != symbol:
            sectors = _yahoo_sectors(tw)
    except Exception as e:  # noqa: BLE001
        notes.append(f"{name}: no sector data from Yahoo ({e})")
    if tw:
        try:
            countries = _countries_from_sec(conn, tw)
            source = f"SEC holdings report of {tw}" if countries else None
        except Exception as e:  # noqa: BLE001
            notes.append(f"{name}: countries not available ({e})")
    if not sectors and not countries:
        return (json.loads(row[0] or "{}"), json.loads(row[1] or "{}"), row[2]) if row else ({}, {}, None)
    conn.execute("INSERT OR REPLACE INTO pf_fund VALUES (?,?,?,?,?)",
                 (symbol, json.dumps(sectors), json.dumps(countries), source, time.strftime("%Y-%m-%d")))
    conn.commit()
    return sectors, countries, source


def _exposure(conn, p, info, refresh, notes):
    """{dimension: {bucket: weight within this holding}} for one holding."""
    is_fund = (info.get("quoteType") or "").upper() in ("ETF", "MUTUALFUND")
    trade_cur = portfolio._major(1, p["currency"])[1] or UNKNOWN
    if is_fund:
        sectors, countries, source = fund(conn, p["yahoo"], p["name"], p.get("ticker"), refresh, notes)
        sectors, countries = _norm(sectors) or {UNKNOWN: 1.0}, _norm(countries) or {UNKNOWN: 1.0}
        p["lookthrough"] = source or ("Yahoo fund data" if sectors.get(UNKNOWN) != 1.0 else None)
    else:
        sectors = {_sector(info.get("sector")) or UNKNOWN: 1.0}
        countries = {info.get("country") or UNKNOWN: 1.0}
    regions, under = {}, {}
    for c, w in countries.items():
        r = REGION.get(c, UNKNOWN if c == UNKNOWN else "Other")
        regions[r] = regions.get(r, 0) + w
        u = _currency(c)
        under[u] = under.get(u, 0) + w
    return dict(sector=sectors, country=countries, region=regions, currency=under, trade_currency={trade_cur: 1.0},
                kind={"ETF" if is_fund else "Shares": 1.0})


def build(refresh=False):
    page = portfolio.build(live=refresh)
    positions = [p for p in page.get("positions") or [] if p.get("value")]
    if not positions:
        return dict(empty=True)
    conn = db.connect()
    try:
        if refresh:
            portfolio.resolve_symbols(conn, positions)
        else:
            known = dict(conn.execute("SELECT t212_ticker, yahoo FROM pf_symbols"))
            from .quote import yahoo_symbol
            for p in positions:
                p.setdefault("yahoo", yahoo_symbol(p["ticker"]) if p["ticker"] else known.get(p["t212_ticker"]))
        notes = []
        total = sum(p["value"] for p in positions)
        dims = {}
        for p in positions:
            info = portfolio._info(conn, p["yahoo"], refresh) if p.get("yahoo") else {}
            ex = _exposure(conn, p, info, refresh, notes)
            w = p["value"] / total
            for dim, buckets in ex.items():
                for b, share in buckets.items():
                    cell = dims.setdefault(dim, {}).setdefault(b, dict(weight=0.0, holdings=[]))
                    cell["weight"] += w * share
                    cell["holdings"].append([p["name"], w * share])

        # the world index, looked through the same way
        bench = {}
        s, c, _ = fund(conn, BENCH["symbol"], BENCH["name"], BENCH["twin"], refresh, notes)
        if s or c:
            ex = _exposure(conn, dict(currency="EUR", yahoo=BENCH["symbol"], name=BENCH["name"], ticker=BENCH["twin"]),
                           {"quoteType": "ETF"}, False, notes)
            bench = {dim: v for dim, v in ex.items() if dim in ("sector", "country", "region", "currency")}

        out = {}
        for dim, buckets in dims.items():
            names = set(buckets) | set(bench.get(dim, {}))
            rows = []
            for n in names:
                cell = buckets.get(n, dict(weight=0.0, holdings=[]))
                top = sorted(cell["holdings"], key=lambda h: -h[1])[:6]
                rows.append(dict(name=n, weight=cell["weight"], bench=bench.get(dim, {}).get(n),
                                 holdings=[dict(name=h[0], weight=h[1]) for h in top]))
            rows.sort(key=lambda r: (r["name"] == UNKNOWN, -(r["weight"] or 0), -(r["bench"] or 0)))
            out[dim] = rows
        funds = [dict(name=p["name"], source=p.get("lookthrough")) for p in positions if "lookthrough" in p]
        return dict(empty=False, dims=out, bench_label=BENCH["label"] if bench else None, funds=funds,
                    notes=list(dict.fromkeys(notes)), total=total, account_currency=page.get("account_currency"))
    finally:
        conn.close()
