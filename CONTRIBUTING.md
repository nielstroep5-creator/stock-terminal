# Contributing

Thanks for helping. Bug reports, wrong numbers, ideas and pull requests are all welcome.

## Reporting a problem

- In the app: Settings > About > **Report a problem**. It collects the version and the last errors (emails and user
  names removed) so you can paste them into an issue. Nothing is sent by itself.
- Wrong number? Say which company, which number and period, what the app shows, and what the company's own report says.

## Changing the code

1. Fork the repository and make a branch.
2. Set up as in the README ("Run from source"), then `git config core.hooksPath .githooks` so the checks run
   before every push.
3. Make your change. Rules that keep the app working for everyone (details in [CLAUDE.md](CLAUDE.md)):
   - Never commit the `data/` folder, databases, settings, zip files, email addresses or other secrets.
   - Nothing tied to one computer: paths come from `stockterminal/config.py`.
   - Every switch or view choice is remembered (`data/settings.json`, not browser storage).
   - Company numbers are shown in US dollars.
   - Screen changes follow [ui/DESIGN.md](ui/DESIGN.md). After changing `ui/`, run `npm run build` in `ui/` and
     commit the rebuilt `stockterminal/app/static/new/` too.
   - Users notice it? Add a line to [CHANGELOG.md](CHANGELOG.md) under the coming version.
4. Run the self-test: `python -m stockterminal.selftest`. It must pass.
5. Open a pull request against `master` and describe what changes for the user.

## How releases work

`master` is development. A release is a tag `vX.Y.Z` matching `stockterminal/__init__.py`; GitHub Actions then
builds the Windows installer and the Mac app and publishes them under Releases, where installed apps find them.
Test versions are tagged `vX.Y.Z-rc1` and published as pre-releases. An urgent fix for a release while `master`
holds unfinished work goes on a `release/X.Y` branch and is tagged from there.
