import { useQuery } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, Check, Rows3 } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Card } from '@/components/app/Page'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Hint } from '@/components/ui/tooltip'
import { api } from '@/lib/api'
import { big, fmt, pctSigned } from '@/lib/format'
import { useSettings } from '@/lib/settings'
import { cn } from '@/lib/utils'
import { Logo } from '@/components/app/Logo'

type N = number | null | undefined
type Info = { price?: N; valuation?: Record<string, N>; ttm?: Record<string, N>; metrics?: Record<string, N>; analyst?: Record<string, N>; is_etf?: boolean }
// [label, value, format, better: 1 = higher is better, -1 = lower is better, 0 = no best]; a label alone is a heading
type RowDef = [string, ((x: Info) => N)?, string?, number?]
const ROWS: RowDef[] = [
  ['Price', (x) => x.price, 'usdps', 0], ['Market cap', (x) => x.valuation?.market_cap, 'usd', 0],
  ['P/E (TTM)', (x) => x.valuation?.pe_ttm, 'x', -1], ['P/S', (x) => x.valuation?.ps_ttm, 'x', -1],
  ['P/FCF', (x) => x.valuation?.pfcf_ttm, 'x', -1], ['EV/EBITDA', (x) => x.valuation?.ev_ebitda_ttm, 'x', -1],
  ['Dividend yield', (x) => x.valuation?.dividend_yield, 'pct2', 1],
  ['Revenue (TTM)', (x) => x.ttm?.revenue, 'usd', 0], ['Revenue growth', (x) => x.metrics?.revenue_yoy, 'pct', 1],
  ['EPS growth', (x) => x.metrics?.eps_diluted_yoy, 'pct', 1],
  ['Gross margin', (x) => x.metrics?.gross_margin, 'pct', 1], ['Operating margin', (x) => x.metrics?.operating_margin, 'pct', 1],
  ['Net margin', (x) => x.metrics?.net_margin, 'pct', 1], ['FCF margin', (x) => x.metrics?.fcf_margin, 'pct', 1],
  ['ROE', (x) => x.metrics?.roe, 'pct', 1], ['ROIC', (x) => x.metrics?.roic, 'pct', 1],
  ['Net cash (debt)', (x) => x.metrics?.net_cash, 'usd', 1], ['Debt / equity', (x) => x.metrics?.debt_to_equity, 'x', -1],
  ['Analyst estimates'],
  ['Forward P/E', (x) => x.analyst?.forward_pe, 'x', -1],
  ['Est. revenue growth, next FY', (x) => x.analyst?.rev_growth_next, 'spct', 1],
  ['Est. EPS growth, next FY', (x) => x.analyst?.eps_growth_next, 'spct', 1],
  ['Price target upside', (x) => x.analyst?.target_upside, 'spct', 1],
  ['Analyst rating', (x) => x.analyst?.rating_mean, 'rating', -1],
]
const has = (v: N): v is number => typeof v === 'number' && Number.isFinite(v)
const ratingText = (m: number) => (m < 1.5 ? 'Strong buy' : m < 2.5 ? 'Buy' : m < 3.5 ? 'Hold' : m < 4.5 ? 'Sell' : 'Strong sell')
function cell(v: N, f?: string): ReactNode {
  if (!has(v)) return '–'
  if (f === 'usd') return big(v)
  if (f === 'spct') return pctSigned(v, 1)
  if (f === 'rating') return <>{ratingText(v)} <span className="text-muted-foreground">{v.toFixed(1)}</span></>
  return fmt(v, f)
}
function median(vs: N[]) {
  const a = vs.filter(has).sort((x, y) => x - y)
  if (a.length < 2) return null
  const m = Math.floor(a.length / 2)
  return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2
}

/** The companies next to each other on the usual numbers. The best value in a row is green; click a row name to
    sort the companies by it; the rows icon chooses which rows to show. Sort and hidden rows are remembered
    (the same choice as in the old screen). */
