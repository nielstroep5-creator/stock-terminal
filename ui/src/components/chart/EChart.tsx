import { BarChart, CustomChart, LineChart, ScatterChart } from 'echarts/charts'
import { DataZoomSliderComponent, GridComponent, LegendComponent, MarkAreaComponent, MarkLineComponent, MarkPointComponent, TitleComponent, TooltipComponent } from 'echarts/components'
import * as echarts from 'echarts/core'
import type { EChartsCoreOption } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { useEffect, useMemo, useRef, useState } from 'react'
import { cn } from '@/lib/utils'

echarts.use([LineChart, BarChart, CustomChart, ScatterChart, GridComponent, TooltipComponent, MarkPointComponent, MarkLineComponent, MarkAreaComponent, DataZoomSliderComponent, LegendComponent, TitleComponent, CanvasRenderer])

export { echarts }
export type ChartOption = EChartsCoreOption

/** The colours of the current theme, for drawing charts (a canvas cannot read CSS by itself). */
export type ChartColors = {
  text: string
  muted: string
  border: string
  card: string
  primary: string
  up: string
  down: string
  series: string[]
}

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim()

/** Re-reads the colours when the theme changes, so charts redraw in light / dark right away. */
export function useChartColors(): ChartColors {
  const [theme, setTheme] = useState(document.documentElement.dataset.theme)
  useEffect(() => {
    const o = new MutationObserver(() => setTheme(document.documentElement.dataset.theme))
    o.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => o.disconnect()
  }, [])
  return useMemo(
    () => ({
      text: css('--foreground'),
      muted: css('--muted-foreground'),
      border: css('--border'),
      card: css('--popover'),
      primary: css('--primary'),
      up: css('--up'),
      down: css('--down'),
      series: ['--series-1', '--series-2', '--series-3', '--series-4', '--series-5', '--series-6'].map(css),
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [theme],
  )
}

/** What every chart shares: font, quiet axes, a tooltip that looks like the rest of the app. */
export function baseOption(c: ChartColors) {
  return {
    color: c.series,
    animation: false,
    textStyle: { fontFamily: "'Inter Variable', system-ui, sans-serif", color: c.muted },
    grid: { left: 8, right: 12, top: 16, bottom: 4, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: c.card,
      borderColor: c.border,
      padding: [8, 10],
      textStyle: { color: c.text, fontSize: 12 },
      axisPointer: { lineStyle: { color: c.border }, shadowStyle: { color: c.border, opacity: 0.35 } },
    },
    xAxis: { axisLine: { lineStyle: { color: c.border } }, axisTick: { show: false }, axisLabel: { color: c.muted, fontSize: 11 }, splitLine: { show: false } },
    yAxis: { axisLine: { show: false }, axisLabel: { color: c.muted, fontSize: 11 }, splitLine: { lineStyle: { color: c.border } }, scale: true },
  }
}

/** Bars of one period together fill about 70% of that period's width, the same on every chart. */
/** Diagonal stripes over a bar: an analyst estimate, not a reported number. */
export const STRIPES = { symbol: 'rect', symbolSize: 1, dashArrayX: [1, 0], dashArrayY: [3, 4], rotation: -Math.PI / 4, color: 'rgba(255,255,255,0.55)' }

export const BAR_FIT = { barCategoryGap: '30%', barGap: '12%', barMaxWidth: 96 }

/* Hover rules, the same on every chart:
 * - several numbers (revenue, gross profit, ...): hovering one lifts that number in every period and dims the rest (FOCUS),
 *   and the tooltip shows it bright (axisTip + <EChart hoverTip>)
 * - one number per period (dividend per year, an ETF's yearly return): hovering anywhere in a period lifts that period's
 *   bar and dims the other periods (FOCUS_SELF + <EChart focusPeriod>)
 * - bar charts get no grey pointer line (BAR_TIP): the lifted bar already shows where you are; line charts keep it */
const BLUR = { itemStyle: { opacity: 0.18 }, lineStyle: { opacity: 0.18 }, label: { opacity: 0.25 } }
export const FOCUS = { emphasis: { focus: 'series' }, blur: BLUR, triggerLineEvent: true }
// silent: the period under the mouse is lifted by <EChart focusPeriod>, not by the bar itself, so the gap between bars counts too
export const FOCUS_SELF = { emphasis: { focus: 'self' }, blur: BLUR, silent: true }
/** Spread into a bar chart's tooltip: no pointer line through the hovered period. */
export const BAR_TIP = { axisPointer: { type: 'none' } }

// the series under the mouse right now (one chart is hovered at a time), read by axisTip
let hovered: string | null = null
const esc = (x: string) => x.replace(/&/g, '&amp;').replace(/</g, '&lt;')
type TipParam = { seriesName: string; seriesIndex: number; color: string; value: unknown; axisValueLabel: string }

/** A tooltip listing every series of a period, with the hovered one bright and the others dimmed.
 *  Use on a chart drawn with <EChart hoverTip>. `show` formats a value of series i. */
export function axisTip(c: ChartColors, show: (i: number, v: number) => string) {
  return (ps: TipParam | TipParam[]) => {
    const list = Array.isArray(ps) ? ps : [ps]
    if (!list.length) return ''
    const on = list.some((p) => p.seriesName === hovered) ? hovered : null
    const rows = list.map((p) => {
      const v = Array.isArray(p.value) ? p.value[1] : p.value
      const val = typeof v === 'number' && Number.isFinite(v) ? show(p.seriesIndex, v) : '–'
      const me = on !== null && p.seriesName === on
      const dim = on !== null && !me
      return `<div style="display:flex;align-items:center;gap:8px;margin-top:4px;opacity:${dim ? 0.4 : 1}">`
        + `<span style="width:9px;height:9px;border-radius:50%;background:${p.color};flex:none"></span>`
        + `<span style="flex:1;font-weight:${me ? 600 : 400}">${esc(p.seriesName)}</span>`
        + `<span style="margin-left:20px;font-weight:700">${val}</span></div>`
    })
    return `<div style="color:${c.text}"><div style="margin-bottom:2px">${esc(list[0].axisValueLabel ?? '')}</div>${rows.join('')}</div>`
  }
}

/** One ECharts chart that follows the size of its box. Give the box a height with className. */
export function EChart({ option, className, onReady, hoverTip, focusPeriod }: { option: ChartOption; className?: string; /** Called once with the chart itself, for the few pages that need more (export a picture, follow the zoom). */ onReady?: (chart: echarts.ECharts) => void; /** The tooltip (axisTip) follows which series is hovered. */ hoverTip?: boolean; /** Lift the bars of the period under the mouse (FOCUS_SELF series). */ focusPeriod?: boolean }) {
  const box = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    if (!box.current) return
    const c = echarts.init(box.current, null, { renderer: 'canvas' })
    chart.current = c
    onReady?.(c)
    if (hoverTip) {
      type Ev = { componentType?: string; seriesName?: string; event?: { offsetX: number; offsetY: number } }
      // redraw the open tooltip at the mouse, so the bright row follows the mouse within one period
      const redraw = (e: Ev) => e.event && c.dispatchAction({ type: 'showTip', x: e.event.offsetX, y: e.event.offsetY })
      c.on('mouseover', (e: Ev) => { if (e.componentType === 'series') { hovered = e.seriesName ?? null; redraw(e) } })
      c.on('mouseout', (e: Ev) => { if (e.componentType === 'series') { hovered = null; redraw(e) } })
      c.on('globalout', () => { hovered = null })
    }
    if (focusPeriod) {
      let at: number | null = null
      const lift = (i: number | null) => {
        if (i === at) return
        at = i
        c.dispatchAction({ type: 'downplay' })
        const series = (c.getOption().series ?? []) as { type?: string }[]
        const bars = series.map((s, k) => (s.type === 'bar' ? k : -1)).filter((k) => k >= 0)
        if (i !== null && bars.length) c.dispatchAction({ type: 'highlight', seriesIndex: bars, dataIndex: i })
      }
      type AxisEv = { axesInfo?: { axisDim: string; value: number }[] }
      c.on('updateAxisPointer', (ev: unknown) => { const x = (ev as AxisEv).axesInfo?.find((a) => a.axisDim === 'x'); lift(x ? +x.value : null) })
      c.on('globalout', () => lift(null))
    }
    const ro = new ResizeObserver(() => c.resize())
    ro.observe(box.current)
    return () => {
      ro.disconnect()
      c.dispose()
      chart.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    // focusPeriod: nothing in the chart reacts to the mouse by itself (hovering the growth line used to undo the
    // lifted year); the period under the mouse is lifted by the handler above
    const o = option as { series?: Record<string, unknown>[] }
    chart.current?.setOption(focusPeriod && Array.isArray(o.series) ? { ...option, series: o.series.map((x) => ({ ...x, silent: true })) } : option, true)
  }, [option])

  return <div ref={box} className={cn('h-64 w-full', className)} />
}
