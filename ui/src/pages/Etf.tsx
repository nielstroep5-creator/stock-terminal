import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowDown, ArrowRight, ArrowUp, ChartLine, Info, Plus, RefreshCw, Search } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom'
import { Card, Page } from '@/components/app/Page'
import { Bars, H2, Kv } from '@/components/app/bits'
import { AlertBell } from '@/components/app/AlertBell'
import { WatchStar } from '@/components/app/WatchStar'
import { BAR_TIP, EChart, FOCUS_SELF, baseOption, echarts, useChartColors } from '@/components/chart/EChart'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ChangePill } from '@/components/ui/pill'
import { Segmented } from '@/components/ui/segmented'
import { TabLinks } from '@/components/ui/tabs'
import { Hint } from '@/components/ui/tooltip'
import { api } from '@/lib/api'
import { big, dateAxis, day, fmt, money, niceName, pctSigned, tone } from '@/lib/format'
import { useSettings } from '@/lib/settings'
import type { Settings } from '@/lib/settings'
import { cn } from '@/lib/utils'
import { Logo } from '@/components/app/Logo'
import { ExtendedPrice, etDay, etTime } from '@/components/app/ExtendedPrice'
import type { Ext } from '@/components/app/ExtendedPrice'

type N = number | null | undefined
type EtfRow = { ticker: string; name: string; family?: string; category?: string; expense_ratio: N; total_assets: N; yield: N; close: N; change?: N; live?: boolean; date?: string | null }
type Profile = {
  name: string; family?: string; category?: string; exchange?: string; description?: string; inception?: string
  expense_ratio: N; total_assets: N; pe: N; beta_3y: N; nav: N; turnover: N; avg_volume: N; yield: N
  holdings?: { symbol: string; name: string; weight: number; loaded?: boolean }[]; holdings_count?: number
  sectors?: Record<string, number>; assets?: Record<string, number>
}
type EtfInfo = {
  ticker: string; profile: Profile | null; has_prices: boolean; price: N; price_date?: string; change: N; high_52w: N; low_52w: N; first_date?: string
  price_live?: boolean; quote?: { regular?: { time?: number } | null; extended?: Ext | null }
  returns: { period: string; since: string; price: N; total: N; annualized: boolean; total_ann: N }[]
  years: { year: number; total: number; partial: boolean }[]
  risk: { vol_1y: N; vol_5y: N; max_dd: { value: number; peak: string; bottom: string } | null; max_dd_5y: { value: number; peak: string; bottom: string } | null; from_high: N } | null
  dividends: { ttm: N; ttm_yield: N; frequency?: string; per_year: { year: number; amount: number; partial: boolean }[]; recent: { date: string; amount: number }[] } | null
}
type Holding = { rank: number; ticker?: string | null; name: string; shares: N; weight: N; price?: N; change?: N; loaded?: boolean }
type Holdings = { count: number; top10: N; category?: string; total_assets: N; pe: N; as_of?: string; source?: string; reweighted?: boolean; sectors?: Record<string, number>; sectors_source?: string; countries?: Record<string, number>; assets?: Record<string, number>; holdings: Holding[] }
type Prices = { dates: string[]; close: number[]; adj: (number | null)[]; open?: (number | null)[]; high?: (number | null)[]; low?: (number | null)[] }
type Range = Settings['price_range']

