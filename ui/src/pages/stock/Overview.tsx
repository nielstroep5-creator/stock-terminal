import { useQuery } from '@tanstack/react-query'
import { ArrowRight, CalendarDays, ChartLine, CircleAlert, Flag, Info, Target, TrendingUp } from 'lucide-react'
import { useMemo, useState } from 'react'
import type React from 'react'
import type { ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Card } from '@/components/app/Page'
import { EChart, baseOption, echarts, useChartColors } from '@/components/chart/EChart'
import { Button } from '@/components/ui/button'
import { ChangePill } from '@/components/ui/pill'
import { Segmented } from '@/components/ui/segmented'
import { IconToggle } from '@/components/ui/toggle'
import { Hint } from '@/components/ui/tooltip'
import { api } from '@/lib/api'
import { big, dateAxis, day, fmt, money, niceName, pctSigned, tone } from '@/lib/format'
import { useSettings } from '@/lib/settings'
import type { Settings } from '@/lib/settings'
import { cn } from '@/lib/utils'
import type { AccuracyItem, ChartEvents, CompanyInfo, McapCheck, Peer, Peers, Prices, Row, Statements } from '@/pages/stock/types'
import { Logo } from '@/components/app/Logo'
import { NewsCard } from '@/components/app/News'

type Range = Settings['price_range']
const RANGES: [Range, number, string][] = [
  ['1M', 31, 'past month'],
  ['3M', 92, 'past 3 months'],
  ['6M', 183, 'past 6 months'],
  ['YTD', 0, 'this year'],
  ['1Y', 365, 'past year'],
  ['3Y', 1096, 'past 3 years'],
  ['5Y', 1826, 'past 5 years'],
  ['10Y', 3652, 'past 10 years'],
  ['Max', 1e6, 'all time'],
]

function sinceDate(range: Range, lastDate: string) {
  const last = new Date(lastDate)
  if (range === 'YTD') return `${last.getFullYear()}-01-01`
  const days = RANGES.find(([k]) => k === range)?.[1] ?? 1e6
  return new Date(last.getTime() - days * 864e5).toISOString().slice(0, 10)
}

/** Change over a range: last close against the last close on or before the start of the range. */
function rangeChange(px: Prices, range: Range): number | null {
  const n = px.close.length
  if (n < 2 || !px.close[n - 1]) return null
  const since = sinceDate(range, px.dates[n - 1])
  if (range !== 'Max' && px.dates[0] > since) return null
  let i = 0
  while (i + 1 < n && px.dates[i + 1] <= since) i++
  return px.close[i] && i < n - 1 ? px.close[n - 1] / px.close[i] - 1 : null
}

function H2({ children }: { children: ReactNode }) {
  return <h2 className="text-[15px] font-semibold">{children}</h2>
}

