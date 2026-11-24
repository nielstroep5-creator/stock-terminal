import { useQueries, useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { MutableRefObject } from 'react'
import { EChart, baseOption, useChartColors } from '@/components/chart/EChart'
import type { echarts } from '@/components/chart/EChart'
import { api } from '@/lib/api'
import { fmt } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { Exporter } from './FinChart'
import { csvLine, sinceDate } from './state'
import type { Chart } from './state'

type Prices = { dates: string[]; close: number[]; adj: (number | null)[] }
type Intraday = Record<string, { t: number[]; close: number[] } | null>
type Key = string | number
type P = { value: number | null; px?: number }

const NY = 'America/New_York'
const day = (d: Key) => new Date(d + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
const when = (ts: Key) => new Date(+ts * 1000).toLocaleString('en-US', { timeZone: NY, month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
const roundPct = (r: number) => {
  const v = r * 100
  const a = Math.abs(v)
  return (v > 0 ? '+' : v < 0 ? '-' : '') + (a >= 100 ? Math.round(a).toLocaleString('en-US') : +a.toFixed(1)) + '%'
}
const sgn = (v: number) => (Math.abs(v) >= 10 ? roundPct(v) : (v > 0 ? '+' : '') + fmt(v, 'pct2'))
const UNITS: [string, number][] = [['day', 1], ['week', 1], ['week', 2], ['month', 1], ['month', 2], ['month', 3], ['month', 6], ['year', 1], ['year', 2], ['year', 5], ['year', 10]]

/** Share price or total return, every line at 0% on the same day. The x-axis has only trading days (or trading
    minutes for 1D / 5D). Drag the bar under the chart to zoom: every line then starts again at 0% on the first
    day in view. Log scale plots the growth multiple (0% = 1x, +100% = 2x), the labels still show the %. */
export function PriceChart({ chart, tickers, colors, full, exporter }: { chart: Chart; tickers: string[]; colors: string[]; full: boolean; exporter: MutableRefObject<Exporter | null> }) {
  const c = useChartColors()
  const range = chart.r
  const intra = range === '1D' || range === '5D'
  const log = chart.sc === 'log'
  const tk = tickers.join(',')
  const px = useQueries({ queries: tickers.map((t) => ({ queryKey: ['company', t, 'prices'], queryFn: () => api.get<Prices>(`/api/prices/${t}`).catch(() => ({ dates: [], close: [], adj: [] }) as Prices), staleTime: 60_000 })) })
  const id = useQuery({ queryKey: ['compare-intraday', tk, range], queryFn: () => api.get<Intraday>(`/api/intraday?tickers=${tk}&range=${range}`), enabled: intra && tickers.length > 0, retry: false, staleTime: 60_000 })
  const ready = px.every((p) => p.data) && (!intra || !id.isLoading)
  const stamp = px.map((p) => p.dataUpdatedAt).join()
  const inst = useRef<echarts.ECharts | null>(null)
  const [view, setView] = useState('')
  // the chart is thrown away while new prices load (e.g. after adding a company): never use a disposed one
  const live = () => (inst.current && !inst.current.isDisposed() ? inst.current : null)

  const model = useMemo(() => {
    if (!ready || !tickers.length) return null
    const pxs = px.map((p) => p.data as Prices)
    const maps = tickers.map(() => new Map<Key, { s: number; c: number }>())
    const prevClose: (number | undefined)[] = []
    if (intra) {
      tickers.forEach((t, i) => {
        const p = id.data?.[t]
        if (!p?.t?.length) return
        p.t.forEach((ts, j) => maps[i].set(ts, { s: p.close[j], c: p.close[j] }))
        if (range === '1D') { // 1D: change against the previous day's close
          const d0 = new Date(p.t[0] * 1000).toLocaleDateString('en-CA', { timeZone: NY })
          let k = pxs[i].dates.length - 1
          while (k >= 0 && pxs[i].dates[k] >= d0) k--
          if (k >= 0) prevClose[i] = pxs[i].close[k]
        }
      })
      if (!maps.some((m) => m.size)) return { error: 'Could not get intraday prices from Yahoo right now. Try again in a minute, or pick another range.' }
    } else {
      const have = pxs.filter((p) => p.dates.length)
      if (!have.length) return { error: 'No prices for these tickers.' }
      const lastDay = have.map((p) => p.dates[p.dates.length - 1]).sort().pop() as string
      // Max starts on the first day all tickers have; All shows each ticker's full history from its own first day
      const start = range === 'All' ? have.map((p) => p.dates[0]).sort()[0] : [sinceDate(range, lastDay), ...have.map((p) => p.dates[0])].sort().pop() as string
      tickers.forEach((_, i) => {
        const p = pxs[i]
        if (!p.dates.length) return
        const src = chart.pm === 'total' ? p.adj.map((v, j) => v ?? p.close[j]) : p.close
        let i0 = 0
        while (i0 + 1 < p.dates.length && p.dates[i0 + 1] <= start) i0++
        for (let j = i0; j < p.dates.length; j++) maps[i].set(p.dates[j], { s: src[j], c: p.close[j] })
      })
    }
    const cmp = (a: Key, b: Key) => (a < b ? -1 : a > b ? 1 : 0)
    const keys = [...new Set(maps.flatMap((m) => [...m.keys()]))].sort(cmp)
    const firstKey = maps.map((m) => (m.size ? [...m.keys()].sort(cmp)[0] : null))
    // the % of every line from the first day in view (or from its own first day, when it starts later)
    const build = (from: number): P[][] => maps.map((m, i) => {
      if (!m.size) return []
      let base: number | null = from === 0 && range === '1D' && prevClose[i] ? (prevClose[i] as number) : null
      for (let j = from; base === null && j >= 0; j--) if (m.has(keys[j])) base = m.get(keys[j])!.s
      if (base === null) base = m.get(firstKey[i] as Key)!.s
      return keys.map((k) => {
        const x = m.get(k)
        const r = x && base ? x.s / base - 1 : null
        return r === null ? { value: null } : { value: log ? Math.log10(Math.max(1 + r, 1e-4)) : r, px: x!.c }
      })
    })
    const info = keys.map((k) => {
      if (intra) {
        const p = Object.fromEntries(new Intl.DateTimeFormat('en-US', { timeZone: NY, year: 'numeric', month: '2-digit', day: '2-digit', hour: 'numeric', hourCycle: 'h23' }).formatToParts(new Date(+k * 1000)).map((x) => [x.type, x.value]))
        return { day: `${p.year}-${p.month}-${p.day}`, hour: +p.hour, y: 0, m: 0, wk: 0, t: 0 }
      }
      const d = new Date(k + 'T00:00:00')
      return { day: '', hour: 0, y: d.getFullYear(), m: d.getMonth(), wk: Math.floor((d.getTime() / 864e5 + 3) / 7), t: d.getTime() }
    })
    return { keys, firstKey, build, info }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, stamp, id.data, tk, range, chart.pm, log])

  const label = (k: Key) => (intra ? when(k) : day(k))
  const what = chart.pm === 'total' && !intra ? 'Total return (dividends reinvested)' : 'Price change'
  const m = model && !('error' in model) ? model : null
  const fullTitle = !m ? what : intra ? `${what} · ${range} · ${range === '1D' ? 'vs. the previous close, ' : ''}${label(m.keys[0])} – ${label(m.keys[m.keys.length - 1])}` : range === 'All' ? `${what} · every line from its own first day` : `${what} · ${range === 'Max' ? 'the longest shared history' : range} · since ${day(m.keys[0])}`

  // what depends on the part in view: grid lines of the log axis and the dates under the chart
  const parts = useMemo(() => {
    if (!m) return null
    const { keys, info } = m
    const disp = (v: number) => (log ? 10 ** v - 1 : v)
    const logAxis = (ds: P[][], a: number, b: number) => {
      let lo = Infinity
      let hi = -Infinity
      for (const d of ds) for (const x of d.slice(a, b + 1)) if (x.value !== null) { lo = Math.min(lo, x.value); hi = Math.max(hi, x.value) }
      if (lo === Infinity) { lo = -0.1; hi = 0.1 }
      const pad = Math.max((hi - lo) * 0.06, 0.01)
      lo -= pad
      hi += pad
      const cand = new Set<number>()
      if (hi - lo < 0.4) { // up to about 2.5x: round % steps
        const rLo = 10 ** lo - 1
        const rHi = 10 ** hi - 1
        const step = [0.05, 0.1, 0.2, 0.25, 0.5].find((st) => (rHi - rLo) / st <= 7) || 1
        for (let r = Math.ceil(rLo / step) * step; r <= rHi + 1e-9; r += step) cand.add(1 + Math.round(r * 1000) / 1000)
      } else { // wider: 1-2-5 multiples
        for (let k = -4; k <= 7; k++) for (const f of [1, 2, 5]) cand.add(f * 10 ** k)
        if (hi - lo < 1.6) for (const x of [1.5, 3, 4, 0.75, 0.8, 0.6]) cand.add(x)
      }
      let ticks = [...cand].filter((x) => Math.log10(x) >= lo && Math.log10(x) <= hi).sort((x, y) => x - y)
      while (ticks.length > 8) ticks = ticks.filter((x, i) => i % 2 === 0 || x === 1)
      return { min: lo, max: hi, ticks }
    }
    const tickSeries = (ax: { ticks: number[] }) => ({ name: '', type: 'line', data: [], silent: true, tooltip: { show: false }, markLine: { symbol: 'none', silent: true, animation: false, lineStyle: { color: c.border, type: 'solid', width: 1 }, label: { position: 'end', color: c.muted, fontSize: 11 }, data: ax.ticks.map((x) => ({ yAxis: Math.log10(x), label: { formatter: roundPct(x - 1) } })) } })
    // dates on calendar boundaries with a step that fits the days in view and the width of the chart
    const tickPlan = (a: number, b: number, w: number) => {
      const room = Math.max(4, Math.floor((w - 160) / 80))
      let unit = 'iday'
      let n = 1
      if (intra && range === '1D') { unit = 'hour'; n = Math.ceil(7 / room) }
      else if (!intra) {
        const days = (info[b].t - info[a].t) / 864e5
        const count: Record<string, number> = { day: b - a + 1, week: days / 7, month: days / 30.44, year: days / 365.25 }
        ;[unit, n] = UNITS.find(([u, k]) => count[u] / k <= room) || ['year', 25]
      }
      const ticks = new Map<string, number>()
      for (let i = Math.max(1, a); i <= b; i++) {
        const p = info[i - 1]
        const q = info[i]
        const on = unit === 'hour' ? q.hour !== p.hour && q.hour % n === 0 : unit === 'iday' ? q.day !== p.day : unit === 'day' ? true : unit === 'week' ? q.wk !== p.wk && q.wk % n === 0 : unit === 'month' ? q.m !== p.m && (q.y * 12 + q.m) % n === 0 : q.y !== p.y && q.y % n === 0
        if (on) ticks.set(String(keys[i]), i)
      }
      const text = (i: number) => {
        const k = keys[i]
        if (unit === 'hour') return new Date(+k * 1000).toLocaleString('en-US', { timeZone: NY, hour: 'numeric' })
        if (unit === 'iday') return new Date(+k * 1000).toLocaleString('en-US', { timeZone: NY, weekday: 'short', month: 'short', day: 'numeric' })
        const d = new Date(k + 'T00:00:00')
        if (unit === 'year') return String(d.getFullYear())
        if (unit === 'month') return d.toLocaleDateString('en-US', { month: 'short' }) + " '" + String(d.getFullYear()).slice(2)
        return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
      }
      const show = (_: number, v: string) => ticks.has(String(v))
      return { axisLabel: { color: c.muted, fontSize: 11, hideOverlap: true, interval: show, formatter: (v: string) => (ticks.has(String(v)) ? text(ticks.get(String(v)) as number) : '') }, axisTick: { interval: show } }
    }
    return { disp, logAxis, tickSeries, tickPlan }
  }, [m, log, intra, range, c])

  const option = useMemo(() => {
    if (!m || !parts) return null
    const o = baseOption(c)
    const { keys, firstKey } = m
    const datas = m.build(0)
    const ax = log ? parts.logAxis(datas, 0, keys.length - 1) : null
    // end labels never cover each other: from the last values, estimate where each label lands (in pixels)
    // and push labels that are closer than one label height apart up or down
    const lastOf = (d: unknown[]) => {
      for (let k = d.length - 1; k >= 0; k--) {
        const v = (d[k] as { value?: number | null } | null)?.value ?? (typeof d[k] === 'number' ? (d[k] as number) : null)
        if (v !== null && v !== undefined) return v
      }
      return null
    }
    const ends = datas.map((d) => lastOf(d as unknown[]))
    const vals = datas.flatMap((d) => (d as unknown[]).map((x) => (x as { value?: number | null } | null)?.value ?? (typeof x === 'number' ? x : null)).filter((v): v is number => v !== null && v !== undefined))
    const lo = Math.min(...vals)
    const hi = Math.max(...vals)
    const H = Math.max(200, (live()?.getHeight() ?? 420) - 70)
    const px = (v: number) => (log ? Math.log(Math.max(v, 1e-9)) : v)
    const toY = (v: number) => (hi > lo ? ((px(hi) - px(v)) / (px(hi) - px(lo) || 1)) * H : 0)
    const order = ends.map((v, i) => [v === null ? null : toY(v), i] as const).filter((x): x is readonly [number, number] => x[0] !== null).sort((a, b) => a[0] - b[0])
    const shift: number[] = tickers.map(() => 0)
    let prev = -Infinity
    for (const [y, i] of order) {
      const at = Math.max(y, prev + 24)
      shift[i] = at - y
      prev = at
    }
    const series: Record<string, unknown>[] = tickers.map((t, i) => ({
      name: t, type: 'line', data: datas[i], showSymbol: false, connectNulls: true, lineStyle: { width: 2 }, itemStyle: { color: colors[i] },
      endLabel: { show: true, formatter: (p: { value: number }) => sgn(parts.disp(p.value)), backgroundColor: colors[i], color: '#fff', fontWeight: 700, fontSize: 12, padding: [4, 8], borderRadius: 6, distance: 8, offset: [0, shift[i]] },
    }))
    if (ax) series.push(parts.tickSeries(ax))
    return {
      ...o,
      grid: { left: 8, right: log ? 150 : 84, top: 20, bottom: 44, containLabel: true },
      title: { show: false, text: fullTitle, left: 'center', top: 0, textStyle: { fontSize: 13, color: c.text } },
      legend: { show: false, left: 0, top: 22, textStyle: { color: c.text }, data: tickers },
      dataZoom: [{ type: 'slider', height: 8, bottom: 14, brushSelect: false, filterMode: 'filter', showDetail: false, showDataShadow: false, borderColor: 'transparent', backgroundColor: c.border, fillerColor: c.primary, borderRadius: 4, handleIcon: 'path://M512 512m-448 0a448 448 0 1 0 896 0a448 448 0 1 0 -896 0Z', handleSize: 18, handleStyle: { color: '#ffffff', borderColor: c.primary, borderWidth: 4, shadowBlur: 3, shadowColor: 'rgba(0,0,0,.25)' }, moveHandleSize: 0 }],
      xAxis: { ...o.xAxis, type: 'category', data: keys, boundaryGap: false, ...parts.tickPlan(0, keys.length - 1, live()?.getWidth() ?? 1000) },
      yAxis: ax ? { ...o.yAxis, position: 'right', scale: false, min: ax.min, max: ax.max, axisLabel: { show: false }, splitLine: { show: false } } : { ...o.yAxis, position: 'right', axisLabel: { color: c.muted, fontSize: 11, formatter: (x: number) => roundPct(x) } },
      tooltip: {
        ...o.tooltip, padding: [10, 14],
        formatter: (ps: { value: number | null; dataIndex: number; seriesName: string; color: string; data: P }[]) => {
          ps = ps.filter((p) => p.value !== null && p.value !== undefined && p.seriesName).sort((a, b) => (b.value as number) - (a.value as number))
          if (!ps.length) return ''
          return `<div style="color:${c.muted};margin-bottom:4px">${label(keys[ps[0].dataIndex])}</div>` + ps.map((p) => {
            const i = tickers.indexOf(p.seriesName)
            const r = parts.disp(p.value as number)
            const own = range === 'All' && firstKey[i] !== keys[0]
            return `<div style="display:flex;gap:8px;align-items:center"><span style="width:9px;height:9px;border-radius:2px;background:${p.color}"></span><b>${p.seriesName}</b><span style="margin-left:auto;padding-left:12px">${p.data.px ? p.data.px.toFixed(2) : ''}</span><span style="color:${r >= 0 ? c.up : c.down}">(${sgn(r)})</span>${own ? `<span style="color:${c.muted}">since ${day(firstKey[i] as Key)}</span>` : ''}</div>`
          }).join('')
        },
      },
      series,
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [m, parts, c, colors, log])

  // dragging the zoom bar: every line starts again at 0% on the first day in view
  useEffect(() => { setView('') }, [option])
  const ready2 = (ch: echarts.ECharts) => { inst.current = ch }
  useEffect(() => {
    const ch = live()
    if (!ch || !m || !parts) return
    const onZoom = () => {
      const z = (ch.getOption() as { dataZoom: { start: number; end: number; startValue: number; endValue: number }[] }).dataZoom[0]
      const whole = z.start <= 0.01 && z.end >= 99.99
      const a = Math.round(z.startValue)
      const b = Math.round(z.endValue)
      const ds = m.build(whole ? 0 : a)
      const ax = log ? parts.logAxis(ds, a, b) : null
      ch.setOption({ ...(ax ? { yAxis: { min: ax.min, max: ax.max } } : {}), xAxis: parts.tickPlan(a, b, ch.getWidth()), series: [...ds.map((data) => ({ data })), ...(ax ? [parts.tickSeries(ax)] : [])] })
      setView(whole ? '' : `${what} · ${label(m.keys[a])} – ${label(m.keys[b])}`)
    }
    ch.on('datazoom', onZoom)
    return () => { ch.off('datazoom', onZoom) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [option])

  exporter.current = {
    title: fullTitle,
    csv: () => {
      if (!m) return ''
      const ds = m.build(0)
      return [csvLine(['Date', ...tickers]), ...m.keys.map((k, i) => csvLine([intra ? when(k) : k, ...ds.map((d) => (d[i]?.value === null || d[i] === undefined ? '' : +(((log ? 10 ** (d[i].value as number) - 1 : (d[i].value as number)) * 100).toFixed(4)))) ]))].join('\r\n')
    },
    png: () => {
      const ch = live()
      if (!ch) return null
      ch.setOption({ title: { show: true, text: view || fullTitle }, legend: { show: true }, grid: { top: 64 } })
      const url = ch.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: c.card, excludeComponents: ['dataZoom'] })
      ch.setOption({ title: { show: false }, legend: { show: false }, grid: { top: 20 } })
      return url
    },
  }

  const height = full ? 'h-[calc(100vh-200px)] min-h-[320px]' : 'h-[460px]'
  if (!tickers.length) return <div className={cn('flex items-center justify-center text-muted-foreground', height)}>Add a company or ETF to start the chart.</div>
  if (!option) return <div className={cn('flex items-center justify-center px-6 text-center text-muted-foreground', height)}>{model && 'error' in model ? model.error : 'Loading…'}</div>
  return (
    <>
      <div className="mb-1 text-center text-[13px] font-semibold">{view || fullTitle}</div>
      <div className="mb-1 flex flex-wrap justify-center gap-x-4 gap-y-1 text-[13px]">
        {tickers.map((t, i) => <span key={t} className="inline-flex items-center gap-1.5 font-medium"><span className="size-2.5 rounded-sm" style={{ background: colors[i] }} />{t}</span>)}
      </div>
      <EChart option={option} onReady={ready2} className={height} />
    </>
  )
}
