"""Standardized revenue / operating income by business segment, product and geography.

Uses the same period logic as the main statements (standalone quarters, Q4 = FY - 9 months,
TTM, latest restated value + original value).

Members that were renamed are merged when it is obvious: same XBRL member name, or the same
label after ignoring case/punctuation. The company's latest label is shown.

`in_partition` marks, per period, the members that together add up to total revenue.
Some breakdowns contain overlapping cuts, e.g. Apple's product axis has both
"Products" (a subtotal) and iPhone/Mac/iPad/...; Microsoft's has both Product/Service and a
detailed split. The largest set of members that sums to total revenue is the main split.
"""
import itertools
import re
from dataclasses import replace

import pandas as pd

from .mapping import ITEM_BY_KEY
from .standardize import (build_calendar, pkey, calendar_quarter, currency_of, load_facts, periods_of,
                          pick_values, series_from_rows, ttm_from_quarters)

SEGMENT_ITEMS = {
    "revenue": [t for t in ITEM_BY_KEY["revenue"]["tags"] if "+" not in t],
    "operating_income": ["OperatingIncomeLoss"],
    "gross_profit": ["GrossProfit"],
}
ADJ_EBITDA = re.compile(r"adjusted.*(ebitda|earningsbeforeinterest)", re.I)
EXACT = 0.0005      # 0.05%: a set of members "adds up" to the total


def _key(label):
    return re.sub(r"[^a-z0-9]", "", label.lower())


def _groups(g):
    """Union members that share an XBRL name or a normalized label -> {member: group_id}."""
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            x = parent[x]
        return x

    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            parent[a] = b

    for m, lab in g[["member", "member_label"]].drop_duplicates().itertuples(index=False):
        union("m:" + m, "l:" + _key(lab))
    # Renamed members (e.g. "Gaming" -> "Xbox"): a later filing restates earlier periods under the
    # new name, so old and new member report identical values for the same periods - but they
    # never appear together in one filing (a subtotal and its only child would).
    filings_of = g.groupby("member").accn.agg(set).to_dict()
    same = (g[g.value != 0].groupby(["concept", "start_date", "end_date", "value"]).member
            .agg(lambda s: tuple(sorted(set(s)))))
    pairs = {}
    for ms in same[same.map(len) == 2]:
        pairs[ms] = pairs.get(ms, 0) + 1
    for (a, b), n in pairs.items():
        if n >= 2 and not (filings_of[a] & filings_of[b]):
            union("m:" + a, "m:" + b)
    # Renamed with a small restatement (Apple: "Other Products" -> "Wearables, Home and Accessories",
    # FY2017 12.863B in the 2017 10-K, 12.826B in the 2019 10-K): a later filing reports the same
    # period with exactly one member swapped for another. All other members of the earlier filing
    # are in the later one (at least two of them; small restatements between them are fine) and
    # add up, with the new member, to the same total; the swapped values are within 1%, the two
    # members never appear in one filing and the old one is no longer used once the new one is
    # (no filing with the old name after the first filing with the new one). Values that are merely close (two regions, two segments of similar size)
    # never pass: one of them would have to vanish while the rest still adds up to the same total.
    rev = g[g.concept.str.split(":").str[-1].isin(SEGMENT_ITEMS["revenue"]) & (g.value != 0)]
    per = {}                                          # (start, end) -> [(filed, accn, {member: value})]
    for (s_, e_, accn), grp in rev.groupby(["start_date", "end_date", "accn"]):
        per.setdefault((s_, e_), []).append((grp.filed.max(), accn, grp.groupby("member").value.last().to_dict()))
    first_filed = rev.groupby("member").filed.min().to_dict()
    last_filed = rev.groupby("member").filed.max().to_dict()
    swaps = {}                                        # (old, new) -> periods where the swap is seen
    for period, lst in per.items():
        for (fa, _, va), (fb, _, vb) in itertools.combinations(sorted(lst), 2):
            if fa == fb:
                continue
            gone, common = set(va) - set(vb), set(va) & set(vb)
            if len(gone) != 1 or len(common) < 2:
                continue
            old = next(iter(gone))
            cands = [m for m in set(vb) - set(va)
                     if abs(vb[m] - va[old]) <= 0.01 * max(abs(vb[m]), abs(va[old]))]
            if len(cands) != 1:
                continue
            new = cands[0]
            # same members otherwise, adding up to the same total in both filings
            tot_a, tot_b = sum(va.values()), sum(vb[m] for m in common | {new})
            if abs(tot_a - tot_b) > EXACT * abs(tot_a):
                continue
            if filings_of[old] & filings_of[new] or last_filed[old] > first_filed[new]:
                continue
            swaps.setdefault((old, new), set()).add(period)
    for (old, new), periods in swaps.items():
        if len(periods) >= 2:                         # one period can be a coincidence
            union("m:" + old, "m:" + new)
    return {m: find("m:" + m) for m in g.member.unique()}


