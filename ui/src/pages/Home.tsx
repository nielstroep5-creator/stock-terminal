import { useQuery } from '@tanstack/react-query'
import { ArrowRight, Bell, CalendarDays, ChartLine, Download, Layers, SlidersHorizontal, Star, Wallet } from 'lucide-react'
import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { AlertRow } from '@/components/app/AlertMenu'
import { Card } from '@/components/app/Page'
import { alertLink, useLatestAlerts, useMarkRead } from '@/lib/alerts'
import { useWatchlists } from '@/components/app/WatchStar'
import { EChart, useChartColors } from '@/components/chart/EChart'
import { ChangePill } from '@/components/ui/pill'
import { api } from '@/lib/api'
import { big, day, fmt, niceName, pctSigned, tone } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { useSettings } from '@/lib/settings'
import { cn } from '@/lib/utils'
import type { CompanyRow } from '@/lib/api'
import { Logo } from '@/components/app/Logo'
import { DataSetupBody, useDataSetup } from '@/components/app/DataSetup'
import { EXT_ICON, EXT_LABEL, EXT_TINT, ExtendedPrice } from '@/components/app/ExtendedPrice'
import type { Ext } from '@/components/app/ExtendedPrice'

type IndexQuote = {
  market_state?: string
  stored?: boolean
  regular?: { price: number; change: number; change_pct: number; previous_close?: number; date?: string } | null
  extended?: Ext | null
}
type MarketIndex = { symbol: string; name: string; quote?: IndexQuote | null; chart?: { t: (number | string)[]; close: number[]; daily?: boolean } | null }
type WlRow = { ticker: string; name: string; etf?: boolean; loaded?: boolean; spark?: number[]; price?: number | null; change?: number | null; position: number; r_1m?: number | null; pe?: number | null; earnings_date?: string | null }
type ComingEvent = { ticker: string; name: string; type: 'earnings' | 'ex_dividend' | 'dividend'; date: string; confirmed?: boolean; amount?: number | null; held?: boolean }
type PortfolioBrief = { connected: boolean; source?: string | null; account_currency?: string; totals?: { total_value?: number | null; unrealized_pl?: number | null; unrealized_pct?: number | null; today_pl?: number | null; today_pct?: number | null } }
type IndexJob = { name: string; running: boolean; total: number; done: number; failed: number }

// Yahoo's market state -> the session shown in the tag next to the date (same words and icons as the price lines)
const MKT_SESSION: Record<string, 'open' | 'pre' | 'post' | 'overnight' | 'closed'> = { REGULAR: 'open', PRE: 'pre', PREPRE: 'overnight', POST: 'post', POSTPOST: 'overnight', OVERNIGHT: 'overnight', CLOSED: 'closed' }
const num2 = (v: number | null | undefined) => (v === null || v === undefined ? '–' : v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }))

function Section({ title, side, children }: { title: string; side?: ReactNode; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <h2 className="text-[15px] font-semibold">{title}</h2>
        {side}
      </div>
      {children}
    </section>
  )
}

export function Spark({ values }: { values?: number[] }) {
  if (!values || values.length < 2) return <span className="text-muted-foreground">–</span>
  const w = 72
  const h = 22
  const lo = Math.min(...values)
  const span = Math.max(...values) - lo || 1
  const pts = values.map((y, i) => `${((i / (values.length - 1)) * w).toFixed(1)},${(h - 2 - ((y - lo) / span) * (h - 4)).toFixed(1)}`).join(' ')
  return (
    <svg aria-hidden width={w} height={h} viewBox={`0 0 ${w} ${h}`} className={cn('inline-block fill-none stroke-[1.5]', values[values.length - 1] >= values[0] ? 'stroke-up' : 'stroke-down')}>
      <polyline points={pts} />
    </svg>
  )
}

