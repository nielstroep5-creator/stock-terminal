import { useQuery } from '@tanstack/react-query'
import { Shapes } from 'lucide-react'
import { Link } from 'react-router-dom'
import { H2 } from '@/components/app/bits'
import { Card } from '@/components/app/Page'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import { fmt, tone } from '@/lib/format'
import { cn } from '@/lib/utils'

// Shared by the sector pages (pages/Sectors.tsx) and the stock page's "Rank in its industry" card, which sits in
// the first file of the app; the sector pages themselves load only when opened.

export type Metric = { key: string; label: string; group: string; fmt: string; better: 'high' | 'low' }

export const sectorPath = (sector: string, industry?: string | null) =>
  `/sectors/${encodeURIComponent(sector)}${industry ? '/' + encodeURIComponent(industry) : ''}`

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)

/** A metric's value, written the same way everywhere on these pages. */
export function metricText(m: Metric, v: unknown) {
  return fmt(num(v), m.fmt)
}

const quarter = (k: number, n: number) => (n < 4 ? 0 : k <= Math.ceil(n / 4) ? 1 : k > n - Math.ceil(n / 4) ? -1 : 0)

/** "#3/24", green for the top quarter, red for the bottom quarter. */
export function RankTag({ r, className }: { r?: [number, number] | null; className?: string }) {
  if (!r) return null
  const [k, n] = r
  const q = quarter(k, n)
  return (
    <span className={cn('num text-[11px] font-normal text-muted-foreground', q > 0 && 'text-up', q < 0 && 'text-down', className)} title={`Rank ${k} of ${n} in this group (1 = best)`}>
      #{k}
      <span className="opacity-70">/{n}</span>
    </span>
  )
}

type Position = {
  ticker: string; kind?: string; sector: string; industry: string; sector_n: number; industry_n: number; metrics: Metric[]
  items: { key: string; value: number | null; ind_med: number | null; ind_rank: [number, number] | null; sec_med: number | null; sec_rank: [number, number] | null }[]
}

const SHOW = ['pe', 'ev_ebitda', 'fcf_yield', 'gross_margin', 'operating_margin', 'net_margin', 'revenue_yoy', 'revenue_cagr_5y', 'roe', 'roic', 'r_1y']
const SIGNED = new Set(['revenue_yoy', 'revenue_cagr_5y', 'r_1y'])

function RankCell({ r }: { r: [number, number] | null }) {
  if (!r) return <span className="text-muted-foreground">–</span>
  const [k, n] = r
  const q = quarter(k, n)
  return (
    <span className="inline-flex items-center justify-end gap-2" title={`Rank ${k} of ${n} (1 = best)`}>
      {/* where it sits from worst (left) to best (right) */}
      <span className="relative hidden h-1.5 w-16 rounded-full bg-secondary sm:inline-block">
        <span className={cn('absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-card', q > 0 ? 'bg-up' : q < 0 ? 'bg-down' : 'bg-muted-foreground')} style={{ left: `${n > 1 ? ((n - k) / (n - 1)) * 100 : 50}%` }} />
      </span>
      <span className={cn('num w-14 text-right', q > 0 && 'text-up', q < 0 && 'text-down')}>{k} of {n}</span>
    </span>
  )
}

/** Stock page Overview: where the company ranks in its industry and its sector, number by number. */
export function IndustryRankCard({ t }: { t: string }) {
  const q = useQuery({ queryKey: ['company', t, 'sector-rank'], queryFn: () => api.get<Position>(`/api/sectors/rank/${t}`), retry: false })
  const d = q.data
  if (!d || d.sector_n < 2) return null
  const byKey = Object.fromEntries(d.metrics.map((m) => [m.key, m]))
  const ind = d.industry_n >= 2
  const rows = SHOW.map((k) => d.items.find((x) => x.key === k)).filter((x): x is Position['items'][number] => !!x && x.value != null && !!byKey[x.key])
  if (!rows.length) return null
  const best = rows.filter((x) => { const r = ind ? x.ind_rank : x.sec_rank; return r && quarter(r[0], r[1]) > 0 }).map((x) => byKey[x.key].label.replace(/^[A-Z][a-z]/, (s) => s.toLowerCase()))
  return (
    <Card className="min-w-0">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b px-4 py-3">
        <H2 className="mb-0">Rank in its {ind ? 'industry' : 'sector'}</H2>
        <span className="text-muted-foreground">
          {ind && <><Link to={sectorPath(d.sector, d.industry)} className="hover:text-foreground hover:underline">{d.industry}</Link> ({d.industry_n}) · </>}
          <Link to={sectorPath(d.sector)} className="hover:text-foreground hover:underline">{d.sector}</Link> ({d.sector_n})
        </span>
        <div className="grow" />
        <Button asChild size="sm">
          <Link to={sectorPath(d.sector, ind ? d.industry : null)}><Shapes /> {ind ? 'Industry' : 'Sector'} page</Link>
        </Button>
      </div>
      <div className="overflow-x-auto">
        <table className="tbl">
          <thead>
            <tr>
              <th>Number</th>
              <th>{t}</th>
              {ind && <th>Industry median</th>}
              {ind && <th>Rank in industry</th>}
              <th>Sector median</th>
              <th>Rank in sector</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((x) => {
              const m = byKey[x.key]
              return (
                <tr key={x.key}>
                  <td title={`${m.better === 'high' ? 'Higher' : 'Lower'} is better`}>{m.label}</td>
                  <td className={cn('font-semibold', SIGNED.has(x.key) && tone(x.value))}>{metricText(m, x.value)}</td>
                  {ind && <td className="text-muted-foreground">{metricText(m, x.ind_med)}</td>}
                  {ind && <td><RankCell r={x.ind_rank} /></td>}
                  <td className="text-muted-foreground">{metricText(m, x.sec_med)}</td>
                  <td><RankCell r={x.sec_rank} /></td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {best.length > 0 && (
        <p className="border-t px-4 py-2 text-[13px] text-muted-foreground">
          In the top quarter of its {ind ? 'industry' : 'sector'} on {best.join(', ')}.
        </p>
      )}
    </Card>
  )
}
