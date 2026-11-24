import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import type { ReactNode } from 'react'
import { Card } from '@/components/app/Page'
import { BAR_FIT, BAR_TIP, EChart, FOCUS, FOCUS_SELF, STRIPES, axisTip, baseOption, useChartColors } from '@/components/chart/EChart'
import type { ChartColors } from '@/components/chart/EChart'
import { Hint } from '@/components/ui/tooltip'
import { api } from '@/lib/api'
import { big, day, fmt, money, pctSigned, tone } from '@/lib/format'
import { cn } from '@/lib/utils'

type N = number | null | undefined
type Est = { avg?: N; low?: N; high?: N; year_ago?: N; growth?: N; analysts?: N; derived?: boolean }
type Annual = { label: string; est: boolean; analysts?: N; eps_adjusted?: N } & Record<string, N | string | boolean>
type Rec = { label: string } & Record<string, number | string>
type AnalystData = {
  has_coverage: boolean
  target: { current: number; mean?: N; median?: N; low?: N; high?: N; analysts?: N; upside: Record<string, N> }
  rating: { mean?: N; analysts?: N }
  calendar: { earnings_date?: string | null; earnings_upcoming?: boolean; earnings_eps?: N; earnings_revenue?: N }
  summary: { forward_pe?: N; peg?: N }
  recommendations: Rec[]
  estimates: { kind: string; rel: string; label: string; eps?: Est; revenue?: Est }[]
  annual: Annual[]
  eps_base?: { fy: number; kind: string; value?: N; gaap?: N; source?: string; reason?: string } | null
  basis?: Record<string, { fy: string; analysts: number; reported: number } | undefined> | null
  dropped?: { key: string; label: string }[]
  eps_trend: { label: string; current?: N; d7?: N; d30?: N; d90?: N }[]
  revisions: { label: string; up7?: N; up30?: N; down7?: N; down30?: N }[]
  surprises: { label: string; quarter_end: string; estimate?: N; actual?: N; surprise?: N }[]
  actions: { date: string; firm: string; action?: string; from_grade?: string; to_grade?: string; pt_current?: N; pt_prior?: N }[]
  growth: { period: string; label: string; stock?: N; index?: N }[]
  history: Record<string, { date: string; eps?: N }[]>
  snapshots: number
  snapshot_date: string
}

const REC: [string, string, string][] = [
  ['strongBuy', 'Strong buy', 'var(--r-sb)'],
  ['buy', 'Buy', 'var(--r-b)'],
  ['hold', 'Hold', 'var(--r-h)'],
  ['sell', 'Sell', 'var(--r-s)'],
  ['strongSell', 'Strong sell', 'var(--r-ss)'],
]
const ACTION: Record<string, string> = { up: 'Upgrade', down: 'Downgrade', main: 'Maintains', init: 'Initiates', reit: 'Reiterates' }
const has = (v: N): v is number => v !== null && v !== undefined
const ratingText = (m: N) => (!has(m) ? '–' : m < 1.5 ? 'Strong buy' : m < 2.5 ? 'Buy' : m < 3.5 ? 'Hold' : m < 4.5 ? 'Sell' : 'Strong sell')
const eps = (v: N) => fmt(v, 'usdps')
const rev = (v: N) => (has(v) ? big(v) : '–')
const Signed = ({ v }: { v: N }) => (has(v) ? <span className={tone(v)}>{pctSigned(v, 1)}</span> : <>–</>)
const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim()

function H2({ children }: { children: ReactNode }) {
  return <h2 className="text-[15px] font-semibold">{children}</h2>
}
function Note({ children, warn }: { children: ReactNode; warn?: boolean }) {
  return <p className={cn('mt-2 text-xs text-muted-foreground', warn && 'text-flag')}>{children}</p>
}
function Top({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Card className="p-4">
      <div className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">{label}</div>
      {children}
    </Card>
  )
}

