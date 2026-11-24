"""Data-quality score per company, refreshed on every load.

Runs the consistency checks from validate.py on the standardized numbers and stores a summary
in the `data_quality` table, so the app can show how far the data can be trusted and why.
"""
import json
import re

import pandas as pd

from . import db

# check name in validate.py -> plain-language description
PLAIN = {
    "A = L&E (reported)": "Balance sheet does not balance (assets vs liabilities + equity)",
    "A = L + E incl. NCI": "Balance sheet does not balance (assets vs liabilities + equity)",
    "Q1..Q4 = FY": "The four quarters do not add up to the full year",
    "GP = Rev - COGS": "Gross profit is not revenue minus cost of revenue",
    "NI (IS) = NI (cash flow)": "Net income differs between income statement and cash-flow statement",
    "NI = pretax - tax": "Net income is not pretax income minus tax",
    "no gaps": "Missing periods",
}


def _labels():
    from .mapping import ITEMS
    return {i["key"]: i["label"] for i in ITEMS} | {"dps": "Dividends per share", "discontinued_ops": "Discontinued operations"}


def _where(period, detail, labels):
    """'FY 2016 dps' + '+26.039%' -> 'FY 2016 Dividends per share (+26.0%)'."""
    parts = period.split()
    if len(parts) >= 3:
        parts = parts[:2] + [labels.get(" ".join(parts[2:]), " ".join(parts[2:]).replace("_", " "))]
    d = re.sub(r"([+-]?\d+\.\d+)%", lambda m: f"{float(m.group(1)):+.1f}%", str(detail or ""))
    d = d.replace("-100.0%", "missing")
    return f"{' '.join(parts)} ({d})" if d else " ".join(parts)


_PERIOD = re.compile(r" (FY|Q[1-4]) (\d{4}):")


def _group_notes(notes):
    """The same correction for many periods becomes one line: 'Shares outstanding, 21 periods 2021-2026:
    the filing has an obvious scaling mistake (a million times too large); corrected'."""
    groups = {}
    for level, item, text in notes:
        text = re.sub(r"\(1e\+06x too large\)", "(a million times too large)", text)
        text = re.sub(r"\(1e\+03x too large\)|\(1000x too large\)", "(a thousand times too large)", text)
        m = _PERIOD.search(text)
        key = (level, _PERIOD.sub(":", text) if m else text)
        groups.setdefault(key, []).append(int(m.group(2)) if m else None)
    out = []
    for (level, text), years in groups.items():
        ys = [y for y in years if y]
        if len(years) > 1 and ys:
            head, _, rest = text.partition(":")
            span = f"{min(ys)}" if min(ys) == max(ys) else f"{min(ys)}–{max(ys)}"
            text = f"{head}, {len(years)} periods {span}:{rest}"
        out.append(dict(level=level, text=text, count=len(years), where=[]))
    return out


def assess(conn, cik, notes=()):
    from . import validate
    fin = pd.read_sql_query("SELECT * FROM financials WHERE cik=?", conn, params=(cik,))
    if fin.empty:
        return None
    ch = validate.checks(conn, cik, fin)
    failed = ch[~ch.ok]
    labels = _labels()
    issues = []
    for check, g in failed.groupby("check", sort=False):
        periods = [_where(p, d, labels) if check != "no gaps" else f"{p}: {d}" for p, d in zip(g.period, g.detail)]
        issues.append(dict(level="fail", text=PLAIN.get(check, check), count=len(g), where=periods[:12]))
    restated = ch[ch.check.str.contains("restated")]
    info = []
    if len(restated):
        years = sorted({y for p in restated.period for y in re.findall(r"\b(?:19|20)\d{2}\b", str(p))})
        if len(years) > 6:
            years = [f"{years[0]}–{years[-1]} ({len(years)} years)"]
        info.append(dict(level="info", count=len(restated), where=[],
                         text=f"The company restated fiscal {', '.join(years[:-1]) + ' and ' + years[-1] if len(years) > 1 else years[0]} later without restating the "
                              f"quarters. Year and quarters are each shown as last reported; growth rates "
                              f"always compare like with like."))
    disagree = ch[ch.check.str.contains("filings disagree")]
    if len(disagree):
        where = [_where(p, d.split(':')[0], labels) for p, d in zip(disagree.period, disagree.detail)]
        info.append(dict(level="info", count=len(disagree), where=where[:12],
                         text="In the company's own filings the four quarters do not add up to the year "
                              "(nothing was derived here); both are shown as the company reported them."))
    info += _group_notes(notes)

    fy = fin[fin.period_type == "FY"]
    preds = conn.execute("SELECT name, predecessor_cik, handover FROM predecessors "
                         "WHERE cik=? AND predecessor_cik>0", (cik,)).fetchall()
    history = dict(first_fy=int(fy.fiscal_year.min()) if len(fy) else None,
                   last_fy=int(fy.fiscal_year.max()) if len(fy) else None,
                   predecessors=[dict(name=n, cik=c, until=h) for n, c, h in preds])
    total = len(ch)
    score = round(100 * (total - len(failed)) / total, 1) if total else None
    conn.execute("INSERT OR REPLACE INTO data_quality (cik, checked_at, score, checks, failed, details) "
                 "VALUES (?,?,?,?,?,?)",
                 (cik, db.now_iso(), score, total, len(failed),
                  json.dumps(dict(issues=issues, notes=info, history=history))))
    conn.commit()
    return score, total, len(failed)


_RESTATED = re.compile(r"restated fiscal (.*?) later")
_WHERE = re.compile(r"^((?:FY|Q[1-4]) \d{4}) (\S+) \((.*)\)$")


def tidy(details):
    """Details stored by an older version read like code ('FY 2016 dps (+26.039%)', the same line 21 times).
    Rewrite them the way assess() writes them now, so every company reads well without recalculating."""
    if not isinstance(details, dict):
        return details
    labels = _labels()
    for i in details.get("issues") or []:
        i["where"] = [(_where(f"{m.group(1)} {m.group(2)}", m.group(3), labels) if (m := _WHERE.match(w)) else w)
                      for w in i.get("where") or []]
    notes, plain = [], []
    for n in details.get("notes") or []:
        text = n.get("text", "")
        m = _RESTATED.search(text)
        if m:
            years = sorted(set(re.findall(r"\b(?:19|20)\d{2}\b", m.group(1))))
            if len(years) > 6:
                years = [f"{years[0]}–{years[-1]} ({len(years)} years)"]
            if years:
                ys = ", ".join(years[:-1]) + " and " + years[-1] if len(years) > 1 else years[0]
                n = {**n, "text": text[:m.start(1)] + ys + text[m.end(1):]}
        if n.get("count", 1) == 1 and not n.get("where") and _PERIOD.search(n.get("text", "")):
            notes.append((n.get("level"), None, n["text"]))
        else:
            plain.append(n)
    details["notes"] = plain + _group_notes(notes)
    return details
