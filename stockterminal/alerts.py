"""Alerts: a price limit crossed, a big daily move, results coming or out, a dividend cut, analysts changing their mind.

Each alert is stored in pf_alerts (the Alerts page, the bell, the stock pages and Home show them) and, when its
kind is set to "phone", also sent to your phone with ntfy (https://ntfy.sh: free app, no account; you pick a
secret topic name).

- What to watch: targets(): your Trading 212 portfolio, everything on your watchlists (watchlist.tickers()), and
  any other stock you gave an alert with the bell on its page ("other": price limits, plus the kinds you ticked).
  A watched ticker you also hold is checked once, as a holding. The "buy below" price of a watched ticker is a
  price limit (your own limit wins). Which kinds run for watchlist tickers is a rule (wl_*).
- The rules: your own settings (prefs group "alerts"), on top of DEFAULTS. Per stock: prefs group "alerts_stock",
  "<id>|below" / "|above" (price limits), "|move" (own daily-move %), "|off" (kinds to skip, comma separated),
  "|mute" (nothing at all), "|on" (other stocks: the kinds besides price limits). <id> is the app's ticker
  (AAPL, BRK.B), or Trading 212's code for a holding without one (VWCEd_EQ). Keys from before (Trading 212 code
  of a US holding, "wl:<TICKER>") are read too and rewritten once by migrate_settings().
- When: in the daily update (07:00, after the Trading 212 sync), with "Check now" on the page, and every 5 minutes
  while markets are open (check_live(), run by the earnings watch task): price limits and big moves only, from
  one batched Yahoo quote request.
- Never twice: every alert has a key (what, which stock, which day); a key that fired is not fired again.
  A price limit fires once and is armed again when the price has moved back REARM % past the limit. An
  intraday move and the next morning's close of the same day share their key.
- Phone: quiet hours hold pushes until the morning; at most one push per stock per day (alerts of the same stock
  found together are merged into one message). Price limits and results always go: you asked for those.
"""
import datetime as dt
import json
import time
from zoneinfo import ZoneInfo

from . import db, events, portfolio, settings

NY = ZoneInfo("America/New_York")
DEFAULTS = dict(
    limit_on=True, limit_push=True, limit_rearm=2.0,                      # price limits (set per stock)
    live_on=True,                                                         # every 5 minutes while markets are open
    move_on=True, move_pct=5.0, move_etf_on=True, move_etf_pct=3.0, move_up=False, move_push=True,  # one stock, one day
    pf_on=False, pf_pct=3.0, pf_up=False, pf_push=True,                   # the whole portfolio, one day
    earn_on=True, earn_days=3, earn_soon_push=False, earn_push=True,      # results coming / out
    earn_quiet=True,                                                      # results reach the phone in quiet hours too
    div_on=True, div_pct=1.0, div_up=False, div_cut_push=True, div_up_push=False,       # dividend per share
    an_on=True, an_eps=5.0, an_target=10.0, an_rec=True, an_push=True,   # analysts (30 days); phone: big ones only
    ins_on=True, ins_min=10000.0, ins_push=True,                          # an insider bought shares (SEC Form 4)
    wl_on=True, wl_move=True, wl_earn=True, wl_div=False, wl_an=True, wl_ins=False,   # watchlist tickers (buy below = price limit)
    ntfy_topic="", ntfy_server="https://ntfy.sh", quiet_on=True, quiet_from="22:00", quiet_to="07:00",
    sum_daily=False, sum_weekly=False, sum_weekday=0,                     # morning summary to the phone (summary.py)
)
KINDS = {"limit": "Price limit", "move": "Daily move", "portfolio": "Portfolio move", "earnings_soon": "Results coming",
         "earnings": "Results", "dividend": "Dividend", "analysts": "Analysts", "insider": "Insider buy"}
STOCK_KINDS = ("limit", "move", "earnings", "dividend", "analysts", "insider")   # what can be skipped per stock
ALWAYS_PUSH = ("limit", "earnings")         # not held back by "one phone message per stock per day"
REC = [(1.5, "strong buy"), (2.5, "buy"), (3.5, "hold"), (4.5, "sell"), (9, "strong sell")]
LIVE_NY = ("02:30", "16:15")                # check_live: New York time, covers Europe's open to the US close
LIVE_EVERY_MIN = 4


# ------------------------------------------------------------------------------------------ per-stock settings
def stock_id(t212_ticker=None, ticker=None):
    """The key of a stock's own settings: the app's ticker, or Trading 212's code without one."""
    if ticker:
        return ticker.upper()
    return portfolio.app_ticker(t212_ticker, None) or t212_ticker


def normalize_stock_prefs(raw):
    """{"<old or new id>|what": value} -> {"<id>|what": value}. Old ids: a US holding's Trading 212 code
    (AAPL_US_EQ) and "wl:AAPL". When old and new say different things, the newest form wins, then the holding's."""
    out, rank = {}, {}
    for k, v in (raw or {}).items():
        t, _, what = str(k).rpartition("|")
        if not t or v is None or v == "":
            continue
        if t.startswith("wl:"):
            sid, r = t[3:].upper(), 0
        elif t.endswith("_US_EQ"):
            sid, r = portfolio.app_ticker(t, None), 1
        else:
            sid, r = t, 2
        key = f"{sid}|{what}"
        if key not in out or r > rank[key]:
            out[key], rank[key] = v, r
    return out


def migrate_settings():
    """Rewrite old per-stock keys in data/settings.json once (the server does this at start). Returns how many
    keys changed; 0 when there was nothing to do."""
    s = settings.read()
    prefs = dict(s.get("prefs") or {})
    raw = prefs.get("alerts_stock")
    if not isinstance(raw, dict) or not raw:
        return 0
    new = normalize_stock_prefs(raw)
    if new == raw:
        return 0
    prefs["alerts_stock"] = new
    settings.update({"prefs": prefs})
    return len(set(raw) ^ set(new))


def _off(cfg):
    """The kinds a stock's own settings skip ("mute" = all)."""
    if cfg.get("mute"):
        return set(STOCK_KINDS) | {"earnings_soon"}
    off = {x for x in str(cfg.get("off") or "").split(",") if x}
    if "earnings" in off:
        off.add("earnings_soon")
    return off


