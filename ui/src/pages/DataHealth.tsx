import { useQuery } from '@tanstack/react-query'
import { CircleCheck, Search } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card, Page } from '@/components/app/Page'
import { Input } from '@/components/ui/input'
import { Segmented } from '@/components/ui/segmented'
import { api } from '@/lib/api'
import { big, fmt } from '@/lib/format'
import { useSettings } from '@/lib/settings'
import { cn, rowKeys } from '@/lib/utils'

type Problem = { field: string; value: number | null; why: string }
type Item = { ticker: string; name: string | null; kind: string; sector: string | null; quality: number | null; problems: Problem[] }
type Health = { companies: number; flagged: number; items: Item[]; fields: Record<string, { label: string; fmt: string }> }

const KIND: Record<string, string> = { bank: 'Bank', insurer: 'Insurer', reit: 'REIT' }
type Show = 'all' | 'odd' | 'missing'

function shown(p: Problem, f: Health['fields']) {
  if (p.value === null) return '–'
  const fm = f[p.field]?.fmt
  if (fm === 'usd') return big(p.value)
  if (fm === 'pct') return Math.abs(p.value) >= 10 ? `${Math.round(p.value * 100).toLocaleString('en-US')}%` : fmt(p.value, 'pct')
  if (fm === 'x') return fmt(p.value, 'x')
  return String(p.value)
}

/** Every company with a number that cannot be right, so you know which numbers to double-check. */
export function DataHealthPage() {
  const nav = useNavigate()
  const { pref, setPref } = useSettings()
  const show = pref<Show>('ui2', 'health_show', 'all')
  const [q, setQ] = useState('')
  const h = useQuery({ queryKey: ['data-health'], queryFn: () => api.get<Health>('/api/data-health') })
  const d = h.data
  const items = useMemo(() => {
    if (!d) return []
    const words = q.toLowerCase().split(/\s+/).filter(Boolean)
    return d.items
      .map((it) => ({ ...it, problems: it.problems.filter((p) => show === 'all' || (show === 'missing') === (p.value === null)) }))
      .filter((it) => it.problems.length && words.every((w) => `${it.ticker} ${it.name ?? ''}`.toLowerCase().includes(w)))
  }, [d, q, show])
  const odd = d ? d.items.filter((it) => it.problems.some((p) => p.value !== null)).length : 0
  const missing = d ? d.items.filter((it) => it.problems.some((p) => p.value === null)).length : 0

  return (
    <Page title="Data health">
      <p className="-mt-3 max-w-[760px] text-muted-foreground">
        Companies with a number that looks impossible (a margin above 100%, revenue halving in a year, a payout of 1,800%) or a line that
        was not found. These come from how a company tags its SEC filings, not from the market. Treat these numbers with care.
      </p>
      {d && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <Card className="p-4">
            <div className="text-muted-foreground">Companies checked</div>
            <div className="num text-2xl font-semibold">{d.companies}</div>
          </Card>
          <Card className="p-4">
            <div className="text-muted-foreground">With an odd number</div>
            <div className="num text-2xl font-semibold">{odd}</div>
          </Card>
          <Card className="p-4">
            <div className="text-muted-foreground">With a missing line</div>
            <div className="num text-2xl font-semibold">{missing}</div>
          </Card>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative w-64">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find a company" className="pl-8" />
        </div>
        <Segmented<Show>
          label="Show"
          value={show}
          onChange={(v) => setPref('ui2', 'health_show', v)}
          options={[
            { value: 'all', label: 'Everything' },
            { value: 'odd', label: 'Odd numbers' },
            { value: 'missing', label: 'Missing lines' },
          ]}
        />
      </div>
      {h.isLoading && <Card className="p-8 text-center text-muted-foreground">Checking every company…</Card>}
      {h.isError && <Card className="p-8 text-center text-muted-foreground">Couldn't check the data. Try again in a moment.</Card>}
      {d && !items.length && (
        <Card className="flex items-center justify-center gap-2 p-8 text-muted-foreground">
          <CircleCheck className="size-4 text-up" /> {d.items.length ? 'Nothing here for this filter.' : 'No odd numbers found.'}
        </Card>
      )}
      {items.length > 0 && (
        <Card className="overflow-x-auto p-0">
          <table className="w-full text-left">
            <thead className="text-[12px] text-muted-foreground">
              <tr className="border-b">
                <th className="px-4 py-2 font-medium">Company</th>
                <th className="px-4 py-2 font-medium">What looks wrong</th>
                <th className="px-4 py-2 text-right font-medium">Value</th>
                <th className="px-4 py-2 text-right font-medium">Checks passed</th>
              </tr>
            </thead>
            <tbody onKeyDown={(e) => rowKeys(e, (row) => row.dataset.href && nav(row.dataset.href))}>
              {items.map((it) => (
                <tr
                  key={it.ticker}
                  tabIndex={0}
                  data-href={`/stock/${it.ticker}`}
                  onClick={() => nav(`/stock/${it.ticker}`)}
                  className="cursor-pointer border-b align-top outline-none last:border-0 hover:bg-panel focus-visible:bg-panel"
                >
                  <td className="px-4 py-2.5">
                    <div className="font-semibold">{it.ticker}</div>
                    <div className="max-w-56 truncate text-[12px] text-muted-foreground">
                      {it.name}
                      {KIND[it.kind] ? ` · ${KIND[it.kind]}` : ''}
                    </div>
                  </td>
                  <td className="px-4 py-2.5">
                    {it.problems.map((p) => (
                      <div key={p.field + p.why} className="py-0.5">
                        {p.why}
                      </div>
                    ))}
                  </td>
                  <td className="num px-4 py-2.5 text-right">
                    {it.problems.map((p) => (
                      <div key={p.field + p.why} className={cn('py-0.5', p.value === null && 'text-muted-foreground')}>
                        {shown(p, d!.fields)}
                      </div>
                    ))}
                  </td>
                  <td className={cn('num px-4 py-2.5 text-right', (it.quality ?? 1) < 0.95 && 'text-flag')}>{it.quality != null ? fmt(it.quality, 'pct') : '–'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </Page>
  )
}