/* ------------------------------------------------------------------ price chart */
function PriceCard({ t }: { t: string }) {
  const { settings, pref, setPref } = useSettings()
  const c = useChartColors()
  const candles = settings.chart_style === 'candles'
  const px = useQuery({ queryKey: ['company', t, 'prices', candles ? 'ohlc' : 'close'], queryFn: () => api.get<Prices>(`/api/prices/${t}${candles ? '?ohlc=1' : ''}`) })
  const ev = useQuery({ queryKey: ['company', t, 'chart-events'], queryFn: () => api.get<ChartEvents>(`/api/chart-events/${t}`), retry: false })
  const range = pref('ui2', 'price_range', settings.price_range) as Range
  const showEv = pref('ui2', 'price_events', true)
  // "Compare with": the S&P 500 or the Nasdaq 100 on the same chart, both as % change from the start
  const vs = pref('ui2', 'price_vs', 'none') as 'none' | 'SPY' | 'QQQ'
  const vsPx = useQuery({ queryKey: ['company', vs, 'prices'], queryFn: () => api.get<Prices>(`/api/prices/${vs}`), enabled: vs !== 'none' && vs !== t, retry: false })
  const [hover, setHover] = useState(false)

  const change = px.data ? rangeChange(px.data, range) : null
  const hasEvents = !!ev.data && (ev.data.earnings.length > 0 || ev.data.dividends.length > 0)

  const option = useMemo(() => {
    if (!px.data?.dates.length) return null
    const all = px.data
    const since = sinceDate(range, all.dates[all.dates.length - 1])
    const i0 = Math.max(0, all.dates.findIndex((d) => d >= since))
    const dates = all.dates.slice(i0)
    const close = all.close.slice(i0)
    const col = close[close.length - 1] >= close[0] ? c.up : c.down
    const o = baseOption(c)
    const other = vs !== 'none' && vs !== t ? vsPx.data : undefined
    if (other?.dates.length) {
      // both lines from 0% on the first day of the range, so you see which did better
      const at = new Map(other.dates.map((d, i) => [d, other.close[i]]))
      let base: number | undefined
      const theirs = dates.map((d) => {
        const v = at.get(d)
        if (v === undefined) return null
        base ??= v
        return v / base - 1
      })
      const mine = close.map((v) => v / close[0] - 1)
      const fmtP = (x: number) => pctSigned(x)
      const end = (data: (number | null)[], color: string, name: string) => ({
        type: 'line', name, data, showSymbol: false, connectNulls: true, lineStyle: { width: name === t ? 1.9 : 1.5, color }, itemStyle: { color },
        endLabel: { show: true, formatter: () => `${name} ${fmtP(data.filter((x): x is number => x !== null).pop() ?? 0)}`, color: '#fff', backgroundColor: color, padding: [3, 6], borderRadius: 4, fontSize: 11, fontWeight: 600 },
      })
      return {
        ...o,
        grid: { left: 4, right: 96, top: 14, bottom: 0, containLabel: true },
        xAxis: { ...o.xAxis, type: 'category', data: dates, boundaryGap: false, axisLabel: { ...o.xAxis.axisLabel, hideOverlap: true, ...dateAxis(dates) } },
        yAxis: { ...o.yAxis, position: 'right', axisLabel: { ...o.yAxis.axisLabel, formatter: (x: number) => pctSigned(x, 0) } },
        tooltip: { ...o.tooltip, valueFormatter: (x: number) => fmtP(x) },
        series: [end(mine, c.primary, t), end(theirs, c.muted, vs)],
      }
    }

    // E on the day results were filed, D on each ex-dividend date; too many to read beyond about 5 years
    const marks: object[] = []
    if (showEv && ev.data && dates.length <= 1300) {
      const at = (d: string) => {
        let lo = 0
        let hi = dates.length
        while (lo < hi) {
          const mid = (lo + hi) >> 1
          if (dates[mid] < d) lo = mid + 1
          else hi = mid
        }
        return lo < dates.length ? lo : -1
      }
      for (const e of ev.data.earnings) {
        const i = e.date < dates[0] ? -1 : at(e.date)
        if (i < 0) continue
        marks.push({
          coord: [i, close[i]], letter: 'E', symbol: 'pin', symbolSize: 22, itemStyle: { color: c.primary },
          tip: `<b>Results filed (${e.form})</b><br>${day(e.date)} · period ending ${e.period ?? '–'}${e.eps !== null && e.eps !== undefined ? `<br>EPS ${fmt(e.eps, 'usdps')}` : ''}`,
        })
      }
      for (const d of ev.data.dividends) {
        const i = d.date < dates[0] ? -1 : at(d.date)
        if (i < 0) continue
        marks.push({
          coord: [i, close[i]], letter: 'D', symbol: 'circle', symbolSize: 13, symbolOffset: [0, 14], itemStyle: { color: c.up },
          tip: `<b>Dividend</b><br>ex-date ${day(d.date)} · ${money(d.amount)} per share`,
        })
      }
    }
    // candlesticks (a setting): open, close, low, high per day; a day without them falls back to its close
    const ohlc = candles && all.open && all.high && all.low
    const candleData = ohlc
      ? close.map((cl, k) => {
          const j = i0 + k
          const op = all.open![j] ?? cl
          return [op, cl, all.low![j] ?? Math.min(op, cl), all.high![j] ?? Math.max(op, cl)]
        })
      : null
    return {
      ...o,
      grid: { left: 4, right: 4, top: 14, bottom: 0, containLabel: true },
      xAxis: { ...o.xAxis, type: 'category', data: dates, boundaryGap: !!candleData, axisLabel: { ...o.xAxis.axisLabel, hideOverlap: true, ...dateAxis(dates) } },
      yAxis: { ...o.yAxis, position: 'right', scale: true },
      tooltip: candleData
        ? { ...o.tooltip, formatter: (p: { dataIndex: number }[]) => { const d = candleData[p[0].dataIndex]; return `${day(dates[p[0].dataIndex])}<br>Open <b>${money(d[0])}</b> · Close <b>${money(d[1])}</b><br>Low <b>${money(d[2])}</b> · High <b>${money(d[3])}</b>` } }
        : { ...o.tooltip, valueFormatter: (x: number) => money(x) },
      series: [
        candleData ? {
          type: 'candlestick', data: candleData, barMaxWidth: 10,
          itemStyle: { color: c.up, color0: c.down, borderColor: c.up, borderColor0: c.down },
          markPoint: {
            data: marks.map((mk) => ({ ...mk, coord: [(mk as { coord: number[] }).coord[0], candleData[(mk as { coord: number[] }).coord[0]][3]] })), animation: false,
            label: { show: true, fontSize: 9, fontWeight: 600, color: '#fff', formatter: (p: { data: { letter: string } }) => p.data.letter },
            tooltip: { show: true, trigger: 'item', formatter: (p: { data: { tip: string } }) => p.data.tip },
          },
        } : {
          type: 'line', data: close, showSymbol: false, lineStyle: { width: 1.7, color: col }, itemStyle: { color: col },
          areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: col + '30' }, { offset: 1, color: col + '00' }]) },
          markPoint: {
            data: marks, animation: false,
            label: { show: true, fontSize: 9, fontWeight: 600, color: '#fff', formatter: (p: { data: { letter: string } }) => p.data.letter },
            tooltip: { show: true, trigger: 'item', formatter: (p: { data: { tip: string } }) => p.data.tip },
          },
        },
      ],
    }
  }, [px.data, ev.data, range, showEv, c, vs, vsPx.data, t, candles])

  return (
    <Card className="flex min-w-0 flex-col gap-3 p-4" >
      <div className="flex flex-wrap items-center gap-2" onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
        <Segmented label="Range of the price chart" value={range} onChange={(v) => setPref('ui2', 'price_range', v)} options={RANGES.map(([k]) => ({ value: k, label: k }))} />
        {(settings.range_change === 'always' || hover) && (
          <ChangePill value={change} arrow={false}>
            {pctSigned(change)} {RANGES.find(([k]) => k === range)?.[2]}
          </ChangePill>
        )}
        <div className="grow" />
        <Segmented
          label="Compare with"
          value={vs}
          onChange={(v) => setPref('ui2', 'price_vs', v)}
          options={[
            { value: 'none', label: 'Price', hint: 'The share price' },
            { value: 'SPY', label: 'vs S&P 500', hint: 'Both as % change from the start of the range: this company and the S&P 500 (SPY)' },
            { value: 'QQQ', label: 'vs Nasdaq', hint: 'Both as % change from the start of the range: this company and the Nasdaq 100 (QQQ)' },
          ]}
        />
        {hasEvents && vs === 'none' && (
          <IconToggle on={showEv} onChange={(v) => setPref('ui2', 'price_events', v)} label="Earnings (E) and dividends (D) on the chart" text="Events">
            <Flag />
          </IconToggle>
        )}
        <Hint label="Compare the price with another company">
          <Button asChild size="icon" aria-label="Compare the price with another company">
            <Link to={`/compare?t=${t}&v=price&r=${range}`}>
              <ChartLine />
            </Link>
          </Button>
        </Hint>
      </div>
      {option ? <EChart option={option} className="h-auto min-h-[320px] flex-1" /> : <div className="flex h-[320px] items-center justify-center text-muted-foreground">{px.isLoading ? 'Loading prices…' : 'No prices for this company.'}</div>}
    </Card>
  )
}

