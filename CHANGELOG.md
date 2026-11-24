# Release notes

What changed in each version of Stock Terminal, newest first. The version on your computer is in
Settings > About. Versions: 1.0.1 = fixes, 1.1.0 = new features.

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
