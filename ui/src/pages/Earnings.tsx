import { useQuery } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, CalendarDays, ExternalLink, List, Loader2, Moon, Rows3, Sun, SunMedium } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Card, Page } from '@/components/app/Page'
import { Empty, H2, Stat } from '@/components/app/bits'
import { Logo } from '@/components/app/Logo'
import { Button } from '@/components/ui/button'
import { Segmented } from '@/components/ui/segmented'
import { Hint } from '@/components/ui/tooltip'
import { api } from '@/lib/api'
import { big, day, niceName, pctSigned, tone } from '@/lib/format'
import { useSettings } from '@/lib/settings'
import { cn } from '@/lib/utils'

/* The Earnings page (stockterminal/earnings_cal.py, /api/earnings-calendar): when the companies on your watchlists
   and in your portfolio report next, and how their last results compared with the estimate. */

type N = number | null | undefined
type Rating = { step: number; reasons: string[] }
type Next = {
  date: string; date_to: string | null; confirmed: boolean; expected: boolean; time: 'before' | 'after' | 'during' | null
  eps_avg: N; eps_low: N; eps_high: N; revenue_avg: N; analysts: N; growth: N; year_ago_eps: N; rating: Rating | null
}
type Result = {
  date: string; eps_est: N; eps_act: N; surprise: N; reaction?: N; quarter_end: boolean; preliminary?: boolean
  revenue?: N; revenue_est?: N; revenue_pct?: N; press_url?: string | null
}
type Co = { ticker: string; name: string; held: boolean; lists: string[]; next: Next | null; results: Result[]; beats: number; counted: number; avg_surprise: N }
type Cal = { today: string; scope: string; companies: Co[]; missing: string[]; etfs: string[]; held_count: number; lists: { id: number; name: string; count: number }[] }

const has = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const eps = (v: N) => (has(v) ? v.toFixed(2) : '–')
const href = (t: string) => `/stock/${encodeURIComponent(t)}/estimates`
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const date = (s: string) => new Date(s + 'T12:00:00')
const addDays = (s: string, n: number) => { const d = date(s); d.setDate(d.getDate() + n); return iso(d) }
const monday = (s: string) => { const d = date(s); return addDays(s, -((d.getDay() + 6) % 7)) }
/** The day a report goes on in the week view: a Sunday date with the Monday after it (Saturday stays with Friday). */
const slot = (s: string) => (date(s).getDay() === 0 ? addDays(s, 1) : s)
const short = (s: string) => date(s).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
const wday = (s: string) => date(s).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })

const TIME: Record<string, [ReactNode, string]> = {
  before: [<Sun key="b" className="size-3.5 text-flag" />, 'Before the market opens'],
  during: [<SunMedium key="d" className="size-3.5 text-flag" />, 'During market hours'],
  after: [<Moon key="a" className="size-3.5 text-primary" />, 'After the market closes'],
}
const RATE: Record<string, [string, string, string]> = {
  '2': ['▲▲', 'Very positive', 'text-up'], '1': ['▲', 'Positive', 'text-up'], '0': ['●', 'Neutral', 'text-muted-foreground'],
  '-1': ['▼', 'Negative', 'text-down'], '-2': ['▼▼', 'Very negative', 'text-down'],
}

/** The stored answer first (instant), then a fresh one from Yahoo for what is older than 20 hours. */
function useCalendar(scope: string) {
  const url = `/api/earnings-calendar?scope=${encodeURIComponent(scope)}`
  const stored = useQuery({ queryKey: ['earnings-cal', scope, 'stored'], queryFn: () => api.get<Cal>(url) })
  const fresh = useQuery({ queryKey: ['earnings-cal', scope, 'fresh'], queryFn: () => api.get<Cal>(url + '&refresh=1'), retry: false, staleTime: 10 * 60_000 })
  return { data: fresh.data ?? stored.data, loading: stored.isLoading, error: stored.error, updating: fresh.isFetching }
}