def config():
    """(rules, per-stock settings {id: {below, above, move, off, on, mute}}): the defaults with your own choices on top."""
    prefs = settings.read().get("prefs") or {}
    rules = dict(DEFAULTS)
    for k, v in (prefs.get("alerts") or {}).items():
        if k in DEFAULTS and v is not None:
            try:
                rules[k] = type(DEFAULTS[k])(v) if not isinstance(DEFAULTS[k], bool) else bool(v)
            except (TypeError, ValueError):
                pass
    per = {}
    for k, v in normalize_stock_prefs(prefs.get("alerts_stock") or {}).items():
        t, _, what = k.rpartition("|")
        per.setdefault(t, {})[what] = v
    return rules, per


def _num(v):
    try:
        x = float(v)
        return x if x > 0 else None
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------------------------------ what to watch
def targets(conn, refresh, rules=None, per=None):
    """What to watch: [{t212_ticker, sid, ticker, name, yahoo, currency, price, etf, origin, ...}]: your portfolio,
    then your watchlists, then other stocks with an alert of their own. t212_ticker is the key of the stored
    alerts: Trading 212's ticker for a holding, 'wl:<TICKER>' for a watched one, 'st:<TICKER>' for another;
    sid is the key of the stock's own settings."""
    rules = rules or DEFAULTS
    if per is None:
        per = config()[1]
    page = portfolio.build(live=refresh)
    positions = [p for p in page.get("positions") or [] if p.get("quantity")]
    if refresh:
        portfolio.resolve_symbols(conn, positions)
    else:
        from .quote import yahoo_symbol
        known = dict(conn.execute("SELECT t212_ticker, yahoo FROM pf_symbols"))
        for p in positions:
            p.setdefault("yahoo", yahoo_symbol(p["ticker"]) if p["ticker"] else known.get(p["t212_ticker"]))
    out = []
    for p in positions:
        info = portfolio._info(conn, p["yahoo"], refresh) if p.get("yahoo") else {}
        out.append(dict(t212_ticker=p["t212_ticker"], sid=stock_id(p["t212_ticker"], p.get("ticker")), name=p["name"],
                        yahoo=p.get("yahoo"), currency=p["currency"], price=p.get("current_price"),
                        etf=(info.get("quoteType") or "").upper() in ("ETF", "MUTUALFUND"), origin="portfolio",
                        ticker=p.get("ticker")))
    covered = {t["sid"] for t in out}
    others = []
    if rules["wl_on"]:
        others += _watched(conn, refresh, out)
        covered |= {t["sid"] for t in others}
    extra = []                                           # other stocks: a limit or kinds chosen in their bell
    for sid, cfg in per.items():
        if sid in covered or "_" in sid or ":" in sid or cfg.get("mute"):
            continue
        if _num(cfg.get("below")) or _num(cfg.get("above")) or cfg.get("on"):
            extra.append(dict(ticker=sid, buy_below=None, lists=[]))
    if extra:
        others += _as_targets(conn, refresh, extra, "other")
    return out + others, page


def _watched(conn, refresh, held):
    """Watchlist tickers as targets (not the ones you hold: those get the buy-below price as a limit)."""
    from . import watchlist
    try:
        watched = watchlist.tickers(conn)
    except Exception:  # noqa: BLE001  no watchlist tables yet
        return []
    by_ticker = {t["ticker"]: t for t in held if t.get("ticker")}
    todo = []
    for w in watched:
        h = by_ticker.get(w["ticker"])
        if h:                                           # held: one check, with the buy-below price added
            h["buy_below"], h["lists"] = w["buy_below"], w["lists"]
        else:
            todo.append(w)
    return _as_targets(conn, refresh, todo, "watchlist")


def _as_targets(conn, refresh, items, origin):
    from . import quote
    if not items:
        return []
    names = dict(conn.execute(f"SELECT ticker, COALESCE(display_name, name) FROM companies WHERE ticker IN ({','.join('?' * len(items))})",
                              [w["ticker"] for w in items]).fetchall())
    etfs = {r[0] for r in conn.execute(f"SELECT ticker FROM etfs WHERE ticker IN ({','.join('?' * len(items))})",
                                       [w["ticker"] for w in items])}
    out = []
    for w in items:
        t = w["ticker"]
        sym = quote.yahoo_symbol(t)
        info = portfolio._info(conn, sym, refresh)
        out.append(dict(t212_ticker=f"{'wl' if origin == 'watchlist' else 'st'}:{t}", sid=t, ticker=t,
                        name=info.get("shortName") or db.readable_name(names.get(t)) or t, yahoo=sym,
                        currency=info.get("currency") or "USD", price=None,
                        etf=t in etfs or (info.get("quoteType") or "").upper() in ("ETF", "MUTUALFUND"), origin=origin,
                        buy_below=w.get("buy_below"), lists=w.get("lists") or []))
    return out


def attach_quotes(watch, problems=None):
    """Yahoo's latest price for every target (one batched request): t['live'] = {price, change, day, state, at}.
    Yahoo's price wins over Trading 212's last price (which can be hours old)."""
    syms = [t["yahoo"] for t in watch if t.get("yahoo")]
    if not syms:
        return 0
    got = portfolio._yahoo_quotes(syms)
    n = 0
    for t in watch:
        q = got.get(t.get("yahoo")) if t.get("yahoo") else None
        if not q or q.get("regularMarketPrice") is None:
            continue
        ts = q.get("regularMarketTime")
        at = dt.datetime.fromtimestamp(ts, NY) if ts else None
        ch = q.get("regularMarketChangePercent")
        t["live"] = dict(price=q["regularMarketPrice"], change=ch / 100 if ch is not None else None,
                         day=at.date().isoformat() if at else None, state=q.get("marketState"),
                         at=at.astimezone().strftime("%H:%M") if at else None)
        n += 1
    missing = [t["name"] for t in watch if t.get("yahoo") and "live" not in t]
    if problems is not None and missing:
        problems.append(f"no price from Yahoo for {len(missing)} of {len(syms)} stocks"
                        + (f" ({', '.join(missing[:4])}{'…' if len(missing) > 4 else ''})" if len(missing) < len(syms) else
                           ": Yahoo not reachable, price limits used the last known price"))
    return n


# ------------------------------------------------------------------------------------------ small helpers
def _state(conn, key, default=None):
    r = conn.execute("SELECT value FROM pf_alert_state WHERE key=?", (key,)).fetchone()
    return json.loads(r[0]) if r else default


