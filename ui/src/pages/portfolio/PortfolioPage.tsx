import { ArrowDown, ArrowUp, Briefcase, Landmark, ListPlus, RefreshCw } from 'lucide-react'
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom'
import { Card, Page } from '@/components/app/Page'
import { Empty, Stat } from '@/components/app/bits'
import { Button } from '@/components/ui/button'
import { TabLinks } from '@/components/ui/tabs'
import { Hint } from '@/components/ui/tooltip'
import { fmt, tone } from '@/lib/format'
import { useSettings } from '@/lib/settings'
import { cn } from '@/lib/utils'
import { CurrencySwitch, pctTxt, qty, sharePrice, t212Symbol, useMoney, usePortfolio } from './common'
import type { Portfolio, Position } from './common'
import { HoldingPage } from './Holding'
import { OwnTrades } from './OwnTrades'
import { Performance } from './Performance'
import { AllocationTab, AnalysisTab, CalendarTab, CostsTab, XrayTab } from './Tabs'
import { Logo } from '@/components/app/Logo'

// The tabs are addresses (/portfolio/xray); the last one is remembered with the old screen's key (state.pfTab).
const TABS = [
  { id: 'holdings', old: 'holdings', label: 'Holdings' },
  { id: 'allocation', old: 'alloc', label: 'Allocation' },
  { id: 'xray', old: 'xray', label: 'X-ray' },
  { id: 'calendar', old: 'cal', label: 'Calendar' },
  { id: 'analysis', old: 'analysis', label: 'Analysis' },
  { id: 'costs', old: 'costs', label: 'Costs' },
  { id: 'trades', old: 'trades', label: 'My trades' }, // only without Trading 212: your own trades (own.py)
]

/** /portfolio, /portfolio/<tab> or /portfolio/<Trading 212 code> (one holding, as in the old screen). */
export function PortfolioRoute() {
  const { sub } = useParams()
  const { pref } = useSettings()
  if (!sub) {
    const last = TABS.find((t) => t.old === pref<string>('state', 'pfTab', 'holdings')) ?? TABS[0]
    return <Navigate to={`/portfolio/${last.id}`} replace />
  }
  const tab = TABS.find((t) => t.id === sub)
  return tab ? <PortfolioPage tab={tab.id} /> : <HoldingPage t212={sub} />
}

function PortfolioPage({ tab }: { tab: string }) {
  const { setPref, pref } = useSettings()
  const { data: d, loading, refreshing, refresh } = usePortfolio()
  const old = TABS.find((t) => t.id === tab)!.old
  if (pref<string>('state', 'pfTab', '') !== old) queueMicrotask(() => setPref('state', 'pfTab', old))

  if (loading || !d) return <Page title="Portfolio"><Card><Empty>Loading…</Empty></Card></Page>
  if (!d.connected && !d.positions) {
    // nothing yet: the "My trades" tab is where you start without Trading 212
    if (tab === 'trades') return <Page title="Portfolio"><OwnTrades /></Page>
    return <NotConnected />
  }
  const own = d.source === 'own'
  const tabs = TABS.filter((t) => t.id !== 'trades' || own || !d.connected)
  const refreshLabel = own ? 'Update prices from Yahoo' : 'Refresh from Trading 212'
  const actions = (
    <>
      <span className="text-xs text-muted-foreground">{d.live ? `Live · ${d.as_of}` : d.synced_at ? `${own ? 'Updated' : 'Last sync'} ${d.synced_at}` : ''}</span>
      {(d.connected || own) && <Hint label={refreshLabel}><Button size="icon" aria-label={refreshLabel} disabled={refreshing} onClick={refresh}><RefreshCw className={cn(refreshing && 'animate-spin')} /></Button></Hint>}
      <CurrencySwitch acc={d.account_currency} other={d.other_currency} rate={d.fx?.rate} />
      <Button asChild><Link to="/tax"><Landmark /> Tax (box 3)</Link></Button>
    </>
  )
  return (
    <Page title="Portfolio" actions={actions}>
      {!!d.errors?.length && <div className="rounded-lg border border-down/30 bg-down-bg px-4 py-2.5 text-[13px] text-down">{d.errors[0]} Showing the last sync.</div>}
      <Totals d={d} />
      <PerformanceWith d={d} />
      <TabLinks label="Portfolio views" tabs={tabs.map((t) => ({ to: `/portfolio/${t.id}`, label: t.label }))} />
      {tab === 'holdings' && <Holdings d={d} />}
      {tab === 'allocation' && <AllocationTab />}
      {tab === 'xray' && <XrayTab />}
      {tab === 'calendar' && <CalendarTab />}
      {tab === 'analysis' && <AnalysisTab />}
      {tab === 'costs' && <CostsTab />}
      {tab === 'trades' && (own || !d.connected ? <OwnTrades /> : <Card><Empty>Trading 212 is connected, so the portfolio shows your Trading 212 account.</Empty></Card>)}
    </Page>
  )
}

