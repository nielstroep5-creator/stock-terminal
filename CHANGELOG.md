# Release notes

What changed in each version of Stock Terminal, newest first. The version on your computer is in
Settings > About. Versions: 1.0.1 = fixes, 1.1.0 = new features.

## 1.2.0 (coming)

- First start: one pop-up at a time (the data download comes before the update question), the Home page offers
  the S&P 500 download right there, the starter pack shows the date of its numbers, and the folder example fits
  your computer (Windows or Mac).
- Your own trades work offline for US shares already in the app: adding one, its price on the day and the
  portfolio value come from the app's own daily prices (split-aware) until Yahoo answers again.
- Importing a broker file (DEGIRO) with the company name in the ticker column now finds the share by its ISIN,
  and a file with 2024-03-15 style dates says "year-month-day" instead of a code.
- Settings in a small window: the section links sit in a row above the settings instead of lying on top of them.
- The top bar shows how fresh the prices are: "Live", or "Offline, closes of Nov 13" / "Old prices" in orange
  when Yahoo does not answer or the daily update has not run, and "Recalculating ratios 40%" while the ratios
  are worked out again after a starter pack or an update.
- Without internet the app no longer hangs: the Ownership tab answers in a few seconds instead of 16, Home's
  index cards right away, and the Dow Jones card has its small chart from the stored closes too.
- Search: "google" finds Alphabet, "facebook" Meta (and other well-known brand names); "apple" no longer lists
  Pineapple or Applied Materials.
- Company names in capitals with a few small letters ("COCA-COLA EUROPACIFIC PARTNERS plc") are written
  normally, and "of", "and", "the" stay small ("Bank of America").
- No more red errors in the browser console for companies without a logo.
- The update pop-up of a copy made with git lists the new release notes instead of the programmers' commit titles.
- Negative numbers use a real minus sign (−1.2B), the stock page says "Net debt 12.3B" instead of "Net cash -12.3B",
  and growth numbers, ratings and the Compare analyst rows show one decimal instead of two.
- Home: "Biggest moves" says it covers all companies in the app (not only yours).
- Compare: the latest numbers at the end of the lines no longer sit on top of each other, and the tables that
  scroll sideways show a soft edge instead of a column that looks cut off.
- Alerts > Rules fits a small window (below about 1000 pixels the list ran off the side).
- ETFs page without internet: the change column shows the last stored day ("Last day") instead of staying empty.
- Less small print: the explanations under the Earnings calendar, the Compare chart and some Portfolio tabs sit
  behind a "How to read this" button.
- Earnings page and Home's "Coming up": US shares in your portfolio now count (they were missing, so the page
  stayed empty for a portfolio of your own trades).
- Data from the first starter pack gets its missing operating income (J&J, Lilly, Exxon, IBM and about 120 more)
  worked out from the stored lines at the next start, so their operating margin and related ratios show.
- Gross, operating and EBITDA margins above 100% (an incomplete revenue line, mostly REITs) are left blank instead
  of showing an impossible number.
- The code is now open source: github.com/nielstroep5-creator/stock-terminal (MIT licence).

## 1.1.0 (2026-11-25)

- Sectors page (under Screener): every sector and industry with its companies, the median valuation, margins,
  growth and returns, and each company's rank inside its group ("#3/24"). A stock's Overview shows where it
  ranks in its industry and sector; Sector and Industry in its profile link to these pages.
- Settings > Data and accounts > Data folder: which folder this copy uses for your data, with a button to open it.
  A test copy (the code folder) says "TEST" next to the name and in the window title, so it can't be mixed up
  with the installed app.
- Ownership tab on the stock page: insider trades from the SEC's Form 4 filings (buys and sales per month, every
  trade with price and value), the largest institutional holders, and which famous investors (Buffett, Ackman,
  Burry, ...) hold the stock according to their 13F reports, with what they bought or sold last quarter.
  A fund without a recent 13F report is listed last and marked "old".
  A new alert tells you when an insider buys a stock you own or watch.
- Data from a starter pack or another copy made by an older version gets its ratios recalculated once, in the
  background, at the next start (this fills the 3/5/10-year growth numbers that the first starter pack lacked).
- Your own trades (without Trading 212) now also work on the box 3 tax page (amounts in euros, also for a
  portfolio kept in dollars) and in the Costs tab (the fees on your trades).

## 1.0.0 (2026-11-25)

The first release: everything built so far, made ready for anyone to install.

**Companies**
- Every US company that files with the SEC: search by name or ticker, a click loads it.
- Stock page: Overview (price chart, key stats, news), Financials, Segments, Ratios, Valuation,
  Estimates (analysts) and Dividends, annual, quarterly and TTM, back to 2009.
- All numbers in US dollars, also for foreign companies (form 20-F, e.g. ASML).
- Data checks: each company's numbers are compared with Yahoo; a data-quality card shows the result.

**Tools**
- Screener with about 75 numbers per company, saved screens.
- Compare: chart any number (revenue growth, margins, price, ...) for several companies side by side.
- DCF on the Valuation tab: fair value per share from your own growth and discount rate, the growth today's
  price assumes (reverse DCF), and a table of what happens if you are a bit off. Your numbers are remembered.
- Watchlists, ETF pages with holdings, home screen with the market and your lists.
- Earnings page: when the companies on your watchlists and in your portfolio report next (week view or a list with
  estimates), and past surprises (reported EPS against the estimate, beat rate, share price reaction).
- Portfolio from Trading 212 (holdings, history, allocation, X-ray, calendar) and a Dutch box 3 tax overview.
- Portfolio without Trading 212: add your own buys and sells, or import your broker's CSV export. Holdings,
  dividends (worked out from Yahoo) and the comparison with the S&P 500 work the same.
- Alerts in the app and on your phone (price limits, big moves, results, dividends, analysts), and a
  morning or weekly summary.

**Data**
- Free sources only: SEC EDGAR and Yahoo Finance, no account or API key.
- Daily update at 07:00 and a check for new results every 5 minutes on results days.
- Starter pack: the S&P 500 ready in minutes instead of hours.
- After a stock split, the company is recalculated the same day, so market cap, EPS and P/E stay right (before, the
  numbers waited for the next quarterly report); a split the numbers missed shows as a market cap warning.

**The app**
- A real app: Stock Terminal.exe with a Windows installer (no Python needed), Stock Terminal.app for Mac.
  Your data lives in your own user folder and survives updates and reinstalling.
- Company data in one click on a new install: get the S&P 500 (starter pack from the download page) or use the
  data of another Stock Terminal copy on your computer. Offered after the welcome screen, and in Settings > Data.
- Report a problem (Settings > About, or "Report this" on an error page): a short report with the version and the
  last errors, to copy or send on GitHub. You see everything in it first; nothing is sent by itself.
- A download page with the newest version for Windows and Mac. Every build is installed on a clean Windows PC
  and loads a company before it is published.
- Settings > About: your version, "Check for updates" and "Update now" (downloads, tests and installs the new
  version, then restarts the app); a pop-up at start when a newer version is available (can be skipped or turned off).
