"""Dutch box 3 overview per year for your Trading 212 portfolio: an aid with your own numbers, not tax advice.

RULES holds every year's percentages and amounts in one place, with their status: update it when the
Belastingdienst publishes final figures or parliament decides. Sources (checked 15 Nov 2026):
- 2024-2027: forfaitair stelsel with tegenbewijs (Wet tegenbewijsregeling box 3); percentages as published.
- 2028: still the forfaitair stelsel (novelle of 3 Nov 2026); its figures are not known yet, so 2027's are
  used and marked as an assumption.
- 2029: vermogenswinstbelasting on financial instruments (Novelle uitbreiding vermogenswinstbelasting box 3,
  adopted by the Tweede Kamer on 12 Nov 2026, Eerste Kamer still to vote): tax on realised gains and direct
  returns, holdings you own on 1 Jan 2029 get that day's value as purchase price, heffingsvrij resultaat
  EUR 1,800 per person, losses carried forward above EUR 500 a year, 35.5% up to EUR 60,000 and 38.5% above.

build() returns the rules and, per year, what this PC knows about your Trading 212 account: value on
1 January (the peildatum), reconstructed cash at Trading 212, money in and out, the actual return of the
portfolio (for the tegenbewijs comparison), dividends gross / withheld / net per country, interest and
realised gains. The tax itself is worked out on the page, because it needs what you have outside
Trading 212 (savings, other investments, a fiscal partner), which you enter there.
"""
import datetime as dt

from . import db, pf_history, portfolio, trading212

RULES = {
    2024: dict(system="forfait", other=0.0604, bank=0.0144, debt=0.0261, exempt=57000, rate=0.36, status="final",
               note="Final figures."),
    2025: dict(system="forfait", other=0.0588, bank=0.0137, debt=0.0270, exempt=57684, rate=0.36, status="final",
               note="Final figures (bank and debt rates set on 20 Feb 2026)."),
    2026: dict(system="forfait", other=0.0600, bank=0.0128, debt=0.0270, exempt=59357, rate=0.36, status="provisional",
               note="Investments 6.00% is final; the bank (1.28%) and debt (2.70%) rates are provisional until early 2027."),
    2027: dict(system="forfait", other=0.0637, bank=0.0128, debt=0.0270, exempt=60098, rate=0.36, status="law",
               note="6.37% and EUR 60,098 per the law and Belastingplan 2027 (the halving of the exempt amount was dropped "
                    "on 3 Nov 2026). Bank and debt rates for 2027 are not known yet: 2026's provisional ones are used."),
    2028: dict(system="forfait", other=0.0637, bank=0.0128, debt=0.0270, exempt=60098, rate=0.36, status="assumed",
               note="Still the forfaitair stelsel with tegenbewijs (the new system moved to 2029). The 2028 figures are not "
                    "known yet: 2027's are used as an assumption."),
    2029: dict(system="gains", brackets=[[60000, 0.355], [None, 0.385]], exempt_result=1800, loss_threshold=500,
               status="proposal",
               note="Vermogenswinstbelasting for shares, ETFs, bonds and funds: tax on realised gains and direct returns "
                    "(dividend, interest). What you own on 1 Jan 2029 gets that day's value as purchase price, so gains "
                    "from before 2029 stay untaxed. Adopted by the Tweede Kamer on 12 Nov 2026; the Eerste Kamer still "
                    "has to vote, so this can change."),
}
CHECKED = "2026-11-15"          # the last time RULES was checked against official sources (update with RULES)
UPCOMING = [                    # what can still change RULES, shown on the page (date = expected, not certain)
    dict(date="2027-01", text="Eerste Kamer: Wet werkelijk rendement box 3 and the novelle (decides the 2029 system)"),
    dict(date="2027-02", text="Final savings and debt rates for 2026"),
    dict(date="2027-09", text="Prinsjesdag: box 3 figures for 2028"),
]
STATUS = {"final": "Final", "provisional": "Partly provisional", "law": "Law, figures partly provisional",
          "assumed": "Assumption", "proposal": "Proposal (Eerste Kamer still to vote)"}
