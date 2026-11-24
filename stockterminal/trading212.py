"""Trading 212 link: read-only access to your own account through the Trading 212 Public API (beta).

Each user connects their own account in Settings with an API key + secret made in the Trading 212 app
(Settings -> API). Make the key with read permissions only (account data, portfolio, history,
metadata), never "orders": then this app can never buy or sell anything.

The key is stored in data/trading212.json on this PC only (data/ is not in git) and is never sent to the
app window: the screen only gets "connected, account 1234, EUR" and the last 4 characters of the key.

sync() copies the account into this PC's database (pf_ tables, see db.py): open positions, every order
with its fill, dividends and cash movements (deposits, withdrawals, interest, fees). The first sync reads
the whole history; later ones stop at the first page that holds nothing new. It runs after connecting,
from the "Sync now" button and in the daily update.

    python -m stockterminal.trading212          # sync now, from the command line

API reference: https://docs.trading212.com  (Invest and Stocks ISA accounts only; live server)
"""
import base64
import json
import os
import sys
import threading
import time

import requests

from .config import DATA_DIR

PATH = DATA_DIR / "trading212.json"
HOST = "https://live.trading212.com"
TIMEOUT = 20
PAGE_PAUSE = 3.2      # the history lists allow 20 requests a minute (per account)
_lock = threading.Lock()


class T212Error(Exception):
    """A message that can be shown to the user as is."""


def _read():
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(data):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(PATH)


def _auth(key, secret):
    return "Basic " + base64.b64encode(f"{key}:{secret}".encode()).decode()


def _get(path, key, secret, params=None, wait_on_limit=False):
    """path: "/api/v0/...", as the API's own nextPagePath gives it. wait_on_limit: on "too many requests"
    wait as long as Trading 212 says and try again (up to 3 times) instead of failing."""
    for attempt in range(4):
        try:
            r = requests.get(HOST + path, params=params, timeout=TIMEOUT,
                             headers={"Authorization": _auth(key, secret), "Accept": "application/json"})
        except requests.RequestException:
            raise T212Error("Could not reach Trading 212. Check your internet connection and try again.")
        if r.status_code == 200:
            return r.json()
        if r.status_code == 401:
            raise T212Error("Trading 212 did not accept this key and secret. Check that both are copied "
                            "completely, and that the key was made for your real-money account (not Practice).")
        if r.status_code == 403:
            raise T212Error("This key is missing a permission. Make a new key with Account data, Portfolio, "
                            "History and Metadata switched on (leave Orders off).")
        if r.status_code == 429:
            try:
                wait = max(1, int(int(r.headers.get("x-ratelimit-reset")) - time.time()) + 1)
            except (TypeError, ValueError):
                wait = 10
            if wait_on_limit and attempt < 3 and wait <= 90:
                time.sleep(wait)
                continue
            raise T212Error(f"Trading 212 allows only a few requests at a time. Try again in {wait} seconds.")
        if r.status_code in (408, 500, 502, 503, 504) and wait_on_limit and attempt < 3:
            time.sleep(5)
            continue
        raise T212Error(f"Trading 212 answered with an error ({r.status_code}). Try again later.")


def _summary(key, secret):
    """Account summary: id, currency, totalValue, cash{...}, investments{...} (1 request per 5 s)."""
    return _get("/api/v0/equity/account/summary", key, secret)


def status():
    """What the screen may know: connected or not, account number, currency, last 4 of the key."""
    d = _read()
    if not d.get("key"):
        return dict(connected=False)
    return dict(connected=True, account_id=d.get("account_id"), currency=d.get("currency"),
                key_hint=d["key"][-4:], connected_at=d.get("connected_at"))


def connect(key, secret):
    """Check the key with one request; saved only when Trading 212 accepts it."""
    key, secret = (key or "").strip(), (secret or "").strip()
    if not key or not secret:
        raise T212Error("Fill in both the API key and the API secret.")
    if len(key) > 200 or len(secret) > 200 or any(c.isspace() for c in key + secret):
        raise T212Error("That doesn't look like a Trading 212 key and secret.")
    s = _summary(key, secret)
    with _lock:
        _cache.clear()
        _write(dict(key=key, secret=secret, account_id=s.get("id"), currency=s.get("currency"),
                    connected_at=time.strftime("%Y-%m-%d")))
    return dict(status(), summary=s)