def _set_state(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO pf_alert_state VALUES (?,?)", (key, json.dumps(value)))


def _closes(conn, symbol, n=2):
    """The last n (date, close) of a symbol, oldest first."""
    rows = conn.execute("SELECT date, close FROM pf_prices WHERE symbol=? AND close IS NOT NULL ORDER BY date DESC LIMIT ?",
                        (symbol, n)).fetchall()
    return rows[::-1]


def _pct(x):
    return f"{x * 100:+.1f}%".replace("-", "−")


def _price(v, cur):
    sym = {"EUR": "€", "USD": "$", "GBP": "£", "GBX": "", "GBp": ""}.get(cur, "")
    unit = "p" if cur in ("GBX", "GBp") else ("" if sym else f" {cur}")
    return f"{sym}{v:,.2f}{unit}"


def _amount(v, cur):
    """A dividend per share: up to 4 decimals where they matter ($0.485, not $0.48)."""
    s = _price(v, cur)
    if abs(v) < 10 and round(v, 2) != round(v, 4):
        s = s.replace(f"{v:,.2f}", f"{v:,.4f}".rstrip("0"))
    return s


def _day(d):
    return dt.date.fromisoformat(d).strftime("%a %d %b")


def _recent(d, today, days):
    return d and (dt.date.fromisoformat(today) - dt.date.fromisoformat(d[:10])).days <= days


def _rec_word(v):
    return next(w for lim, w in REC if v <= lim)


def _open(t):
    """True while the stock's market is in its regular session (an unfinished day is not a close)."""
    return (t.get("live") or {}).get("state") == "REGULAR"


# ------------------------------------------------------------------------------------------ the checks
def _limits(conn, t, rules, per, now, add):
    mine = dict(per.get(t["sid"]) or {})
    from_list = False
    if _num(mine.get("below")) is None and t.get("buy_below"):      # the watchlist's buy-below price
        mine["below"], from_list = t["buy_below"], True
    price = (t.get("live") or {}).get("price") or t.get("price")
    if not price:
        return
    rearm = rules["limit_rearm"] / 100
    for side in ("below", "above"):
        lim = _num(mine.get(side))
        if lim is None:
            continue
        skey = f"limit|{t['t212_ticker']}|{side}|{lim}"
        armed = _state(conn, skey, True)
        hit = price <= lim if side == "below" else price >= lim
        if hit and armed:
            _set_state(conn, skey, False)
            what = (f"your buy-below price on {', '.join(t.get('lists') or ['your watchlist'])}" if side == "below" and from_list
                    else f"your limit: {side} {_price(lim, t['currency'])}")
            at = (t.get("live") or {}).get("at")
            add("limit", t, f"{side}|{lim}|{now.strftime('%Y-%m-%d %H:%M')}",
                f"{t['name']} {'below' if side == 'below' else 'above'} {_price(lim, t['currency'])}"
                + (" · buy price reached" if from_list and side == "below" else ""),
                f"Price {_price(price, t['currency'])}{' at ' + at if at and _open(t) else ' now'} ({what}).",
                -1 if side == "below" else 1, rules["limit_push"])
        elif not hit and not armed and (price >= lim * (1 + rearm) if side == "below" else price <= lim * (1 - rearm)):
            _set_state(conn, skey, True)                      # moved back far enough: armed again


def _move_limit(t, rules, per):
    """The daily-move limit of a stock as a fraction (its own %, else the rule for shares / ETFs); None = off."""
    own = _num((per.get(t["sid"]) or {}).get("move"))
    if own:
        return own / 100
    if t["etf"]:
        return rules["move_etf_pct"] / 100 if rules["move_etf_on"] else None
    return rules["move_pct"] / 100


def _move(conn, t, rules, per, today, add):
    lim = _move_limit(t, rules, per)
    if not t["yahoo"] or not lim:
        return
    live = t.get("live") or {}
    if t["origin"] != "portfolio" or not live:           # no stored closes: Yahoo's change of the latest session
        if live.get("change") is None or not live.get("day") or not _recent(live["day"], today, 4):
            return
        if _open(t):
            return _move_today(t, rules, per, add)
        day, ch, close = live["day"], live["change"], live["price"]
    else:
        c = _closes(conn, t["yahoo"], 3)
        if _open(t) and c and live.get("day") and c[-1][0] >= live["day"]:
            c = c[:-1]                                     # today's unfinished session is not a close
        c = c[-2:]
        if len(c) < 2 or not c[0][1] or not _recent(c[1][0], today, 4):
            return
        day, ch, close = c[1][0], c[1][1] / c[0][1] - 1, c[1][1]
    own = _num((per.get(t["sid"]) or {}).get("move"))
    if ch <= -lim or (rules["move_up"] and ch >= lim):
        add("move", t, day, f"{t['name']} {_pct(ch)} on {_day(day)}",
            f"Closed at {_price(close, t['currency'])} ({_pct(ch)}; your alert: {'±' if rules['move_up'] else '−'}{lim * 100:g}%"
            f"{' for this stock' if own else ' for ETFs' if t['etf'] else ''}).", -2 if ch < 0 else 2, rules["move_push"])


def _move_today(t, rules, per, add):
    """A big move while the market is open (check_live, or Check now during the day). Same key as the close,
    so the morning check does not repeat it."""
    lim = _move_limit(t, rules, per)
    live = t.get("live") or {}
    ch = live.get("change")
    if not lim or ch is None or not live.get("day"):
        return
    own = _num((per.get(t["sid"]) or {}).get("move"))
    if ch <= -lim or (rules["move_up"] and ch >= lim):
        add("move", t, live["day"], f"{t['name']} {_pct(ch)} today",
            f"{_price(live['price'], t['currency'])} at {live.get('at') or 'now'}, {_pct(ch)} since the last close "
            f"(your alert: {'±' if rules['move_up'] else '−'}{lim * 100:g}%{' for this stock' if own else ' for ETFs' if t['etf'] else ''}).",
            -2 if ch < 0 else 2, rules["move_push"])


def _portfolio_move(rules, today, add):
    from . import pf_history
    h = pf_history.build(refresh=False)
    if h.get("empty") or len(h.get("dates") or []) < 2:
        return
    acc = h["acc"]
    twr, dates = h["series"][acc]["twr"], h["dates"]
    if not twr[-2] or not _recent(dates[-1], today, 4):
        return
    ch = twr[-1] / twr[-2] - 1
    lim = rules["pf_pct"] / 100
    if ch <= -lim or (rules["pf_up"] and ch >= lim):
        value = h["series"][acc]["value"][-1]
        add("portfolio", dict(t212_ticker=None, sid=None, name="Your portfolio"), dates[-1], f"Portfolio {_pct(ch)} on {_day(dates[-1])}",
            f"Value {_price(value, acc)} at the close (your alert: {'±' if rules['pf_up'] else '−'}{rules['pf_pct']:g}%).",
            -2 if ch < 0 else 2, rules["pf_push"])


def _earnings(conn, t, e, rules, today, add):
    soon = [d for d in e.get("earnings") or [] if today <= d]
    if soon:
        d = soon[0]
        days = (dt.date.fromisoformat(d) - dt.date.fromisoformat(today)).days
        if days <= rules["earn_days"]:
            exp = []
            if e.get("eps_avg") is not None:
                exp.append(f"EPS expected {e['eps_avg']:.2f}")
            if e.get("revenue_avg"):
                exp.append(f"revenue {e['revenue_avg'] / 1e9:,.2f}B")
            rt = events.rate_expectation(e)
            if rt:
                exp.append(f"expectation: {rate_word(rt['step'])}")
            when = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
            add("earnings_soon", t, d, f"{t['name']} reports {when} ({_day(d)}{'' if len(soon) == 1 else ', date not confirmed'})",
                ("; ".join(exp) + ".") if exp else "No analyst estimates for this quarter yet.", 0, rules["earn_soon_push"])
    done = [r for r in e.get("results") or [] if r.get("eps_act") is not None and r.get("date") and r["date"] <= today
            and not r.get("quarter_end")]
    if done and _recent(done[-1]["date"], today, 5):
        r = done[-1]
        if t.get("ticker") and settings.read().get("earnings_watch", True):
            from . import earnings_watch          # the earnings watch writes this alert, with revenue and the 10-Q
            if earnings_watch.has_numbers(conn, t["ticker"], r["date"]):
                return
        if _results_alert(conn, t["t212_ticker"], r["date"]):
            return                                 # the earnings watch wrote it under its own date
        rt = events.rate_surprise(r["eps_est"], r["eps_act"])
        reaction = events._reaction(conn, t["yahoo"], r["date"])
        body = f"EPS {r['eps_act']:.2f}" + (f" vs {r['eps_est']:.2f} expected ({_pct(rt['surprise'])})" if rt else "")
        if reaction is not None:
            body += f"; share price {_pct(reaction)} from the close before to the close after"
        add("earnings", t, r["date"], f"{t['name']} results: {rate_word(rt['step']) if rt else 'out'}", body + ".",
            rt["step"] if rt else 0, rules["earn_push"])


def _results_alert(conn, t212, day, days=2):
    """The key of a stored Results alert of this stock for a report within `days` of `day`, or None (the morning
    check and the earnings watch may date the same report a day apart)."""
    d0 = dt.date.fromisoformat(day[:10])
    for (key,) in conn.execute("SELECT key FROM pf_alerts WHERE kind='earnings' AND t212_ticker=?", (t212,)):
        try:
            if abs((dt.date.fromisoformat(key.rsplit("|", 1)[-1][:10]) - d0).days) <= days:
                return key
        except ValueError:
            continue
    return None


def rate_word(step):
    return {2: "very positive ▲▲", 1: "positive ▲", 0: "neutral ●", -1: "negative ▼", -2: "very negative ▼▼"}[step]


def dividend_before(hist):
    """(date, amount) to compare the latest dividend with: the payment about a year earlier (so a small interim
    dividend is never a "cut" of the bigger final one), else the median of up to 3 before (one special dividend
    does not count). hist: [[date, amount], ...] oldest first."""
    if len(hist) < 2:
        return None
    last = dt.date.fromisoformat(hist[-1][0][:10])
    year = []
    for d, v in hist[:-1]:
        off = abs((last - dt.date.fromisoformat(d[:10])).days - 365)
        if off <= 45:
            year.append((off, d, v))
    if year:
        _, d, v = min(year)
        return d, v
    before = sorted(hist[-4:-1], key=lambda h: h[1])
    mid = before[len(before) // 2]
    return mid[0], mid[1]


def dividend_change(hist):
    """(change, reference amount, words) of the latest dividend, or None. A cut or raise counts only when it holds
    against the payment before AND the one about a year earlier, so a small interim dividend is not a cut of the
    bigger final one, a special dividend before does not make the next one a cut, and a raise is told once."""
    ref = dividend_before(hist)
    if not ref or not ref[1] or not hist[-2][1]:
        return None
    last, prev, year = hist[-1][1], hist[-2][1], ref[1]
    a, b = last / prev - 1, last / year - 1
    if a < 0 and b < 0:
        return (a, prev, "before") if a > b else (b, year, "a year before")
    if a > 0 and b > 0:
        return (a, prev, "before") if a < b else (b, year, "a year before")
    return 0.0, prev, "before"


def _dividend(t, e, rules, today, add):
    hist = e.get("dividend_history") or []
    if len(hist) < 2 or not _recent(hist[-1][0], today, 14):
        return
    got = dividend_change(hist)
    if not got:
        return
    ch, prev, when = got
    last = hist[-1][1]
    lim = rules["div_pct"] / 100
    scale = 0.01 if t["currency"] in ("GBX", "GBp") else 1.0
    cur = portfolio._major(1, t["currency"])[1]
    body = f"{_amount(last * scale, cur)} per share (ex-dividend {_day(hist[-1][0])}) vs {_amount(prev * scale, cur)} {when}."
    if ch <= -lim:
        add("dividend", t, hist[-1][0], f"{t['name']} dividend cut {_pct(ch)}", body, -2 if ch <= -0.1 else -1, rules["div_cut_push"])
    elif rules["div_up"] and ch >= lim:
        add("dividend", t, hist[-1][0], f"{t['name']} dividend raised {_pct(ch)}", body, 2 if ch >= 0.1 else 1, rules["div_up_push"])


def _analysts(conn, t, e, rules, today, add):
    month = today[:7]
    lim_eps, lim_t = rules["an_eps"] / 100, rules["an_target"] / 100
    for label, now, ago in (("this year", "trend_y_now", "trend_y_30d"), ("next year", "trend_ny_now", "trend_ny_30d")):
        a, b = e.get(now), e.get(ago)
        if a is None or not b:
            continue
        ch = a / b - 1 if b > 0 else (a - b) / abs(b)
        if abs(ch) >= lim_eps:
            add("analysts", t, f"eps|{label}|{month}", f"{t['name']}: EPS estimate {label} {_pct(ch)} in 30 days",
                f"Analysts now expect {a:.2f} per share {label}, a month ago {b:.2f}.", 1 if ch > 0 else -1,
                rules["an_push"] and abs(ch) >= 2 * lim_eps)
    tm = e.get("target_mean")
    if tm:                                             # Yahoo has no target history: keep our own, one per day
        key = f"target|{t['yahoo']}"
        hist = [h for h in _state(conn, key, []) if _recent(h[0], today, 60)]
        if not hist or hist[-1][0] != today:
            hist.append([today, tm])
        _set_state(conn, key, hist)
        old = [h for h in hist if not _recent(h[0], today, 27)]
        if old:
            d0, v0 = old[-1]
            ch = tm / v0 - 1
            if abs(ch) >= lim_t:
                add("analysts", t, f"target|{month}", f"{t['name']}: price target {_pct(ch)}",
                    f"Average analyst target now {_price(tm, t['currency'])}, on {_day(d0)} {_price(v0, t['currency'])}.",
                    1 if ch > 0 else -1, rules["an_push"] and abs(ch) >= 2 * lim_t)
    if rules["an_rec"] and e.get("rec_now") and e.get("rec_1m"):
        w0, w1 = _rec_word(e["rec_1m"]), _rec_word(e["rec_now"])
        if w0 != w1:
            better = e["rec_now"] < e["rec_1m"]
            add("analysts", t, f"rec|{w1}|{month}", f"{t['name']}: analysts now say {w1}",
                f"Average advice moved from {w0} to {w1} in the last month ({e.get('rec_now_n') or '?'} analysts).",
                1 if better else -1, rules["an_push"])


def _usd_short(v):
    v = float(v or 0)
    for lim, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= lim:
            return f"${v / lim:,.1f}{unit}".replace(".0" + unit, unit)
    return f"${v:,.0f}"


def insider_alerts(conn, t, cik, rules, today, add, since_days=7):
    """An insider (director, officer, 10% owner) bought shares on the open market (Form 4 filed in the last week)."""
    from . import insiders
    since = (dt.date.fromisoformat(today) - dt.timedelta(days=since_days)).isoformat()
    for g in insiders.recent_buys(conn, cik, since, rules["ins_min"]):
        who = g["owner"] or "An insider"
        role = f" ({g['role']})" if g.get("role") else ""
        price = f" at {_price(g['price'], 'USD')}" if g.get("price") else ""
        stake = ""
        if g.get("owned_after") and g["shares"] and g["owned_after"] > g["shares"]:
            stake = f", {g['shares'] / (g['owned_after'] - g['shares']):+.0%} on what they had".replace("-", "−")
        when = f" on {_day(g['date'])}" if g.get("date") else ""
        add("insider", t, f"{g['accn']}|{who}", f"{t['name']}: {who} bought {_usd_short(g['value'])}",
            f"{who}{role} bought {g['shares']:,.0f} shares{price}{when}{stake}. Filed with the SEC {_day(g['filed'])}.",
            2, rules["ins_push"])


def _insider_check(conn, t, rules, today, add, refresh, problems):
    if not t.get("ticker"):
        return
    row = conn.execute("SELECT cik FROM companies WHERE ticker=?", (t["ticker"],)).fetchone()
    if not row or not row[0]:
        return
    if refresh:                                        # new Form 4s of the last two weeks (one small request + each new one)
        from . import insiders
        try:
            insiders.update(conn, row[0], since=(dt.date.fromisoformat(today) - dt.timedelta(days=14)).isoformat(), max_new=15)
        except Exception as e:  # noqa: BLE001  the SEC could not be reached: what is stored is checked
            if not any(p.startswith("insider trades") for p in problems):
                problems.append(f"insider trades not updated (SEC): {str(e)[:120]}")
    insider_alerts(conn, t, row[0], rules, today, add)


# ------------------------------------------------------------------------------------------ run
def _kinds_on(t, rules, per):
    """Which checks run for this stock: the rules, the watchlist rules, and the stock's own settings."""
    wl, other = t["origin"] == "watchlist", t["origin"] == "other"
    cfg = per.get(t.get("sid")) or {}
    extra = {x for x in str(cfg.get("on") or "").split(",") if x}
    on = dict(limit=rules["limit_on"],
              move=rules["move_on"] and (not wl or rules["wl_move"]) and (not other or "move" in extra),
              earn=rules["earn_on"] and (not wl or rules["wl_earn"]) and (not other or "earnings" in extra),
              div=rules["div_on"] and (not wl or rules["wl_div"]) and (not other or "dividend" in extra),
              an=rules["an_on"] and (not wl or rules["wl_an"]) and (not other or "analysts" in extra) and not t["etf"],
              ins=rules["ins_on"] and (not wl or rules["wl_ins"]) and (not other or "insider" in extra) and not t["etf"])
    off = _off(cfg)
    for k, kind in (("limit", "limit"), ("move", "move"), ("earn", "earnings"), ("div", "dividend"), ("an", "analysts"),
                    ("ins", "insider")):
        if kind in off:
            on[k] = False
    return on


def _adder(conn, per, now, new):
    def add(kind, t, ident, title, body, level, push):
        key = f"{kind}|{t.get('t212_ticker') or '-'}|{ident}"
        if kind in _off(per.get(t.get("sid")) or {}):
            return
        cur = conn.execute("""INSERT OR IGNORE INTO pf_alerts (key, created, kind, t212_ticker, ticker, name, title, body, level, push)
                              VALUES (?,?,?,?,?,?,?,?,?,?)""",
                           (key, now.strftime("%Y-%m-%d %H:%M"), kind, t.get("t212_ticker"), t.get("ticker"), t.get("name"),
                            title, body, level, 1 if push else 0))
        if cur.rowcount:
            new.append(dict(kind=kind, title=title, body=body))
    return add


def _prefetch_events(conn, symbols, problems):
    """Yahoo events (results, dividends, analysts) of every symbol that needs fresh ones, 4 at a time, then
    stored: the slow part of a check, without holding the database while waiting for Yahoo."""
    from concurrent.futures import ThreadPoolExecutor
    stale = []
    for s in dict.fromkeys(symbols):
        row = conn.execute("SELECT data, fetched FROM pf_events WHERE symbol=?", (s,)).fetchone()
        if not row or json.loads(row[0]).get("version") != events.VERSION or time.time() - row[1] >= events.CACHE_HOURS * 3600:
            stale.append(s)
    if not stale:
        return

    def one(s):
        try:
            return s, events.fetch(s), None
        except Exception as e:  # noqa: BLE001  the stored one is used
            return s, None, e
    failed = []
    with ThreadPoolExecutor(4) as ex:
        for s, data, err in ex.map(one, stale):
            if data is not None:
                conn.execute("INSERT OR REPLACE INTO pf_events VALUES (?,?,?)", (s, json.dumps(data), time.time()))
            else:
                failed.append(s)
    conn.commit()
    if failed:
        problems.append(f"results, dividends and analysts not updated for {len(failed)} stocks (Yahoo)"
                        + (f": {', '.join(failed[:4])}{'…' if len(failed) > 4 else ''}" if len(failed) < len(stale) else ""))


def check(refresh=False, now=None, say=print):
    """Run every check, store new alerts and send the phone ones. {new: [...], pushed, problems}."""
    rules, per = config()
    now = now or dt.datetime.now()
    today = now.date().isoformat()
    conn = db.connect()
    new, problems = [], []
    try:
        watch, page = targets(conn, refresh, rules, per)
        for e in page.get("errors") or []:
            problems.append(f"Trading 212: {e} (the last sync was used)")
        if refresh:                                     # the latest closes for the daily moves, prices, events
            from . import pf_history
            pf_history.refresh_prices(conn, [t["yahoo"] for t in watch if t["yahoo"] and t["origin"] == "portfolio"],
                                      (now.date() - dt.timedelta(days=10)).isoformat(), problems)
            attach_quotes(watch, problems)
            _prefetch_events(conn, [t["yahoo"] for t in watch if t["yahoo"]], problems)
        add = _adder(conn, per, now, new)
        for t in watch:
            on = _kinds_on(t, rules, per)
            try:
                if on["limit"]:
                    _limits(conn, t, rules, per, now, add)
                if on["move"]:
                    _move(conn, t, rules, per, today, add)
                if t["yahoo"] and (on["earn"] or on["div"] or on["an"]):
                    e = events.cached(conn, t["yahoo"], False)
                    if e:
                        if on["earn"]:
                            _earnings(conn, t, e, rules, today, add)
                        if on["div"]:
                            _dividend(t, e, rules, today, add)
                        if on["an"]:
                            _analysts(conn, t, e, rules, today, add)
                if on["ins"]:
                    _insider_check(conn, t, rules, today, add, refresh, problems)
            except Exception as ex:  # noqa: BLE001  one stock never stops the rest
                problems.append(f"{t['name']}: {ex}")
            conn.commit()
        if rules["pf_on"]:
            try:
                _portfolio_move(rules, today, add)
            except Exception as ex:  # noqa: BLE001
                problems.append(f"portfolio move: {ex}")
        conn.commit()
        pushed = send_pending(conn, rules, now, problems)
        from . import summary                           # the morning summary, when due (summary.py)
        summary.send_if_due(conn, rules, now, problems)
        _set_state(conn, "last_check", dict(at=now.strftime("%Y-%m-%d %H:%M"), new=len(new), pushed=pushed, problems=problems[:10]))
        conn.commit()
        say(f"alerts: {len(new)} new, {pushed} sent to the phone" + (f", {len(problems)} problems" if problems else ""))
        return dict(new=new, pushed=pushed, problems=problems)
    except Exception as e:
        conn.rollback()
        _set_state(conn, "last_check", dict(at=now.strftime("%Y-%m-%d %H:%M"), new=len(new), pushed=0,
                                            problems=[f"the check stopped: {e}"] + problems[:9], failed=True))
        conn.commit()
        raise
    finally:
        conn.close()


def live_due(now_utc=None):
    """True on weekdays from Europe's open to the US close (New York time LIVE_NY)."""
    ny = (now_utc or dt.datetime.now(dt.timezone.utc)).astimezone(NY)
    return ny.weekday() < 5 and LIVE_NY[0] <= ny.strftime("%H:%M") <= LIVE_NY[1]


def check_live(now=None, say=print, force=False):
    """Price limits and big moves while markets are open, from one batched Yahoo quote request; run every 5 minutes
    by the earnings watch. Nothing (no internet) outside market hours, when it is switched off, or without any
    price limit or move rule. Returns the number of new alerts."""
    rules, per = config()
    if not rules["live_on"] or not (rules["limit_on"] or rules["move_on"]):
        return 0
    now = now or dt.datetime.now()
    utc = now.astimezone(dt.timezone.utc) if now.tzinfo else now.astimezone().astimezone(dt.timezone.utc)
    if not force and not live_due(utc):
        return 0
    local = now.astimezone().replace(tzinfo=None) if now.tzinfo else now
    conn = db.connect()
    new, problems = [], []
    try:
        last = _state(conn, "last_live") or {}
        if not force and last.get("at") and (local - dt.datetime.strptime(last["at"], "%Y-%m-%d %H:%M")).total_seconds() < LIVE_EVERY_MIN * 60:
            return 0
        try:
            watch, _ = targets(conn, False, rules, per)
        except Exception as e:  # noqa: BLE001  no portfolio: the watchlists and other stocks still count
            say(f"alerts: portfolio not readable ({e})")
            watch = (_watched(conn, False, []) if rules["wl_on"] else [])
        todo = []
        for t in watch:
            on = _kinds_on(t, rules, per)
            cfg = per.get(t["sid"]) or {}
            has_limit = on["limit"] and (_num(cfg.get("below")) or _num(cfg.get("above")) or t.get("buy_below"))
            if has_limit or (on["move"] and _move_limit(t, rules, per)):
                todo.append((t, on))
        if not todo:
            return 0
        attach_quotes([t for t, _ in todo], problems)
        add = _adder(conn, per, local, new)
        for t, on in todo:
            if not _open(t):
                continue
            try:
                if on["limit"]:
                    _limits(conn, t, rules, per, local, add)
                if on["move"]:
                    _move_today(t, rules, per, add)
            except Exception as ex:  # noqa: BLE001
                problems.append(f"{t['name']}: {ex}")
        _set_state(conn, "last_live", dict(at=local.strftime("%Y-%m-%d %H:%M"), checked=len(todo), new=len(new), problems=problems[:5]))
        conn.commit()
        pushed = send_pending(conn, rules, local, problems)
        conn.commit()
        if new or problems:
            say(f"prices: {len(todo)} checked, {len(new)} new alerts, {pushed} to the phone"
                + (f", problems: {'; '.join(problems)}" if problems else ""))
        return len(new)
    finally:
        conn.close()


def _quiet(rules, now):
    a, b, x = rules["quiet_from"], rules["quiet_to"], now.strftime("%H:%M")
    if not rules.get("quiet_on", True) or not a or not b or a == b:
        return False
    return a <= x < b if a < b else x >= a or x < b


def send_pending(conn, rules, now, problems):
    """Send the phone alerts not sent yet (the last 24 hours), one message per stock; returns how many went.
    One message per stock per day, except price limits and results (ALWAYS_PUSH)."""
    topic = (rules["ntfy_topic"] or "").strip()
    since = (now - dt.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")
    rows = conn.execute("""SELECT id, kind, t212_ticker, name, title, body, level FROM pf_alerts
                           WHERE push=1 AND pushed IS NULL AND created >= ? ORDER BY id""", (since,)).fetchall()
    if rules["earn_quiet"]:                             # results may come in quiet hours (US evening)
        rows = [r for r in rows if not _quiet(rules, now) or r[1] == "earnings"]
    elif _quiet(rules, now):
        rows = []
    if not rows or not topic:
        return 0
    today = now.strftime("%Y-%m-%d")
    groups = {}
    for r in rows:
        groups.setdefault(r[2] or r[3], []).append(r)
    sent = 0
    for holding, items in groups.items():
        done_today = conn.execute("SELECT 1 FROM pf_alerts WHERE COALESCE(t212_ticker, name)=? AND pushed LIKE ? LIMIT 1",
                                  (holding, today + "%")).fetchone()
        if done_today:                                  # one push per stock per day: the rest stays in the app
            held = [r[0] for r in items if r[1] not in ALWAYS_PUSH]
            if held:
                conn.execute(f"UPDATE pf_alerts SET pushed='no' WHERE id IN ({','.join('?' * len(held))})", held)
            items = [r for r in items if r[1] in ALWAYS_PUSH]
            if not items:
                continue
        ids = [r[0] for r in items]
        title = items[0][4] if len(items) == 1 else f"{items[0][3]}: {len(items)} alerts"
        body = "\n".join((r[5] if len(items) == 1 else f"• {r[4]}") for r in items)
        urgent = any(r[1] in ("limit", "move", "portfolio") for r in items)
        tag = "chart_with_downwards_trend" if min(r[6] for r in items) < 0 else "chart_with_upwards_trend"
        try:
            push(rules, title, body, priority=4 if urgent else 3, tags=[tag])
        except Exception as e:  # noqa: BLE001  try again at the next check
            problems.append(f"phone (ntfy): {e}")
            break
        conn.execute(f"UPDATE pf_alerts SET pushed=? WHERE id IN ({','.join('?' * len(ids))})", [now.strftime("%Y-%m-%d %H:%M"), *ids])
        sent += 1
    return sent


def test_message():
    """(title, body, tags) of the "Send test" message: the latest daily move of your biggest holding, written exactly
    like a real alert, marked as a test. Without portfolio prices: an example."""
    conn = db.connect()
    try:
        from .quote import yahoo_symbol
        known = dict(conn.execute("SELECT t212_ticker, yahoo FROM pf_symbols"))
        hide = portfolio.hidden()                       # never a holding you chose to hide
        for t212, isin, name, cur in conn.execute("SELECT t212_ticker, isin, name, instrument_currency FROM pf_positions ORDER BY current_value DESC"):
            if t212 in hide:
                continue
            us = portfolio.app_ticker(t212, isin)
            sym = yahoo_symbol(us) if us else known.get(t212)
            if not sym:
                continue
            c = _closes(conn, sym, 2)
            if len(c) == 2 and c[0][1]:
                ch = c[1][1] / c[0][1] - 1
                return (f"Test · {name} {_pct(ch)} on {_day(c[1][0])}",
                        f"Closed at {_price(c[1][1], cur)} ({_pct(ch)}). This is how a daily-move alert looks; "
                        f"real ones come when a move passes your limit.",
                        ["chart_with_downwards_trend" if ch < 0 else "chart_with_upwards_trend"])
    except Exception:  # noqa: BLE001  the example below still works
        pass
    finally:
        conn.close()
    return ("Test · NVIDIA −6.2% on Fri 13 Nov",
            "Closed at $177.00 (−6.2%; your alert: −5%). An example of how a daily-move alert looks.",
            ["chart_with_downwards_trend"])


def push(rules, title, body, priority=3, tags=()):
    """One message to your phone through ntfy (JSON, so any language works)."""
    import requests
    topic = (rules["ntfy_topic"] or "").strip()
    if not topic:
        raise ValueError("no ntfy topic set")
    server = (rules["ntfy_server"] or DEFAULTS["ntfy_server"]).rstrip("/")
    r = requests.post(server, json=dict(topic=topic, title=title[:250], message=body[:3500], priority=priority,
                                        tags=list(tags)), timeout=15)
    r.raise_for_status()


# ------------------------------------------------------------------------------------------ for the screen
COLS = ("id", "created", "kind", "t212_ticker", "ticker", "name", "title", "body", "level", "push", "pushed", "read")


def _where(ticker=None, q=None, kind=None, origin=None, unread=False):
    w, args = [], []
    if ticker:
        if "_" in ticker or ":" in ticker:
            w.append("t212_ticker=?")
            args.append(ticker)
        else:
            w.append("UPPER(ticker)=?")
            args.append(ticker.upper())
    if q:
        w.append("(title LIKE ? OR body LIKE ? OR name LIKE ? OR UPPER(ticker)=?)")
        args += [f"%{q}%"] * 3 + [q.upper()]
    if kind:
        kinds = ("earnings", "earnings_soon") if kind == "earnings" else (kind,)
        w.append(f"kind IN ({','.join('?' * len(kinds))})")
        args += list(kinds)
    if origin == "portfolio":
        w.append("(t212_ticker IS NULL OR (t212_ticker NOT LIKE 'wl:%' AND t212_ticker NOT LIKE 'st:%'))")
    elif origin == "watchlist":
        w.append("t212_ticker LIKE 'wl:%'")
    elif origin == "other":
        w.append("t212_ticker LIKE 'st:%'")
    if unread:
        w.append("read=0")
    return (" WHERE " + " AND ".join(w)) if w else "", args


def listing(limit=300, offset=0, ticker=None, q=None, kind=None, origin=None, unread=False, light=False):
    """The stored alerts, newest first, for the Alerts page, the bell, Home and the stock pages."""
    conn = db.connect()
    try:
        where, args = _where(ticker, q, kind, origin, unread)
        rows = conn.execute(f"SELECT {', '.join(COLS)} FROM pf_alerts{where} ORDER BY created DESC, id DESC LIMIT ? OFFSET ?",
                            (*args, int(limit), int(offset))).fetchall()
        total = conn.execute(f"SELECT COUNT(*) FROM pf_alerts{where}", args).fetchone()[0]
        unread_n = conn.execute("SELECT COUNT(*) FROM pf_alerts WHERE read=0").fetchone()[0]
        newest = conn.execute("SELECT MAX(id) FROM pf_alerts").fetchone()[0] or 0
        last = _state(conn, "last_check")
        live = _state(conn, "last_live")
        watched = [] if light else _watched_list(conn)
        etfs = {r[0] for r in conn.execute("SELECT ticker FROM etfs")}
    finally:
        conn.close()
    rules, _ = config()
    items = [dict(zip(COLS, r)) for r in rows]
    for i in items:
        i["etf"] = i["ticker"] in etfs              # the link goes to the ETF page
    out = dict(items=items, total=total, unread=unread_n, newest=newest, kinds=KINDS,
               last_check=last, last_live=live, phone=bool(rules["ntfy_topic"].strip()))
    if not light:
        out.update(defaults=DEFAULTS, watched=watched)
    return out


def _watched_list(conn):
    """Your watched stocks for the Rules page: [{ticker, name, lists, buy_below, held, price}] (price only from the
    quote cache: this never goes to the network)."""
    from . import quote, watchlist
    try:
        watched = watchlist.tickers(conn)
    except Exception:  # noqa: BLE001  no watchlist tables yet
        return []
    if not watched:
        return []
    held = {portfolio.app_ticker(t, isin) for t, isin in conn.execute("SELECT t212_ticker, isin FROM pf_positions")}
    names = dict(conn.execute(f"SELECT ticker, COALESCE(display_name, name) FROM companies WHERE ticker IN ({','.join('?' * len(watched))})",
                              [w["ticker"] for w in watched]).fetchall())
    out = []
    for w in watched:
        reg = ((quote.cached(w["ticker"]) or {}).get("regular") or {})
        out.append(dict(ticker=w["ticker"], name=db.readable_name(names.get(w["ticker"])) or w["ticker"], lists=w["lists"],
                        buy_below=w["buy_below"], held=w["ticker"] in held, price=reg.get("price")))
    return sorted(out, key=lambda x: (x["held"], (x["name"] or "").lower()))


def _last_price(conn, ticker=None, yahoo=None):
    """A price without going to the network: the quote cache, else the latest stored close."""
    from . import quote
    if ticker:
        reg = ((quote.cached(ticker) or {}).get("regular") or {})
        if reg.get("price"):
            return reg["price"]
        r = conn.execute("SELECT close FROM prices WHERE ticker=? AND close IS NOT NULL ORDER BY date DESC LIMIT 1", (ticker,)).fetchone()
        if r:
            return r[0]
    if yahoo:
        r = conn.execute("SELECT close FROM pf_prices WHERE symbol=? AND close IS NOT NULL ORDER BY date DESC LIMIT 1", (yahoo,)).fetchone()
        if r:
            return r[0]
    return None


def stocks():
    """Every stock alerts know about, for "Your stocks" on the Rules page and the stock pages' bell: holdings,
    watched stocks and other stocks with settings of their own. No network."""
    rules, per = config()
    conn = db.connect()
    try:
        try:
            watch, _ = targets(conn, False, dict(rules, wl_on=True), per)
        except Exception:  # noqa: BLE001  no portfolio: watchlists and others still
            watch = _watched(conn, False, []) + _as_targets(conn, False, [dict(ticker=s) for s, c in per.items()
                                                                          if "_" not in s and ":" not in s], "other")
        seen = {t["sid"] for t in watch}
        for sid, cfg in per.items():                    # muted or empty other stocks: still listed, so they can be changed
            if sid not in seen and "_" not in sid and ":" not in sid:
                watch += _as_targets(conn, False, [dict(ticker=sid)], "other")
                seen.add(sid)
        out = []
        for t in watch:
            cfg = per.get(t["sid"]) or {}
            price = t.get("price") if t["origin"] == "portfolio" else None
            price = price or _last_price(conn, t.get("ticker"), t.get("yahoo"))
            on = _kinds_on(t, rules, per)
            out.append(dict(sid=t["sid"], ticker=t.get("ticker"), t212_ticker=t["t212_ticker"], name=t["name"], origin=t["origin"],
                            etf=t["etf"], currency=t["currency"] if t["origin"] == "portfolio" else t.get("currency") or "USD",
                            price=price, lists=t.get("lists") or [], buy_below=t.get("buy_below"),
                            below=_num(cfg.get("below")), above=_num(cfg.get("above")), move=_num(cfg.get("move")),
                            move_rule=(_move_limit(dict(t, sid=None), rules, {}) or 0) * 100,
                            off=sorted(_off(cfg) - {"earnings_soon"}), on=[x for x in str(cfg.get("on") or "").split(",") if x],
                            mute=bool(cfg.get("mute")), kinds={k: bool(v) for k, v in on.items()}))
        return dict(stocks=out, rules={k: rules[k] for k in ("move_pct", "move_etf_pct", "move_etf_on", "move_on", "limit_on", "wl_on")})
    finally:
        conn.close()


def stock(ticker):
    """One stock for its bell: its settings, where it comes from, and its latest alerts."""
    sid = ticker if "_" in ticker else ticker.upper()
    rows = stocks()
    hit = next((s for s in rows["stocks"] if s["sid"] == sid or (s.get("ticker") or "") == sid), None)
    if not hit:
        conn = db.connect()
        try:
            t = _as_targets(conn, False, [dict(ticker=sid)], "other")[0]
            hit = dict(sid=sid, ticker=sid, t212_ticker=f"st:{sid}", name=t["name"], origin="none", etf=t["etf"], currency=t["currency"],
                       price=_last_price(conn, sid), lists=[], buy_below=None, below=None, above=None, move=None,
                       move_rule=rows["rules"]["move_etf_pct" if t["etf"] else "move_pct"], off=[], on=[], mute=False, kinds={})
        finally:
            conn.close()
    recent = listing(limit=5, ticker=hit.get("ticker") or hit["t212_ticker"], light=True)
    return dict(hit, recent=recent["items"], recent_total=recent["total"], rules=rows["rules"])


def mark_read(ids=None, ticker=None):
    conn = db.connect()
    try:
        if ids:
            conn.execute(f"UPDATE pf_alerts SET read=1 WHERE id IN ({','.join('?' * len(ids))})", [int(i) for i in ids])
        elif ticker:
            where, args = _where(ticker=ticker)
            conn.execute(f"UPDATE pf_alerts SET read=1{where}", args)
        else:
            conn.execute("UPDATE pf_alerts SET read=1")
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    check(refresh=True)
