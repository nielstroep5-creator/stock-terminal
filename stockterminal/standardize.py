"""Turn raw XBRL facts into clean, comparable financial statements.

Usage:
    python -m stockterminal.standardize AAPL MSFT

For each company this:
  1. Works out the fiscal calendar (fiscal years and their quarter end dates).
  2. For every standard item in mapping.py, finds the value for every period using the
     first candidate tag that has one (so tag changes over the years are handled).
  3. Turns year-to-date figures into standalone quarters and derives Q4 = FY - 9 months.
  4. Fills gaps with fallback formulas, and computes always-derived items (FCF, EBITDA, debt).
  5. Adjusts share counts and per-share values for stock splits.
  6. Builds trailing-twelve-month (TTM) values.
  7. Writes everything to the `financials` table, then metrics and daily valuation.

Restatements: `value` is the latest filed number, `original_value` the first one reported.
"""
import math
import re
import sys
from dataclasses import dataclass, replace
from statistics import mode

import pandas as pd

from . import db
from .mapping import ITEMS, ITEM_BY_KEY
from .mapping import BANK_MARKERS, INDUSTRY_SIC, NOT_FOR_INSURERS
BANK_ITEMS = [i["key"] for i in ITEMS if i["bank"]]
INSURER_ITEMS = [i["key"] for i in ITEMS if i["insurer"]]
REIT_ITEMS = [i["key"] for i in ITEMS if i["reit"]]


def industry_of(conn, cik):
    """'insurer' / 'reit' from the SEC industry code, else None (banks are recognised from their data)."""
    row = conn.execute("SELECT sic FROM companies WHERE cik=?", (cik,)).fetchone()
    sic = str(row[0] or "") if row else ""
    return next((k for k, pat in INDUSTRY_SIC.items() if re.match(pat, sic)), None)