const RANGES: [Range, number][] = [['1M', 31], ['3M', 92], ['6M', 183], ['YTD', 0], ['1Y', 365], ['3Y', 1096], ['5Y', 1826], ['10Y', 3652], ['Max', 1e6]]
const since = (range: Range, last: string) => (range === 'YTD' ? `${last.slice(0, 4)}-01-01` : new Date(new Date(last).getTime() - (RANGES.find(([k]) => k === range)?.[1] ?? 1e6) * 864e5).toISOString().slice(0, 10))
const RANGE_WORDS: Record<string, string> = { '1M': 'past month', '3M': 'past 3 months', '6M': 'past 6 months', YTD: 'this year', '1Y': 'past year', '3Y': 'past 3 years', '5Y': 'past 5 years', '10Y': 'past 10 years', Max: 'all time' }
const has = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const dash = <span className="text-muted-foreground">–</span>
const monthYear = (d: string) => new Date(d + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', year: 'numeric' })
const ASSET_WORD: Record<string, string> = { Stocks: 'an equity', Bonds: 'a bond', 'Cash & money market': 'a money market' }

/* ------------------------------------------------------------------ the ETFs page */
export function EtfsPage() {
  const nav = useNavigate()
  const stored = useQuery({ queryKey: ['etfs'], queryFn: () => api.get<EtfRow[]>('/api/etfs') })
  const live = useQuery({ queryKey: ['etfs', 'live'], queryFn: () => api.get<EtfRow[]>('/api/etfs?live=1'), refetchInterval: 60_000, retry: false })
  const popular = useQuery({ queryKey: ['etfs', 'popular'], queryFn: () => api.get<{ ticker: string; name: string }[]>('/api/etfs/popular'), retry: false })
  const rows = live.data ?? stored.data ?? []
  return (
    <Page title="ETFs" actions={<span className="text-[13px] text-muted-foreground">{rows.length} ETFs. Add one with the search (for example VOO, SCHD or XLK).</span>}>
      <Card className="min-w-0 overflow-x-auto">
        <table className="tbl left-2-4">
          <thead>
            <tr><th>Fund</th><th>Ticker</th><th>Category</th><th>Family</th><th>Price</th><th>{rows.some((r) => r.live) ? 'Today' : <Hint label={`No live prices: the change of the last stored day${rows[0]?.date ? ` (${day(rows[0].date)})` : ''}`}><span className="cursor-help">Last day</span></Hint>}</th><th>Assets</th><th>Expense ratio</th><th>Yield</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.ticker} className="cursor-pointer" onClick={() => nav(`/etf/${r.ticker}`)}>
                <td className="max-w-80 truncate font-medium">{r.name}</td>
                <td className="font-semibold">{r.ticker}</td>
                <td className="text-muted-foreground">{r.category || '–'}</td>
                <td className="max-w-52 truncate text-muted-foreground">{r.family || '–'}</td>
                <td>{fmt(r.close, 'usdps')}</td>
                <td className={tone(r.change)}>{has(r.change) ? pctSigned(r.change) : ''}</td>
                <td>{r.total_assets ? big(r.total_assets) : '–'}</td>
                <td>{fmt(r.expense_ratio, 'pct2')}</td>
                <td>{fmt(r.yield, 'pct2')}</td>
              </tr>
            ))}
            {!rows.length && <tr><td colSpan={9} className="py-10! text-center! text-muted-foreground">{stored.isLoading ? 'Loading…' : 'No ETFs yet. Add one with the search.'}</td></tr>}
          </tbody>
        </table>
      </Card>
      {!!popular.data?.length && (
        <section className="flex flex-col gap-3">
          <div className="flex items-baseline gap-3">
            <h2 className="text-[15px] font-semibold">Popular ETFs</h2>
            <span className="text-[13px] text-muted-foreground">Not in your app yet. Click one to load it (about 20 seconds).</span>
          </div>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
            {popular.data.map((p) => (
              <Link key={p.ticker} to={`/load/${p.ticker}`} title={p.name} className="group flex items-start gap-2 rounded-xl border bg-card p-3 transition-colors hover:bg-panel">
                <span className="min-w-0 flex-1">
                  <span className="block text-[13px] font-semibold">{p.ticker}</span>
                  <span className="block truncate text-xs text-muted-foreground">{p.name}</span>
                </span>
                <Plus className="size-4 text-muted-foreground group-hover:text-primary" />
              </Link>
            ))}
          </div>
        </section>
      )}
    </Page>
  )
}

/* ------------------------------------------------------------------ one ETF */
const autoChecked: Record<string, number> = {}