function PerformanceWith({ d }: { d: Portfolio }) {
  const { cur } = useMoney(d.account_currency, d.other_currency, d.fx?.rate)
  return <Performance cur={cur} />
}

function NotConnected() {
  return (
    <Page title="Portfolio">
      <Card>
        <Empty className="min-h-72">
          <span className="mb-1 flex size-11 items-center justify-center rounded-full bg-secondary text-primary"><Briefcase className="size-5" /></span>
          <span className="text-[15px] font-semibold text-foreground">Track your portfolio here</span>
          <span className="max-w-md">Add your buys and sells yourself (or import your broker's CSV export), or connect a Trading 212 account. Either way you see your holdings, dividends and how you did against the S&P 500. Everything stays on this computer.</span>
          <span className="mt-2 flex flex-wrap justify-center gap-2">
            <Button asChild variant="default"><Link to="/portfolio/trades"><ListPlus /> Add my trades</Link></Button>
            <Button asChild><Link to="/settings?find=t212">Connect Trading 212</Link></Button>
          </span>
        </Empty>
      </Card>
    </Page>
  )
}

function Totals({ d }: { d: Portfolio }) {
  const { m, ms } = useMoney(d.account_currency, d.other_currency, d.fx?.rate)
  const T = d.totals
  const n = d.positions.length
  const own = d.source === 'own'
  const todayPart = T.today_count && T.today_count < n ? ` · ${T.today_count} of ${n} holdings` : ''
  return (
    <Card className="grid grid-cols-2 gap-x-6 gap-y-4 p-4 sm:grid-cols-4 xl:grid-cols-7">
      {/* one source: the holdings in the table plus cash, so the header and the table always add up */}
      {own ? (
        <Stat label="Total value" tip="What your holdings are worth now. Cash is not tracked for your own trades." value={m(T.invested)} sub={`${n} ${n === 1 ? 'holding' : 'holdings'}`} />
      ) : (
        <Stat
          label="Total value"
          tip={`Your holdings plus cash.${T.total_value != null && Math.abs(T.total_value - (T.invested + (T.cash || 0))) > 1 ? ` Trading 212's own total is ${m(T.total_value)} (it counts pending orders and other items too).` : ''}`}
          value={m(T.invested + (T.cash || 0))}
          sub={`Holdings ${m(T.invested)} · cash ${m(T.cash)}`}
        />
      )}
      <Stat label="Invested" tip="What you paid for your open positions. Below it: what they are worth now." value={m(T.cost)} sub={`Now worth ${m(T.invested)}`} />
      <Stat label="Return" tip="Open positions: current value minus what you paid, currency effect included." value={ms(T.unrealized_pl)} className={tone(T.unrealized_pl)} sub={pctTxt(T.unrealized_pct)} />
      <Stat label="Today" tip="Change since the previous close on each holding's own exchange (Yahoo). Holdings without a Yahoo quote are left out." value={T.today_pl === null || T.today_pl === undefined ? '–' : ms(T.today_pl)} className={tone(T.today_pl)} sub={T.today_pl === null || T.today_pl === undefined ? 'no quotes right now' : pctTxt(T.today_pct) + todayPart} />
      <Stat label="Currency effect" tip={own ? 'How much of the return comes from exchange rates: the value now minus the same value at the rates you bought at.' : "How much of the return comes from exchange rates (Trading 212's own figure)."} value={ms(T.fx_impact)} className={tone(T.fx_impact)} sub="part of the return" />
      <Stat label="Realized" value={ms(T.realized_pl)} className={tone(T.realized_pl)} sub="from shares sold" />
      <Stat label="Dividends" tip={own ? 'Worked out from Yahoo: the shares you held before each ex-dividend date times the dividend, before tax.' : 'All dividends paid into your account, as Trading 212 reports them.'} value={m(T.dividends)} sub="received" />
    </Card>
  )
}

type Col = { id: string; label: string; get: (p: Position) => number | string | null | undefined; tip?: string }
const COLS: Col[] = [
  { id: 'name', label: 'Holding', get: (p) => p.name || p.t212_ticker },
  { id: 'quantity', label: 'Shares', get: (p) => p.quantity },
  { id: 'avg', label: 'Avg. price', get: (p) => p.avg_price },
  { id: 'price', label: 'Price', get: (p) => p.current_price },
  { id: 'today', label: 'Today', get: (p) => p.today_pct },
  { id: 'value', label: 'Value', get: (p) => p.value },
  { id: 'weight', label: 'Weight', get: (p) => p.weight },
  { id: 'pl', label: 'Return', get: (p) => p.pl, tip: 'Current value minus what you paid, as Trading 212 reports it, currency effect included.' },
  { id: 'pl_pct', label: 'Return %', get: (p) => p.pl_pct },
  { id: 'fx', label: 'Currency effect', get: (p) => p.fx_impact },
  { id: 'divs', label: 'Dividends', get: (p) => p.dividends },
]

function Holdings({ d }: { d: Portfolio }) {
  const nav = useNavigate()
  const { pref, setPref } = useSettings()
  const { m, ms, cur } = useMoney(d.account_currency, d.other_currency, d.fx?.rate)
  const [sortCol, sortDir] = pref<string>('ui2', 'pf_sort', 'value|desc').split('|')
  const col = COLS.find((c) => c.id === sortCol) ?? COLS[5]
  const rows = [...d.positions].sort((a, b) => {
    const x = col.get(a)
    const y = col.get(b)
    if (x === y) return 0
    if (x === null || x === undefined) return 1
    if (y === null || y === undefined) return -1
    const r = typeof x === 'string' ? x.localeCompare(String(y)) : (x as number) - (y as number)
    return sortDir === 'asc' ? r : -r
  })
  const sortBy = (id: string) => setPref('ui2', 'pf_sort', `${id}|${sortCol === id && sortDir === 'desc' ? 'asc' : id === 'name' && sortCol !== id ? 'asc' : 'desc'}`)
  return (
    <div className="flex flex-col gap-2">
      <Card className="min-w-0 overflow-x-auto">
        <table className="tbl sticky-first">
          <thead>
            <tr>
              {COLS.map((c) => (
                <th key={c.id}>
                  <button type="button" onClick={() => sortBy(c.id)} title={c.tip} className={cn('inline-flex items-center gap-1 hover:text-foreground', col.id === c.id && 'text-foreground')}>
                    {c.label}
                    {col.id === c.id && (sortDir === 'asc' ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />)}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.t212_ticker} className="cursor-pointer" onClick={() => nav(`/portfolio/${encodeURIComponent(p.t212_ticker)}`)}>
                <td className="max-w-72">
                  <div className="flex items-center gap-2.5">
                    <Logo t={p.ticker || p.yahoo} size={24} keep />
                    <div className="min-w-0">
                      <div className="truncate font-medium" title={p.isin || ''}>{p.name || p.t212_ticker}</div>
                      <div className="text-xs text-muted-foreground" title={p.yahoo ? `Yahoo: ${p.yahoo}` : undefined}>{p.ticker || t212Symbol(p.t212_ticker)}</div>
                    </div>
                  </div>
                </td>
                <td>{qty(p.quantity)}</td>
                <td>{sharePrice(p.avg_price, p.currency)}</td>
                <td>{sharePrice(p.current_price, p.currency)}</td>
                <td className={tone(p.today_pct)}>{p.today_pct === null || p.today_pct === undefined ? '–' : pctTxt(p.today_pct)}</td>
                <td className="font-medium">{m(p.value)}</td>
                <td>{fmt(p.weight, 'pct')}</td>
                <td className={tone(p.pl)}>{ms(p.pl)}</td>
                <td className={tone(p.pl)}>{pctTxt(p.pl_pct)}</td>
                <td className={tone(p.fx_impact)}>{ms(p.fx_impact)}</td>
                <td>{p.dividends ? m(p.dividends) : '–'}</td>
              </tr>
            ))}
            {!rows.length && <tr><td colSpan={COLS.length} className="py-10! text-center! text-muted-foreground">No open positions.</td></tr>}
          </tbody>
        </table>
      </Card>
      <p className="text-xs text-muted-foreground">
        {cur !== d.account_currency && d.fx?.rate ? `Amounts converted from ${d.account_currency} at today's rate (${d.fx.pair} ${d.fx.rate.toFixed(4)}, ${d.fx.source}). ` : ''}
        Prices per share in the currency the share trades in. Click a holding for its own page: your trades, its chart and company details.
      </p>
    </div>
  )
}
