"""Real stock splits vs spin-offs.

Yahoo lists a spin-off as a 'stock split' with an odd ratio (S&P Global 2026: 1.057, 3M 2024: 1.196) and
divides all earlier prices and dividends by it. Shares did not change, so applying that ratio to
the SEC numbers would make EPS and share counts wrong for every year before the spin-off.

Rules (the same for every company):
  - a clean ratio (2, 3/2, 1/10, 20, ...) is a real split; one that is only nearly clean (2.202) only
    when the share count changed accordingly;
  - an odd ratio is a spin-off (real ratio 1), unless the SEC cover-page share count shows a
    reverse split at the same time (Hilton 2017: 1-for-3 plus two spin-offs, Yahoo 0.487):
    then the real ratio is that clean reverse-split fraction;
  - an odd ratio below 1 where the share count changed for another reason (a merger) is taken as it is
    (Willis Towers Watson 2016: 1-for-2.649 at the merger).
Prices and dividends before a spin-off are put back on the real-share basis (multiplied by
yahoo ratio / real ratio), so price, EPS, market cap and dividend yield all use the same shares.
Adjusted close (total return) stays as Yahoo has it.
"""
import math
from fractions import Fraction


def _clean(x, max_den=10, tol=0.003):
    """x as a simple split ratio (2, 3/2, 5/4, 20, 1/10, 1/20, ...), or None."""
    if not x or x <= 0:
        return None
    y = x if x >= 1 else 1 / x                   # reverse splits: 1-for-n
    c = Fraction(y).limit_denominator(max_den)
    if c == 1 or abs(y / float(c) - 1) > tol:
        return None
    return float(c) if x >= 1 else 1 / float(c)


def _observed(conn, cik, date):
    """Change in the cover-page share count across the split date (None if not known)."""
    q = ("SELECT value FROM xbrl_facts WHERE cik=? AND tag='EntityCommonStockSharesOutstanding' "
         "AND end_date{}? AND value>0 ORDER BY end_date {} LIMIT 1")
    b = conn.execute(q.format("<", "DESC"), (cik, date)).fetchone()
    a = conn.execute(q.format(">=", "ASC"), (cik, date)).fetchone()
    return a[0] / b[0] if a and b else None


def classify(ratio, obs):
    """-> (real ratio, note)"""
    c = _clean(ratio)
    # a ratio that is only NEARLY clean (HP 2015: 2.202 for the HP Enterprise spin-off, close to 11/5)
    # needs the share count to agree: unchanged across the date means a spin-off
    if c and abs(ratio / c - 1) > 1e-4 and obs and abs(math.log(obs)) < 0.1 and abs(math.log(c)) > 0.1:
        c = None
    if c:
        return c, "split"
    if obs:
        c = _clean(obs, max_den=10, tol=0.04)
        if c and c < 1 and 1 <= ratio / c <= 2.2:
            return c, "reverse split + spin-off"
        if ratio < 1 and abs(math.log(obs)) > 0.05:
            return ratio, "reverse split (merger)"
    elif ratio < 1:
        return ratio, "reverse split"
    return 1.0, "spin-off"


def refresh(conn, ticker, cik):
    """Classify every split of the ticker, store it, and fix prices/dividends. Returns [(date, ratio)]
    with the real ratios (what the SEC numbers must be adjusted by)."""
    rows = conn.execute("SELECT date, ratio FROM splits WHERE ticker=? AND ratio>0", (ticker,)).fetchall()
    out = []
    for date, ratio in rows:
        real, note = classify(ratio, _observed(conn, cik, date) if cik else None)
        conn.execute("INSERT INTO split_basis (ticker, date, real_ratio, applied, note) VALUES (?,?,?,1,?) "
                     "ON CONFLICT(ticker, date) DO UPDATE SET real_ratio=excluded.real_ratio, note=excluded.note",
                     (ticker, date, real, note))
        out.append((date, real))
    apply_prices(conn, ticker)
    return out


def apply_prices(conn, ticker):
    """Bring prices/dividends before each spin-off to the wanted factor (idempotent)."""
    rows = conn.execute("SELECT b.date, s.ratio, b.real_ratio, b.applied FROM split_basis b JOIN splits s "
                        "ON s.ticker=b.ticker AND s.date=b.date WHERE b.ticker=?", (ticker,)).fetchall()
    for date, ratio, real, applied in rows:
        want = ratio / real if real else 1.0
        f = want / (applied or 1.0)
        if abs(f - 1) > 1e-9:
            conn.execute("UPDATE prices SET open=open*?, high=high*?, low=low*?, close=close*? "
                         "WHERE ticker=? AND date<?", (f, f, f, f, ticker, date))
            conn.execute("UPDATE dividends SET amount=amount*? WHERE ticker=? AND date<?", (f, ticker, date))
        conn.execute("UPDATE split_basis SET applied=? WHERE ticker=? AND date=?", (want, ticker, date))
    conn.commit()


def reset(conn, ticker):
    """Prices and dividends were just reloaded from Yahoo: nothing is applied any more."""
    conn.execute("UPDATE split_basis SET applied=1 WHERE ticker=?", (ticker,))


def fix_new_rows(conn, ticker, since):
    """Rows from `since` on were just rewritten with Yahoo's numbers: apply the spin-off factors to them."""
    for date, applied in conn.execute("SELECT date, applied FROM split_basis WHERE ticker=? AND date>? "
                                      "AND abs(applied-1)>1e-9", (ticker, since)).fetchall():
        conn.execute("UPDATE prices SET open=open*?, high=high*?, low=low*?, close=close*? "
                     "WHERE ticker=? AND date>=? AND date<?", (applied,) * 4 + (ticker, since, date))
        conn.execute("UPDATE dividends SET amount=amount*? WHERE ticker=? AND date>=? AND date<?",
                     (applied, ticker, since, date))
