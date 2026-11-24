"""Watchlists: your own named lists of stocks and ETFs (the Watchlist page and the star on every stock page).

Stored in pf_watchlists / pf_watch: personal like every pf_ table, so each install keeps its own and the
starter pack leaves them out. A ticker may be on several lists. Per ticker you can keep a "buy below" price
and a note; the date and price on the day you added it give the "since added" column.

Other parts of the app can ask what is watched with tickers() (e.g. alerts: one entry per ticker, with the
names of the lists it is on).
"""
import csv
import datetime as dt
import io
import re

TICKER = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")
MAX_LISTS = 50
MAX_ITEMS = 500
DEFAULT_NAME = "My watchlist"


def clean_ticker(t):
    t = str(t or "").strip().upper()
    if not TICKER.match(t):
        raise ValueError(f"'{t}' is not a ticker")
    return t


def clean_name(name):
    name = re.sub(r"\s+", " ", str(name or "")).strip()[:60]
    if not name:
        raise ValueError("A list needs a name")
    return name


def _today():
    return dt.date.today().isoformat()


# --------------------------------------------------------------------------- lists
def lists(conn):
    """[{id, name, tickers: [...]}] in your order (tickers in the list's own order)."""
    out = [dict(id=i, name=n, tickers=[]) for i, n in
           conn.execute("SELECT id, name FROM pf_watchlists ORDER BY position, id")]
    by_id = {x["id"]: x for x in out}
    for lid, t in conn.execute("SELECT list_id, ticker FROM pf_watch ORDER BY position, added, ticker"):
        if lid in by_id:
            by_id[lid]["tickers"].append(t)
    return out


def create(conn, name):
    name = clean_name(name)
    if conn.execute("SELECT COUNT(*) FROM pf_watchlists").fetchone()[0] >= MAX_LISTS:
        raise ValueError(f"At most {MAX_LISTS} lists")
    pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM pf_watchlists").fetchone()[0]
    cur = conn.execute("INSERT INTO pf_watchlists (name, position, created) VALUES (?,?,?)", (name, pos, _today()))
    conn.commit()
    return cur.lastrowid


def _exists(conn, list_id):
    if not conn.execute("SELECT 1 FROM pf_watchlists WHERE id=?", (list_id,)).fetchone():
        raise KeyError("This list does not exist (any more)")


def rename(conn, list_id, name):
    _exists(conn, list_id)
    conn.execute("UPDATE pf_watchlists SET name=? WHERE id=?", (clean_name(name), list_id))
    conn.commit()


def delete(conn, list_id):
    _exists(conn, list_id)
    conn.execute("DELETE FROM pf_watch WHERE list_id=?", (list_id,))     # also without foreign keys on
    conn.execute("DELETE FROM pf_watchlists WHERE id=?", (list_id,))
    conn.commit()


def reorder_lists(conn, ids):
    known = {r[0] for r in conn.execute("SELECT id FROM pf_watchlists")}
    ids = [i for i in ids if i in known]
    ids += sorted(known - set(ids))
    conn.executemany("UPDATE pf_watchlists SET position=? WHERE id=?", [(n, i) for n, i in enumerate(ids)])
    conn.commit()


# --------------------------------------------------------------------------- tickers on a list
def add(conn, list_id, ticker, price=None):
    """Add a ticker at the end of the list (nothing changes when it is already on it). True when added."""
    _exists(conn, list_id)
    t = clean_ticker(ticker)
    if conn.execute("SELECT 1 FROM pf_watch WHERE list_id=? AND ticker=?", (list_id, t)).fetchone():
        return False
    if conn.execute("SELECT COUNT(*) FROM pf_watch WHERE list_id=?", (list_id,)).fetchone()[0] >= MAX_ITEMS:
        raise ValueError(f"At most {MAX_ITEMS} tickers on one list")
    pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM pf_watch WHERE list_id=?", (list_id,)).fetchone()[0]
    conn.execute("INSERT INTO pf_watch (list_id, ticker, position, added, added_price) VALUES (?,?,?,?,?)",
                 (list_id, t, pos, _today(), price))
    conn.commit()
    return True


def remove(conn, list_id, ticker):
    conn.execute("DELETE FROM pf_watch WHERE list_id=? AND ticker=?", (list_id, clean_ticker(ticker)))
    conn.commit()


def update_item(conn, list_id, ticker, changes):
    """Change the buy-below price and / or the note of one ticker on a list."""
    t = clean_ticker(ticker)
    if not conn.execute("SELECT 1 FROM pf_watch WHERE list_id=? AND ticker=?", (list_id, t)).fetchone():
        raise KeyError(f"{t} is not on this list")
    if "buy_below" in changes:
        v = changes["buy_below"]
        if v in (None, ""):
            v = None
        else:
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise ValueError("The buy-below price must be a number") from None
            if not 0 < v < 1e7:
                raise ValueError("The buy-below price must be above 0")
        conn.execute("UPDATE pf_watch SET buy_below=? WHERE list_id=? AND ticker=?", (v, list_id, t))
    if "note" in changes:
        note = str(changes["note"] or "").strip()[:500] or None
        conn.execute("UPDATE pf_watch SET note=? WHERE list_id=? AND ticker=?", (note, list_id, t))
    conn.commit()