/** Reported bars, estimate bars with a low to high whisker, and a growth line. */
function annualOption(c: ChartColors, annual: Annual[], k: 'revenue' | 'eps') {
  const o = baseOption(c)
  const f = (x: N) => (k === 'revenue' ? rev(x) : eps(x))
  const num = (a: Annual, key: string) => a[key] as N
  const whisk = annual.map((a, i) => (a.est && has(num(a, k + '_low')) ? [i, num(a, k + '_low'), num(a, k + '_high')] : null)).filter(Boolean)
  const adj = k === 'eps' ? annual.map((a, i) => (!a.est && has(a.eps_adjusted) ? [i, a.eps_adjusted] : null)).filter(Boolean) : []
  const gc = css('--r-h')
  type Api = { coord: (p: number[]) => number[]; value: (i: number) => number; size: (p: number[]) => number[] }
  return {
    ...o,
    xAxis: { ...o.xAxis, type: 'category', data: annual.map((a) => a.label.replace(/^FY(\d\d)(\d\d)/, annual.length > 9 ? 'FY$2' : 'FY$1$2') + (a.est ? 'E' : '')), axisLabel: { ...o.xAxis.axisLabel, interval: 0 } },
    yAxis: [
      { ...o.yAxis, scale: false, axisLabel: { ...o.yAxis.axisLabel, formatter: (x: number) => f(x) } },
      { ...o.yAxis, scale: false, position: 'right', splitLine: { show: false }, axisLabel: { ...o.yAxis.axisLabel, formatter: (x: number) => fmt(x, 'pct') } },
    ],
    tooltip: {
      ...o.tooltip, ...BAR_TIP,
      formatter: (ps: { seriesType: string; dataIndex: number }[]) => {
        const a = annual[(ps.find((x) => x.seriesType !== 'custom') ?? ps[0]).dataIndex]
        return (
          `<b>${a.label}${a.est ? ' (estimate)' : ''}</b><br>${k === 'revenue' ? 'Revenue' : 'EPS'}: <b>${f(num(a, k))}</b><br>Growth: ${fmt(num(a, k + '_growth'), 'pct')}` +
          (k === 'eps' && !a.est && has(a.eps_adjusted) ? `<br>Adjusted EPS: <b>${f(a.eps_adjusted)}</b> (analyst basis)` : '') +
          (a.est ? `<br>Range: ${f(num(a, k + '_low'))} – ${f(num(a, k + '_high'))}<br>${a.analysts ?? '–'} analysts` : '')
        )
      },
    },
    series: [
      { name: 'Reported', type: 'bar', ...BAR_FIT, ...FOCUS_SELF, itemStyle: { color: c.primary, borderRadius: [3, 3, 0, 0] }, data: annual.map((a) => (a.est ? null : num(a, k))) },
      { name: 'Estimate', type: 'bar', ...BAR_FIT, ...FOCUS_SELF, barGap: '-100%', itemStyle: { color: c.primary + 'b3', decal: STRIPES, borderRadius: [3, 3, 0, 0] }, data: annual.map((a) => (a.est ? num(a, k) : null)) },
      {
        name: 'Low – high', type: 'custom', z: 10, data: whisk,
        renderItem: (_p: unknown, a: Api) => {
          const lo = a.coord([a.value(0), a.value(1)])
          const hi = a.coord([a.value(0), a.value(2)])
          const st = { stroke: c.text, lineWidth: 1.2 }
          return { type: 'group', children: [
            { type: 'line', shape: { x1: lo[0], y1: lo[1], x2: hi[0], y2: hi[1] }, style: st },
            { type: 'line', shape: { x1: lo[0] - 6, y1: lo[1], x2: lo[0] + 6, y2: lo[1] }, style: st },
            { type: 'line', shape: { x1: hi[0] - 6, y1: hi[1], x2: hi[0] + 6, y2: hi[1] }, style: st },
          ] }
        },
      },
      ...(adj.length
        ? [{
            name: 'Adjusted EPS', type: 'custom', z: 11, data: adj,
            renderItem: (_p: unknown, a: Api) => {
              const p = a.coord([a.value(0), a.value(1)])
              const w = Math.min(a.size([1, 0])[0] * 0.3, 17) + 4
              const shape = { x1: p[0] - w, y1: p[1], x2: p[0] + w, y2: p[1] }
              return { type: 'group', children: [
                { type: 'line', shape, style: { stroke: css('--card'), lineWidth: 4.5 } },
                { type: 'line', shape, style: { stroke: c.text, lineWidth: 2 } },
              ] }
            },
          }]
        : []),
      { name: 'Growth', type: 'line', yAxisIndex: 1, symbolSize: 5, lineStyle: { width: 1.6, color: gc }, itemStyle: { color: gc }, data: annual.map((a) => num(a, k + '_growth')) },
    ],
  }
}

