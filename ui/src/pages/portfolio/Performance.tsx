import { useQuery } from '@tanstack/react-query'
import { Fragment, useMemo } from 'react'
import { Card } from '@/components/app/Page'
import { Empty, H2, Stat } from '@/components/app/bits'
import { EChart, baseOption, echarts, useChartColors } from '@/components/chart/EChart'
import { Input } from '@/components/ui/input'
import { Segmented } from '@/components/ui/segmented'
import { api } from '@/lib/api'
import { big, dateAxis, day, tone } from '@/lib/format'
import { useSettings } from '@/lib/settings'
import { cn } from '@/lib/utils'
import { sinceDate } from '@/pages/compare/state'
import { CUR_SYM, SyncHistory, cash, pctTxt } from './common'
import type { N } from './common'

type Series = { value: (number | null)[]; invested: (number | null)[]; dividends: (number | null)[]; twr: (number | null)[] }
export type History = {
  reason?: 'no_trades' | 'no_prices'
  empty: boolean; error?: string; acc: string; other: string | null; dates: string[]; warnings?: string[]; updated?: string
  series: Record<string, Series>; bench: ({ key: string; label: string; symbol: string } & Record<string, (number | null)[] | string>)[]
}
const RANGES = ['1M', '3M', '6M', 'YTD', '1Y', '3Y', '5Y', 'Max', 'Custom']
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const has = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const pts = (d: number) => `${d >= 0 ? '+' : '−'}${Math.abs(d).toFixed(1)} pts`

/** Performance: time-weighted return next to the S&P 500 and the FTSE All-World, the value against the money put in,
    or a month-by-month table. Mode, range and custom dates are the same choices as in the old screen. */