export function EtfPage() {
  const params = useParams()
  const t = (params.ticker ?? '').toUpperCase()
  const tab = params.tab === 'holdings' ? 'holdings' : 'overview'
  const { settings } = useSettings()
  const qc = useQueryClient()
  const stored = useQuery({ queryKey: ['company', t, 'etf'], queryFn: () => api.get<EtfInfo>(`/api/etf/${t}`) })
  const live = useQuery({ queryKey: ['company', t, 'etf', 'live'], queryFn: () => api.get<EtfInfo>(`/api/etf/${t}?live=1`), enabled: !!stored.data?.has_prices, refetchInterval: 60_000, retry: false })
  const info = live.data ?? stored.data
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const current = useRef(t)
  current.current = t

  const refresh = useCallback(async (ticker: string, force: boolean) => {
    setBusy(true)
    try {
      const r = await api.post<{ steps?: Record<string, { ok: boolean; changed?: boolean }> }>(`/api/update/${ticker}?force=${force ? 1 : 0}`)
      if (current.current !== ticker) return
      const p = r.steps?.prices
      setNote(p && !p.ok ? 'Could not update prices (Yahoo did not answer). Showing the stored data.' : '')
      await qc.invalidateQueries({ queryKey: p?.changed || force ? ['company', ticker] : ['company', ticker, 'etf', 'live'] })
    } catch (e) {
      if (force) setNote('Refresh failed: ' + (e instanceof Error ? e.message : 'unknown error'))
    } finally {
      setBusy(false)
    }
  }, [qc])
  const hasPrices = !!stored.data?.has_prices
  useEffect(() => {
    setNote('')
    if (hasPrices && (!autoChecked[t] || Date.now() - autoChecked[t] > 5 * 60_000)) {
      autoChecked[t] = Date.now()
      void refresh(t, false)
    }
  }, [t, hasPrices, refresh])
  const [, tick] = useState(0)
  useEffect(() => { const i = window.setInterval(() => tick((n) => n + 1), 15_000); return () => window.clearInterval(i) }, [])

  if (stored.isError || (stored.data && !stored.data.profile && !stored.data.has_prices))
    return (
      <main className="p-6">
        <Card className="flex flex-col items-center gap-3 px-6 py-14 text-center">
          <h2 className="text-base font-semibold">{t} is not in your database</h2>
          <p className="max-w-md text-muted-foreground">Load it first: it takes about 20 seconds.</p>
          <Button asChild variant="default"><Link to={`/load/${t}`}><Plus /> Add {t}</Link></Button>
        </Card>
      </main>
    )
  if (!info) return <main className="p-6 text-muted-foreground">Loading {t}…</main>
  const p = info.profile
  // a bare /etf/SPY (search, lists, links) opens on the Overview tab, so the tab is underlined
  if (params.tab !== tab || (tab === 'holdings' && !p?.holdings_count)) return <Navigate to={`/etf/${t}/overview`} replace />
  const mins = live.dataUpdatedAt && live.data?.quote ? Math.floor((Date.now() - live.dataUpdatedAt) / 60000) : null
  const abs = info.price && has(info.change) ? info.price - info.price / (1 + info.change) : null
  const reg = info.quote?.regular
  const ext = settings.show_extended ? info.quote?.extended : null
  const when = reg?.time ? (info.price_live ? `Live ${etTime(reg.time)}` : `Close ${etDay(reg.time)}, 4:00 PM ET`) : info.price_date ? `Close ${day(info.price_date)}` : ''

  return (
    <main className={cn('flex flex-col gap-5 p-6', settings.page_width === 'centered' && 'mx-auto w-full max-w-[1180px]')}>
      <header className="flex flex-wrap items-start gap-x-4 gap-y-3">
        <Logo
          t={t}
          size={44}
          fallback={<span aria-hidden className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-accent text-[13px] font-bold text-accent-foreground">{t.slice(0, 4)}</span>}
        />
        <div className="min-w-0">
          <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
            <h1 className="text-[22px] leading-7 font-bold tracking-tight">{p?.name ?? t}</h1>
            <span className="self-center rounded bg-secondary px-1.5 py-0.5 text-[11px] font-semibold text-muted-foreground">ETF</span>
            <span className="text-muted-foreground">{[t, p?.family, p?.category].filter(Boolean).join(' · ')}</span>
          </div>
          {info.price ? (
            <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-1">
              <span className="num text-[26px] leading-8 font-bold tracking-tight">{money(info.price)}</span>
              <ChangePill value={info.change}>{abs !== null ? `${abs >= 0 ? '+' : '−'}${Math.abs(abs).toFixed(2)} (${pctSigned(info.change).replace(/^[+−]/, '')})` : pctSigned(info.change)}</ChangePill>
              <span className="text-xs text-muted-foreground">USD · {when}</span>
            </div>
          ) : null}
          {info.price && ext ? <ExtendedPrice ext={ext} /> : null}
        </div>
        <div className="grow" />
        <div className="flex flex-col items-end gap-1.5">
          <div className="flex items-center gap-2">
            {mins !== null && <span className="text-xs text-muted-foreground">{mins < 1 ? 'Updated just now' : mins < 60 ? `Updated ${mins}m ago` : `Updated ${Math.floor(mins / 60)}h ago`}</span>}
            <Hint label="Refresh: live price and prices"><Button size="icon" aria-label="Refresh" disabled={busy} onClick={() => void refresh(t, true)}><RefreshCw className={cn(busy && 'animate-spin')} /></Button></Hint>
            <WatchStar t={t} withLabel />
            <AlertBell sid={t} etf />
            <Button asChild><Link to={`/compare?t=${t}&v=price`}><ChartLine /> Compare</Link></Button>
          </div>
          {note && <span className="max-w-xs text-right text-xs text-muted-foreground">{note}</span>}
        </div>
      </header>
      <TabLinks label="Sections of this ETF" tabs={[{ to: `/etf/${t}/overview`, label: 'Overview' }, ...(p?.holdings_count ? [{ to: `/etf/${t}/holdings`, label: 'Holdings' }] : [])]} />
      {tab === 'overview' ? <EtfOverview t={t} info={info} /> : <EtfHoldings t={t} />}
    </main>
  )
}

function PriceCard({ t }: { t: string }) {
  const c = useChartColors()
  const { settings, pref, setPref } = useSettings()
  const range = pref('ui2', 'price_range', settings.price_range) as Range
  const mode = pref<string>('ui2', 'etf_mode', 'price')
  const total = mode === 'total'
  // "Compare with": the S&P 500 or the Nasdaq 100 on the same chart (same choice as on a stock page); never against itself
  const vsPref = pref('ui2', 'price_vs', 'none') as 'none' | 'SPY' | 'QQQ'
  const vs = vsPref === t ? 'none' : vsPref
  const candles = settings.chart_style === 'candles' && !total && vs === 'none'
  const [hover, setHover] = useState(false)
  const px = useQuery({ queryKey: ['company', t, 'prices', candles ? 'ohlc' : 'close'], queryFn: () => api.get<Prices>(`/api/prices/${t}${candles ? '?ohlc=1' : ''}`) })
  const vsPx = useQuery({ queryKey: ['company', vs, 'prices', 'close'], queryFn: () => api.get<Prices>(`/api/prices/${vs}`), enabled: vs !== 'none', retry: false })
  const view = useMemo(() => {
    const d = px.data
    if (!d?.dates.length) return null
    const s = since(range, d.dates[d.dates.length - 1])
    let i0 = 0
    while (i0 + 1 < d.dates.length && d.dates[i0 + 1] <= s) i0++
    if (range === 'Max') i0 = 0
    const src = total ? d.adj.map((v, i) => v ?? d.close[i]) : d.close
    const dates = d.dates.slice(i0)
    const vals = src.slice(i0)
    const ohlc = candles && d.open && d.high && d.low
      ? vals.map((cl, k) => {
          const j = i0 + k
          const op = d.open![j] ?? cl
          return [op, cl, d.low![j] ?? Math.min(op, cl), d.high![j] ?? Math.max(op, cl)]
        })
      : null
    return { dates, vals, ohlc, change: vals[vals.length - 1] / vals[0] - 1 }
  }, [px.data, range, total, candles])
  const option = useMemo(() => {
    if (!view) return null
    const o = baseOption(c)
    const other = vs !== 'none' ? vsPx.data : undefined
    if (other?.dates.length) {
      // both lines from 0% on the first day of the range, so you see which did better
      const osrc = total ? other.adj.map((v, i) => v ?? other.close[i]) : other.close
      const at = new Map(other.dates.map((d, i) => [d, osrc[i]]))
      let base: number | undefined
      const theirs = view.dates.map((d) => {
        const v = at.get(d)
        if (v === undefined || v === null) return null
        base ??= v
        return v / base - 1
      })
      const mine = view.vals.map((v) => v / view.vals[0] - 1)
      const end = (data: (number | null)[], color: string, name: string) => ({
        type: 'line', name, data, showSymbol: false, connectNulls: true, lineStyle: { width: name === t ? 1.9 : 1.5, color }, itemStyle: { color },
        endLabel: { show: true, formatter: () => `${name} ${pctSigned(data.filter((x): x is number => x !== null).pop() ?? 0)}`, color: '#fff', backgroundColor: color, padding: [3, 6], borderRadius: 4, fontSize: 11, fontWeight: 600 },
      })
      return {
        ...o,
        grid: { left: 8, right: 96, top: 12, bottom: 4, containLabel: true },
        xAxis: { ...o.xAxis, type: 'category', data: view.dates, boundaryGap: false, axisLabel: { ...o.xAxis.axisLabel, hideOverlap: true, ...dateAxis(view.dates) } },
        yAxis: { ...o.yAxis, position: 'right', axisLabel: { ...o.yAxis.axisLabel, formatter: (x: number) => pctSigned(x, 0) } },
        tooltip: { ...o.tooltip, valueFormatter: (x: number) => pctSigned(x) },
        series: [end(mine, c.primary, t), end(theirs, c.muted, vs)],
      }
    }
    const col = view.change >= 0 ? c.up : c.down
    const data = total ? view.vals.map((v) => (v / view.vals[0] - 1) * 100) : view.vals
    return {
      ...o,
      grid: { left: 8, right: 8, top: 12, bottom: 4, containLabel: true },
      xAxis: { ...o.xAxis, type: 'category', data: view.dates, boundaryGap: !!view.ohlc, axisLabel: { ...o.xAxis.axisLabel, hideOverlap: true, ...dateAxis(view.dates) } },
      yAxis: { ...o.yAxis, position: 'right', scale: !!view.ohlc, axisLabel: { ...o.yAxis.axisLabel, formatter: total ? (v: number) => `${v}%` : undefined } },
      tooltip: view.ohlc
        ? { ...o.tooltip, formatter: (p: { dataIndex: number }[]) => { const d = view.ohlc![p[0].dataIndex]; return `${view.dates[p[0].dataIndex]}<br>Open <b>$${d[0].toFixed(2)}</b> · Close <b>$${d[1].toFixed(2)}</b><br>Low <b>$${d[2].toFixed(2)}</b> · High <b>$${d[3].toFixed(2)}</b>` } }
        : { ...o.tooltip, valueFormatter: (x: number) => (total ? `${x > 0 ? '+' : ''}${x.toFixed(2)}%` : `$${x.toFixed(2)}`) },
      series: [view.ohlc
        ? { type: 'candlestick', data: view.ohlc, barMaxWidth: 10, itemStyle: { color: c.up, color0: c.down, borderColor: c.up, borderColor0: c.down } }
        : { type: 'line', data, showSymbol: false, lineStyle: { width: 1.6, color: col }, itemStyle: { color: col }, areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: col + '33' }, { offset: 1, color: col + '00' }]) } }],
    }
  }, [view, c, total, vs, vsPx.data, t])
  return (
    <Card className="flex min-w-0 flex-col p-4">
      <div className="mb-2 flex flex-wrap items-center gap-2" onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
        <Segmented label="Range of the price chart" value={range} onChange={(v) => setPref('ui2', 'price_range', v)} options={RANGES.map(([k]) => ({ value: k, label: k }))} />
        {view && (settings.range_change === 'always' || hover) && (
          <ChangePill value={view.change} arrow={false}>
            {pctSigned(view.change)} {RANGE_WORDS[range]}{total ? ' with dividends' : ''}
          </ChangePill>
        )}
        <div className="grow" />
        <Segmented label="Price or total return" value={mode} onChange={(v) => setPref('ui2', 'etf_mode', v)} options={[{ value: 'price', label: 'Price' }, { value: 'total', label: 'Total return' }]} />
        <Segmented
          label="Compare with"
          value={vs}
          onChange={(v) => setPref('ui2', 'price_vs', v)}
          options={[
            { value: 'none', label: 'Alone', hint: 'Only this fund' },
            ...(t !== 'SPY' ? [{ value: 'SPY', label: 'vs S&P 500', hint: 'Both as % change from the start of the range: this fund and the S&P 500 (SPY)' }] : []),
            ...(t !== 'QQQ' ? [{ value: 'QQQ', label: 'vs Nasdaq', hint: 'Both as % change from the start of the range: this fund and the Nasdaq 100 (QQQ)' }] : []),
          ]}
        />
        <Hint label="Compare the price with another company or fund">
          <Button asChild size="icon" aria-label="Compare the price with another company or fund">
            <Link to={`/compare?t=${t}&v=price&r=${range}`}>
              <ChartLine />
            </Link>
          </Button>
        </Hint>
      </div>
      {option ? <EChart option={option} className="h-[300px] flex-1" /> : <div className="flex h-[300px] items-center justify-center text-muted-foreground">{px.isLoading ? 'Loading…' : 'No prices'}</div>}
    </Card>
  )
}

