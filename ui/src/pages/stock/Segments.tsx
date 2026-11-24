import { scrollToNewest } from '@/pages/stock/Financials'
import { useQuery } from '@tanstack/react-query'
import { Percent } from 'lucide-react'
import { useMemo } from 'react'
import { Card } from '@/components/app/Page'
import { BAR_FIT, BAR_TIP, EChart, FOCUS, axisTip, baseOption, useChartColors } from '@/components/chart/EChart'
import { Segmented } from '@/components/ui/segmented'
import { IconToggle } from '@/components/ui/toggle'
import { Hint } from '@/components/ui/tooltip'
import { api } from '@/lib/api'
import { big, fmt } from '@/lib/format'
import { useSettings } from '@/lib/settings'
import type { Settings } from '@/lib/settings'
import { cn } from '@/lib/utils'
import type { Period } from '@/pages/stock/types'

type SegCell = { v: number; part?: number; m: string; src: string } | undefined
type Member = { key: string; label: string; values: Record<string, SegCell> }
type SegData = {
  breakdowns: { key: string; label: string }[]
  breakdown: string
  items: string[]
  periods: (Period & { flag?: string | null })[]
  members: Member[]
  totals: Record<string, number | null>
}
const ITEMS: [string, string][] = [['revenue', 'Revenue'], ['operating_income', 'Operating income'], ['gross_profit', 'Gross profit'], ['adjusted_ebitda', 'Adjusted EBITDA']]
// more segments than series colours: continue with softer shades
const EXTRA = ['#8a94a6', '#d4a017', '#3b9ae1', '#e0607e', '#7cb342', '#9575cd']

