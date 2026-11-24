import { useQueries, useQuery } from '@tanstack/react-query'
import { ArrowLeftRight, Rows3, Square } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { MutableRefObject } from 'react'
import { BAR_FIT, EChart, STRIPES, baseOption, useChartColors } from '@/components/chart/EChart'
import type { echarts } from '@/components/chart/EChart'
import { IconToggle } from '@/components/ui/toggle'
import { Segmented } from '@/components/ui/segmented'
import { Hint } from '@/components/ui/tooltip'
import { api } from '@/lib/api'
import { fmt } from '@/lib/format'
import { useSettings } from '@/lib/settings'
import { cn } from '@/lib/utils'
import { scrollToNewest } from '@/pages/stock/Financials'
import { SHAPES, csvLine, numberColor } from './state'
import type { Chart, Field } from './state'

// [calendar-quarter date, value, fiscal label, warning, period end date]
type Pt = [string, number | null, string, string | null | undefined, string | undefined]
type EstYear = { fy: number; est: boolean; revenue?: number | null; eps?: number | null; revenue_growth?: number | null; eps_growth?: number | null }
type Cell = { v: number | null; fiscal: string; end?: string; flag?: string | null; est: boolean; partial?: boolean; g?: number | null }
export type Exporter = { title: string; png: () => string | null; csv: () => string }

/* Numbers that have analyst estimates for the coming fiscal years: how to read them from /api/estimates_annual. */
const EST_FIELDS: Record<string, (a: EstYear) => number | null | undefined> = {
  'item:revenue': (a) => a.revenue, 'item:eps_diluted': (a) => a.eps, 'metric:revenue_yoy': (a) => a.revenue_growth, 'metric:eps_diluted_yoy': (a) => a.eps_growth,
}
const EST_GROWTH: Record<string, 'revenue_growth' | 'eps_growth'> = { 'item:revenue': 'revenue_growth', 'item:eps_diluted': 'eps_growth' }
/* The slot of a period on the x-axis. Annual: the calendar year in which the fiscal year ENDS (Apple's year to
   Sep 2026 and Nvidia's year to Jan 2026 are both 2026; a 52/53-week year ending in the first days of January
   belongs to the year before). Quarterly / TTM: the calendar quarter. */
function slotOf(p: Pt, period: string) {
  if (period === 'FY') {
    const end = p[4] || p[0]
    const y = +end.slice(0, 4)
    return String(end.slice(5) <= '01-07' ? y - 1 : y)
  }
  return `${p[0].slice(0, 4)} Q${Math.ceil(+p[0].slice(5, 7) / 3)}`
}
const prevSlot = (k: string, period: string) => (period === 'FY' ? String(+k - 1) : `${+k.slice(0, 4) - 1}${k.slice(4)}`)
const longDate = (d?: string) => (d ? new Date(d + 'T00:00:00').toLocaleDateString('en-US', { day: 'numeric', month: 'short', year: 'numeric' }) : '')
const ok = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const signed = (v: number) => (v > 0 ? '+' : '') + fmt(v, 'pct')
/* change of a percentage (a margin, a growth rate) over the years shown: in percentage points, not as growth */
const signedPp = (v: number) => `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v * 100).toFixed(1)} pp`
/* one chart with several numbers: number 1 solid, 2 dashed, 3 dotted (with the shapes of the points) */
const LINE_TYPES = ['solid', 'dashed', 'dotted'] as const
type Span = { total: number; cagr: number | null; pp?: boolean }

function Shape({ i, color }: { i: number; color: string }) {
  return (
    <svg aria-hidden viewBox="0 0 12 12" className="size-3 shrink-0" style={{ fill: color }}>
      {i === 0 ? <circle cx="6" cy="6" r="5" /> : i === 1 ? <path d="M6 0.5 11.5 6 6 11.5 0.5 6z" /> : <path d="M6 1 11.5 11H0.5z" />}
    </svg>
  )
}
export { Shape }

/** The year slider under the chart: a dot per year, two handles. Drag a handle or the coloured part, click a
    year, or use the arrow keys on a handle. Until you move the start it is the first year every company has. */