export function EarningsPage() {
  const { scope: fromUrl } = useParams()
  const { pref, setPref } = useSettings()
  const nav = useNavigate()
  const scope = fromUrl || pref<string>('ui2', 'earn_scope', 'all')
  const { data: d, loading, error, updating } = useCalendar(scope)
  const choose = (s: string) => { setPref('ui2', 'earn_scope', s); nav(`/earnings/${s}`) }

  const tabs: [string, string, number | null][] = d
    ? [['all', 'Everything', null], ...(d.held_count ? [['held', 'Portfolio', d.held_count] as [string, string, number]] : []), ...d.lists.map((l) => [String(l.id), l.name, l.count] as [string, string, number])]
    : []
  const actions = updating ? <span className="flex items-center gap-1.5 text-xs text-muted-foreground"><Loader2 className="size-3.5 animate-spin" /> Checking Yahoo for new dates…</span> : null

  if (loading) return <Page title="Earnings"><Card><Empty>Loading…</Empty></Card></Page>
  if (error || !d) {
    const gone = String((error as Error | null)?.message || '').includes('no longer exists')
    return (
      <Page title="Earnings">
        <Card><Empty>{gone ? 'That watchlist no longer exists.' : 'The earnings calendar could not be loaded.'}<Button onClick={() => choose('all')}>Show everything</Button></Empty></Card>
      </Page>
    )
  }
  const nothing = !d.lists.some((l) => l.count) && !d.held_count
  return (
    <Page title="Earnings" actions={actions}>
      {tabs.length > 1 && (
        <div className="-mt-1 flex flex-wrap items-center gap-1 border-b">
          {tabs.map(([id, label, count]) => (
            <button
              key={id} onClick={() => choose(id)}
              className={cn('-mb-px flex items-center gap-1.5 border-b-2 border-transparent px-3 py-2 text-[13px] font-medium text-muted-foreground hover:text-foreground', id === d.scope && 'border-primary text-foreground')}
            >
              {label}
              {count !== null && <span className="num rounded bg-secondary px-1.5 text-[11px] text-muted-foreground">{count}</span>}
            </button>
          ))}
        </div>
      )}
      {nothing ? (
        <Card>
          <Empty>
            <CalendarDays className="size-5" />
            Add companies to a watchlist (the star on a stock page), or connect your portfolio, and their results dates show up here.
            <Button asChild><Link to="/watchlist">Open the watchlist</Link></Button>
          </Empty>
        </Card>
      ) : !d.companies.length ? (
        <Card><Empty><CalendarDays className="size-5" />{d.etfs.length && !d.missing.length ? 'Only ETFs here: funds report no results.' : 'No results dates found yet. They are looked up at Yahoo when you are online.'}</Empty></Card>
      ) : (
        <>
          <Summary d={d} />
          <ComingUp d={d} />
          <Past d={d} />
        </>
      )}
      <p className="text-xs leading-relaxed text-muted-foreground">
        Dates, estimates and reported EPS from Yahoo, looked up again after 20 hours. A date with ≈ or a dashed outline is not confirmed:
        a range Yahoo gives, or (≈) a date expected from the company's own rhythm (about 52 weeks after the same quarter last year).
        Surprise = reported EPS against the average analyst estimate (adjusted EPS, as analysts estimate it). Revenue against the estimate
        comes from the earnings watch, for results it saw coming in. Price reaction = the last close before the results day to the first close after it.
        {!!d.etfs.length && <span className="block">ETFs are left out (funds report no results): {d.etfs.join(', ')}.</span>}
        {!!d.missing.length && <span className="block">No calendar from Yahoo yet for: {d.missing.join(', ')}.</span>}
      </p>
    </Page>
  )
}

