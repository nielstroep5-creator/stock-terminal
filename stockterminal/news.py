"""News per stock from Yahoo (yfinance), for the stock and holding pages: title, source, time, a short summary
and the link. Kept NEWS_MINUTES in news_cache, so opening a page again does not ask Yahoo again; offline, the
stored news is shown. Works for any Yahoo symbol (AAPL, ASML.AS, VWCE.DE).

Yahoo has given two shapes over the years: the old flat one (title, publisher, link, providerPublishTime) and
the current one with everything under "content" (title, provider.displayName, canonicalUrl.url, pubDate).
Both are read; an item without a title or a link is skipped.
"""
import datetime as dt
import json
import time

from . import db

NEWS_MINUTES = 30
COUNT = 20


def _iso_utc(v):
    """'2026-11-22T14:05:00Z' from a unix time or an ISO string; None when unreadable."""
    try:
        if isinstance(v, (int, float)):
            return dt.datetime.fromtimestamp(v, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if isinstance(v, str) and v:
            d = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
            return d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError, OSError):
        pass
    return None


def _thumb(t):
    """The smallest picture at least 100 px wide, else the original."""
    if not isinstance(t, dict):
        return None
    res = [r for r in t.get("resolutions") or [] if isinstance(r, dict) and r.get("url")]
    ok = sorted((r for r in res if (r.get("width") or 0) >= 100), key=lambda r: r.get("width") or 0)
    return (ok[0] if ok else res[0] if res else {}).get("url") or t.get("originalUrl")


def item(raw):
    """One news item in the shape the screen uses, or None."""
    if not isinstance(raw, dict):
        return None
    c = raw.get("content") if isinstance(raw.get("content"), dict) else None
    if c:
        url = ((c.get("clickThroughUrl") or {}).get("url") or (c.get("canonicalUrl") or {}).get("url"))
        out = dict(id=raw.get("id") or c.get("id"), title=c.get("title"), source=(c.get("provider") or {}).get("displayName"),
                   url=url, published=_iso_utc(c.get("pubDate") or c.get("displayTime")),
                   summary=(c.get("summary") or c.get("description") or "").strip() or None, thumb=_thumb(c.get("thumbnail")),
                   kind=(c.get("contentType") or "").lower() or None)
    else:
        out = dict(id=raw.get("uuid"), title=raw.get("title"), source=raw.get("publisher"), url=raw.get("link"),
                   published=_iso_utc(raw.get("providerPublishTime")), summary=None, thumb=_thumb(raw.get("thumbnail")),
                   kind=(raw.get("type") or "").lower() or None)
    if not out["title"] or not out["url"] or not str(out["url"]).startswith(("https://", "http://")):
        return None
    if out["summary"] and len(out["summary"]) > 400:
        out["summary"] = out["summary"][:400].rsplit(" ", 1)[0] + "…"
    return out


def fetch(symbol, count=COUNT):
    """The latest news of one Yahoo symbol, newest first. Raises when Yahoo fails."""
    import yfinance as yf
    tk = yf.Ticker(symbol)
    try:
        raw = tk.get_news(count=count)
    except (AttributeError, TypeError):                 # older yfinance: .news only
        raw = tk.news
    items, seen = [], set()
    for r in raw or []:
        it = item(r)
        if it and it["url"] not in seen:
            seen.add(it["url"])
            items.append(it)
    items.sort(key=lambda i: i["published"] or "", reverse=True)
    return items[:count]


def get(symbol, refresh=False):
    """{symbol, items, fetched (unix time or None), stale (True when the stored news is shown because Yahoo failed)}.
    refresh=False answers from what is stored (nothing online); refresh=True asks Yahoo when the stored news is
    older than NEWS_MINUTES."""
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return dict(symbol=symbol, items=[], fetched=None, stale=False)
    conn = db.connect()
    try:
        row = conn.execute("SELECT data, fetched FROM news_cache WHERE symbol=?", (symbol,)).fetchone()
        if row and (not refresh or time.time() - row[1] < NEWS_MINUTES * 60):
            return dict(symbol=symbol, items=json.loads(row[0]), fetched=row[1], stale=False)
        if not refresh:
            return dict(symbol=symbol, items=[], fetched=None, stale=False)
        try:
            items = fetch(symbol)
        except Exception as e:  # noqa: BLE001  offline / Yahoo changed: show what is stored
            return dict(symbol=symbol, items=json.loads(row[0]) if row else [], fetched=row[1] if row else None,
                        stale=True, error=str(e)[:200])
        now = time.time()
        conn.execute("INSERT OR REPLACE INTO news_cache VALUES (?,?,?)", (symbol, json.dumps(items), now))
        conn.commit()
        return dict(symbol=symbol, items=items, fetched=now, stale=False)
    finally:
        conn.close()