def reorder(conn, list_id, tickers):
    """Your own order: the given tickers first, in that order; any others after them as they were."""
    _exists(conn, list_id)
    have = [r[0] for r in conn.execute("SELECT ticker FROM pf_watch WHERE list_id=? ORDER BY position, added, ticker",
                                       (list_id,))]
    want = [t for t in dict.fromkeys(str(x).upper() for x in tickers) if t in have]
    order = want + [t for t in have if t not in want]
    conn.executemany("UPDATE pf_watch SET position=? WHERE list_id=? AND ticker=?",
                     [(n, list_id, t) for n, t in enumerate(order)])
    conn.commit()


def items(conn, list_id):
    _exists(conn, list_id)
    cols = ("ticker", "position", "added", "added_price", "buy_below", "note")
    return [dict(zip(cols, r)) for r in conn.execute(
        f"SELECT {', '.join(cols)} FROM pf_watch WHERE list_id=? ORDER BY position, added, ticker", (list_id,))]


def tickers(conn):
    """Everything you watch: [{ticker, lists: [names], buy_below}] (lowest buy-below price over the lists).
    For other parts of the app, such as alerts."""
    out = {}
    for t, name, below in conn.execute("""SELECT w.ticker, l.name, w.buy_below FROM pf_watch w
                                          JOIN pf_watchlists l ON l.id=w.list_id ORDER BY l.position, w.position"""):
        e = out.setdefault(t, dict(ticker=t, lists=[], buy_below=None))
        e["lists"].append(name)
        if below is not None and (e["buy_below"] is None or below < e["buy_below"]):
            e["buy_below"] = below
    return list(out.values())


# --------------------------------------------------------------------------- CSV
CSV_COLS = ["Ticker", "Name", "Added", "Price when added", "Buy below", "Note"]


def to_csv(conn, list_id, names=None):
    names = names or {}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLS)
    for it in items(conn, list_id):
        w.writerow([it["ticker"], names.get(it["ticker"], ""), it["added"] or "",
                    "" if it["added_price"] is None else round(it["added_price"], 4),
                    "" if it["buy_below"] is None else it["buy_below"], it["note"] or ""])
    return buf.getvalue()


def parse_import(text):
    """Tickers (with buy-below price and note when the file has those columns) from a CSV file or plain text.
    Accepts this app's own export, a file with a 'Ticker' / 'Symbol' column, or just tickers separated by
    commas, spaces or new lines. Returns [{ticker, buy_below, note}], each ticker once."""
    text = (text or "").lstrip("﻿").strip()
    if not text:
        return []
    out = {}
    first = text.splitlines()[0]
    sep = max(",;\t", key=first.count)
    rows = list(csv.reader(io.StringIO(text), delimiter=sep)) if first.count(sep) else None
    head = [h.strip().lower() for h in rows[0]] if rows else []
    col = next((head.index(k) for k in ("ticker", "symbol", "tickers", "symbols") if k in head), None)
    if rows and col is not None:
        bcol = next((head.index(k) for k in ("buy below", "buy_below", "target") if k in head), None)
        ncol = head.index("note") if "note" in head else (head.index("notes") if "notes" in head else None)
        for r in rows[1:]:
            if len(r) <= col:
                continue
            try:
                t = clean_ticker(r[col])
            except ValueError:
                continue
            below = None
            if bcol is not None and len(r) > bcol and r[bcol].strip():
                try:
                    below = float(r[bcol].replace("$", "").replace(",", "").strip())
                except ValueError:
                    below = None
            note = r[ncol].strip() if ncol is not None and len(r) > ncol and r[ncol].strip() else None
            out.setdefault(t, dict(ticker=t, buy_below=below, note=note))
    else:
        for word in re.split(r"[\s,;]+", text):
            word = word.strip().strip('"\'').lstrip("$")
            try:
                t = clean_ticker(word)
            except ValueError:
                continue
            out.setdefault(t, dict(ticker=t, buy_below=None, note=None))
    return list(out.values())[:MAX_ITEMS]


def import_rows(conn, list_id, rows, price_of=lambda t: None):
    """Add imported rows (skipping tickers already on the list; their buy-below / note are filled when empty)."""
    added = 0
    for r in rows:
        if add(conn, list_id, r["ticker"], price_of(r["ticker"])):
            added += 1
        cur = conn.execute("SELECT buy_below, note FROM pf_watch WHERE list_id=? AND ticker=?",
                           (list_id, r["ticker"])).fetchone()
        ch = {}
        if r.get("buy_below") and cur[0] is None:
            ch["buy_below"] = r["buy_below"]
        if r.get("note") and not cur[1]:
            ch["note"] = r["note"]
        if ch:
            try:
                update_item(conn, list_id, r["ticker"], ch)
            except ValueError:
                pass
    return added