/* ------------------------------------------------------------------ the numbers at the top */
function Summary({ d }: { d: Cal }) {
  const end = addDays(monday(d.today), 6)
  const week = d.companies.filter((c) => c.next && c.next.date <= end)
  const month = d.companies.filter((c) => c.next && c.next.date <= addDays(d.today, 30))
  const nxt = [...d.companies].filter((c) => c.next).sort((a, b) => a.next!.date.localeCompare(b.next!.date))[0]
  const beats = d.companies.reduce((s, c) => s + c.beats, 0)
  const counted = d.companies.reduce((s, c) => s + c.counted, 0)
  const surprises = d.companies.flatMap((c) => c.results.slice(0, 4).map((r) => r.surprise).filter(has))
  const avg = surprises.length ? surprises.reduce((a, b) => a + b, 0) / surprises.length : null
  return (
    <Card className="grid grid-cols-2 gap-4 p-4 md:grid-cols-4">
      <Stat label="Reporting this week" value={week.length} sub={week.length ? week.map((c) => c.ticker).slice(0, 6).join(', ') + (week.length > 6 ? '…' : '') : 'nothing this week'} />
      <Stat label="Next 30 days" value={month.length} sub={`of ${d.companies.length} companies`} />
      <Stat label="Next to report" value={nxt ? nxt.ticker : '–'} sub={nxt?.next ? `${nxt.next.expected ? '≈ ' : ''}${wday(nxt.next.date)}` : undefined} />
      <Stat
        label="Beat the estimate" tip="Of the last 4 results of every company here: how often reported EPS was above the average analyst estimate, and the average surprise."
        value={counted ? `${Math.round((beats / counted) * 100)}%` : '–'}
        sub={counted ? `${beats} of ${counted} results · average ${pctSigned(avg, 1)}` : undefined}
      />
    </Card>
  )
}

/* ------------------------------------------------------------------ coming up */
function NextTip({ c }: { c: Co }) {
  const n = c.next!
  return (
    <span className="flex max-w-72 flex-col gap-0.5">
      <span className="font-semibold">{niceName(c.name)}</span>
      <span>{n.expected ? 'Expected around ' : n.confirmed ? '' : 'Not confirmed: '}{wday(n.date)}{n.date_to ? ` – ${wday(n.date_to)}` : ''}{n.time ? ` · ${TIME[n.time][1].toLowerCase()}` : ''}</span>
      {has(n.eps_avg) && <span>EPS estimate {eps(n.eps_avg)}{has(n.eps_low) && has(n.eps_high) ? ` (${eps(n.eps_low)} to ${eps(n.eps_high)})` : ''}</span>}
      {has(n.revenue_avg) && <span>Revenue estimate {big(n.revenue_avg)}</span>}
      {c.counted > 0 && <span>Beat the estimate {c.beats} of the last {c.counted} times</span>}
    </span>
  )
}

function Chip({ c }: { c: Co }) {
  const n = c.next!
  const unsure = n.expected || !n.confirmed
  return (
    <Hint label={<NextTip c={c} />}>
      <Link to={href(c.ticker)} className={cn('flex min-w-0 items-center gap-1.5 rounded-md border bg-card px-1.5 py-1 text-[12.5px] font-medium hover:bg-panel', unsure && 'border-dashed', c.held && 'border-primary/50')}>
        <Logo t={c.ticker} size={16} />
        <span className="truncate">{n.expected && '≈ '}{c.ticker}</span>
        <span className="grow" />
        {n.time && TIME[n.time][0]}
      </Link>
    </Hint>
  )
}