function YearSlider({ years, estYears, from, to, lockFrom, onChange }: { years: number[]; estYears: Set<number>; from: number; to: number; lockFrom: boolean; onChange: (r: { from: string; to: string }) => void }) {
  const n = years.length
  const track = useRef<HTMLDivElement>(null)
  const [live, setLive] = useState<[number, number] | null>(null)
  const a0 = Math.max(0, years.indexOf(from))
  const b0 = Math.max(a0, years.indexOf(to) < 0 ? n - 1 : years.indexOf(to))
  const [a, b] = live ?? [a0, b0]
  if (n < 2) return null
  const pos = (i: number) => (i / (n - 1)) * 100
  const step = Math.max(1, Math.ceil(n / 9))
  const commit = (x: number, y: number, fromSet: boolean) => onChange({ from: fromSet ? String(years[x]) : '', to: y < n - 1 ? String(years[y]) : '' })
  const at = (clientX: number) => {
    const r = track.current!.getBoundingClientRect()
    return Math.round(Math.min(1, Math.max(0, (clientX - r.left) / r.width)) * (n - 1))
  }
  const drag = (e: React.PointerEvent, what: 'a' | 'b' | 'w') => {
    e.preventDefault()
    e.stopPropagation()
    const start = at(e.clientX)
    let cur: [number, number] = [a0, b0]
    const move = (ev: PointerEvent) => {
      const i = at(ev.clientX)
      if (what === 'a') cur = [Math.min(i, b0), b0]
      else if (what === 'b') cur = [a0, Math.max(i, a0)]
      else { const d = Math.max(-a0, Math.min(n - 1 - b0, i - start)); cur = [a0 + d, b0 + d] }
      setLive(cur)
    }
    const up = () => {
      document.removeEventListener('pointermove', move)
      document.removeEventListener('pointerup', up)
      setLive(null)
      if (cur[0] !== a0 || cur[1] !== b0) commit(cur[0], cur[1], what === 'b' ? lockFrom : true)
    }
    document.addEventListener('pointermove', move)
    document.addEventListener('pointerup', up)
  }
  const key = (e: React.KeyboardEvent, h: 'a' | 'b') => {
    const d = e.key === 'ArrowLeft' || e.key === 'ArrowDown' ? -1 : e.key === 'ArrowRight' || e.key === 'ArrowUp' ? 1 : 0
    if (!d) return
    e.preventDefault()
    if (h === 'a') commit(Math.max(0, Math.min(b0, a0 + d)), b0, true)
    else commit(a0, Math.min(n - 1, Math.max(a0, b0 + d)), lockFrom)
  }
  const handle = 'absolute top-1/2 size-4 -translate-x-1/2 -translate-y-1/2 cursor-ew-resize rounded-full border-[3px] border-primary bg-card shadow outline-none focus-visible:ring-2 focus-visible:ring-ring/60'
  return (
    <div className="px-3 pt-2 pb-1 select-none">
      <div
        ref={track} className="relative h-4 cursor-pointer"
        onPointerDown={(e) => {
          const i = at(e.clientX)
          if (Math.abs(i - a0) <= Math.abs(i - b0) && i <= b0) commit(i, b0, true)
          else commit(a0, Math.max(i, a0), lockFrom)
        }}
      >
        <div className="absolute inset-x-0 top-1/2 h-1 -translate-y-1/2 rounded-full bg-border" />
        <div className="absolute top-1/2 h-1 -translate-y-1/2 cursor-grab rounded-full bg-primary" style={{ left: `${pos(a)}%`, width: `${pos(b) - pos(a)}%` }} onPointerDown={(e) => drag(e, 'w')} />
        {years.map((y, i) => (
          <span key={y} className={cn('pointer-events-none absolute top-1/2 size-1 -translate-x-1/2 -translate-y-1/2 rounded-full', i >= a && i <= b ? 'bg-primary-foreground/80' : 'bg-muted-foreground/50')} style={{ left: `${pos(i)}%` }} />
        ))}
        <button type="button" aria-label={`Start year: ${years[a]}`} title={`Start: ${years[a]}`} className={handle} style={{ left: `${pos(a)}%` }} onPointerDown={(e) => drag(e, 'a')} onKeyDown={(e) => key(e, 'a')} />
        <button type="button" aria-label={`End year: ${years[b]}`} title={`End: ${years[b]}`} className={handle} style={{ left: `${pos(b)}%` }} onPointerDown={(e) => drag(e, 'b')} onKeyDown={(e) => key(e, 'b')} />
      </div>
      <div className="num relative mt-1 h-4 text-[11px] text-muted-foreground">
        {years.map((y, i) => (i === n - 1 || (i % step === 0 && n - 1 - i >= step * 0.6) ? <span key={y} className="absolute -translate-x-1/2 whitespace-nowrap" style={{ left: `${pos(i)}%` }}>{y}{estYears.has(y) ? ' (E)' : ''}</span> : null))}
      </div>
    </div>
  )
}