export function SideBySide({ tickers, colors }: { tickers: string[]; colors: string[] }) {
  const { pref, setPref } = useSettings()
  const { data: info } = useQuery({ queryKey: ['compare-table', tickers.join(',')], queryFn: () => api.get<Record<string, Info | null>>(`/api/compare_table?tickers=${tickers.join(',')}&live=1`), enabled: tickers.length > 0, staleTime: 60_000 })
  const hidden = new Set(pref<string>('state', 'cmpHidden', '').split('|').filter(Boolean))
  const [sortRow, sortDir] = pref<string>('state', 'cmpSort', '').split('|')
  if (!tickers.length) return null
  const cols = tickers.map((t, i) => ({ t, color: colors[i] }))
  const sr = ROWS.find((r) => r[0] === sortRow && r[1])
  if (sr && info) {
    const val = (t: string) => (info[t] ? sr[1]!(info[t] as Info) : null)
    cols.sort((a, b) => {
      const x = val(a.t)
      const y = val(b.t)
      if (x === y) return 0
      if (!has(x)) return 1
      if (!has(y)) return -1
      return sortDir === 'asc' ? x - y : y - x
    })
  }
  const sortBy = (l: string, better: number) => {
    const first = better < 0 ? 'asc' : 'desc'
    setPref('state', 'cmpSort', sortRow === l ? (sortDir === first ? `${l}|${first === 'asc' ? 'desc' : 'asc'}` : '') : `${l}|${first}`)
  }
  const toggle = (l: string) => {
    if (hidden.has(l)) hidden.delete(l)
    else hidden.add(l)
    setPref('state', 'cmpHidden', [...hidden].join('|'))
  }
  return (
    <Card className="min-w-0">
      <div className="flex items-center gap-3 px-4 pt-3 pb-2">
        <h2 className="text-[15px] font-semibold">Side by side</h2>
        <span className="text-xs text-muted-foreground">The best value in each row is green. Click a row name to sort the companies.</span>
        <div className="grow" />
        <Popover>
          <Hint label="Choose rows"><PopoverTrigger asChild><Button size="icon" aria-label="Choose rows"><Rows3 /></Button></PopoverTrigger></Hint>
          <PopoverContent className="max-h-[420px] w-64 overflow-y-auto p-1">
            <div className="px-2 pt-1 pb-1.5 text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">Rows to show</div>
            {ROWS.filter((r) => r[1]).map(([l]) => (
              <button key={l} type="button" role="menuitemcheckbox" aria-checked={!hidden.has(l)} onClick={() => toggle(l)} className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] outline-none hover:bg-secondary focus-visible:bg-secondary">
                <span className={cn('flex size-4 shrink-0 items-center justify-center rounded border', !hidden.has(l) && 'border-primary bg-primary text-primary-foreground')}>{!hidden.has(l) && <Check className="size-3" />}</span>
                {l}
              </button>
            ))}
          </PopoverContent>
        </Popover>
      </div>
      <div className="overflow-x-auto">
        <table className="tbl sticky-first">
          <thead>
            <tr>
              <th />
              {cols.map((c) => (
                <th key={c.t}><Link to={info?.[c.t]?.is_etf ? `/etf/${c.t}` : `/stock/${c.t}`} className="inline-flex items-center gap-1.5 font-semibold text-foreground hover:underline"><span className="size-2.5 rounded-sm" style={{ background: c.color }} /><Logo t={c.t} size={16} />{c.t}</Link></th>
              ))}
              <th>Median</th>
            </tr>
          </thead>
          <tbody>
            {ROWS.filter((r) => !hidden.has(r[0])).map(([l, f, fm, better]) => {
              if (!f) return <tr key={l}><td colSpan={cols.length + 2} className="bg-panel text-[11px]! font-semibold tracking-wider text-muted-foreground uppercase">{l}</td></tr>
              const vals = cols.map((c) => (info?.[c.t] ? f(info[c.t] as Info) : null))
              const ok = vals.map((v) => has(v) && (fm !== 'x' || v > 0))
              let best = -1
              if (better && ok.filter(Boolean).length > 1) vals.forEach((v, i) => { if (ok[i] && (best < 0 || (better > 0 ? (v as number) > (vals[best] as number) : (v as number) < (vals[best] as number)))) best = i })
              return (
                <tr key={l}>
                  <td>
                    <button type="button" title={`Sort the companies by ${l}`} onClick={() => sortBy(l, better ?? 0)} className={cn('inline-flex items-center gap-1 hover:text-primary', sr?.[0] === l && 'font-semibold')}>
                      {l}
                      {sr?.[0] === l && (sortDir === 'asc' ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />)}
                    </button>
                  </td>
                  {vals.map((v, i) => <td key={cols[i].t} className={cn(i === best && 'font-semibold text-up')}>{info ? cell(v, fm) : ''}</td>)}
                  <td className="text-muted-foreground">{info ? cell(median(vals), fm) : ''}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </Card>
  )
}
