"""SQLite database: schema and small helpers."""
import re
import sqlite3
from datetime import datetime, timezone

from .config import DATA_DIR, DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    cik               INTEGER PRIMARY KEY,
    ticker            TEXT,
    name              TEXT,
    exchange          TEXT,
    sic               TEXT,
    sic_description   TEXT,
    fiscal_year_end   TEXT,      -- MMDD, e.g. 0927
    state             TEXT,
    description       TEXT,
    sector            TEXT,
    industry          TEXT,
    employees         INTEGER,
    website           TEXT,
    shares_outstanding INTEGER,
    beta              REAL,
    updated_at        TEXT
);
CREATE INDEX IF NOT EXISTS ix_companies_ticker ON companies(ticker);

-- Full SEC ticker list, used for search.
CREATE TABLE IF NOT EXISTS tickers (
    ticker   TEXT PRIMARY KEY,
    cik      INTEGER NOT NULL,
    name     TEXT
);
CREATE INDEX IF NOT EXISTS ix_tickers_cik ON tickers(cik);

-- Every XBRL fact from SEC companyfacts, unfiltered (long format).
-- start_date is '' (not NULL) for point-in-time facts so the UNIQUE key works.
CREATE TABLE IF NOT EXISTS xbrl_facts (
    cik        INTEGER NOT NULL,
    taxonomy   TEXT NOT NULL,
    tag        TEXT NOT NULL,
    label      TEXT,
    unit       TEXT NOT NULL,
    value      REAL,
    start_date TEXT NOT NULL DEFAULT '',
    end_date   TEXT NOT NULL,
    fy         INTEGER,
    fp         TEXT,
    form       TEXT,
    filed      TEXT,
    accn       TEXT NOT NULL,
    frame      TEXT,
    UNIQUE (cik, taxonomy, tag, unit, start_date, end_date, accn)
);
CREATE INDEX IF NOT EXISTS ix_facts_cik_tag ON xbrl_facts(cik, tag);

CREATE TABLE IF NOT EXISTS filings (
    cik          INTEGER NOT NULL,
    accn         TEXT NOT NULL,
    form         TEXT,
    filing_date  TEXT,
    report_date  TEXT,
    primary_doc  TEXT,
    PRIMARY KEY (cik, accn)
);

