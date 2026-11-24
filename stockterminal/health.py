"""Data health: every company with a number that cannot be right, for the Data health page.

Works on the Screener's table (already in memory), so it costs nothing extra. Each rule names the number,
says in plain words why it looks wrong, and which kinds of company it applies to.
"""
from . import screener

ALL, NOT_FIN = None, ("", "reit")       # NOT_FIN: margins mean little for banks and insurers

# (field, test, why, kinds)
RULES = [
    ("revenue", lambda v: v <= 0, "Revenue is zero or negative", NOT_FIN),
    ("gross_margin", lambda v: v > 1 or v < -1, "Gross margin above 100% or below -100%", NOT_FIN),
    ("operating_margin", lambda v: v > 1 or v < -3, "Operating margin above 100% or below -300%", NOT_FIN),
    ("net_margin", lambda v: v > 2 or v < -5, "Net margin far outside a normal range", NOT_FIN),
    ("fcf_margin", lambda v: v > 1.5 or v < -5, "Free cash flow margin far outside a normal range", NOT_FIN),
    ("revenue_yoy", lambda v: v < -0.5 or v > 5, "Revenue changed by more than -50% or +500% in a year", ALL),
    ("roic", lambda v: v > 1.5, "ROIC above 150%", ALL),
    ("payout_ratio", lambda v: v > 3, "Dividends more than three times earnings (often a one-off charge, check the year)", ("", "bank", "insurer")),
    ("div_yield", lambda v: v > 0.15, "Dividend yield above 15%", ALL),
    ("shares_diluted_yoy", lambda v: v > 1 or v < -0.5, "Share count more than doubled or halved in a year", ALL),
    ("quality", lambda v: v < 0.95, "Fails more than 5% of the data checks", ALL),
    ("yahoo_match", lambda v: v < 0.75, "Fewer than 3 in 4 of its main numbers match Yahoo Finance", ALL),
]
MISSING = [   # numbers every ordinary company has; empty means a line item was not found
    ("operating_margin", "No operating income found", ("",)),
    ("fcf", "No free cash flow found", ("",)),
    ("revenue", "No revenue found", ALL),
]


def scan(rows=None):
    """[{ticker, name, kind, quality, problems: [{field, value, why}]}], worst first."""
    out = []
    for r in rows if rows is not None else screener.rows():
        kind = r.get("kind") or ""
        probs = []
        for f, test, why, kinds in RULES:
            v = r.get(f)
            if v is None or (kinds is not None and kind not in kinds):
                continue
            try:
                bad = test(v)
            except TypeError:
                continue
            if bad:
                probs.append(dict(field=f, value=v, why=why))
        for f, why, kinds in MISSING:
            if r.get(f) is None and (kinds is None or kind in kinds) and not any(p["field"] == f for p in probs):
                probs.append(dict(field=f, value=None, why=why))
        if probs:
            out.append(dict(ticker=r["ticker"], name=r.get("name"), kind=kind, sector=r.get("sector"),
                            quality=r.get("quality"), problems=probs))
    out.sort(key=lambda c: (-len(c["problems"]), c["quality"] if c["quality"] is not None else 1, c["ticker"]))
    return out


def summary(rows=None):
    rows = rows if rows is not None else screener.rows()
    found = scan(rows)
    return dict(companies=len(rows), flagged=len(found), items=found,
                fields={k: dict(label=l, fmt=f) for k, l, _, f, _ in screener.FIELDS})


if __name__ == "__main__":
    for c in scan():
        print(c["ticker"], "; ".join(f"{p['why']} ({p['value']})" for p in c["problems"]))