def disconnect():
    """Removes the key. The synced portfolio stays in the database (it is yours, on this PC)."""
    with _lock:
        _cache.clear()
        try:
            PATH.unlink()
        except FileNotFoundError:
            pass
    return status()


def credentials():
    d = _read()
    if not d.get("key"):
        raise T212Error("Trading 212 is not connected. Connect it in Settings.")
    return d["key"], d["secret"]


_cache = {}


def summary():
    """Live account summary, cached 10 s (Trading 212 allows 1 request per 5 s)."""
    hit = _cache.get("summary")
    if hit and time.time() - hit[0] < 10:
        return hit[1]
    s = _summary(*credentials())
    _cache["summary"] = (time.time(), s)
    return s


def positions_live(conn):
    """Open positions straight from Trading 212 (cached 10 s; 1 request per second allowed), also saved as
    the stored snapshot. Raises T212Error when Trading 212 cannot be reached: the caller falls back."""
    hit = _cache.get("positions")
    if not (hit and time.time() - hit[0] < 10):
        rows = [_position_row(p, None) for p in _get("/api/v0/equity/positions", *credentials())]
        hit = (time.time(), rows)
        _cache["positions"] = hit
        conn.execute("DELETE FROM pf_positions")
        conn.executemany(f"INSERT OR REPLACE INTO pf_positions VALUES ({','.join('?' * 14)})", rows)
        conn.commit()
    return hit[1]


# ------------------------------------------------------------------------------------------- sync
def _pages(path, key, secret, known, say):
    """Every page of a history list, newest first. Stops after a page in which every item is already
    stored (known(item) -> True), so a later sync only reads what is new."""
    nxt, n = path + ("&" if "?" in path else "?") + "limit=50", 0
    while nxt:
        if n:
            time.sleep(PAGE_PAUSE)
        page = _get(nxt, key, secret, wait_on_limit=True)
        items = page.get("items") or []
        n += 1
        done = not items or all(known(i) for i in items)    # checked before the caller stores this page
        yield items
        if done:
            break
        nxt = page.get("nextPagePath")
        if n % 5 == 0:
            say(f"  ... {n} pages")


def _trade_row(item):
    o, f = item.get("order") or {}, item.get("fill") or {}
    inst, w = o.get("instrument") or {}, f.get("walletImpact") or {}
    cur = w.get("currency")
    fees = sum(t.get("quantity") or 0 for t in w.get("taxes") or [] if t.get("currency") in (None, cur))
    rid = f"fill:{f['id']}" if f.get("id") is not None else f"order:{o.get('id')}"
    return (rid, o.get("id"), f.get("id"), o.get("ticker") or inst.get("ticker"), inst.get("isin"), inst.get("name"),
            inst.get("currency") or o.get("currency"), o.get("side"), o.get("type"), o.get("status"), f.get("type"),
            f.get("quantity"), f.get("price"), f.get("filledAt"), o.get("createdAt"), cur, w.get("netValue"),
            w.get("fxRate"), w.get("realisedProfitLoss"), fees or None, o.get("initiatedFrom"), json.dumps(item))


def _dividend_row(d):
    inst = d.get("instrument") or {}
    return (d.get("reference"), d.get("ticker"), inst.get("isin"), inst.get("name"), d.get("tickerCurrency"),
            d.get("quantity"), d.get("amount"), d.get("currency"), d.get("amountInEuro"), d.get("grossAmountPerShare"),
            d.get("paidOn"), d.get("type"), json.dumps(d))


def _cash_row(t):
    return (t.get("reference"), t.get("type"), t.get("amount"), t.get("currency"), t.get("dateTime"), json.dumps(t))


def _position_row(p, now):
    inst, w = p.get("instrument") or {}, p.get("walletImpact") or {}
    return (inst.get("ticker"), inst.get("isin"), inst.get("name"), inst.get("currency"), p.get("quantity"),
            p.get("averagePricePaid"), p.get("currentPrice"), w.get("currency"), w.get("currentValue"),
            w.get("totalCost"), w.get("unrealizedProfitLoss"), w.get("fxImpact"), p.get("createdAt"), json.dumps(p))


def _set_meta(conn, **kv):
    conn.executemany("INSERT OR REPLACE INTO pf_meta (key, value) VALUES (?,?)",
                     [(k, json.dumps(v)) for k, v in kv.items()])
    conn.commit()


def meta(conn):
    return {k: json.loads(v) for k, v in conn.execute("SELECT key, value FROM pf_meta")}


