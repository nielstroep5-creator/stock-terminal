import { useQuery } from '@tanstack/react-query'
import { TriangleAlert } from 'lucide-react'
import { useMemo } from 'react'
import type { ReactNode } from 'react'
import { Card } from '@/components/app/Page'
import { BAR_FIT, BAR_TIP, EChart, FOCUS, FOCUS_SELF, axisTip, baseOption, useChartColors } from '@/components/chart/EChart'
import { Button } from '@/components/ui/button'
import { Segmented } from '@/components/ui/segmented'
import { Hint } from '@/components/ui/tooltip'
import { api } from '@/lib/api'
import { big, day, pctSigned, tone } from '@/lib/format'
import { useSettings } from '@/lib/settings'
import { cn } from '@/lib/utils'

type Next = { ex_date: string; pay_date?: string | null }
type Year = { year: number; rate: number | null; amount: number; special: number; payments: number; growth?: number | null; partial?: boolean }
type DividendData = {
  has_dividends: boolean
  stopped?: boolean
  price?: number | null
  yield_fwd?: number | null
  yield_ttm?: number | null
  annual?: number | null
  last_amount?: number | null
  last_ex?: string | null
  last_pay?: string | null
  next?: Next | null
  payout?: number | null
  ffo_payout?: number | null
  fcf_payout?: number | null
  basis?: string
  growth?: Record<string, number | null>
  frequency?: string | null
  per_year_count?: number | null
  years_growth?: number | null
  years_paid?: number
  first_year?: number
  earlier_run?: [number, number] | null
  specials?: number
  paid_ttm?: number | null
  per_year: Year[]
  yield_history: [string, number][]
  payout_years?: { fy: number; payout?: number | null; fcf_payout?: number | null; ffo_payout?: number | null }[]
  history: { date: string; amount: number; change?: number | null; special?: boolean }[]
}

const pct = (v: number | null | undefined, dec = 2) => (v === null || v === undefined ? '–' : (v * 100).toFixed(dec) + '%')
// dollars with 2 decimals, or up to 4 when the amount needs them ($0.125, $0.0025)
const usd = (v: number | null | undefined) => (v === null || v === undefined ? '–' : '$' + v.toFixed(4).replace(/(\.\d\d\d*?)0+$/, '$1'))

function Tile({ label, value, sub, hint, className }: { label: string; value: ReactNode; sub?: ReactNode; hint?: string; className?: string }) {
  const body = (
    <Card className="h-full p-3.5">
      <div className={cn('text-xs text-muted-foreground', hint && 'underline decoration-dotted underline-offset-2')}>{label}</div>
      <div className={cn('num mt-0.5 text-lg font-bold tracking-tight whitespace-nowrap', className)}>{value}</div>
      <div className="num min-h-4 text-xs text-muted-foreground">{sub}</div>
    </Card>
  )
  return hint ? (
    <Hint label={hint}>
      <div>{body}</div>
    </Hint>
  ) : (
    body
  )
}