export function Performance({ cur }: { cur: string }) {
  const c = useChartColors()
  const { pref, setPref } = useSettings()
  const stored = useQuery({ queryKey: ['pf', 'history', 'stored'], queryFn: () => api.get<History>('/api/portfolio/history?refresh=0').catch((e: Error) => ({ empty: true, error: e.message }) as History) })
  const fresh = useQuery({ queryKey: ['pf', 'history', 'fresh'], queryFn: () => api.get<History>('/api/portfolio/history'), retry: false, staleTime: 10 * 60_000 })
  const hist = fresh.data && !fresh.data.empty ? fresh.data : stored.data
  const mode = pref<string>('state', 'pfMode', 'ret')
  const range = pref<string>('state', 'pfRange', '1Y')
  const from0 = pref<string>('state', 'pfFrom', '')
  const to0 = pref<string>('state', 'pfTo', '')

  const view = useMemo(() => {
    if (!hist || hist.empty || !hist.dates?.length) return null
    const S = hist.series[cur] ?? hist.series[hist.acc]
    const dates = hist.dates
    const last = dates[dates.length - 1]
    let from: string
    let to = last
    if (range === 'Custom') {
      from = from0 || dates[0]
      to = to0 && to0 > from ? to0 : last
    } else from = range === 'Max' ? dates[0] : sinceDate(range, last)
    let i0 = 0
    let i1 = dates.length - 1
    while (i0 + 1 < dates.length && dates[i0 + 1] <= from) i0++
    while (i1 > i0 && dates[i1] > to) i1--
    const rebase = (arr: (number | null)[]) => {
      const b = arr[i0]
      return arr.slice(i0, i1 + 1).map((v) => (v === null || !b ? null : v / b - 1))
    }
    const bench = hist.bench.filter((b) => Array.isArray(b[cur])).map((b) => ({ label: b.label, data: b[cur] as (number | null)[] }))
    return { S, dates, i0, i1, xs: dates.slice(i0, i1 + 1), rebase, bench, first: dates[0], last }
  }, [hist, cur, range, from0, to0])

  const option = useMemo(() => {
    if (!view || mode === 'mon') return null
    const o = baseOption(c)
    const { S, i0, i1, xs, rebase, bench } = view
    type Line = { name: string; data: (number | null)[]; color: string; width: number; area?: boolean; dash?: boolean }
    const lines: Line[] =
      mode === 'val'
        ? [
            { name: 'Value', data: S.value.slice(i0, i1 + 1), color: c.series[0], width: 2.2, area: true },
            { name: 'Money put in', data: S.invested.slice(i0, i1 + 1), color: c.muted, width: 1.3, dash: true },
          ]
        : [{ name: 'Your portfolio', data: rebase(S.twr), color: c.series[0], width: 2.2 }, ...bench.map((b, k) => ({ name: b.label, data: rebase(b.data), color: c.series[1 + k], width: 1.5 }))]
    const f = mode === 'val' ? (v: number) => cash(v, cur, 0) : (v: number) => pctTxt(v)
    const ends: Record<string, N> = Object.fromEntries(lines.map((l) => [l.name, l.data[l.data.length - 1]]))
    return {
      ...o,
      legend: { top: 0, left: 0, icon: 'roundRect', itemWidth: 12, itemHeight: 3, textStyle: { color: c.text, fontSize: 12 }, formatter: (n: string) => (has(ends[n]) ? `${n}  ${f(ends[n] as number)}` : n) },
      grid: { left: 8, right: 8, top: 34, bottom: 4, containLabel: true },
      xAxis: { ...o.xAxis, type: 'category', data: xs, boundaryGap: false, axisLabel: { ...o.xAxis.axisLabel, hideOverlap: true, ...dateAxis(xs) } },
      yAxis: { ...o.yAxis, position: 'right', axisLabel: { ...o.yAxis.axisLabel, formatter: mode === 'val' ? (v: number) => (CUR_SYM[cur] ?? '') + big(v) : (v: number) => `${(v * 100).toFixed(0)}%` } },
      tooltip: { ...o.tooltip, valueFormatter: (v: N) => (has(v) ? f(v) : '–') },
      series: lines.map((l) => ({
        type: 'line', name: l.name, data: l.data, showSymbol: false, connectNulls: true,
        lineStyle: { width: l.width, color: l.color, type: l.dash ? 'dashed' : 'solid' }, itemStyle: { color: l.color },
        areaStyle: l.area ? { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: l.color + '33' }, { offset: 1, color: l.color + '00' }]) } : undefined,
      })),
    }
  }, [view, mode, c, cur])

  const custom = range === 'Custom' && view
  return (
    <Card className="min-w-0 p-4">
      <H2
        side={
          <>
            <Segmented label="What the chart shows" value={mode} onChange={(v) => setPref('state', 'pfMode', v)} options={[{ value: 'ret', label: 'Return %' }, { value: 'val', label: 'Value' }, { value: 'mon', label: 'Monthly' }]} />
            {mode !== 'mon' && (
              <Segmented
                label="Period"
                value={range}
                onChange={(v) => {
                  if (v === 'Custom' && !from0 && view) {
                    setPref('state', 'pfFrom', view.xs[0])
                    setPref('state', 'pfTo', view.last)
                  }
                  setPref('state', 'pfRange', v)
                }}
                options={RANGES.map((r) => ({ value: r, label: r }))}
              />
            )}
          </>
        }
      >
        Performance
      </H2>
      {custom && mode !== 'mon' && (
        <div className="mb-2 flex items-center gap-2 text-[13px] text-muted-foreground">
          From <Input type="date" className="h-8 w-40" min={view.first} max={view.last} value={view.xs[0]} onChange={(e) => setPref('state', 'pfFrom', e.target.value)} />
          to <Input type="date" className="h-8 w-40" min={view.first} max={view.last} value={view.xs[view.xs.length - 1]} onChange={(e) => setPref('state', 'pfTo', e.target.value)} />
        </div>
      )}
      {!view ? (
        <Empty className="h-[320px]">{stored.isLoading || fresh.isLoading ? 'Loading the price history of your holdings… the first time takes a little while.' : hist?.reason === 'no_trades' ? <SyncHistory what="the performance" /> : hist?.error || hist?.warnings?.[0] || 'No price history yet.'}</Empty>
      ) : mode === 'mon' ? (
        <Monthly hist={hist!} cur={cur} />
      ) : (
        <>
          <EChart option={option!} className="h-[320px]" />
          <PeriodNumbers hist={hist!} view={view} cur={cur} />
        </>
      )}
      {view && (
        <p className="mt-3 text-xs text-muted-foreground">
          {mode === 'val'
            ? `What your holdings were worth each day in ${cur}, and the money you put into them (buys minus sells). Cash is not included. `
            : mode === 'mon'
              ? `Time-weighted return per month and per year in ${cur}: buying and selling do not count, price moves and dividends do. The first and the current month are partial. `
              : `Time-weighted return in ${cur}: only price moves and dividends count, not when or how much you bought or sold, so it compares fairly with the indexes. `}
          S&P 500 = SPY, FTSE All-World = VWCE, both with dividends reinvested. {cur !== hist!.acc && `Converted at each day's ${hist!.acc}/${cur} rate. `}Prices from Yahoo{hist!.updated ? `, updated ${hist!.updated}` : ''}.
          {!!hist!.warnings?.length && <span className="block text-down">{hist!.warnings.join(' · ')}</span>}
        </p>
      )}
    </Card>
  )
}