def sync(conn=None, say=print):
    """Copy the account into the database. Returns counts of new items."""
    from . import db
    own = conn is None
    conn = conn or db.connect()
    try:
        key, secret = credentials()
        _sync_state.update(step="account")
        s = _summary(key, secret)
        m = meta(conn)
        stored = m.get("account_id")
        if (stored is not None and stored != s.get("id")) or m.get("source") == "own":
            # another account than last time, or the tables were made from your own trades (own.py, kept in
            # pf_own_trades): start clean
            say("Trading 212: a different account or source than before, the old portfolio data is replaced")
            for t in ("pf_trades", "pf_dividends", "pf_cash", "pf_positions"):
                conn.execute(f"DELETE FROM {t}")
            conn.execute("DELETE FROM pf_meta WHERE key NOT IN ('own_currency', 'own_divs_fetched', 'prices_fetched')")
            conn.commit()
        _set_meta(conn, source="t212", account_id=s.get("id"), currency=s.get("currency"), summary=s)

        time.sleep(1.1)
        _sync_state.update(step="positions")
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        rows = [_position_row(p, now) for p in _get("/api/v0/equity/positions", key, secret, wait_on_limit=True)]
        conn.execute("DELETE FROM pf_positions")
        conn.executemany(f"INSERT OR REPLACE INTO pf_positions VALUES ({','.join('?' * 14)})", rows)
        conn.commit()
        say(f"Trading 212: {len(rows)} open positions")

        def have(table, col):
            return lambda value: conn.execute(f"SELECT 1 FROM {table} WHERE {col}=?", (value,)).fetchone() is not None

        new = {}
        lists = [
            ("orders", "/api/v0/equity/history/orders", "pf_trades", 22, _trade_row, have("pf_trades", "id")),
            ("dividends", "/api/v0/equity/history/dividends", "pf_dividends", 13, _dividend_row,
             have("pf_dividends", "reference")),
            ("transactions", "/api/v0/equity/history/transactions", "pf_cash", 6, _cash_row, have("pf_cash", "reference")),
        ]
        for name, path, table, ncols, to_row, exists in lists:
            time.sleep(PAGE_PAUSE)
            _sync_state.update(step=name)
            known = lambda item, to_row=to_row, exists=exists: exists(to_row(item)[0])
            count = 0
            for items in _pages(path, key, secret, known, say):
                fresh = [to_row(i) for i in items if not known(i)]
                conn.executemany(f"INSERT OR REPLACE INTO {table} VALUES ({','.join('?' * ncols)})",
                                 [to_row(i) for i in items])
                conn.commit()
                count += len(fresh)
            new[name] = count
            say(f"Trading 212: {count} new {name}")

        totals = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("pf_trades", "pf_dividends", "pf_cash", "pf_positions")}
        _set_meta(conn, last_sync=time.strftime("%Y-%m-%d %H:%M"), last_error=None, totals=totals)
        return new
    except T212Error as e:
        _set_meta(conn, last_error=str(e))
        raise
    finally:
        if own:
            conn.close()


_sync_state = {"running": False, "step": None, "error": None, "new": None}


def sync_in_background():
    """Start a sync unless one is running; the screen polls sync_status()."""
    with _lock:
        if _sync_state["running"]:
            return sync_status()
        _sync_state.update(running=True, step="starting", error=None, new=None)

    def run():
        try:
            _sync_state["new"] = sync(say=lambda *_: None)
        except T212Error as e:
            _sync_state["error"] = str(e)
        except Exception as e:  # noqa: BLE001  shown in Settings instead of failing silently
            _sync_state["error"] = f"Sync failed: {type(e).__name__}: {e}"
        finally:
            _sync_state.update(running=False, step=None)

    threading.Thread(target=run, daemon=True).start()
    return sync_status()


def sync_status():
    """For the screen: running / step / error, plus last sync and stored totals (no internet)."""
    from . import db
    out = dict(_sync_state)
    try:
        conn = db.connect()
        try:
            m = meta(conn)
        finally:
            conn.close()
        out.update(last_sync=m.get("last_sync"), totals=m.get("totals"),
                   last_error=m.get("last_error"), currency=m.get("currency"))
    except Exception:  # noqa: BLE001  no database yet
        pass
    return out


if __name__ == "__main__":
    try:
        sync()
    except T212Error as e:
        print(e)
        sys.exit(1)
