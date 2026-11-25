"""Portfolio costs: what you paid Trading 212 and others on your trades, and what your ETFs cost you each year.

- Trade costs come from each fill as Trading 212 booked it (pf_trades.raw: walletImpact.taxes), by kind:
  currency conversion, French transaction tax, UK stamp duty, FINRA fee, ... in your account currency.
  Fees that are not tied to a trade (pf_cash, type FEE) are listed as "other fees".
- Your own trades (own.py): the fees you filled in or imported per trade, in your own currency (own.rebuild
  books them the same way, as a transaction fee). There is no account behind them, so no other fees.
- ETF costs: the yearly expense ratio (TER) from Yahoo times what you hold now. TER is not charged
  separately: it is taken from the fund's value, so it is already inside your return.
"""
import json

from . import db, portfolio, trading212

TAX_NAMES = {"CURRENCY_CONVERSION_FEE": "Currency conversion", "FRENCH_TRANSACTION_TAX": "French transaction tax",
             "STAMP_DUTY": "UK stamp duty", "STAMP_DUTY_RESERVE_TAX": "UK stamp duty (reserve)", "PTM_LEVY": "UK PTM levy",
             "FINRA_FEE": "FINRA fee (US sells)", "TRANSACTION_FEE": "Transaction fee", "COMMISSION_TURNOVER": "Commission"}


def ter(info):
    """Yearly expense ratio as a fraction, or None. Yahoo gives netExpenseRatio in percent, the other as a fraction."""
    for key, scale in (("netExpenseRatio", 100), ("annualReportExpenseRatio", 1)):
        v = info.get(key)
        if isinstance(v, (int, float)) and v > 0:
            f = v / scale
            return f / 100 if f > 0.05 else f            # a fraction above 5% is a percent after all
    return None


def build(refresh=False):
    page = portfolio.build(live=refresh)
    acc = page.get("account_currency") or "EUR"
    conn = db.connect()
    try:
        by_kind, by_year, by_holding, other_cur = {}, {}, {}, {}
        traded, n_trades, with_fees = 0.0, 0, 0
        own = trading212.meta(conn).get("source") == "own"
        hide = portfolio.hidden()
        for name, t212, day, net, raw in conn.execute(
                """SELECT name, t212_ticker, substr(filled_at, 1, 4), net_value, raw FROM pf_trades
                   WHERE fill_id IS NOT NULL AND fill_type='TRADE'"""):
            n_trades += 1
            traded += abs(net or 0)
            try:
                taxes = ((json.loads(raw or "{}").get("fill") or {}).get("walletImpact") or {}).get("taxes") or []
            except ValueError:
                taxes = []
            with_fees += any(tx.get("quantity") for tx in taxes)
            for tx in taxes:
                amount, cur = abs(tx.get("quantity") or 0), tx.get("currency") or acc
                if not amount:
                    continue
                kind = "Broker fees" if own else \
                    TAX_NAMES.get(tx.get("name"), (tx.get("name") or "Other").replace("_", " ").capitalize())
                if cur != acc:                              # rare: charged in another currency, listed apart
                    other_cur.setdefault(cur, 0)
                    other_cur[cur] += amount
                    continue
                by_kind[kind] = by_kind.get(kind, 0) + amount
                by_year[day] = by_year.get(day, 0) + amount
                key = "Hidden holdings" if t212 in hide else (name or t212)   # still counted in the totals
                by_holding[key] = by_holding.get(key, 0) + amount
        cash_fees = [dict(date=d, amount=abs(a or 0), currency=c) for d, a, c in conn.execute(
            "SELECT substr(date_time, 1, 10), amount, currency FROM pf_cash WHERE type='FEE' ORDER BY date_time DESC")]
        total = sum(by_kind.values())

        funds = []
        positions = [p for p in page.get("positions") or [] if p.get("value")]
        if refresh:
            portfolio.resolve_symbols(conn, positions)
        known = dict(conn.execute("SELECT t212_ticker, yahoo FROM pf_symbols"))
        from .quote import yahoo_symbol
        invested = sum(p["value"] for p in positions)
        for p in positions:
            sym = p.get("yahoo") or (yahoo_symbol(p["ticker"]) if p["ticker"] else known.get(p["t212_ticker"]))
            info = portfolio._info(conn, sym, refresh) if sym else {}
            if (info.get("quoteType") or "").upper() not in ("ETF", "MUTUALFUND"):
                continue
            r = ter(info)
            funds.append(dict(name=p["name"], t212_ticker=p["t212_ticker"], value=p["value"], ter=r,
                              yearly=p["value"] * r if r is not None else None))
        fund_cost = sum(f["yearly"] or 0 for f in funds)
        return dict(
            account_currency=acc, other_currency=page.get("other_currency"), fx=page.get("fx"),
            source="own" if own else "t212", trades_with_fees=with_fees,
            total=total, trades=n_trades, traded=traded, pct_of_traded=total / traded if traded else None,
            by_kind=sorted([dict(name=k, amount=v) for k, v in by_kind.items()], key=lambda r: -r["amount"]),
            by_year=sorted([dict(year=k, amount=v) for k, v in by_year.items()], key=lambda r: r["year"], reverse=True),
            by_holding=sorted([dict(name=k, amount=v) for k, v in by_holding.items()], key=lambda r: -r["amount"]),
            other_currency_fees=[dict(currency=k, amount=v) for k, v in other_cur.items()],
            cash_fees=cash_fees, funds=funds, fund_cost=fund_cost, invested=invested,
            fund_cost_pct=fund_cost / invested if invested else None,
            funds_missing=[f["name"] for f in funds if f["ter"] is None])
    finally:
        conn.close()