type View = { S: Series; dates: string[]; i0: number; i1: number; bench: { label: string; data: (number | null)[] }[] }

/** The numbers of the chosen period, under the chart. */
function PeriodNumbers({ view, cur }: { hist: History; view: View; cur: string }) {
  const { S, dates, i0, i1, bench } = view
  const at = (arr: (number | null)[], i: number) => arr[i] ?? 0
  const t0 = S.twr[i0]
  const ret = t0 ? (S.twr[i1] ?? 0) / t0 - 1 : null
  const v0 = at(S.value, i0)
  const v1 = at(S.value, i1)
  const putIn = at(S.invested, i1) - at(S.invested, i0)
  const divs = at(S.dividends, i1) - at(S.dividends, i0)
  const result = v1 - v0 - putIn + divs
  let peak = -Infinity
  let dd = 0
  let best: [number, string] | null = null
  let worst: [number, string] | null = null
  for (let i = i0; i <= i1; i++) {
    const x = S.twr[i]
    if (x === null) continue
    peak = Math.max(peak, x)
    dd = Math.min(dd, x / peak - 1)
    const prev = S.twr[i - 1]
    if (i > i0 && prev) {
      const r = x / prev - 1
      if (!best || r > best[0]) best = [r, dates[i]]
      if (!worst || r < worst[0]) worst = [r, dates[i]]
    }
  }
  const years = (+new Date(dates[i1]) - +new Date(dates[i0])) / 31557600000
  const ms = (v: number) => (v > 0 ? '+' : v < 0 ? '−' : '') + cash(Math.abs(v), cur)
  const vs = bench
    .map((b) => {
      const s = b.data[i0]
      const e = b.data[i1]
      if (ret === null || !s || !has(e)) return null
      const diff = (ret - (e / s - 1)) * 100
      return <div key={b.label} className={diff >= 0 ? 'text-up' : 'text-down'}>{pts(diff)} vs {b.label}</div>
    })
    .filter(Boolean)
  return (
    <div className="mt-4 border-t pt-3">
      <div className="mb-2 text-xs font-medium text-muted-foreground">{day(dates[i0])} – {day(dates[i1])}</div>
      <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 lg:grid-cols-5">
        <Stat label="Return" tip="Time-weighted: price moves and dividends only, so it compares with the indexes." value={pctTxt(ret)} className={tone(ret)} sub={vs.length ? <>{vs}</> : undefined} />
        <Stat label="Result" tip="End value minus start value, minus the money put in, plus dividends received." value={ms(result)} className={tone(result)} sub="gain or loss in money" />
        <Stat label="Value" value={cash(v1, cur)} sub={`from ${cash(v0, cur)}`} />
        <Stat label="Money put in" value={ms(putIn)} sub={putIn < 0 ? 'more sold than bought' : 'buys minus sells'} />
        <Stat label="Dividends" value={cash(divs, cur)} sub="received" />
        <Stat label="Biggest drop" tip="The largest fall from a high to a later low within the period." value={dd < 0 ? pctTxt(dd) : '–'} className={dd < 0 ? 'text-down' : ''} sub="from a peak" />
        <Stat label="Best day" value={best ? pctTxt(best[0]) : '–'} className={tone(best?.[0])} sub={best ? day(best[1]) : ''} />
        <Stat label="Worst day" value={worst ? pctTxt(worst[0]) : '–'} className={tone(worst?.[0])} sub={worst ? day(worst[1]) : ''} />
        {years >= 1 && ret !== null && <Stat label="Per year" value={pctTxt(Math.pow(1 + ret, 1 / years) - 1)} className={tone(ret)} sub={`over ${years.toFixed(1)} years`} />}
      </div>
    </div>
  )
}