/* ------------------------------------------------------------------ key numbers */
type Stat = [label: ReactNode, value: ReactNode]

function mcapWarnText(mc: McapCheck) {
  if (!mc) return ''
  const parts: string[] = []
  if (mc.text) parts.push(mc.text)
  const j = mc.jumps ?? []
  if (j.length) {
    const days = j.reduce((a, x) => a + x.days, 0)
    parts.push(`${days} day${days > 1 ? 's' : ''} with an impossible market cap (${j.slice(0, 3).map((x) => x.from + (x.to !== x.from ? ' to ' + x.to : '')).join(', ')}${j.length > 3 ? ', …' : ''}).`)
  }
  return parts.join(' ')
}
const mcapIsInfo = (mc: McapCheck) => !!mc && mc.reason === 'yahoo' && !(mc.jumps ?? []).length

function KeyNumbers({ info }: { info: CompanyInfo }) {
  const c = info.company
  const v = info.valuation ?? {}
  const ttm = info.ttm ?? {}
  const m = info.metrics ?? {}
  const b = info.balance ?? {}
  const a = info.analyst
  const col = (x: number | null | undefined, text: string) => <span className={tone(x)}>{text}</span>
  const warn = mcapWarnText(info.mcap_check ?? null)
  const mcap: Stat = [
    'Market cap',
    <span className="inline-flex items-center gap-1" key="m">
      {v.market_cap ? big(v.market_cap) : '–'}
      {info.valuation_stale && (
        <Hint label={`No current share count since ${info.valuation_stale}: market cap and the multiples are not shown rather than years-old numbers.`}>
          <Info className="size-3.5 text-muted-foreground" />
        </Hint>
      )}
      {warn && (
        <Hint label={warn + (info.mcap_check?.yahoo && info.mcap_check.reason !== 'yahoo' ? ` Yahoo: ${big(info.mcap_check.yahoo)}` : '')}>
          {mcapIsInfo(info.mcap_check ?? null) ? <Info className="size-3.5 text-muted-foreground" /> : <CircleAlert className="size-3.5 text-flag" />}
        </Hint>
      )}
    </span>,
  ]
  const divYield: Stat = [
    <Hint key="dy" label={`Forward: the latest regular dividend x payments a year / price.${v.dividend_yield_ttm ? ` Last 12 months: ${fmt(v.dividend_yield_ttm, 'pct2')}.` : ''}`}>
      <span className="underline decoration-dotted underline-offset-2">Dividend yield (fwd)</span>
    </Hint>,
    v.dividend_yield === 0 || v.dividend_yield == null ? <span className="text-muted-foreground">No dividend</span> : fmt(v.dividend_yield, 'pct2'),
  ]
  const beta: Stat = ['Beta', c.beta ? c.beta.toFixed(2) : '–']
  const fwd: Stat[] = a ? [['Forward P/E', fmt(a.forward_pe, 'x')], ['PEG ratio', fmt(a.peg, 'x', { noX: true })]] : []
  const estGrowth: Stat[] = a ? [[`EPS growth ${a.next_fy ?? 'next year'} (est.)`, col(a.eps_growth_next, fmt(a.eps_growth_next, 'pct'))]] : []
  const revGrowth: Stat = ['Revenue growth (TTM)', col(m.revenue_yoy, fmt(m.revenue_yoy, 'pct'))]
  const common: Stat[] = [['Net income (TTM)', ttm.net_income ? big(ttm.net_income) : '–'], ['EPS (TTM)', fmt(ttm.eps_diluted, 'usdps')]]

  let groups: [string, Stat[]][]
  if (info.is_bank) {
    const ptbv = v.close && (m.tangible_book_value_per_share ?? 0) > 0 ? v.close / (m.tangible_book_value_per_share as number) : null
    groups = [
      ['Valuation', [mcap, ['P/E (TTM)', fmt(v.pe_ttm, 'x')], ...fwd, ['P/B', fmt(v.pb, 'x')], ['P/TBV', fmt(ptbv, 'x')], ['P/S (TTM)', fmt(v.ps_ttm, 'x')]]],
      ['Profitability', [['Revenue (TTM)', ttm.revenue ? big(ttm.revenue) : '–'], ['Net interest income (TTM)', ttm.net_interest_income ? big(ttm.net_interest_income) : '–'], ...common, ['ROE / ROTCE', `${fmt(m.roe, 'pct')} / ${fmt(m.rotce, 'pct')}`], ['Efficiency ratio', fmt(m.efficiency_ratio, 'pct')]]],
      ['Growth', [revGrowth, ...estGrowth]],
      ['Balance and dividend', [['Deposits', b.deposits ? big(b.deposits) : '–'], ['Loans / deposits', fmt(m.loans_to_deposits, 'pct')], divYield, beta]],
    ]
  } else if (info.is_insurer) {
    groups = [
      ['Valuation', [mcap, ['P/E (TTM)', fmt(v.pe_ttm, 'x')], ...fwd, ['P/B', fmt(v.pb, 'x')], ['P/S (TTM)', fmt(v.ps_ttm, 'x')], ['Book value per share', fmt(m.book_value_per_share, 'usdps')]]],
      ['Profitability', [['Revenue (TTM)', ttm.revenue ? big(ttm.revenue) : '–'], ['Premiums earned (TTM)', ttm.premiums_earned ? big(ttm.premiums_earned) : '–'], ['Investment income (TTM)', ttm.net_investment_income ? big(ttm.net_investment_income) : '–'], ...common, ['ROE', fmt(m.roe, 'pct')],
        // the loss ratio only means something for property & casualty insurers (SEC industry 633x / 635x)
        ...(/^63[35]/.test(String(c.sic ?? '')) ? [[<Hint key="l" label="Claims and losses divided by premiums earned. Lower is better."><span className="underline decoration-dotted underline-offset-2">Loss ratio</span></Hint>, fmt(m.loss_ratio, 'pct')] as Stat] : [])]],
      ['Growth', [revGrowth, ['Premium growth (TTM)', col(m.premiums_earned_yoy, fmt(m.premiums_earned_yoy, 'pct'))], ...estGrowth]],
      ['Balance and dividend', [divYield, beta]],
    ]
  } else {
    groups = [
      ['Valuation', [mcap, ['Enterprise value', v.enterprise_value ? big(v.enterprise_value) : '–'], ['P/E (TTM)', fmt(v.pe_ttm, 'x')], ...fwd, ['P/S (TTM)', fmt(v.ps_ttm, 'x')], ['P/FCF (TTM)', fmt(v.pfcf_ttm, 'x')], ['EV/EBITDA', fmt(v.ev_ebitda_ttm, 'x')], ['P/B', fmt(v.pb, 'x')]]],
      ['Profitability', [['Revenue (TTM)', ttm.revenue ? big(ttm.revenue) : '–'], ...common, ['Free cash flow (TTM)', ttm.fcf ? big(ttm.fcf) : '–'], ['Gross margin', fmt(m.gross_margin, 'pct')], ['Operating margin', fmt(m.operating_margin, 'pct')], ['ROIC', fmt(m.roic, 'pct')]]],
      ['Growth', [revGrowth, ['EPS growth (TTM)', col(m.eps_diluted_yoy, fmt(m.eps_diluted_yoy, 'pct'))], ...estGrowth]],
      ['Balance and dividend', [['Net cash', m.net_cash !== undefined && m.net_cash !== null ? big(m.net_cash) : '–'], divYield, beta]],
    ]
  }

  return (
    <Card className="grid grid-cols-1 content-start gap-x-8 gap-y-5 p-4 sm:grid-cols-2">
      {groups.map(([title, items]) => (
        <div key={title}>
          <div className="mb-1 text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">{title}</div>
          {items.map(([k, val], i) => (
            <div key={i} className="flex items-baseline justify-between gap-3 border-b border-dashed py-[5px] last:border-0">
              <span className="text-muted-foreground">{k}</span>
              <span className="num font-medium whitespace-nowrap">{val}</span>
            </div>
          ))}
        </div>
      ))}
    </Card>
  )
}

