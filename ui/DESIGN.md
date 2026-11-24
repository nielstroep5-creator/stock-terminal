# The screen: how to add things so they look and work the same

The app was rebuilt in November 2026 (React + TypeScript + Tailwind, shadcn-style parts). The old plain-JavaScript
screen was removed on 2026-11-17, so everything lives here (`ui/`).

Read this before you add a page, a tab, a switch or a chart. When in doubt, copy the nearest existing page:
the Portfolio page (`src/pages/portfolio/`) and the stock page (`src/pages/stock/`) follow every rule below.

## The idea behind the look
- Light, calm and clean, in the spirit of stockanalysis.com and fiscal.ai: white cards on a quiet background,
  one blue accent, green and red only for up and down.
- Pages are rethought for what you want to know, not copied from the old screen. Lead with the answer
  (a number, a chart), details below.
- Icons only where they help: in the sidebar, on icon-only buttons (always with a tooltip and `aria-label`),
  and in front of a button's text when it makes the action quicker to spot. Icons come from `lucide-react`.
- Plain, short English. Say what a number means in a tooltip (`tip` on `Stat` / `Kv`), not in a paragraph.
  No jargon without a tooltip, no exclamation marks.
- Light and dark must both look right. Never write a colour by hand: use the tokens below.

## Building blocks (use these, don't make new versions)
| Need | Use | File |
|---|---|---|
| A page with a title and buttons on the right | `Page` (title, actions) | `components/app/Page.tsx` |
| A panel | `Card` | `components/app/Page.tsx` |
| A card title with switches on the right | `H2` (side) | `components/app/bits.tsx` |
| Big number with label and a line below (totals) | `Stat` | `components/app/bits.tsx` |
| Label and value list (key facts) | `Kv` | `components/app/bits.tsx` |
| Horizontal bars (sectors, countries) | `Bars` | `components/app/bits.tsx` |
| Nothing to show yet, or loading | `Empty` | `components/app/bits.tsx` |
| A company's / ETF's logo next to its ticker (`keep` in lists so tickers line up; `fallback` = what shows without one) | `Logo` | `components/app/Logo.tsx` |
| Buttons | `Button` (variants: `outline` default, `default` = blue, `ghost`, `link`; sizes `sm`, `icon`) | `components/ui/button.tsx` |
| Choose one of a few (1Y / 5Y, Bars / Lines) | `Segmented` (always give `label`) | `components/ui/segmented.tsx` |
| Tabs of a page | `TabLinks`: each tab is its own address (`/portfolio/xray`) | `components/ui/tabs.tsx` |
| On / off | `Switch`, or `IconToggle` for an icon button | `components/ui/` |
| Tooltip | `Hint` | `components/ui/tooltip.tsx` |
| Green / red change badge | `ChangePill` | `components/ui/pill.tsx` |
| Pop-up window | `Dialog` | `components/ui/dialog.tsx` |
| Short confirmation ("Saved") | `useToast()` | `lib/toast.tsx` |
| Charts | `EChart` with `baseOption(useChartColors())`; bars use `BAR_FIT`; hover rules below | `components/chart/EChart.tsx` |
| Numbers and dates | `fmt`, `big`, `pctSigned`, `money`, `tone`, `day` | `lib/format.ts` |
| Talking to the server | `api.get` / `api.post` with `useQuery` from @tanstack/react-query | `lib/api.ts` |

Charts use echarts with only the parts we need registered in `EChart.tsx`. If you need a new chart type,
register it there.

Hovering a chart works the same everywhere (`EChart.tsx`):
- Several numbers in one chart (revenue, gross profit, segments): give each series `FOCUS`, the tooltip
  `axisTip(c, format)` and the chart `<EChart hoverTip>`. Hovering a number lifts it in every period, dims the others,
  and shows it bright in the tooltip.
- One number per period (dividend per year, yearly return): `FOCUS_SELF` and `<EChart focusPeriod>`. Hovering anywhere in
  a period lights up that period's bar and dims the others.
- Bar charts spread `BAR_TIP` into their tooltip: no grey pointer line. Line and price charts keep the line.

## Colours and type
- Tokens in `src/index.css` (light at the top, dark below): `background`, `card`, `panel`, `primary`, `secondary`,
  `muted-foreground`, `border`, `up` / `up-bg`, `down` / `down-bg`, `flag` (warning), `series-1` to `series-6`
  (chart lines, in that order). Use them as Tailwind classes (`text-up`, `bg-down-bg`, `text-muted-foreground`).
- Up and down: `tone(value)` gives `text-up` / `text-down`. Use it for every signed number.
- Numbers line up: add `num` (same digit width) to anything with numbers outside a table.
- Text sizes in use: page title 22px bold, card title 15px semibold, body 13px, small notes `text-xs`.

## Tables
- `<table className="tbl">` inside a `Card` with `overflow-x-auto`. Numbers are right-aligned, the first
  column left. Extras: `sticky-first` (first column stays in view when scrolling sideways), `tight`
  (narrow columns), `left-2-4` (columns 2 to 4 left-aligned).
- `.tbl` is not in a Tailwind layer, so a Tailwind class that changes it needs `!` (`text-left!`).
- Sortable headers: a button with an up/down arrow, the sort choice remembered (see below).
- Years run oldest on the left, newest on the right, the same as the charts.

## Remembering choices (required)
Every switch, tab, sort order and range must be remembered across restarts, per person:
- `const { pref, setPref } = useSettings()` from `lib/settings.tsx`. A view choice: `pref('ui2', 'my_key', fallback)`
  and `setPref('ui2', 'my_key', value)`. The fallback decides the type.
- A choice that existed in the old screen (Screener screens, Compare, Portfolio tabs) keeps its old group `state` and key,
  so what people saved before the redesign still shows.
- A real setting (in Settings): add it to `DEFAULTS` / `CHOICES` in `stockterminal/settings.py` and a row in
  `ROWS` in `src/pages/Settings.tsx`; read it with `settings.x`, change it with `update({ x })`.
  The Settings search and the Ctrl+K search find it by itself.
- Never use localStorage or sessionStorage: the app window forgets them.
- Limits of the settings file: a value up to 2000 characters, 200 keys per group.

## A new page
1. Make `src/pages/MyPage.tsx` that returns `<Page title="...">` with `Card`s inside.
2. Add the route in `src/App.tsx`. Use the address for things you can link to (`/stock/AAPL/financials`).
3. A main page also gets a line in `NAV` in `src/lib/nav.ts` (sidebar and Ctrl+K search), with a lucide icon.
4. Loading: `Empty` with "Loading…". Missing data: say what is missing and what to do, with a button if there is one.
5. Errors from the server: show them in the page in plain words, not a blank screen.

## Before you push
1. `cd ui`, `npm install` (first time), `npm run build`. Fix every TypeScript error.
2. Look at the page in light and in dark, and at a narrow window.
3. Commit the source and the built `stockterminal/app/static/new/` together. The self-test fails when the
   built files are older than the source.