function YearChart({ info }: { info: EtfInfo }) {
  const c = useChartColors()
  const ys = info.years.slice(-15)
  const option = useMemo(() => {
    const o = baseOption(c)
    return {
      ...o,
      grid: { left: 8, right: 8, top: 22, bottom: 4, containLabel: true },
      xAxis: { ...o.xAxis, type: 'category', data: ys.map((y) => (y.partial ? `${y.year} YTD` : String(y.year))) },
      yAxis: { ...o.yAxis, scale: false, axisLabel: { ...o.yAxis.axisLabel, formatter: (v: number) => `${v}%` } },
      tooltip: { ...o.tooltip, ...BAR_TIP, valueFormatter: (x: number) => `${x > 0 ? '+' : ''}${x.toFixed(1)}%` },
      series: [{ type: 'bar', name: 'Total return', barMaxWidth: 36, ...FOCUS_SELF, data: ys.map((y) => ({ value: +(y.total * 100).toFixed(2), itemStyle: { color: y.total >= 0 ? c.up : c.down, opacity: y.partial ? 0.5 : 1, borderRadius: [2, 2, 0, 0] } })), label: { show: ys.length <= 12, position: 'top', fontSize: 10, color: c.muted, formatter: (p: { value: number }) => `${p.value > 0 ? '+' : ''}${p.value.toFixed(0)}%` } }],
    }
  }, [ys, c])
  if (!ys.length) return <p className="text-muted-foreground">No price history.</p>
  return <EChart focusPeriod option={option} className="h-[240px]" />
}

