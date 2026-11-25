import { useQuery } from '@tanstack/react-query'
import { ArrowDownWideNarrow, ArrowUpNarrowWide, ChartColumn, ChartLine, Download, FolderOpen, Hash, Percent } from 'lucide-react'
import { Fragment, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Card } from '@/components/app/Page'
import { BAR_FIT, BAR_TIP, EChart, FOCUS, axisTip, baseOption, useChartColors } from '@/components/chart/EChart'
import { Button } from '@/components/ui/button'
import { Segmented } from '@/components/ui/segmented'
import { IconToggle } from '@/components/ui/toggle'
import { Hint } from '@/components/ui/tooltip'
import { api } from '@/lib/api'
import { big, fmt, pctSigned, tone } from '@/lib/format'
import type { Unit } from '@/lib/format'
import { useSettings } from '@/lib/settings'
import type { Settings } from '@/lib/settings'
import { cn } from '@/lib/utils'
import { yearBefore } from '@/pages/stock/types'
import type { Cell, Period, Row, Statements } from '@/pages/stock/types'

type PeriodKind = Settings['default_period']
type Span = '5' | '10' | 'all'

const DEFAULT_CHART: Record<string, string[]> = {
  income: ['revenue', 'gross_profit', 'operating_income', 'net_income'],
  balance: ['total_assets', 'total_liabilities', 'total_equity'],
  cashflow: ['operating_cf', 'fcf'],
  ratios: ['gross_margin', 'operating_margin', 'net_margin'],
}
const MAX_CHARTED = 6
const MARGIN_OF: Record<string, string> = { gross_profit: 'Gross margin', operating_income: 'Operating margin', ebitda: 'EBITDA margin', net_income: 'Net margin', fcf: 'Free cash flow margin' }
// costs, spending and debt: more is not "good", so their change is shown without green / red
const GROWTH_NEUTRAL = new Set(['cost_of_revenue', 'rnd', 'sga', 'sales_marketing', 'general_admin', 'operating_expenses', 'interest_expense', 'income_tax', 'capex', 'acquisitions', 'buybacks', 'dividends_paid', 'sbc', 'debt_issued', 'debt_repaid', 'd_and_a', 'accounts_payable', 'total_liabilities', 'total_debt', 'borrowings', 'long_term_debt', 'short_term_debt', 'commercial_paper', 'current_portion_ltd', 'finance_lease_liabilities', 'total_current_liabilities', 'stock_issued', 'shares_basic', 'shares_diluted', 'shares_outstanding', 'investing_cf', 'financing_cf'])
const METHOD_TEXT: Record<string, string> = {
  reported: 'Reported in the filing',
  ytd_diff: 'Calculated: year-to-date minus previous year-to-date',
  fy_minus_ytd: 'Calculated: full year minus first 9 months',
  fy_minus_q: 'Calculated: full year minus Q1–Q3',
  fy_avg_minus_q: 'Calculated from full-year average',
  derived: 'Calculated with a formula',
  ttm: 'Sum of the last 4 quarters',
  scale_fixed: 'Reported in the filing with an obvious scaling mistake (factor 1000); corrected',
  sign_fixed: 'Reported in the filing with the wrong sign (negative); corrected',
  from_weighted: 'The filing gives 0; the weighted average share count of the period is used',
  from_cover: 'The filing tags shares issued (including bought-back shares) as outstanding; the cover-page count is used',
}

const val = (c: Cell | undefined) => (c && c.v !== null && c.v !== undefined ? c.v : null)

function growth(all: Period[], row: Row, p: Period): number | null {
  if (row.fmt === 'pct') return null
  const prev = yearBefore(all, p)
  const a = val(row.values[p.key])
  const b = prev ? val(row.values[prev.key]) : null
  return a !== null && b !== null && b > 0 ? a / b - 1 : null
}

type Tip = { row: Row; cell: Cell; g: number | null; p: Period }

type Mode = 'fin' | 'rat'

/** Financial statements. */
export function Financials(props: { t: string; name: string }) {
  return <Board {...props} mode="fin" />
}

/** Ratios: the same board (chart on top, table below), grouped instead of split in statements. */
export function Ratios(props: { t: string; name: string }) {
  return <Board {...props} mode="rat" />
}

/** A table with the oldest year on the left opens scrolled to the right end, where the newest years are. */
export function scrollToNewest(el: HTMLDivElement | null) {
  if (el) requestAnimationFrame(() => (el.scrollLeft = el.scrollWidth))
}