CREATE TABLE IF NOT EXISTS prices (
    ticker    TEXT NOT NULL,
    date      TEXT NOT NULL,
    open      REAL,
    high      REAL,
    low       REAL,
    close     REAL,
    adj_close REAL,
    volume    INTEGER,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS dividends (
    ticker  TEXT NOT NULL,
    date    TEXT NOT NULL,
    amount  REAL,
    PRIMARY KEY (ticker, date)
);

-- ETF fund profile from Yahoo (see etf.py); holdings / sectors / assets are JSON.
CREATE TABLE IF NOT EXISTS etfs (
    ticker        TEXT PRIMARY KEY,
    name          TEXT,
    family        TEXT,
    category      TEXT,
    legal_type    TEXT,
    description   TEXT,
    exchange      TEXT,
    inception     TEXT,
    expense_ratio REAL,       -- fraction, 0.0009 = 0.09%
    total_assets  REAL,
    yield_        REAL,
    pe            REAL,
    beta_3y       REAL,
    nav           REAL,
    turnover      REAL,
    avg_volume    REAL,
    holdings      TEXT,
    sectors       TEXT,
    assets        TEXT,
    updated_at    TEXT
);

-- Full ETF holdings (see holdings.py): the latest list per ETF; weight is a fraction of the fund.
CREATE TABLE IF NOT EXISTS etf_holdings (
    etf      TEXT NOT NULL,
    rank     INTEGER,
    ticker   TEXT,
    name     TEXT,
    cusip    TEXT,
    shares   REAL,
    value    REAL,
    weight   REAL,
    asset    TEXT,
    country  TEXT
);
CREATE INDEX IF NOT EXISTS ix_etf_holdings ON etf_holdings(etf);

-- CUSIP -> ticker answers from OpenFIGI (ticker NULL = no US listing found).
CREATE TABLE IF NOT EXISTS cusip_map (
    cusip      TEXT PRIMARY KEY,
    ticker     TEXT,
    name       TEXT,
    checked_at TEXT
);

CREATE TABLE IF NOT EXISTS splits (
    ticker  TEXT NOT NULL,
    date    TEXT NOT NULL,
    ratio   REAL,
    PRIMARY KEY (ticker, date)
);

-- Yahoo lists spin-offs as 'splits' with an odd ratio. real_ratio = the actual change in the share
-- count (1 for a spin-off); applied = factor currently multiplied into prices/dividends before `date`
-- to undo Yahoo's spin-off adjustment (see splitbasis.py)
CREATE TABLE IF NOT EXISTS split_basis (
    ticker     TEXT NOT NULL,
    date       TEXT NOT NULL,
    real_ratio REAL,
    applied    REAL DEFAULT 1,
    note       TEXT,
    PRIMARY KEY (ticker, date)
);

-- ---------------------------------------------------------------- standardized data (step 2)
CREATE TABLE IF NOT EXISTS standard_items (
    item       TEXT PRIMARY KEY,
    label      TEXT,
    statement  TEXT,      -- income / balance / cashflow
    kind       TEXT,      -- flow / instant / shares / eps / per_share / shares_instant
    sort_order INTEGER,
    tags       TEXT,      -- candidate XBRL tags, in priority order
    formulas   TEXT       -- fallback formulas
);

-- One row per company x item x period. period_type: FY, Q (standalone quarter), TTM.
CREATE TABLE IF NOT EXISTS financials (
    cik              INTEGER NOT NULL,
    item             TEXT NOT NULL,
    period_type      TEXT NOT NULL,
    fiscal_year      INTEGER,
    fiscal_quarter   INTEGER,          -- 1-4, NULL for FY
    calendar_quarter TEXT,             -- nearest calendar quarter of end_date, e.g. 2026Q3
    start_date       TEXT,             -- NULL for balance-sheet items
    end_date         TEXT NOT NULL,
    value            REAL,             -- latest reported (restated) value, split-adjusted
    original_value   REAL,             -- first reported value, split-adjusted
    unit             TEXT,
    source           TEXT,             -- XBRL tag or formula that produced the value
    method           TEXT,             -- reported / ytd_diff / fy_minus_ytd / fy_minus_q / derived / ttm
    is_derived       INTEGER,
    filed            TEXT,             -- filing date of `value`
    original_filed   TEXT,             -- date the number first became public
    accn             TEXT,
    prior_year_value REAL,             -- same period a year earlier, as known when this one was first filed
    PRIMARY KEY (cik, item, period_type, end_date)
);
CREATE INDEX IF NOT EXISTS ix_fin_item ON financials(item, period_type, calendar_quarter);

CREATE TABLE IF NOT EXISTS metrics (
    cik              INTEGER NOT NULL,
    metric           TEXT NOT NULL,
    period_type      TEXT NOT NULL,
    fiscal_year      INTEGER,
    fiscal_quarter   INTEGER,
    calendar_quarter TEXT,
    end_date         TEXT NOT NULL,
    value            REAL,
    PRIMARY KEY (cik, metric, period_type, end_date)
);
CREATE INDEX IF NOT EXISTS ix_metrics_metric ON metrics(metric, period_type, calendar_quarter);

CREATE TABLE IF NOT EXISTS valuation_daily (
    ticker            TEXT NOT NULL,
    date              TEXT NOT NULL,
    close             REAL,
    shares_outstanding REAL,
    market_cap        REAL,
    enterprise_value  REAL,
    pe_ttm            REAL,
    ps_ttm            REAL,
    pfcf_ttm          REAL,
    pb                REAL,
    ev_ebitda_ttm     REAL,
    ev_sales_ttm      REAL,
    earnings_yield    REAL,
    fcf_yield         REAL,
    dividend_yield    REAL,
    PRIMARY KEY (ticker, date)
);

-- ---------------------------------------------------------------- filing-level XBRL (step 3)
CREATE TABLE IF NOT EXISTS parsed_filings (
    cik INTEGER, accn TEXT, parser_version INTEGER, segment_facts INTEGER,
    extension_facts INTEGER, note TEXT, parsed_at TEXT,
    PRIMARY KEY (cik, accn)
);

-- Raw dimensional facts (segment / product / geography) from each filing's XBRL instance.
CREATE TABLE IF NOT EXISTS segment_facts (
    cik          INTEGER NOT NULL,
    accn         TEXT NOT NULL,
    form         TEXT,
    filed        TEXT,
    concept      TEXT NOT NULL,      -- e.g. us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax
    breakdown    TEXT NOT NULL,      -- business_segment / product / geography ('a|b' when 2 dimensions)
    member       TEXT NOT NULL,      -- e.g. aapl:IPhoneMember
    member_label TEXT,               -- company's own label, e.g. iPhone
    ndims        INTEGER,
    value        REAL,
    unit         TEXT,
    start_date   TEXT NOT NULL DEFAULT '',
    end_date     TEXT NOT NULL,
    UNIQUE (cik, accn, concept, breakdown, member, unit, start_date, end_date)
);
CREATE INDEX IF NOT EXISTS ix_segfacts ON segment_facts(cik, breakdown, concept);

-- Standardized segment data: same period logic as `financials`.
CREATE TABLE IF NOT EXISTS segment_financials (
    cik              INTEGER NOT NULL,
    breakdown        TEXT NOT NULL,
    member_key       TEXT NOT NULL,  -- stable key across renames of the same label
    member_label     TEXT,           -- latest label used by the company
    in_partition     INTEGER,        -- 1 if part of the set of members that adds up to total revenue
    item             TEXT NOT NULL,  -- revenue / operating_income / gross_profit
    period_type      TEXT NOT NULL,
    fiscal_year      INTEGER,
    fiscal_quarter   INTEGER,
    calendar_quarter TEXT,
    start_date       TEXT,
    end_date         TEXT NOT NULL,
    value            REAL,
    original_value   REAL,
    source           TEXT,
    method           TEXT,
    filed            TEXT,
    PRIMARY KEY (cik, breakdown, member_key, item, period_type, end_date)
);

-- Earlier SEC registrants of the same company (legal reorganisations). Their facts are stored
-- in xbrl_facts under the current cik. predecessor_cik = 0 is a marker "searched, nothing more".
CREATE TABLE IF NOT EXISTS predecessors (
    cik             INTEGER NOT NULL,
    predecessor_cik INTEGER NOT NULL,
    name            TEXT,
    handover        TEXT,      -- first filing date of the successor
    matched         INTEGER,   -- number of identical values that proved the link
    found_at        TEXT,
    PRIMARY KEY (cik, predecessor_cik)
);

-- Result of the automatic checks, refreshed every time a company is standardized.
CREATE TABLE IF NOT EXISTS data_quality (
    cik        INTEGER PRIMARY KEY,
    checked_at TEXT,
    score      REAL,      -- % of checks passed
    checks     INTEGER,
    failed     INTEGER,
    details    TEXT       -- JSON: {issues: [...], notes: [...], history: {...}}
);

-- Cover-page share counts (standardize.py): "shares outstanding as of <date>" on the first page of every
-- 10-K / 10-Q, a few weeks after the period end. Used for the market cap when newer than the balance sheet.
CREATE TABLE IF NOT EXISTS share_counts (
    cik    INTEGER NOT NULL,
    date   TEXT NOT NULL,     -- the "as of" date on the cover page
    filed  TEXT,              -- filing date (when the number became public)
    value  REAL,              -- split-adjusted
    PRIMARY KEY (cik, date)
);

-- Market cap checks (mcapcheck.py): our share count vs Yahoo, and days with an impossible market cap.
CREATE TABLE IF NOT EXISTS mcap_check (
    ticker     TEXT PRIMARY KEY,
    checked_at TEXT,
    date       TEXT,      -- latest valuation day
    ours       REAL,      -- our market cap that day
    yahoo      REAL,      -- Yahoo's market cap (newest daily snapshot)
    diff       REAL,      -- our share count / Yahoo's - 1
    reason     TEXT,      -- share_classes / stale / other (NULL = within 10%)
    text       TEXT,      -- the warning shown in the app
    jumps      TEXT       -- JSON [{from, to, days}]: stretches with an impossible market cap
);

-- Accuracy check (accuracy.py): our main annual numbers next to Yahoo's, last 4 fiscal years.
CREATE TABLE IF NOT EXISTS accuracy_check (
    ticker     TEXT NOT NULL,
    item       TEXT NOT NULL,
    period_end TEXT NOT NULL,
    ours       REAL,      -- NULL: we have no number
    yahoo      REAL,
    diff       REAL,      -- % (ours - yahoo) / |yahoo|
    checked_at TEXT,
    PRIMARY KEY (ticker, item, period_end)
);

-- Analyst expectations from Yahoo (analyst.py). One snapshot per day, so estimate history builds up.
CREATE TABLE IF NOT EXISTS analyst_estimates (
    ticker        TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    section       TEXT NOT NULL,   -- eps_estimate, revenue_estimate, eps_trend, eps_revisions, growth, ...
    period        TEXT NOT NULL,   -- 0q, +1q, 0y, +1y, LTG, 0m, -1m ... ('' when not period-based)
    field         TEXT NOT NULL,   -- avg, low, high, numberOfAnalysts, growth, 7daysAgo, ...
    value         REAL,
    text          TEXT,
    PRIMARY KEY (ticker, snapshot_date, section, period, field)
);
CREATE TABLE IF NOT EXISTS analyst_actions (
    ticker      TEXT NOT NULL,
    date        TEXT NOT NULL,
    firm        TEXT NOT NULL,
    to_grade    TEXT NOT NULL,
    from_grade  TEXT,
    action      TEXT,              -- up, down, main, init, reit
    pt_action   TEXT,              -- Raises, Lowers, Maintains, ...
    pt_current  REAL,
    pt_prior    REAL,
    PRIMARY KEY (ticker, date, firm, to_grade)
);
CREATE TABLE IF NOT EXISTS earnings_surprises (
    ticker        TEXT NOT NULL,
    quarter_end   TEXT NOT NULL,
    eps_estimate  REAL,
    eps_actual    REAL,
    surprise_pct  REAL,
    updated       TEXT,
    PRIMARY KEY (ticker, quarter_end)
);

-- Portfolio from Trading 212 (stockterminal/trading212.py). Personal: each PC has only its own, and the
-- starter pack (starter.py) leaves every pf_ table out. raw = the item exactly as Trading 212 sent it.
CREATE TABLE IF NOT EXISTS pf_trades (
    id                  TEXT PRIMARY KEY,  -- fill:<fill id>, or order:<order id> for an order without a fill
    order_id            INTEGER,
    fill_id             INTEGER,
    t212_ticker         TEXT,              -- e.g. AAPL_US_EQ, ASMLa_EQ
    isin                TEXT,
    name                TEXT,
    instrument_currency TEXT,
    side                TEXT,              -- BUY / SELL
    order_type          TEXT,              -- MARKET, LIMIT, ...
    status              TEXT,              -- FILLED, CANCELLED, ...
    fill_type           TEXT,              -- TRADE, STOCK_SPLIT, SPIN_OFF, ...
    quantity            REAL,
    price               REAL,              -- per share, instrument currency
    filled_at           TEXT,
    created_at          TEXT,
    account_currency    TEXT,
    net_value           REAL,              -- in account currency
    fx_rate             REAL,
    realised_pl         REAL,
    fees                REAL,              -- taxes and fees in account currency
    initiated_from      TEXT,
    raw                 TEXT
);
CREATE INDEX IF NOT EXISTS ix_pf_trades_isin ON pf_trades(isin);
CREATE TABLE IF NOT EXISTS pf_dividends (
    reference       TEXT PRIMARY KEY,
    t212_ticker     TEXT,
    isin            TEXT,
    name            TEXT,
    ticker_currency TEXT,
    quantity        REAL,
    amount          REAL,      -- account currency
    currency        TEXT,
    amount_eur      REAL,
    gross_per_share REAL,      -- instrument currency
    paid_on         TEXT,
    type            TEXT,
    raw             TEXT
);
CREATE TABLE IF NOT EXISTS pf_cash (
    reference TEXT PRIMARY KEY,
    type      TEXT,            -- DEPOSIT, WITHDRAW, FEE, TRANSFER, INTEREST_ON_FREE_CASH, LENDING_INTEREST
    amount    REAL,
    currency  TEXT,
    date_time TEXT,
    raw       TEXT
);
CREATE TABLE IF NOT EXISTS pf_positions (    -- the open positions at the last sync
    t212_ticker         TEXT PRIMARY KEY,
    isin                TEXT,
    name                TEXT,
    instrument_currency TEXT,
    quantity            REAL,
    avg_price           REAL,  -- instrument currency
    current_price       REAL,  -- instrument currency
    account_currency    TEXT,
    current_value       REAL,  -- account currency
    total_cost          REAL,
    unrealized_pl       REAL,
    fx_impact           REAL,
    opened_at           TEXT,
    raw                 TEXT
);
CREATE TABLE IF NOT EXISTS pf_meta (key TEXT PRIMARY KEY, value TEXT);   -- account, summary, last sync
CREATE TABLE IF NOT EXISTS pf_prices (        -- daily Yahoo history for the portfolio chart (pf_history.py)
    symbol    TEXT NOT NULL,           -- Yahoo symbol: holdings, exchange rates (EURUSD=X), benchmarks
    date      TEXT NOT NULL,
    close     REAL,                    -- split-adjusted, as Yahoo gives it
    adj_close REAL,                    -- also dividend-adjusted (benchmarks: total return)
    split     REAL,                    -- split ratio on that day, else NULL
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS pf_info (          -- Yahoo profile of a holding (sector, country, description, ...)
    symbol  TEXT PRIMARY KEY,
    info    TEXT,                      -- JSON, only the fields the holding page shows
    fetched TEXT
);
CREATE TABLE IF NOT EXISTS pf_fund (           -- look-through of an ETF you hold (allocation.py)
    symbol    TEXT PRIMARY KEY,        -- Yahoo symbol of the ETF
    sectors   TEXT,                    -- JSON {sector: weight}
    countries TEXT,                    -- JSON {country: weight}
    source    TEXT,                    -- where the countries came from (e.g. "SEC report of VT")
    fetched   TEXT
);
CREATE TABLE IF NOT EXISTS pf_alerts (  -- alerts that fired (alerts.py), newest shown first on the Alerts page
    id          INTEGER PRIMARY KEY,
    key         TEXT UNIQUE,              -- what it is about, so the same thing never fires twice
    created     TEXT,                     -- local time YYYY-MM-DD HH:MM
    kind        TEXT,                     -- limit | move | portfolio | earnings_soon | earnings | dividend | analysts
    t212_ticker TEXT,
    name        TEXT,
    title       TEXT,
    body        TEXT,
    level       INTEGER,                  -- -2 very negative .. 2 very positive (colour on the page)
    push        INTEGER,                  -- 1: meant for the phone
    pushed      TEXT,                     -- when it went to the phone; NULL: not (yet); 'no': not sent (see body of check())
    read        INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_ae_snapshot ON analyst_estimates(snapshot_date, section);  -- earnings_watch.plan
CREATE TABLE IF NOT EXISTS earnings_watch (  -- results days being watched (earnings_watch.py), one row per report
    ticker        TEXT NOT NULL,
    date          TEXT NOT NULL,          -- report day (New York date, from Yahoo's calendar)
    cik           INTEGER,
    expect        TEXT,                   -- JSON: what analysts expected (eps, revenue, ...), saved BEFORE the report
    created       TEXT,
    checked_sec   TEXT,                   -- last time the SEC's filing list was read (UTC)
    out_at        TEXT,                   -- when the 8-K with results (item 2.02) was seen
    out_accn      TEXT,
    press_url     TEXT,                   -- the press release (exhibit 99.1) or the filing's page
    result        TEXT,                   -- JSON: preliminary numbers (Yahoo; pr_* = read from the press release)
    checked_yahoo TEXT,
    filed_at      TEXT,                   -- when the 10-Q / 10-K was loaded
    filed_accn    TEXT,
    official      TEXT,                   -- JSON: revenue / eps of the quarter from the filing (the terminal's own numbers)
    alerted       TEXT,
    done          TEXT,                   -- watch over (everything in, or the window passed)
    PRIMARY KEY (ticker, date)
);
CREATE TABLE IF NOT EXISTS pf_alert_state (  -- what the checks remember between runs (armed price limits, targets)
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS pf_fund_holdings ( -- what a (twin) ETF holds, for the X-ray tab (xray.py)
    symbol  TEXT PRIMARY KEY,          -- US ETF whose SEC report was read (e.g. VT for VWCE)
    as_of   TEXT,                      -- report date
    rows    TEXT,                      -- JSON [[name, isin, weight], ...] shares only
    fetched TEXT
);
CREATE TABLE IF NOT EXISTS pf_events (         -- upcoming earnings / dividend dates of your holdings (events.py)
    symbol  TEXT PRIMARY KEY,
    data    TEXT,                      -- JSON from events.fetch
    fetched REAL                       -- unix time
);
CREATE TABLE IF NOT EXISTS news_cache (       -- news per Yahoo symbol (news.py), a cache: not personal, not in a starter pack
    symbol  TEXT PRIMARY KEY,
    data    TEXT,                      -- JSON list of news items (news.item)
    fetched REAL                       -- unix time
);
CREATE TABLE IF NOT EXISTS insider_filings (   -- Form 4s already read (insiders.py): never downloaded twice
    accn    TEXT PRIMARY KEY,
    cik     INTEGER,                   -- the company (issuer)
    filed   TEXT,
    status  TEXT,                      -- ok / empty / error
    fetched TEXT
);
CREATE TABLE IF NOT EXISTS insider_trades (    -- one row per stock transaction of a Form 4
    accn        TEXT,
    line        INTEGER,
    cik         INTEGER,
    filed       TEXT,
    date        TEXT,                  -- transaction date
    owner       TEXT,
    owner_cik   INTEGER,
    role        TEXT,                  -- "CEO", "Director", "10% owner", ...
    code        TEXT,                  -- P buy, S sale, A award, M exercise, F tax, G gift (CODES)
    ad          TEXT,                  -- A acquired / D disposed
    shares      REAL,
    price       REAL,                  -- dollars per share (None for awards / gifts)
    owned_after REAL,
    direct      TEXT,                  -- D direct / I indirect (a trust, a fund)
    plan        INTEGER,               -- 1: under a 10b5-1 trading plan (set up in advance)
    PRIMARY KEY (accn, line)
);
CREATE INDEX IF NOT EXISTS insider_trades_cik ON insider_trades (cik, date);
CREATE TABLE IF NOT EXISTS issuer_cusips (     -- a company's share CUSIPs, from Schedule 13G/13D filings (investors.py)
    cik     INTEGER PRIMARY KEY,
    cusips  TEXT,                      -- JSON list, [] when none was found
    checked TEXT
);
CREATE TABLE IF NOT EXISTS inv_filers (        -- famous investors whose 13F is followed (investors.FAMOUS)
    cik     INTEGER PRIMARY KEY,
    name    TEXT,                      -- the filer's SEC name (Berkshire Hathaway Inc)
    checked TEXT
);
CREATE TABLE IF NOT EXISTS inv_filings (       -- their latest two 13F-HR filings
    accn        TEXT PRIMARY KEY,
    cik         INTEGER,
    period      TEXT,                  -- quarter end
    filed       TEXT,
    total_value REAL,                  -- dollars, shares only
    positions   INTEGER
);
CREATE TABLE IF NOT EXISTS inv_holdings (      -- what is in them: one row per CUSIP (shares only), dollars
    accn   TEXT,
    cusip  TEXT,
    issuer TEXT,
    cls    TEXT,
    shares REAL,
    value  REAL,
    PRIMARY KEY (accn, cusip)
);
CREATE TABLE IF NOT EXISTS holders_cache (     -- largest holders per Yahoo symbol (investors.py), a cache
    symbol  TEXT PRIMARY KEY,
    data    TEXT,
    fetched REAL
);
CREATE TABLE IF NOT EXISTS pf_symbols (       -- Trading 212 code -> Yahoo symbol (portfolio.py), NULL = not found
    t212_ticker TEXT PRIMARY KEY,
    yahoo       TEXT,
    checked     TEXT
);
-- Your own trades, typed in or imported from a broker's CSV (own.py), for a portfolio without Trading 212.
-- own.rebuild() turns them into the same pf_trades / pf_positions / pf_dividends rows a Trading 212 sync makes.
CREATE TABLE IF NOT EXISTS pf_own_trades (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    date     TEXT NOT NULL,                -- YYYY-MM-DD
    symbol   TEXT NOT NULL,                -- Yahoo symbol: AAPL, BRK-B, ASML.AS, VWRL.L
    name     TEXT,
    currency TEXT,                         -- the currency the share trades in, as Yahoo gives it (USD, EUR, GBp)
    side     TEXT NOT NULL,                -- BUY / SELL
    quantity REAL NOT NULL,                -- shares as bought (before later splits)
    price    REAL NOT NULL,                -- per share, in `currency`
    fees     REAL,                         -- in `currency`
    note     TEXT,
    added    TEXT
);
CREATE TABLE IF NOT EXISTS pf_own_divs (       -- dividends per share from Yahoo, for your own trades (own.py)
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,                  -- ex-dividend date
    amount REAL,                           -- per share, today's share basis, in the share's currency
    PRIMARY KEY (symbol, date)
);
-- Watchlists (watchlist.py): personal like every pf_ table, so never in a starter pack.
CREATE TABLE IF NOT EXISTS pf_watchlists (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    created  TEXT
);
CREATE TABLE IF NOT EXISTS pf_watch (          -- one ticker on one list
    list_id     INTEGER NOT NULL REFERENCES pf_watchlists(id) ON DELETE CASCADE,
    ticker      TEXT NOT NULL,
    position    INTEGER NOT NULL DEFAULT 0,    -- your own order on the list
    added       TEXT,                          -- date added (YYYY-MM-DD)
    added_price REAL,                          -- price on that day, for "since added"
    buy_below   REAL,                          -- your own price: the row lights up below it
    note        TEXT,
    PRIMARY KEY (list_id, ticker)
);

CREATE TABLE IF NOT EXISTS fetch_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker     TEXT,
    source     TEXT,     -- e.g. sec_facts, sec_submissions, yahoo_prices
    status     TEXT,     -- ok / error / cached
    rows       INTEGER,
    message    TEXT,
    fetched_at TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect() -> sqlite3.Connection:
    if not DB_PATH.exists():                    # first start on a new PC: use a starter pack if there is one
        from . import starter
        starter.import_if_needed()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # wait up to a minute when another process (app, daily update, batch load) is writing
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


# Columns added after the first version: (table, column, type). Existing databases get them here.
MIGRATIONS = [
    ("financials", "prior_year_value", "REAL"),
    ("valuation_daily", "p_ffo_ttm", "REAL"),
    ("valuation_daily", "shares_source", "TEXT"),   # 'yahoo' when the share count is Yahoo's (metrics.py)
    ("etfs", "holdings_as_of", "TEXT"),
    ("etfs", "holdings_source", "TEXT"),
    ("etfs", "holdings_count", "INTEGER"),
    ("etfs", "holdings_checked", "TEXT"),
    ("etfs", "countries", "TEXT"),
    ("etfs", "asset_mix", "TEXT"),
    ("companies", "display_name", "TEXT"),      # Yahoo's name ("Amazon.com, Inc."), nicer than the SEC's ("AMAZON COM INC")
    ("pf_alerts", "ticker", "TEXT"),            # the app's ticker of the stock (links, the stock page's bell); NULL: none
]


def _migrate(conn):
    for table, column, ctype in MIGRATIONS:
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ctype}")
            if (table, column) == ("pf_alerts", "ticker"):      # alerts stored before: the ticker from their key
                conn.execute("""UPDATE pf_alerts SET ticker = CASE
                                  WHEN t212_ticker LIKE 'wl:%' OR t212_ticker LIKE 'st:%' THEN substr(t212_ticker, 4)
                                  WHEN t212_ticker LIKE '%\\_US\\_EQ' ESCAPE '\\' THEN replace(substr(t212_ticker, 1, length(t212_ticker) - 6), '_', '.')
                                  END""")
    # Names stored before clean_name existed (e.g. "PROGRESSIVE CORP/OH/")
    for table, key in (("companies", "cik"), ("tickers", "ticker")):
        rows = conn.execute(f"SELECT {key}, name FROM {table} WHERE name LIKE '%/'").fetchall()
        conn.executemany(f"UPDATE {table} SET name=? WHERE {key}=?",
                         [(clean_name(n), k) for k, n in rows if clean_name(n) != n])
    conn.commit()


_STATE_TAG = re.compile(r"\s*/[A-Za-z]{2,4}/\s*$")


def clean_name(name):
    """Drop the SEC's disambiguation tag at the end of a name: "PROGRESSIVE CORP/OH/" -> "PROGRESSIVE CORP".
    The SEC adds it (usually the state of incorporation, sometimes NEW) when names would clash."""
    if not name:
        return name
    prev = None
    while prev != name:
        prev, name = name, _STATE_TAG.sub("", name)
    return name.strip()


_KEEP_UPPER = {"LLC", "LP", "L.P.", "PLC", "NV", "N.V.", "SA", "S.A.", "AG", "SE", "ETF", "REIT", "USA", "US", "II", "III",
               "IV", "AB", "ASA", "LTD.", "HP", "IBM", "AT&T", "3M", "CVS", "UPS", "AES", "SBA", "UDR", "CMS", "PPG", "TJX",
               "NVR", "SLB", "PTC", "CDW", "KLA", "NXP", "EOG", "PNC", "MSCI", "CBRE", "D.R.", "J.M.", "W.R.", "C.H.", "T."}


def readable_name(name):
    """A company name for the screen. The SEC writes names in capitals ("COCA COLA CO"); Yahoo's display name
    is used where we have it, otherwise this makes the capitals readable ("Coca Cola Co"). Mixed-case names
    are left alone."""
    name = re.sub(r"\s*/[A-Za-z]{2,4}$", "", clean_name(name) or "")       # "LIMITED/NV" without the end slash
    if not name or name != name.upper() or not any(c.isalpha() for c in name):
        return name
    words = []
    for w in name.split():
        core = w.strip(",.")
        if core in _KEEP_UPPER or w in _KEEP_UPPER or (len(core) <= 3 and core.isalpha() and not any(v in core for v in "AEIOUY")):
            words.append(w)                      # initials and abbreviations: "SBA", "CVS", "LLC"
        else:
            words.append(re.sub(r"(?<=')S\b", "s", w.capitalize()) if "'" in w else
                         "-".join(x.capitalize() for x in w.split("-")))
    return " ".join(words)


def log(conn, ticker, source, status, rows=None, message=None):
    conn.execute(
        "INSERT INTO fetch_log (ticker, source, status, rows, message, fetched_at) "
        "VALUES (?,?,?,?,?,?)",
        (ticker, source, status, rows, message, now_iso()),
    )
    conn.commit()


def upsert_company(conn, cik: int, **fields):
    """Insert or update a company row; only non-None fields overwrite existing values."""
    fields = {k: v for k, v in fields.items() if v is not None}
    fields["updated_at"] = now_iso()
    conn.execute("INSERT OR IGNORE INTO companies (cik) VALUES (?)", (cik,))
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE companies SET {sets} WHERE cik=?", (*fields.values(), cik))
    conn.commit()


# SEC industry code (SIC) -> sector, for the few companies Yahoo gives no sector (Fiserv). Coarse but
# never empty: (first digits, sector), the longest matching prefix wins.
_SIC_SECTOR = [
    ("01", "Consumer Defensive"), ("02", "Consumer Defensive"), ("10", "Basic Materials"), ("12", "Energy"),
    ("13", "Energy"), ("14", "Basic Materials"), ("15", "Industrials"), ("16", "Industrials"), ("17", "Industrials"),
    ("20", "Consumer Defensive"), ("21", "Consumer Defensive"), ("22", "Consumer Cyclical"), ("23", "Consumer Cyclical"),
    ("24", "Basic Materials"), ("25", "Consumer Cyclical"), ("26", "Basic Materials"), ("27", "Communication Services"),
    ("28", "Basic Materials"), ("283", "Healthcare"), ("284", "Consumer Defensive"), ("29", "Energy"),
    ("30", "Basic Materials"), ("31", "Consumer Cyclical"), ("32", "Basic Materials"), ("33", "Basic Materials"),
    ("34", "Industrials"), ("35", "Industrials"), ("357", "Technology"), ("36", "Technology"), ("37", "Industrials"),
    ("371", "Consumer Cyclical"), ("38", "Technology"), ("384", "Healthcare"), ("385", "Healthcare"),
    ("39", "Consumer Cyclical"), ("4", "Industrials"), ("48", "Communication Services"), ("49", "Utilities"),
    ("50", "Industrials"), ("51", "Consumer Defensive"), ("52", "Consumer Cyclical"), ("53", "Consumer Cyclical"),
    ("54", "Consumer Defensive"), ("55", "Consumer Cyclical"), ("56", "Consumer Cyclical"), ("57", "Consumer Cyclical"),
    ("58", "Consumer Cyclical"), ("59", "Consumer Cyclical"), ("6", "Financial Services"), ("65", "Real Estate"),
    ("6798", "Real Estate"), ("7", "Consumer Cyclical"), ("73", "Technology"), ("78", "Communication Services"),
    ("79", "Consumer Cyclical"), ("80", "Healthcare"), ("87", "Industrials"),
]


def sector_of(sector, sic):
    """Yahoo's sector, or one derived from the SEC industry code when Yahoo has none."""
    if sector or not sic:
        return sector
    s = str(sic)
    best = max(((len(p), sec) for p, sec in _SIC_SECTOR if s.startswith(p)), default=None)
    return best[1] if best else None