function ComingUp({ d }: { d: Cal }) {
  const { pref, setPref } = useSettings()
  const view = pref<string>('ui2', 'earn_view', 'weeks') === 'list' ? 'list' : 'weeks'
  const ahead = d.companies.filter((c) => c.next).sort((a, b) => a.next!.date.localeCompare(b.next!.date) || a.ticker.localeCompare(b.ticker))
  return (
    <Card className="p-4">
      <H2 side={<Segmented label="How to show what is coming up" value={view} onChange={(v) => setPref('ui2', 'earn_view', v)} options={[{ value: 'weeks', icon: <Rows3 />, hint: 'Per week' }, { value: 'list', icon: <List />, hint: 'List with estimates' }]} />}>
        Coming up
      </H2>
      {!ahead.length ? <Empty>No results dates ahead yet.</Empty> : view === 'weeks' ? <Weeks d={d} ahead={ahead} /> : <AheadTable ahead={ahead} today={d.today} />}
      <div className="mt-3 flex flex-wrap gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1">{TIME.before[0]} before the open</span>
        <span className="flex items-center gap-1">{TIME.after[0]} after the close</span>
        <span className="flex items-center gap-1"><span className="inline-block h-3 w-5 rounded border border-dashed" /> not confirmed</span>
        {d.held_count > 0 && <span className="flex items-center gap-1"><span className="inline-block h-3 w-5 rounded border border-primary/50" /> in your portfolio</span>}
      </div>
    </Card>
  )
}

function Weeks({ d, ahead }: { d: Cal; ahead: Co[] }) {
  const first = monday(d.today)
  const byWeek = new Map<string, Co[]>()
  for (const c of ahead) {
    const w = monday(slot(c.next!.date))
    byWeek.set(w, [...(byWeek.get(w) ?? []), c])
  }
  const weeks = [...byWeek.keys()].sort()
  return (
    <div className="flex flex-col gap-4 overflow-x-auto">
      {weeks.map((w) => {
        const list = byWeek.get(w)!
        const n = Math.round((date(w).getTime() - date(first).getTime()) / (7 * 864e5))
        const title = n === 0 ? 'This week' : n === 1 ? 'Next week' : `In ${n} weeks`
        return (
          <div key={w} className="min-w-[640px]">
            <div className="mb-1.5 flex items-baseline gap-2 text-[13px]">
              <span className="font-semibold">{title}</span>
              <span className="text-muted-foreground">{short(w)} – {short(addDays(w, 4))}</span>
            </div>
            <div className="grid grid-cols-5 gap-2">
              {[0, 1, 2, 3, 4].map((i) => {
                const dd = addDays(w, i)
                const here = list.filter((c) => Math.min((date(slot(c.next!.date)).getDay() + 6) % 7, 4) === i)
                const past = dd < d.today
                return (
                  <div key={i} className={cn('flex min-h-16 flex-col gap-1 rounded-lg bg-panel p-1.5', dd === d.today && 'ring-1 ring-primary', past && 'opacity-50')}>
                    <div className={cn('px-0.5 text-[11px] font-medium text-muted-foreground', dd === d.today && 'text-primary')}>{dd === d.today ? 'Today' : wday(dd)}</div>
                    {here.map((c) => <Chip key={c.ticker} c={c} />)}
                  </div>
                )
              })}
            </div>
          </div>
        )
      })}
    </div>
  )
}

function Beats({ results, n = 4 }: { results: Result[]; n?: number }) {
  const done = results.filter((r) => has(r.eps_act) && has(r.eps_est)).slice(0, n).reverse()
  if (!done.length) return <span className="text-muted-foreground">–</span>
  return (
    <span className="inline-flex items-center gap-1">
      {done.map((r) => (
        <Hint key={r.date} label={`${day(r.date)}: EPS ${eps(r.eps_act)} vs ${eps(r.eps_est)} expected (${pctSigned(r.surprise, 1)})`}>
          <span className={cn('size-2.5 cursor-help rounded-full', r.eps_act! > r.eps_est! ? 'bg-up' : r.eps_act! < r.eps_est! ? 'bg-down' : 'bg-muted-foreground/50')} />
        </Hint>
      ))}
    </span>
  )
}

function RateBadge({ r }: { r: Rating | null }) {
  if (!r) return <span className="text-muted-foreground">–</span>
  const [sym, label, cls] = RATE[String(r.step)] ?? RATE['0']
  return (
    <Hint label={<span className="flex flex-col">{r.reasons.map((x) => <span key={x}>{x}</span>)}</span>}>
      <span className={cn('cursor-help text-xs font-semibold whitespace-nowrap', cls)}>{sym} {label}</span>
    </Hint>
  )
}