function DivChart({ info }: { info: EtfInfo }) {
  const c = useChartColors()
  const ys = (info.dividends?.per_year ?? []).slice(-15)
  const option = useMemo(() => {
    const o = baseOption(c)
    return {
      ...o,
      grid: { left: 8, right: 8, top: 16, bottom: 4, containLabel: true },
      xAxis: { ...o.xAxis, type: 'category', data: ys.map((y) => (y.partial ? `${y.year} so far` : String(y.year))) },
      yAxis: { ...o.yAxis, scale: false, axisLabel: { ...o.yAxis.axisLabel, formatter: (v: number) => `$${v}` } },
      tooltip: { ...o.tooltip, ...BAR_TIP, valueFormatter: (x: number) => `$${x.toFixed(2)}` },
      series: [{ type: 'bar', name: 'Dividends per share', barMaxWidth: 36, ...FOCUS_SELF, data: ys.map((y) => ({ value: +y.amount.toFixed(4), itemStyle: { color: c.primary, opacity: y.partial ? 0.5 : 1, borderRadius: [2, 2, 0, 0] } })) }],
    }
  }, [ys, c])
  if (!ys.length) return <p className="text-muted-foreground">This fund pays no dividends.</p>
  return <EChart focusPeriod option={option} className="h-[200px]" />
}