COUNTRY = {"US": "United States", "NL": "Netherlands", "IE": "Ireland", "DE": "Germany", "FR": "France", "GB": "United Kingdom",
           "CH": "Switzerland", "LU": "Luxembourg", "BE": "Belgium", "DK": "Denmark", "SE": "Sweden", "CA": "Canada",
           "ES": "Spain", "IT": "Italy", "FI": "Finland", "NO": "Norway", "JE": "Jersey", "AU": "Australia", "JP": "Japan"}


def _at_or_before(dates, values, day):
    """Value on the last date <= day, or 0 before the first date."""
    out = 0.0
    for d, v in zip(dates, values):
        if d > day:
            break
        if v is not None:
            out = v
    return out


def _fx(conn, acc, cur, day):
    """Units of `cur` per 1 account-currency unit on (or before) `day`, from the stored daily rates."""
    if not cur or cur == acc:
        return 1.0
    r = conn.execute("SELECT close FROM pf_prices WHERE symbol=? AND date<=? AND close IS NOT NULL ORDER BY date DESC LIMIT 1",
                     (f"{acc}{cur}=X", day)).fetchone() or \
        conn.execute("SELECT close FROM pf_prices WHERE symbol=? AND close IS NOT NULL ORDER BY date LIMIT 1",
                     (f"{acc}{cur}=X",)).fetchone()
    return r[0] if r else None


def _cash_flows(conn):
    """[(day, change)] of the cash at Trading 212, rebuilt from every booking (approximate). A portfolio made
    from your own trades (own.py) has no account behind it, so no cash."""
    flows = []
    if trading212.meta(conn).get("source") == "own":
        return flows
    for t, amount, day in conn.execute("SELECT type, amount, substr(date_time, 1, 10) FROM pf_cash"):
        a = amount or 0
        if t == "DEPOSIT" or t in ("INTEREST_ON_FREE_CASH", "LENDING_INTEREST"):
            flows.append((day, abs(a)))
        elif t in ("WITHDRAW", "FEE"):
            flows.append((day, -abs(a)))
        else:                                         # TRANSFER: as booked
            flows.append((day, a))
    for side, net, day in conn.execute("""SELECT side, net_value, substr(filled_at, 1, 10) FROM pf_trades
                                          WHERE fill_id IS NOT NULL AND fill_type='TRADE' AND net_value IS NOT NULL"""):
        flows.append((day, -abs(net) if side == "BUY" else abs(net)))
    for amount, day in conn.execute("SELECT amount, substr(paid_on, 1, 10) FROM pf_dividends"):
        flows.append((day, amount or 0))
    return sorted(flows)


