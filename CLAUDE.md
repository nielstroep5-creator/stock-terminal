# Stock Terminal: rules for working on this project

This repository is public and open source (MIT). Niels and TvR maintain it, each with Claude; others may send
pull requests. Follow these rules so the app keeps working for everyone who downloads it.

## Stable and development
- `master` = development. Releases are tags `vX.Y.Z` (must match `__version__`); the build publishes them under
  Releases, and installed apps only update to those. `vX.Y.Z-rc1` = pre-release (test version).
- Urgent fix for a released version while `master` holds unfinished work: branch `release/X.Y` from the tag,
  fix there, tag `vX.Y.Z+1`, and merge the fix back into `master`.
- Everything here is public: no personal data, emails, portfolios or tokens in code, commits, issues or PRs.

## Every time
1. **`git pull` before you start.** The other person may have pushed since.
2. **Always update `WORKING.md`, for every task, however small.** Don't change a part someone else has
   listed there. Before the first edit, add a line (`- name: what you are doing (files or part of the app) - since date`)
   and push it; remove it when the work is merged or abandoned. Do this on `master`, even when the work itself
   happens on a branch, because otherwise the other person cannot see it. Change only `WORKING.md` in that commit.
3. **Small steps:** when one change works, commit and push it right away. Don't collect a day of work.
4. **Run the self-test before every push:** `python -m stockterminal.selftest` (or `Run self-test.bat`).
   Don't push when it fails. GitHub runs the same test after every push and emails when it fails.
   The pre-push hook (`.githooks/pre-push`) runs it for you and blocks a failing push, or one that adds
   data files or email addresses. Make sure it is on (`git config core.hooksPath .githooks`; the start
   file sets it) and never bypass it with `--no-verify`. In a session without the packages it only warns:
   say so to the user.
   Safety net: after downloading a new version, `Start Stock Terminal.bat` runs the self-test and goes
   back to the previous version (with a message) when it fails, so a broken push never stops the app.
5. **Bigger or risky changes:** do them on a branch and open a pull request, so the other person can
   look first. Small fixes can go straight to `master`.
6. Short, clear commit messages that say what changed for the user.

## Never
- Commit `data/` (database, settings, exports), `sec_contact.txt`, `.venv/`, zip files or any secret.
  Everyone keeps their own data folder; the self-test fails when such files are in git.
- Hardcode anything to one PC: no `C:\Users\...` paths, no one person's email or ticker list. Paths are
  relative to the app folder (`stockterminal/config.py`: `ROOT`, `DATA_DIR`).
