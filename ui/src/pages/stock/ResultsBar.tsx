import { useQuery } from '@tanstack/react-query'
import { ExternalLink, FileCheck2, Zap } from 'lucide-react'
import { Card } from '@/components/app/Page'
import { Hint } from '@/components/ui/tooltip'
import { api } from '@/lib/api'
import { big, day, pctSigned, tone } from '@/lib/format'

type Report = {
  date: string
  out_at: string | null
  press_url: string | null
  official: boolean
  filed_at: string | null
  verdict: 'beat' | 'miss' | 'mixed' | 'out'
  numbers: {
    revenue: number | null
    revenue_est: number | null
    revenue_pct: number | null
    eps: number | null
    eps_est: number | null
    eps_pct: number | null
  } | null
}

const VERDICT = { beat: 'Beat expectations', miss: 'Missed expectations', mixed: 'Mixed against expectations', out: 'Results are out' }
const usd = (v: number) => (v < 0 ? '−$' : '$') + big(Math.abs(v))

function Line({ label, value, est, pct, fmt }: { label: string; value: number | null; est: number | null; pct: number | null; fmt: (v: number) => string }) {
  if (value === null) return null
  return (
    <span className="num inline-flex items-baseline gap-1.5">
      <span className="text-muted-foreground">{label}</span>
      <b>{fmt(value)}</b>
      {est !== null && <span className="text-muted-foreground">vs {fmt(est)} expected</span>}
      {pct !== null && <b className={tone(pct)}>{pctSigned(pct, 1)}</b>}
    </span>
  )
}

/** "New results" bar on a stock page: what the earnings watch saw in the last two weeks (earnings_watch.py). */
export function ResultsBar({ t }: { t: string }) {
  const q = useQuery({
    queryKey: ['earnings', t],
    queryFn: () => api.get<{ report: Report | null }>(`/api/earnings/${t}`),
    refetchInterval: 60_000,
  })
  const r = q.data?.report
  if (!r) return null
  const n = r.numbers
  return (
    <Card className="flex flex-wrap items-center gap-x-5 gap-y-2 px-4 py-3 text-[13px]">
      <span className="inline-flex items-center gap-2 font-semibold">
        {r.official ? <FileCheck2 className="size-4 text-primary" /> : <Zap className="size-4 text-primary" />}
        New results · {day(r.date)}
        <Hint
          label={
            r.official
              ? 'From the 10-Q / 10-K the company filed with the SEC. The EPS is the adjusted EPS analysts estimate (Yahoo).'
              : 'Preliminary: read from the press release and Yahoo Finance minutes after the results. The official numbers replace them when the 10-Q is filed.'
          }
        >
          <span tabIndex={0} className="max-w-xs rounded-md bg-secondary px-1.5 py-0.5 text-xs font-normal text-muted-foreground">{r.official ? 'official' : 'preliminary'}</span>
        </Hint>
      </span>
      <span className={n && r.verdict !== 'out' ? tone(r.verdict === 'beat' ? 1 : r.verdict === 'miss' ? -1 : 0) : 'text-muted-foreground'}>
        {n ? VERDICT[r.verdict] : 'Results are out, numbers follow'}
      </span>
      {n && <Line label="Revenue" value={n.revenue} est={n.revenue_est} pct={n.revenue_pct} fmt={usd} />}
      {n && <Line label="EPS" value={n.eps} est={n.eps_est} pct={n.eps_pct} fmt={(v) => v.toFixed(2)} />}
      <span className="grow" />
      {r.press_url && (
        <a href={r.press_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline">
          Press release <ExternalLink className="size-3.5" />
        </a>
      )}
    </Card>
  )
}
