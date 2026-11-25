import { useQuery } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, ChartLine, ChevronRight, Hash } from 'lucide-react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import type { ReactNode } from 'react'
import { Empty, H2, Stat } from '@/components/app/bits'
import { RankTag, metricText, sectorPath } from '@/components/app/IndustryRank'
import type { Metric } from '@/components/app/IndustryRank'
import { Logo } from '@/components/app/Logo'
import { Card, Page } from '@/components/app/Page'
import { Button } from '@/components/ui/button'
import { Segmented } from '@/components/ui/segmented'
import { IconToggle } from '@/components/ui/toggle'
import { Hint } from '@/components/ui/tooltip'
import { api } from '@/lib/api'
import { big, fmt, niceName, tone } from '@/lib/format'
import { useSettings } from '@/lib/settings'
import { cn, rowKeys } from '@/lib/utils'

// Sector and industry pages (stockterminal/sectors.py): every sector and industry with its companies, the median
// margins, growth and valuation, and where each company ranks inside its group. Built from the Screener table,
// so it needs nothing new from the internet. Addresses: /sectors, /sectors/<sector>, /sectors/<sector>/<industry>.
// Remembered (prefs ui2): sectors_view (which numbers), sectors_sort, sectors_co_sort, sectors_ranks.

type Med = Record<string, number | null>
type Summary = { name: string; count: number; market_cap: number | null; med: Med; industries?: number; top?: string[] }
type Overview = { metrics: Metric[]; sectors: Summary[]; count: number; med: Med }
type Company = { ticker: string; name: string; industry?: string | null; market_cap: number | null; kind?: string; ranks: Record<string, [number, number]> } & Record<string, unknown>
type Group = Summary & { sector: string; industry: string | null; metrics: Metric[]; market: Med; sector_med?: Med; industries?: Summary[]; siblings?: string[]; companies: Company[] }

const VIEWS = ['Valuation', 'Margins', 'Growth', 'Returns'] as const
type View = (typeof VIEWS)[number]
const SIGNED = new Set(['revenue_yoy', 'eps_diluted_yoy', 'revenue_cagr_5y', 'r_1y'])
const TIPS: Record<View, string> = {
  Valuation: 'Medians of the price multiples. Negative P/E and EV/EBITDA (losses) are left out.',
  Margins: 'Median margins over the last 12 months.',
  Growth: 'Median growth: last 12 months vs the 12 before, and the yearly rate over 5 fiscal years.',
  Returns: 'Median returns on capital, debt and the price change over a year.',
}

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)

function useView() {
  const { pref, setPref } = useSettings()
  const v = pref<string>('ui2', 'sectors_view', 'Valuation') as View
  const view = VIEWS.includes(v) ? v : 'Valuation'
  return [view, (x: View) => setPref('ui2', 'sectors_view', x)] as const
}

function ViewSwitch({ view, onChange }: { view: View; onChange: (v: View) => void }) {
  return <Segmented label="Which numbers" value={view} onChange={onChange} options={VIEWS.map((v) => ({ value: v, label: v }))} />
}

/** A sortable table header; the sort is remembered under `prefKey` as "key:asc|desc". */
function useSort(prefKey: string, fallback: string) {
  const { pref, setPref } = useSettings()
  const [k, d] = (pref<string>('ui2', prefKey, '') || fallback).split(':')
  const by = (key: string) => setPref('ui2', prefKey, k !== key ? `${key}:${key === 'name' ? 'asc' : 'desc'}` : `${key}:${d === 'asc' ? 'desc' : 'asc'}`)
  const sort = <T,>(rows: T[], get: (r: T, key: string) => unknown) => {
    const sign = d === 'asc' ? 1 : -1
    return [...rows].sort((a, b) => {
      const x = get(a, k), y = get(b, k)
      if (typeof x === 'string' || typeof y === 'string') return sign * String(x ?? '').localeCompare(String(y ?? ''))
      const nx = num(x), ny = num(y)
      if (nx == null) return ny == null ? 0 : 1      // empty values always last
      if (ny == null) return -1
      return sign * (nx - ny)
    })
  }
  return { k, d, by, sort }
}

function Th({ id, label, tip, s, left }: { id: string; label: string; tip?: string; s: ReturnType<typeof useSort>; left?: boolean }) {
  return (
    <th aria-sort={s.k === id ? (s.d === 'asc' ? 'ascending' : 'descending') : undefined} className={left ? 'text-left!' : undefined}>
      <button type="button" title={tip} onClick={() => s.by(id)} className={cn('inline-flex items-center gap-1 hover:text-foreground', s.k === id && 'font-semibold text-foreground')}>
        {label}
        {s.k === id && (s.d === 'asc' ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />)}
      </button>
    </th>
  )
}