function Tiles({ d }: { d: DividendData }) {
  const g = d.growth ?? {}
  const nx = d.next
  const reit = d.ffo_payout !== null && d.ffo_payout !== undefined
  const payout = reit ? d.ffo_payout : d.payout
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
      <Tile label="Dividend yield" value={d.stopped ? '–' : pct(d.yield_fwd)} sub={d.yield_ttm != null ? `${pct(d.yield_ttm)} trailing 12 months` : ''} hint={`Annual dividend divided by the share price${d.price ? ` (${usd(d.price)})` : ''}. Trailing: what was paid in the last 12 months divided by the price.`} />
      <Tile label="Annual dividend" value={d.stopped ? '–' : usd(d.annual)} sub={`last payment ${usd(d.last_amount)}`} hint="The latest regular payment times the number of payments a year. Special dividends are not counted." />
      {nx ? (
        <Tile label="Next ex-dividend" value={day(nx.ex_date)} sub={nx.pay_date ? `Paid ${day(nx.pay_date)}` : `${usd(d.last_amount)} expected`} hint="Buy before this date to receive the dividend. Announced by the company." />
      ) : (
        <Tile label="Last ex-dividend" value={day(d.last_ex)} sub={d.last_pay ? `Paid ${day(d.last_pay)}` : `${usd(d.last_amount)} per share`} hint="Buy before this date to receive the dividend. The next one appears here once the company announces it." />
      )}
      <Tile
        label={reit ? 'Payout ratio (FFO)' : 'Payout ratio'}
        value={payout != null ? pct(payout) : '–'}
        sub={d.fcf_payout != null ? `${pct(d.fcf_payout)} of free cash flow` : ''}
        className={payout != null && payout > 1 ? 'text-down' : ''}
        hint={reit ? 'Dividends divided by funds from operations (FFO), last 12 months. Net income says little for a REIT.' : d.basis === 'per_share' ? 'Dividends per share of the last 12 months divided by diluted EPS.' : 'Dividends paid divided by net income, last 12 months.'}
      />
      {d.stopped ? (
        <Tile label="Dividend growth" value="Suspended" sub={`last paid ${day(d.last_ex)}`} className="text-down" />
      ) : (
        <Tile label="Dividend growth" value={g['1y'] != null ? pctSigned(g['1y']) : '–'} sub={g['5y'] != null ? `${pctSigned(g['5y'])} a year over 5Y` : '1 year'} className={tone(g['1y'])} hint="The annual dividend now against a year earlier. Below it the yearly growth over 5 years. Special dividends are left out." />
      )}
      <Tile label="Payout frequency" value={d.stopped ? 'Suspended' : d.frequency || '–'} sub={d.stopped ? 'was ' + (d.frequency ?? '').toLowerCase() : d.per_year_count ? `${d.per_year_count} payment${d.per_year_count === 1 ? '' : 's'} a year` : ''} />
      <Tile label="Years of growth" value={d.years_growth ?? '–'} sub={`Paid ${d.years_paid} year${d.years_paid === 1 ? '' : 's'} in a row`} hint={`Calendar years in a row with a higher annual dividend than the year before (payment history since ${d.first_year}).`} />
    </div>
  )
}

function Head({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-2 px-4 pt-3">
      <h2 className="text-[15px] font-semibold">{title}</h2>
      <div className="grow" />
      {children}
    </div>
  )
}

