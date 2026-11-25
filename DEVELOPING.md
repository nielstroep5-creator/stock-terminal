# Developing Stock Terminal

How the code works and how to run it from source. For how to contribute, see [CONTRIBUTING.md](CONTRIBUTING.md).

## Data sources
- **SEC EDGAR** (free): ticker list, company profile, filing list, and every XBRL financial fact.
- **Yahoo Finance** via `yfinance` (free, unofficial): daily prices, dividends, splits, sector/industry, description.

Everything goes into one SQLite file: `data/terminal.db`. Raw SEC JSON is cached in `data/raw/`.

## Setup (once)
```
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```
(macOS / Linux: `python3.12 -m venv .venv` and `.venv/bin/python -m pip install -r requirements.txt`.)
SEC requires a contact email in requests. The app asks for it on first start (and under
Settings in the left sidebar); it is saved in `data/settings.json` with your other settings (not committed to git).

**Skip the long first download:** put a starter pack (`stock-terminal-data-*.zip`, ~360 MB, made with
`python -m stockterminal.starter export`) in this folder before the first start. The app unpacks it by
itself and has every company right away. It leaves out the raw SEC data; a company gets that once,
automatically, the first time it is recalculated or files a new report (see `stockterminal/starter.py`).

**Shared company list:** `companies.txt` lists every company. The app uploads companies you add to GitHub
and the daily update loads companies someone else added (see `stockterminal/shared.py`).

## Use
```
.venv\Scripts\python -m stockterminal.fetch AAPL MSFT          # download + standardize
.venv\Scripts\python -m stockterminal.standardize AAPL MSFT    # only re-standardize (no downloads)
.venv\Scripts\python -m stockterminal.validate AAPL MSFT       # checks -> data/reports/validation_report.md
.venv\Scripts\python -m stockterminal.check AAPL MSFT          # quick raw-data sanity report
```
Re-running is safe: rows are updated, not duplicated.

### Loading many companies (S&P 500)
```
.venv\Scripts\python -m stockterminal.load_index sp500              # or double-click "Load S&P 500.bat"
.venv\Scripts\python -m stockterminal.load_index sp500 --status     # progress only
.venv\Scripts\python -m stockterminal.load_index my_list.txt        # any list of tickers
.venv\Scripts\python -m stockterminal.quality_report                # quality report of everything loaded
```
- Phase 1 loads every company with the last 5 years of filings (usable in the app right away, ~25 s each);
  phase 2 reads the older filings (segments back to 2009, ~35 s each). Then `data/reports/sp500_quality.md`.
- Resumable: stop it any time and start again. Companies already loaded are skipped. Each company runs in its
  own process with a time limit and one retry; failures are listed in `data/logs/load_sp500_progress.json`.
- Share classes of one company (GOOGL/GOOG, FOXA/FOX, NWSA/NWS) are loaded once. `BRK.B` = `BRK-B`.
- Foreign filers with IFRS statements (20-F, e.g. TSMC) are not supported yet: prices only.
- `fetch --recent-years N` / `fetch --backfill` are the two phases for single tickers. Add `--refresh` to ignore the SEC cache,
`--no-prices` to skip Yahoo.

## How the data is cleaned (step 2)
- `mapping.py` lists every standard line item (revenue, capex, ...) with the XBRL tags that can
  hold it, in priority order, plus fallback formulas. No company-specific rules anywhere.
- Quarters are standalone (year-to-date cash flows are converted; Q4 = full year - 9 months).
- `value` = latest restated number; `original_value` = number as first reported.
- Share counts and per-share numbers are adjusted for stock splits.
- TTM (trailing twelve months) is built from 4 consecutive quarters.
- `calendar_quarter` (e.g. 2026Q3) lines up companies with different fiscal years.
- Daily valuation only uses numbers that were public on that day (no look-ahead).

- Growth rates are like-for-like: the number as first published divided by the year-ago number
  from that same filing (`prior_year_value`), so a restated year next to unrestated quarters
  cannot distort them.