/** Financial numbers: one series per company and number, side by side per year or per calendar quarter. Numbers
    in different units get their own axis (the first on the left, the others on the right). Analyst estimates
    (annual only) are faded bars and dashed lines. Under it: the year slider, the legend and the numbers. */
export function FinChart({ chart, tickers, colors, fields, full, exporter, onRange }: { chart: Chart; tickers: string[]; colors: string[]; fields: Field[]; full: boolean; exporter: MutableRefObject<Exporter | null>; onRange: (r: { from: string; to: string }) => void }) {
  const c = useChartColors()
  const { settings, pref, setPref } = useSettings()
  const period = chart.p
  const mode = chart.m
  // annual: fiscal years by the year they end in, or calendar years (a setting; an old link with y= still wins)
  const cal = period === 'FY' && (chart.y || settings.compare_years) === 'cal'
  const tk = tickers.join(',')
  const data = useQueries({
    queries: fields.map((fi) => ({ queryKey: ['compare-series', tk, fi.key, cal ? 'TTM' : period], queryFn: () => api.get<Record<string, Pt[]>>(`/api/series?tickers=${tk}&field=${encodeURIComponent(fi.key)}&period=${cal ? 'TTM' : period}`), enabled: tickers.length > 0, staleTime: 60_000 })),
  })
  const estOn = !cal && chart.e && period === 'FY' && fields.some((fi) => EST_FIELDS[fi.key])
  const est = useQuery({ queryKey: ['compare-est', tk], queryFn: () => api.get<Record<string, EstYear[]>>(`/api/estimates_annual?tickers=${tk}`), enabled: estOn && tickers.length > 0, staleTime: 60_000 })
  const loaded = data.every((d) => d.data) && (!estOn || !est.isLoading)
  const datas = data.map((d) => d.data)
  const stamp = data.map((d) => d.dataUpdatedAt).join()
  const box = useRef<HTMLDivElement>(null)
  const inst = useRef<echarts.ECharts | null>(null)
  const [width, setWidth] = useState(900)
  useEffect(() => {
    if (!box.current) return
    const ro = new ResizeObserver(([e]) => setWidth(e.contentRect.width))
    ro.observe(box.current)
    return () => ro.disconnect()
  }, [])
  const newest = pref('ui2', 'cmp_newest_first', false)
  // 2 or 3 numbers: a small chart per number under each other (default), or all in one chart
  const layout = pref('ui2', 'cmp_layout', 'stack') as 'stack' | 'one'
  const stacked = layout === 'stack' && fields.length > 1

  const model = useMemo(() => {
    if (!loaded) return null
    const unitOf = (fi: Field) => (mode === 'value' ? fi.fmt : mode === 'growth' ? 'pct' : 'num')
    const units = [...new Set(fields.map(unitOf))]
    // per number and company: slot -> cell
    const maps = fields.map((fi, mi) => tickers.map((t) => {
      const m = new Map<string, Cell>()
      const pts = datas[mi]?.[t] ?? []
      if (cal) {
        // calendar years: the trailing 12 months at each December quarter; the current year is the latest TTM (partial)
        for (const p of pts) {
          const y = p[0].slice(0, 4)
          const whole = p[0].slice(5, 7) === '12'
          const mon = new Date(p[0] + 'T00:00:00').toLocaleDateString('en-US', { month: 'short' })
          m.set(y, { v: p[1], fiscal: whole ? `Jan–Dec ${y} (${p[2]})` : `Jan–${mon} ${y}: partial, last 12 months (${p[2]})`, end: p[4], flag: p[3], est: false, partial: !whole })
        }
      } else for (const p of pts) m.set(slotOf(p, period), { v: p[1], fiscal: p[2], end: p[4], flag: p[3], est: false })
      const read = EST_FIELDS[fi.key]
      const list = (estOn && est.data?.[t]) || []
      const last = pts[pts.length - 1]
      if (read && last && list.length) {
        // estimate years go in the slots that follow from the last reported year (FY label -> slot offset)
        const off = +slotOf(last, 'FY') - +String(last[2]).replace(/\D/g, '')
        for (const a of list) {
          if (!a.est) continue
          const k = String(a.fy + off)
          const v = read(a)
          if (ok(v) && !m.has(k)) m.set(k, { v, fiscal: `FY${a.fy} (E)`, est: true, g: EST_GROWTH[fi.key] ? a[EST_GROWTH[fi.key]] : undefined })
        }
      }
      return m
    }))
    const allSlots = [...new Set(maps.flatMap((ms) => ms.flatMap((m) => [...m.keys()])))].sort()
    const yrs = [...new Set(allSlots.map((k) => +k.slice(0, 4)))].sort((x, y) => x - y)
    const estYears = new Set(yrs.filter((y) => allSlots.filter((k) => +k.slice(0, 4) === y).every((k) => maps.every((ms) => ms.every((m) => !m.get(k) || m.get(k)!.est)))))
    // start unlocked: the first year every company (with data) has
    const firsts = tickers.map((_, ti) => allSlots.find((k) => maps.some((ms) => ok(ms[ti].get(k)?.v)))).filter(Boolean).map((k) => +k!.slice(0, 4))
    const clamp = (y: number) => (yrs.length ? Math.min(yrs[yrs.length - 1], Math.max(yrs[0], y)) : y)
    const yFrom = chart.from ? clamp(+chart.from) : firsts.length ? Math.max(...firsts) : yrs[0]
    const lastRep = [...yrs].reverse().find((y) => !estYears.has(y)) ?? yrs[yrs.length - 1]
    const yTo = chart.to === 'rep' ? Math.max(yFrom, lastRep) : chart.to ? Math.max(yFrom, clamp(+chart.to)) : yrs[yrs.length - 1]
    const slots = allSlots.filter((k) => +k.slice(0, 4) >= yFrom && +k.slice(0, 4) <= yTo)
    const any = (k: string, f: (x: Cell) => boolean | undefined) => maps.some((ms) => ms.some((m) => { const x = m.get(k); return !!x && !!f(x) }))
    const label = (k: string) => (cal && any(k, (x) => x.partial) ? `${k} (partial)` : any(k, (x) => x.est) && !any(k, (x) => !x.est) ? `${k} (E)` : k)
    const multi = fields.length > 1
    const lines = fields.flatMap((fi, mi) => tickers.map((t, ti) => {
      const m = maps[mi][ti]
      const base = slots.map((s) => m.get(s)).find((y) => y && ok(y.v) && y.v > 0)
      const vals = slots.map((k) => {
        const x = m.get(k)
        if (!x || !ok(x.v)) return null
        if (mode === 'growth') {
          if (x.est && ok(x.g)) return x.g // estimate: growth on the analysts' basis
          const p = m.get(prevSlot(k, period))
          return p && ok(p.v) && p.v > 0 ? x.v / p.v - 1 : null
        }
        if (mode === 'index') return base ? (x.v / (base.v as number)) * 100 : null
        return x.v
      })
      // total change and yearly growth over the years shown; for a percentage (margin, growth) the change in
      // percentage points instead: a growth rate of a growth rate means nothing
      const idx = vals.map((v, i) => [v, i] as const).filter((x): x is readonly [number, number] => x[0] !== null)
      let span: Span | null = null
      const unit = unitOf(fi)
      if (idx.length > 1 && unit === 'pct') {
        span = { total: idx[idx.length - 1][0] - idx[0][0], cagr: null, pp: true }
      } else if (mode === 'value' && idx.length > 1 && idx[0][0] > 0 && idx[idx.length - 1][0] > 0) {
        const [a, i0] = idx[0]
        const [b, i1] = idx[idx.length - 1]
        const n = period === 'FY' ? +slots[i1] - +slots[i0] : (i1 - i0) / 4
        span = { total: b / a - 1, cagr: n > 0 ? Math.pow(b / a, 1 / n) - 1 : null }
      }
      return { name: multi ? `${t} · ${fi.label}` : t, t, fi, mi, ti, type: chart.c[mi] ?? 'line', unit: unitOf(fi), axis: units.indexOf(unitOf(fi)), color: numberColor(colors, c.series, ti, mi), cells: slots.map((k) => m.get(k)), vals, span }
    }))
    return { units, slots, yrs, estYears, yFrom, yTo, label, lines, multi, hasEst: lines.some((l) => l.cells.some((x) => x?.est)), flagged: lines.some((l) => l.cells.some((x, i) => x?.flag && l.vals[i] !== null)) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded, stamp, est.data, tk, fields, colors, chart, cal, c])

  const title = fields.map((f) => f.label).join(' · ') + { FY: cal ? ' · calendar years (Jan–Dec)' : ' · fiscal years', Q: ' · quarterly', TTM: ' · trailing 12 months' }[period] + (mode === 'growth' ? ' · YoY %' : mode === 'index' ? ' · indexed (100)' : '')

  const option = useMemo(() => {
    if (!model) return null
    const o = baseOption(c)
    const { slots, lines, units } = model
    const n = stacked ? fields.length : 1
    const plotW = Math.max(300, width - 140)
    const bars = lines.filter((l) => l.type === 'bar').length
    const roomBars = slots.length * Math.max(stacked ? Math.ceil(bars / n) : bars, 1) <= plotW / 42
    const roomLines = slots.length <= plotW / 55
    const series: Record<string, unknown>[] = []
    for (const l of lines) {
      const pt = (i: number, onlyEst?: boolean) => {
        const x = l.cells[i]
        const v = l.vals[i]
        if (v === null || !x || (onlyEst !== undefined && x.est !== onlyEst)) return { value: null }
        const d: Record<string, unknown> = { value: v, cell: x, span: l.span }
        if (x.flag) Object.assign(d, { symbol: 'triangle', symbolSize: 9, itemStyle: { color: '#d97706', opacity: 1 } })
        if (l.type === 'bar' && x.est) d.itemStyle = { color: l.color, opacity: 0.7, decal: STRIPES }
        else if (l.type === 'bar' && x.partial) d.itemStyle = { color: l.color, opacity: 0.45 }
        return d
      }
      // numbers above the bars and points: all of them when there is room, otherwise only the latest
      const all = chart.vl === 'on' || (chart.vl === 'auto' && (l.type === 'bar' ? roomBars : roomLines && !(bars && !stacked && fields.length > 1)))
      // dollar amounts in one scale per line with one decimal (89.1B next to 303.0B, not 89.10B)
      const top = Math.max(0, ...l.vals.map((v) => Math.abs(v ?? 0)))
      const scale: [number, string] | null = l.unit === 'usd' || l.unit === 'shares' ? (top >= 1e12 ? [1e12, 'T'] : top >= 1e9 ? [1e9, 'B'] : top >= 1e6 ? [1e6, 'M'] : null) : null
      const label = (v: number) => (scale ? (v / scale[0]).toFixed(Math.abs(v / scale[0]) >= 1000 ? 0 : 1) + scale[1] : fmt(v, l.unit))
      const lineType = stacked ? 'solid' : LINE_TYPES[l.mi] ?? 'solid'
      const base = {
        name: l.name, type: l.type, yAxisIndex: stacked ? l.mi : l.axis, xAxisIndex: stacked ? l.mi : 0, ...BAR_FIT, connectNulls: true, z: l.type === 'line' ? 5 : 2,
        // points only show where labels do, or on hover (a dot on every year made the lines busy)
        symbol: SHAPES[l.mi], symbolSize: slots.length < 60 ? 7 : 4, showSymbol: l.type === 'line',
        emphasis: { focus: 'series', itemStyle: { opacity: 1 } }, blur: { itemStyle: { opacity: 0.18 }, lineStyle: { opacity: 0.18 }, label: { opacity: 0.25 } },
        lineStyle: { width: 2.5, type: lineType }, itemStyle: l.type === 'line' ? { color: l.color, borderColor: c.card, borderWidth: 1.5, opacity: all ? 1 : 0 } : { color: l.color, borderRadius: [2, 2, 0, 0] },
        label: { show: all, position: 'top', distance: 5, fontSize: 11, fontWeight: 600, color: c.text, textBorderColor: c.card, textBorderWidth: 2.5, opacity: 1, formatter: (p: { value: number | null; data: { hide?: boolean } }) => (p.value === null || p.value === undefined || p.data.hide ? '' : label(p.value)) },
        // end labels of several lines: pushed apart instead of piled on each other
        labelLayout: all ? { hideOverlap: true } : { moveOverlap: 'shiftY', hideOverlap: false },
      }
      const latest = (rows: Record<string, unknown>[]) => {
        if (all || chart.vl !== 'auto') return rows
        const i = rows.map((d) => d.value !== null && d.value !== undefined).lastIndexOf(true)
        // a bar's number sits centred on it; a line's ends at its last point so it never runs off the chart
        if (i >= 0) rows[i] = { ...rows[i], label: l.type === 'bar' ? { show: true } : { show: true, align: 'right', offset: [4, 0] }, itemStyle: { ...((rows[i].itemStyle as object) ?? {}), opacity: 1 } }
        return rows
      }
      if (l.type === 'bar') { series.push({ ...base, data: latest(slots.map((_, i) => pt(i))) }); continue }
      // lines: the reported part solid, estimates as a dashed continuation from the last reported year
      series.push({ ...base, data: latest(slots.map((_, i) => pt(i, false))) })
      if (l.cells.some((x) => x?.est)) {
        const lastRep = slots.reduce((a, _, i) => (l.cells[i] && !l.cells[i]!.est && l.vals[i] !== null ? i : a), -1)
        series.push({ ...base, lineStyle: { width: 2.5, type: 'dashed' }, itemStyle: { ...base.itemStyle, opacity: 1 }, showSymbol: true, data: slots.map((_, i) => (i === lastRep ? { ...pt(i), hide: true } : pt(i, true))) })
      }
    }
    const xData = slots.map(model.label)
    // stacked: one grid per number, the same years under each other; only the bottom one has year labels
    const unitOfMi = (mi: number) => lines.find((l) => l.mi === mi)?.unit ?? 'num'
    const band = 100 / n
    const grids = stacked
      ? fields.map((_, mi) => ({ left: 8, right: 24, top: `${mi * band + 6}%`, height: `${band - 13}%`, containLabel: true }))
      : [{ left: 8, right: units.length > 2 ? 72 : 24, top: 28, bottom: 8, containLabel: true }]
    const xAxes = stacked
      ? fields.map((_, mi) => ({ ...o.xAxis, gridIndex: mi, type: 'category', data: xData, boundaryGap: true, axisLabel: { ...o.xAxis.axisLabel, show: mi === n - 1 } }))
      : { ...o.xAxis, type: 'category', data: xData, boundaryGap: true }
    const yAxes = stacked
      ? fields.map((fi, mi) => ({
          ...o.yAxis, gridIndex: mi, scale: mode !== 'value' || unitOfMi(mi) !== 'usd',
          name: fi.label, nameLocation: 'end', nameGap: 8, nameTextStyle: { align: 'left', color: c.text, fontWeight: 600, fontSize: 12 },
          axisLabel: { color: c.muted, fontSize: 11, formatter: (x: number) => fmt(x, unitOfMi(mi)) },
        }))
      : units.map((u, i) => ({ ...o.yAxis, position: i ? 'right' : 'left', offset: i > 1 ? 72 : 0, scale: mode !== 'value' || u !== 'usd', splitLine: i ? { show: false } : o.yAxis.splitLine, axisLabel: { color: c.muted, fontSize: 11, formatter: (x: number) => fmt(x, u) } }))
    return {
      ...o,
      grid: grids,
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      // title and legend are only drawn into a saved picture: on the page they are part of the page itself
      title: { show: false, text: title, left: 'center', top: 0, textStyle: { fontSize: 13, color: c.text } },
      legend: { show: false, left: 0, top: 22, textStyle: { color: c.text }, data: lines.map((l) => l.name) },
      xAxis: xAxes,
      yAxis: yAxes,
      tooltip: {
        ...o.tooltip, trigger: 'item',
        formatter: (p: { data: { value: number | null; hide?: boolean; cell?: Cell; span?: Span | null }; dataIndex: number; seriesName: string; marker: string; value: number; seriesIndex: number }) => {
          const d = p.data
          if (!d || d.value === null || d.value === undefined || d.hide || !d.cell) return ''
          const x = d.cell
          const l = lines.find((q) => q.name === p.seriesName)
          const per = x.est ? `${x.fiscal} · analyst estimate` : `${x.fiscal}${x.end ? ` · ended ${longDate(x.end)}` : ''}`
          const sp = d.span ? ` <span style="color:${c.muted}">(${d.span.pp ? `change ${signedPp(d.span.total)}` : `total ${signed(d.span.total)}${d.span.cagr !== null ? `, per year ${signed(d.span.cagr)}` : ''}`})</span>` : ''
          return `${period === 'FY' ? 'Calendar year' : 'Period'}: <b>${model.label(slots[p.dataIndex])}</b><br>Fiscal: <b>${per}</b><br>${p.marker}${p.seriesName}${sp}: <b>${fmt(p.value, l?.unit ?? 'num')}${x.est ? ' (E)' : ''}</b>` + (x.flag ? `<div style="color:#d97706;max-width:320px;white-space:normal">⚠ ${x.flag.replace(/</g, '&lt;')}</div>` : '')
        },
      },
      series,
    }
  }, [model, c, width, chart.vl, fields, mode, period, title, stacked])

  const csv = () => (model ? [csvLine(['Period', ...model.lines.map((l) => l.name)]), ...model.slots.map((k, i) => csvLine([model.label(k), ...model.lines.map((l) => l.vals[i] ?? '')]))].join('\r\n') : '')
  exporter.current = {
    title,
    csv,
    png: () => {
      const ch = inst.current && !inst.current.isDisposed() ? inst.current : null
      if (!ch) return null
      if (stacked) ch.setOption({ title: { show: true } })
      else ch.setOption({ title: { show: true }, legend: { show: true }, grid: { top: model && model.lines.length > 1 ? 64 : 40 } })
      const url = ch.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: c.card })
      if (stacked) ch.setOption({ title: { show: false } })
      else ch.setOption({ title: { show: false }, legend: { show: false }, grid: { top: 28 } })
      return url
    },
  }

  if (!tickers.length) return <div className="flex h-[420px] items-center justify-center text-muted-foreground">Add a company to start the chart.</div>
  const order = model ? (newest ? model.slots.map((_, i) => i).reverse() : model.slots.map((_, i) => i)) : []
  return (
    <>
      <div ref={box}>
        {fields.length > 1 && (
          <div className="mb-1 flex justify-end">
            <Segmented
              label="Layout"
              value={layout}
              onChange={(v) => setPref('ui2', 'cmp_layout', v)}
              options={[{ value: 'stack', icon: <Rows3 />, hint: 'A chart per number, under each other' }, { value: 'one', icon: <Square />, hint: 'All numbers in one chart (solid, dashed, dotted)' }]}
            />
          </div>
        )}
        {option ? <EChart key={stacked ? 'stack' : 'one'} option={option} onReady={(ch) => { inst.current = ch }} className={full ? 'h-[calc(100vh-330px)] min-h-[320px]' : stacked ? (fields.length > 2 ? 'h-[660px]' : 'h-[500px]') : 'h-[420px]'} /> : <div className={cn('flex items-center justify-center text-muted-foreground', full ? 'h-[calc(100vh-330px)]' : 'h-[420px]')}>{data.some((d) => d.isError) ? 'Could not load these numbers.' : 'Loading…'}</div>}
      </div>
      {model && <YearSlider years={model.yrs} estYears={model.estYears} from={model.yFrom} to={model.yTo} lockFrom={!!chart.from} onChange={onRange} />}
      {model && (
        <div className="mt-2 grid grid-cols-1 gap-x-8 gap-y-1 border-t pt-3 text-[13px] md:grid-cols-2">
          {model.lines.map((l) => (
            <div key={l.name} className="flex items-center gap-2">
              <Shape i={l.mi} color={l.color} />
              {!stacked && fields.length > 1 && <svg aria-hidden viewBox="0 0 22 4" className="h-1 w-5 shrink-0"><line x1="0" y1="2" x2="22" y2="2" stroke={l.color} strokeWidth="2.5" strokeDasharray={l.mi === 1 ? '5 3' : l.mi === 2 ? '1.5 3' : undefined} /></svg>}
              <span className="min-w-0 truncate font-medium">{l.name}</span>
              <span className="grow" />
              {l.span?.pp ? (
                <Hint label="Change in percentage points over the years shown"><span className={cn('num w-24 text-right font-semibold', l.span.total >= 0 ? 'text-up' : 'text-down')}>{signedPp(l.span.total)}</span></Hint>
              ) : l.span ? (
                <>
                  <Hint label="Total change over the years shown"><span className="num text-muted-foreground">{signed(l.span.total)}</span></Hint>
                  {l.span.cagr !== null && <Hint label="Growth per year over the years shown"><span className={cn('num w-24 text-right font-semibold', l.span.cagr >= 0 ? 'text-up' : 'text-down')}>{signed(l.span.cagr)} / yr</span></Hint>}
                </>
              ) : null}
            </div>
          ))}
        </div>
      )}
      {model && (model.hasEst || cal || model.flagged) && (
        <p className="mt-2 text-xs text-muted-foreground">
          {cal ? 'Calendar years: the four quarters that fall in January to December added up (balance sheet: the value at the end of the year). The current year is not complete yet: its lighter bar shows the last 12 months up to the latest quarter. ' : model.hasEst ? 'Faded bars and dashed lines marked (E) are analyst estimates for fiscal years not reported yet. Years sit in the calendar year in which the fiscal year ends. ' : ''}
          {model.flagged && <>A <span className="text-flag">▲</span> marks a value that did not pass a consistency check; hover it to see why.</>}
        </p>
      )}
      {model && !full && (
        <div className="-mx-4 mt-4 border-t px-4 pt-3">
          <div className="mb-2 flex items-center gap-2">
            <h2 className="text-[15px] font-semibold">Numbers in this chart</h2>
            <div className="grow" />
            <IconToggle on={newest} onChange={(v) => setPref('ui2', 'cmp_newest_first', v)} label={newest ? 'Newest first. Click for oldest first.' : 'Oldest first. Click for newest first.'}><ArrowLeftRight /></IconToggle>
          </div>
          <div ref={newest ? undefined : scrollToNewest} className="overflow-x-auto">
            <table className="tbl tight sticky-first">
              <thead>
                <tr>
                  <th />
                  {order.map((i) => <th key={model.slots[i]}>{model.label(model.slots[i])}</th>)}
                </tr>
              </thead>
              <tbody>
                {model.lines.map((l) => (
                  <tr key={l.name}>
                    <td><span className="flex items-center gap-2"><Shape i={l.mi} color={l.color} /><span className="font-medium">{l.name}</span></span></td>
                    {order.map((i) => <td key={i} className={cn(l.cells[i]?.est && 'text-muted-foreground italic')}>{l.vals[i] === null ? '–' : fmt(l.vals[i], l.unit)}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  )
}
