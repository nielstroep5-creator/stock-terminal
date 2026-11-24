"""Read each 10-K / 10-Q's own XBRL files for data that SEC companyfacts leaves out.

companyfacts only has company-wide numbers with standard (us-gaap/dei) tags. The filing's
own XBRL "instance" also contains:
  * dimensional facts: revenue / operating income by business segment, product and geography
  * company-specific "extension" tags, e.g. Microsoft's cash-flow line
    "Depreciation, amortization, and other" (msft:DepreciationAmortizationAndOther)

Files are cached gzip-compressed under data/raw/filings/<cik>/<accession>/ and each filing is
parsed only once (table parsed_filings), so re-runs are fast.
"""
import gzip
import json
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

from . import db, sec
from .config import RAW_DIR
from .mapping import ITEMS

PARSER_VERSION = 5     # 5: more company-specific capex lines (2026-11-17)

# Cash-flow statement lines that are depreciation / amortization. When a company shows more than
# one of them (e.g. "Depreciation and amortization" plus "Amortization of acquired intangible
# assets"), total D&A is their sum; no single standard tag holds it.
DA_TAGS = {"DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
           "DepreciationAmortizationAndAccretionNet", "Depreciation", "DepreciationNonproduction",
           "AmortizationOfIntangibleAssets", "OtherDepreciationAndAmortization",
           "CapitalizedComputerSoftwareAmortization1", "AdjustmentForAmortization"}
DA_LABEL = (r"^depreciation(,| and)? (depletion,? )?(and )?amortization"
            r"|^amortization (and [a-z\- ]+ )?of (acquired |purchased |finite-lived )?intangible")
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{name}"
FIRST_YEAR = "2009-01-01"

# Dimension that only says "this is an operating segment" - ignored when present.
NEUTRAL_DIMS = {("ConsolidationItemsAxis", "OperatingSegmentsMember")}

# Axes we keep dimensional facts for (matched on the axis local name).
AXES = {
    "StatementBusinessSegmentsAxis": "business_segment",
    "ProductOrServiceAxis": "product",
    "StatementGeographicalAxis": "geography",
    "SegmentGeographicalAxis": "geography",
}
# Per-share-class numbers (StatementClassOfStockAxis). Companies with several share classes whose
# shares are not worth the same (Berkshire: 1 Class A = 1500 Class B) only tag EPS and share counts
# per class, and companyfacts drops those. Stored as taxonomy "class", tag "<Concept>|<Member>".
CLASS_AXIS = "StatementClassOfStockAxis"
CLASS_CONCEPTS = {"EarningsPerShareBasic", "EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted",
                  "WeightedAverageNumberOfSharesOutstandingBasic",
                  "WeightedAverageNumberOfDilutedSharesOutstanding",
                  "CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding"}

STANDARD_NS = ("fasb.org", "xbrl.us", "xbrl.sec.gov", "xbrl.org", "w3.org", "ifrs.org")
XBRLI = "{http://www.xbrl.org/2003/instance}"
XBRLDI = "{http://xbrl.org/2006/xbrldi}"
LINK = "{http://www.xbrl.org/2003/linkbase}"
XLINK = "{http://www.w3.org/1999/xlink}"
LABEL_ROLE = "http://www.xbrl.org/2003/role/label"


# --------------------------------------------------------------------------- downloading
def _cached(cik, acc, name):
    path = RAW_DIR / "filings" / str(cik) / acc / (name + ".gz")
    if path.exists():
        return gzip.decompress(path.read_bytes())
    data = sec.get_bytes(ARCHIVE.format(cik=cik, acc=acc, name=name))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(data))
    return data


def filing_files(cik, acc):
    names = [i["name"] for i in json.loads(_cached(cik, acc, "index.json"))["directory"]["item"]]
    inst = [n for n in names if n.endswith("_htm.xml")] or \
           [n for n in names if re.fullmatch(r"[A-Za-z0-9]+-\d{8}\.xml", n)]
    lab = [n for n in names if n.endswith("_lab.xml")]
    pre = [n for n in names if n.endswith("_pre.xml")]
    xsd = [n for n in names if n.endswith(".xsd")]
    if not inst:
        return None
    # Newer filings embed label/presentation linkbases inside the .xsd schema file.
    return dict(instance=inst[0], lab=lab or xsd[:1], pre=pre or xsd[:1])