def build():
    from . import trading212
    conn = db.connect()
    try:
        m = trading212.meta(conn)
        acc = m.get("currency") or "EUR"
        h = pf_history.build(refresh=False)
        if h.get("empty"):
            # connected = there is a key: then the page asks to sync the history, never to connect again
            return dict(empty=True, rules=RULES, status=STATUS, checked=CHECKED, upcoming=UPCOMING,
                        connected=bool(trading212.status().get("connected")), reason=h.get("reason"),
                        last_sync=m.get("last_sync"))
        S = h["series"][acc]
        dates = h["dates"]
        today = dt.date.today().isoformat()
        first_year = int(dates[0][:4])
        flows = _cash_flows(conn)
        cash_now = sum(c for _, c in flows)
        summary_cash = ((m.get("summary") or {}).get("cash") or {}).get("availableToTrade")

        def cash_on(day):                              # balance at the start of `day`
            return sum(c for d, c in flows if d < day)

        years = {}
        for y in range(first_year, max(int(today[:4]), first_year) + 1):
            start, end = f"{y}-01-01", f"{y}-12-31"
            last_day = min(end, today)
            prev = f"{y - 1}-12-31"
            v0, v1 = _at_or_before(dates, S["value"], prev), _at_or_before(dates, S["value"], last_day)
            inv0, inv1 = _at_or_before(dates, S["invested"], prev), _at_or_before(dates, S["invested"], last_day)
            net_in = inv1 - inv0

            divs, by_country = dict(net=0.0, gross=0.0, withheld=0.0, unknown_gross=0.0), {}
            for day, amount, isin, tcur, qty, gps in conn.execute(
                    """SELECT substr(paid_on, 1, 10), amount, isin, ticker_currency, quantity, gross_per_share FROM pf_dividends
                       WHERE substr(paid_on, 1, 4)=?""", (str(y),)):
                net = amount or 0
                gross = None
                if gps and qty and tcur:
                    scale, cur = (0.01, "GBP") if tcur in ("GBX", "GBp") else (1.0, tcur)
                    rate = _fx(conn, acc, cur, day)
                    if rate:
                        gross = gps * qty * scale / rate
                if gross is None or gross < net * 0.99:     # no usable gross: count it as untaxed
                    gross = net
                    divs["unknown_gross"] += net
                c = COUNTRY.get((isin or "")[:2], (isin or "??")[:2])
                row = by_country.setdefault(c, dict(country=c, gross=0.0, withheld=0.0, net=0.0))
                for k, v in (("gross", gross), ("withheld", gross - net), ("net", net)):
                    row[k] += v
                    divs[k] += v
            interest = sum(abs(a or 0) for a, in conn.execute(
                """SELECT amount FROM pf_cash WHERE type IN ('INTEREST_ON_FREE_CASH','LENDING_INTEREST')
                   AND substr(date_time, 1, 4)=?""", (str(y),)))
            realised, hide = {}, portfolio.hidden()          # a hidden holding: counted, without its name
            for t212, name, amount in conn.execute(
                    """SELECT t212_ticker, name, SUM(realised_pl) FROM pf_trades WHERE fill_id IS NOT NULL AND fill_type='TRADE'
                       AND side='SELL' AND realised_pl IS NOT NULL AND substr(filled_at, 1, 4)=? GROUP BY t212_ticker, name""",
                    (str(y),)):
                key = "Hidden holdings" if t212 in hide else (name or t212)
                realised[key] = realised.get(key, 0) + (amount or 0)
            realised = sorted(realised.items(), key=lambda kv: -kv[1])
            actual = v1 - v0 - net_in + divs["gross"] + interest
            base = v0 + max(net_in, 0) / 2                  # rough average money at work, for a percentage
            years[y] = dict(
                year=y, complete=end < today, start_value=v0, end_value=v1, money_in=net_in,
                cash_start=cash_on(start), cash_end=cash_on(min(f"{y + 1}-01-01", today)),
                dividends=divs, dividends_by_country=sorted(by_country.values(), key=lambda r: -r["gross"]),
                interest=interest, realised=[dict(name=n, amount=a) for n, a in realised],
                realised_total=sum(a for _, a in realised), actual=actual,
                actual_pct=actual / base if base > 0 else None)
        return dict(empty=False, account_currency=acc, rules=RULES, status=STATUS, years=years, today=today,
                    checked=CHECKED, upcoming=UPCOMING,
                    cash_check=dict(rebuilt=cash_now, trading212=summary_cash), value_now=S["value"][-1],
                    cost_now=sum(p["cost"] or 0 for p in portfolio.build(live=False).get("positions") or []))
    finally:
        conn.close()


# ---------------------------------------------------------------- export: one year as CSV (Excel) or PDF
# The page sends the calculation it shows (it holds what you entered); everything else comes from here.
def export(year, fmt, page):
    """(file name without extension, bytes) of the overview of `year` in `fmt` ("csv" or "pdf")."""
    from . import trading212
    d = build()
    if d.get("empty"):
        raise ValueError("no Trading 212 data")
    y = int(year)
    R = RULES.get(y)
    if not R:
        raise ValueError(f"no rules for {y}")
    Y = d["years"].get(y) or d["years"].get(str(y))
    acc = d["account_currency"]
    peil = f"{y - 1}-12-31"
    held = pf_history.build(refresh=False, snapshots=[peil]).get("holdings_on", {}).get(peil, []) if Y else []
    conn = db.connect()
    try:
        account = (trading212.meta(conn).get("summary") or {}).get("id")
    finally:
        conn.close()
    rep = dict(year=y, rules=R, status=STATUS[R["status"]], acc=acc, Y=Y, held=held, peil=peil, page=page or {},
               made=dt.date.today().isoformat(), account=account)
    name = f"Box 3 overview {y} - Trading 212 - made {rep['made']}"
    return name, (_csv(rep) if fmt == "csv" else _pdf(rep))