FORMS = ("10-K", "10-K/A", "10-KT", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A")
TOL = pd.Timedelta(days=5)          # tolerance when matching period start dates
DAY = pd.Timedelta(days=1)
MONEY_KINDS = ("flow", "instant")
ADDITIVE = ("flow", "per_share")    # quarters add up to the year


@dataclass
class Rec:
    value: float
    orig: float            # first reported value
    filed: str             # filing date of `value`
    orig_filed: str        # filing date of `orig` (= when it became public)
    accn: str | None
    source: str            # tag or formula
    method: str            # reported / ytd_diff / fy_minus_ytd / fy_minus_q / derived / ttm
    versions: tuple = ()   # every filed version: (filed, value, accn), oldest first
    parts: tuple = ()      # for computed values: the records it was computed from ...
    fn: object = None      # ... and the function that combines them


def version_at(rec, filed, after=False):
    """The version of `rec` filed on/before `filed` (or the first on/after it if after=True).

    Computed records (Q4 = FY - 9 months, formulas, TTM) are re-computed from the versions of
    their parts, so "what was known on that date" also works for them.
    """
    if not rec.versions:
        if rec.parts and rec.fn is not None:
            try:
                v = rec.fn(*[version_at(p, filed, after).value for p in rec.parts])
            except ZeroDivisionError:
                return rec
            return replace(rec, value=v) if math.isfinite(v) else rec
        return rec
    if after:
        vs = [v for v in rec.versions if v[0] >= filed]
        v = vs[0] if vs else rec.versions[-1]
    else:
        vs = [v for v in rec.versions if v[0] <= filed]
        v = vs[-1] if vs else rec.versions[0]
    return replace(rec, value=v[1], filed=v[0], accn=v[2])


def _stable_since(rec):
    """Filing date from which `rec` has had its current value (later filings only repeated it)."""
    since = rec.filed
    for filed, val, _ in reversed(rec.versions):
        if abs(val - rec.value) > 1e-9 * max(abs(val), abs(rec.value), 1.0):
            break
        since = filed
    return since


def _combine(recs, fn, source, method):
    return Rec(fn(*[r.value for r in recs]), fn(*[r.orig for r in recs]),
               max(r.filed for r in recs), max(r.orig_filed for r in recs),
               recs[0].accn, source, method, parts=tuple(recs), fn=fn)


# --------------------------------------------------------------------------- loading
def load_facts(conn, cik):
    q = (f"SELECT taxonomy, tag, label, unit, value, start_date, end_date, fy, fp, form, filed, accn "
         f"FROM xbrl_facts WHERE cik=? AND form IN ({','.join('?' * len(FORMS))})")
    df = pd.read_sql_query(q, conn, params=(cik, *FORMS))
    # facts read from the filings themselves carry the accession number without dashes:
    # use one format so "reported in the same filing" also works across both sources
    df["accn"] = df.accn.str.replace(r"^(\d{10})(\d{2})(\d{6})$", r"\1-\2-\3", regex=True)
    df["end"] = pd.to_datetime(df.end_date)
    df["start"] = pd.to_datetime(df.start_date, errors="coerce")   # '' -> NaT for instants
    df["days"] = (df.end - df.start).dt.days
    return df


def load_splits(conn, ticker):
    return [(r[0], r[1]) for r in
            conn.execute("SELECT date, ratio FROM splits WHERE ticker=? AND ratio>0", (ticker,))]


def currency_of(df):
    money = df[~df.unit.str.contains("/") & ~df.unit.isin(["shares", "pure"])]
    return money.unit.mode().iloc[0] if len(money) else "USD"


# --------------------------------------------------------------------------- fiscal calendar
def build_calendar(df):
    """List of fiscal years: dict(fy, start, end, q=[q1_end, q2_end, q3_end])."""
    d = df[df.start.notna()]
    annual = d[d.days.between(350, 380) & d.form.str.match(r"^(10-K|20-F|40-F)")]
    cnt = annual.groupby(["start", "end"]).size().reset_index(name="n")
    cnt = cnt[cnt.n >= 5]
    best = cnt.sort_values("n").groupby("end").tail(1).sort_values("end")

    # a year that is some 10-K's OWN (latest) period, not only a comparison column: after a change of
    # fiscal year (Ferguson 2025: August-July -> calendar years) later reports show the old years
    # recast to the new calendar; those overlap the real years and must not replace them
    own_k = annual[annual.fp == "FY"]
    own_ends = set(own_k[own_k.end == own_k.groupby("accn").end.transform("max")].end)
    years = []
    for r in best.itertuples():
        if years and (r.end - years[-1]["end"]).days < 300:      # overlapping candidate
            mine, theirs = r.end in own_ends, years[-1]["end"] in own_ends
            if (mine and not theirs) or (mine == theirs and r.n > years[-1]["n"]):
                years[-1] = dict(start=r.start, end=r.end, n=r.n)
            continue
        years.append(dict(start=r.start, end=r.end, n=r.n))

    # A year whose 10-K has no full-year period (a merger splits it in two: Paramount Skydance 2025 =
    # Jan-Aug predecessor + Aug-Dec successor): it still is a fiscal year when a 10-K has a balance
    # sheet one year after the previous year end. Without it the calendar stops and no later quarter
    # is placed (Skydance would keep its 2025 share count forever).
    kb = df[df.start.isna() & df.form.str.match(r"^(10-K|20-F|40-F)")]
    k_dates = sorted(set(kb[pd.to_datetime(kb.filed) > kb.end].end))     # only dates already past when filed

    def _next_year_end(prev_end):
        want = prev_end + pd.DateOffset(years=1)
        near = [e for e in k_dates if abs((e - want).days) <= 10]
        return min(near, key=lambda e: abs((e - want).days)) if near else None

    filled = []
    for i, y in enumerate(years):
        filled.append(y)
        nxt = years[i + 1]["end"] if i + 1 < len(years) else None
        e = _next_year_end(y["end"])
        while e is not None and (nxt is None or (nxt - e).days >= 300):
            filled.append(dict(start=filled[-1]["end"] + DAY, end=e, n=0))
            e = _next_year_end(e)
    years = filled

    # Fiscal-year label: SEC's `fy` field on a 10-K's own period tells us the company's
    # naming convention (e.g. MSFT year ending June 2026 = FY2026). Use the most common offset.
    # Only a 10-K's OWN year counts: the earlier years shown next to it carry the same `fy` value
    # (a new company's first 10-K, FedEx Freight 2026, shows three years all labelled 2026)
    own = annual[annual.fp == "FY"]
    own = own[own.end == own.groupby("accn").end.transform("max")]
    label_src = own.groupby("end").fy.min()
    offsets = [int(label_src[y["end"]]) - (y["end"] - 14 * DAY).year
               for y in years if y["end"] in label_src.index and pd.notna(label_src[y["end"]])]
    offset = mode(offsets) if offsets else 0

    years = _with_transitions(d, years)

    # Year still in progress (has 10-Qs but no 10-K yet).
    if years and years[-1]["end"] is not None:
        years.append(dict(start=years[-1]["end"] + DAY, end=None, n=0))

    prev_fy = None
    for y in years:
        S = y["start"]
        if y.get("transition"):
            # a transition period keeps the label of the year before it; its quarters get numbers 5, 6, ...
            y["fy"] = prev_fy if prev_fy is not None else (S - 14 * DAY).year + offset
            y["q"] = [None, None, None]
            continue
        E = y["end"] if y["end"] is not None else S + pd.Timedelta(days=371)
        y["fy"] = (E - 14 * DAY).year + offset
        c = d[(d.end > S) & (d.end < E - 20 * DAY) &
              (((d.start - S).abs() <= TOL) | d.days.between(80, 100))]
        k = (c.end - S).dt.days
        y["q"] = []
        # 13-week quarters end ~91/182/273 days into the year; 52/53-week retailers with
        # 12/12/12/16-week quarters (Costco) end at ~84/167/251 days; Kroger's 16/12/12/12-week
        # quarters at ~112/196/280 days
        for lo, hi in ((80, 120), (160, 200), (245, 290)):
            ends = c.end[k.between(lo, hi)]
            y["q"].append(ends.value_counts().idxmax() if len(ends) else None)
        # a quarter end missing before a known one in the year in progress (a spin-off whose first 10-Q
        # is Q2): the start of that later quarter's 3-month period gives it
        for i in ((1, 2) if y["end"] is None else ()):
            if y["q"][i - 1] is None and y["q"][i] is not None:
                st = c.start[(c.end == y["q"][i]) & c.days.between(80, 100)]
                if len(st):
                    y["q"][i - 1] = st.value_counts().idxmax() - DAY
        prev_fy = y["fy"]
    # the year in progress counts once it has any quarter (a spin-off's first 10-Q can be its Q2:
    # Honeywell Aerospace 2026)
    return [y for y in years if y["end"] is not None or any(q is not None for q in y["q"]) or y.get("tq")]


def _with_transitions(d, years):
    """Insert transition periods: the months between two fiscal years when a company changes its fiscal
    year (Ferguson: August-July until July 2025, then a 5-month transition period August-December 2025
    in a 10-KT, then calendar years; FedEx: June-May until May 2026, then June-December 2026).
    A transition period is not a year (no FY row, so a 5-month total never shows as a full year); its
    3-month quarters are kept, as quarters 5, 6, ... of the year before it."""
    if not years:
        return years
    out = []
    for i, y in enumerate(years):
        out.append(y)
        if i + 1 < len(years) and 25 < (years[i + 1]["start"] - y["end"]).days < 340:
            out.append(dict(start=y["end"] + DAY, end=years[i + 1]["start"] - DAY, n=0, transition=True))
    S = years[-1]["end"] + DAY
    short = d[((d.start - S).abs() <= TOL) & d.days.between(25, 340)]
    # a finished transition period: a 10-K(T) for a period shorter than a year, starting after the last year
    kt = short[short.form.str.match(r"^10-K")]
    kt = kt.groupby("end").size()
    if len(kt) and kt.max() >= 5:
        out.append(dict(start=S, end=kt.idxmax(), n=0, transition=True))
    else:
        # one in progress: a 10-Q period from the last year end that is no quarter of a normal year
        # (FedEx: June 2026 alone, and June-September = 4 months)
        q = short[short.form.str.match(r"^10-Q")]
        # (normal: 13-week quarters, 12/12/12/16 and 16/12/12/12-week quarters of 52-week years)
        q = q[~(q.days.between(80, 125) | q.days.between(160, 200) | q.days.between(245, 290))]
        if len(q) >= 5:
            out.append(dict(start=S, end=None, n=0, transition=True))
    for y in out:
        if y.get("transition"):
            E = y["end"] if y["end"] is not None else y["start"] + pd.Timedelta(days=400)
            c = d[(d.end > y["start"]) & (d.end <= E) & d.days.between(80, 100)]
            ends = c.groupby("end").size()
            tq, last = [], y["start"] - DAY
            for e in sorted(ends[ends >= 5].index):
                st = c.start[c.end == e].value_counts().idxmax()
                if st > last:
                    tq.append((st, e))
                    last = e
            y["tq"] = tq
    return out


def pkey(y, key):
    """Result key (ptype, fiscal year, fiscal quarter) of one period of year `y`."""
    if key == "FY":
        return ("FY", y["fy"], 0)
    return ("Q", y["fy"], int(key[1:]) + (4 if y.get("transition") else 0))


def periods_of(y):
    """Period definitions for one fiscal year: key -> (start, end)."""
    if y.get("transition"):
        return {f"Q{k + 1}": se for k, se in enumerate(y.get("tq") or [])}
    S, E, (q1, q2, q3) = y["start"], y["end"], y["q"]
    p = {}
    if E is not None:
        p["FY"] = (S, E)
    bounds = [S, q1, q2, q3, E]
    for k in range(1, 5):
        prev, end = bounds[k - 1], bounds[k]
        if end is None or (k > 1 and prev is None):
            continue
        p[f"Q{k}"] = (S if k == 1 else prev + DAY, end)
    return p


# --------------------------------------------------------------------------- tag series
class Series:
    """All values of one tag (or 'A + B' sum), indexed by end date."""

    def __init__(self, by_end, loose=False):
        self.by_end = by_end      # end -> list of (start, Rec)
        self.loose = loose        # dei cover-page values: dated after period end

    def find(self, start, end):
        if self.loose and start is None:       # nearest cover date within 75 days after end
            for e in sorted(k for k in self.by_end if end <= k <= end + pd.Timedelta(days=75)):
                for s, rec in self.by_end[e]:
                    if s is None:
                        return rec
            return None
        for s, rec in self.by_end.get(end, []):
            if start is None and s is None:
                return rec
            if start is not None and s is not None and abs(s - start) <= TOL:
                return rec
        return None


def _split_factor(filed, splits):
    f = 1.0
    for date, ratio in splits:
        if date > filed:      # split happened after this number was published
            f *= ratio
    return f


def tag_series(df, cand, unit, kind, splits):
    parts = [p.strip() for p in cand.split("+")]
    singles = [_single_series(df, p, unit, kind, splits) for p in parts]
    if len(singles) == 1:
        return singles[0]
    # Sum of several tags: only where all parts exist for the exact same period.
    by_end = {}
    for end, lst in singles[0].by_end.items():
        for s, rec in lst:
            others = [o.find(s, end) for o in singles[1:]]
            if all(others):
                by_end.setdefault(end, []).append(
                    (s, _combine([rec, *others], lambda *v: sum(v), cand, "reported")))
    return Series(by_end)


def _single_series(df, p, unit, kind, splits):
    tax, tag = p.split(":", 1) if ":" in p else ("us-gaap", p)
    if tax == "dei":
        sub = df[(df.taxonomy == tax) & (df.tag == tag)]
    else:
        sub = df[(df.taxonomy == tax) & (df.tag == tag) & (df.unit == unit)]
    return series_from_rows(sub, kind, splits, p, loose=tax == "dei")


def _fix_sign_slips(versions):
    """A filing that repeats a number with the wrong sign (Walmart's 10-K of 2011 shows the income
    tax of 2010 as -7,156 where the filings before and after it show +7,156): when exactly one
    version has the other sign and is the exact negative of another version, flip it."""
    if len(versions) < 3:
        return versions
    pos = sum(v > 0 for _, v, _ in versions)
    neg = sum(v < 0 for _, v, _ in versions)
    if min(pos, neg) != 1 or max(pos, neg) < 2:     # only ONE filing out of line (else: real dispute)
        return versions
    minority = -1 if pos > neg else 1
    vals = [v for _, v, _ in versions]
    out = []
    for f, v, a in versions:
        if v and (v > 0) == (minority > 0) and any(abs(v + w) <= 1e-9 * abs(v) for w in vals):
            v = -v
        out.append((f, v, a))
    return out


# Share counts of a shell company before an IPO or merger (VICI 2016, Kraft Heinz 2014, Baker Hughes
# GE 2017: 100 shares). Real, but no use for a market cap or per-share numbers.
SHELL_COUNTS = (1, 10, 100, 1000)


def _fix_version_scale(groups):
    """Per-share numbers repeated 1,000x or 1,000,000x too large in a later filing (Halliburton's
    10-Q of July 2021 repeats the EPS of Q2 2020 as -1,910,000 instead of -1.91; same for Hartford
    and ICE): when two versions of the same period differ by such a factor, the one closer to the
    usual size of the tag (median over all periods) is right and the other is scaled to it."""
    vals = sorted(abs(v) for _, _, vs in groups for _, v, _ in vs if v)
    if not vals:
        return groups
    med = vals[len(vals) // 2]
    off = lambda v: abs(math.log(abs(v) / med))  # noqa: E731
    out = []
    for sd, ed, versions in groups:
        nz = [v for _, v, _ in versions if v]
        if len(set(nz)) < 2:
            out.append((sd, ed, versions))
            continue
        best = min(nz, key=off)
        fixed = []
        for f, v, a in versions:
            if v and v != best:
                fac = next((x for x in (1e3, 1e6, 1e-3, 1e-6) if abs(v * x / best - 1) < 0.02), None)
                if fac:
                    v *= fac
            fixed.append((f, v, a))
        out.append((sd, ed, fixed))
    return out


def _fix_share_scale(groups, source):
    """Share counts filed in millions or thousands by mistake (Coca-Cola's 10-Qs of 2017-2018 give the
    weighted diluted shares as 4,334 instead of 4,334,000,000): a number that is about 1,000x or
    1,000,000x off the median of the same tag over all periods is scaled back. Real share counts
    never move by a factor 1,000, so only such slips match. Done before quarters are derived, so a
    slip does not end up in Q4 (= 4 x year - Q1 - Q2 - Q3) or the TTM average."""
    # shell-company counts (exactly 1, 10, 100 or 1,000 shares before an IPO or merger) are left out
    # of the median: Paramount Skydance's two shell 10-Qs with 1,000 shares made the median 1,000,
    # which "scaled" its real 1,071,666,977 shares down to 1,071.67
    vals = sorted(abs(v) for _, _, vs in groups for _, v, _ in vs if v and v not in SHELL_COUNTS)
    if len(vals) < 3:
        return groups
    med = vals[len(vals) // 2]
    out = []
    for sd, ed, versions in groups:
        fixed = []
        for f, v, a in versions:
            r = abs(v) / med if v else 1
            # an exact 1, 10, 100 or 1,000 is a real share count (the shell company before an IPO or
            # merger: VICI 2016, Kraft Heinz 2014), not one filed in the wrong unit
            if not 0.2 <= r <= 5 and v not in SHELL_COUNTS:
                fac = next((x for x in (1e3, 1e6, 1e-3, 1e-6) if 0.5 <= r * x <= 2), None)
                if fac:
                    v *= fac
            fixed.append((f, v, a))
        out.append((sd, ed, fixed))
    return out


def series_from_rows(sub, kind, splits, source, loose=False):
    """Build a Series from fact rows (columns start_date, end_date, value, filed, accn)."""
    by_end = {}
    if sub.empty:
        return Series(by_end, loose=loose)
    sub = sub.sort_values("filed")
    groups = []
    for (sd, ed), g in sub.groupby(["start_date", "end_date"], sort=False):
        versions = []
        for r in g.itertuples():
            val = float(r.value)
            if kind in ("shares", "shares_instant"):
                val *= _split_factor(r.filed, splits)
            elif kind in ("eps", "per_share"):
                val /= _split_factor(r.filed, splits)
            versions.append((r.filed, val, r.accn))
        groups.append((sd, ed, _fix_sign_slips(versions)))
    if kind in ("shares", "shares_instant"):
        groups = _fix_share_scale(groups, source)
    elif kind in ("eps", "per_share"):
        groups = _fix_version_scale(groups)
    for sd, ed, versions in groups:
        (f0, o, _), (f1, v, a1) = versions[0], versions[-1]
        rec = Rec(v, o, f1, f0, a1, source, "reported", tuple(versions))
        start = pd.Timestamp(sd) if sd else None
        by_end.setdefault(pd.Timestamp(ed), []).append((start, rec))
    return Series(by_end, loose=loose)


# --------------------------------------------------------------------------- per-year values
def values_for_year(ser, y, kind, anchors):
    """Return {period_key: Rec} for one tag series and one fiscal year.

    Derived quarters use versions of the same vintage: e.g. Q4 = FY - 9 months takes the FY
    value from the 10-K that followed the 9-month 10-Q, so a later restatement of only the
    annual figure does not get dumped into Q4.
    """
    P = periods_of(y)
    out = {}
    if y.get("transition") and kind not in ("instant", "shares_instant"):
        # transition period: only the quarters as reported (no year-to-date chain from a normal year start)
        for key, (qs, qe) in P.items():
            r = ser.find(qs, qe)
            if r:
                out[key] = r
        return out
    if kind in ("instant", "shares_instant"):
        for key, (_, end) in P.items():
            r = ser.find(None, end)
            if r:
                # balance sheet consistency: prefer the value from the same filing as Total Assets
                a = anchors.get(end)
                match = [v for v in r.versions if v[2] == a] if a else []
                out[key] = replace(r, value=match[-1][1], filed=match[-1][0], accn=a) if match else r
        return out

    S = y["start"]
    fy = ser.find(*P["FY"]) if "FY" in P else None
    if fy:
        out["FY"] = fy
    additive = kind in ADDITIVE
    cum = {}
    for k in (1, 2, 3):
        if f"Q{k}" not in P:
            continue
        qs, qe = P[f"Q{k}"]
        direct = ser.find(qs, qe)
        cum[k] = direct if k == 1 else ser.find(S, qe)
        if direct:
            out[f"Q{k}"] = direct
        elif additive and cum.get(k) and cum.get(k - 1):
            prev = version_at(cum[k - 1], cum[k].filed)
            out[f"Q{k}"] = _combine([cum[k], prev], lambda a, b: a - b, cum[k].source, "ytd_diff")
    if "Q4" in P:
        direct = ser.find(*P["Q4"])
        q123 = [out.get(f"Q{k}") for k in (1, 2, 3)]
        if direct:
            out["Q4"] = direct
        elif fy and additive and cum.get(3):
            # Annual figure of the same vintage as the 9-month figure: the first one filed
            # after the 9-month number took its current value. (Not after its LAST filing: an
            # unchanged number is repeated in later reports, and by then the year may have
            # been restated for something the quarters never were restated for.)
            fy_v = version_at(fy, _stable_since(cum[3]), after=True)
            out["Q4"] = _combine([fy_v, cum[3]], lambda a, b: a - b, fy.source, "fy_minus_ytd")
        elif fy and additive and all(q123):
            out["Q4"] = _combine([fy, *q123], lambda a, b, c, d: a - b - c - d, fy.source, "fy_minus_q")
        elif fy and kind == "shares" and all(q123):
            # weighted-average shares: the year's average is ~ the mean of the 4 quarters
            out["Q4"] = _combine([fy, *q123], lambda a, b, c, d: 4 * a - b - c - d,
                                 fy.source, "fy_avg_minus_q")
    if kind == "flow" and fy and cum.get(2) and cum.get(3):
        _reconcile_quarters(out, fy, cum)
    return out


def _off(a, b, tol=1e-4):
    return abs(a - b) > tol * max(abs(a), abs(b), 1.0)


def _reconcile_quarters(out, fy, cum):
    """Repair quarters that do not add up to the year, using the year-to-date figures.

    A company's filings sometimes contradict themselves: a single-quarter figure repeated in a
    later report with a wrong date, or a first quarter that was silently reclassified. The
    cash-flow / income statement itself reports year-to-date totals (6 and 9 months, full year),
    and those are what the annual figure is built from. So when the four quarters do not add up
    we rebuild them from the year-to-date chain, and split the first half using the second
    quarter that was reported in the SAME filing as the 6-month total."""
    qs = [out.get(f"Q{k}") for k in (1, 2, 3, 4)]
    if not all(qs):
        return
    total = sum(q.value for q in qs)
    fy_v = version_at(fy, _stable_since(cum[3]), after=True)
    if not _off(total, fy.value) or not _off(total, fy_v.value):
        return
    if any(not r.versions for r in (cum[2], cum[3], fy)):
        return
    def in_filing(q, total):
        """The single-quarter figure from the same filing as the year-to-date total."""
        if q.method != "reported":
            return None
        same = [v for v in q.versions if v[2] == total.accn]
        return replace(q, value=same[-1][1], filed=same[-1][0], accn=same[-1][2]) if same else q

    new = dict(Q4=_combine([fy_v, cum[3]], lambda a, b: a - b, fy.source, "fy_minus_ytd"))
    q3 = in_filing(out["Q3"], cum[3])
    if q3 is None:
        c2 = version_at(cum[2], cum[3].filed)
        q3 = _combine([cum[3], c2], lambda a, b: a - b, cum[3].source, "ytd_diff")
    if _off(cum[3].value - q3.value, cum[2].value):
        return          # 6- and 9-month totals come from different restatement rounds: leave as is
    new["Q3"] = q3
    q2 = in_filing(out["Q2"], cum[2])
    if q2 is not None:
        new["Q2"] = q2
        new["Q1"] = _combine([cum[2], q2], lambda a, b: a - b, cum[2].source, "ytd_diff")
    else:
        q1 = version_at(cum[1], cum[2].filed) if cum.get(1) else None
        if q1 is None:
            return
        new["Q1"] = q1
        new["Q2"] = _combine([cum[2], q1], lambda a, b: a - b, cum[2].source, "ytd_diff")
    if _off(sum(new[f"Q{k}"].value for k in (1, 2, 3, 4)), fy_v.value):
        return                                       # the year-to-date chain is inconsistent too
    for k, rec in new.items():
        old = out[k]
        if _off(rec.value, old.value):
            # keep the date the quarter first became public
            out[k] = replace(rec, orig_filed=min(rec.orig_filed, old.orig_filed))


def _same_filing_conflict(a, b, tol=0.005):
    """True if two records were reported in the SAME filing with clearly different values.

    Two tags that a company uses side by side with different numbers measure different things
    (e.g. "total non-operating income" vs "other non-operating income")."""
    if not a.versions or not b.versions:
        return False
    other = {v[2]: v[1] for v in b.versions}
    for _, val, accn in a.versions:
        o = other.get(accn)
        if o is not None and abs(val - o) > tol * max(abs(val), abs(o)):
            return True
    return False


def _with_history(rec, others):
    """Add versions that EARLIER filings reported under another candidate tag.

    Companies switch tags over time (SalesRevenueNet -> RevenueFromContract...). The first
    published number for a period may therefore sit under an older tag. Knowing it gives the
    true "first reported" value and the true date the number became public.
    """
    first = rec.versions[0]
    extra = []
    for o in others:
        if o is rec or not o.versions or o.method != "reported" or _same_filing_conflict(rec, o):
            continue
        for v in o.versions:
            ratio = v[1] / first[1] if first[1] else 0
            if v[0] < first[0] and 0.5 <= ratio <= 2:
                extra.append(v)
    if not extra:
        return rec
    seen, hist = set(), []
    for v in sorted(extra, key=lambda v: v[0]):
        if v[2] not in seen:
            seen.add(v[2])
            hist.append(v)
    return replace(rec, orig=hist[0][1], orig_filed=hist[0][0], versions=tuple(hist) + rec.versions)


class _HistSeries:
    """A tag series whose records also carry the versions earlier filings reported under the
    other candidate tags. Derived quarters (Q4 = year - 9 months, TTM, ...) are built from
    these, so their "first public" date is right even when the company switched tags."""

    def __init__(self, ser, others):
        self.ser, self.others = ser, others
        self.by_end, self.loose = ser.by_end, ser.loose

    def find(self, start, end):
        rec = self.ser.find(start, end)
        if rec is None or not rec.versions or rec.method != "reported":
            return rec
        return _with_history(rec, [r for r in (o.find(start, end) for o in self.others) if r])


def pick_values(sers, years, kind, anchors=None):
    """Combine candidate series (in priority order) into {(ptype, fy, fq): Rec}."""
    anchors = anchors or {}
    sers = [(c, s) for c, s in sers if s.by_end]
    loose = {c for c, s in sers if s.loose}
    res = {}
    for y in years:
        outs = [(c, values_for_year(s, y, kind, anchors)) for c, s in sers]
        outs = [(c, o) for c, o in outs if o]
        if not outs:
            continue
        primary = next(((c, o) for c, o in outs if "FY" in o), outs[0])
        pfy = primary[1].get("FY")
        if pfy and not any(k.startswith("Q") for k in primary[1]):
            # The primary tag only has the annual number. If another candidate has the year AND
            # its quarters, and the two disagree inside the same filing, the primary one is a
            # footnote figure and the other one is the actual statement line: use that.
            # A lower-priority tag with the SAME annual number and its quarters is simply the
            # same line under another name: use it (Cisco 2010: "Revenues" only for the year,
            # "SalesRevenueNet" for year and quarters, "SalesRevenueGoodsNet" = products only).
            full = [(c, o) for c, o in outs if c != primary[0] and "FY" in o
                    and all(f"Q{k}" in o for k in (1, 2, 3))]
            same = [(c, o) for c, o in full if not _off(o["FY"].value, pfy.value, 0.005)]
            other = [(c, o) for c, o in full if _same_filing_conflict(pfy, o["FY"])]
            if same:
                # keep the higher-priority annual record (it carries the full filing history)
                primary = (same[0][0], {**same[0][1], "FY": pfy})
            elif other:
                primary = other[0]
                pfy = primary[1]["FY"]
        merged = dict(primary[1])
        usable = [primary[1]]
        used = [primary[0]]
        for c, o in outs:
            if c == primary[0]:
                continue
            # A tag whose annual figure differs from the primary one inside the same filing
            # measures something else: never use it to fill this year's missing quarters.
            # (Cover-page values are dated differently by nature, so they are exempt.)
            if c not in loose and pfy and "FY" in o and _same_filing_conflict(pfy, o["FY"]):
                continue
            usable.append(o)
            used.append(c)
            higher = [o2 for c2, o2 in outs[:[x for x, _ in outs].index(c)] if c2 not in loose]
            for key, rec in o.items():
                # Same for a single period: a value that disagrees with a higher-priority tag for
                # that period inside the same filing (e.g. a company's own "Revenue" line that is
                # really revenue + other income) is not used for it.
                if c not in loose and any(key in o2 and _same_filing_conflict(rec, o2[key]) for o2 in higher):
                    continue
                merged.setdefault(key, rec)
        # Derived values (Q2 = 6 months - Q1, Q4 = year - 9 months): the date they became
        # public must count filings made under an older tag too, otherwise a tag switch makes
        # the number look as if it was published years late (and valuation charts get holes).
        by_name = dict(sers)
        early = {}
        for c in used:
            hs = _HistSeries(by_name[c], [by_name[x] for x in used if x != c])
            for key, rec in values_for_year(hs, y, kind, anchors).items():
                early.setdefault((c, key), rec.orig_filed)
        src = {id(rec): c for c, o in outs for rec in o.values()}
        for key, rec in merged.items():
            if rec.versions and rec.method == "reported":
                rec = _with_history(rec, [o[key] for o in usable if key in o])
            else:
                e = early.get((src.get(id(rec)), key))
                if e and e < rec.orig_filed:
                    rec = replace(rec, orig_filed=e)
            merged[key] = rec
        # Q4 still missing while the year and Q1-Q3 are there, from different tags (Air Products:
        # the 10-K tags operating cash flow only as "continuing operations", the 10-Qs only as
        # the total): Q4 = year - Q1 - Q2 - Q3
        if kind in ADDITIVE and "Q4" in periods_of(y) and "Q4" not in merged and "FY" in merged \
                and all(f"Q{k}" in merged for k in (1, 2, 3)):
            merged["Q4"] = _combine([merged["FY"], *(merged[f"Q{k}"] for k in (1, 2, 3))],
                                    lambda a, b, c, d: a - b - c - d, merged["FY"].source, "fy_minus_q")
        for key, rec in merged.items():
            res[pkey(y, key)] = rec
    return res


def ttm_from_quarters(qvals, meta, kind="flow"):
    """qvals: {pk: Rec} for Q periods -> {('TTM', fy, fq): Rec} (4 consecutive quarters)."""
    quarters = sorted(qvals, key=lambda pk: meta[pk][1])
    out = {}
    for i in range(3, len(quarters)):
        four = quarters[i - 3:i + 1]
        if any(abs(meta[b][0] - (meta[a][1] + DAY)) > TOL for a, b in zip(four, four[1:])):
            continue
        recs = [qvals[q] for q in four]
        fn = (lambda *v: sum(v) / 4) if kind == "shares" else (lambda *v: sum(v))
        out[("TTM", four[-1][1], four[-1][2])] = (_combine(recs, fn, recs[-1].source, "ttm"),
                                                   (meta[four[0]][0], meta[four[-1]][1]))
    return out


# --------------------------------------------------------------------------- formulas
_TOKEN = re.compile(r"\b([a-z][a-z0-9_]*)(\??)")


def eval_formula(formula, vals):
    names = _TOKEN.findall(formula)                    # [(name, '?' or '')]
    present = [n for n, _ in names if vals.get(n) is not None]
    if not present or any(vals.get(n) is None and not opt for n, opt in names):
        return None
    expr = formula.replace("?", "")
    zeros = {n: 0.0 for n, _ in names}

    def fn(*v):
        return eval(expr, {"__builtins__": {}}, {**zeros, **dict(zip(present, v))})

    used = [vals[n] for n in present]
    try:
        v, o = fn(*[r.value for r in used]), fn(*[r.orig for r in used])
    except ZeroDivisionError:
        return None
    if not (math.isfinite(v) and math.isfinite(o)):
        return None
    return Rec(v, o, max(r.filed for r in used), max(r.orig_filed for r in used),
               None, formula, "derived", parts=tuple(used), fn=fn)


# --------------------------------------------------------------------------- calendar quarter
def calendar_quarter(end):
    best = None
    for yr in (end.year - 1, end.year, end.year + 1):
        for q, (m, d) in enumerate(((3, 31), (6, 30), (9, 30), (12, 31)), 1):
            diff = abs((pd.Timestamp(yr, m, d) - end).days)
            if best is None or diff < best[0]:
                best = (diff, f"{yr}Q{q}")
    return best[1]


# --------------------------------------------------------------------------- share classes
def _class_key(member):
    """CommonClassBMember / EquivalentClassBMember -> 'ClassB' (same share class, different label)."""
    if "Class" not in member and member.endswith("CommonStockMember"):
        return ""          # plain common stock (HersheyCommonStockMember = CommonStockMember)
    return re.sub(r"Member$|^(Common|Equivalent|Capital)|Stock", "", member)


def share_class_rows(df, ticker, splits=()):
    """Companies whose share classes are NOT worth the same (Berkshire: 1 A = 1500 B) tag EPS and
    share counts only per class. Returns (rows, note) where rows are synthetic facts expressed in
    units of the class that trades under `ticker`:
      class:EarningsPerShare*            EPS of the traded class
      class:CommonStockSharesOutstanding  all classes converted to traded-class shares
      dei:TradedClassSharesOutstanding    same, from the cover page
    Conversion ratio per class = its EPS / the traded class's EPS in the same filing (rounded to
    3 digits), so raw share counts of different classes are never simply added up. A filing without
    per-class EPS borrows the ratio of the nearest filing from the same stock-split era (a split of
    only the traded class changes the ratio: Berkshire 30 -> 1500 in 2010).
    Returns (None, None) for companies with one class or classes of equal value (e.g. Alphabet)."""
    c = df[df.taxonomy == "class"]
    if c.empty:
        return None, None
    c = c.assign(concept=c.tag.str.split("|").str[0], member=c.tag.str.split("|").str[1])
    c = c.assign(key=c.member.map(_class_key))
    norm = lambda t: re.sub(r"[^A-Z0-9]", "", str(t).upper())  # noqa: E731
    syms = c[c.concept == "TradingSymbol"]
    hit = syms[syms.label.map(norm) == norm(ticker)]
    if not hit.empty:
        traded = hit.key.mode().iloc[0]
    else:
        # no trading symbol per class in the filings (Erie Indemnity): the listed class is the one
        # with by far the most shares (Berkshire B, Erie A); only when it clearly dominates
        so = c[c.concept.isin(["CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding"])]
        if so.empty:
            return None, None
        last = so[so.end_date == so.end_date.max()].groupby("key").value.max().sort_values()
        if len(last) < 2:
            return None, None
        if last.iloc[-1] >= 100 * last.iloc[-2]:
            traded = last.index[-1]
        elif "" in last.index and all(k.startswith("Class") for k in last.index if k):
            # plain common stock next to a lettered class (Hershey: Common listed, Class B held by a
            # trust): the plain common stock is the listed one
            traded = ""
        else:
            return None, None
    eps = c[c.concept == "EarningsPerShareBasic"]
    teps = eps[eps.key == traded].drop_duplicates(["start_date", "end_date", "accn"])
    teps = teps.set_index(["start_date", "end_date", "accn"]).value
    # per filing and class: EPS ratio to the traded class
    ratios = {}
    for r in eps[eps.key != traded].itertuples():
        t = teps.get((r.start_date, r.end_date, r.accn))
        if t and abs(t) > 0.05 and r.value:
            ratios.setdefault((r.accn, r.key), []).append(r.value / t)
    if not ratios:
        return None, None
    rnd = lambda x: float(f"{x:.3g}")  # noqa: E731
    filed_of = dict(zip(c.accn, c.filed))
    era = lambda accn: _split_factor(filed_of[accn], splits)  # noqa: E731
    ratios = {k: rnd(pd.Series(v).median()) for k, v in ratios.items()}
    by_key = {}
    for (accn, key), v in ratios.items():
        by_key.setdefault(key, []).append((filed_of[accn], v, era(accn)))
    # classes of equal value (the typical ratio is 1; a one-off difference such as Alphabet's 2015
    # Class C adjustment payment does not count): the normal tags are fine
    if all(abs(pd.Series([v for _, v, _ in vs]).median() - 1) < 0.02 for vs in by_key.values()):
        return None, None
    latest = {k: max(vs)[1] for k, vs in by_key.items()}

    def ratio(accn, key):
        """Ratio from this filing, else from the nearest filing of the same stock-split era."""
        if (accn, key) in ratios:
            return ratios[(accn, key)]
        vs = by_key.get(key)
        if not vs:
            return None
        vs = [v for v in vs if v[2] == era(accn)]     # none: unknown, leave out
        if not vs:
            return None
        f = pd.Timestamp(filed_of[accn])
        return min(vs, key=lambda x: abs(pd.Timestamp(x[0]) - f))[1]

    out = []
    for concept in ("EarningsPerShareBasic", "EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"):
        sub = c[(c.concept == concept) & (c.key == traded)].drop_duplicates(["start_date", "end_date", "accn"])
        out.append(sub.assign(tag=concept))
        # a filing that gives EPS only for another class (Berkshire's 10-Qs of 2010-2013: class A only):
        # converted to the traded class with the ratio of that filing's era (1 A = 1500 B)
        have = set(zip(sub.start_date, sub.end_date, sub.accn))
        conv = []
        for r in c[(c.concept == concept) & (c.key != traded)].drop_duplicates(
                ["start_date", "end_date", "accn"]).itertuples():
            if (r.start_date, r.end_date, r.accn) in have:
                continue
            f = ratio(r.accn, r.key)
            if f:
                conv.append({**r._asdict(), "value": r.value / f})
        if conv:
            out.append(pd.DataFrame(conv).drop(columns="Index").assign(tag=concept))
    cover = set(c[c.concept == "EntityCommonStockSharesOutstanding"].member)
    for concept, tax, tag in (("CommonStockSharesOutstanding", "class", "CommonStockSharesOutstanding"),
                              ("EntityCommonStockSharesOutstanding", "dei", "TradedClassSharesOutstanding")):
        sub = c[(c.concept == concept) & c.member.isin(cover)].drop_duplicates(["member", "end_date", "accn"])
        rows = []
        for (end, accn), g in sub.groupby(["end_date", "accn"]):
            total, ok = 0.0, True
            for r in g.itertuples():
                if r.key == traded:
                    total += r.value
                    continue
                f = ratio(accn, r.key)
                if f is None:
                    ok = False
                    break
                total += r.value * f
            if ok and (g.key == traded).any():
                first = g.iloc[0]
                rows.append({**first.to_dict(), "taxonomy": tax, "tag": tag, "value": total})
        if rows:
            out.append(pd.DataFrame(rows))
    res = pd.concat(out, ignore_index=True)
    res["taxonomy"] = res.taxonomy.where(res.tag == "TradedClassSharesOutstanding", "class")
    desc = ", ".join(f"1 {k} = {v:g} {traded}" for k, v in sorted(latest.items()))
    note = ("info", "shares_outstanding",
            f"Several share classes of different value ({desc}): EPS is per {traded} share and share "
            f"counts / market cap are in {traded}-equivalent shares")
    return res[df.columns.intersection(res.columns)], note


# capital spending lines added to the property-and-equipment line (step 1a2 in standardize_company)
CAPEX_EXTRA = ("PaymentsToAcquireSoftware", "PaymentsForSoftware", "PaymentsToDevelopSoftware",
               "PaymentsToAcquireIntangibleAssets", "PaymentsToAcquireEquipmentOnLease",
               "PaymentsForConstructionInProcess", "PaymentsForNuclearFuel",
               "PaymentsToAcquireOilAndGasPropertyAndEquipment")


def after_class_rows(df, cls_rows):
    """Share classes that were merged into one (Kinder Morgan's class P/A/B until 2014): for every period
    a filing gives no per-class data for at all, the plain EPS and share counts are the traded class's own
    numbers, so they are copied in as the per-class rows (otherwise every year after the merger would have
    no EPS and no share count)."""
    # a period with per-class facts that could not be converted (Berkshire 2010-2013 quarters) stays out:
    # its plain numbers are class A figures
    # (only up to the last period with usable class data: after it, the classes are gone and a stray
    # per-class fact means nothing, Kinder Morgan 2025)
    last = cls_rows.end_date.max()
    raw = df[(df.taxonomy == "class") & (df.end_date <= last)]
    with_class = set(zip(cls_rows.accn, cls_rows.end_date)) | set(zip(raw.accn, raw.end_date))
    later = df[~pd.Series([k in with_class for k in zip(df.accn, df.end_date)], index=df.index)]
    out = []
    for tax, tag, new_tax, new_tag in (
            ("us-gaap", "EarningsPerShareBasic", "class", "EarningsPerShareBasic"),
            ("us-gaap", "EarningsPerShareDiluted", "class", "EarningsPerShareDiluted"),
            ("us-gaap", "EarningsPerShareBasicAndDiluted", "class", "EarningsPerShareBasicAndDiluted"),
            ("us-gaap", "CommonStockSharesOutstanding", "class", "CommonStockSharesOutstanding"),
            ("us-gaap", "WeightedAverageNumberOfSharesOutstandingBasic", "class", "WeightedAverageNumberOfSharesOutstandingBasic"),
            ("us-gaap", "WeightedAverageNumberOfDilutedSharesOutstanding", "class",
             "WeightedAverageNumberOfDilutedSharesOutstanding"),
            ("dei", "EntityCommonStockSharesOutstanding", "dei", "TradedClassSharesOutstanding")):
        sub = later[(later.taxonomy == tax) & (later.tag == tag)]
        if len(sub):
            out.append(sub.assign(taxonomy=new_tax, tag=new_tag))
    return pd.concat(out, ignore_index=True) if out else None


# members of the share-class axis that are not common stock (never added to a share count)
NOT_COMMON = re.compile(r"Preferred|Exchangeable|Treasury|Warrant|Debenture|Note|Right|Option|Restricted|"
                        r"Depositary|Adr|Ads[A-Z]|Units?Member|Partnership", re.I)


def class_total_rows(df):
    """Share counts given only per class, with no total (Skydance since 2026: class A 31.5 million +
    class B 5,012 million on the cover page; Bloom Energy after its IPO). For companies whose classes
    are worth the same (share_class_rows found no difference), the total is the sum of the common
    stock classes. Only for filings and dates that have no total of their own."""
    c = df[(df.taxonomy == "class") & (df.unit == "shares")]
    if c.empty:
        return None
    c = c.assign(concept=c.tag.str.split("|").str[0], member=c.tag.str.split("|").str[1])
    c = c[~c.member.fillna("").str.contains(NOT_COMMON)]
    # only a filing's own date, not the earlier dates it repeats for comparison (Brown-Forman's 10-Qs
    # repeat last year's per-class counts from before a stock split)
    c = c[c.end_date == c.groupby(["accn", "concept"]).end_date.transform("max")]
    cover = {a: set(g.member) for a, g in c[c.concept == "EntityCommonStockSharesOutstanding"].groupby("accn")}
    out = []
    for concept, tax in (("CommonStockSharesOutstanding", "us-gaap"), ("EntityCommonStockSharesOutstanding", "dei")):
        sub = c[c.concept == concept]
        if sub.empty:
            continue
        plain = df[(df.taxonomy == tax) & (df.tag == concept) & (df.value > 0)]   # a total of 0 is no total
        have = set(zip(plain.accn, plain.end_date))
        rows = []
        for (accn, end), g in sub.groupby(["accn", "end_date"]):
            g = g.drop_duplicates("member", keep="last")
            if (accn, end) in have or len(g) < 2 or (g.value <= 0).all():
                continue
            # the balance sheet can list fewer classes than the cover page (Alphabet 2014: A and B on the
            # balance sheet, A, B and C on the cover): then its sum is not the total
            if concept == "CommonStockSharesOutstanding" and not cover.get(accn, set()) <= set(g.member):
                continue
            first = g.iloc[0]
            rows.append({**first.to_dict(), "taxonomy": tax, "tag": concept, "value": float(g.value.sum()),
                         "label": "sum of share classes"})
        if rows:
            out.append(pd.DataFrame(rows))
    if not out:
        return None
    res = pd.concat(out, ignore_index=True)
    return res[df.columns.intersection(res.columns)]


# Items that, for a company with share classes of different value, come only from the per-class
# data (the plain tags hold a mix of classes): key -> (tags, formulas)
CLASS_OVERRIDES = {
    "eps_basic": (["class:EarningsPerShareBasic"], []),
    "eps_diluted": (["class:EarningsPerShareDiluted", "class:EarningsPerShareBasicAndDiluted"], ["eps_basic"]),
    # class:WeightedAverage... exist only for periods without class data (after_class_rows): the plain count
    "shares_basic": (["class:WeightedAverageNumberOfSharesOutstandingBasic"], ["net_income_common / eps_basic"]),
    "shares_diluted": (["class:WeightedAverageNumberOfDilutedSharesOutstanding"],
                       ["net_income_common / eps_diluted", "shares_basic"]),
    "shares_outstanding": (["class:CommonStockSharesOutstanding", "dei:TradedClassSharesOutstanding"], []),
}


# --------------------------------------------------------------------------- main build
def standardize_company(conn, cik, ticker):
    df = load_facts(conn, cik)
    if df.empty:
        raise RuntimeError("no financial statements in SEC data (fund or ETF?) - prices only")
    if not (df.taxonomy == "us-gaap").any() and (df.taxonomy == "ifrs-full").any():
        # foreign filers reporting under IFRS (20-F, e.g. TSMC): not supported yet. Remove anything
        # computed earlier, so no wrong valuation (ADR vs ordinary shares, other currency) is shown.
        for table, col, key in (("financials", "cik", cik), ("metrics", "cik", cik),
                                ("valuation_daily", "ticker", ticker)):
            conn.execute(f"DELETE FROM {table} WHERE {col}=?", (key,))
        conn.commit()
        raise RuntimeError("foreign filer (IFRS statements, 20-F/40-F) - not supported yet, prices only")
    from . import splitbasis
    splits = splitbasis.refresh(conn, ticker, cik)    # real splits only (spin-offs are not splits)
    cur = currency_of(df)
    unit_for = {"flow": cur, "instant": cur, "shares": "shares", "shares_instant": "shares",
                "eps": f"{cur}/shares", "per_share": f"{cur}/shares"}
    years = build_calendar(df)
    cls_rows, cls_note = share_class_rows(df, ticker, splits)
    overrides = {}
    if cls_rows is not None:
        df = pd.concat([df, cls_rows.assign(end=pd.to_datetime(cls_rows.end_date),
                                            start=pd.to_datetime(cls_rows.start_date, errors="coerce"))],
                       ignore_index=True)
        later = after_class_rows(df, cls_rows)
        if later is not None:
            df = pd.concat([df, later], ignore_index=True)
        overrides = CLASS_OVERRIDES
    else:
        tot = class_total_rows(df)
        if tot is not None:
            # a total of 0 next to the per-class counts (Bloom Energy 2018) gives way to their sum
            key = set(zip(tot.taxonomy, tot.tag, tot.accn, tot.end_date))
            zero = (df.value == 0) & pd.Series([k in key for k in zip(df.taxonomy, df.tag, df.accn, df.end_date)],
                                               index=df.index)
            df = pd.concat([df[~zero], tot], ignore_index=True)
    # cover-page share counts ("shares outstanding as of <date shortly before filing>"): newer than the
    # balance sheet, so they also catch a merger or share issue right after the quarter (Skyworks Nov 2026)
    cover = tag_series(df, "dei:TradedClassSharesOutstanding" if overrides else "dei:EntityCommonStockSharesOutstanding",
                       "shares", "shares_instant", splits)
    cover_by_accn = {a: v for lst in cover.by_end.values() for _, rec in lst for _, v, a in rec.versions}
    tags_of = lambda it: overrides.get(it["key"], (it["tags"], None))[0]  # noqa: E731
    is_reit = industry_of(conn, cik) == "reit"
    _sic = conn.execute("SELECT sic FROM companies WHERE cik=?", (cik,)).fetchone()
    is_fin = str((_sic or [None])[0] or "").startswith("6")       # SEC industry 6xxx: finance, insurance, real estate
    # a formula starting with "reit:" is only for REITs, "nonfin:" only for companies outside finance
    # (for a broker, interest is part of the business, not financing)
    formulas_of = lambda it: [f.split(":", 1)[1].strip() if f.startswith(("reit:", "nonfin:")) else f  # noqa: E731
                              for f in overrides.get(it["key"], (None, it["formulas"]))[1]
                              if (is_reit or not f.startswith("reit:")) and (not is_fin or not f.startswith("nonfin:"))]

    # results[(ptype, fy, fq)] = {item: Rec};  meta[...] = (start, end)
    results, meta = {}, {}
    for y in years:
        for key, (s, e) in periods_of(y).items():
            pk = pkey(y, key)
            results.setdefault(pk, {})
            meta[pk] = (s, e)

    # balance-sheet anchor: the filing that supplied Total Assets for each date
    assets = tag_series(df, "Assets", cur, "instant", splits)
    anchors = {end: lst[0][1].accn for end, lst in assets.by_end.items()}

    cand_series = {}     # item -> its candidate tag series (for the repairs below)
    # 1) tags. Per fiscal year one primary tag is chosen (the first in priority order that has
    #    the annual value), so quarters and the year come from the same tag where possible.
    #    Exception: if the primary tag only has the annual number and a lower-priority tag has
    #    the full year AND quarters with a different annual value in the SAME filing, the two
    #    tags measure different things (e.g. a footnote vs the cash-flow statement line) and the
    #    statement line (the one with quarters) is used. Remaining gaps are filled from other tags.
    notes = [cls_note] if cls_note else []     # things a reader of the data should know (shown in the app)
    for item in ITEMS:
        kind = item["kind"]
        sers = [(c, tag_series(df, c, unit_for[kind], kind, splits)) for c in tags_of(item)]
        cand_series[item["key"]] = sers
        for pk, rec in pick_values(sers, years, kind, anchors).items():
            results[pk].setdefault(item["key"], rec)

    # 1a0) cost of revenue tagged only as products + services (RTX since 2018: CostOfGoodsAndServicesSold
    #      split by ProductMember / ServiceMember, no total). Only exactly those two standard members
    #      from the same filing are added up, so nothing is counted twice.
    if any("cost_of_revenue" not in results[pk] for pk in results if pk[0] == "FY"):
        seg = pd.read_sql_query(
            "SELECT member, start_date, end_date, value, filed, accn FROM segment_facts WHERE cik=? "
            "AND concept='us-gaap:CostOfGoodsAndServicesSold' AND breakdown='product' AND ndims=1 "
            "AND member IN ('us-gaap:ProductMember','us-gaap:ServiceMember') AND unit=?",
            conn, params=(cik, cur))
        if len(seg):
            g = seg.groupby(["start_date", "end_date", "accn"]).agg(
                value=("value", "sum"), n=("member", "nunique"), filed=("filed", "first")).reset_index()
            g = g[g.n == 2]
            g["accn"] = g.accn.str.replace(r"^(\d{10})(\d{2})(\d{6})$", r"\1-\2-\3", regex=True)
            ser = series_from_rows(g, "flow", splits, "CostOfGoodsAndServicesSold (products + services)")
            for pk, rec in pick_values([("seg_cost", ser)], years, "flow").items():
                if "cost_of_revenue" in results[pk]:
                    continue
                # same filing as revenue where possible: a later filing may have restated the costs
                # for a changed company (spin-offs) while revenue is still the earlier figure
                rev = results[pk].get("revenue")
                same = [v for v in rec.versions if rev and v[2] == rev.accn]
                if same:
                    rec = replace(rec, value=same[-1][1], filed=same[-1][0], accn=same[-1][2])
                results[pk]["cost_of_revenue"] = rec

    # 1a0b) revenue tagged only per product with no undimensioned total (APA: Revenues for
    #       "oil and gas" = "own production" + "purchased"). A member whose value equals the sum
    #       of the other members of the same filing and period is the total.
    if any("revenue" not in results[pk] for pk in results if pk[0] == "FY"):
        seg = pd.read_sql_query(
            "SELECT member, start_date, end_date, value, filed, accn FROM segment_facts WHERE cik=? "
            "AND concept IN ('us-gaap:Revenues','us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax') "
            "AND breakdown='product' AND ndims=1 AND unit=?", conn, params=(cik, cur))
        tot_rows = []
        for (s_, e_, a_), g in seg.groupby(["start_date", "end_date", "accn"]):
            g = g.drop_duplicates("member")
            if len(g) < 3:
                continue
            tot_v = g.value.sum()
            for r in g.itertuples():
                if r.value and abs(2 * r.value - tot_v) <= 0.001 * abs(r.value):
                    tot_rows.append(dict(start_date=s_, end_date=e_, value=r.value, filed=r.filed, accn=a_))
                    break
        if tot_rows:
            g = pd.DataFrame(tot_rows)
            g["accn"] = g.accn.str.replace(r"^(\d{10})(\d{2})(\d{6})$", r"\1-\2-\3", regex=True)
            ser = series_from_rows(g, "flow", splits, "Revenues (total of the product breakdown)")
            for pk, rec in pick_values([("seg_rev", ser)], years, "flow").items():
                results[pk].setdefault("revenue", rec)

    # 1a0c) lessors that tag no revenue total at all, only their rent (American Homes 4 Rent since
    #       2021): the rent is the revenue for the years that have nothing else (year and quarters).
    #       Not for banks: their revenue is built from interest and fee income later.
    no_rev = {pk[1] for pk in results if pk[0] == "FY" and "revenue" not in results[pk]}
    if no_rev and not any(all(m in v for m in BANK_MARKERS) for pk, v in results.items() if pk[0] == "FY"):
        ser = tag_series(df, "OperatingLeaseLeaseIncome", cur, "flow", splits)
        picked = pick_values([("OperatingLeaseLeaseIncome", ser)], years, "flow")
        # a year with exactly the same rent as another year is a fact filed under the wrong date
        # (VMRK: "2025" = 2022 to the dollar): left out, with its quarters
        fy_vals = [r.value for pk, r in picked.items() if pk[0] == "FY"]
        dup = {pk[1] for pk, r in picked.items() if pk[0] == "FY" and fy_vals.count(r.value) > 1}
        for pk, rec in picked.items():
            if pk[1] in no_rev and pk[1] not in dup and pk in results:
                results[pk].setdefault("revenue", rec)

    # 1a1) revenue from contracts with customers is only PART of revenue for insurers, lessors and
    #      conglomerates (Berkshire: 247B from contracts + 124B premiums and investment income = 371B
    #      "Revenues"). When the company's "Revenues" total in the same filing equals contract revenue
    #      + revenue not from contracts, that total is used. (Alphabet tags a hedging amount as "not
    #      from contracts" that is already inside contract revenue: its total equals contract revenue,
    #      so nothing changes there.)
    rnfc = tag_series(df, "RevenueNotFromContractWithCustomer", cur, "flow", splits)
    tot = dict(cand_series.get("revenue", [])).get("Revenues")
    # Also: a "Revenues" total in the SAME filing at least 25% above contract revenue means contract
    #      revenue is only a part (REITs: rent is lease income, not contract revenue; UDR 11M of 1.7B).
    #      The whole year switches (year and its quarters, from that same tag), so they stay
    #      consistent. Not for banks: their "Revenues" is a gross figure (see 2b).
    # (both bank markers: insurers report policyholder "deposits" too, and their "Revenues" total of
    #  premiums + investment income is the real one: MetLife 77B vs 2.4B contract revenue)
    is_bank_like = any(all(m in v for m in BANK_MARKERS) for pk, v in results.items() if pk[0] == "FY")
    # A smaller gap counts too when the company's own split by country adds up to "Revenues" and not
    # to contract revenue: then "Revenues" is the total it reports (Sempra: 13.2B incl. leases and
    # other revenue vs 11.8B from contracts). Without that evidence a larger "Revenues" can include
    # other income (Chevron) or be a gross figure (Republic Services), so it is not used.
    geo = pd.read_sql_query(
        "SELECT concept, start_date, end_date, accn, SUM(value) AS total, COUNT(*) AS n FROM (SELECT DISTINCT "
        "concept, member, start_date, end_date, accn, value FROM segment_facts WHERE cik=? AND ndims=1 AND "
        "breakdown='geography' AND concept IN ('us-gaap:Revenues', "
        "'us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax')) GROUP BY 1,2,3,4", conn, params=(cik,))
    geo_sum = {(r.concept.split(":")[1], r.start_date, r.end_date, r.accn.replace("-", "")): r.total
               for r in geo.itertuples() if r.n >= 2}

    def _geo_total(tag, s_, e_, accn, value):
        g = geo_sum.get((tag, str(s_.date()), str(e_.date()), (accn or "").replace("-", "")))
        return g is not None and not _off(g, value, 0.005)

    if tot is not None and not is_bank_like:
        by_fy = {y["fy"]: y for y in years if not y.get("transition")}
        switched = set()
        for pk, vals in list(results.items()):
            rev = vals.get("revenue")
            # the test runs on the year; a year without its annual report yet (the current one) is
            # tested on its quarters, otherwise this year's quarters stay on the partial tag (AMT 2026:
            # 0.2B contract revenue a quarter next to 2.8B "Revenues")
            if pk[1] in switched or (pk[0] != "FY" and ("FY", pk[1], 0) in results) or not rev or not rev.value or \
                    rev.source != "RevenueFromContractWithCustomerExcludingAssessedTax":
                continue
            t = tot.find(*meta[pk])
            same = [v for v in (t.versions if t else ()) if v[2] == rev.accn]
            if not same or same[-1][1] < 1.005 * rev.value or pk[1] not in by_fy:
                continue
            if same[-1][1] < 1.25 * rev.value and not (
                    _geo_total("Revenues", *meta[pk], rev.accn, same[-1][1])
                    and not _geo_total("RevenueFromContractWithCustomerExcludingAssessedTax", *meta[pk], rev.accn, rev.value)):
                continue
            out = values_for_year(tot, by_fy[pk[1]], "flow", {})
            switched.add(pk[1])
            for key, r in out.items():
                k2 = pkey(by_fy[pk[1]], key)
                if k2 in results:
                    results[k2]["revenue"] = r
    # REITs without a "Revenues" total (Camden): rent is tagged only as lease income, and contract
    # revenue is a small extra (15M of 1.6B). Revenue = lease income + contract revenue, same filing.
    if not is_bank_like and industry_of(conn, cik) == "reit":
        lease = tag_series(df, "OperatingLeaseLeaseIncome", cur, "flow", splits)
        for pk, vals in results.items():
            rev = vals.get("revenue")
            if not rev or rev.source != "RevenueFromContractWithCustomerExcludingAssessedTax":
                continue
            t = tot.find(*meta[pk]) if tot is not None else None
            if t and any(v[2] == rev.accn for v in t.versions):
                continue                             # a "Revenues" total exists there: rule above decides
            ls = lease.find(*meta[pk]) if lease.by_end else None
            same = [v for v in (ls.versions if ls else ()) if v[2] == rev.accn]
            if same and same[-1][1] > rev.value:
                vals["revenue"] = replace(rev, value=rev.value + same[-1][1], orig=rev.orig + same[-1][1],
                                          source="RevenueFromContractWithCustomerExcludingAssessedTax + OperatingLeaseLeaseIncome",
                                          versions=())
        # a Q4 worked out as year - 9 months before this: again, from the new numbers
        for pk, vals in results.items():
            q4, fy = vals.get("revenue"), results.get(("FY", pk[1], 0), {}).get("revenue")
            qs = [results.get(("Q", pk[1], k), {}).get("revenue") for k in (1, 2, 3)]
            if pk[0] == "Q" and pk[2] == 4 and q4 and q4.method != "reported" and fy and all(qs) \
                    and "OperatingLeaseLeaseIncome" in fy.source and q4.source != fy.source:
                vals["revenue"] = _combine([fy, *qs], lambda a, b, c, d: a - b - c - d, fy.source, "fy_minus_q")
    if rnfc.by_end and tot is not None:
        for pk, vals in results.items():
            rev = vals.get("revenue")
            if not rev or rev.source != "RevenueFromContractWithCustomerExcludingAssessedTax":
                continue
            s_, e_ = meta[pk]
            t, n = tot.find(s_, e_), rnfc.find(s_, e_)
            if t and n and n.value and not _off(rev.value + n.value, t.value, 0.002) and _off(rev.value, t.value, 0.002):
                vals["revenue"] = t

    # 1a2) capex: other capital spending on its own cash-flow line next to property and equipment, as
    #      most data vendors count it: software bought or built (Hilton, Akamai, DoorDash, FIS), intangible
    #      assets (ADP's capitalised software), equipment for leasing out (Caterpillar, Deere), utility
    #      construction and nuclear fuel (AEP), oil and gas wells (EOG). When such a line is reported for
    #      the same period in the same filing as the property line, capex is their sum.
    base_tags = ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
                 "PaymentsToAcquireOtherPropertyPlantAndEquipment", "PaymentsToAcquireOtherProductiveAssets")
    for extra in CAPEX_EXTRA:
        sw = tag_series(df, extra, cur, "flow", splits)
        for pk, s in pick_values([(extra, sw)], years, "flow").items():
            c = results.get(pk, {}).get("capex")
            if not c or not s.value or s.value < 0 or c.source.split(" + ")[0] not in base_tags or extra in c.source:
                continue
            if extra == "PaymentsToAcquireOilAndGasPropertyAndEquipment" and \
                    c.source.startswith("PaymentsToAcquireProductiveAssets"):
                continue      # "productive assets" is the total that already holds the wells (Chevron 2022)
            if abs(s.value - c.value) <= 0.1 * abs(c.value):
                continue      # about the same amount: the same spending tagged twice (Chevron's oil and gas line)
            same = [v for v in s.versions if v[2] == c.accn] or ([(s.filed, s.value, s.accn)] if s.accn == c.accn else [])
            if not same:
                continue
            first = [v for v in s.versions if v[0] == c.orig_filed]
            results[pk]["capex"] = replace(c, value=c.value + same[-1][1],
                                           orig=c.orig + (first[0][1] if first else same[-1][1]),
                                           source=f"{c.source} + {extra}", versions=())

    # 1a) a total of 0 is a tagging slip, not a number (Bank of America: Assets = 0 for 2008; Coca-Cola's
    #     10-Q/A of Oct 2009: 0 shares outstanding)
    zero_so = []      # periods whose filing gave 0 shares outstanding (filled from the weighted count below)
    for pk, vals in results.items():
        so0 = vals.get("shares_outstanding")
        if so0 and so0.value == 0:
            zero_so.append(pk)
        for k in ("total_assets", "liabilities_and_equity", "revenue", "shares_outstanding"):
            if k in vals and vals[k].value == 0:
                del vals[k]
        # a shell company's 100 or 1,000 shares (before an IPO or merger) would give a market cap of
        # a few thousand dollars once the real company trades (Kraft Heinz July 2015, Baker Hughes 2017)
        for k in ("shares_outstanding", "shares_basic", "shares_diluted"):
            if k in vals and vals[k].value in SHELL_COUNTS:
                del vals[k]
            elif k in vals and vals[k].value < 0:
                x = vals[k]
                if x.method == "reported":     # sign slip (Occidental's 10-Q of May 2016: -891,624,558)
                    vals[k] = replace(x, value=-x.value, orig=abs(x.orig), method="sign_fixed", versions=())
                else:                          # a derived Q4 (year average - quarters) that cannot be right
                    del vals[k]

    # 1b0) weighted share counts against net income / EPS. Some companies tag them in millions in
    #      every filing (McDonald's: 716.4 for 716.4 million), so the median check in
    #      series_from_rows cannot see it. Net income / EPS is the real average share count: a share
    #      count about 1,000x or 1,000,000x off it is scaled back. Done before the shares-outstanding
    #      check below, which uses these counts as its reference.
    #      But when the share count fits the other periods of the same fiscal year (whose own share
    #      count fits their net income / EPS), the EPS is the slip and is scaled instead.
    def _implied(vals, sk, ek):
        ni = vals.get("net_income_common") or vals.get("net_income")
        sh, eps = vals.get(sk), vals.get(ek)
        if not sh or not eps or not ni or not eps.value or not ni.value or sh.value <= 0:
            return None
        imp = ni.value / eps.value
        return (sh, eps, imp) if imp > 0 else None

    def _med(k):
        v = sorted(abs(x[k].value) for x in results.values() if k in x and x[k].value)
        return v[len(v) // 2] if v else None

    def _size_off(x, med):         # how far a value is from the usual size of its item, in factors of 10
        return abs(math.log10(abs(x) / med)) if med and x else 0.0

    meds = {k: _med(k) for k in ("shares_diluted", "shares_basic", "eps_diluted", "eps_basic")}
    fixes = []
    for pk, vals in results.items():
        for sk, ek in (("shares_diluted", "eps_diluted"), ("shares_basic", "eps_basic")):
            got = _implied(vals, sk, ek)
            if not got:
                continue
            sh, eps, implied = got
            r = sh.value / implied
            if 0.2 <= r <= 5:
                continue
            fac = next((f for f in (1e3, 1e6, 1e-3, 1e-6) if 0.8 <= r * f <= 1.25), None)
            if not fac:
                continue
            same_year = [k for k in results if k[1] == pk[1] and k != pk]
            # Halliburton's 10-Qs of 2022-2023 give EPS 1,000,000x too large in every version: the EPS
            # is far from its usual size while the share count is not
            eps_wrong = _size_off(eps.value, meds[ek]) > _size_off(sh.value, meds[sk]) + 1.5
            for k in ([] if eps_wrong else same_year):
                o = _implied(results[k], sk, ek)
                if o and 0.2 <= o[0].value / sh.value <= 5 and 0.2 <= o[0].value / o[2] <= 5:
                    eps_wrong = True
                    break
            fixes.append((pk, ek if eps_wrong else sk, fac))
    for pk, k, fac in fixes:
        x = results[pk][k]
        same = x.orig and x.value and abs(x.orig / x.value - 1) < 0.02     # first version wrong too?
        results[pk][k] = replace(x, value=x.value * fac, orig=x.orig * fac if same else x.orig,
                                 method="scale_fixed", versions=())

    # 1b) share count sanity. Filings sometimes contain a scaling mistake (Apple's 10-Q for
    #     March 2014 reports shares outstanding 1000x too small). Compare with the weighted
    #     average share count of the same period and repair an obvious factor-1000 slip.
    for pk, vals in results.items():
        so = vals.get("shares_outstanding")
        ref = vals.get("shares_basic") or vals.get("shares_diluted")
        if not so or not ref or ref.value <= 0 or so.value <= 0:
            continue
        ratio = so.value / ref.value
        if 0.2 <= ratio <= 5:
            continue
        fix = next((f for f in (1e3, 1e6, 1e-3, 1e-6) if 0.5 <= ratio * f <= 2), None)
        label = f"{'FY' if pk[0] == 'FY' else 'Q' + str(pk[2])} {pk[1]}"
        if fix:
            o_ratio = so.orig / ref.value
            vals["shares_outstanding"] = replace(
                so, value=so.value * fix, orig=so.orig * fix if not 0.2 <= o_ratio <= 5 else so.orig,
                method="scale_fixed", versions=())
            notes.append(("fixed", "shares_outstanding",
                          f"Shares outstanding {label}: the filing has an obvious scaling mistake "
                          f"({fix:g}x too small); corrected" if fix > 1 else
                          f"Shares outstanding {label}: the filing has an obvious scaling mistake "
                          f"({1 / fix:g}x too large); corrected"))
        else:
            del vals["shares_outstanding"]
            notes.append(("warn", "shares_outstanding",
                          f"Shares outstanding {label}: reported value does not fit the average share "
                          f"count ({ratio:.2f}x); left out"))

    # 1b1) shares outstanding given as 0 (Bloom Energy after its IPO, when it only tagged the count per
    #      class): the period's own weighted share count is used, so the market cap does not keep
    #      using an older number (Bloom's pre-IPO 10.6 million shares for a year)
    for pk in zero_so:
        vals = results[pk]
        ref = vals.get("shares_basic") or vals.get("shares_diluted")
        if "shares_outstanding" not in vals and ref and ref.value > 0:
            vals["shares_outstanding"] = replace(ref, method="from_weighted", versions=())

    # 1b1b) shares ISSUED tagged as shares outstanding (Everest, Globe Life, International Paper, Tyler:
    #      the balance sheet count includes shares bought back and held in treasury). When the cover page
    #      of the same filing and the period's average share count agree with each other and the balance
    #      sheet count is more than 10% higher, the cover page count is the real number outstanding.
    for pk, vals in results.items():
        so = vals.get("shares_outstanding")
        ref = vals.get("shares_diluted") or vals.get("shares_basic")
        if not so or so.source != "CommonStockSharesOutstanding" or not ref or ref.value <= 0:
            continue
        dv = cover_by_accn.get(so.accn)
        if dv and abs(dv / ref.value - 1) < 0.05 and so.value > 1.10 * dv:
            vals["shares_outstanding"] = replace(
                so, value=dv, orig=dv if so.orig > 1.10 * dv else so.orig, method="from_cover",
                source="dei:EntityCommonStockSharesOutstanding", versions=())

    # 1b2) per-share dividends: some companies tag the QUARTERLY rate in the annual context too
    #      (Visa, Applied Materials: "dividends declared per share 0.52" for the year). When the year
    #      equals the quarters instead of their sum, the year is the sum of the quarters.
    for pk, vals in results.items():
        fy = vals.get("dps")
        if pk[0] != "FY" or not fy or not fy.value:
            continue
        qs = [results.get(("Q", pk[1], k), {}).get("dps") for k in (1, 2, 3, 4)]
        q123 = [q for q in qs[:3] if q and q.method == "reported"]
        if len(q123) < 3 or not all(abs(q.value - fy.value) <= 0.3 * fy.value for q in q123):
            continue
        q4 = qs[3] if qs[3] and qs[3].method == "reported" else replace(fy, method="fy_rate")
        results[("Q", pk[1], 4)]["dps"] = q4
        vals["dps"] = _combine([*q123, q4], lambda a, b, c, d: a + b + c + d, fy.source, "sum_of_quarters")

    # 1c) a balance sheet must balance. Occasionally a later filing shows a DIFFERENT balance
    #     sheet under an old date (JPMorgan labels its 1 Jan 2010 opening balance, after an
    #     accounting change, as 31 Dec 2009), so the latest versions no longer add up while the
    #     originally reported ones do. Then the original balance sheet is used for that date.
    def _balances(vals, attr):
        g = lambda k: getattr(vals[k], attr) if k in vals else None  # noqa: E731
        a, le = g("total_assets"), g("liabilities_and_equity")
        l, e = g("total_liabilities"), g("total_equity_incl_nci") or g("total_equity")
        t = g("temporary_equity") or 0
        # (some companies' own "total liabilities" already include the mezzanine equity)
        ok = [abs(x - a) <= 0.001 * abs(a) for x in (le, (l + e + t) if l is not None and e is not None else None)
              if a and x is not None]
        if t and l is not None and e is not None and a and abs(l + e - a) <= 0.001 * abs(a):
            ok = [abs(le - a) <= 0.001 * abs(a)] if le is not None else []
        return all(ok) if ok else None
    for pk, vals in results.items():
        if _balances(vals, "value") is False and _balances(vals, "orig"):
            for k, r in list(vals.items()):
                if ITEM_BY_KEY[k]["kind"] in ("instant", "shares_instant") and r.value != r.orig:
                    vals[k] = replace(r, value=r.orig, filed=r.orig_filed, versions=r.versions[:1])
            if pk[0] == "FY":
                notes.append(("fixed", "total_assets",
                              f"Balance sheet FY {pk[1]}: a later filing shows a different balance sheet under "
                              f"this date that does not add up; the balance sheet as originally reported is used"))

    # 1c2) mezzanine equity the company tags only with its own tag (Palantir's pre-IPO preferred
    #      stock, Visa 2008, Tesla's convertible notes): the reported balance sheet balances
    #      (assets = liabilities & equity) but liabilities + equity fall short by a positive amount.
    #      That amount is the line between them.
    for vals in results.values():
        a, le = vals.get("total_assets"), vals.get("liabilities_and_equity")
        l, e = vals.get("total_liabilities"), vals.get("total_equity_incl_nci") or vals.get("total_equity")
        if not (a and le and l and e) or _off(a.value, le.value, 0.001):
            continue
        t = vals.get("temporary_equity")
        gap = a.value - l.value - e.value - (t.value if t else 0)
        if gap > 0.001 * abs(a.value) and not (t and t.value):
            vals["temporary_equity"] = Rec(gap, a.orig - l.orig - e.orig, a.filed, a.orig_filed, a.accn,
                                           "total_assets - total_liabilities - total_equity_incl_nci",
                                           "derived", parts=(a, l, e), fn=lambda x, y, z: x - y - z)

    # 1d) "long-term debt incl. current portion" smaller than the current portion alone: the
    #     company tagged only the non-current part with the total tag (NVIDIA Jan 2016: all debt
    #     was a convertible note due within a year, "LongTermDebt" = 0). Use it as the non-current part.
    for vals in results.values():
        tot, cur_p = vals.get("long_term_debt_total"), vals.get("current_portion_ltd")
        if tot and cur_p and "long_term_debt" not in vals and tot.value < cur_p.value * 0.999:
            vals["long_term_debt"] = vals.pop("long_term_debt_total")

    # 1e) gross profit as the company reports it is the reference. If revenue - cost of revenue
    #     does not give it, either revenue still contains excise taxes (Philip Morris before 2018)
    #     or the cost tag covers only part of the costs (Caterpillar: machinery only); then revenue
    #     without excise, or cost of revenue = revenue - gross profit, is used for that period.
    for pk, vals in results.items():
        rev, cor, gp = vals.get("revenue"), vals.get("cost_of_revenue"), vals.get("gross_profit")
        if not (rev and cor and gp) or gp.source != "GrossProfit" or not _off(rev.value - cor.value, gp.value, 0.005):
            continue
        ex = vals.get("excise_taxes")
        if ex and not _off(rev.value - ex.value - cor.value, gp.value, 0.005):
            vals["revenue"] = eval_formula("revenue - excise_taxes", vals)
        else:
            vals["cost_of_revenue"] = eval_formula("revenue - gross_profit", vals)

    # 1y) REITs: capex = upkeep of the buildings (capital improvements + tenant improvements / leasing
    #     costs) when the REIT reports those lines. Development and buying properties are growth, not
    #     capex, so free cash flow is close to what the REIT itself calls its recurring cash flow.
    #     Purchases of equipment (property, plant and equipment: Weyerhaeuser's mills) stay in.
    if is_reit:
        ppe_tags = ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireOtherPropertyPlantAndEquipment")
        for vals in results.values():
            cap = vals.get("capex")
            ppe = {"ppe": cap} if cap is not None and cap.source in ppe_tags else {}
            r = eval_formula("capital_improvements? + tenant_improvements? + ppe?", {**vals, **ppe})
            if r is not None and (vals.get("capital_improvements") or vals.get("tenant_improvements")):
                src = " + ".join(n for n, k in (("capital improvements", "capital_improvements"),
                                                 ("tenant improvements", "tenant_improvements"),
                                                 ("equipment", "ppe")) if ({**vals, **ppe}).get(k))
                vals["capex"] = replace(r, source=f"{src} (REIT upkeep)")

    # 1z) capex no longer reported: some companies with almost no capital spending drop the line from
    #     their cash-flow statement (AppLovin after 2024: ~$5M a year next to $2B+ operating cash flow).
    #     Then capex = 0 (so free cash flow = operating cash flow), but only when the company did report
    #     capex before, its last reported year was immaterial (at most 1% of operating cash flow) and the
    #     fiscal year in question has no reported capex at all (quarters of a year with a reported annual
    #     capex stay empty: they cannot be split).
    fy_capex = sorted((meta[pk][1], pk[1], v["capex"], v.get("operating_cf")) for pk, v in results.items()
                      if pk[0] == "FY" and v.get("capex"))
    if fy_capex:
        last_end, last_fy, cap, ocf = fy_capex[-1]
        years_with_capex = {fy for _, fy, _, _ in fy_capex}
        if ocf and ocf.value > 0 and abs(cap.value) <= 0.01 * ocf.value:
            for pk, vals in results.items():
                o = vals.get("operating_cf")
                if (pk[0] in ("FY", "Q") and o and "capex" not in vals and meta[pk][0] > last_end
                        and pk[1] not in years_with_capex):
                    vals["capex"] = replace(o, value=0.0, orig=0.0, source="not reported (immaterial, taken as 0)",
                                            method="derived", versions=(), parts=(), fn=None)

    # 2) formulas (fallbacks and always-derived items); a few passes for dependencies
    for pk, vals in results.items():
        for _ in range(3):
            for item in ITEMS:
                if item["key"] in vals:
                    continue
                for f in formulas_of(item):
                    r = eval_formula(f, vals)
                    if r is not None:
                        vals[item["key"]] = r
                        break

    # 2n) minority-interest income = profit incl. minority interest - profit for the shareholders.
    #     Companies with several kinds of minority owners (Blackstone, KKR: holdings partners,
    #     redeemable and other) tag only one of them; when both totals come from the SAME filing and
    #     the tag does not fit, their difference is used.
    for vals in results.values():
        tot, ni, nci = vals.get("net_income_incl_nci"), vals.get("net_income"), vals.get("nci_income")
        if not (tot and ni and nci) or tot.method != "reported" or ni.method != "reported":
            continue
        if tot.accn and tot.accn == ni.accn and _off(ni.value + nci.value, tot.value, 0.01):
            vals["nci_income"] = eval_formula("net_income_incl_nci - net_income", vals)

    # 2p) per-share figures of a period still in another share class's units (Berkshire's quarters of
    #     2010-2012 per class A share, 1500x the class B figure): net income / EPS then gives a share count
    #     about one class ratio away from the year's. EPS is divided and the share count multiplied by it.
    class_ratios = [float(x) for x in re.findall(r"= ([\d.]+) ", cls_note[2])] if cls_note else []
    class_ratios = [r for r in class_ratios if r >= 5 or r <= 0.2]
    for pk, vals in results.items():
        if pk[0] != "Q" or not class_ratios:
            continue
        fy = results.get(("FY", pk[1], 0), {})
        fy_sh = fy.get("shares_diluted") or fy.get("shares_basic")
        ni = vals.get("net_income_common") or vals.get("net_income")
        if not fy_sh or not fy_sh.value or not ni or not ni.value:
            continue
        for ek, sk in (("eps_basic", "shares_basic"), ("eps_diluted", "shares_diluted")):
            e = vals.get(ek)
            if not e or not e.value:
                continue
            implied = ni.value / e.value
            f = next((r for r in class_ratios if 0.8 <= implied * r / fy_sh.value <= 1.25), None)
            if not f:
                continue
            vals[ek] = replace(e, value=e.value / f, orig=e.orig / f, method="class_converted", versions=(), parts=(), fn=None)
            sh = vals.get(sk)
            if sh and sh.value and 0.8 <= sh.value * f / fy_sh.value <= 1.25:
                vals[sk] = replace(sh, value=sh.value * f, orig=sh.orig * f, method="class_converted", versions=(), parts=(), fn=None)

    # 2q) Q4 EPS: not additive, so it cannot be "year minus 9 months". When the 10-K gives no Q4
    #     figure: Q4 shares = 4 x the year's average - Q1 - Q2 - Q3 (quarters: reported, else net
    #     income / EPS), Q4 EPS = Q4 net income to common / Q4 shares. The year's average share count
    #     falls back to basic shares or net income / EPS (Smucker, Tesla, ServiceNow tag no diluted
    #     count). Kept when the four quarters' EPS add up to the year's EPS within 5% (EPS is not
    #     additive, but close); if not, the Q3 count and then the year's count are tried the same way.
    #     When that check cannot be made, only a computed Q4 count in line with the other quarters.
    for pk, vals in results.items():
        if pk[0] != "FY":
            continue
        q = [results.get(("Q", pk[1], k)) for k in (1, 2, 3, 4)]
        if not all(x is not None for x in q) or q[3].get("eps_diluted"):
            continue
        fy_eps, ni4 = vals.get("eps_diluted"), q[3].get("net_income_common")
        fy_sh = vals.get("shares_diluted") or vals.get("shares_basic")
        # (a year's EPS is large enough to divide by from 10 cents: ServiceNow 2014 -0.25 after its split)
        if fy_sh is None and fy_eps and vals.get("net_income_common") and abs(fy_eps.value) >= 0.1:
            fy_sh = eval_formula("net_income_common / eps_diluted", vals)
        if not (fy_sh and ni4) or not fy_sh.value or fy_sh.value <= 0:
            continue
        can_tie = bool(fy_eps and fy_eps.value and all(x.get("eps_diluted") for x in q[:3]))

        def ties(eps4):     # the four quarters' EPS add up to the year's (5%, at least a cent: rounding)
            total = sum(x["eps_diluted"].value for x in q[:3]) + eps4.value
            return abs(total - fy_eps.value) <= max(0.05 * abs(fy_eps.value), 0.01)

        sh = []
        for x in q[:3]:
            r = x.get("shares_diluted")
            if r is None and x.get("eps_diluted") and x.get("net_income_common") \
                    and abs(x["eps_diluted"].value) >= 0.25:
                r = eval_formula("net_income_common / eps_diluted", x)
            sh.append(r)
        if not all(r is not None and r.value and r.value > 0 for r in sh):
            # a quarter's share count is unknown (no count tagged and EPS too small to divide by:
            # ServiceNow, Smucker): Q4 net income / the year's average count, if the sum then ties
            if can_tie:
                e = _combine([ni4, fy_sh], lambda a, b: a / b, "net_income_common / shares_diluted (year)", "derived")
                if ties(e):
                    q[3]["eps_diluted"] = e
            continue
        ref = [r.value for r in sh] + [fy_sh.value]
        in_line = lambda v: 0.85 * min(ref) <= v <= 1.15 * max(ref)  # noqa: E731
        # candidates for the Q4 share count, best first; with the year's EPS to check against, the first
        # whose EPS makes the four quarters add up wins (Smucker 2023: the year's average works, the
        # computed Q4 count does not); without it, only a computed count in line with the quarters
        cands = []
        sh4 = _combine([fy_sh, *sh], lambda a, b, c, d: 4 * a - b - c - d, "shares_diluted", "fy_avg_minus_q")
        if sh4.value > 0:
            cands.append((sh4, "net_income_common / shares_diluted"))
        cands += [(sh[2], "net_income_common / shares_diluted (Q3)"),
                  (fy_sh, "net_income_common / shares_diluted (year)")]
        for c_sh, src in cands:
            e = _combine([ni4, c_sh], lambda a, b: a / b, src, "derived")
            if ties(e) if can_tie else (c_sh is sh4 and in_line(sh4.value)):
                q[3]["eps_diluted"] = e
                if c_sh is sh4:     # an estimate from Q3 or the year is not shown as Q4's share count
                    q[3].setdefault("shares_diluted", sh4)
                break

    # 2a) pretax income that does not tie: net income = pretax income - tax (+ discontinued
    #     operations, extraordinary items, equity-method income). If the chosen annual figure breaks
    #     that, but another figure the company filed for the same year (another tag or another
    #     filing) makes it tie exactly, that one is used (Lilly's 10-K of 2013 tags its 2011 and 2012
    #     pretax income with an unrelated number under the first tag). A derived Q4 follows.
    def _ni_gap(vals, pretax):
        tax = vals.get("income_tax")
        tgt = vals.get("net_income_incl_nci") or vals.get("net_income")
        if not tax or not tgt or not tgt.value:
            return None
        z = lambda k: vals[k].value if k in vals else 0  # noqa: E731
        base = pretax - tax.value + z("discontinued_ops") + z("extraordinary_items")
        targets = [tgt.value] + ([vals["net_income"].value] if "net_income" in vals else [])
        return min(abs(b - t) / abs(t) for b in (base, base + z("equity_method_income")) for t in targets if t)
    for pk, vals in results.items():
        pt = vals.get("pretax_income")
        if pk[0] != "FY" or not pt or pt.method != "reported":
            continue
        gap = _ni_gap(vals, pt.value)
        if gap is None or gap < 0.01:
            continue
        s_, e_ = meta[pk]
        alts = []
        for c, ser in cand_series.get("pretax_income", []):
            r = ser.find(s_, e_)
            for v in (r.versions if r else ()):
                g = _ni_gap(vals, v[1])
                if g is not None and g < 0.001:
                    alts.append((v, c))
        if not alts:
            continue
        (filed, val, accn), c = max(alts, key=lambda x: x[0][0])        # latest such filing
        old = pt.value
        vals["pretax_income"] = Rec(val, val, filed, min(v[0][0] for v in alts), accn, c, "reported",
                                    tuple(sorted({v[0] for v in alts})))
        q4 = results.get(("Q", pk[1], 4), {})
        qs = [results.get(("Q", pk[1], k), {}).get("pretax_income") for k in (1, 2, 3)]
        # the quarters from the same line as the year (Keysight, Agilent file two pretax lines every quarter;
        # mixing them gives a Q4 of year minus three quarters of the other line)
        ser = dict(cand_series.get("pretax_income", [])).get(c)
        if ser is not None and all(qs) and any(x.source != c for x in qs):
            same = [ser.find(*meta[("Q", pk[1], k)]) if ("Q", pk[1], k) in meta else None for k in (1, 2, 3)]
            if all(same):
                for k, r in zip((1, 2, 3), same):
                    results[("Q", pk[1], k)]["pretax_income"] = r
                qs = same
        if q4.get("pretax_income") and q4["pretax_income"].method != "reported" and all(qs):
            q4["pretax_income"] = _combine([vals["pretax_income"], *qs], lambda a, b, c, d: a - b - c - d,
                                           c, "fy_minus_q")
        notes.append(("fixed", "pretax_income",
                      f"Pretax income FY {pk[1]}: the company filed {old / 1e6:,.0f}M and {val / 1e6:,.0f}M for "
                      f"this year; {val / 1e6:,.0f}M is used (it agrees with net income and tax)"))

    # 2b) bank-only lines (deposits, net interest income, ...) are kept only for banks
    fy_keys = sorted(pk for pk in results if pk[0] == "FY" and results[pk])
    bank = bool(fy_keys) and all(m in results[fy_keys[-1]] for m in BANK_MARKERS)
    # for a bank the generic interest lines are replaced by the bank's total interest lines
    drop = list(BANK_ITEMS if not bank else ("interest_income", "interest_expense", "ebitda"))
    # insurer / REIT lines only for those; an insurer has no gross profit or operating income
    industry = None if bank else industry_of(conn, cik)
    drop += [k for k in INSURER_ITEMS if industry != "insurer"]
    drop += [k for k in REIT_ITEMS if industry != "reit"]
    if industry == "insurer":
        drop += NOT_FOR_INSURERS
    # health insurers (UnitedHealth, Elevance, Cigna) report an operating income line of their own: keep it
    own_oi = industry == "insurer" and any(
        (v.get("operating_income") is not None and v["operating_income"].method == "reported") for v in results.values())
    for vals in results.values():
        for k in drop:
            if own_oi and k in ("operating_income", "operating_expenses", "ebitda"):
                continue
            vals.pop(k, None)
        # a bank's revenue = net interest income + noninterest income. Used for years without a
        # total tag, and instead of a tag that is clearly something else (American Express tags
        # only its fee revenue as "revenue from contracts with customers")
        if bank and vals.get("noninterest_income") and vals["noninterest_income"].source != "revenue - net_interest_income":
            r = eval_formula("net_interest_income + noninterest_income", vals)
            rev = vals.get("revenue")
            if r is not None and (rev is None or _off(rev.value, r.value, 0.02)):
                vals["revenue"] = r
    standardize_company.is_bank = bank
    standardize_company.industry = "bank" if bank else industry

    # 2r) a derived Q4 (a formula, e.g. operating expenses = gross profit - operating income, or EBITDA) can be
    #     built from other inputs than the year, so the four quarters do not add up to the year. Then Q4 =
    #     year - Q1 - Q2 - Q3, as data vendors do: the quarters and TTM then agree with the annual report.
    #     Not for bank revenue: a bank's year can be gross "Revenues" while its quarters are net interest
    #     income + fee income, and subtracting one from the other mixes them (Truist Q4 2022 halved).
    bank_like = any(all(m in v for m in BANK_MARKERS) for pk, v in results.items() if pk[0] == "FY")
    flows = [i["key"] for i in ITEMS if i["kind"] == "flow" and i["key"] != "net_income_common"
             and not (bank_like and i["key"] == "revenue")]
    for pk, vals in results.items():
        if pk[0] != "FY":
            continue
        q = [results.get(("Q", pk[1], k)) for k in (1, 2, 3, 4)]
        if not all(x is not None for x in q):
            continue
        for k in flows:
            fy, q4, q123 = vals.get(k), q[3].get(k), [x.get(k) for x in q[:3]]
            if not (fy and q4 and all(q123)) or q4.method != "derived":
                continue
            total = q4.value + sum(r.value for r in q123)
            if abs(total - fy.value) <= max(2.5e6, 0.001 * abs(fy.value)):
                continue
            q[3][k] = _combine([fy, *q123], lambda a, b, c, d: a - b - c - d, fy.source, "fy_minus_q")

    # 3) TTM from 4 consecutive quarters
    quarters = sorted((pk for pk in results if pk[0] == "Q"), key=lambda pk: meta[pk][1])
    for i in range(3, len(quarters)):
        four = quarters[i - 3:i + 1]
        if any(abs(meta[b][0] - (meta[a][1] + DAY)) > TOL for a, b in zip(four, four[1:])):
            continue
        ttm = {}
        for item in ITEMS:
            k, kind = item["key"], item["kind"]
            recs = [results[q].get(k) for q in four]
            if kind in ("instant", "shares_instant"):
                if recs[-1]:
                    ttm[k] = replace(recs[-1], method="ttm")
                continue
            if not all(recs):
                continue
            fn = (lambda *v: sum(v) / 4) if kind == "shares" else (lambda *v: sum(v))
            ttm[k] = _combine(recs, fn, recs[-1].source, "ttm")
        last = quarters[i]
        pk = ("TTM", last[1], last[2])
        results[pk] = ttm
        meta[pk] = (meta[four[0]][0], meta[last][1])

    # 3a) TTM for the quarters of a transition period, which do not follow 4 consecutive quarters (FedEx:
    #     June 2026 alone sits between May and July): the year before + the transition period so far
    #     - the same months a year earlier (FedEx Sep 2026: year to May 2026 + Jun-Sep 2026 - Jun-Sep 2025)
    for pk in [p for p in results if p[0] == "Q" and p[2] >= 5]:
        fy_pk = ("FY", pk[1], 0)
        tk = ("TTM", pk[1], pk[2])
        if fy_pk not in results or (tk in results and "revenue" in results[tk]):
            continue
        s1, qe = meta[fy_pk][1] + DAY, meta[pk][1]
        ttm = results.setdefault(tk, {})
        meta.setdefault(tk, (qe - pd.DateOffset(years=1) + DAY, qe))
        for item in ITEMS:
            k, kind = item["key"], item["kind"]
            if kind in ("instant", "shares_instant"):
                if k in results[pk]:
                    ttm.setdefault(k, replace(results[pk][k], method="ttm"))
                continue
            F = results[fy_pk].get(k)
            if kind != "flow" or k in ttm or F is None:
                continue
            for _, ser in cand_series.get(k, []):
                a = ser.find(s1, qe)
                b = ser.find(s1 - pd.DateOffset(years=1), qe - pd.DateOffset(years=1))
                if a and b:
                    ttm[k] = _combine([F, a, b], lambda f, x, y: f + x - y, a.source, "ttm")
                    break
        # diluted / basic shares: those of the latest quarter (a TTM average is not available here),
        # so EPS = net income to common / shares
        for k in ("shares_diluted", "shares_basic"):
            src = results[pk].get(k) or results[fy_pk].get(k)
            if src and k not in ttm:
                ttm[k] = replace(src, method="ttm")
        for _ in range(3):
            for item in ITEMS:
                if item["key"] not in ttm:
                    for f in formulas_of(item):
                        r = eval_formula(f, ttm)
                        if r is not None:
                            ttm[item["key"]] = r
                            break
        if not ttm:
            del results[tk], meta[tk]

    # 3b) companies that report only once a year (foreign filers on 20-F / 40-F, e.g. ASML: their
    #     quarterly results are press releases without XBRL) have no quarters, so no TTM. The trailing
    #     twelve months at a year end are that year, so TTM = FY: key stats, TTM charts, the screener
    #     and the multiples then work for them too. Only when no quarter has any flow item at all.
    flows = {it["key"] for it in ITEMS if it["kind"] not in ("instant", "shares_instant")}
    if not any(flows & set(v) for pk, v in results.items() if pk[0] == "Q"):
        for pk in [p for p in results if p[0] == "FY"]:
            tk = ("TTM", pk[1], 4)
            ttm = results.setdefault(tk, {})
            meta.setdefault(tk, meta[pk])
            for k, r in results[pk].items():
                if k in flows and k not in ttm:
                    ttm[k] = replace(r, method="ttm")

    # 4) write. prior_year_value = the same period one year earlier AS IT WAS KNOWN when this
    #    period was first published (i.e. the comparison column of that same filing). Growth
    #    computed as original_value / prior_year_value is always like-for-like, also across
    #    accounting changes where a company restates the year but not its quarters.
    # Statements in another currency (foreign filers: ASML in euros) are converted to US dollars, so every
    # company is in dollars (and its US listing trades in dollars): amounts over a period at the average
    # exchange rate of that period, balance-sheet amounts at the rate on the balance-sheet date. Without
    # any exchange rate (offline, first time) they stay in their own currency; the valuation converts then.
    money = {"flow", "instant", "eps", "per_share"}
    fx = None
    if cur != "USD":
        from . import metrics
        fx = metrics.fx_history(conn, cur)
    def rate(pk, kind):
        if fx is None or kind not in money or pk not in meta:
            return 1.0
        start, end = meta[pk]
        if kind != "instant" and start is not None and not pd.isna(start):
            span = fx[(fx.index >= start) & (fx.index <= end)]
            if len(span) >= 5:
                return float(span.mean())
        before = fx[fx.index <= end]
        return float(before.iloc[-1] if len(before) else fx.iloc[0])
    out_unit = dict(unit_for)
    if fx is not None:
        out_unit.update(flow="USD", instant="USD", eps="USD/shares", per_share="USD/shares")

    rows = []
    for pk, vals in results.items():
        ptype, fy, fq = pk
        start, end = meta[pk]
        prior_vals = results.get((ptype, fy - 1, fq), {})
        for k, r in vals.items():
            item = ITEM_BY_KEY[k]
            is_instant = item["kind"] in ("instant", "shares_instant")
            prior = prior_vals.get(k)
            prior_value = version_at(prior, r.orig_filed).value if prior else None
            fxr = rate(pk, item["kind"])
            if prior_value is not None:
                prior_value *= rate((ptype, fy - 1, fq), item["kind"])
            rows.append((
                cik, k, ptype, fy, fq or None, calendar_quarter(end),
                None if is_instant else start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"),
                r.value * fxr if r.value is not None else None, r.orig * fxr if r.orig is not None else None, out_unit[item["kind"]], r.source, r.method,
                int(r.method != "reported"), r.filed, r.orig_filed, r.accn, prior_value))
    if not any(r[8] is not None for r in rows) and conn.execute(
            "SELECT 1 FROM financials WHERE cik=? AND value IS NOT NULL LIMIT 1", (cik,)).fetchone():
        # nothing came out (SEC data incomplete, a download that failed half-way): keep the numbers we have
        # rather than replacing a full history with an empty one
        raise RuntimeError("no values from the SEC data this time - the numbers already here are kept")
    conn.execute("DELETE FROM share_counts WHERE cik=?", (cik,))
    conn.executemany("INSERT OR REPLACE INTO share_counts (cik, date, filed, value) VALUES (?,?,?,?)",
                     [(cik, end.strftime("%Y-%m-%d"), rec.orig_filed, rec.orig)
                      for end, lst in cover.by_end.items() for _, rec in lst[:1]
                      if rec.orig and rec.orig > 0 and rec.orig not in SHELL_COUNTS])
    conn.execute("DELETE FROM financials WHERE cik=?", (cik,))
    conn.executemany(
        "INSERT INTO financials (cik, item, period_type, fiscal_year, fiscal_quarter, calendar_quarter, "
        "start_date, end_date, value, original_value, unit, source, method, is_derived, filed, "
        "original_filed, accn, prior_year_value) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    standardize_company.notes = notes
    return len(rows)


def save_catalog(conn):
    conn.execute("DELETE FROM standard_items")
    conn.executemany(
        "INSERT INTO standard_items (item, label, statement, kind, sort_order, tags, formulas) "
        "VALUES (?,?,?,?,?,?,?)",
        [(i["key"], i["label"], i["statement"], i["kind"], n, " | ".join(i["tags"]),
          " | ".join(i["formulas"])) for n, i in enumerate(ITEMS)])
    conn.commit()


def run(conn, tickers, fetch_raw=True):
    """fetch_raw=False (recalculating everything): a company from a starter pack is not downloaded again
    (that would take hours for the whole pack); only its ratios and valuation are recalculated."""
    from . import metrics, quality, segments, starter
    save_catalog(conn)
    slim_done = 0
    for t in tickers:
        t = t.upper()
        row = conn.execute("SELECT cik FROM companies WHERE ticker=?", (t,)).fetchone()
        if not row:
            print(f"{t}: not in database - run fetch first")
            continue
        cik = row[0]
        if not fetch_raw and starter.is_slim(conn, cik):
            try:
                metrics.compute_metrics(conn, cik)
                metrics.compute_valuation(conn, cik, t)
                slim_done += 1
            except Exception as e:  # noqa: BLE001
                print(f"{t}: ratios failed: {e}")
            continue
        try:
            starter.ensure_raw(conn, t, cik)        # company from a starter pack: get its raw SEC data first
        except Exception as e:  # noqa: BLE001
            print(f"{t}: skipped, numbers kept as they are - {e}")
            continue
        try:
            n = standardize_company(conn, cik, t)
            m = metrics.compute_metrics(conn, cik)
            v = metrics.compute_valuation(conn, cik, t)
            s = segments.standardize_segments(conn, cik)
            print(f"{t}: {n} financial values, {m} metric values, {v} daily valuation rows, "
                  f"{s} segment values")
            from . import mcapcheck
            mc = mcapcheck.check(conn, t)
            if mc and mcapcheck.warning_text(mc):
                print(f"{t}: market cap warning - {mcapcheck.warning_text(mc)}")
            from . import accuracy
            accuracy.refresh_ours(conn, t, cik)
            qr = quality.assess(conn, cik, getattr(standardize_company, "notes", ()))
            if qr:
                print(f"{t}: data quality {qr[0]}% ({qr[1] - qr[2]} of {qr[1]} checks passed)")
            db.log(conn, t, "standardize", "ok", n)
        except Exception as e:  # noqa: BLE001
            print(f"{t}: standardize failed: {e}")
            db.log(conn, t, "standardize", "error", None, str(e)[:500])
    if slim_done:
        print(f"\n{slim_done} companies from the starter pack: ratios and valuation recalculated. Their raw SEC "
              f"data is not on this PC, so their statements are rebuilt when they get a new filing "
              f"(or recalculate one fully with: python -m stockterminal.standardize TICKER).")


if __name__ == "__main__":
    # no tickers given = recalculate every loaded company (no downloads, takes seconds per company)
    _conn = db.connect()
    if sys.argv[1:]:
        run(_conn, sys.argv[1:])
    else:
        run(_conn, [t for (t,) in _conn.execute("SELECT ticker FROM companies ORDER BY ticker")], fetch_raw=False)