function Board({ t, name, mode }: { t: string; name: string; mode: Mode }) {
  const fin = mode === 'fin'
  const { settings, pref, setPref } = useSettings()
  const c = useChartColors()
  const statement = fin ? pref<string>('ui2', 'fin_statement', 'income') : 'ratios'
  const period = pref('ui2', `${mode}_period`, settings.default_period) as PeriodKind
  const unit = pref('ui2', `${mode}_unit`, 'M') as Unit
  // oldest first by default, the same direction as the chart above the table (Niels's choice, 2026-11-17)
  const newest = pref('ui2', `${mode}_newest_first`, false)
  const span = pref('ui2', `${mode}_span`, '10') as Span
  const kind = pref('ui2', `${mode}_chart`, fin ? 'bar' : 'line') as 'bar' | 'line'
  const labels = pref('ui2', `${mode}_labels`, false)
  const extra = fin && pref('ui2', 'fin_growth_rows', true)
  const chartedPref = pref<string>('ui2', `${mode}_rows_${statement}`, '')
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const [saved, setSaved] = useState<string | null>(null)

  const q = useQuery({ queryKey: ['company', t, fin ? 'statements' : 'metrics', period], queryFn: () => api.get<Statements>(`/api/${fin ? 'statements' : 'metrics'}/${t}?period=${period}`), placeholderData: (prev) => prev })
  const data = q.data

  // a line found in only a few old years (American Tower's gross profit, 2009 to 2011) is left out: it reads as
  // missing data more than as information. Lines with any of the latest three periods always stay.
  const rows = useMemo(() => {
    if (!data) return []
    const recent = data.periods.slice(-3).map((p) => p.key)
    return data.rows.filter((r) => {
      if (fin && r.statement !== statement) return false
      const n = data.periods.filter((p) => val(r.values[p.key]) !== null).length
      if (!n) return false
      return !fin || recent.some((k) => val(r.values[k]) !== null) || n >= data.periods.length / 4
    })
  }, [data, statement, fin])
  const revenue = useMemo(() => data?.rows.find((r) => r.key === 'revenue'), [data])
  const periods = useMemo(() => {
    if (!data) return []
    const n = span === 'all' ? data.periods.length : +span * (period === 'FY' ? 1 : 4)
    return data.periods.slice(-n)
  }, [data, span, period])

  const charted = useMemo(() => {
    const keys = chartedPref === '-' ? [] : chartedPref ? chartedPref.split(',') : (DEFAULT_CHART[statement] ?? [])
    return keys.map((k) => rows.find((r) => r.key === k)).filter((r): r is Row => !!r)
  }, [chartedPref, rows, statement])
  const colorOf = (key: string) => c.series[charted.findIndex((r) => r.key === key) % c.series.length]

  const toggleRow = (key: string) => {
    const keys = charted.map((r) => r.key)
    const next = keys.includes(key) ? keys.filter((k) => k !== key) : [...keys, key].slice(-MAX_CHARTED)
    setPref('ui2', `${mode}_rows_${statement}`, next.length ? next.join(',') : '-')
  }

  const option = useMemo(() => {
    const shown = charted.filter((r) => !hidden.has(r.key))
    if (!shown.length || !periods.length) return null
    const o = baseOption(c)
    const main = shown[0].fmt === 'shares' ? 'usd' : shown[0].fmt
    const axisOf = (r: Row) => ((r.fmt === 'shares' ? 'usd' : r.fmt) === main ? 0 : 1)
    const second = shown.find((r) => axisOf(r) === 1)
    const show = (f: string) => (x: number) => (f === 'usd' || f === 'shares' ? big(x) : fmt(x, f))
    return {
      ...o,
      grid: { ...o.grid, top: labels ? 26 : 14 },
      xAxis: { ...o.xAxis, type: 'category', data: periods.map((p) => p.label), axisLabel: { ...o.xAxis.axisLabel, hideOverlap: true } },
      yAxis: [
        { ...o.yAxis, scale: false, axisLabel: { ...o.yAxis.axisLabel, formatter: show(main) } },
        { ...o.yAxis, scale: false, show: !!second, splitLine: { show: false }, axisLabel: { ...o.yAxis.axisLabel, formatter: show(second?.fmt ?? main) } },
      ],
      tooltip: { ...o.tooltip, ...(kind === 'bar' ? BAR_TIP : {}), formatter: axisTip(c, (i, x) => show(shown[i].fmt)(x)) },
      series: shown.map((r) => {
        const col = colorOf(r.key)
        const type = axisOf(r) === 1 ? 'line' : kind
        return {
          name: r.label, type, yAxisIndex: axisOf(r), data: periods.map((p) => val(r.values[p.key])),
          itemStyle: { color: col, borderRadius: [3, 3, 0, 0] }, lineStyle: { color: col, width: 2 },
          symbol: 'circle', symbolSize: 6, showSymbol: type === 'line' && periods.length <= 24, connectNulls: true,
          label: { show: labels && periods.length * shown.length <= 48, position: 'top', fontSize: 10, color: c.muted, formatter: (p: { value: number | null }) => (p.value === null ? '' : show(r.fmt)(p.value)) },
          ...BAR_FIT, ...FOCUS,
        }
      }),
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [charted, hidden, periods, kind, labels, c])

  /* one floating tooltip for the whole table: where a number comes from, and its change against a year earlier */
  const [tip, setTip] = useState<Tip | null>(null)
  const tipBox = useRef<HTMLDivElement>(null)
  const moveTip = (e: React.MouseEvent) => {
    const el = tipBox.current
    if (!el) return
    const w = el.offsetWidth
    const h = el.offsetHeight
    el.style.left = Math.min(e.clientX + 14, window.innerWidth - w - 10) + 'px'
    el.style.top = (e.clientY + 18 + h > window.innerHeight ? e.clientY - h - 10 : e.clientY + 18) + 'px'
  }
  const overCell = (e: React.MouseEvent) => {
    const td = (e.target as HTMLElement).closest<HTMLElement>('td[data-r]')
    if (!td || !data) return setTip(null)
    const row = rows.find((r) => r.key === td.dataset.r)
    const p = data.periods.find((x) => x.key === td.dataset.p)
    const cell = row && p ? row.values[p.key] : null
    if (!row || !p || !cell || val(cell) === null) return setTip(null)
    if (tip?.row !== row || tip.p !== p) setTip({ row, cell, p, g: growth(data.periods, row, p) })
  }

  const saveCsv = async () => {
    if (!data) return
    const cols = newest ? [...periods].reverse() : periods
    const q2 = (s: string) => `"${s.replace(/"/g, '""')}"`
    const title = fin ? data.statements[statement] : 'Ratios'
    const lines = [[q2(`${name} (${t}), ${title}, USD`), ...cols.map((p) => q2(p.label))].join(',')]
    for (const r of rows) lines.push([q2(r.label), ...cols.map((p) => val(r.values[p.key]) ?? '')].join(','))
    try {
      const r = await api.post<{ path: string }>('/api/export', { kind: 'csv', name: `${t} ${title} ${period}`, data: lines.join('\r\n') })
      setSaved(r.path)
    } catch {
      setSaved('')
    }
  }

  if (!data) return <div className="text-muted-foreground">{q.isError ? 'Could not load these numbers.' : 'Loading…'}</div>
  const cols = newest ? [...periods].reverse() : periods
  const unitWord = unit === 'B' ? 'billions' : 'millions'
  const cur = data.currency && data.currency !== 'USD' ? data.currency : '$'

  const subRow = (key: string, label: string, cells: (number | null)[], coloured: boolean) => (
    <tr key={key} className="text-muted-foreground italic">
      <td className="pl-7! text-xs">{label}</td>
      <td />
      {cells.map((g, i) => (
        <td key={i} className={cn('text-xs', coloured && tone(g))}>
          {g === null ? '–' : coloured ? pctSigned(g, 1) : fmt(g, 'pct')}
        </td>
      ))}
    </tr>
  )

  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        {fin && <Segmented label="Statement" value={statement} onChange={(v) => { setPref('ui2', 'fin_statement', v); setHidden(new Set()) }} options={Object.entries(data.statements).map(([k, l]) => ({ value: k, label: l }))} />}
        <div className="grow" />
        <Segmented label="Period" value={period} onChange={(v) => setPref('ui2', `${mode}_period`, v)} options={[{ value: 'FY', label: 'Annual' }, { value: 'Q', label: 'Quarterly' }, { value: 'TTM', label: 'TTM' }]} />
        <Segmented label="Years shown" value={span} onChange={(v) => setPref('ui2', `${mode}_span`, v)} options={[{ value: '5', label: '5Y' }, { value: '10', label: '10Y' }, { value: 'all', label: 'All' }]} />
        <Segmented label="Numbers in" value={unit} onChange={(v) => setPref('ui2', `${mode}_unit`, v)} options={[{ value: 'M', label: 'Millions' }, { value: 'B', label: 'Billions' }]} />
        {fin && (
          <IconToggle on={extra} onChange={(v) => setPref('ui2', 'fin_growth_rows', v)} label="Growth and margin rows under the main lines" text="Growth rows">
            <Percent />
          </IconToggle>
        )}
        <Hint label={newest ? 'Newest period first. Click for oldest first.' : 'Oldest period first. Click for newest first.'}>
          <Button size="icon" aria-label="Order of the periods" onClick={() => setPref('ui2', `${mode}_newest_first`, !newest)}>
            {newest ? <ArrowDownWideNarrow /> : <ArrowUpNarrowWide />}
          </Button>
        </Hint>
        <Hint label="Save this table as a spreadsheet (CSV)">
          <Button size="icon" aria-label="Save this table as a spreadsheet" onClick={() => void saveCsv()}>
            <Download />
          </Button>
        </Hint>
      </div>
      {saved !== null && (
        <div className="-mt-2 flex items-center justify-end gap-2 text-xs text-muted-foreground">
          {saved ? (
            <>
              Saved as {saved.split(/[\\/]/).pop()}
              <button type="button" className="inline-flex items-center gap-1 font-medium text-primary hover:underline" onClick={() => void api.post('/api/reveal', { path: saved }).catch(() => {})}>
                <FolderOpen className="size-3.5" /> Show in folder
              </button>
            </>
          ) : (
            'Could not save the file.'
          )}
        </div>
      )}

      {(data.annual_only || data.converted_from) && (
        <div className="text-[13px] text-muted-foreground">
          {data.annual_only && `${t} reports once a year (a foreign company, annual report on form 20-F or 40-F): the SEC has no quarterly numbers for it, so TTM shows the latest full year. `}
          {data.converted_from && `Converted from ${data.converted_from} to US dollars: amounts for a year at that year's average exchange rate, the balance sheet at the rate on its date.`}
        </div>
      )}

      <Card className="min-w-0">
        <div className="flex flex-wrap items-center gap-2 px-4 pt-3">
          <div className="flex flex-wrap items-center gap-1.5">
            {charted.length === 0 && <span className="text-muted-foreground">Click the chart icon of a row to draw it here.</span>}
            {charted.map((r) => {
              const off = hidden.has(r.key)
              return (
                <button
                  key={r.key}
                  type="button"
                  aria-pressed={!off}
                  onClick={() => setHidden((h) => { const n = new Set(h); if (off) n.delete(r.key); else n.add(r.key); return n })}
                  className={cn('inline-flex h-7 items-center gap-1.5 rounded-md border px-2 text-xs font-medium outline-none hover:bg-secondary focus-visible:ring-2 focus-visible:ring-ring/60', off && 'text-muted-foreground line-through opacity-60')}
                >
                  <span className="size-2.5 rounded-[3px]" style={{ background: colorOf(r.key) }} />
                  {r.label}
                </button>
              )
            })}
          </div>
          <div className="grow" />
          <Segmented
            label="Draw as"
            value={kind}
            onChange={(v) => setPref('ui2', `${mode}_chart`, v)}
            options={[{ value: 'bar', icon: <ChartColumn />, hint: 'Bars' }, { value: 'line', icon: <ChartLine />, hint: 'Lines' }]}
          />
          <IconToggle on={labels} onChange={(v) => setPref('ui2', `${mode}_labels`, v)} label="Numbers on the chart" text="Labels">
            <Hash />
          </IconToggle>
          <Hint label="Open this company in Compare">
            <Button asChild size="icon" aria-label="Open this company in Compare">
              <Link to={`/compare?t=${t}`}>
                <ChartLine />
              </Link>
            </Button>
          </Hint>
        </div>
        <div className="px-4 pt-2 pb-3">{option ? <EChart option={option} hoverTip className="h-[280px]" /> : <div className="h-10" />}</div>

        <div ref={newest ? undefined : scrollToNewest} className="overflow-x-auto border-t" onMouseOver={overCell} onMouseMove={moveTip} onMouseLeave={() => setTip(null)}>
          <table className="tbl sticky-first edge">
            <thead>
              <tr>
                <th className="min-w-56">{fin ? `In ${cur} ${unitWord}, except per share` : `Ratio (amounts in ${cur} ${unitWord})`}</th>
                <th className="w-9 px-0!" />
                {cols.map((p) => (
                  <th key={p.key} title={`Period ends ${p.end}`}>
                    {p.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr>
                  <td colSpan={cols.length + 2} className="text-muted-foreground">
                    No numbers for this statement.
                  </td>
                </tr>
              )}
              {rows.map((r, ri) => {
                const on = charted.some((x) => x.key === r.key)
                const margin = statement !== 'balance' && MARGIN_OF[r.key] && revenue
                return (
                  <Fragment key={r.key}>
                    {!fin && r.group && r.group !== rows[ri - 1]?.group && (
                      <tr>
                        <td colSpan={cols.length + 2} className="bg-panel pt-3! text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">
                          {r.group}
                        </td>
                      </tr>
                    )}
                    <tr className={cn(r.bold && 'font-semibold')}>
                      <td>{r.label}</td>
                      <td className="px-0!">
                        <button
                          type="button"
                          aria-label={on ? `Remove ${r.label} from the chart` : `Show ${r.label} in the chart`}
                          aria-pressed={on}
                          title={on ? 'Remove from the chart' : 'Show in the chart'}
                          onClick={() => toggleRow(r.key)}
                          className={cn('inline-flex size-6 items-center justify-center rounded-md align-middle outline-none hover:bg-secondary focus-visible:ring-2 focus-visible:ring-ring/60', !on && 'text-muted-foreground/50 hover:text-foreground')}
                          style={on ? { color: colorOf(r.key) } : undefined}
                        >
                          <ChartColumn className="size-3.5" />
                        </button>
                      </td>
                      {cols.map((p) => {
                        const cell = r.values[p.key]
                        const v = val(cell)
                        const derived = cell?.m && cell.m !== 'reported' && cell.m !== 'ttm'
                        return (
                          <td key={p.key} data-r={r.key} data-p={p.key} className={cn(v === null ? 'text-muted-foreground' : derived && 'text-muted-foreground')}>
                            {fmt(v, r.fmt, { unit })}
                            {cell?.flag && <span className="ml-1 inline-block size-1.5 rounded-full bg-flag align-middle" />}
                          </td>
                        )
                      })}
                    </tr>
                    {extra && r.bold && r.fmt !== 'pct' && subRow(r.key + '_g', 'Growth', cols.map((p) => growth(data.periods, r, p)), !GROWTH_NEUTRAL.has(r.key))}
                    {extra && margin && subRow(r.key + '_m', MARGIN_OF[r.key], cols.map((p) => { const n = val(r.values[p.key]); const d = val(revenue.values[p.key]); return n !== null && d !== null && d > 0 ? n / d : null }), false)}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      </Card>

      <p className="text-xs text-muted-foreground">
        {fin ? (
          <>
            Click the chart icon of a row to draw it. Hover a number to see where it comes from. Grey numbers are calculated (for example Q4 = full year minus 9 months).{' '}
            <span className="inline-block size-1.5 rounded-full bg-flag align-middle" /> marks a value that did not pass a consistency check.
          </>
        ) : (
          'Click the chart icon of a row to draw it. Growth compares with the same period one year earlier. Returns use average equity, assets and invested capital.'
        )}
      </p>

      <div ref={tipBox} className={cn('pointer-events-none fixed z-50 max-w-xs rounded-lg border bg-popover px-3 py-2 text-xs shadow-lg', !tip && 'hidden')}>
        {tip && (
          <>
            <div className="font-semibold">
              {tip.row.label} · {tip.p.label}
            </div>
            {tip.cell?.flag && <div className="text-flag">{tip.cell.flag}</div>}
            {tip.g !== null && (
              <div>
                <span className={GROWTH_NEUTRAL.has(tip.row.key) ? '' : tone(tip.g)}>{pctSigned(tip.g, 1)}</span> vs. a year earlier
              </div>
            )}
            {tip.cell?.src && <div className="break-all">Source: {tip.cell.src}</div>}
            {tip.cell?.m && <div className="text-muted-foreground">{METHOD_TEXT[tip.cell.m.split('+')[0]] ?? tip.cell.m}</div>}
            {tip.cell?.orig !== undefined && <div className="text-muted-foreground">Originally reported: {fmt(tip.cell.orig, tip.row.fmt)} (restated later)</div>}
            {tip.cell?.filed && <div className="text-muted-foreground">Filed {tip.cell.filed}</div>}
          </>
        )}
      </div>
    </>
  )
}
