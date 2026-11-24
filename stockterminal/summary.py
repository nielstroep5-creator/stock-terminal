"""Morning summary to your phone (ntfy): one message with how your holdings did, the biggest movers, what is
coming up this week and how many alerts wait in the app. Every weekday morning, or once a week, or both.

- When: after the alerts check (alerts.check: the 07:00 daily update and "Check now"). Due once per day (daily,
  Monday to Friday: Monday's covers Friday) and once per ISO week on or after the chosen weekday (weekly: a PC
  that was off on that day sends it the next time). On a day both are due, only the weekly one goes. Never in
  quiet hours: it stays due until the next check outside them.
- What it counts: your holdings now, with the stored daily closes (pf_prices, refreshed by the check), so a
  buy or sell in between does not show as a gain or loss. Changes are in each stock's own currency, weighted
  by what the holding is worth now; hidden holdings are left out of the movers.
- Rules (prefs group "alerts", alerts.DEFAULTS): sum_daily, sum_weekly, sum_weekday (0 = Monday).
"""
import datetime as dt

from . import alerts, events, portfolio

AHEAD_DAYS = 7          # "coming up": the next week
MOVERS = 3              # per direction
STALE_DAYS = 6          # a holding whose last close is older is left out (delisted, no prices)


def _money(v, cur):
    return ("−" if v < 0 else "+") + alerts._price(abs(v), cur)


def _holdings(conn, days):
    """[(name, value, change)] of what you hold now over the last `days` trading days, from the stored closes."""
    from .quote import yahoo_symbol
    page = portfolio.build(live=False)
    if not page.get("connected") and not page.get("positions"):
        return None, page
    known = dict(conn.execute("SELECT t212_ticker, yahoo FROM pf_symbols"))
    hidden = portfolio.hidden()
    today = dt.date.today()
    out = []
    for p in page.get("positions") or []:
        if not p.get("quantity") or not p.get("value") or p["t212_ticker"] in hidden:
            continue
        sym = p.get("yahoo") or (yahoo_symbol(p["ticker"]) if p.get("ticker") else known.get(p["t212_ticker"]))
        if not sym:
            continue
        c = alerts._closes(conn, sym, days + 1)
        if len(c) < 2 or not c[0][1] or (today - dt.date.fromisoformat(c[-1][0][:10])).days > STALE_DAYS:
            continue
        out.append((p["name"] or p["t212_ticker"], p["value"], c[-1][1] / c[0][1] - 1))
    return out, page


def _coming(today):
    """Lines for the results and dividend dates of your holdings in the next AHEAD_DAYS (≈ = expected)."""
    try:
        cal = events.portfolio_calendar(refresh=False)
    except Exception:  # noqa: BLE001  the summary still goes without it
        return []
    hi = (today + dt.timedelta(days=AHEAD_DAYS)).isoformat()
    word = {"earnings": "results", "ex_dividend": "ex-dividend", "dividend_payment": "dividend paid"}
    lines = []
    for i in cal.get("items") or []:
        if i["type"] in word and not i.get("past") and today.isoformat() <= i["date"] <= hi:
            lines.append(f"{'≈ ' if i.get('expected') else ''}{alerts._day(i['date'])} · {i['name']} {word[i['type']]}")
    return lines


def build(conn, weekly=False, today=None):
    """(title, body) of the summary, or None without a portfolio."""
    today = today or dt.date.today()
    rows, page = _holdings(conn, 5 if weekly else 1)
    if rows is None:
        return None
    cur = page.get("account_currency") or "EUR"
    when = "this week" if weekly else ("on Friday" if today.weekday() == 0 else "yesterday")
    lines = []
    total = (page.get("totals") or {}).get("total_value")
    if rows:
        gain = sum(v * ch / (1 + ch) for _, v, ch in rows)
        base = sum(v for _, v, _ in rows) - gain
        pct = gain / base if base else 0
        title = f"{'Your week' if weekly else 'Good morning'} · holdings {alerts._pct(pct)} {when}"
        lines.append(f"Holdings {when}: {_money(gain, cur)} ({alerts._pct(pct)})"
                     + (f" · portfolio {alerts._price(total, cur).replace('.00', '')}" if total else ""))
        up = sorted((r for r in rows if r[2] > 0), key=lambda r: -r[2])[:MOVERS]
        down = sorted((r for r in rows if r[2] < 0), key=lambda r: r[2])[:MOVERS]
        if up:
            lines.append("▲ " + " · ".join(f"{n} {alerts._pct(ch)}" for n, _, ch in up))
        if down:
            lines.append("▼ " + " · ".join(f"{n} {alerts._pct(ch)}" for n, _, ch in down))
    else:
        title = f"{'Your week' if weekly else 'Good morning'} · no new prices"
        lines.append("No new closing prices for your holdings yet.")
    coming = _coming(today)
    lines.append("")
    if coming:
        lines.append("Coming up:")
        lines += [f"• {c}" for c in coming[:8]] + ([f"• and {len(coming) - 8} more"] if len(coming) > 8 else [])
    else:
        lines.append("Nothing coming up in the next 7 days.")
    unread = conn.execute("SELECT COUNT(*) FROM pf_alerts WHERE read=0").fetchone()[0]
    if unread:
        lines.append("")
        lines.append(f"{unread} unread alert{'s' if unread != 1 else ''} in the app.")
    return title, "\n".join(lines)


def due(conn, rules, now):
    """'weekly', 'daily' or None."""
    if alerts._quiet(rules, now):
        return None
    today = now.date()
    week = "%d-W%02d" % today.isocalendar()[:2]
    if rules["sum_weekly"] and alerts._state(conn, "summary_week") != week and today.weekday() >= rules["sum_weekday"] % 7:
        return "weekly"
    if rules["sum_daily"] and today.weekday() < 5 and alerts._state(conn, "summary_day") != today.isoformat():
        return "daily"
    return None


def send_if_due(conn, rules, now, problems):
    """Send the summary when due (and a phone topic is set); True when one went."""
    if not (rules["ntfy_topic"] or "").strip() or not (rules["sum_daily"] or rules["sum_weekly"]):
        return False
    kind = due(conn, rules, now)
    if not kind:
        return False
    try:
        msg = build(conn, weekly=kind == "weekly", today=now.date())
        if msg:
            alerts.push(rules, msg[0], msg[1], priority=2, tags=["sunrise" if kind == "daily" else "calendar"])
    except Exception as e:  # noqa: BLE001  tried again at the next check
        problems.append(f"summary: {e}")
        return False
    alerts._set_state(conn, "summary_day", now.date().isoformat())
    if kind == "weekly":
        alerts._set_state(conn, "summary_week", "%d-W%02d" % now.date().isocalendar()[:2])
    conn.commit()
    return bool(msg)