function AheadTable({ ahead, today }: { ahead: Co[]; today: string }) {
  const nav = useNavigate()
  return (
    <div className="-mx-4 overflow-x-auto">
      <table className="tbl sticky-first">
        <thead>
          <tr>
            <th>Company</th><th className="text-left!">Date</th><th className="text-left!">When</th>
            <th><Hint label="Average analyst estimate of the adjusted EPS, with the lowest and highest estimate. Hover a number for the number of analysts and the EPS a year ago."><span className="cursor-help">EPS estimate</span></Hint></th>
            <th><Hint label="Expected EPS against the same quarter a year ago"><span className="cursor-help">Growth</span></Hint></th><th>Revenue est.</th>
            <th className="text-left!"><Hint label="Beat (green) or missed (red) the EPS estimate, last 4 results, oldest left"><span className="cursor-help">Last 4</span></Hint></th>
            <th className="text-left!"><Hint label="Expected EPS growth, analysts raising or lowering their estimate, and how often the company beat lately. Hover a rating for the reasons."><span className="cursor-help">Expectation</span></Hint></th>
          </tr>
        </thead>
        <tbody>
          {ahead.map((c) => {
            const n = c.next!
            const days = Math.round((date(n.date).getTime() - date(today).getTime()) / 864e5)
            return (
              <tr key={c.ticker} className="cursor-pointer" onClick={() => nav(href(c.ticker))}>
                <td>
                  <span className="flex items-center gap-2">
                    <Logo t={c.ticker} size={18} keep />
                    <span className="font-semibold">{c.ticker}</span>
                    <span className="max-w-48 truncate text-muted-foreground">{niceName(c.name)}</span>
                  </span>
                </td>
                <td className="text-left! whitespace-nowrap">
                  <span className={cn(!n.confirmed && 'text-muted-foreground')}>{n.expected && '≈ '}{wday(n.date)}{n.date_to ? ` – ${short(n.date_to)}` : ''}</span>
                  <span className="ml-1.5 text-xs text-muted-foreground">{days === 0 ? 'today' : days === 1 ? 'tomorrow' : `in ${days} days`}</span>
                </td>
                <td className="text-left!">{n.time ? <span className="flex items-center gap-1 text-xs whitespace-nowrap">{TIME[n.time][0]}{n.time === 'before' ? 'Before open' : n.time === 'after' ? 'After close' : 'In the day'}</span> : <span className="text-muted-foreground">–</span>}</td>
                <td className="whitespace-nowrap">
                  {has(n.eps_avg) ? (
                    <Hint label={`${has(n.analysts) ? `${n.analysts} analysts` : 'Analysts'}${has(n.eps_low) && has(n.eps_high) ? `, ${eps(n.eps_low)} to ${eps(n.eps_high)}` : ''}${has(n.year_ago_eps) ? ` · a year ago ${eps(n.year_ago_eps)}` : ''}`}>
                      <span className="cursor-help">{eps(n.eps_avg)}{has(n.eps_low) && has(n.eps_high) && <span className="ml-1 text-xs text-muted-foreground">{eps(n.eps_low)}–{eps(n.eps_high)}</span>}</span>
                    </Hint>
                  ) : '–'}
                </td>
                <td className={tone(n.growth)}>{pctSigned(n.growth, 1)}</td>
                <td>{has(n.revenue_avg) ? big(n.revenue_avg) : '–'}</td>
                <td className="text-left!"><Beats results={c.results} /></td>
                <td className="text-left!"><RateBadge r={n.rating} /></td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/* ------------------------------------------------------------------ past surprises */
type SortKey = 'ticker' | 'beat' | 'avg' | 'last' | 'reaction' | 'date'
const SORTS: Record<SortKey, (c: Co) => number | string | null> = {
  ticker: (c) => c.ticker,
  beat: (c) => (c.counted ? c.beats / c.counted : null),
  avg: (c) => c.avg_surprise ?? null,
  last: (c) => c.results[0]?.surprise ?? null,
  reaction: (c) => c.results.find((r) => has(r.reaction))?.reaction ?? null,
  date: (c) => c.results[0]?.date ?? null,
}

function Past({ d }: { d: Cal }) {
  const { pref, setPref } = useSettings()
  const view = pref<string>('ui2', 'earn_past', 'latest') === 'company' ? 'company' : 'latest'
  return (
    <Card className="p-4">
      <H2 side={<Segmented label="How to show past results" value={view} onChange={(v) => setPref('ui2', 'earn_past', v)} options={[{ value: 'latest', label: 'Latest results' }, { value: 'company', label: 'Per company' }]} />}>
        Past surprises
      </H2>
      {view === 'latest' ? <Latest d={d} /> : <PerCompany d={d} />}
    </Card>
  )
}

function Latest({ d }: { d: Cal }) {
  const nav = useNavigate()
  const since = addDays(d.today, -183)
  const rows = d.companies
    .flatMap((c) => c.results.filter((r) => r.date >= since && has(r.eps_act)).map((r) => ({ c, r })))
    .sort((a, b) => b.r.date.localeCompare(a.r.date) || a.c.ticker.localeCompare(b.c.ticker))
  if (!rows.length) return <Empty>No results reported in the last 6 months.</Empty>
  return (
    <div className="-mx-4 overflow-x-auto">
      <table className="tbl sticky-first">
        <thead>
          <tr>
            <th>Company</th><th className="text-left!">Reported</th><th>EPS estimate</th><th>EPS reported</th>
            <th><Hint label="Reported EPS against the average analyst estimate"><span className="cursor-help">EPS surprise</span></Hint></th>
            <th><Hint label="Revenue against the estimate, from the earnings watch (only for results it saw coming in)"><span className="cursor-help">Revenue surprise</span></Hint></th>
            <th><Hint label="Share price from the last close before the results day to the first close after it"><span className="cursor-help">Price reaction</span></Hint></th>
            <th className="text-left!" />
          </tr>
        </thead>
        <tbody>
          {rows.map(({ c, r }) => (
            <tr key={c.ticker + r.date} className="cursor-pointer" onClick={() => nav(href(c.ticker))}>
              <td>
                <span className="flex items-center gap-2">
                  <Logo t={c.ticker} size={18} keep />
                  <span className="font-semibold">{c.ticker}</span>
                  <span className="max-w-48 truncate text-muted-foreground">{niceName(c.name)}</span>
                </span>
              </td>
              <td className="text-left! whitespace-nowrap">{r.quarter_end ? <Hint label="Yahoo gives the quarter end here, not the day it was reported"><span className="cursor-help">quarter to {day(r.date)}</span></Hint> : day(r.date)}{r.preliminary && <span className="ml-1.5 text-xs text-flag">preliminary</span>}</td>
              <td>{eps(r.eps_est)}</td>
              <td className="font-medium">{eps(r.eps_act)}</td>
              <td><SurprisePill v={r.surprise} /></td>
              <td>{has(r.revenue_pct) ? <Hint label={`Revenue ${big(r.revenue)} vs ${big(r.revenue_est)} expected`}><span className={cn('cursor-help', tone(r.revenue_pct))}>{pctSigned(r.revenue_pct, 1)}</span></Hint> : <span className="text-muted-foreground">–</span>}</td>
              <td className={tone(r.reaction)}>{pctSigned(r.reaction, 1)}</td>
              <td className="text-left!">
                {r.press_url && (
                  <Hint label="The press release">
                    <a href={r.press_url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()} aria-label="The press release" className="text-muted-foreground hover:text-foreground"><ExternalLink className="size-3.5" /></a>
                  </Hint>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function SurprisePill({ v }: { v: N }) {
  if (!has(v)) return <span className="text-muted-foreground">–</span>
  const word = v > 0.005 ? 'beat' : v < -0.005 ? 'miss' : 'in line'
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={cn('rounded px-1.5 py-px text-[11px] font-medium', v > 0.005 ? 'bg-up-bg text-up' : v < -0.005 ? 'bg-down-bg text-down' : 'bg-secondary text-muted-foreground')}>{word}</span>
      <span className={tone(v)}>{pctSigned(v, 1)}</span>
    </span>
  )
}

function PerCompany({ d }: { d: Cal }) {
  const nav = useNavigate()
  const { pref, setPref } = useSettings()
  const raw = pref<string>('ui2', 'earn_sort', 'date:desc')
  const [k0, dir0] = raw.split(':')
  const sk: SortKey = (k0 in SORTS ? k0 : 'date') as SortKey
  const sd = dir0 === 'asc' ? 'asc' : 'desc'
  const rows = d.companies.filter((c) => c.results.length)
  rows.sort((a, b) => {
    const x = SORTS[sk](a), y = SORTS[sk](b)
    if (x === null || y === null) return x === y ? 0 : x === null ? 1 : -1      // empty at the bottom either way
    const o = typeof x === 'string' ? x.localeCompare(y as string) : x - (y as number)
    return sd === 'asc' ? o : -o
  })
  if (!rows.length) return <Empty>No reported results from Yahoo yet.</Empty>
  const head = (k: SortKey, label: string, tip?: string, left?: boolean) => (
    <th className={cn(left && 'text-left!')}>
      <button
        className="inline-flex items-center gap-1"
        onClick={() => setPref('ui2', 'earn_sort', `${k}:${sk === k && sd === 'desc' ? 'asc' : k === 'ticker' && sk !== k ? 'asc' : 'desc'}`)}
      >
        {tip ? <Hint label={tip}><span className="cursor-help">{label}</span></Hint> : label}
        {k === sk && (sd === 'asc' ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />)}
      </button>
    </th>
  )
  return (
    <div className="-mx-4 overflow-x-auto">
      <table className="tbl sticky-first">
        <thead>
          <tr>
            {head('ticker', 'Company', undefined, true)}
            <th className="text-left!"><Hint label="Beat (green) or missed (red) the EPS estimate, last 8 results, oldest left. Hover a dot for the numbers."><span className="cursor-help">Last 8 results</span></Hint></th>
            {head('beat', 'Beat rate', 'How often reported EPS was above the estimate, last 4 results')}
            {head('avg', 'Avg surprise', 'Average EPS surprise of the last 4 results')}
            {head('last', 'Last surprise')}
            {head('reaction', 'Last reaction', 'Share price reaction to the last results')}
            {head('date', 'Last reported', undefined, true)}
            <th className="text-left!">Next</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => {
            const last = c.results[0]
            const react = c.results.find((r) => has(r.reaction))
            return (
              <tr key={c.ticker} className="cursor-pointer" onClick={() => nav(href(c.ticker))}>
                <td>
                  <span className="flex items-center gap-2">
                    <Logo t={c.ticker} size={18} keep />
                    <span className="font-semibold">{c.ticker}</span>
                    <span className="max-w-48 truncate text-muted-foreground">{niceName(c.name)}</span>
                  </span>
                </td>
                <td className="text-left!"><Beats results={c.results} n={8} /></td>
                <td>{c.counted ? `${c.beats} of ${c.counted}` : '–'}</td>
                <td className={tone(c.avg_surprise)}>{pctSigned(c.avg_surprise, 1)}</td>
                <td><SurprisePill v={last?.surprise} /></td>
                <td className={tone(react?.reaction)}>{pctSigned(react?.reaction, 1)}</td>
                <td className="text-left! whitespace-nowrap">{last ? day(last.date) : '–'}</td>
                <td className="text-left! whitespace-nowrap text-muted-foreground">{c.next ? `${c.next.expected ? '≈ ' : ''}${short(c.next.date)}` : '–'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