export function Dividends({ t }: { t: string }) {
  const { pref, setPref } = useSettings()
  const c = useChartColors()
  const stored = useQuery({ queryKey: ['company', t, 'dividends'], queryFn: () => api.get<DividendData>(`/api/dividends/${t}`) })
  // then the live price and the next announced dividend
  const live = useQuery({ queryKey: ['company', t, 'dividends', 'live'], queryFn: () => api.get<DividendData>(`/api/dividends/${t}?live=1`), enabled: !!stored.data?.has_dividends, retry: false })
  const d = stored.data
  const top = live.data?.has_dividends ? live.data : d
  const years = pref<string>('ui2', 'div_years', '20')
  const all = pref('ui2', 'div_all', false)

  const yearOption = useMemo(() => {
    if (!d?.has_dividends) return null
    const ysAll = d.per_year.filter((y) => y.amount > 0 || y.year >= (d.first_year ?? 0))
    const ys = years === 'all' ? ysAll : ysAll.slice(-+years)
    const o = baseOption(c)
    const label = (special: boolean) => ({
      show: ys.length <= 20, position: 'top', color: c.muted, fontSize: 10.5,
      formatter: (x: { dataIndex: number }) => { const y = ys[x.dataIndex]; return !!y.special === special && y.growth != null ? pctSigned(y.growth, 1) : '' },
    })
    return {
      ...o,
      grid: { ...o.grid, top: 24 },
      xAxis: { ...o.xAxis, type: 'category', data: ys.map((y) => (y.partial ? 'Now' : String(y.year))) },
      yAxis: { ...o.yAxis, scale: false, axisLabel: { ...o.yAxis.axisLabel, formatter: (v: number) => '$' + v } },
      tooltip: {
        ...o.tooltip, ...BAR_TIP,
        formatter: (ps: { dataIndex: number }[]) => {
          const y = ys[ps[0].dataIndex]
          return `<b>${y.year}${y.partial ? ' (now)' : ''}</b><br>Annual dividend ${usd(y.rate)}${y.growth != null ? ` (${pctSigned(y.growth, 1)})` : ''}${y.special ? `<br>Special dividend ${usd(y.special)}` : ''}<br>Paid in ${y.year}${y.partial ? ' so far' : ''}: ${usd(y.amount)} in ${y.payments} payment${y.payments === 1 ? '' : 's'}`
        },
      },
      series: [
        { type: 'bar', name: 'Annual dividend', stack: 'd', ...BAR_FIT, ...FOCUS_SELF, data: ys.map((y) => ({ value: y.rate == null ? null : +y.rate.toFixed(4), itemStyle: { color: c.series[0], opacity: y.partial ? 0.55 : 1, borderRadius: y.special ? 0 : [3, 3, 0, 0] } })), label: label(false) },
        { type: 'bar', name: 'Special dividend', stack: 'd', ...BAR_FIT, ...FOCUS_SELF, data: ys.map((y) => (y.special ? +y.special.toFixed(4) : null)), itemStyle: { color: c.series[1], borderRadius: [3, 3, 0, 0] }, label: label(true) },
      ],
    }
  }, [d, years, c])

  const yieldOption = useMemo(() => {
    if (!d?.has_dividends || d.yield_history.length < 2) return null
    const h = d.yield_history
    const vals = h.map((p) => p[1])
    const span = +h[h.length - 1][0].slice(0, 4) - +h[0][0].slice(0, 4)
    const step = [1, 2, 5, 10].find((n) => span / n <= 12) ?? 20
    const avg = vals.reduce((a, b) => a + b, 0) / vals.length
    const o = baseOption(c)
    return {
      ...o,
      grid: { ...o.grid, top: 18 },
      xAxis: {
        ...o.xAxis, type: 'category', data: h.map((p) => p[0]), boundaryGap: false,
        // one label per year, on the first point of that year
        axisLabel: { ...o.xAxis.axisLabel, hideOverlap: true, formatter: (v: string) => v.slice(0, 4), interval: (i: number) => { const y = +h[i][0].slice(0, 4); return i > 0 && y % step === 0 && y !== +h[i - 1][0].slice(0, 4) } },
      },
      // from 0: a yield axis that starts at 1% cuts off the years below it, which looks like missing data
      yAxis: { ...o.yAxis, scale: false, min: 0, axisLabel: { ...o.yAxis.axisLabel, formatter: (x: number) => (x * 100).toFixed(1) + '%' } },
      tooltip: { ...o.tooltip, valueFormatter: (x: number) => pct(x) },
      series: [
        {
          name: 'Dividend yield', type: 'line', data: vals, showSymbol: false, lineStyle: { width: 1.7, color: c.primary }, itemStyle: { color: c.primary }, areaStyle: { color: c.primary + '14' },
          markLine: { symbol: 'none', silent: true, lineStyle: { type: 'dashed', color: c.muted }, z: 5, precision: 6, data: [{ yAxis: avg }], label: { formatter: `avg ${pct(avg)}`, position: 'insideEndTop', color: c.muted, fontSize: 11.5, backgroundColor: c.card, borderColor: c.border, borderWidth: 1, borderRadius: 4, padding: [2, 6] } },
        },
      ],
    }
  }, [d, c])

  const payout = useMemo(() => {
    const py = (d?.payout_years ?? []).slice(-12)
    if (!py.length) return null
    const cap = (v: number | null | undefined) => (v == null ? null : +(Math.min(v, 3) * 100).toFixed(2)) // a loss year makes the bar explode: cap at 300%
    const reit = py.some((y) => y.ffo_payout != null)
    const fcf = !reit && py.some((y) => y.fcf_payout != null)
    const o = baseOption(c)
    const names = [reit ? 'Of FFO' : 'Of earnings', ...(fcf ? ['Of free cash flow'] : [])]
    return {
      names,
      option: {
        ...o,
        xAxis: { ...o.xAxis, type: 'category', data: py.map((y) => `FY${y.fy}`) },
        yAxis: { ...o.yAxis, scale: false, axisLabel: { ...o.yAxis.axisLabel, formatter: (x: number) => x + '%' } },
        tooltip: { ...o.tooltip, ...BAR_TIP, formatter: axisTip(c, (_, x) => (x >= 300 ? '300%+' : x.toFixed(1) + '%')) },
        series: [
          { type: 'bar', name: names[0], ...BAR_FIT, ...(fcf ? FOCUS : FOCUS_SELF), data: py.map((y) => cap(reit ? y.ffo_payout : y.payout)), itemStyle: { color: c.series[0], borderRadius: [3, 3, 0, 0] } },
          ...(fcf ? [{ type: 'bar', name: names[1], ...BAR_FIT, ...FOCUS, data: py.map((y) => cap(y.fcf_payout)), itemStyle: { color: c.series[2], borderRadius: [3, 3, 0, 0] } }] : []),
          { type: 'line', name: '100%', data: py.map(() => 100), symbol: 'none', silent: true, lineStyle: { type: 'dashed', width: 1, color: c.muted }, tooltip: { show: false } },
        ],
      },
    }
  }, [d, c])

  if (!d || !top) return <div className="text-muted-foreground">{stored.isError ? 'Could not load the dividends.' : 'Loading…'}</div>
  if (!d.has_dividends) return <Card className="p-10 text-center text-muted-foreground">{t} has not paid a dividend.</Card>
  const g = d.growth ?? {}
  const next = d.next ?? top.next
  const growthRows: [string, ReactNode][] = [
    ...(['1y', '3y', '5y', '10y'] as const).map((k, i): [string, ReactNode] => [['1 year', '3 years (per year)', '5 years (per year)', '10 years (per year)'][i], <span key={k} className={tone(g[k])}>{g[k] != null ? pctSigned(g[k]) : '–'}</span>]),
    ['Years of growth in a row', d.years_growth ?? '–'],
    ['Years paid in a row', d.years_paid ?? '–'],
    ['Paying since', d.first_year ? `${d.first_year}${d.earlier_run ? ` (also ${d.earlier_run[0]}–${d.earlier_run[1]})` : ''}` : '–'],
    ...(d.specials ? [['Special dividends', d.specials] as [string, ReactNode]] : []),
    ...(d.paid_ttm != null ? [['Cash paid out, last 12 months', '$' + big(d.paid_ttm)] as [string, ReactNode]] : []),
  ]
  const dot = (i: number, name: string) => (
    <span key={name} className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
      <span className="size-2.5 rounded-[3px]" style={{ background: c.series[i] }} />
      {name}
    </span>
  )

  return (
    <>
      {d.stopped && (
        <div className="flex items-center gap-2 rounded-lg border border-flag/40 bg-flag/10 px-3 py-2 text-[13px]">
          <TriangleAlert className="size-4 text-flag" />
          No dividend since {day(d.last_ex)}: the dividend looks suspended. The history is below.
        </div>
      )}
      <Tiles d={top} />
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Card className="min-w-0">
          <Head title="Annual dividend per share">
            {d.per_year.some((y) => y.special > 0) && (
              <>
                {dot(0, 'Annual dividend')}
                {dot(1, 'Special dividend')}
              </>
            )}
            <Segmented label="Years shown" value={years} onChange={(v) => setPref('ui2', 'div_years', v)} options={[{ value: '10', label: '10Y' }, { value: '20', label: '20Y' }, { value: 'all', label: 'All' }]} />
          </Head>
          <div className="px-4 pt-1 pb-3">{yearOption && <EChart focusPeriod option={yearOption} className="h-[260px]" />}</div>
        </Card>
        <Card className="p-4">
          <h2 className="text-[15px] font-semibold">Dividend growth</h2>
          <div className="mt-2">
            {growthRows.map(([k, v]) => (
              <div key={k} className="flex items-baseline justify-between gap-3 border-b border-dashed py-[5px] last:border-0">
                <span className="text-muted-foreground">{k}</span>
                <span className="num font-semibold">{v}</span>
              </div>
            ))}
          </div>
        </Card>
      </div>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card className="min-w-0">
          <Head title="Dividend yield">
            <span className="text-xs text-muted-foreground">annual dividend / price</span>
          </Head>
          <div className="px-4 pt-1 pb-3">{yieldOption ? <EChart option={yieldOption} className="h-[240px]" /> : <div className="py-8 text-muted-foreground">No yield history.</div>}</div>
        </Card>
        <Card className="min-w-0">
          <Head title="Payout ratio">
            {payout?.names.map((n, i) => dot(i === 0 ? 0 : 2, n))}
            <span className="text-xs text-muted-foreground">per fiscal year</span>
          </Head>
          <div className="px-4 pt-1 pb-3">{payout ? <EChart option={payout.option} hoverTip focusPeriod={payout.names.length === 1} className="h-[240px]" /> : <div className="py-8 text-muted-foreground">No payout data in the SEC filings.</div>}</div>
        </Card>
      </div>
      <Card className="min-w-0">
        <div className="flex items-center gap-3 border-b px-4 py-3">
          <h2 className="text-[15px] font-semibold">Dividend history</h2>
          <div className="grow" />
          <span className="text-muted-foreground">{d.history.length} payments</span>
        </div>
        <div className="overflow-x-auto">
          <table className="tbl">
            <thead>
              <tr>
                <th>Ex-dividend date</th>
                <th>Amount</th>
                <th>Change</th>
                <th>Type</th>
              </tr>
            </thead>
            <tbody>
              {next && (
                <tr className="[&>td]:bg-accent/60">
                  <td>{day(next.ex_date)}</td>
                  <td>
                    <Hint label="The company announced the dates. The amount is expected to equal the last regular payment.">
                      <span>
                        {usd(d.last_amount)} <span className="text-muted-foreground">(expected)</span>
                      </span>
                    </Hint>
                  </td>
                  <td />
                  <td>
                    <span className="rounded-md bg-up-bg px-1.5 py-0.5 text-xs font-semibold text-up">Upcoming</span>
                    {next.pay_date && <span className="text-muted-foreground"> paid {day(next.pay_date)}</span>}
                  </td>
                </tr>
              )}
              {d.history.slice(0, all ? undefined : 20).map((h) => (
                <tr key={h.date}>
                  <td>{day(h.date)}</td>
                  <td>{usd(h.amount)}</td>
                  <td className={tone(h.change)}>{h.change != null && Math.abs(h.change) > 0.0005 ? pctSigned(h.change) : ''}</td>
                  <td>{h.special ? <span className="rounded-md bg-secondary px-1.5 py-0.5 text-xs font-semibold">Special</span> : 'Regular'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {d.history.length > 20 && (
          <div className="border-t p-3 text-center">
            <Button size="sm" onClick={() => setPref('ui2', 'div_all', !all)}>
              {all ? 'Show the last 20' : `Show all ${d.history.length}`}
            </Button>
          </div>
        )}
      </Card>
      <p className="text-xs text-muted-foreground">
        Payments by ex-dividend date, per share and adjusted for splits (Yahoo Finance). Annual dividend per year: the regular payments of the last 12 months at year end (the bar for this year: now). Special means far above or below the regular payments around it. Specials are left out of the annual dividend, growth and streaks. Payout ratios come from the company's SEC filings.
      </p>
    </>
  )
}
