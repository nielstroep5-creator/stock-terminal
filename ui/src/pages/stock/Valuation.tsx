import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import type { ReactNode } from 'react'
import { Card } from '@/components/app/Page'
import { EChart, baseOption, useChartColors } from '@/components/chart/EChart'
import { Segmented } from '@/components/ui/segmented'
import { Hint } from '@/components/ui/tooltip'
import { api } from '@/lib/api'
import { dateAxis, fmt } from '@/lib/format'
import { useSettings } from '@/lib/settings'
import { cn } from '@/lib/utils'
import type { CompanyInfo } from '@/pages/stock/types'
import { Dcf } from '@/pages/stock/Dcf'

type Field = { key: string; label: string; fmt: string }
type ValuationData = { dates: string[]; series: Record<string, (number | null)[]>; fields: Field[] }
type Range = '1Y' | '3Y' | '5Y' | '10Y' | 'Max'
const YEARS: Record<Range, number> = { '1Y': 1, '3Y': 3, '5Y': 5, '10Y': 10, Max: 100 }
const WHY: Record<string, string> = { pe_ttm: 'loss-making', pfcf_ttm: 'negative free cash flow', ev_ebitda_ttm: 'negative EBITDA', pb: 'negative equity' }

function stats(d: ValuationData, key: string, years: number) {
  const last = d.dates[d.dates.length - 1]
  const since = new Date(+new Date(last) - years * 365.25 * 864e5).toISOString().slice(0, 10)
  const xs = (d.series[key] ?? []).filter((v, i): v is number => v !== null && d.dates[i] >= since)
  if (!xs.length) return null
  const avg = xs.reduce((a, b) => a + b, 0) / xs.length
  const sd = Math.sqrt(xs.reduce((a, b) => a + (b - avg) ** 2, 0) / xs.length)
  return { avg, sd, min: Math.min(...xs), max: Math.max(...xs) }
}

/** The Valuation tab: multiples against their own history, or a DCF (remembered). */
export function Valuation({ t, info }: { t: string; info: CompanyInfo }) {
  const { pref, setPref } = useSettings()
  const view = pref<string>('ui2', 'val_view', 'multiples') === 'dcf' ? 'dcf' : 'multiples'
  const switcher = (
    <Segmented
      label="Valuation view"
      value={view}
      onChange={(v) => setPref('ui2', 'val_view', v)}
      options={[
        { value: 'multiples', label: 'Multiples' },
        { value: 'dcf', label: 'DCF' },
      ]}
    />
  )
  return view === 'dcf' ? <Dcf t={t} switcher={switcher} /> : <Multiples t={t} info={info} switcher={switcher} />
}