/** Today's session of one index: green when above yesterday's close (the dotted line), red when below. */
function IndexCard({ x }: { x: MarketIndex }) {
  const nav = useNavigate()
  const c = useChartColors()
  const { settings } = useSettings()
  const r = x.quote?.regular
  const ext = settings.show_extended ? x.quote?.extended : null
  const option = useMemo(() => {
    const d = x.chart
    if (!d?.close.length) return null
    const base = d.daily ? d.close[0] : (r?.previous_close ?? d.close[0])
    const lo = Math.min(...d.close, base)
    const hi = Math.max(...d.close, base)
    const pad = (hi - lo) * 0.06 || 1
    const col = d.close[d.close.length - 1] >= base ? c.up : c.down
    const tip = { trigger: 'axis', backgroundColor: c.card, borderColor: c.border, textStyle: { color: c.text, fontSize: 12 } }
    const line = { type: 'line', showSymbol: false, lineStyle: { width: 1.6, color: col }, itemStyle: { color: col }, areaStyle: { origin: base, color: col + '24' } }
    if (d.daily)
      // no connection: the last closes from the database, one point a day
      return {
        animation: false,
        grid: { left: 0, right: 0, top: 4, bottom: 4 },
        tooltip: { ...tip, formatter: (p: { name: string; value: number }[]) => `${day(p[0].name)} close<br><b>${num2(p[0].value)}</b>` },
        xAxis: { type: 'category', show: false, data: d.t, boundaryGap: false },
        yAxis: { type: 'value', show: false, min: lo - pad, max: hi + pad },
        series: [{ ...line, data: d.close }],
      }
    const t0 = (d.t[0] as number) * 1000
    return {
      animation: false,
      grid: { left: 0, right: 0, top: 4, bottom: 4 },
      tooltip: {
        ...tip,
        formatter: (p: { value: number[] }[]) => `${new Date(p[0].value[0]).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' })} ET<br><b>${num2(p[0].value[1])}</b>`,
      },
      xAxis: { type: 'time', show: false, min: t0, max: t0 + 6.5 * 3600e3 },
      yAxis: { type: 'value', show: false, min: lo - pad, max: hi + pad },
      series: [{
        ...line, data: d.t.map((t, i) => [(t as number) * 1000, d.close[i]]),
        markLine: { silent: true, symbol: 'none', label: { show: false }, lineStyle: { type: 'dotted', color: c.muted, width: 1.5 }, data: [{ yAxis: base }] },
      }],
    }
  }, [x, r, c])
  return (
    <Card className="flex cursor-pointer items-stretch gap-4 p-4 transition-colors hover:bg-panel">
      <button type="button" className="min-w-[120px] text-left outline-none" onClick={() => nav(`/etf/${x.symbol}`)}>
        <div className="text-[13px] font-semibold">{x.symbol}</div>
        <div className="truncate text-xs text-muted-foreground">{x.name}</div>
        <div className="num mt-1.5 text-xl font-bold tracking-tight">{r ? num2(r.price) : '–'}</div>
        {/* change_pct is a fraction (0.0036), like everywhere in the app; it was shown as a percent: -0.00% */}
        {r && <ChangePill value={r.change_pct}>{pctSigned(r.change_pct)}</ChangePill>}
        {x.quote?.stored && <div className="mt-1 text-xs text-muted-foreground">Close {day(r?.date ?? null, false)}{x.chart?.daily ? ' · last 5 days' : ''}</div>}
        {ext && <ExtendedPrice ext={ext} compact plain />}
      </button>
      <div className="min-w-0 flex-1">{option ? <EChart option={option} className="h-[96px]" /> : <div className="flex h-[96px] items-center justify-center text-xs text-muted-foreground">No chart without a connection</div>}</div>
    </Card>
  )
}