def best_partition(values, total):
    """Largest set of members whose sum equals total (within EXACT); else closest set."""
    names = list(values)
    if not names or not total:
        return set(names)
    if len(names) > 16:            # too many to search; assume all members partition
        return set(names)
    best, best_diff = None, None
    exact = []
    for r in range(1, len(names) + 1):
        for combo in itertools.combinations(names, r):
            d = abs(sum(values[n] for n in combo) - total) / abs(total)
            if d <= EXACT:
                exact.append(combo)
            if best_diff is None or d < best_diff:
                best, best_diff = combo, d
    if exact:
        return set(max(exact, key=len))
    return set(best)


def _basis_value(v, pk, basis):
    """Value of period pk on the chosen basis. As first reported, Q4 = FY - (Q1+Q2+Q3) using
    the originally reported figures (all from the same year's own 10-Qs and 10-K)."""
    r = v.get(pk)
    if r is None:
        return None
    if basis == "latest":
        return r.value
    if pk[0] == "Q" and pk[2] == 4 and r.method != "reported":
        fy = v.get(("FY", pk[1], 0))
        q = [v.get(("Q", pk[1], i)) for i in (1, 2, 3)]
        if fy and all(q):
            return fy.orig - sum(x.orig for x in q)
    return r.orig


def standardize_segments(conn, cik):
    df = load_facts(conn, cik)
    years = build_calendar(df)
    cur = currency_of(df)
    meta = {}
    for y in years:
        for key, se in periods_of(y).items():
            meta[pkey(y, key)] = se

    sf = pd.read_sql_query("SELECT * FROM segment_facts WHERE cik=? AND ndims=1 AND unit=?",
                           conn, params=(cik, cur))
    if sf.empty:
        conn.execute("DELETE FROM segment_financials WHERE cik=?", (cik,))
        return 0
    sf["local"] = sf.concept.str.split(":").str[-1]
    total_rev = {(pt, fy, int(fq or 0)): v for pt, fy, fq, v in conn.execute(
        "SELECT period_type, fiscal_year, fiscal_quarter, value FROM financials "
        "WHERE cik=? AND item='revenue'", (cik,))}
    # Statements of a foreign company in another currency were converted to US dollars (standardize):
    # segments are matched to the total in the company's own currency and written in dollars, at the
    # same rate (the average of each period).
    rate = lambda pk: 1.0  # noqa: E731
    in_usd = conn.execute("SELECT 1 FROM financials WHERE cik=? AND item='revenue' AND unit='USD' LIMIT 1",
                          (cik,)).fetchone()
    if cur != "USD" and in_usd:
        from . import metrics
        fx = metrics.fx_history(conn, cur)
        if fx is None:
            conn.execute("DELETE FROM segment_financials WHERE cik=?", (cik,))
            conn.commit()
            return 0
        rate = lambda pk: metrics.period_rate(fx, *meta[pk]) if pk in meta else None  # noqa: E731
        total_rev = {pk: (v / rate(pk) if rate(pk) else None) for pk, v in total_rev.items()}

    # Many companies judge their segments on "Adjusted EBITDA" (their own, non-GAAP measure, e.g.
    # Uber) instead of operating income. Kept as a separate line, never mixed with operating income.
    seg_items = dict(SEGMENT_ITEMS)
    adj = sorted({c for c in sf.local.unique() if ADJ_EBITDA.search(c)})
    if adj:
        seg_items["adjusted_ebitda"] = adj

    out = {}   # (breakdown, gid) -> dict(label, values={item: {pk: (Rec, (start,end))}})
    for bd, g in sf.groupby("breakdown"):
        gmap = _groups(g)
        g = g.assign(gid=g.member.map(gmap))
        for gid, gg in g.groupby("gid"):
            label = gg.sort_values(["filed", "end_date"]).member_label.iloc[-1]
            vals = {}
            for item, concepts in seg_items.items():
                sers = [(c, series_from_rows(gg[gg.local == c], "flow", [], c)) for c in concepts]
                picked = pick_values(sers, years, "flow")
                if not picked:
                    continue
                vals[item] = {pk: r for pk, r in picked.items()}
            if vals:
                out[(bd, gid)] = dict(label=label, key=_key(label), values=vals)

    # Per period, choose the basis (latest restated vs. as first reported) whose members add up
    # to the company's total revenue for that period, then mark the members of the main split.
    # (Segment figures are often restated by later filings while the quarterly company total
    # was not, or the other way round; the company total is the anchor.)
    in_part = {}
    for bd in {k[0] for k in out}:
        members = [k for k in out if k[0] == bd]
        rev = {k: out[k]["values"].get("revenue", {}) for k in members}
        pks = {pk for k in members for pk in rev[k]}
        fy_sets, q3_sets = {}, {}
        for pk in sorted(pks, key=lambda p: (p[0] != "FY", p)):   # full years first
            recs = {k: rev[k][pk] for k in members if pk in rev[k]}
            total = total_rev.get(pk)
            candidates = []
            for basis in ("latest", "first_reported"):
                vals = {k: _basis_value(rev[k], pk, basis) for k in recs}
                vals = {k: v for k, v in vals.items() if v is not None}
                fy_set = fy_sets.get(("FY", pk[1]), set())
                q3_set = q3_sets.get(pk[1], set())
                if pk[0] == "Q" and pk[2] == 4 and fy_set and fy_set <= set(vals):
                    pool = {k: v for k, v in vals.items() if k in fy_set}
                elif pk[0] == "Q" and pk[2] == 4 and q3_set and q3_set <= set(vals):
                    pool = {k: v for k, v in vals.items() if k in q3_set}   # same members as Q3
                else:
                    attr = "filed" if basis == "latest" else "orig_filed"
                    last = max(getattr(r, attr) for r in recs.values())
                    pool = {k: v for k, v in vals.items() if getattr(recs[k], attr) == last}
                chosen = best_partition(pool, total)
                d = abs(sum(pool[k] for k in chosen) - total) / abs(total) if total else 0
                candidates.append((d, basis, chosen, vals))
            d_l, d_o = candidates[0][0], candidates[1][0]
            d, basis, chosen, vals = candidates[1] if d_o < d_l - EXACT else candidates[0]
            if basis == "first_reported":
                for k in members:
                    for item, v in out[k]["values"].items():
                        if pk in v:
                            nv = _basis_value(v, pk, basis)
                            if nv is not None:
                                v[pk] = replace(v[pk], value=nv, method=v[pk].method + "+as_first_reported")
            if pk[0] == "FY":
                fy_sets[("FY", pk[1])] = chosen
            if pk[0] == "Q" and pk[2] == 3:
                q3_sets[pk[1]] = chosen
            for k in chosen:
                in_part[(k, pk)] = 1

    # TTM from the (possibly re-based) quarters
    for k, d in out.items():
        for item, v in d["values"].items():
            q = {pk: r for pk, r in v.items() if pk[0] == "Q"}
            for pk, (r, se) in ttm_from_quarters(q, meta).items():
                v[pk] = r
                last_q = ("Q", pk[1], pk[2])
                four = sorted(q, key=lambda p: meta[p][1])
                idx = four.index(last_q)
                if all(in_part.get((k, p)) for p in four[idx - 3:idx + 1]):
                    in_part[(k, pk)] = 1
                meta[pk] = se

    rows = []
    for (bd, gid), d in out.items():
        for item, v in d["values"].items():
            for pk, r in v.items():
                start, end = meta[pk]
                fxr = rate(pk) or 1.0
                rows.append((cik, bd, d["key"], d["label"], in_part.get(((bd, gid), pk), 0), item,
                             pk[0], pk[1], pk[2] or None, calendar_quarter(end),
                             start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"),
                             r.value * fxr, r.orig * fxr if r.orig is not None else None, r.source, r.method, r.filed))
    conn.execute("DELETE FROM segment_financials WHERE cik=?", (cik,))
    conn.executemany(f"INSERT OR REPLACE INTO segment_financials VALUES ({','.join('?' * 17)})", rows)
    conn.commit()
    return len(rows)
