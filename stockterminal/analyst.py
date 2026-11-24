"""Analyst expectations from Yahoo Finance (via yfinance): free, no key, US and most listed stocks.

Every run stores a dated snapshot, so a history of estimates builds up over time
(Yahoo itself only shows today's numbers).

Tables
  analyst_estimates  snapshot per day, long format: (ticker, snapshot_date, section, period, field) -> value/text
      sections: eps_estimate, revenue_estimate   periods 0q, +1q, 0y, +1y (this/next quarter, this/next fiscal year)
                eps_trend (current, 7/30/60/90 days ago), eps_revisions (up/down last 7/30 days)
                growth (0q, +1q, 0y, +1y, LTG = long-term growth; stock vs index)
                price_target (low/mean/median/high), recommendations (0m, -1m, -2m, -3m counts)
                calendar (next earnings date + expected EPS/revenue), summary (forward P/E, forward EPS, PEG, ...)
  analyst_actions    upgrades/downgrades with price-target changes (one row per firm per day, accumulates)
  earnings_surprises estimated vs actual EPS per reported quarter (accumulates)

Usage:
    python -m stockterminal.analyst              (all loaded companies)
    python -m stockterminal.analyst AAPL MSFT
"""
import argparse
import datetime as dt
import time

import pandas as pd
import yfinance as yf

from . import db

# Ready-made numbers in Yahoo's quote summary (`info`)
SUMMARY_FIELDS = ["forwardPE", "forwardEps", "trailingPegRatio", "pegRatio", "targetLowPrice",
                  "targetMeanPrice", "targetMedianPrice", "targetHighPrice", "recommendationMean",
                  "recommendationKey", "averageAnalystRating", "numberOfAnalystOpinions",
                  # for the market cap check (mcapcheck.py)
                  "marketCap", "currentPrice", "sharesOutstanding", "impliedSharesOutstanding",
                  # exact time of the next results (earnings_watch.py)
                  "earningsTimestamp", "earningsTimestampStart", "isEarningsDateEstimate"]

# yfinance attribute -> section name
FRAMES = [("earnings_estimate", "eps_estimate"), ("revenue_estimate", "revenue_estimate"),
          ("eps_trend", "eps_trend"), ("eps_revisions", "eps_revisions"), ("growth_estimates", "growth")]


def _cell(v):
    """-> (value, text): numbers go in value, everything else (dates, labels) in text."""
    if v is None or (not isinstance(v, (list, tuple, dict)) and pd.isna(v)):
        return None, None
    if isinstance(v, bool):
        return float(v), None
    if isinstance(v, (int, float)):
        return float(v), None
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):  # numpy scalar
        try:
            x = v.item()
            if isinstance(x, (int, float)):
                return float(x), None
        except Exception:  # noqa: BLE001
            pass
    if isinstance(v, (pd.Timestamp, dt.date, dt.datetime)):
        return None, pd.Timestamp(v).strftime("%Y-%m-%d")
    if isinstance(v, (list, tuple)):
        return None, ", ".join(_cell(x)[1] or str(_cell(x)[0]) for x in v if x is not None)
    return None, str(v)


def _frame_rows(df, section):
    """DataFrame indexed by period ('0q', '+1y', ...) -> long rows."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []
    if "period" in df.columns:  # recommendations: period is a column
        df = df.set_index("period")
    rows = []
    for period, r in df.iterrows():
        for field, v in r.items():
            val, txt = _cell(v)
            if val is not None or txt is not None:
                rows.append((section, str(period), str(field), val, txt))
    return rows


def _dict_rows(d, section, keys=None):
    rows = []
    for k, v in (d or {}).items():
        if keys is not None and k not in keys:
            continue
        val, txt = _cell(v)
        if val is not None or txt is not None:
            rows.append((section, "", str(k), val, txt))
    return rows


# Every quoteSummary module the yfinance accessors below ask for, one by one (8 requests). They are
# fetched in ONE request and handed to yfinance's own parsers, so the parsing is exactly yfinance's.
PREFETCH_MODULES = ["earningsTrend", "industryTrend", "sectorTrend", "indexTrend", "recommendationTrend",
                    "financialData", "calendarEvents", "quoteType", "defaultKeyStatistics", "assetProfile",
                    "summaryDetail", "earningsHistory", "upgradeDowngradeHistory"]


def _prefetch(yt):
    """Serve yfinance's per-module quoteSummary requests from one combined request. A module missing from
    the combined answer (or a failed combined request) falls back to yfinance's normal single request."""
    import copy
    try:
        combined = yt._quote._fetch(PREFETCH_MODULES)
        have = set(combined["quoteSummary"]["result"][0])
    except Exception:  # noqa: BLE001 - fall back to the normal requests
        return 0
    for scraper in (yt._quote, yt._analysis):
        orig = scraper._fetch

        def fetch(modules, _orig=orig):
            if isinstance(modules, list) and set(modules) <= have:
                return copy.deepcopy(combined)     # parsers may modify what they get
            return _orig(modules)
        scraper._fetch = fetch
    return len(have)