# --------------------------------------------------------------------------- parsing
def _qname_from_href(href):
    frag = href.split("#")[-1]
    return frag.replace("_", ":", 1)


TERSE_ROLE = "http://www.xbrl.org/2003/role/terseLabel"


def parse_labels(blobs):
    """concept qname -> {role: text} for every label the filing defines."""
    labels = {}
    for data in blobs:
        root = ET.fromstring(data)
        for link in root.iter(LINK + "labelLink"):
            locs = {l.get(XLINK + "label"): _qname_from_href(l.get(XLINK + "href"))
                    for l in link.iter(LINK + "loc")}
            res = {}
            for lab in link.iter(LINK + "label"):
                res.setdefault(lab.get(XLINK + "label"), []).append(
                    (lab.get(XLINK + "role"), (lab.text or "").strip()))
            for arc in link.iter(LINK + "labelArc"):
                q = locs.get(arc.get(XLINK + "from"))
                for role, text in res.get(arc.get(XLINK + "to"), []):
                    if q and text:
                        labels.setdefault(q, {})[role] = text
    return labels


def display_label(labels, q):
    """Terse label if the company defined one (usually the nicest), else the standard label."""
    roles = labels.get(q, {})
    return roles.get(TERSE_ROLE) or roles.get(LABEL_ROLE) or next(iter(roles.values()), "")


STATEMENT_ROLES = {"cashflow": r"cashflow",
                   "balance": r"balancesheet|financialposition|financialcondition",
                   "income": r"(income|operations|earnings)$|incomestatement"}
NOT_STATEMENT = r"parenthetical|detail|table|polic|disclosure|comprehensive"


def parse_statement_concepts(blobs, statement="cashflow"):
    """Concepts shown on one primary statement (from the presentation linkbase)."""
    pat = STATEMENT_ROLES[statement]
    for data in blobs:
        root = ET.fromstring(data)
        for link in root.iter(LINK + "presentationLink"):
            role = (link.get(XLINK + "role") or "").lower()
            if re.search(pat, role) and not re.search(NOT_STATEMENT, role):
                return {_qname_from_href(l.get(XLINK + "href")) for l in link.iter(LINK + "loc")}
    return set()



def parse_instance(data):
    prefixes, std_prefixes = {}, set()
    for event, (prefix, uri) in ET.iterparse(__import__("io").BytesIO(data), events=("start-ns",)):
        prefixes[uri] = prefix
        if any(s in uri for s in STANDARD_NS):
            std_prefixes.add(prefix)
    root = ET.fromstring(data)

    contexts = {}
    for ctx in root.iter(XBRLI + "context"):
        p = ctx.find(XBRLI + "period")
        inst = p.find(XBRLI + "instant")
        start = "" if inst is not None else p.find(XBRLI + "startDate").text.strip()
        end = (inst if inst is not None else p.find(XBRLI + "endDate")).text.strip()
        dims = [(m.get("dimension"), (m.text or "").strip()) for m in ctx.iter(XBRLDI + "explicitMember")]
        typed = any(True for _ in ctx.iter(XBRLDI + "typedMember"))
        contexts[ctx.get("id")] = (start, end, dims, typed)

    units = {}
    for u in root.iter(XBRLI + "unit"):
        meas = [m.text.split(":")[-1] for m in u.iter(XBRLI + "measure")]
        units[u.get("id")] = "/".join(meas) if u.find(XBRLI + "divide") is not None else meas[0]

    facts = []
    for el in root:
        if el.tag.endswith(("}DocumentFiscalYearFocus", "}DocumentFiscalPeriodFocus")) and el.text:
            facts.append(dict(concept="dei:" + el.tag.split("}")[1], local=el.tag.split("}")[1], extension=False,
                              ctx=contexts.get(el.get("contextRef"), ("", "", [], False)), unit="", value=None,
                              text=el.text.strip()))
            continue
        if el.tag.endswith("}TradingSymbol") and el.get("contextRef") in contexts and el.text:
            facts.append(dict(concept="dei:TradingSymbol", local="TradingSymbol", extension=False,
                              ctx=contexts[el.get("contextRef")], unit="pure", value=0.0,
                              text=el.text.strip()))
            continue
        ctx = el.get("contextRef")
        if ctx is None or el.text is None or ctx not in contexts:
            continue
        uri, local = el.tag[1:].split("}")
        try:
            value = float(el.text.strip())
        except ValueError:
            continue
        prefix = prefixes.get(uri, "")
        facts.append(dict(concept=f"{prefix}:{local}", local=local, extension=prefix not in std_prefixes,
                          ctx=contexts[ctx], unit=units.get(el.get("unitRef"), ""), value=value))
    return facts