def _rule_line(R, money):
    if R["system"] == "forfait":
        return (f"Investments {R['other'] * 100:.2f}%, savings {R['bank'] * 100:.2f}%, debts {R['debt'] * 100:.2f}% · "
                f"tax-free {money(R['exempt'])} per person · rate {R['rate'] * 100:.0f}%")
    br = ", ".join(f"{r * 100:.1f}% up to {money(lim)}" if lim else f"{r * 100:.1f}% above" for lim, r in R["brackets"])
    return f"Tax on realised gains, dividend and interest · tax-free result {money(R['exempt_result'])} per person · {br}"


def _sections(rep, money, num, qty):
    """The report as [(title, columns, rows, bold_row_indexes, notes)], shared by CSV and PDF."""
    y, Y, page = rep["year"], rep["Y"], rep["page"]
    out = []
    for block in page.get("blocks") or []:                     # what the page worked out (with your own inputs)
        rows = [[r[0], money(r[1]) if isinstance(r[1], (int, float)) else (r[1] or "")] for r in block.get("rows", [])]
        bold = {i for i, r in enumerate(block.get("rows", [])) if len(r) > 2 and r[2]}
        out.append((block.get("title", ""), [("", 320, "l"), ("", 140, "r")], rows, bold, block.get("notes", [])))
    if Y:
        held = rep["held"]
        rows = [[h["name"] or h["symbol"], h["isin"] or "", qty(h["quantity"]), num(h["price"]), h["currency"] or "",
                 money(h["value"])] for h in held]
        rows.append(["Cash at Trading 212", "", "", "", "", money(Y["cash_start"])])
        total = sum(h["value"] or 0 for h in held) + (Y["cash_start"] or 0)
        rows.append(["Total", "", "", "", "", money(total)])
        out.append((f"Holdings on 1 January {y} (peildatum)",
                    [("Holding", 200, "l"), ("ISIN", 92, "l"), ("Shares", 62, "r"), ("Price", 62, "r"), ("Cur.", 34, "l"),
                     (f"Value ({rep['acc']})", 80, "r")],
                    rows, {len(rows) - 1},
                    [f"Prices: the close of {rep['peil']} or the trading day before, converted at that day's rate. Cash rebuilt from "
                     "every Trading 212 booking. Check these against the annual statement (jaaroverzicht) of Trading 212 before you file."]))
        D = Y["dividends"]
        rows = [[r["country"], money(r["gross"]), money(r["withheld"]), money(r["net"])] for r in Y["dividends_by_country"]]
        rows.append(["Total", money(D["gross"]), money(D["withheld"]), money(D["net"])])
        notes = ["Withheld = gross minus what Trading 212 paid, converted at that day's rate. Dutch dividend tax and foreign "
                 "withholding tax can usually be settled in the tax return."]
        if D.get("unknown_gross", 0) > 0.01:
            notes.append(f"For {money(D['unknown_gross'])} the gross amount is unknown: counted as received.")
        out.append((f"Dividends {y} by country of the company",
                    [("Country", 200, "l"), ("Gross", 90, "r"), ("Tax withheld", 90, "r"), ("Received", 90, "r")],
                    rows if D["gross"] else [["No dividends", "", "", ""]], {len(rows) - 1} if D["gross"] else set(), notes))
        rows = [["Interest on cash", money(Y["interest"])],
                ["Realised gains and losses (shares sold, Trading 212's figure)", money(Y["realised_total"])]]
        rows += [[f"   {r['name']}", money(r["amount"])] for r in Y["realised"]]
        out.append((f"Interest and realised results {y}", [("", 320, "l"), ("", 140, "r")], rows, {0, 1},
                    ["Under the forfait (up to 2028) realised gains are not taxed separately; under the 2029 proposal they are."]))
    return out


