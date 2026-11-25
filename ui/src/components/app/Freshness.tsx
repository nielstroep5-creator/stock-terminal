import { useQuery } from '@tanstack/react-query'
import { LoaderCircle } from 'lucide-react'
import { Hint } from '@/components/ui/tooltip'
import { api } from '@/lib/api'
import { day } from '@/lib/format'
import { cn } from '@/lib/utils'

type Status = { prices_date: string | null; filings_date: string | null; quotes: 'live' | 'offline' | 'unknown'; recalc: { done: number; total: number } | null }

/** Days between a stored date and today (weekends count, so a Monday still shows Friday's closes as fresh). */
function age(d: string | null) {
  if (!d) return Infinity
  return (Date.now() - +new Date(d.slice(0, 10) + 'T00:00:00')) / 864e5
}

/** Top bar: how fresh the numbers are, and the ratio recalculation that runs after a starter pack or an update. */
export function Freshness() {
  const q = useQuery({
    queryKey: ['data-status'],
    queryFn: () => api.get<Status>('/api/data/status'),
    refetchInterval: (s) => (s.state.data?.recalc ? 3000 : 60_000),
    retry: false,
  })
  const s = q.data
  if (!s) return null
  const dates = `Daily closes up to ${day(s.prices_date)}${s.filings_date ? `, newest report filed ${day(s.filings_date)}` : ''}.`
  if (s.recalc) {
    const pct = Math.round((s.recalc.done / Math.max(1, s.recalc.total)) * 100)
    return (
      <Hint label={`The ratios (margins, growth, valuation) are being worked out again from the stored numbers, once, after new data or an app update. Some ratios can be empty until it is done. ${dates}`}>
        <span className="hidden h-7 cursor-help items-center gap-1.5 rounded-md border px-2 text-xs text-muted-foreground sm:inline-flex">
          <LoaderCircle className="size-3.5 animate-spin" />
          Recalculating ratios {pct}%
        </span>
      </Hint>
    )
  }
  const offline = s.quotes === 'offline'
  const stale = age(s.prices_date) > 4
  if (!offline && !stale) {
    return (
      <Hint label={`${s.quotes === 'live' ? 'Prices are live from Yahoo. ' : ''}${dates}`}>
        <span className="hidden h-7 cursor-help items-center gap-1.5 px-1.5 text-xs text-muted-foreground lg:inline-flex">
          <span className={cn('size-1.5 rounded-full', s.quotes === 'live' ? 'bg-up' : 'bg-muted-foreground/50')} />
          {s.quotes === 'live' ? 'Live' : `Prices of ${day(s.prices_date, false)}`}
        </span>
      </Hint>
    )
  }
  return (
    <Hint
      label={
        offline
          ? `Yahoo does not answer (no internet?), so prices are the last stored daily closes. ${dates}`
          : `The daily update has not run for a few days. ${dates} It runs every morning at 07:00 while the computer is on (Settings > Data and accounts > Daily update).`
      }
    >
      <span className="hidden h-7 cursor-help items-center gap-1.5 rounded-md border border-flag/40 bg-flag/10 px-2 text-xs font-medium text-flag sm:inline-flex">
        <span className="size-1.5 rounded-full bg-flag" />
        {offline ? 'Offline' : 'Old prices'}, closes of {day(s.prices_date, false)}
      </span>
    </Hint>
  )
}