def _readable(qname):
    local = qname.split(":")[-1]
    local = re.sub(r"(Segment)?Member$", "", local)
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", local).strip()


def _clean_label(text):
    return re.sub(r"\s*\[(Member|Axis|Domain)\]\s*$", "", text).strip()


# --------------------------------------------------------------------------- main
def process_filing(conn, cik, acc, form, filed, statement_patterns, store_cik=None):
    """Parse one filing. `cik` is the registrant that filed it (needed for the download address);
    rows are stored under `store_cik` when the filer is a predecessor of the company we track."""
    files = filing_files(cik, acc)
    file_cik, cik = cik, store_cik or cik
    if not files:
        return 0, 0, "no XBRL instance"
    facts = parse_instance(_cached(file_cik, acc, files["instance"]))
    labels = parse_labels([_cached(file_cik, acc, n) for n in files["lab"]])

    # 1) dimensional facts on the axes we care about (max 2 dimensions)
    seg_rows = []
    for f in facts:
        start, end, dims, typed = f["ctx"]
        dims = [(a, m) for a, m in dims
                if (a.split(":")[-1], m.split(":")[-1]) not in NEUTRAL_DIMS]
        if not dims or typed or len(dims) > 2:
            continue
        axes = [a.split(":")[-1] for a, _ in dims]
        if not all(a in AXES for a in axes):
            continue
        members = [m for _, m in dims]
        mlabels = [_clean_label(display_label(labels, m)) or _readable(m) for m in members]
        seg_rows.append((cik, acc, form, filed, f["concept"], "|".join(AXES[a] for a in axes),
                         "|".join(members), "|".join(mlabels), len(dims), f["value"], f["unit"],
                         start, end))
    conn.executemany(
        "INSERT OR REPLACE INTO segment_facts (cik, accn, form, filed, concept, breakdown, member, "
        "member_label, ndims, value, unit, start_date, end_date) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        seg_rows)

    # 2) company-specific tags on the cash-flow statement / balance sheet, matched to standard
    #    items by label (e.g. NVIDIA's balance-sheet line "Marketable securities")
    ext_rows = []
    pre = [_cached(file_cik, acc, n) for n in files["pre"]]
    cf = parse_statement_concepts(pre, "cashflow")
    on_statement = {"cashflow": cf, "balance": parse_statement_concepts(pre, "balance"),
                    "income": parse_statement_concepts(pre, "income")}
    matched = {}
    extension = {f["concept"] for f in facts if f["extension"]}
    for key, (stmt, pat) in statement_patterns.items():
        for q in sorted(on_statement.get(stmt, set()) & extension):
            if any(re.search(pat, t, re.I) for t in labels.get(q, {}).values()):
                matched[key] = q
                break
    by_concept = {q: k for k, q in matched.items()}
    for f in facts:
        start, end, dims, typed = f["ctx"]
        if dims or typed or not f["extension"] or f["concept"] not in by_concept:
            continue
        ext_rows.append((cik, "ext", by_concept[f["concept"]], f"{f['concept']} ({display_label(labels, f['concept'])})",
                         f["unit"], f["value"], start, end, None, None, form, filed, acc, None))
    # 3) total D&A when the cash-flow statement splits it over several lines
    da = sorted(q for q in cf if (q.split(":")[-1] in DA_TAGS and q not in extension) or
                (q in extension and any(re.search(DA_LABEL, t, re.I) for t in labels.get(q, {}).values())))
    if len(da) >= 2:
        per_ctx = {}
        for f in facts:
            start, end, dims, typed = f["ctx"]
            if not dims and not typed and start and f["concept"] in da:
                per_ctx.setdefault((start, end, f["unit"]), {})[f["concept"]] = f["value"]
        label = "sum of " + " + ".join(da)
        for (start, end, unit), vals in per_ctx.items():
            if len(vals) == len(da):
                ext_rows.append((cik, "ext", "d_and_a_sum", label, unit, sum(vals.values()), start, end,
                                 None, None, form, filed, acc, None))
    # 4) per-share-class EPS / share counts, and the trading symbol of each class
    for f in facts:
        start, end, dims, typed = f["ctx"]
        if typed or len(dims) != 1 or dims[0][0].split(":")[-1] != CLASS_AXIS:
            continue
        member = dims[0][1].split(":")[-1]
        if f["local"] == "TradingSymbol":
            ext_rows.append((cik, "class", f"TradingSymbol|{member}", f["text"], "pure", 0.0, "", end,
                             None, None, form, filed, acc, None))
        elif f["local"] in CLASS_CONCEPTS:
            ext_rows.append((cik, "class", f"{f['local']}|{member}", None, f["unit"], f["value"], start, end,
                             None, None, form, filed, acc, None))
    conn.executemany(
        "INSERT OR REPLACE INTO xbrl_facts (cik, taxonomy, tag, label, unit, value, start_date, end_date, "
        "fy, fp, form, filed, accn, frame) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ext_rows)
    # 5) the statements themselves, when the SEC's companyfacts has nothing from this filing
    n_std = _fill_standard_facts(conn, cik, acc, form, filed, facts)
    return len(seg_rows), len(ext_rows) + n_std, f"{n_std} statement facts from the filing" if n_std else ""


STANDARD_TAXONOMIES = {"us-gaap", "dei", "ifrs-full"}


def _dashed(acc):
    return f"{acc[:10]}-{acc[10:12]}-{acc[12:]}" if "-" not in acc and len(acc) == 18 else acc


def _has_statement_facts(conn, cik, acc):
    return conn.execute("SELECT 1 FROM xbrl_facts WHERE cik=? AND accn IN (?,?) AND taxonomy IN ('us-gaap','ifrs-full') "
                        "LIMIT 1", (cik, _dashed(acc), acc.replace("-", ""))).fetchone() is not None


def _fill_standard_facts(conn, cik, acc, form, filed, facts):
    """Facts of the financial statements, read from the filing's own XBRL file, for a 10-K / 10-Q the
    SEC's companyfacts has no numbers from. That happens with filings made together with a second
    registrant (utilities with their subsidiaries, REITs with their operating partnership, Citigroup
    with CGMHI since 2026): companyfacts can leave them out entirely, so the company's newest quarters
    were missing. Only facts without dimensions are taken: those belong to the parent company (the
    co-registrants' numbers carry a legal-entity dimension). If companyfacts includes the filing later,
    its rows replace these (same filing number, period and tag)."""
    if form not in ("10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "40-F") or _has_statement_facts(conn, cik, acc):
        return 0
    meta = {f["local"]: f.get("text") for f in facts if f["local"] in ("DocumentFiscalYearFocus", "DocumentFiscalPeriodFocus")}
    fy = int(meta["DocumentFiscalYearFocus"]) if (meta.get("DocumentFiscalYearFocus") or "").isdigit() else None
    fp = meta.get("DocumentFiscalPeriodFocus")
    rows = {}
    for f in facts:
        start, end, dims, typed = f["ctx"]
        tax = f["concept"].split(":")[0]
        if dims or typed or f["value"] is None or tax not in STANDARD_TAXONOMIES or f["extension"] or not end:
            continue
        rows[(tax, f["local"], f["unit"], start, end)] = (cik, tax, f["local"], None, f["unit"], f["value"], start, end,
                                                         fy, fp, form, filed, _dashed(acc), None)
    conn.executemany(
        "INSERT OR IGNORE INTO xbrl_facts (cik, taxonomy, tag, label, unit, value, start_date, end_date, "
        "fy, fp, form, filed, accn, frame) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", list(rows.values()))
    return len(rows)


def fill_missing(conn, ticker, cik, store_cik=None, since="2024-01-01"):
    """Repair: every 10-K / 10-Q since `since` that has no statement facts gets them from its own XBRL file
    (parsed before this repair existed, or companyfacts dropped it). Returns the number of filings filled."""
    todo = conn.execute(
        "SELECT accn, form, filing_date FROM filings WHERE cik=? AND form IN ('10-K','10-Q','10-K/A','10-Q/A','20-F','40-F') "
        "AND filing_date>=? ORDER BY filing_date", (cik, since)).fetchall()
    n = 0
    for accn, form, filed in todo:
        acc = accn.replace("-", "")
        if _has_statement_facts(conn, store_cik or cik, acc):
            continue
        try:
            files = filing_files(cik, acc)
            if not files:
                continue
            facts = parse_instance(_cached(cik, acc, files["instance"]))
            if _fill_standard_facts(conn, store_cik or cik, acc, form, filed, facts):
                n += 1
        except Exception as ex:  # noqa: BLE001 - one bad filing must not stop the rest
            db.log(conn, ticker, "sec_filing_facts", "error", None, f"{accn}: {ex}"[:300])
    conn.commit()
    if n:
        db.log(conn, ticker, "sec_filing_facts", "ok", n, "filings with statement facts read from the filing itself")
    return n


def _prefetch(cik, acc):
    """Download (into the cache) the files process_filing will read. Errors are left for later."""
    try:
        files = filing_files(cik, acc)
        for n in [files["instance"], *files["lab"], *files["pre"]] if files else []:
            _cached(cik, acc, n)
    except Exception:  # noqa: BLE001 - process_filing tries again and records the error
        pass


def load_filings(conn, ticker, cik, store_cik=None, since=None):
    """Download + parse every 10-K/10-Q (and a foreign company's 20-F / 40-F) since 2009 (or since `since`) that was not parsed yet.

    `store_cik`: store the results under this company instead (cik is then its predecessor).
    Files are downloaded by a few threads ahead of the parsing (the SEC rate limit is shared);
    parsing and database writes stay in this thread."""
    patterns = {i["key"]: (i["statement"], i["statement_label"]) for i in ITEMS if i.get("statement_label")}
    done = {r[0] for r in conn.execute(
        "SELECT accn FROM parsed_filings WHERE cik=? AND parser_version=?", (cik, PARSER_VERSION))}
    todo = conn.execute(
        "SELECT accn, form, filing_date FROM filings WHERE cik=? AND form IN ('10-K','10-Q','10-K/A','10-Q/A',"
        "'10-KT','20-F','20-F/A','40-F','40-F/A') AND filing_date>=? ORDER BY filing_date DESC", (cik, max(since or FIRST_YEAR, FIRST_YEAR))).fetchall()
    todo = [t for t in todo if t[0] not in done]      # newest first: the most useful data lands first
    n_seg = n_ext = 0
    pool = ThreadPoolExecutor(max_workers=4)
    futures = [pool.submit(_prefetch, cik, a.replace("-", "")) for a, _, _ in todo]
    for i, (accn, form, filed) in enumerate(todo, 1):
        acc = accn.replace("-", "")
        futures[i - 1].result()
        try:
            s, e, note = process_filing(conn, cik, acc, form, filed, patterns, store_cik)
        except Exception as ex:  # noqa: BLE001 - one bad filing must not stop the rest
            s, e, note = 0, 0, f"error: {ex}"[:300]
        n_seg += s
        n_ext += e
        conn.execute("INSERT OR REPLACE INTO parsed_filings VALUES (?,?,?,?,?,?,?)",
                     (cik, accn, PARSER_VERSION, s, e, note, db.now_iso()))
        conn.commit()
        if i % 10 == 0:
            print(f"  filings parsed: {i}/{len(todo)}")
    pool.shutdown()
    fill_missing(conn, ticker, cik, store_cik)       # filings parsed earlier whose numbers companyfacts left out
    db.log(conn, ticker, "sec_filings", "ok", n_seg, f"{len(todo)} filings, {n_ext} extension facts")
    return len(todo), n_seg, n_ext