/** Multiples over time against their own history. */
function Multiples({ t, info, switcher }: { t: string; info: CompanyInfo; switcher: ReactNode }) {
  const { pref, setPref } = useSettings()
  const c = useChartColors()
  const q = useQuery({ queryKey: ['company', t, 'valuation'], queryFn: () => api.get<ValuationData>(`/api/valuation/${t}?live=1`) })
  const data = q.data
  const range = pref('ui2', 'val_range', '5Y') as Range
  const wanted = pref<string>('ui2', 'val_field', 'pe_ttm')
  const fwd = info.analyst?.forward_pe ?? null

  const multiples = useMemo(() => (data ? data.fields.filter((f) => !['market_cap', 'enterprise_value'].includes(f.key) && (data.series[f.key] ?? []).some((x) => x !== null)) : []), [data])
  const field = multiples.find((f) => f.key === wanted) ?? multiples[0]

  const option = useMemo(() => {
    if (!data || !field) return null
    const last = data.dates[data.dates.length - 1]
    const since = new Date(+new Date(last) - YEARS[range] * 365.25 * 864e5).toISOString().slice(0, 10)
    const i0 = Math.max(0, data.dates.findIndex((d) => d >= since))
    const dates = data.dates.slice(i0)
    const vals = data.series[field.key].slice(i0).map((v) => ((v !== null && v > 0) || field.fmt === 'pct' ? v : null))
    const st = stats(data, field.key, YEARS[range])
    // stretches without a value (a multiple means nothing while earnings or cash flow are negative): shade them and say why
    const gaps: object[] = []
    for (let i = 0; i < vals.length; i++) {
      if (vals[i] !== null) continue
      let j = i
      while (j + 1 < vals.length && vals[j + 1] === null) j++
      if (j - i >= 5)
        gaps.push([
          { xAxis: dates[i], itemStyle: { color: c.muted + '1c' }, label: { show: j - i > vals.length / 12, formatter: WHY[field.key] ?? 'not meaningful', position: 'inside', color: c.muted, fontSize: 11 } },
          { xAxis: dates[j] },
        ])
      i = j
    }
    const band = st ? [[{ yAxis: st.avg - st.sd }, { yAxis: st.avg + st.sd }]] : []
    const o = baseOption(c)
    return {
      ...o,
      grid: { left: 4, right: 12, top: 18, bottom: 0, containLabel: true },
      xAxis: { ...o.xAxis, type: 'category', data: dates, boundaryGap: false, axisLabel: { ...o.xAxis.axisLabel, hideOverlap: true, ...dateAxis(dates) } },
      yAxis: { ...o.yAxis, axisLabel: { ...o.yAxis.axisLabel, formatter: (x: number) => fmt(x, field.fmt) } },
      tooltip: { ...o.tooltip, valueFormatter: (x: number) => fmt(x, field.fmt) },
      series: [
        {
          name: field.label, type: 'line', data: vals, showSymbol: false, connectNulls: false, lineStyle: { width: 1.7, color: c.primary }, itemStyle: { color: c.primary },
          markLine: st
            ? {
                symbol: 'none', silent: true, lineStyle: { type: 'dashed', color: c.muted }, z: 5, precision: 6, data: [{ yAxis: st.avg }],
                label: { formatter: `avg ${fmt(st.avg, field.fmt)}`, position: 'insideEndTop', color: c.muted, fontSize: 11.5, backgroundColor: c.card, borderColor: c.border, borderWidth: 1, borderRadius: 4, padding: [2, 6] },
              }
            : undefined,
          markArea: band.length || gaps.length ? { silent: true, itemStyle: { color: c.primary + '14' }, data: [...band, ...gaps] } : undefined,
        },
      ],
    }
  }, [data, field, range, c])

  if (!data) return <>{switcher}<div className="text-muted-foreground">{q.isError ? 'Could not load the valuation history.' : 'Loading…'}</div></>
  if (!data.dates.length || !field) return <>{switcher}<Card className="p-10 text-center text-muted-foreground">No valuation history.</Card></>

  const cur = (key: string) => {
    const s = data.series[key]
    return s[s.length - 1]
  }
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        {switcher}
        <Segmented label="Multiple" value={field.key} onChange={(v) => setPref('ui2', 'val_field', v)} options={multiples.map((f) => ({ value: f.key, label: f.label }))} className="flex-wrap" />
        <div className="grow" />
        {fwd && field.key === 'pe_ttm' && (
          <span className="num text-[13px] text-muted-foreground">
            Trailing <b className="text-foreground">{fmt(cur('pe_ttm'), 'x')}</b> · forward <b className="text-foreground">{fmt(fwd, 'x')}</b>
          </span>
        )}
        <Segmented label="Range" value={range} onChange={(v) => setPref('ui2', 'val_range', v)} options={(Object.keys(YEARS) as Range[]).map((k) => ({ value: k, label: k }))} />
      </div>
      <Card className="min-w-0">
        <div className="px-4 pt-3 pb-3">{option && <EChart option={option} className="h-[340px]" />}</div>
        <div className="overflow-x-auto border-t">
          <table className="tbl">
            <thead>
              <tr>
                <th>Multiple</th>
                <th>Current</th>
                <th>1Y avg</th>
                <th>5Y avg</th>
                <th>10Y avg</th>
                <th>10Y low</th>
                <th>10Y high</th>
              </tr>
            </thead>
            <tbody>
              {multiples.map((f) => {
                const s1 = stats(data, f.key, 1)
                const s5 = stats(data, f.key, 5)
                const s10 = stats(data, f.key, 10)
                return [
                  <tr key={f.key} className={cn('cursor-pointer', f.key === field.key && '[&>td]:bg-accent/60')} onClick={() => setPref('ui2', 'val_field', f.key)}>
                    <td className={cn(f.key === field.key && 'font-semibold text-accent-foreground')}>{f.label}</td>
                    <td className="font-semibold">{fmt(cur(f.key), f.fmt)}</td>
                    <td>{fmt(s1?.avg, f.fmt)}</td>
                    <td>{fmt(s5?.avg, f.fmt)}</td>
                    <td>{fmt(s10?.avg, f.fmt)}</td>
                    <td>{fmt(s10?.min, f.fmt)}</td>
                    <td>{fmt(s10?.max, f.fmt)}</td>
                  </tr>,
                  f.key === 'pe_ttm' && fwd ? (
                    <tr key="_fwd" className="text-muted-foreground italic">
                      <td className="pl-7! text-xs">
                        <Hint label="Price divided by the analysts' expected EPS for the next 12 months. Only today's value: a history builds up with the daily analyst data.">
                          <span className="underline decoration-dotted underline-offset-2">Forward P/E (analysts)</span>
                        </Hint>
                      </td>
                      <td className="font-semibold text-foreground not-italic">{fmt(fwd, 'x')}</td>
                      <td>–</td>
                      <td>–</td>
                      <td>–</td>
                      <td>–</td>
                      <td>–</td>
                    </tr>
                  ) : null,
                ]
              })}
            </tbody>
          </table>
        </div>
      </Card>
      <p className="text-xs text-muted-foreground">
        Daily values using only the financial data that was public on each day. Dashed line: average over the selected range. Band: one standard deviation above and below. Grey stretches: no meaningful value, because earnings or cash flow were negative. Click a row to chart it.
      </p>
    </>
  )
}