function MoverList({ title, rows }: { title: string; rows: CompanyRow[] }) {
  const nav = useNavigate()
  return (
    <Card className="min-w-0 py-1">
      <div className="px-4 pt-3 pb-1 text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">{title}</div>
      {rows.map((r) => (
        <button key={r.ticker} type="button" onClick={() => nav(`/stock/${r.ticker}`)} className="flex w-full items-center gap-3 px-4 py-2 text-left outline-none hover:bg-panel focus-visible:bg-panel">
          <Logo t={r.ticker} size={18} keep />
          <span className="w-14 shrink-0 text-[13px] font-semibold">{r.ticker}</span>
          <span className="min-w-0 flex-1 truncate text-muted-foreground">{niceName(r.name)}</span>
          <span className="num">{fmt(r.close, 'usdps')}</span>
          <span className="flex w-20 justify-end"><ChangePill value={r.change} arrow={false}>{pctSigned(r.change)}</ChangePill></span>
        </button>
      ))}
    </Card>
  )
}

/** A new install has no companies yet: the starter pack (a few minutes) first, the same choices as the pop-up after the
    welcome screen; loading from the SEC company by company stays as the slow way (the same job as "Load S&P 500.bat"). */
function GetStarted() {
  const [name, setName] = useState<string | null>(null)
  const setup = useDataSetup()
  const job = useQuery({
    queryKey: ['load-index', name],
    queryFn: () => api.get<IndexJob>(`/api/load-index/${name}`),
    enabled: !!name,
    refetchInterval: (q) => (q.state.data && !q.state.data.running ? false : 4000),
  })
  const start = async (n: string) => {
    await api.post(`/api/load-index/${n}`, {})
    setName(n)
  }
  const j = job.data
  return (
    <Card className="flex flex-col gap-3 p-5">
      <div className="flex items-center gap-2.5">
        <span className="flex size-9 items-center justify-center rounded-lg bg-accent text-accent-foreground"><Download className="size-[18px]" /></span>
        <div>
          <h2 className="text-[15px] font-semibold">Get started</h2>
          <p className="text-muted-foreground">The app has no companies yet. The quickest way: the S&amp;P 500 in one download.</p>
        </div>
      </div>
      {j ? (
        <div className="flex flex-col gap-1.5">
          <div className="h-2 overflow-hidden rounded-full bg-secondary">
            <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${j.total ? (100 * j.done) / j.total : 0}%` }} />
          </div>
          <span className="text-muted-foreground">
            {j.running ? `Loading: ${j.done} of ${j.total} companies done` : `Finished: ${j.done} of ${j.total} companies loaded`}
            {j.failed ? ` · ${j.failed} could not be loaded` : ''}. {j.running && 'Companies show up here as soon as they are in.'}
          </span>
        </div>
      ) : (
        <>
          {setup.data && <div className="max-w-2xl"><DataSetupBody info={setup.data} /></div>}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t pt-3 text-xs text-muted-foreground">
            <span>Other ways, straight from the SEC (slower, works without the download page):</span>
            <Button variant="link" size="sm" className="h-auto p-0 text-xs" onClick={() => void start('popular')}>30 popular companies (a few minutes)</Button>
            <Button variant="link" size="sm" className="h-auto p-0 text-xs" onClick={() => void start('sp500')}>the S&amp;P 500 one by one (several hours)</Button>
          </div>
        </>
      )}
    </Card>
  )
}

const EV_LABEL = { earnings: 'Earnings', ex_dividend: 'Ex-dividend', dividend: 'Dividend paid' } as const

/** Earnings and dividend dates of everything you watch or hold, the coming 30 days. */
function ComingUp() {
  const nav = useNavigate()
  const stored = useQuery({ queryKey: ['coming-up'], queryFn: () => api.get<{ events: ComingEvent[] }>('/api/coming-up') })
  const fresh = useQuery({ queryKey: ['coming-up', 'fresh'], queryFn: () => api.get<{ events: ComingEvent[] }>('/api/coming-up?refresh=1'), retry: false, staleTime: 3600e3 })
  const ev = (fresh.data ?? stored.data)?.events ?? []
  return (
    <Card className="min-w-0 py-1">
      <div className="flex items-center gap-2 px-4 pt-3 pb-1">
        <CalendarDays className="size-4 text-muted-foreground" />
        <span className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">Coming up · 30 days</span>
      </div>
      {ev.length === 0 ? (
        <p className="px-4 py-3 text-muted-foreground">{stored.isLoading || fresh.isLoading ? 'Looking for dates…' : 'No earnings or dividend dates for your companies in the coming 30 days.'}</p>
      ) : (
        ev.slice(0, 10).map((e) => (
          <button key={`${e.ticker}-${e.type}-${e.date}`} type="button" onClick={() => nav(`/stock/${e.ticker}`)} className="flex w-full items-center gap-3 px-4 py-1.5 text-left outline-none hover:bg-panel focus-visible:bg-panel">
            <span className="num w-14 shrink-0 text-muted-foreground">{day(e.date, false)}</span>
            <Logo t={e.ticker} size={16} keep />
            <span className="w-14 shrink-0 text-[13px] font-semibold">{e.ticker}</span>
            <span className="min-w-0 flex-1 truncate">
              {EV_LABEL[e.type]}
              {e.type === 'earnings' && e.confirmed === false ? <span className="text-muted-foreground"> (expected)</span> : null}
              {e.amount && e.type !== 'earnings' ? <span className="text-muted-foreground"> · ${e.amount.toFixed(2)}</span> : null}
            </span>
            {e.held && <span className="rounded bg-secondary px-1.5 text-[11px] text-muted-foreground">held</span>}
          </button>
        ))
      )}
    </Card>
  )
}

/** The latest alerts (prices, results, dividends, analysts) with the unread count; hidden until there is one. */
function AlertsCard() {
  const nav = useNavigate()
  const markRead = useMarkRead()
  const q = useLatestAlerts(5)
  const d = q.data
  if (!d || !d.items.length) return null
  return (
    <Card className="min-w-0 py-1">
      <div className="flex items-center gap-2 px-4 pt-3 pb-1">
        <Bell className="size-4 text-muted-foreground" />
        <span className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">Alerts{d.unread ? ` · ${d.unread} unread` : ''}</span>
        <div className="grow" />
        <Link to="/alerts/list" className="inline-flex items-center gap-1 text-[13px] font-medium text-primary hover:underline">All <ArrowRight className="size-3.5" /></Link>
      </div>
      <div className="px-2 pb-1">
        {d.items.map((i) => (
          <AlertRow key={i.id} i={i} compact onOpen={(x) => { if (!x.read) void markRead({ ids: [x.id] }); nav(alertLink(x) ?? '/alerts/list') }} />
        ))}
      </div>
    </Card>
  )
}

/** Your portfolio in one card (Trading 212 connected, or your own trades). */
function PortfolioBriefCard() {
  const q = useQuery({ queryKey: ['portfolio', 'brief'], queryFn: () => api.get<PortfolioBrief>('/api/portfolio'), retry: false })
  const p = q.data
  if (!(p?.connected || p?.source === 'own') || !p.totals) return null
  const t = p.totals
  const cur = p.account_currency === 'USD' ? '$' : p.account_currency === 'EUR' ? '€' : p.account_currency === 'GBP' ? '£' : ''
  return (
    <Link to="/portfolio" className="flex items-center gap-4 rounded-xl border bg-card p-4 outline-none transition-colors hover:bg-panel focus-visible:ring-2 focus-visible:ring-ring/50">
      <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-accent text-accent-foreground"><Wallet className="size-[18px]" /></span>
      <div className="min-w-0 flex-1">
        <div className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">Portfolio</div>
        <div className="num text-lg font-bold">{t.total_value != null ? `${cur}${big(t.total_value)}` : '–'}</div>
      </div>
      <div className="num text-right text-[13px]">
        <div className={tone(t.today_pct)}>{t.today_pct != null ? `${pctSigned(t.today_pct)} today` : 'Today –'}</div>
        <div className={cn('text-muted-foreground', tone(t.unrealized_pct))}>{t.unrealized_pct != null ? `${pctSigned(t.unrealized_pct)} total` : ''}</div>
      </div>
      <ArrowRight className="size-4 text-muted-foreground" />
    </Link>
  )
}

function SessionTag({ session }: { session: 'open' | 'pre' | 'post' | 'overnight' | 'closed' }) {
  if (session === 'open')
    return (
      <span className="inline-flex items-center gap-1.5 self-center rounded-md bg-up-bg px-1.5 py-0.5 text-xs font-semibold text-up">
        <span aria-hidden className="size-1.5 animate-pulse rounded-full bg-up" />
        Market open
      </span>
    )
  if (session === 'closed') return <span className="self-center rounded-md bg-secondary px-1.5 py-0.5 text-xs font-semibold text-muted-foreground">Market closed</span>
  const Icon = EXT_ICON[session]
  return (
    <span className="inline-flex items-center gap-1 self-center rounded-md bg-secondary px-1.5 py-0.5 text-xs font-semibold text-muted-foreground">
      <Icon aria-hidden className={cn('size-3.5', EXT_TINT[session])} />
      {EXT_LABEL[session]}
    </span>
  )
}

export function HomePage() {
  const nav = useNavigate()
  const { settings, pref } = useSettings()
  const market = useQuery({ queryKey: ['market'], queryFn: () => api.get<{ indexes: MarketIndex[] }>('/api/market'), refetchInterval: 60_000, retry: false })
  const companies = useQuery({ queryKey: ['companies', 'live'], queryFn: () => api.get<CompanyRow[]>('/api/companies?live=1'), refetchInterval: 60_000, retry: false })
  const { data: lists } = useWatchlists()
  const list = lists?.find((l) => l.id === pref('ui2', 'wl_id', 0)) ?? lists?.[0]
  const wl = useQuery({
    queryKey: ['watchlist-rows', list?.id, 'home'],
    queryFn: () => api.get<{ rows: WlRow[] }>(`/api/watchlists/${list?.id}/rows?live=1`),
    enabled: !!list?.tickers.length,
    refetchInterval: 60_000,
    retry: false,
  })

  const movers = useMemo(() => {
    const ch = (companies.data ?? []).filter((r) => r.change !== null && r.change !== undefined).sort((a, b) => (b.change as number) - (a.change as number))
    const n = Math.min(6, Math.floor(ch.length / 2) || 1)
    return ch.length ? { up: ch.slice(0, n), down: ch.slice(-n).reverse() } : null
  }, [companies.data])
  // which session the moves are from: "today, live" or the date of the last close in the database
  const moveDay = useMemo(() => {
    const rows = companies.data ?? []
    if (rows.some((r) => r.live)) return 'today, live'
    const d = rows.map((r) => r.date).filter(Boolean).sort().pop()
    return d ? `session of ${day(d, false)}` : 'latest session'
  }, [companies.data])
  // the trading session now: the session of the extended price Yahoo is quoting, else its market state
  const q0 = market.data?.indexes.find((x) => x.quote && !x.quote.stored)?.quote
  const session = q0?.extended?.session ?? (q0?.market_state ? MKT_SESSION[q0.market_state] : undefined)
  const empty = companies.data && companies.data.length === 0
  const today = new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })
  const links: [string, string, string, ReactNode][] = [
    ['/screener', 'Screener', companies.data ? `All ${companies.data.filter((r) => r.in_screener).length} companies: filter, sort and compare` : 'Filter and sort every company in your database', <SlidersHorizontal key="i" />],
    ['/etfs', 'ETFs', 'Index funds: costs, returns, holdings and dividends', <Layers key="i" />],
    ['/watchlist', 'Watchlist', 'Your own lists: live prices, returns, valuation and coming results', <Star key="i" />],
    ['/compare', 'Compare', 'Chart revenue, margins, growth and more side by side', <ChartLine key="i" />],
  ]

  return (
    <main className={cn('flex flex-col gap-7 p-6', settings.page_width === 'centered' && 'mx-auto w-full max-w-[1180px]')}>
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-[22px] font-bold tracking-tight">Home</h1>
        <span className="text-muted-foreground">{today}</span>
        {session ? <SessionTag session={session} /> : null}
      </header>

      {empty && <GetStarted />}

      <Section title="Markets">
        {market.data ? (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            {market.data.indexes.map((x) => (
              <IndexCard key={x.symbol} x={x} />
            ))}
          </div>
        ) : (
          <Card className="p-4 text-muted-foreground">{market.isError ? 'Market data is not available right now (no internet connection?).' : 'Loading the markets…'}</Card>
        )}
      </Section>

      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        {list && wl.data && wl.data.rows.length > 0 ? (
          <Section
            title={list.name}
            side={
              <Link to={`/watchlist/${list.id}`} className="inline-flex items-center gap-1 text-[13px] font-medium text-primary hover:underline">
                All {list.tickers.length} <ArrowRight className="size-3.5" />
              </Link>
            }
          >
            <Card className="min-w-0 overflow-x-auto">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Company</th>
                    <th>3 months</th>
                    <th>Price</th>
                    <th>Today</th>
                    <th>1 month</th>
                    <th>P/E</th>
                    <th>Next earnings</th>
                  </tr>
                </thead>
                <tbody>
                  {[...wl.data.rows].sort((a, b) => a.position - b.position).slice(0, 8).map((r) => (
                    <tr key={r.ticker} className="cursor-pointer" onClick={() => nav(r.etf ? `/etf/${r.ticker}` : r.loaded ? `/stock/${r.ticker}` : `/load/${r.ticker}`)}>
                      <td>
                        <div className="flex max-w-[300px] items-center gap-2">
                          <Logo t={r.ticker} size={18} keep />
                          <span className="w-14 shrink-0 font-semibold">{r.ticker}</span>
                          <span className="min-w-0 truncate text-muted-foreground" title={niceName(r.name)}>{r.name !== r.ticker ? niceName(r.name) : ''}</span>
                        </div>
                      </td>
                      <td><Spark values={r.spark} /></td>
                      <td>{fmt(r.price, 'usdps')}</td>
                      <td><ChangePill value={r.change} arrow={false}>{pctSigned(r.change)}</ChangePill></td>
                      <td className={tone(r.r_1m)}>{pctSigned(r.r_1m)}</td>
                      <td>{fmt(r.pe, 'x')}</td>
                      <td className="text-muted-foreground">{r.earnings_date ? day(r.earnings_date, false) : '–'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          </Section>
        ) : (
          <Section title="Watchlist">
            <Card className="p-4 text-muted-foreground">
              Star a company (☆ next to its name) to follow it here. <Link to="/watchlist" className="font-medium text-primary hover:underline">Open the Watchlist</Link>
            </Card>
          </Section>
        )}
        <Section title="Your week">
          <div className="flex flex-col gap-4">
            <PortfolioBriefCard />
            <ComingUp />
            <AlertsCard />
          </div>
        </Section>
      </div>

      <Section title="Biggest moves" side={<span className="text-muted-foreground">Your companies, {moveDay}</span>}>
        {movers ? (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <MoverList title="Top gainers" rows={movers.up} />
            <MoverList title="Top losers" rows={movers.down} />
          </div>
        ) : (
          <Card className="p-4 text-muted-foreground">{companies.isLoading ? 'Loading prices…' : empty ? 'No companies yet.' : 'Prices are not available right now.'}</Card>
        )}
      </Section>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {links.map(([to, title, text, icon]) => (
          <Link key={to} to={to} className="group flex items-start gap-3 rounded-xl border bg-card p-4 outline-none transition-colors hover:bg-panel focus-visible:ring-2 focus-visible:ring-ring/50">
            <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-accent text-accent-foreground [&_svg]:size-[18px]">{icon}</span>
            <span className="min-w-0 flex-1">
              <span className="block font-semibold">{title}</span>
              <span className="block text-[13px] text-muted-foreground">{text}</span>
            </span>
            <ArrowRight className="mt-1 size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
          </Link>
        ))}
      </div>
    </main>
  )
}