const groupValue = (r: Summary, key: string): unknown => (key === 'name' || key === 'count' || key === 'market_cap' || key === 'industries' ? r[key as keyof Summary] : r.med[key])

/** The table of sectors (on /sectors) or of the industries of one sector. */
function GroupTable({ rows, cols, all, allLabel, open, s, industries }: {
  rows: Summary[]; cols: Metric[]; all?: Med; allLabel?: string; open: (name: string) => void; s: ReturnType<typeof useSort>; industries?: boolean
}) {
  const sorted = s.sort(rows, groupValue)
  return (
    <div className="overflow-x-auto">
      <table className="tbl sticky-first">
        <thead>
          <tr>
            <Th id="name" label={industries ? 'Industry' : 'Sector'} s={s} left />
            <Th id="count" label="Companies" s={s} />
            {!industries && <Th id="industries" label="Industries" s={s} />}
            <Th id="market_cap" label="Market cap" tip="All its companies together" s={s} />
            {cols.map((c) => <Th key={c.key} id={c.key} label={c.label} tip={`Median ${c.label} (${c.better === 'high' ? 'higher' : 'lower'} is better)`} s={s} />)}
          </tr>
        </thead>
        <tbody onKeyDown={rowKeys}>
          {sorted.map((r) => (
            <tr key={r.name} tabIndex={0} className="cursor-pointer outline-none focus-visible:bg-panel" onClick={() => open(r.name)}>
              <td>
                <span className="inline-flex items-center gap-1.5 font-semibold">
                  {r.name}
                  <ChevronRight className="size-3.5 text-muted-foreground" />
                </span>
              </td>
              <td>{r.count}</td>
              {!industries && <td>{r.industries ?? '–'}</td>}
              <td>{big(r.market_cap)}</td>
              {cols.map((c) => <td key={c.key} className={SIGNED.has(c.key) ? tone(r.med[c.key]) : undefined}>{metricText(c, r.med[c.key])}</td>)}
            </tr>
          ))}
          {all && (
            <tr className="text-muted-foreground [&>td]:border-t-2">
              <td className="font-medium">{allLabel}</td>
              <td />
              {!industries && <td />}
              <td />
              {cols.map((c) => <td key={c.key}>{metricText(c, all[c.key])}</td>)}
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

function Loading({ q, what }: { q: { isLoading: boolean; error: unknown }; what: string }) {
  if (q.isLoading) return <Card><Empty>Loading… The first time after a start this builds the table of every company, which takes a few seconds.</Empty></Card>
  return (
    <Card>
      <Empty>
        {q.error ? `${what} could not be loaded: ${String((q.error as Error).message || q.error)}` : `No companies loaded yet.`}
        <Button asChild size="sm"><Link to="/sectors">All sectors</Link></Button>
      </Empty>
    </Card>
  )
}

/* ------------------------------------------------------------------ /sectors */
function SectorsOverview() {
  const nav = useNavigate()
  const [view, setView] = useView()
  const s = useSort('sectors_sort', 'market_cap:desc')
  const q = useQuery({ queryKey: ['sectors'], queryFn: () => api.get<Overview>('/api/sectors') })
  const d = q.data
  if (!d?.sectors.length) return <Page title="Sectors"><Loading q={q} what="The sectors" /></Page>
  const cols = d.metrics.filter((m) => m.group === view)
  const total = d.sectors.reduce((a, x) => a + (x.market_cap || 0), 0) || 1
  return (
    <Page title="Sectors" actions={<ViewSwitch view={view} onChange={setView} />}>
      <Card className="p-4">
        <H2 side={<span className="text-xs text-muted-foreground">Share of the market cap of all {d.count} companies</span>}>How big each sector is</H2>
        <div className="flex h-7 w-full overflow-hidden rounded-md">
          {d.sectors.filter((x) => x.market_cap).map((x, i) => (
            <Hint key={x.name} label={`${x.name}: ${fmt((x.market_cap || 0) / total, 'pct')} (${big(x.market_cap)})`}>
              <button
                type="button"
                aria-label={x.name}
                onClick={() => nav(sectorPath(x.name))}
                className="h-full border-r border-card last:border-0 hover:opacity-80"
                style={{ width: `${((x.market_cap || 0) / total) * 100}%`, background: `var(--series-${(i % 6) + 1})`, opacity: 1 - Math.floor(i / 6) * 0.35 }}
              />
            </Hint>
          ))}
        </div>
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
          {d.sectors.filter((x) => x.market_cap).map((x, i) => (
            <Link key={x.name} to={sectorPath(x.name)} className="inline-flex items-center gap-1.5 hover:text-foreground">
              <span className="size-2.5 rounded-sm" style={{ background: `var(--series-${(i % 6) + 1})`, opacity: 1 - Math.floor(i / 6) * 0.35 }} />
              {x.name} <span className="num">{fmt((x.market_cap || 0) / total, 'pct')}</span>
            </Link>
          ))}
        </div>
      </Card>
      <Card className="min-w-0">
        <div className="border-b px-4 py-3"><H2 className="mb-0" side={<span className="text-xs text-muted-foreground">{TIPS[view]}</span>}>{view} by sector</H2></div>
        <GroupTable rows={d.sectors} cols={cols} all={d.med} allLabel={`All ${d.count} companies`} open={(n) => nav(sectorPath(n))} s={s} />
      </Card>
      <p className="text-xs text-muted-foreground">
        Medians: half of the companies are above, half below, so one odd company cannot pull the number. Built from the Screener (the database), all in US dollars; prices and multiples are from the last daily update. Sector and industry come from Yahoo.
      </p>
    </Page>
  )
}

/* ------------------------------------------------------------------ /sectors/<sector>[/<industry>] */
function CompaniesTable({ g, cols, ranks }: { g: Group; cols: Metric[]; ranks: boolean }) {
  const nav = useNavigate()
  const s = useSort('sectors_co_sort', 'market_cap:desc')
  const sorted = s.sort(g.companies, (r, k) => (k === 'name' ? r.ticker : r[k]))
  const med = g.med
  return (
    <div className="overflow-x-auto">
      <table className="tbl tight sticky-first">
        <thead>
          <tr>
            <Th id="name" label="Company" s={s} left />
            {!g.industry && <Th id="industry" label="Industry" s={s} left />}
            <Th id="market_cap" label="Market cap" s={s} />
            {cols.map((c) => <Th key={c.key} id={c.key} label={c.label} tip={`${c.better === 'high' ? 'Higher' : 'Lower'} is better. Rank = place in this group (1 = best).`} s={s} />)}
          </tr>
        </thead>
        <tbody onKeyDown={rowKeys}>
          {sorted.map((r, i) => (
            <tr key={r.ticker} tabIndex={0} className="cursor-pointer outline-none focus-visible:bg-panel" onClick={() => nav(`/stock/${r.ticker}`)}>
              <td>
                <div className="flex items-center gap-2">
                  <span className="num w-6 shrink-0 text-right text-xs text-muted-foreground">{i + 1}</span>
                  <Logo t={r.ticker} size={18} keep />
                  <span className="w-14 shrink-0 font-semibold">{r.ticker}</span>
                  <span className="max-w-44 truncate text-muted-foreground" title={niceName(r.name)}>{niceName(r.name)}</span>
                </div>
              </td>
              {!g.industry && (
                <td className="max-w-48 truncate text-left! text-muted-foreground" title={r.industry || ''}>
                  <Link to={sectorPath(g.sector, r.industry || 'Other')} onClick={(e) => e.stopPropagation()} className="hover:text-foreground hover:underline">{r.industry || 'Other'}</Link>
                </td>
              )}
              <td>{big(r.market_cap)}</td>
              {cols.map((c) => {
                const v = num(r[c.key])
                return (
                  <td key={c.key} className={SIGNED.has(c.key) ? tone(v) : undefined}>
                    {metricText(c, v)}
                    {ranks && <span className="ml-1.5 inline-block w-10 text-left"><RankTag r={r.ranks[c.key]} /></span>}
                  </td>
                )
              })}
            </tr>
          ))}
          <tr className="text-muted-foreground [&>td]:border-t-2">
            <td className="font-medium">Median of the {g.count}</td>
            {!g.industry && <td />}
            <td />
            {cols.map((c) => <td key={c.key}>{metricText(c, med[c.key])}{ranks && <span className="ml-1.5 inline-block w-10" />}</td>)}
          </tr>
        </tbody>
      </table>
    </div>
  )
}

function GroupPage({ sector, industry }: { sector: string; industry?: string }) {
  const nav = useNavigate()
  const { pref, setPref } = useSettings()
  const [view, setView] = useView()
  const ranks = pref('ui2', 'sectors_ranks', true)
  const s = useSort('sectors_sort', 'market_cap:desc')
  const params = new URLSearchParams({ sector, ...(industry ? { industry } : {}) })
  const q = useQuery({ queryKey: ['sectors', sector, industry ?? ''], queryFn: () => api.get<Group>(`/api/sectors/group?${params}`), retry: false })
  const g = q.data
  const crumbs: ReactNode = (
    <nav aria-label="Where you are" className="-mb-2 flex flex-wrap items-center gap-1 text-[13px] text-muted-foreground">
      <Link to="/sectors" className="hover:text-foreground hover:underline">Sectors</Link>
      <ChevronRight className="size-3.5" />
      {industry ? <Link to={sectorPath(sector)} className="hover:text-foreground hover:underline">{sector}</Link> : <span>{sector}</span>}
      {industry && <><ChevronRight className="size-3.5" /><span>{industry}</span></>}
    </nav>
  )
  if (!g) return <Page title={industry || sector}>{crumbs}<Loading q={q} what="This group" /></Page>
  const cols = g.metrics.filter((m) => m.group === view)
  // the line under each number: how it compares with the wider group
  const ref = industry ? g.sector_med : g.market
  const refName = industry ? 'sector' : 'all'
  const refTip = industry ? `the whole ${g.sector} sector` : 'all loaded companies'
  const lower = (l: string) => (/^[A-Z][a-z]/.test(l) ? l[0].toLowerCase() + l.slice(1) : l)
  const tiles = (['pe', 'ev_ebitda', 'operating_margin', 'net_margin', 'revenue_yoy', 'roe'] as const).map((k) => g.metrics.find((m) => m.key === k)!).filter(Boolean)
  const actions = (
    <div className="flex flex-wrap items-center gap-2">
      <ViewSwitch view={view} onChange={setView} />
      <IconToggle on={ranks} onChange={(on) => setPref('ui2', 'sectors_ranks', on)} label="Show each company's rank in this group" text="Ranks"><Hash /></IconToggle>
      <Button asChild><Link to={`/compare?t=${g.companies.slice(0, 6).map((c) => c.ticker).join(',')}`}><ChartLine /> Compare the biggest</Link></Button>
    </div>
  )
  return (
    <Page title={g.name} actions={actions}>
      {crumbs}
      <Card className="p-4">
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 lg:grid-cols-8">
          <Stat label="Companies" value={g.count} sub={industry ? `of ${g.sector}` : `${g.industries?.length ?? 0} industries`} />
          <Stat label="Market cap" value={big(g.market_cap)} sub="all together" />
          {tiles.map((m) => {
            const v = g.med[m.key], r = ref?.[m.key]
            return (
              <Stat
                key={m.key}
                label={`Median ${lower(m.label)}`}
                tip={`Half of the companies in this group are above, half below. Below: the median of ${refTip}.`}
                value={<span className={SIGNED.has(m.key) ? tone(v) : undefined}>{metricText(m, v)}</span>}
                sub={`${refName}: ${metricText(m, r)}`}
              />
            )
          })}
        </div>
      </Card>
      {g.industries && g.industries.length > 1 && (
        <Card className="min-w-0">
          <div className="border-b px-4 py-3"><H2 className="mb-0" side={<span className="text-xs text-muted-foreground">{TIPS[view]}</span>}>Industries in {g.name}</H2></div>
          <GroupTable rows={g.industries} cols={cols} all={g.med} allLabel={`Whole sector (${g.count})`} open={(n) => nav(sectorPath(sector, n))} s={s} industries />
        </Card>
      )}
      {g.siblings && g.siblings.length > 1 && (
        <div className="flex flex-wrap items-center gap-1.5 text-[13px]">
          <span className="mr-1 text-muted-foreground">Other industries in {g.sector}:</span>
          {g.siblings.filter((x) => x !== industry).map((x) => (
            <Link key={x} to={sectorPath(g.sector, x)} className="rounded-md bg-secondary px-2 py-0.5 text-muted-foreground hover:text-foreground">{x}</Link>
          ))}
        </div>
      )}
      <Card className="min-w-0">
        <div className="border-b px-4 py-3">
          <H2 className="mb-0" side={<span className="text-xs text-muted-foreground">{ranks ? 'Rank: 1 = best in this group; green = top quarter, red = bottom quarter' : TIPS[view]}</span>}>Companies</H2>
        </div>
        <CompaniesTable g={g} cols={cols} ranks={ranks} />
      </Card>
      <p className="text-xs text-muted-foreground">
        A negative P/E or EV/EBITDA (a loss) has no rank and is left out of the median. Built from the Screener (the database), all in US dollars; multiples are from the last daily update.
      </p>
    </Page>
  )
}

export function SectorsPage() {
  const { sector, industry } = useParams()
  if (!sector) return <SectorsOverview />
  return <GroupPage key={`${sector}/${industry ?? ''}`} sector={sector} industry={industry} />
}