function Legend({ items }: { items: [string, ReactNode][] }) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
      {items.map(([name, mark]) => (
        <span key={name} className="inline-flex items-center gap-1.5">
          {mark}
          {name}
        </span>
      ))}
    </div>
  )
}

function AnnualCard({ t, d, k }: { t: string; d: AnalystData; k: 'revenue' | 'eps' }) {
  const c = useChartColors()
  const option = useMemo(() => annualOption(c, d.annual, k), [c, d.annual, k])
  const f = k === 'revenue' ? rev : eps
  const reported = d.annual.filter((x) => !x.est).length
  const years = d.annual.filter((a, i) => a.est || i >= reported - 4)
  const num = (a: Annual, key: string) => a[key] as N
  const cell = (a: Annual) => cn(a.est && 'bg-accent/40')
  const e = d.eps_base
  const b = d.basis?.[k]
  return (
    <Card className="min-w-0">
      <div className="flex flex-wrap items-center gap-3 px-4 pt-3">
        <H2>{k === 'revenue' ? 'Revenue' : 'Earnings per share'}</H2>
        <div className="grow" />
        <Legend
          items={[
            ['Reported', <span key="r" className="size-2.5 rounded-[3px] bg-primary" />],
            ['Estimate', <span key="e" className="size-2.5 rounded-[3px] border border-dashed border-primary bg-primary/25" />],
            ...(k === 'eps' && d.annual.some((a) => !a.est && has(a.eps_adjusted)) ? [['Adjusted EPS', <span key="a" className="h-0.5 w-3 bg-foreground" />] as [string, ReactNode]] : []),
            ['Growth', <span key="g" className="h-0.5 w-3" style={{ background: 'var(--r-h)' }} />],
          ]}
        />
      </div>
      <div className="px-4 pt-1 pb-2">
        <EChart focusPeriod option={option} className="h-[240px]" />
      </div>
      <div className="overflow-x-auto border-t">
        <table className="tbl">
          <thead>
            <tr>
              <th>Fiscal year</th>
              {years.map((a) => (
                <th key={a.label} className={cell(a)}>
                  {a.label}
                  {a.est && <span className="ml-0.5 text-[10px] font-semibold text-primary">E</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr className="font-semibold">
              <td>{k === 'revenue' ? 'Revenue' : 'EPS'}</td>
              {years.map((a) => (
                <td key={a.label} className={cell(a)}>{f(num(a, k))}</td>
              ))}
            </tr>
            {k === 'eps' && (
              <tr className="text-muted-foreground">
                <td>EPS (adjusted)</td>
                {years.map((a) => (
                  <td key={a.label} className={cell(a)}>{!a.est && has(a.eps_adjusted) ? f(a.eps_adjusted) : '–'}</td>
                ))}
              </tr>
            )}
            <tr>
              <td>Growth</td>
              {years.map((a) => (
                <td key={a.label} className={cell(a)}><Signed v={num(a, k + '_growth')} /></td>
              ))}
            </tr>
            <tr className="text-xs text-muted-foreground">
              <td>Range (est.)</td>
              {years.map((a) => (
                <td key={a.label} className={cn(cell(a), 'whitespace-nowrap')}>{a.est && has(num(a, k + '_low')) ? `${f(num(a, k + '_low'))} – ${f(num(a, k + '_high'))}` : ''}</td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
      <div className="px-4 pb-3">
        {k === 'eps' ? (
          <Note warn={e?.kind === 'gaap'}>
            History is GAAP (SEC). Estimates and growth to the first estimate year are adjusted (analyst basis).
            {e && (e.kind === 'adjusted'
              ? ` Growth to FY${e.fy + 1}E is measured from adjusted FY${e.fy} EPS of ${eps(e.value)} (GAAP ${eps(e.gaap)}; source: ${e.source}).`
              : ` No reliable adjusted EPS for FY${e.fy} (${e.reason}), so growth to FY${e.fy + 1}E uses GAAP EPS of ${eps(e.gaap)} and mixes the two definitions.`)}
          </Note>
        ) : null}
        {b && k === 'revenue' && (
          <Note warn>
            Analysts use a different revenue definition for {t} (their {b.fy}: {rev(b.analysts)}, reported: {rev(b.reported)}), so estimate bars and reported bars are not directly comparable. Growth for estimate years uses the analysts' own base.
          </Note>
        )}
      </div>
    </Card>
  )
}

export function Estimates({ t }: { t: string }) {
  const c = useChartColors()
  const q = useQuery({ queryKey: ['company', t, 'analyst'], queryFn: () => api.get<AnalystData>(`/api/analyst/${t}?live=1`) })
  const d = q.data

  const recOption = useMemo(() => {
    if (!d?.has_coverage) return null
    const o = baseOption(c)
    return {
      ...o,
      xAxis: { ...o.xAxis, type: 'category', data: d.recommendations.map((x) => x.label) },
      yAxis: { ...o.yAxis, scale: false, minInterval: 1 },
      tooltip: { ...o.tooltip, ...BAR_TIP, formatter: axisTip(c, (_, x) => String(x)) },
      series: REC.map(([k, l, col]) => ({ name: l, type: 'bar', stack: 'r', barMaxWidth: 56, ...FOCUS, itemStyle: { color: css(col.slice(4, -1)) }, label: { show: true, color: '#fff', fontSize: 11, formatter: (p: { value: number }) => p.value || '' }, data: d.recommendations.map((x) => x[k]) })),
    }
  }, [d, c])
  const growthOption = useMemo(() => {
    if (!d?.has_coverage) return null
    const o = baseOption(c)
    return {
      ...o,
      xAxis: { ...o.xAxis, type: 'category', data: d.growth.map((x) => (x.period === 'LTG' ? 'Next 5Y (a year)' : x.label)) },
      yAxis: { ...o.yAxis, scale: false, axisLabel: { ...o.yAxis.axisLabel, formatter: (x: number) => fmt(x, 'pct') } },
      tooltip: { ...o.tooltip, ...BAR_TIP, formatter: axisTip(c, (_, x) => fmt(x, 'pct')) },
      series: [
        { name: t, type: 'bar', barMaxWidth: 26, ...FOCUS, itemStyle: { color: c.primary, borderRadius: [3, 3, 0, 0] }, data: d.growth.map((x) => x.stock) },
        { name: 'S&P 500', type: 'bar', barMaxWidth: 26, ...FOCUS, itemStyle: { color: c.muted + '80', borderRadius: [3, 3, 0, 0] }, data: d.growth.map((x) => x.index) },
      ],
    }
  }, [d, c, t])
  const histKeys = useMemo(() => (d?.has_coverage ? Object.keys(d.history).filter((k) => d.history[k].length > 1) : []), [d])
  const histOption = useMemo(() => {
    if (!d || !histKeys.length) return null
    const dates = [...new Set(histKeys.flatMap((k) => d.history[k].map((p) => p.date)))].sort()
    const o = baseOption(c)
    return {
      ...o,
      xAxis: { ...o.xAxis, type: 'category', data: dates },
      yAxis: { ...o.yAxis, axisLabel: { ...o.yAxis.axisLabel, formatter: (x: number) => eps(x) } },
      tooltip: { ...o.tooltip, valueFormatter: (x: N) => eps(x) },
      series: histKeys.map((k) => ({ name: `EPS ${k}`, type: 'line', showSymbol: true, symbolSize: 4, data: dates.map((x) => d.history[k].find((p) => p.date === x)?.eps ?? null) })),
    }
  }, [d, histKeys, c])

  if (!d) return <div className="text-muted-foreground">{q.isError ? 'Could not load the analyst data.' : 'Loading…'}</div>
  if (!d.has_coverage) return <Card className="p-10 text-center text-muted-foreground">No analyst coverage for {t} on Yahoo Finance (common for small companies).</Card>

  const pt = d.target
  const cur = pt.current
  const r = d.rating
  const cal = d.calendar
  const rec0 = d.recommendations[d.recommendations.length - 1]
  const recN = rec0 ? REC.reduce((s, [k]) => s + (+rec0[k] || 0), 0) : 0
  const q0 = d.estimates.find((e) => e.kind === 'quarter' && e.rel === 'this quarter')
  const lo = Math.min(pt.low ?? cur, cur)
  const hi = Math.max(pt.high ?? cur, cur)
  const pos = (v: N) => (hi > lo && has(v) ? ((v - lo) / (hi - lo)) * 100 : 50)
  const days = cal.earnings_date ? Math.round((+new Date(cal.earnings_date + 'T00:00:00') - +new Date(new Date().toDateString())) / 864e5) : null
  const rows = [...d.estimates.filter((e) => e.kind === 'quarter'), ...d.estimates.filter((e) => e.kind === 'year')]
  const ltg = d.growth.find((g) => g.period === 'LTG')
  const kv = (k: string, v: ReactNode) => (
    <div className="flex items-baseline justify-between gap-3 border-b border-dashed py-[5px] last:border-0">
      <span className="text-muted-foreground">{k}</span>
      <span className="num font-medium">{v}</span>
    </div>
  )

  return (
    <>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Top label="Price target">
          {pt.mean ? (
            <>
              <div className="num mt-1 text-2xl font-bold tracking-tight">
                {money(pt.mean)} <span className={cn('text-sm font-semibold', tone(pt.upside.mean))}>{pctSigned(pt.upside.mean, 1)}</span>
              </div>
              <div className="text-xs text-muted-foreground">Average of {pt.analysts ?? '–'} price targets · price now {money(cur)}</div>
              <div className="relative mt-7 mb-3 h-1.5 rounded-full bg-secondary">
                <div className="absolute inset-y-0 rounded-full bg-primary/35" style={{ left: `${pos(pt.low)}%`, right: `${100 - pos(pt.high)}%` }} />
                <Hint label={`Average target ${money(pt.mean)}`}>
                  <span className="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-card bg-primary" style={{ left: `${pos(pt.mean)}%` }} />
                </Hint>
                <span className="absolute -top-1.5 h-[18px] w-0.5 -translate-x-1/2 bg-foreground" style={{ left: `${pos(cur)}%` }}>
                  <span className="absolute -top-4 left-1/2 -translate-x-1/2 text-[10px] font-semibold">Now</span>
                </span>
              </div>
              <table className="tbl mt-1">
                <thead>
                  <tr><th /><th>Low</th><th>Median</th><th>Average</th><th>High</th></tr>
                </thead>
                <tbody>
                  <tr><td className="text-muted-foreground">Target</td><td>{eps(pt.low)}</td><td>{eps(pt.median)}</td><td>{eps(pt.mean)}</td><td>{eps(pt.high)}</td></tr>
                  <tr><td className="text-muted-foreground">Upside</td>{(['low', 'median', 'mean', 'high'] as const).map((k) => <td key={k}><Signed v={pt.upside[k]} /></td>)}</tr>
                </tbody>
              </table>
            </>
          ) : (
            <div className="mt-2 text-muted-foreground">No price targets.</div>
          )}
        </Top>
        <Top label="Analyst rating">
          <div className={cn('mt-1 text-2xl font-bold tracking-tight', has(r.mean) && (r.mean < 2.5 ? 'text-up' : r.mean >= 3.5 ? 'text-down' : ''))}>{ratingText(r.mean)}</div>
          <div className="text-xs text-muted-foreground">Score {r.mean ? r.mean.toFixed(2) : '–'} (1 = strong buy, 5 = strong sell) · {recN || r.analysts || '–'} ratings</div>
          {recN > 0 && rec0 && (
            <>
              <div className="mt-5 flex h-2.5 gap-0.5 overflow-hidden rounded-full">
                {REC.map(([k, l, col]) => (+rec0[k] ? <Hint key={k} label={`${l}: ${rec0[k]}`}><span style={{ flex: +rec0[k], background: col }} /></Hint> : null))}
              </div>
              <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                {REC.map(([k, l, col]) => (
                  <span key={k} className="inline-flex items-center gap-1.5">
                    <span className="size-2.5 rounded-[3px]" style={{ background: col }} />
                    {l} <b className="text-foreground">{+rec0[k] || 0}</b>
                  </span>
                ))}
              </div>
            </>
          )}
        </Top>
        <Top label="Next earnings">
          {cal.earnings_upcoming ? (
            <>
              <div className="mt-1 text-2xl font-bold tracking-tight">{day(cal.earnings_date)}</div>
              <div className="text-xs text-muted-foreground">{days === 0 ? 'today' : `in ${days} days`}{q0 ? ` · ${q0.label} results` : ''}</div>
            </>
          ) : (
            <>
              <div className="mt-1 text-2xl font-bold tracking-tight text-muted-foreground">Not announced</div>
              <div className="text-xs text-muted-foreground">{cal.earnings_date ? `last report ${day(cal.earnings_date)}` : ' '}</div>
            </>
          )}
          <div className="mt-3">
            {kv('Expected EPS', <>{eps(cal.earnings_eps)} {q0?.eps && has(q0.eps.low) && <span className="font-normal text-muted-foreground">({eps(q0.eps.low)} – {eps(q0.eps.high)})</span>}</>)}
            {kv('Expected revenue', <>{rev(cal.earnings_revenue)} {q0?.revenue?.low ? <span className="font-normal text-muted-foreground">({rev(q0.revenue.low)} – {rev(q0.revenue.high)})</span> : null}</>)}
            {kv('Forward P/E · PEG', `${fmt(d.summary.forward_pe, 'x')} · ${fmt(d.summary.peg, 'x', { noX: true })}`)}
          </div>
        </Top>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <AnnualCard t={t} d={d} k="revenue" />
        <AnnualCard t={t} d={d} k="eps" />
      </div>

      <Card className="min-w-0">
        <div className="border-b px-4 py-3"><H2>Estimates per period</H2></div>
        <div className="overflow-x-auto">
          <table className="tbl">
            <thead>
              <tr><th>Period</th><th>EPS estimate</th><th>Low – high</th><th>Year ago</th><th>Growth</th><th>Revenue estimate</th><th>Low – high</th><th>Year ago</th><th>Growth</th><th>Analysts</th></tr>
            </thead>
            <tbody>
              {rows.map((e) => {
                const p = e.eps ?? {}
                const v = e.revenue ?? {}
                return (
                  <tr key={e.kind + e.label}>
                    <td>{e.label} <span className="text-muted-foreground">{e.rel}</span></td>
                    <td className="font-semibold">{eps(p.avg)}</td>
                    <td className="text-muted-foreground">{has(p.low) ? `${eps(p.low)} – ${eps(p.high)}` : '–'}</td>
                    <td>{eps(p.year_ago)}</td>
                    <td><Signed v={p.growth} /></td>
                    <td className="font-semibold">
                      {v.avg ? big(v.avg) : '–'}
                      {v.derived && (
                        <Hint label={`Yahoo has no ${e.label} revenue row of its own. This is the consensus it uses as the base of the year after.`}>
                          <span className="ml-1 rounded bg-secondary px-1 text-[10px] font-medium text-muted-foreground">base</span>
                        </Hint>
                      )}
                    </td>
                    <td className="text-muted-foreground">{v.low ? `${big(v.low)} – ${big(v.high)}` : '–'}</td>
                    <td>{v.year_ago ? big(v.year_ago) : '–'}</td>
                    <td><Signed v={v.growth} /></td>
                    <td>{p.analysts || v.analysts || '–'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        <div className="px-4 pb-3">
          <Note>
            Average (consensus) of the analysts covering {t}. "Year ago" and growth as Yahoo reports them, except where Yahoo's year-ago points to the wrong period: then they are measured against the right one.
            {d.dropped?.length ? ` Hidden: ${d.dropped.map((x) => `Yahoo's ${x.key === 'eps' ? 'EPS' : 'revenue'} row for ${x.label} (already reported, Yahoo has not updated it yet)`).join('; ')}.` : ''}
          </Note>
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card className="min-w-0">
          <div className="border-b px-4 py-3"><H2>EPS trend and revisions</H2></div>
          <div className="overflow-x-auto">
            <table className="tbl">
              <thead><tr><th>EPS estimate</th><th>Now</th><th>7 days ago</th><th>30 days ago</th><th>90 days ago</th><th>vs 90 days</th></tr></thead>
              <tbody>
                {d.eps_trend.map((x) => (
                  <tr key={x.label}><td>{x.label}</td><td className="font-semibold">{eps(x.current)}</td><td>{eps(x.d7)}</td><td>{eps(x.d30)}</td><td>{eps(x.d90)}</td><td><Signed v={has(x.current) && x.d90 ? x.current / x.d90 - 1 : null} /></td></tr>
                ))}
              </tbody>
            </table>
            <table className="tbl border-t">
              <thead><tr><th>Revisions (analysts)</th><th>Up, 7 days</th><th>Up, 30 days</th><th>Down, 7 days</th><th>Down, 30 days</th></tr></thead>
              <tbody>
                {d.revisions.map((x) => (
                  <tr key={x.label}>
                    <td>{x.label}</td>
                    <td className={x.up7 ? 'text-up' : 'text-muted-foreground'}>{x.up7 ?? '–'}</td>
                    <td className={x.up30 ? 'text-up' : 'text-muted-foreground'}>{x.up30 ?? '–'}</td>
                    <td className={x.down7 ? 'text-down' : 'text-muted-foreground'}>{x.down7 ?? '–'}</td>
                    <td className={x.down30 ? 'text-down' : 'text-muted-foreground'}>{x.down30 ?? '–'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="px-4 pb-3"><Note>How the average EPS estimate moved, and how many analysts raised or lowered theirs.</Note></div>
        </Card>
        <Card className="min-w-0 p-4">
          <div className="flex flex-wrap items-center gap-3">
            <H2>Expected growth vs. S&P 500</H2>
            <div className="grow" />
            <Legend items={[[t, <span key="a" className="size-2.5 rounded-[3px] bg-primary" />], ['S&P 500', <span key="b" className="size-2.5 rounded-[3px] bg-muted-foreground/50" />]]} />
          </div>
          {growthOption && <EChart option={growthOption} hoverTip className="mt-2 h-[240px]" />}
          <Note>Expected EPS growth for {t} and for the S&P 500. "Next 5Y" is the expected long-term growth per year{ltg && has(ltg.stock) ? '' : ' (Yahoo has no long-term estimate for this company)'}.</Note>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card className="min-w-0 p-4">
          <div className="flex flex-wrap items-center gap-3">
            <H2>Rating history</H2>
            <div className="grow" />
            <Legend items={REC.map(([k, l, col]) => [l, <span key={k} className="size-2.5 rounded-[3px]" style={{ background: col }} />])} />
          </div>
          {recOption && <EChart option={recOption} hoverTip className="mt-2 h-[240px]" />}
          <Note>Number of analysts per rating, by month.</Note>
        </Card>
        <Card className="min-w-0">
          <div className="border-b px-4 py-3"><H2>Earnings surprises</H2></div>
          {d.surprises.length ? (
            <>
              <div className="overflow-x-auto">
                <table className="tbl">
                  <thead><tr><th>Quarter</th><th>EPS estimate</th><th>EPS actual</th><th>Surprise</th></tr></thead>
                  <tbody>
                    {d.surprises.map((s) => (
                      <tr key={s.quarter_end}>
                        <td>{s.label} <span className="text-muted-foreground">{s.quarter_end.slice(0, 7)}</span></td>
                        <td>{eps(s.estimate)}</td>
                        <td className="font-semibold">{eps(s.actual)}</td>
                        <td>
                          {has(s.surprise) ? (
                            <span className={cn('rounded-md px-1.5 py-0.5 text-xs font-semibold', s.surprise > 0 ? 'bg-up-bg text-up' : s.surprise < 0 ? 'bg-down-bg text-down' : 'bg-secondary')}>
                              {s.surprise > 0 ? 'Beat' : s.surprise < 0 ? 'Miss' : 'In line'} {pctSigned(s.surprise, 1)}
                            </span>
                          ) : '–'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="px-4 pb-3"><Note>Actual EPS as Yahoo reports it (usually adjusted, like the estimates).</Note></div>
            </>
          ) : (
            <div className="p-4 text-muted-foreground">No earnings history.</div>
          )}
        </Card>
      </div>

      <Card className="min-w-0">
        <div className="border-b px-4 py-3"><H2>Recent upgrades and downgrades</H2></div>
        {d.actions.length ? (
          <div className="max-h-[560px] overflow-auto">
            <table className="tbl left-2-4">
              <thead><tr><th>Date</th><th>Firm</th><th>Action</th><th>Rating</th><th>Price target</th><th>vs price now</th></tr></thead>
              <tbody>
                {d.actions.map((a, i) => (
                  <tr key={i}>
                    <td className="text-muted-foreground">{day(a.date)}</td>
                    <td>{a.firm}</td>
                    <td><span className={cn('rounded-md px-1.5 py-0.5 text-xs font-semibold', a.action === 'up' ? 'bg-up-bg text-up' : a.action === 'down' ? 'bg-down-bg text-down' : 'bg-secondary')}>{ACTION[a.action ?? ''] ?? a.action}</span></td>
                    <td>{a.from_grade && a.from_grade !== a.to_grade && <span className="text-muted-foreground">{a.from_grade} → </span>}{a.to_grade}</td>
                    <td>
                      {a.pt_current ? (
                        a.pt_prior && a.pt_prior !== a.pt_current ? (
                          <><span className="text-muted-foreground">{eps(a.pt_prior)}</span> → <b className={a.pt_current > a.pt_prior ? 'text-up' : 'text-down'}>{eps(a.pt_current)}</b></>
                        ) : <b>{eps(a.pt_current)}</b>
                      ) : '–'}
                    </td>
                    <td><Signed v={a.pt_current && cur ? a.pt_current / cur - 1 : null} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="p-4 text-muted-foreground">No rating changes recorded.</div>
        )}
      </Card>

      <Card className="min-w-0 p-4">
        <div className="flex flex-wrap items-center gap-3">
          <H2>Estimate history</H2>
          <div className="grow" />
          <Legend items={histKeys.map((k, i) => [`EPS ${k}`, <span key={k} className="size-2.5 rounded-[3px]" style={{ background: c.series[i % c.series.length] }} />])} />
        </div>
        {histOption && <EChart option={histOption} className="mt-2 h-[220px]" />}
        <Note>
          {d.snapshots} daily snapshot{d.snapshots === 1 ? '' : 's'} so far (latest {d.snapshot_date}). Yahoo only shows today's numbers, so the history of how estimates changed builds up each time the analyst data is updated.
        </Note>
      </Card>
      <p className="text-xs text-muted-foreground">
        Source: Yahoo Finance analyst data, snapshot of {d.snapshot_date}. Reported numbers: SEC filings (GAAP, diluted EPS). Analyst EPS estimates often exclude one-off items, so they can differ from reported EPS.
      </p>
    </>
  )
}