function EtfOverview({ t, info }: { t: string; info: EtfInfo }) {
  const c = useChartColors()
  const nav = useNavigate()
  const p = info.profile
  const d = info.dividends
  const k = info.risk
  const yrs = p?.inception || info.first_date ? Math.floor((Date.now() - new Date((p?.inception || info.first_date) as string).getTime()) / 3.156e10) : null
  const sectors = Object.entries(p?.sectors ?? {}).filter(([, v]) => v > 0.0005).sort((a, b) => b[1] - a[1])
  const assets = Object.entries(p?.assets ?? {}).filter(([, v]) => v > 0.0005).sort((a, b) => b[1] - a[1])
  const hs = p?.holdings ?? []
  const top10 = hs.reduce((a, h) => a + (h.weight || 0), 0)
  const dd = (x: { value: number; peak: string; bottom: string } | null | undefined) => (x?.value ? <><span className="text-down">{fmt(x.value, 'pct')}</span> <span className="text-xs font-normal text-muted-foreground">({monthYear(x.peak)} – {monthYear(x.bottom)})</span></> : dash)
  return (
    <div className="flex flex-col gap-5">
      {!p && <Card className="p-3 text-[13px] text-muted-foreground">No fund details from Yahoo for {t}: only prices are shown.</Card>}
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
        <PriceCard t={t} />
        <Card className="p-4">
          <H2>Key facts</H2>
          <Kv label="Expense ratio" tip="Yearly cost of the fund, taken from its assets. 0.10% = $1 per $1,000 invested.">{fmt(p?.expense_ratio, 'pct2')}</Kv>
          <Kv label="Assets" tip="Total money invested in the fund.">{p?.total_assets ? '$' + big(p.total_assets) : '–'}</Kv>
          <Kv label="Dividend yield" tip="Dividends paid over the last 12 months divided by the price.">{fmt(d?.ttm_yield ?? p?.yield, 'pct2')}</Kv>
          <Kv label="Dividends (12M)">{d?.ttm ? money(d.ttm) : '–'}</Kv>
          <Kv label="P/E" tip="Average price / earnings of the stocks the fund holds (Yahoo).">{fmt(p?.pe, 'x')}</Kv>
          <Kv label="Beta (3Y)" tip="How much the fund moves with the market. 1 = the same as the S&P 500.">{has(p?.beta_3y) ? p.beta_3y.toFixed(2) : '–'}</Kv>
          <Kv label="52-week range">{info.low_52w ? `${money(info.low_52w)} – ${money(info.high_52w)}` : '–'}</Kv>
          <Kv label="NAV" tip="Net asset value: what the fund's holdings are worth per share.">{p?.nav ? money(p.nav) : '–'}</Kv>
          <Kv label="Avg. volume">{p?.avg_volume ? big(p.avg_volume) : '–'}</Kv>
          <Kv label="Holdings turnover" tip="Share of the portfolio replaced per year. Low = cheap and tax efficient.">{fmt(p?.turnover, 'pct')}</Kv>
          <Kv label="Inception">{p?.inception ? `${day(p.inception)}${yrs ? ` (${yrs}y)` : ''}` : info.first_date ? day(info.first_date) : '–'}</Kv>
        </Card>
      </div>
      <div className="grid gap-5 lg:grid-cols-2">
        <Card className="p-4">
          <H2>Returns</H2>
          {info.returns.length ? (
            <table className="tbl">
              <thead><tr><th>Period</th><th title="Price change only">Price</th><th title="Price change plus dividends, reinvested">Total return</th><th title="Total return per year (3 years and longer)">Per year</th></tr></thead>
              <tbody>
                {info.returns.map((r) => (
                  <tr key={r.period}>
                    <td>{r.period === 'Max' ? <>Max <span className="text-muted-foreground">(since {r.since.slice(0, 4)})</span></> : r.period}</td>
                    <td className={tone(r.price)}>{has(r.price) ? pctSigned(r.price) : '–'}</td>
                    <td className={cn('font-semibold', tone(r.total))}>{has(r.total) ? pctSigned(r.total) : '–'}</td>
                    <td className={tone(r.total_ann)}>{r.annualized && has(r.total_ann) ? pctSigned(r.total_ann) : dash}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <p className="text-muted-foreground">No price history.</p>}
        </Card>
        <Card className="p-4">
          <H2 side={<span className="text-xs text-muted-foreground">Total return, dividends reinvested</span>}>Returns per year</H2>
          <YearChart info={info} />
        </Card>
      </div>
      {p && (
        <div className="grid gap-5 lg:grid-cols-2">
          <Card className="p-4">
            <H2 side={p.holdings_count ? <Link to={`/etf/${t}/holdings`} className="inline-flex items-center gap-1 text-[13px] font-medium text-primary hover:underline">All {p.holdings_count.toLocaleString('en-US')} holdings <ArrowRight className="size-3.5" /></Link> : null}>Top 10 holdings</H2>
            {hs.length ? (
              <>
                <div className="flex flex-col">
                  {hs.map((h) => (
                    <button key={h.symbol + h.name} type="button" disabled={!h.loaded} onClick={() => nav(`/stock/${h.symbol}`)} className="grid grid-cols-[4.75rem_minmax(0,1fr)_6rem_3.5rem] items-center gap-3 rounded-md px-1 py-1.5 text-left text-[13px] enabled:hover:bg-panel">
                      <span className="flex items-center gap-2 font-semibold"><Logo t={h.symbol} size={16} keep />{h.symbol}</span>
                      <span className="truncate text-muted-foreground">{niceName(h.name)}</span>
                      <span className="h-1.5 rounded-full bg-secondary"><span className="block h-1.5 rounded-full bg-primary" style={{ width: `${(h.weight / Math.max(...hs.map((x) => x.weight || 0))) * 100}%` }} /></span>
                      <span className="num text-right">{fmt(h.weight, 'pct')}</span>
                    </button>
                  ))}
                </div>
                <p className="mt-2 text-xs text-muted-foreground">The top 10 are {fmt(top10, 'pct')} of the fund.</p>
              </>
            ) : <p className="text-muted-foreground">No holdings published.</p>}
          </Card>
          <Card className="p-4">
            <H2>Sectors</H2>
            {sectors.length ? <Bars items={sectors} color={c.primary} /> : <p className="text-muted-foreground">No sector data.</p>}
            {assets.length > 0 && (
              <div className="mt-4 flex flex-wrap gap-x-4 gap-y-1 border-t pt-3 text-[13px]">
                {assets.map(([a, v]) => <span key={a}><b className="num">{fmt(v, 'pct')}</b> <span className="text-muted-foreground">{a}</span></span>)}
              </div>
            )}
          </Card>
        </div>
      )}
      <div className="grid gap-5 lg:grid-cols-2">
        <Card className="p-4">
          <H2 side={d?.frequency ? <span className="text-xs text-muted-foreground">{d.frequency} payouts</span> : null}>Dividends per year</H2>
          <DivChart info={info} />
          {!!d?.recent.length && (
            <div className="mt-3 grid grid-cols-2 gap-x-6 border-t pt-2">
              {d.recent.slice(0, 6).map((x) => <Kv key={x.date} label={day(x.date)}>${x.amount.toFixed(4).replace(/0{1,2}$/, '')}</Kv>)}
            </div>
          )}
        </Card>
        <Card className="p-4">
          <H2>Risk</H2>
          {k ? (
            <>
              <Kv label="Volatility (1Y)" tip="How much the price swings, per year. The S&P 500 is usually around 15 to 20%.">{fmt(k.vol_1y, 'pct')}</Kv>
              <Kv label="Volatility (5Y)">{fmt(k.vol_5y, 'pct')}</Kv>
              <Kv label="Biggest drop (5Y)" tip="Largest fall from a high to a low, dividends included.">{dd(k.max_dd_5y)}</Kv>
              <Kv label="Biggest drop (ever)">{dd(k.max_dd)}</Kv>
              <Kv label="Below all-time high">{has(k.from_high) && k.from_high < -0.0005 ? <span className="text-down">{fmt(k.from_high, 'pct')}</span> : 'At the high'}</Kv>
            </>
          ) : <p className="text-muted-foreground">No price history.</p>}
          {p?.description && (
            <>
              <h2 className="mt-5 mb-2 text-[15px] font-semibold">About</h2>
              <p className="text-[13px] leading-relaxed text-muted-foreground">{p.description}</p>
            </>
          )}
        </Card>
      </div>
      <p className="text-xs text-muted-foreground">Fund details (costs, top 10, sectors) come from Yahoo Finance, updated weekly. Returns, risk and dividends are calculated from the daily prices.</p>
    </div>
  )
}

function EtfHoldings({ t }: { t: string }) {
  const nav = useNavigate()
  const c = useChartColors()
  const { pref, setPref } = useSettings()
  const stored = useQuery({ queryKey: ['company', t, 'holdings'], queryFn: () => api.get<Holdings>(`/api/etf/${t}/holdings`) })
  const live = useQuery({ queryKey: ['company', t, 'holdings', 'live'], queryFn: () => api.get<Holdings>(`/api/etf/${t}/holdings?live=1`), retry: false, staleTime: 60_000 })
  const d = live.data ?? stored.data
  const bd = pref<string>('state', 'etfBd', 'sectors')
  const [sortK, dir] = pref<string>('ui2', 'hold_sort', 'weight:desc').split(':')
  const [find, setFind] = useState('')
  const rows = useMemo(() => {
    if (!d) return []
    const f = find.toLowerCase()
    const r = f ? d.holdings.filter((h) => (h.ticker || '').toLowerCase().includes(f) || (h.name || '').toLowerCase().includes(f)) : [...d.holdings]
    const key = sortK as keyof Holding
    return r.sort((a, b) => {
      const x = a[key]
      const y = b[key]
      if (x === y) return 0
      if (x === null || x === undefined) return 1
      if (y === null || y === undefined) return -1
      const cmp = typeof x === 'string' ? x.localeCompare(y as string) : (x as number) - (y as number)
      return dir === 'desc' ? -cmp : cmp
    })
  }, [d, find, sortK, dir])
  if (!d) return <Card className="p-4 text-muted-foreground">{stored.isError ? 'Could not load the holdings.' : 'Loading holdings…'}</Card>
  const assetTop = Object.entries(d.assets ?? {}).sort((a, b) => b[1] - a[1])[0]
  const top5 = d.holdings.filter((h) => h.ticker).slice(0, 5).map((h) => `${niceName(h.name)} at ${fmt(h.weight, 'pct')}`)
  const asOf = d.as_of ? day(d.as_of) : ''
  const src = d.source === 'ssga' ? `Daily holdings file from State Street, as of ${asOf}.` : `SEC holdings report (N-PORT) of ${asOf}. Funds report to the SEC with a delay of a few months.` + (d.reweighted ? " Weights are updated with today's prices (shares × price), so they stay close to the fund's current weights." : '')
  const breakdown = Object.entries({ sectors: d.sectors, assets: d.assets, countries: d.countries }[bd] ?? {}).filter(([, v]) => v > 0.0005).sort((a, b) => b[1] - a[1]).slice(0, 14)
  const sortBy = (k: string) => setPref('ui2', 'hold_sort', k === sortK ? `${k}:${dir === 'desc' ? 'asc' : 'desc'}` : `${k}:${['rank', 'ticker', 'name'].includes(k) ? 'asc' : 'desc'}`)
  const cols: [string, string][] = [['rank', 'No.'], ['ticker', 'Symbol'], ['name', 'Name'], ['weight', 'Weight'], ['shares', 'Shares'], ['price', 'Price'], ['change', 'Today']]
  const stat = (label: string, value: ReactNode) => <div className="flex flex-col gap-0.5"><span className="text-xs text-muted-foreground">{label}</span><span className="num text-[17px] font-semibold">{value}</span></div>
  return (
    <div className="flex flex-col gap-5">
      <Card className="flex gap-3 p-4 text-[13px]">
        <Info className="mt-0.5 size-4 shrink-0 text-primary" />
        <p>{t} is {ASSET_WORD[assetTop?.[0] ?? ''] || 'an'} ETF with {d.count.toLocaleString('en-US')} holdings.{top5.length ? ` The largest are ${top5.slice(0, -1).join(', ')}${top5.length > 1 ? ' and ' : ''}${top5[top5.length - 1]}.` : ''}</p>
      </Card>
      <Card className="grid grid-cols-2 gap-4 p-4 md:grid-cols-3 xl:grid-cols-6">
        {stat('Holdings', d.count.toLocaleString('en-US'))}
        {stat('Top 10', fmt(d.top10, 'pct'))}
        {stat('Asset class', assetTop ? (assetTop[0] === 'Stocks' ? 'Equity' : assetTop[0]) : '–')}
        {stat('Category', d.category || '–')}
        {stat('Assets', d.total_assets ? '$' + big(d.total_assets) : '–')}
        {stat('P/E', has(d.pe) ? d.pe.toFixed(1) : '–')}
      </Card>
      <Card className="p-4">
        <H2 side={<Segmented label="Breakdown by" value={bd} onChange={(v) => setPref('state', 'etfBd', v)} options={[{ value: 'sectors', label: 'Sectors' }, { value: 'assets', label: 'Asset class' }, { value: 'countries', label: 'Country' }]} />}>Breakdown</H2>
        {breakdown.length ? <Bars items={breakdown} color={c.primary} unit={(v) => fmt(v, 'pct')} /> : <p className="text-muted-foreground">No data for this breakdown.</p>}
        <p className="mt-3 text-xs text-muted-foreground">{bd === 'sectors' ? (d.sectors_source === 'own' ? 'Sectors of the holdings, from the company data in your database.' : 'Sector weights from Yahoo Finance.') : "From the fund's latest holdings report."}</p>
      </Card>
      <Card className="min-w-0">
        <div className="flex flex-wrap items-center gap-3 px-4 pt-3 pb-1">
          <h2 className="text-[15px] font-semibold">All holdings</h2>
          <span className="text-[13px] text-muted-foreground">{find ? `${rows.length} of ${d.holdings.length}` : d.holdings.length}</span>
          <div className="grow" />
          <label className="relative">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input value={find} onChange={(e) => setFind(e.target.value)} placeholder="Find a holding" spellCheck={false} aria-label="Find a holding" className="h-8 w-56 pl-8" />
          </label>
        </div>
        <p className="px-4 pb-2 text-xs text-muted-foreground">{src}</p>
        <div className="overflow-x-auto">
          <table className="tbl left-2-4">
            <thead>
              <tr>
                {cols.map(([k, l]) => (
                  <th key={k} aria-sort={k === sortK ? (dir === 'asc' ? 'ascending' : 'descending') : undefined}>
                    <button type="button" onClick={() => sortBy(k)} className={cn('inline-flex items-center gap-1 hover:text-foreground', k === sortK && 'font-semibold text-foreground')}>
                      {l}
                      {k === sortK && (dir === 'asc' ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />)}
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 600).map((h) => (
                <tr key={h.rank} className={h.ticker ? 'cursor-pointer' : undefined} title={h.ticker && !h.loaded ? 'Not in your database yet: click to add' : undefined} onClick={h.ticker ? () => nav(h.loaded ? `/stock/${h.ticker}` : `/load/${h.ticker}`) : undefined}>
                  <td className="w-12 text-muted-foreground">{h.rank}</td>
                  <td className="font-semibold"><span className="inline-flex items-center gap-2"><Logo t={h.ticker} size={16} keep />{h.ticker ? <span className={h.loaded ? undefined : 'text-muted-foreground'}>{h.ticker}</span> : dash}</span></td>
                  <td className="max-w-96 truncate">{niceName(h.name)}</td>
                  <td>{fmt(h.weight, 'pct2')}</td>
                  <td>{h.shares ? Math.round(h.shares).toLocaleString('en-US') : '–'}</td>
                  <td>{h.price ? money(h.price) : '–'}</td>
                  <td className={tone(h.change)}>{has(h.change) ? pctSigned(h.change) : '–'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {rows.length > 600 && <p className="px-4 py-3 text-xs text-muted-foreground">Showing the first 600. Use the search to find the others.</p>}
      </Card>
    </div>
  )
}