- Start the app's own modules with `sys.executable -m ...`: use `config.module_cmd(module, ...)`, which also works in the
  installed app (no Python there). Data paths come from `config.DATA_DIR` (the user's own folder in the installed app).
- Break the app for someone who just downloaded it: a fresh install without data must start
  (`Start Stock Terminal.bat` pulls, installs packages and starts it).

## Settings and view choices
- Per-user settings live in `data/settings.json` (`stockterminal/settings.py`), not in the browser:
  the app window forgets browser storage when it closes.
- Every new switch / toggle / view choice must be remembered: `useSettings()` in `ui/src/lib/settings.tsx`
  (`update` for a real setting, `pref` / `setPref` for a view choice, group `ui2`). Never browser storage.
- A real setting (in the Settings menu): add it to `DEFAULTS` / `CHOICES` in `settings.py` and a row in
  `ui/src/pages/Settings.tsx` (`ROWS`, in the section it belongs to). The Settings search finds it by itself.
- Some choices keep the keys of the old screen (group `state`: `scrF`, `scrSaved`, `pfTab`, ...) so that what
  people saved before the redesign still shows. Keep using those keys.

## The screen (`ui/`)
- The screen is built from `ui/` (React + TypeScript + Tailwind) into `stockterminal/app/static/new/` and served
  at `/` (also `/new`). The old plain-JavaScript screen was removed on 2026-11-17; `/old` sends you to `/`.
- **New features follow `ui/DESIGN.md`** (building blocks, colours, tables, icons, wording, remembering choices,
  a checklist for a new page). Copy the nearest existing page rather than inventing a new look.
- After every change in `ui/`: `cd ui`, `npm install` (first time), `npm run build`, and commit the changed
  `stockterminal/app/static/new/` together with the source. The built files are in git so that nobody needs
  Node.js to start the app; the self-test fails when they are older than the source.
- While working: `python -m stockterminal.app --serve` plus `npm run dev` in `ui/` (live reload).

## Code layout
- `stockterminal/` Python package
  - `fetch.py`, `sec.py`, `prices.py`, `analyst.py`, `etf.py`, `holdings.py`: download data (SEC, Yahoo)
  - `standardize.py` + `mapping.py`: SEC facts -> standard line items; `metrics.py`: ratios, valuation
  - `dividends.py`: the Dividends tab (yield, growth, streaks, specials, payout ratios, payment history)
  - `watchlist.py`: the Watchlist page (named lists in `pf_watchlists` / `pf_watch`; `tickers()` = everything watched)
  - `alerts.py`: alerts (price limits, moves, results, dividend, analysts) for the portfolio, the watchlists and any stock with a bell alert, in the
    app (`pf_alerts`) and on the phone (ntfy); run by `daily.py` and "Check now"; rules in prefs `alerts` / `alerts_stock`
  - `news.py`: news per stock from Yahoo (stock page Overview, holding page), cached half an hour in `news_cache`
  - `summary.py`: the morning / weekly summary to the phone (holdings, movers, the week ahead), sent after the alerts check
  - `logos.py`: company logos (GitHub logo set by ticker, else the website icon), cached in `data/logos/`, served at `/api/logo/{t}`
  - `screener.py`: the Screener page (one row per company with ~75 numbers, kept in memory; filtering happens in the screen)
  - `quality.py`, `validate.py`, `spotcheck.py`: data checks; `daily.py`: the 07:00 daily update
  - `db.py`: SQLite schema (`data/terminal.db`); `config.py`: paths
  - `app/server.py`: local web server (FastAPI) used by the app window; `app/__main__.py`: starts it
  - `app/static/new/`: the screen, **built files, never edit by hand** (made from `ui/`, see above); `app/static/logo.svg`
  - `selftest.py`: the self-test
  - `appupdate.py`: updates of the app itself (Settings > About, pop-up at start): git copy = pull + packages + self-test
    with rollback; installed app = newest release in this repository's Releases (`RELEASES_REPO`)
  - `datasetup.py`: company data for a new install (Settings > Data, pop-up after the welcome screen): the starter pack
    from this repository's Releases (release asset `stock-terminal-data-*.zip`) or another copy's data, prepared in
    `data/incoming/` and swapped in at the next start (`apply_incoming`, before anything opens the database)
  - `report.py`: "Report a problem" (Settings > About): version + last errors of `data/logs/app_errors.log` (screen and
    server errors), emails / user names removed, to copy or open as an issue in this repository; never sent by itself
  - `__init__.py`: the app version (`__version__`, shown in Settings > About); change it together with `CHANGELOG.md`
    (release notes, newest first; add a line under the coming version for every change users notice)
- `packaging/`: the installed app (PyInstaller recipe `stock-terminal.spec`, `launcher.py` = the window, `runner.py` =
  background jobs, `installer.iss` = Windows installer, icons); built by `.github/workflows/build-app.yml` (also a clean-PC install test)
- `docs/`: the download page (GitHub Pages, branch master, folder /docs) and the screenshots of the README
- `ui/`: source of the screen (React + TypeScript + Tailwind, shadcn-style parts in `src/components/ui/`,
  shared page parts in `src/components/app/`, pages in `src/pages/`). `ui/DESIGN.md`: how to build in it.
- `companies.txt`: shared list of companies, synced through git by the daily update
- `.bat` files: double-click helpers for Windows (start, daily update, recalculate, self-test)
- `.command` files: the same helpers for macOS (start, daily update, self-test); keep both in step when you change one

## Running things
- App: `Start Stock Terminal.bat`, or `.venv\Scripts\python.exe -m stockterminal.app`
  (`--serve` = server only, open http://127.0.0.1:8765 in a browser)
- Recalculate all companies from local data: `python -m stockterminal.standardize [TICKER ...]`
- Load a company: `python -m stockterminal.fetch TICKER`
