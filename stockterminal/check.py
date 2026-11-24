"""Quick sanity report of what's in the database.

Usage:  python -m stockterminal.check AAPL MSFT
"""
import sys

from . import db

REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
]


def annual(conn, cik, tag):
    """Latest-filed value per fiscal year for full-year (~365 day) periods from 10-Ks."""
    return conn.execute(
        """
        SELECT substr(end_date,1,4) AS yr, end_date, value FROM xbrl_facts
        WHERE cik=? AND taxonomy='us-gaap' AND tag=? AND unit='USD' AND form LIKE '10-K%'
          AND julianday(end_date) - julianday(start_date) BETWEEN 350 AND 380
        GROUP BY end_date HAVING filed = MAX(filed)
        ORDER BY end_date DESC LIMIT 5
        """, (cik, tag)).fetchall()


def report(conn, ticker):
    ticker = ticker.upper()
    row = conn.execute("SELECT cik, name, sector, industry, employees, fiscal_year_end "
                       "FROM companies WHERE ticker=?", (ticker,)).fetchone()
    if not row:
        print(f"{ticker}: not in database")
        return
    cik, name, sector, industry, emp, fye = row
    print(f"\n=== {ticker}  {name}  (CIK {cik}) ===")
    print(f"sector={sector} | industry={industry} | employees={emp} | FY end={fye}")
    q = lambda sql, *a: conn.execute(sql, a).fetchone()
    print("filings:   ", q("SELECT COUNT(*), MIN(filing_date), MAX(filing_date) FROM filings WHERE cik=?", cik))
    print("xbrl facts:", q("SELECT COUNT(*), COUNT(DISTINCT taxonomy||':'||tag), MIN(end_date), MAX(end_date) "
                           "FROM xbrl_facts WHERE cik=?", cik), "(rows, distinct tags, first, last)")
    print("taxonomies:", conn.execute("SELECT taxonomy, COUNT(DISTINCT tag) FROM xbrl_facts WHERE cik=? "
                                      "GROUP BY taxonomy", (cik,)).fetchall())
    print("prices:    ", q("SELECT COUNT(*), MIN(date), MAX(date) FROM prices WHERE ticker=?", ticker))
    print("dividends: ", q("SELECT COUNT(*), MIN(date), MAX(date) FROM dividends WHERE ticker=?", ticker))
    print("splits:    ", conn.execute("SELECT date, ratio FROM splits WHERE ticker=?", (ticker,)).fetchall())

    for tag in REVENUE_TAGS:
        rev = annual(conn, cik, tag)
        if rev:
            break
    ni = {r[1]: r[2] for r in annual(conn, cik, "NetIncomeLoss")}
    print(f"Revenue tag: {tag}")
    print(f"{'FY end':<12}{'Revenue ($B)':>14}{'Net income ($B)':>18}")
    for _, end, val in rev:
        n = ni.get(end)
        print(f"{end:<12}{val / 1e9:>14.1f}{(n / 1e9 if n else float('nan')):>18.1f}")


def main():
    conn = db.connect()
    for t in sys.argv[1:] or ["AAPL", "MSFT"]:
        report(conn, t)
    errors = conn.execute("SELECT ticker, source, message FROM fetch_log WHERE status='error'").fetchall()
    print(f"\nErrors in fetch_log: {errors or 'none'}")


if __name__ == "__main__":
    main()