- A tag that disagrees with the main tag inside the same filing measures something else and is
  never used to fill that year's missing quarters.
- Total D&A = sum of all depreciation/amortization lines on the cash-flow statement when a
  company splits it (read from each filing, `ext:d_and_a_sum`).
- Share counts with an obvious factor-1000 mistake in the filing are repaired.
- Dividend yield = payments of the last 12 months, counted back from the latest payment.
- A company's own (extension) tag on the income statement, balance sheet or cash-flow statement is
  used when its label matches a standard line and no standard tag has the number (e.g. Exxon's
  pre-2017 quarterly revenue, NVIDIA's "Marketable securities").
- A tag that disagrees with a higher-priority tag for the same period inside the same filing is
  not used for that period.
- Several share classes of different value (Berkshire: 1 A = 1500 B): EPS is that of the class
  that trades under the ticker, and share count / market cap are in equivalent shares of that
  class (ratio = EPS of each class / EPS of the traded class). Classes of equal value (Alphabet)
  are simply added up as before.
- Insurers (SEC industry code 63xx) get premiums, claims, net investment income and loss ratio
  instead of gross profit / operating income / EBITDA; EV-based multiples and P/FCF are blanked
  (as for banks). REITs (code 6798) get FFO = net income + D&A - gains on property sales +
  real-estate impairments, FFO per share and P/FFO.

- Stock splits vs spin-offs: Yahoo lists a spin-off as a "split" with an odd ratio (S&P Global 2026: 1.057).
  Only real splits adjust the SEC numbers; a spin-off is recognised by its odd ratio and an unchanged SEC
  share count, and prices/dividends before it are put back on the real-share basis (`splitbasis.py`,
  table `split_basis`). A reverse split at the same time as a spin-off (Hilton 2017) is read from the SEC
  share count.
- Capex includes purchased/developed software when a company reports it on its own cash-flow line.

## Older history under an earlier SEC registration
After a legal reorganisation a company continues under a new SEC number (CIK) and its older
filings stay under the old one (Marvell 2021, Alphabet 2015, ...). `sec.load_predecessors` finds
the old registrant automatically (it reported identical numbers for the same dates and then
stopped filing) and loads its history into the same company. Table: `predecessors`.
Free structured SEC data starts around 2009; nothing earlier exists for any company.

## Data quality
Every load runs the consistency checks and stores a score per company (`data_quality` table,
shown on the Overview tab): balance sheet balances, quarters add up to the year, gross profit and
net income are consistent, no missing periods.

Tables: `standard_items`, `financials`, `metrics`, `valuation_daily`, `predecessors`, `data_quality`.

- `python -m stockterminal.spotcheck` compares the most-used numbers of 30 random companies with Yahoo
  (`data/reports/spotcheck.md`).
- Numbers that failed a check get a warning sign in the Financials tab and a triangle in Compare charts.
- The daily update logs each updated company's quality score before and after; new warnings are listed
  in `data/reports/quality_alerts.md`. `Set up daily update.bat` (double-click once) makes Windows run the
  update every morning at 07:00.

## Analyst expectations (Yahoo)
`fetch` also stores analyst data; `python -m stockterminal.analyst` (or `Update analyst data.bat`) refreshes
only that, for every loaded company, without SEC downloads. Each run saves a dated snapshot, so a history of
estimates builds up from the first run on (Yahoo only shows today's numbers).
- `analyst_estimates`: EPS and revenue estimates (this/next quarter, this/next fiscal year: avg/low/high,
  analysts, growth), EPS trend (now vs 7/30/60/90 days ago), EPS revisions, growth incl. long-term (LTG),
  price targets, buy/hold/sell counts, next earnings date with expected EPS/revenue, forward P/E, forward EPS, PEG.
- `analyst_actions`: upgrades/downgrades with price-target changes. `earnings_surprises`: estimate vs actual EPS.
Nothing beyond next fiscal year exists for free except the long-term growth rate.

## The app (step 3)
- `python -m stockterminal.app` opens the window; `python -m stockterminal.app --serve` runs it in a browser
  at http://127.0.0.1:8765 instead.
- **Screens:** a left sidebar with Home, Portfolio (and Tax), Watchlist, Screener, ETFs, Compare and Data health;
  search from anywhere with Ctrl+K or `/`; `?` shows the keyboard shortcuts.
  The stock page has Overview, Financials, Segments, Ratios, Valuation, Dividends and Estimates.
- Source of the screen: `ui/` (React + TypeScript + Tailwind). It is built into `stockterminal/app/static/new/`,
  which is in git, so nobody needs Node.js to run the app. **Adding something? Read `ui/DESIGN.md` first**, so
  it looks and works like the rest.
- Light and dark theme; colours are tokens in `ui/src/index.css`.

## Alerts

The bell in the top bar shows the latest alerts and opens **Alerts** (`/alerts/list`, rules at `/alerts/rules`): a price limit crossed,
a big daily move (one stock or the whole portfolio), results coming up and their outcome, a dividend cut, and analysts changing their
estimate, price target or advice. They are about the holdings in your Trading 212 portfolio, the stocks on your watchlists (a buy-below
price works as a price limit), and any other stock you set an alert on with the **bell next to the star** on its stock or ETF page
(price limits, its own daily-move %, what to skip, mute). Price limits and big moves are checked every 5 minutes while markets are open
(by the earnings watch task, one batched Yahoo request); everything is checked in the daily update (07:00) and with "Check now".
A new alert while the app is open shows a pop-up; Home has an Alerts card. Under **Rules** you set each kind on or off, its percentages,
whether it also goes to your phone through [ntfy](https://ntfy.sh) (free app, no account: subscribe to the topic name you choose there),
and see every stock's limits in **Your stocks**. Quiet hours hold phone messages until the morning; at most one phone message per stock
per day, except price limits and results. Rules are kept in your own `data/settings.json` (prefs `alerts`, per stock `alerts_stock`
keyed by ticker); fired alerts in `pf_alerts` (`stockterminal/alerts.py`).

## Releasing (Niels decides when)
1. On `master`: set `__version__` in `stockterminal/__init__.py` to the final number (no `-dev`) and give the
   `CHANGELOG.md` section its date. Push and wait until the self-test on GitHub is green.
2. Tag **that commit on master** (`git tag v1.2.0 && git push origin v1.2.0`), never the download-page commit
   that the build pushes. The build checks that the tagged commit has the app and that the tag matches
   `__version__`, then publishes the release with the notes from `CHANGELOG.md` and an install footer.
3. A test version is `v1.2.0-rc1` with `__version__ = "1.2.0-rc1"`: it becomes a pre-release.
4. Afterwards set `__version__` to the next `-dev` number.

## Code layout
- `stockterminal/config.py` – paths and settings
- `stockterminal/db.py` – database schema and helpers
- `stockterminal/sec.py` – SEC downloads
- `stockterminal/prices.py` – Yahoo downloads
- `stockterminal/mapping.py` – catalog of standard line items and their XBRL tags
- `stockterminal/standardize.py` – raw facts -> clean financial statements
- `stockterminal/metrics.py` – growth, margins, returns, valuation
- `stockterminal/validate.py` – validation report
- `stockterminal/quality.py` – data quality score per company (runs on every load)
- `stockterminal/fetch.py` – command line entry point
- `stockterminal/check.py` – verification report
- `stockterminal/filings.py` – per-filing XBRL (segments, company-specific tags)
- `stockterminal/segments.py` – standardized segment / product / geography data
- `stockterminal/app/` – desktop app (server.py = local API, static/new/ = the built screen from ui/)
- `ui/` – source of the new design (`DESIGN.md` = how to build in it, `FEATURES.md` = what moved over)