def load_analyst(conn, ticker: str, info=None, batched=True) -> int:
    """Pull every analyst dataset Yahoo has for one ticker. Returns stored snapshot rows (0 = no coverage)."""
    t = ticker.upper()
    yt = yf.Ticker(ticker)
    if batched:
        _prefetch(yt)
    rows, errors = [], []

    def part(name, fn):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - one missing dataset must not stop the rest
            errors.append(f"{name}: {str(e)[:80]}")
            return None

    for attr, section in FRAMES:
        rows += part(attr, lambda: _frame_rows(getattr(yt, attr), section)) or []
    rows += part("recommendations", lambda: _frame_rows(yt.recommendations, "recommendations")) or []
    rows += part("price_targets", lambda: _dict_rows(yt.analyst_price_targets, "price_target")) or []
    rows += part("calendar", lambda: _dict_rows(yt.calendar, "calendar")) or []
    if info is None:
        info = part("info", lambda: yt.info) or {}
    rows += _dict_rows(info, "summary", set(SUMMARY_FIELDS))

    snap = dt.date.today().isoformat()
    conn.execute("DELETE FROM analyst_estimates WHERE ticker=? AND snapshot_date=?", (t, snap))
    conn.executemany("INSERT OR REPLACE INTO analyst_estimates (ticker, snapshot_date, section, period, field, "
                     "value, text) VALUES (?,?,?,?,?,?,?)", [(t, snap, *r) for r in rows])

    hist = part("earnings_history", lambda: yt.earnings_history)
    n_hist = 0
    if isinstance(hist, pd.DataFrame) and not hist.empty:
        for q, r in hist.iterrows():
            qd = _cell(q)[1] or str(q)
            conn.execute("INSERT OR REPLACE INTO earnings_surprises (ticker, quarter_end, eps_estimate, "
                         "eps_actual, surprise_pct, updated) VALUES (?,?,?,?,?,?)",
                         (t, qd, _cell(r.get("epsEstimate"))[0], _cell(r.get("epsActual"))[0],
                          _cell(r.get("surprisePercent"))[0], snap))
            n_hist += 1

    ud = part("upgrades_downgrades", lambda: yt.upgrades_downgrades)
    n_act = 0
    if isinstance(ud, pd.DataFrame) and not ud.empty:
        ud = ud.reset_index()
        for r in ud.to_dict("records"):
            date = _cell(r.get("GradeDate"))[1]
            if not date:
                continue
            conn.execute("INSERT OR REPLACE INTO analyst_actions (ticker, date, firm, to_grade, from_grade, action, "
                         "pt_action, pt_current, pt_prior) VALUES (?,?,?,?,?,?,?,?,?)",
                         (t, date, r.get("Firm") or "", r.get("ToGrade") or "", r.get("FromGrade") or None,
                          r.get("Action"), r.get("priceTargetAction") or None,
                          _cell(r.get("currentPriceTarget"))[0] or None,  # Yahoo uses 0 for "no target"
                          _cell(r.get("priorPriceTarget"))[0] or None))
            n_act += 1
    conn.commit()
    msg = f"{len(rows)} snapshot values, {n_hist} surprises, {n_act} rating actions"
    if errors:
        msg += " | missing: " + "; ".join(errors)
    db.log(conn, t, "yahoo_analyst", "ok" if rows else "empty", len(rows), msg[:500])
    return len(rows)


def main(argv=None):
    p = argparse.ArgumentParser(description="Download analyst expectations from Yahoo (no SEC downloads)")
    p.add_argument("tickers", nargs="*", help="default: every loaded company")
    args = p.parse_args(argv)
    conn = db.connect()
    tickers = args.tickers or [r[0] for r in conn.execute(
        "SELECT ticker FROM companies WHERE ticker IS NOT NULL ORDER BY ticker")]
    t0 = time.time()
    for tk in tickers:
        try:
            n = load_analyst(conn, tk)
            print(f"{tk}: {n} values" if n else f"{tk}: no analyst coverage")
        except Exception as e:  # noqa: BLE001
            print(f"{tk}: failed ({e})")
            db.log(conn, tk, "yahoo_analyst", "error", None, str(e)[:500])
    conn.close()
    print(f"Done in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