/* ------------------------------------------------------------------ tiles and highlights (fiscal years) */
function MiniBars({ values, className }: { values: (number | null)[]; className?: string }) {
  const max = Math.max(...values.map((x) => Math.abs(x ?? 0)), 1e-9)
  return (
    <span aria-hidden className={cn('inline-flex h-7 items-end gap-[3px]', className)}>
      {values.map((x, i) => (
        <span
          key={i}
          className={cn('w-[7px] rounded-[2px]', x !== null && x < 0 ? 'bg-down/70' : i === values.length - 1 ? 'bg-primary' : 'bg-primary/35')}
          style={{ height: `${Math.max(8, (Math.abs(x ?? 0) / max) * 100)}%` }}
        />
      ))}
    </span>
  )
}

const TILE_ROWS = ['revenue', 'net_income', 'fcf', 'eps_diluted']
const HIGHLIGHT_ROWS = ['revenue', 'gross_profit', 'operating_income', 'net_income', 'eps_diluted', 'fcf', 'net_interest_income', 'premiums_earned']

function series(st: Statements, row: Row, n: number) {
  const ps = st.periods.slice(-n)
  return { periods: ps, values: ps.map((p) => row.values[p.key]?.v ?? null) }
}

function Tiles({ st }: { st: Statements }) {
  const rows = TILE_ROWS.map((k) => st.rows.find((r) => r.key === k)).filter((r): r is Row => !!r && series(st, r, 1).values[0] !== null)
  if (!rows.length) return null
  return (
    <div className="grid grid-cols-2 gap-4 xl:grid-cols-[repeat(var(--n),minmax(0,1fr))]" style={{ '--n': rows.length } as React.CSSProperties}>
      {rows.map((r) => {
        const { periods, values } = series(st, r, 8)
        const last = values[values.length - 1]
        const prev = values[values.length - 2]
        const g = last !== null && prev !== null && prev !== undefined && prev > 0 ? last / prev - 1 : null
        return (
          <Card key={r.key} className="flex items-end justify-between gap-3 p-4">
            <div className="min-w-0">
              <div className="truncate text-xs text-muted-foreground">
                {r.label} · {periods[periods.length - 1].label}
              </div>
              <div className="num mt-0.5 text-xl font-bold tracking-tight">{r.fmt === 'usdps' ? money(last) : '$' + big(last)}</div>
              <div className={cn('num text-xs font-medium', tone(g))}>{g === null ? ' ' : `${pctSigned(g, 1)} vs. ${periods[periods.length - 2]?.label ?? 'a year earlier'}`}</div>
            </div>
            <Hint label={`${r.label}, last ${values.length} fiscal years`}>
              <span>
                <MiniBars values={values} className="h-10" />
              </span>
            </Hint>
          </Card>
        )
      })}
    </div>
  )
}