/** Revenue (or profit) split by product, business segment or region, with the company's own names. */
export function Segments({ t }: { t: string }) {
  const { settings, pref, setPref } = useSettings()
  const c = useChartColors()
  const period = pref('ui2', 'fin_period', settings.default_period) as Settings['default_period']
  const bd = pref<string>('ui2', 'seg_breakdown', '')
  const item = pref<string>('ui2', 'seg_item', 'revenue')
  const mix = pref('ui2', 'seg_mix', false)
  const span = pref<string>('ui2', 'seg_span', '10')
  const q = useQuery({
    queryKey: ['company', t, 'segments', period, bd, item],
    queryFn: () => api.get<SegData>(`/api/segments/${t}?period=${period}&breakdown=${encodeURIComponent(bd)}&item=${encodeURIComponent(item)}`),
    placeholderData: (prev) => prev,
  })
  const data = q.data

  const view = useMemo(() => {
    if (!data) return null
    const n = span === 'all' ? data.periods.length : +span * (period === 'FY' ? 1 : 4)
    const periods = data.periods.slice(-n)
    const inPart = (m: Member, p: Period) => !!m.values[p.key]?.part
    const main = data.members.filter((m) => periods.some((p) => inPart(m, p)))
    const sumOf = (p: Period) => main.reduce((s, m) => s + (inPart(m, p) ? (m.values[p.key]?.v ?? 0) : 0), 0)
    return { periods, main, others: data.members.filter((m) => !main.includes(m)), inPart, sumOf }
  }, [data, span, period])

  const colors = useMemo(() => [...c.series.slice(0, 5), ...EXTRA], [c])
  const option = useMemo(() => {
    if (!view || !view.main.length) return null
    const o = baseOption(c)
    const f = (x: number) => (mix ? fmt(x, 'pct') : big(x))
    return {
      ...o,
      color: colors,
      xAxis: { ...o.xAxis, type: 'category', data: view.periods.map((p) => p.label), axisLabel: { ...o.xAxis.axisLabel, hideOverlap: true } },
      yAxis: { ...o.yAxis, scale: false, max: mix ? 1 : null, axisLabel: { ...o.yAxis.axisLabel, formatter: f } },
      tooltip: { ...o.tooltip, ...BAR_TIP, formatter: axisTip(c, (_, x) => f(x)) },
      series: view.main.map((m) => ({
        name: m.label, type: 'bar', stack: 's', ...BAR_FIT, ...FOCUS,
        data: view.periods.map((p) => (view.inPart(m, p) ? (mix ? (m.values[p.key]?.v ?? 0) / view.sumOf(p) : m.values[p.key]?.v) : null)),
      })),
    }
  }, [view, mix, c, colors])

  if (!data || !view) return <div className="text-muted-foreground">{q.isError ? 'Could not load the segments.' : 'Loading…'}</div>
  if (!data.breakdowns.length) return <Card className="p-10 text-center text-muted-foreground">No segment data.</Card>
  const cols = view.periods // oldest first, like the chart above it
  const items = ITEMS.filter(([k]) => data.items.includes(k))
  const itemLabel = ITEMS.find(([k]) => k === item)?.[1].toLowerCase() ?? item
  const show = (m: Member, p: Period) => {
    const cell = m.values[p.key]
    if (!cell) return '–'
    return mix ? fmt(cell.v / view.sumOf(p), 'pct') : fmt(cell.v, 'usd', { unit: 'M' })
  }

  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <Segmented label="Split by" value={data.breakdown} onChange={(v) => setPref('ui2', 'seg_breakdown', v)} options={data.breakdowns.map((b) => ({ value: b.key, label: b.label }))} className="flex-wrap" />
        {items.length > 1 && <Segmented label="Number" value={data.items.includes(item) ? item : items[0][0]} onChange={(v) => setPref('ui2', 'seg_item', v)} options={items.map(([k, l]) => ({ value: k, label: l }))} />}
        <div className="grow" />
        <Segmented label="Period" value={period} onChange={(v) => setPref('ui2', 'fin_period', v)} options={[{ value: 'FY', label: 'Annual' }, { value: 'Q', label: 'Quarterly' }, { value: 'TTM', label: 'TTM' }]} />
        <Segmented label="Years shown" value={span} onChange={(v) => setPref('ui2', 'seg_span', v)} options={[{ value: '5', label: '5Y' }, { value: '10', label: '10Y' }, { value: 'all', label: 'All' }]} />
        <IconToggle on={mix} onChange={(v) => setPref('ui2', 'seg_mix', v)} label="Show as share of the total" text="Share of total">
          <Percent />
        </IconToggle>
      </div>
      <Card className="min-w-0">
        <div className="flex flex-wrap gap-x-4 gap-y-1 px-4 pt-3 text-xs">
          {view.main.map((m, i) => (
            <span key={m.key} className="inline-flex items-center gap-1.5">
              <span className="size-2.5 rounded-[3px]" style={{ background: colors[i % colors.length] }} />
              {m.label}
            </span>
          ))}
        </div>
        <div className="px-4 pt-2 pb-3">{option ? <EChart option={option} hoverTip className="h-[320px]" /> : <div className="py-8 text-center text-muted-foreground">Nothing to chart for this split.</div>}</div>
        <div ref={scrollToNewest} className="overflow-x-auto border-t">
          <table className="tbl sticky-first">
            <thead>
              <tr>
                <th className="min-w-56">{data.breakdowns.find((b) => b.key === data.breakdown)?.label}{mix ? '' : ', $ millions'}</th>
                {cols.map((p) => (
                  <th key={p.key}>
                    {p.flag ? (
                      <Hint label={p.flag}>
                        <span>
                          {p.label}
                          <span className="ml-1 inline-block size-1.5 rounded-full bg-flag align-middle" />
                        </span>
                      </Hint>
                    ) : (
                      p.label
                    )}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[...view.main, ...view.others].map((m, i) => (
                <tr key={m.key} className={cn(i >= view.main.length && 'text-muted-foreground')}>
                  <td>
                    <span className="inline-flex items-center gap-2">
                      {i < view.main.length && <span className="size-2 rounded-[2px]" style={{ background: colors[i % colors.length] }} />}
                      {m.label}
                    </span>
                  </td>
                  {cols.map((p) => (
                    <td key={p.key} title={m.values[p.key] ? `Source: ${m.values[p.key]?.src}` : undefined}>
                      {show(m, p)}
                    </td>
                  ))}
                </tr>
              ))}
              <tr className="font-semibold">
                <td>Sum of segments</td>
                {cols.map((p) => (
                  <td key={p.key}>{mix ? '100%' : fmt(view.sumOf(p), 'usd', { unit: 'M' })}</td>
                ))}
              </tr>
              {!mix && (
                <tr className="text-muted-foreground">
                  <td>Company total ({itemLabel})</td>
                  {cols.map((p) => (
                    <td key={p.key}>{fmt(data.totals[p.key], 'usd', { unit: 'M' })}</td>
                  ))}
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
      <p className="text-xs text-muted-foreground">
        Names as reported by the company. Grey rows are subtotals or another way to split the same number, so they are not in the chart.{' '}
        <span className="inline-block size-1.5 rounded-full bg-flag align-middle" /> on a period: the segments do not add up to the company total there.
      </p>
    </>
  )
}