/** Return per month and year: last value of each month against the month before (the first month against the first value). */
function monthlyReturns(dates: string[], idx: (number | null)[]) {
  const ends = new Map<string, number>()
  let first: number | null = null
  dates.forEach((d, k) => {
    const v = idx[k]
    if (v === null || v === undefined) return
    if (first === null) first = v
    ends.set(d.slice(0, 7), v)
  })
  const months: Record<string, number | null> = {}
  const years: Record<string, number | null> = {}
  let prev = first as number | null
  let prevYearEnd = first as number | null
  let curYear: string | null = null
  for (const [m, v] of ends) {
    const y = m.slice(0, 4)
    if (curYear !== null && y !== curYear) prevYearEnd = prev
    curYear = y
    months[m] = prev ? v / prev - 1 : null
    years[y] = prevYearEnd ? v / prevYearEnd - 1 : null
    prev = v
  }
  return { months, years }
}

function Monthly({ hist, cur }: { hist: History; cur: string }) {
  const S = hist.series[cur] ?? hist.series[hist.acc]
  const P = monthlyReturns(hist.dates, S.twr)
  const B = hist.bench.filter((b) => Array.isArray(b[cur])).map((b) => ({ label: b.label, ...monthlyReturns(hist.dates, b[cur] as (number | null)[]) }))
  const years = Object.keys(P.years).sort().reverse()
  const cell = (r: N, strong?: boolean) => {
    if (!has(r)) return <td className="bg-transparent!" />
    const a = Math.min(Math.abs(r) / 0.08, 1) // full colour at ±8%
    return (
      <td className={cn('text-center! text-foreground', strong && 'font-semibold')} style={{ background: `color-mix(in srgb, var(${r >= 0 ? '--up' : '--down'}) ${Math.round(8 + a * 45)}%, transparent)` }}>
        {`${r > 0 ? '+' : r < 0 ? '−' : ''}${Math.abs(r * 100).toFixed(1)}%`}
      </td>
    )
  }
  return (
    <div className="overflow-x-auto">
      <table className="tbl tight heatmap">
        <thead>
          <tr>
            <th>Year</th>
            {MON.map((m) => <th key={m} className="text-center!">{m}</th>)}
            <th className="text-center!">Year</th>
            {B.map((b) => <th key={b.label}>{b.label}</th>)}
          </tr>
        </thead>
        <tbody>
          {years.map((y) => (
            <tr key={y}>
              <td className="font-semibold">{y}</td>
              {MON.map((_, k) => <Fragment key={k}>{cell(P.months[`${y}-${String(k + 1).padStart(2, '0')}`])}</Fragment>)}
              {cell(P.years[y], true)}
              {B.map((b) => {
                const r = b.years[y]
                const mine = P.years[y]
                return (
                  <td key={b.label} className="text-muted-foreground">
                    {has(r) ? pctTxt(r) : ''}
                    {has(r) && has(mine) && <div className={cn('text-[11px]', mine - r >= 0 ? 'text-up' : 'text-down')}>{pts((mine - r) * 100)}</div>}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