function Highlights({ t, st }: { t: string; st: Statements }) {
  const rows = HIGHLIGHT_ROWS.map((k) => st.rows.find((r) => r.key === k)).filter((r): r is Row => !!r && Object.values(r.values).some((c) => c && c.v !== null))
  const periods = st.periods.slice(-5)
  if (!rows.length || !periods.length) return null
  const billions = rows.some((r) => r.fmt === 'usd' && Math.abs(r.values[periods[periods.length - 1].key]?.v ?? 0) >= 1e10)
  return (
    <Card className="min-w-0">
      <div className="flex items-center gap-3 border-b px-4 py-3">
        <H2>Financial highlights</H2>
        <div className="grow" />
        <Link to={`/stock/${t}/financials`} className="inline-flex items-center gap-1 text-[13px] font-medium text-primary hover:underline">
          All financials <ArrowRight className="size-3.5" />
        </Link>
      </div>
      <div className="overflow-x-auto">
        <table className="tbl">
          <thead>
            <tr>
              <th>In {st.currency && st.currency !== 'USD' ? st.currency : '$'} {billions ? 'billions' : 'millions'}, except per share</th>
              {periods.map((p) => (
                <th key={p.key}>{p.label}</th>
              ))}
              <th>{periods.length}-year trend</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key}>
                <td className={cn(r.bold && 'font-semibold')}>{r.label}</td>
                {periods.map((p) => (
                  <td key={p.key}>{fmt(r.values[p.key]?.v, r.fmt, { unit: billions ? 'B' : 'M' })}</td>
                ))}
                <td>
                  <MiniBars values={periods.map((p) => r.values[p.key]?.v ?? null)} className="h-5" />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

/* ------------------------------------------------------------------ analysts */
function ratingText(mean: number) {
  return mean < 1.5 ? 'Strong buy' : mean < 2.5 ? 'Buy' : mean < 3.5 ? 'Hold' : mean < 4.5 ? 'Sell' : 'Strong sell'
}

function AnalystCard({ t, info }: { t: string; info: CompanyInfo }) {
  const a = info.analyst
  if (!a) return null
  const line = (icon: ReactNode, label: string, value: ReactNode) => (
    <div className="flex items-center gap-2 border-t py-2.5 first:border-0">
      <span className="text-muted-foreground [&_svg]:size-4">{icon}</span>
      <span className="text-muted-foreground">{label}</span>
      <span className="grow" />
      <span className="num font-medium">{value}</span>
    </div>
  )
  return (
    <Card className="flex flex-col">
      <div className="flex items-center gap-3 border-b px-4 py-3">
        <H2>Analysts</H2>
        <div className="grow" />
        <Link to={`/stock/${t}/estimates`} className="inline-flex items-center gap-1 text-[13px] font-medium text-primary hover:underline">
          Estimates <ArrowRight className="size-3.5" />
        </Link>
      </div>
      <div className="grid grid-cols-2 gap-4 px-4 pt-4">
        <div>
          <div className="text-xs text-muted-foreground">Consensus</div>
          <div className={cn('text-lg font-bold', a.rating_mean ? (a.rating_mean < 2.5 ? 'text-up' : a.rating_mean < 3.5 ? '' : 'text-down') : '')}>{a.rating_mean ? ratingText(a.rating_mean) : '–'}</div>
          <div className="text-xs text-muted-foreground">{a.analysts ? `${a.analysts} analysts` : ''}</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">Price target</div>
          <div className="num text-lg font-bold">{a.target_mean ? money(a.target_mean) : '–'}</div>
          <div className={cn('num text-xs font-medium', tone(a.target_upside))}>{a.target_mean ? `${pctSigned(a.target_upside, 1)} from today` : ''}</div>
        </div>
      </div>
      <div className="px-4 pt-2 pb-1">
        {line(<CalendarDays />, 'Next earnings', a.earnings_upcoming ? day(a.earnings_date) : <span className="text-muted-foreground">Not announced</span>)}
        {line(<Target />, 'Forward P/E', fmt(a.forward_pe, 'x'))}
        {line(<TrendingUp />, `EPS growth ${a.next_fy ?? 'next year'} (est.)`, <span className={tone(a.eps_growth_next)}>{fmt(a.eps_growth_next, 'pct')}</span>)}
      </div>
    </Card>
  )
}

/* ------------------------------------------------------------------ about, profile, peers, data quality */
function About({ info }: { info: CompanyInfo }) {
  const { pref, setPref } = useSettings()
  const open = pref('ui2', 'about_open', false)
  const c = info.company
  const text = c.description || 'No description available.'
  const b = info.balance ?? {}
  const fye = c.fiscal_year_end ? new Date(2000, +c.fiscal_year_end.slice(0, 2) - 1, +c.fiscal_year_end.slice(2)).toLocaleString('en-US', { month: 'long', day: 'numeric' }) : '–'
  const facts: [string, ReactNode][] = [
    ['Sector', c.sector || '–'],
    ['Industry', c.industry || '–'],
    ['Employees', c.employees ? c.employees.toLocaleString('en-US') : '–'],
    ['Fiscal year ends', fye],
    ['Shares outstanding', b.shares_outstanding ? big(b.shares_outstanding) : '–'],
    ['Financial data', info.fy_range?.a ? `FY${info.fy_range.a} – FY${info.fy_range.b}` : '–'],
    ['Website', c.website ? <a key="w" href={c.website} target="_blank" rel="noreferrer" className="text-primary hover:underline">{c.website.replace(/^https?:\/\//, '')}</a> : '–'],
    ['SEC CIK', c.cik || '–'],
  ]
  return (
    <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
      <Card className="p-4">
        <H2>About {niceName(c.name) || c.ticker}</H2>
        <p className={cn('mt-2 leading-relaxed text-muted-foreground', !open && 'line-clamp-4')}>{text}</p>
        {text.length > 420 && (
          <button type="button" className="mt-2 text-[13px] font-medium text-primary hover:underline" onClick={() => setPref('ui2', 'about_open', !open)}>
            {open ? 'Show less' : 'Show more'}
          </button>
        )}
      </Card>
      <Card className="p-4">
        <H2>Profile</H2>
        <div className="mt-2">
          {facts.map(([k, val]) => (
            <div key={k} className="flex items-baseline justify-between gap-3 border-b border-dashed py-[5px] last:border-0">
              <span className="text-muted-foreground">{k}</span>
              <span className="num text-right font-medium">{val}</span>
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}

function PeersCard({ t }: { t: string }) {
  const nav = useNavigate()
  const q = useQuery({ queryKey: ['company', t, 'peers'], queryFn: () => api.get<Peers>(`/api/peers/${t}`), retry: false })
  const d = q.data
  if (!d?.peers.length) return null
  const all = [d.me, ...d.peers]
  // Banks and insurers have no operating margin: return on equity and price/book say more about them.
  const fin = d.me.kind === 'bank' || d.me.kind === 'insurer'
  type Col = { key: keyof Peer; label: string; kind: 'pct' | 'x'; better: 'high' | 'low'; color?: boolean }
  const cols: Col[] = [
    { key: 'pe', label: 'P/E (TTM)', kind: 'x', better: 'low' },
    ...(fin ? [{ key: 'pb', label: 'P/B', kind: 'x', better: 'low' } as Col] : []),
    { key: 'revenue_yoy', label: 'Revenue growth', kind: 'pct', better: 'high', color: true },
    fin ? { key: 'roe', label: 'ROE', kind: 'pct', better: 'high' } : { key: 'operating_margin', label: 'Operating margin', kind: 'pct', better: 'high' },
    { key: 'r_1y', label: 'Return 1Y', kind: 'pct', better: 'high', color: true },
  ]
  // Where the company ranks among these peers ("#2 of 6"), for the idea "peer ranks".
  const rank = (c: Col) => {
    const ok = all.map((r) => r[c.key] as number | null | undefined).filter((x): x is number => typeof x === 'number' && (c.better === 'high' || x > 0))
    const mine = d.me[c.key] as number | null | undefined
    if (typeof mine !== 'number' || ok.length < 3 || (c.better === 'low' && mine <= 0)) return null
    return 1 + ok.filter((x) => (c.better === 'high' ? x > mine : x < mine)).length
  }
  const ranks = cols.map(rank)
  const best = cols.map((c, i) => (ranks[i] === 1 ? c.label.replace(' (TTM)', '').toLowerCase() : null)).filter(Boolean)
  return (
    <Card className="min-w-0">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b px-4 py-3">
        <H2>Similar companies</H2>
        <span className="text-muted-foreground">
          {d.by === 'industry' ? 'Same industry' : 'Same sector'}: {d.group}, closest in size
        </span>
        <div className="grow" />
        <Button asChild size="sm">
          <Link to={`/compare?t=${all.map((r) => r.ticker).join(',')}`}>
            <ChartLine />
            Compare all
          </Link>
        </Button>
      </div>
      <div className="overflow-x-auto">
        <table className="tbl">
          <thead>
            <tr>
              <th>Company</th>
              <th>Market cap</th>
              {cols.map((c) => (
                <th key={c.key}>{c.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {all.map((r, i) => (
              <tr key={r.ticker} className={cn(i === 0 ? 'font-semibold' : 'cursor-pointer')} onClick={i === 0 ? undefined : () => nav(`/stock/${r.ticker}/overview`)}>
                <td>
                  <span className="inline-flex items-center gap-2 align-middle"><Logo t={r.ticker} size={18} keep /><span className="font-semibold">{r.ticker}</span></span> <span className="font-normal text-muted-foreground">{niceName(r.name)}</span>
                </td>
                <td>{r.market_cap ? big(r.market_cap) : '–'}</td>
                {cols.map((c, k) => (
                  <td key={c.key} className={c.color ? tone(r[c.key] as number | null) : undefined}>
                    {fmt(r[c.key] as number | null, c.kind)}
                    {i === 0 && ranks[k] && (
                      <span className="ml-1.5 text-[11px] font-normal text-muted-foreground" title={`Rank among these ${all.length} companies (${c.better === 'high' ? 'higher' : 'lower'} is better)`}>
                        #{ranks[k]}
                      </span>
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {best.length > 0 && (
        <p className="border-t px-4 py-2 text-[13px] text-muted-foreground">
          Best of these {all.length} on {best.join(', ')}.
        </p>
      )}
    </Card>
  )
}

type ValuationData = { dates: string[]; series: Record<string, (number | null)[]> }
const BAND_FIELDS: [string, string][] = [['pe_ttm', 'P/E'], ['p_ffo_ttm', 'P/FFO'], ['ps_ttm', 'P/S'], ['pfcf_ttm', 'P/FCF'], ['ev_ebitda_ttm', 'EV/EBITDA'], ['pb', 'P/B']]

/** Where today's multiples sit in their own 5-year range: "P/E 24x, range 18x to 35x, higher than 62% of days". */
function band(d: ValuationData, key: string) {
  const s = d.series[key]
  const n = d.dates.length
  if (!s || !n) return null
  const now = s[n - 1]
  if (now == null || now <= 0) return null
  const since = new Date(+new Date(d.dates[n - 1]) - 5 * 365.25 * 864e5).toISOString().slice(0, 10)
  const xs = s.filter((v, i): v is number => v != null && v > 0 && d.dates[i] >= since).sort((a, b) => a - b)
  // under two years of usable days (e.g. recent losses) is too little to call a range
  if (xs.length < 500) return null
  const lo = xs[Math.floor(xs.length * 0.02)], hi = xs[Math.ceil(xs.length * 0.98) - 1]   // the odd spike left out
  const med = xs[Math.floor(xs.length / 2)]
  const below = xs.filter((v) => v < now).length / xs.length
  return { now, lo, hi, med, below }
}

function ValuationBand({ t, info }: { t: string; info: CompanyInfo }) {
  const q = useQuery({ queryKey: ['company', t, 'valuation'], queryFn: () => api.get<ValuationData>(`/api/valuation/${t}?live=1`), enabled: info.has_prices, retry: false })
  const rows = useMemo(() => {
    if (!q.data) return []
    const order = info.is_bank ? ['pe_ttm', 'pb', 'ps_ttm'] : BAND_FIELDS.map(([k]) => k)
    return order.map((k) => [k, BAND_FIELDS.find(([f]) => f === k)![1], band(q.data!, k)] as const).filter((r) => r[2]).slice(0, 3)
  }, [q.data, info.is_bank])
  if (!rows.length) return null
  const x = (v: number) => (v.toFixed(v >= 100 ? 0 : 1) + 'x')
  return (
    <Card className="p-4">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <H2>Cheap or expensive against its own past?</H2>
        <Link to={`/stock/${t}/valuation`} className="inline-flex items-center gap-1 text-[13px] font-medium text-primary hover:underline">
          Valuation <ArrowRight className="size-3.5" />
        </Link>
      </div>
      <div className="grid grid-cols-1 gap-x-8 gap-y-4 md:grid-cols-3">
        {rows.map(([k, label, b]) => {
          const { now, lo, hi, med, below } = b!
          const pos = (v: number) => `${Math.min(100, Math.max(0, ((v - lo) / (hi - lo || 1)) * 100))}%`
          const pctl = Math.round(below * 100)
          const word = pctl >= 80 ? 'expensive' : pctl <= 20 ? 'cheap' : null
          return (
            <div key={k}>
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-muted-foreground">{label}</span>
                <span className="num font-semibold">{x(now)}</span>
              </div>
              <div className="relative my-2 h-2 rounded-full bg-gradient-to-r from-up/30 via-muted to-down/30">
                <Hint label={`5-year median ${x(med)}`}>
                  <span className="absolute top-[-2px] h-3 w-px bg-muted-foreground/60" style={{ left: pos(med) }} />
                </Hint>
                <span className="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-background bg-foreground shadow" style={{ left: pos(now) }} />
              </div>
              <div className="flex justify-between text-[12px] text-muted-foreground num">
                <span>{x(lo)}</span>
                <span>5-year range</span>
                <span>{x(hi)}</span>
              </div>
              <div className="mt-1 text-[13px]">
                {now > hi ? 'Above its 5-year range' : now < lo ? 'Below its 5-year range' : `Higher than ${pctl}% of days in 5 years`}
                {word && now >= lo && now <= hi && <span className="text-muted-foreground">, on the {word} side</span>}
              </div>
            </div>
          )
        })}
      </div>
    </Card>
  )
}

const pct1 = (d: number | null) => (d == null ? '' : `${d > 0 ? '+' : ''}${d.toFixed(1)}%`)

// One line per number that differs from Yahoo Finance (accuracy.py). Yahoo adjusts and defines some lines its own
// way, so this is something to look at, not an error.
function yahooText(o: AccuracyItem) {
  if (o.missing === o.periods.length) return `${o.label}: Yahoo has a number, this app has none`
  return `${o.label} differs from Yahoo Finance (up to ${pct1(o.worst)})`
}

function QualityCard({ info }: { info: CompanyInfo }) {
  const { pref, setPref } = useSettings()
  const showAll = pref('ui2', 'quality_open', false)
  const qd = info.quality
  if (!qd) return null
  const d = qd.details ?? {}
  const issues = d.issues ?? []
  const notes = d.notes ?? []
  const h = d.history ?? {}
  const open = issues.reduce((a, i) => a + (i.count ?? 1), 0)
  const warn = mcapWarnText(info.mcap_check ?? null)
  const acc = info.accuracy ?? null
  const item = (key: string, kind: 'fail' | 'info', text: ReactNode, i?: { count?: number; where?: string[] }) => (
    <li key={key} className="flex gap-2 py-1">
      <span className={cn('mt-[7px] size-1.5 shrink-0 rounded-full', kind === 'fail' ? 'bg-flag' : 'bg-muted-foreground/50')} />
      <span>
        {text}
        {i?.count && i.count > 1 ? <span className="text-muted-foreground"> ({i.count}×)</span> : null}
        {i?.where?.length ? <span className="block text-xs text-muted-foreground">{i.where.join(' · ')}{(i.count ?? 0) > i.where.length ? ' · …' : ''}</span> : null}
      </span>
    </li>
  )
  const list = [
    ...issues.map((i, n) => item('i' + n, 'fail', i.text, i)),
    ...(h.predecessors ?? []).map((p) => item('p' + p.cik, 'info', <>Financial history before {p.until} comes from the earlier SEC registration <b>{niceName(p.name)}</b> (CIK {p.cik}).</>)),
    ...(warn ? [item('m', mcapIsInfo(info.mcap_check ?? null) ? 'info' : 'fail', `Market cap: ${warn}`)] : []),
    ...(acc?.off ?? []).map((o) => item('y' + o.item, 'info', yahooText(o), { count: o.periods.length, where: o.periods.map((p) => `FY ending ${p.end.slice(0, 7)}: ${p.ours == null ? 'none here' : pct1(p.diff)}`) })),
    ...notes.map((i, n) => item('n' + n, i.level === 'warn' ? 'fail' : 'info', i.text, i)),
  ]
  // One line for an investor; the technical details fold out (and that choice is remembered).
  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <H2>Data quality</H2>
        <span className={cn('num rounded-md px-1.5 py-0.5 text-xs font-semibold', qd.score >= 99 ? 'bg-up-bg text-up' : qd.score >= 95 ? 'bg-secondary' : 'bg-down-bg text-down')}>{qd.score.toFixed(1)}%</span>
        <span className="text-muted-foreground">
          Data checked: {qd.checks - qd.failed} of {qd.checks} checks passed{open ? ` · ${open} to look at` : ''}
          {acc && acc.compared > 0 ? ` · ${acc.match} of ${acc.compared} main numbers match Yahoo` : ''}
        </span>
        <div className="grow" />
        {list.length > 0 && (
          <button type="button" className="text-[13px] font-medium text-primary hover:underline" onClick={() => setPref('ui2', 'quality_open', !showAll)}>
            {showAll ? 'Hide details' : `Details (${list.length})`}
          </button>
        )}
      </div>
      {showAll && (
        <>
          <ul className="mt-2">{list}</ul>
          <p className="mt-2 text-xs text-muted-foreground">
            History FY{h.first_fy}–FY{h.last_fy} · checked {day(String(qd.checked_at))}. Checks: the balance sheet balances, quarters add up to the year, gross profit and net income are consistent, no missing periods. The main annual numbers are also compared with Yahoo Finance (within 1%); Yahoo adjusts some lines its own way, so a difference is a lead, not proof. Numbers that failed a check carry an orange dot in the tables.{' '}
            <Link to="/data-health" className="font-medium text-primary hover:underline">All companies with an odd number</Link>
          </p>
        </>
      )}
    </Card>
  )
}

/* ------------------------------------------------------------------ the tab */
export function Overview({ t, info }: { t: string; info: CompanyInfo }) {
  const st = useQuery({
    queryKey: ['company', t, 'statements', 'FY'],
    queryFn: () => api.get<Statements>(`/api/statements/${t}?period=FY`),
    enabled: info.has_financials,
  })
  return (
    <>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        {info.has_prices ? <PriceCard t={t} /> : <Card className="flex items-center justify-center p-10 text-muted-foreground">No prices for this company.</Card>}
        <KeyNumbers info={info} />
      </div>
      {st.data && <Tiles st={st.data} />}
      {info.has_financials && <ValuationBand t={t} info={info} />}
      {(st.data || info.analyst) && (
        <div className={cn('grid grid-cols-1 gap-4', st.data && info.analyst && 'xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]')}>
          {st.data && <Highlights t={t} st={st.data} />}
          <AnalystCard t={t} info={info} />
        </div>
      )}
      <NewsCard ticker={t} />
      <About info={info} />
      <PeersCard t={t} />
      <QualityCard info={info} />
    </>
  )
}