def _csv(rep):
    sep = ";"
    nl = lambda v: "" if v is None else f"{v:.2f}".replace(".", ",")          # noqa: E731  Excel (Dutch): decimal comma
    num = lambda v: "" if v is None else f"{v:.4f}".rstrip("0").rstrip(",.").replace(".", ",")  # noqa: E731
    q = lambda s: f'"{s}"' if any(c in str(s) for c in '";\n') else str(s)  # noqa: E731
    R = rep["rules"]
    lines = [f"Box 3 overview {rep['year']} - Trading 212", f"Made on{sep}{rep['made']}",
             f"Rules{sep}{q(rep['status'] + ': ' + _rule_line(R, lambda v: f'EUR {v:,.0f}'))}",
             f"Rules checked on{sep}{CHECKED}", f"Amounts in{sep}{rep['acc']}",
             "An aid with your own numbers, not tax advice: the Belastingdienst decides.", ""]
    for title, cols, rows, _bold, notes in _sections(rep, nl, num, num):
        lines.append(q(title))
        if any(c[0] for c in cols):
            lines.append(sep.join(q(c[0]) for c in cols))
        lines += [sep.join(q(c) for c in r) for r in rows]
        lines += [q(n) for n in notes]
        lines.append("")
    return ("﻿" + "\r\n".join(lines)).encode("utf-8")      # BOM: Excel reads it as UTF-8


def _nl_money(v, cur="EUR"):
    if v is None:
        return "–"
    s = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    sym = {"EUR": "€", "USD": "$", "GBP": "£"}.get(cur, cur + " ")
    return f"{'-' if v < -0.004 else ''}{sym} {s}"


def _pdf(rep):
    from . import pdf
    y, R, acc = rep["year"], rep["rules"], rep["acc"]
    money = lambda v: _nl_money(v, acc)                                   # noqa: E731
    num = lambda v: "–" if v is None else f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")  # noqa: E731
    qty = lambda v: "–" if v is None else (f"{v:,.6f}".rstrip("0").rstrip(".").replace(",", "X").replace(".", ",").replace("X", "."))  # noqa: E731
    doc = pdf.Pdf(f"Box 3 overview {y} - Trading 212", footer=f"Stock Terminal · box 3 overview {y} · made {rep['made']}")
    doc.heading(f"Box 3 overview {y}")
    doc.text(f"Trading 212 account{' ' + str(rep['account']) if rep['account'] else ''} · amounts in {acc} · made on {rep['made']}",
             size=10, color=pdf.GREY)
    doc.space(6)
    color = {"final": pdf.POS, "proposal": pdf.NEG, "assumed": pdf.NEG}.get(R["status"], pdf.ACCENT)
    doc.badge(f"Rules {y}: {rep['status']}", color)
    doc.text(_rule_line(R, money), size=9.5, bold=True)
    doc.text(R["note"], size=8.5, color=pdf.GREY)
    doc.text(f"Rules last checked against official sources on {CHECKED}.", size=8.5, color=pdf.GREY)
    doc.text("An aid with your own numbers, not tax advice: the Belastingdienst decides.", size=8.5, color=pdf.GREY)
    for title, cols, rows, bold, notes in _sections(rep, money, num, qty):
        doc.section(title)
        signed = title.startswith("Interest") or title.startswith("Actual")      # gains and losses: red when negative
        colors = {(i, j): pdf.NEG for i, r in enumerate(rows) for j, c in enumerate(r) if j and signed and c.startswith("-")}
        doc.table(cols, rows, bold_rows=bold, colors=colors)
        for n in notes:
            doc.space(2)
            doc.text(n, size=8, color=pdf.GREY)
    return doc.output()
