import { useQuery } from '@tanstack/react-query'
import { ExternalLink, Loader2, TriangleAlert } from 'lucide-react'
import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Card } from '@/components/app/Page'
import { Empty, H2 } from '@/components/app/bits'
import { BAR_FIT, BAR_TIP, EChart, FOCUS, axisTip, baseOption, useChartColors } from '@/components/chart/EChart'
import { Button } from '@/components/ui/button'
import { Segmented } from '@/components/ui/segmented'
import { Hint } from '@/components/ui/tooltip'
import { api } from '@/lib/api'
import { big, day, pctSigned, tone } from '@/lib/format'
import { useSettings } from '@/lib/settings'
import { cn } from '@/lib/utils'

// Ownership tab: insider trades (SEC Form 4, stockterminal/insiders.py) and big investors (Yahoo's largest holders and
// famous investors' 13F filings, stockterminal/investors.py). Amounts in US dollars.

type Trade = {
  accn: string; filed: string; date: string | null; owner: string | null; role: string | null; code: string; kind: string
  ad: string | null; shares: number; price: number | null; value: number | null; owned_after: number | null
  direct: string | null; plan: boolean; change: number | null; url: string
}
type Total = { n: number; people: number; shares: number; value: number }
type InsiderData = {
  available: boolean; reason?: string; foreign?: boolean; error?: string | null; left?: number; filings?: number; years?: number
  trades?: Trade[]; buys_3m?: Total; sells_3m?: Total; buys_12m?: Total; sells_12m?: Total; months?: { month: string; buy: number; sell: number }[]
}
type Famous = {
  cik: number; investor: string | null; fund: string | null; period: string; filed: string; old: boolean; shares: number; value: number
  weight: number | null; change: number | 'new' | 'sold' | null; prev_shares: number | null; prev_period: string | null; url: string
}
type Top = { holder: string; shares: number | null; value: number | null; pct: number | null; change: number | null; date: string | null }
type InvestorData = {
  yahoo: { insiders?: number | null; institutions?: number | null; institutions_float?: number | null; institutions_count?: number | null; top?: Top[]; error?: string; stale?: boolean } | null
  famous: { holders: Famous[]; matched_by: string } | null
  state: { running: boolean; loaded: number; total: number; error: string | null } | null
  followed: { cik: number; name: string }[]
}

function usd(v: number | null | undefined) {
  if (v == null) return '–'
  const a = Math.abs(v)
  if (a >= 1e6) return '$' + big(v)
  if (a >= 1e3) return '$' + (v / 1e3).toFixed(a >= 1e5 ? 0 : 1) + 'K'
  return '$' + Math.round(v).toLocaleString('en-US')
}
const price = (v: number | null | undefined) => (v == null ? '–' : '$' + v.toFixed(2))
const pct = (v: number | null | undefined, dec = 1) => (v == null ? '–' : (v * 100).toFixed(dec) + '%')
const shares = (v: number | null | undefined) => (v == null ? '–' : v >= 1e6 ? big(v) : Math.round(v).toLocaleString('en-US'))
const quarter = (d: string) => `Q${Math.floor((+d.slice(5, 7) - 1) / 3) + 1} ${d.slice(0, 4)}`

const KIND_TIP: Record<string, string> = {
  P: 'Bought on the open market, with their own money', S: 'Sold on the open market', A: 'Shares given by the company (pay, a bonus)',
  M: 'Turned options into shares', X: 'Turned options into shares', F: 'Shares kept back by the company to pay the tax on an award',
  G: 'Given away (to family, a charity)', D: 'Given back to the company', C: 'A conversion into shares', J: 'Other change',
}

function Tile({ label, value, sub, hint, className }: { label: string; value: ReactNode; sub?: ReactNode; hint?: string; className?: string }) {
  const body = (
    <Card className="h-full p-3.5">
      <div className={cn('text-xs text-muted-foreground', hint && 'underline decoration-dotted underline-offset-2')}>{label}</div>
      <div className={cn('num mt-0.5 text-lg font-bold tracking-tight whitespace-nowrap', className)}>{value}</div>
      <div className="num min-h-4 text-xs text-muted-foreground">{sub}</div>
    </Card>
  )
  return hint ? <Hint label={hint}><div>{body}</div></Hint> : body
}

export function Ownership({ t }: { t: string }) {
  // what is stored first (instant), then the new Form 4s from the SEC, a batch at a time until all are in
  const stored = useQuery({ queryKey: ['company', t, 'insiders'], queryFn: () => api.get<InsiderData>(`/api/insiders/${t}`) })
  const fresh = useQuery({
    queryKey: ['company', t, 'insiders', 'fresh'],
    queryFn: () => api.get<InsiderData>(`/api/insiders/${t}?refresh=1`),
    enabled: !!stored.data?.available,
    retry: false,
    refetchInterval: (q) => (q.state.data?.left && !q.state.data.error ? 300 : false),
  })
  const inv = useQuery({
    queryKey: ['company', t, 'investors'],
    queryFn: () => api.get<InvestorData>(`/api/investors/${t}?refresh=1`),
    retry: false,
    refetchInterval: (q) => (q.state.data?.state?.running ? 4000 : false),
  })
  const d = fresh.data ?? stored.data
  const y = inv.data?.yahoo
  const loadingMore = fresh.isFetching || !!fresh.data?.left

  return (
    <>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Tile label="Insiders own" value={pct(y?.insiders, 2)} sub="of all shares" hint="Shares held by directors, officers and owners of more than 10% (Yahoo)." />
        <Tile label="Institutions own" value={pct(y?.institutions)} sub={y?.institutions_count ? `${Math.round(y.institutions_count).toLocaleString('en-US')} institutions` : ''}
          hint="Shares held by funds, banks and other institutions that file a 13F report (Yahoo)." />
        <Tile label="Insider buys, 12 months" value={d?.buys_12m ? usd(d.buys_12m.value) : '–'} className={d?.buys_12m?.value ? 'text-up' : ''}
          sub={d?.buys_12m ? (d.buys_12m.n ? `${d.buys_12m.people} ${d.buys_12m.people === 1 ? 'person' : 'people'}, ${d.buys_12m.n} buy${d.buys_12m.n === 1 ? '' : 's'}` : 'none') : ''}
          hint="Shares insiders bought on the open market with their own money in the last 12 months. Awards and option exercises are not counted." />
        <Tile label="Insider sales, 12 months" value={d?.sells_12m ? usd(d.sells_12m.value) : '–'} className={d?.sells_12m?.value ? 'text-down' : ''}
          sub={d?.sells_12m ? (d.sells_12m.n ? `${d.sells_12m.people} ${d.sells_12m.people === 1 ? 'person' : 'people'}, ${d.sells_12m.n} sale${d.sells_12m.n === 1 ? '' : 's'}` : 'none') : ''}
          hint="Shares insiders sold on the open market in the last 12 months. Many sales are planned in advance (10b5-1 plans) or pay for taxes." />
      </div>
      <InsiderCard t={t} d={d} loadingMore={loadingMore} loading={stored.isLoading} failed={stored.isError} />
      <div className="grid grid-cols-1 items-start gap-4 2xl:grid-cols-2">
        <FamousCard inv={inv.data} failed={inv.isError} />
        <TopHolders y={y} loading={inv.isLoading} />
      </div>
    </>
  )
}

// ------------------------------------------------------------------------------------------------ insider trades
function InsiderCard({ t, d, loading, loadingMore, failed }: { t: string; d?: InsiderData; loading: boolean; loadingMore: boolean; failed: boolean }) {
  const { pref, setPref } = useSettings()
  const c = useChartColors()
  const show = pref<string>('ui2', 'own_ins_show', 'market')
  const [more, setMore] = useState(false)
  const trades = useMemo(() => (d?.trades ?? []).filter((g) => show === 'all' || g.code === 'P' || g.code === 'S'), [d, show])

  const option = useMemo(() => {
    const ms = d?.months ?? []
    if (!ms.some((m) => m.buy || m.sell)) return null
    const o = baseOption(c)
    const label = (m: string) => new Date(m + '-01T00:00:00').toLocaleDateString('en-US', { month: 'short', year: '2-digit' })
    return {
      ...o,
      xAxis: { ...o.xAxis, type: 'category', data: ms.map((m) => label(m.month)) },
      yAxis: { ...o.yAxis, scale: false, axisLabel: { ...o.yAxis.axisLabel, formatter: (v: number) => (v < 0 ? '−$' : '$') + big(Math.abs(v)) } },
      tooltip: { ...o.tooltip, ...BAR_TIP, formatter: axisTip(c, (_, v) => usd(Math.abs(v))) },
      series: [
        { type: 'bar', name: 'Bought', stack: 'x', ...BAR_FIT, ...FOCUS, data: ms.map((m) => m.buy || null), itemStyle: { color: c.up, borderRadius: [3, 3, 0, 0] } },
        { type: 'bar', name: 'Sold', stack: 'x', ...BAR_FIT, ...FOCUS, data: ms.map((m) => (m.sell ? -m.sell : null)), itemStyle: { color: c.down, borderRadius: [0, 0, 3, 3] } },
      ],
    }
  }, [d, c])

  const rows = more ? trades : trades.slice(0, 40)
  return (
    <Card className="p-5">
      <H2 side={
        <div className="flex flex-wrap items-center gap-2">
          {loadingMore && <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground"><Loader2 className="size-3.5 animate-spin" /> Getting filings from the SEC…</span>}
          <Segmented label="Which trades" value={show} onChange={(v) => setPref('ui2', 'own_ins_show', v)}
            options={[{ value: 'market', label: 'Buys and sales' }, { value: 'all', label: 'All' }]} />
        </div>
      }>
        Insider trades
      </H2>
      {d?.error && (
        <p className="mb-3 flex items-center gap-1.5 text-[13px] text-flag"><TriangleAlert className="size-3.5" /> {d.error}. Showing what is stored.</p>
      )}
      {!d ? (
        <Empty>{failed ? 'Could not load the insider trades.' : loading ? 'Loading…' : ''}</Empty>
      ) : !d.available ? (
        <Empty>{d.reason}</Empty>
      ) : (
        <>
          {option && (
            <div className="mb-4">
              <div className="mb-1 flex items-center gap-3 text-xs text-muted-foreground">
                <span>Bought and sold on the open market per month</span>
                <span className="inline-flex items-center gap-1.5"><span className="size-2.5 rounded-[3px]" style={{ background: c.up }} /> Bought</span>
                <span className="inline-flex items-center gap-1.5"><span className="size-2.5 rounded-[3px]" style={{ background: c.down }} /> Sold</span>
              </div>
              <EChart option={option} hoverTip className="h-[200px]" />
            </div>
          )}
          {!trades.length ? (
            <Empty>
              {loadingMore ? 'Getting the Form 4 filings from the SEC…'
                : d.foreign ? `${t} is a foreign company: its insiders do not file Form 4 with the SEC.`
                  : show === 'market' && d.trades?.length ? `No open-market buys or sales by insiders in the last ${d.years} years. Choose "All" to see awards and option exercises.`
                    : `No insider trades filed in the last ${d.years} years.`}
            </Empty>
          ) : (
            <div className="-mx-5 overflow-x-auto">
              <table className="tbl tight">
                <thead>
                  <tr>
                    <th>Date</th>
                    <th className="text-left!">Insider</th>
                    <th className="text-left!">Type</th>
                    <th>Shares</th>
                    <th>Price</th>
                    <th>Value</th>
                    <th><Hint label="Shares this person holds after the trade"><span className="cursor-help">Owns after</span></Hint></th>
                    <th><Hint label="How much their holding grew or shrank with this trade"><span className="cursor-help">Change</span></Hint></th>
                    <th aria-label="Filing" />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((g) => {
                    const sign = g.ad === 'D' ? -1 : 1
                    return (
                      <tr key={`${g.accn}-${g.date}-${g.code}-${g.direct}`}>
                        <td className="text-left! whitespace-nowrap">{day(g.date)}</td>
                        <td className="max-w-[280px] text-left!">
                          <div className="truncate font-medium">{g.owner ?? '–'}</div>
                          <div className="truncate text-xs text-muted-foreground">{g.role ?? ''}{g.direct === 'I' ? (g.role ? ' · ' : '') + 'held indirectly' : ''}</div>
                        </td>
                        <td className="text-left! whitespace-nowrap">
                          <Hint label={(KIND_TIP[g.code] ?? g.kind) + (g.plan ? '. Under a trading plan set up in advance (10b5-1).' : '')}>
                            <span className={cn('cursor-help', g.code === 'P' && 'font-medium text-up', g.code === 'S' && 'text-down')}>{g.kind}{g.plan ? ' *' : ''}</span>
                          </Hint>
                        </td>
                        <td className={tone(sign)}>{sign < 0 ? '−' : '+'}{shares(g.shares)}</td>
                        <td>{price(g.price)}</td>
                        <td>{usd(g.value)}</td>
                        <td>{shares(g.owned_after)}</td>
                        <td className={tone(g.change)}>{g.change == null ? '–' : pctSigned(g.change, 1)}</td>
                        <td>
                          <a href={g.url} target="_blank" rel="noreferrer" aria-label="The filing at the SEC" className="inline-flex text-muted-foreground hover:text-primary">
                            <ExternalLink className="size-3.5" />
                          </a>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
          <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
            <span>From the Form 4 filings at the SEC, last {d.years} years. * = under a trading plan set up in advance (10b5-1). Options are left out.</span>
            <div className="grow" />
            {trades.length > 40 && <Button size="sm" variant="ghost" onClick={() => setMore(!more)}>{more ? 'Show fewer' : `Show all ${trades.length}`}</Button>}
          </div>
        </>
      )}
    </Card>
  )
}

// ------------------------------------------------------------------------------------------------ famous investors
function FamousCard({ inv, failed }: { inv?: InvestorData; failed: boolean }) {
  const f = inv?.famous
  const st = inv?.state
  const change = (r: Famous) => {
    if (r.change === 'new') return <span className="font-medium text-up">New</span>
    if (r.change === 'sold') return <span className="font-medium text-down">Sold all</span>
    if (r.change == null) return '–'
    if (Math.abs(r.change) < 0.0005) return <span className="text-muted-foreground">No change</span>
    return <span className={tone(r.change)}>{pctSigned(r.change, 1)}</span>
  }
  const names = (inv?.followed ?? []).map((x) => x.name).join(', ')
  return (
    <Card className="p-5">
      <H2 side={st?.running ? <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground"><Loader2 className="size-3.5 animate-spin" /> Loading 13F filings ({st.loaded} of {st.total})…</span> : null}>
        Famous investors
      </H2>
      {!inv ? (
        <Empty>{failed ? 'Could not load the investors.' : 'Loading…'}</Empty>
      ) : !f ? (
        <Empty>13F holdings are matched to SEC companies only.</Empty>
      ) : !f.holders.length ? (
        <Empty>{st?.running && !st.loaded ? 'Loading the 13F filings of the investors followed (once a week, about a minute the first time)…' : 'None of the famous investors followed held this stock in their last two 13F reports.'}</Empty>
      ) : (
        <div className="-mx-5 overflow-x-auto">
          <table className="tbl tight">
            <thead>
              <tr>
                <th>Investor</th>
                <th>Shares</th>
                <th>Value</th>
                <th><Hint label="This stock as a share of everything in their 13F report"><span className="cursor-help">Of portfolio</span></Hint></th>
                <th><Hint label="Shares now against the quarter before"><span className="cursor-help">Change</span></Hint></th>
                <th>Quarter</th>
              </tr>
            </thead>
            <tbody>
              {f.holders.map((r) => (
                <tr key={r.cik} className={r.old ? 'text-muted-foreground' : undefined}>
                  <td className="max-w-[240px]">
                    <a href={r.url} target="_blank" rel="noreferrer" className="block hover:text-primary">
                      <div className="truncate font-medium">{r.investor ?? r.fund}</div>
                      {r.investor && <div className="truncate text-xs text-muted-foreground">{r.fund}</div>}
                    </a>
                  </td>
                  <td>{r.shares ? shares(r.shares) : '–'}</td>
                  <td>{r.value ? usd(r.value) : '–'}</td>
                  <td>{r.weight ? pct(r.weight) : '–'}</td>
                  <td>{change(r)}</td>
                  <td className="whitespace-nowrap text-muted-foreground">
                    {r.old ? (
                      <Hint label="No newer 13F report: the fund stopped filing, or now files under another name. These holdings may no longer be current.">
                        <span className="cursor-help">{quarter(r.period)} · old</span>
                      </Hint>
                    ) : quarter(r.period)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Hint label={`Followed: ${names}`}>
        <p className="mt-3 cursor-help text-xs text-muted-foreground">
          From their 13F reports at the SEC: what {inv?.followed.length ?? 'well-known'} well-known investors held at the end of each quarter, filed up to 45 days later.
          Value at the quarter's end.{f?.matched_by === 'name' ? ' Matched by company name.' : ''}
        </p>
      </Hint>
    </Card>
  )
}

// ------------------------------------------------------------------------------------------------ largest holders (Yahoo)
function TopHolders({ y, loading }: { y?: InvestorData['yahoo']; loading: boolean }) {
  const top = y?.top ?? []
  return (
    <Card className="p-5">
      <H2>Largest institutional holders</H2>
      {!top.length ? (
        <Empty>{loading ? 'Loading…' : y?.error ? 'Could not reach Yahoo for the largest holders.' : 'Yahoo has no holders for this stock.'}</Empty>
      ) : (
        <div className="-mx-5 overflow-x-auto">
          <table className="tbl tight">
            <thead>
              <tr>
                <th>Holder</th>
                <th>Shares</th>
                <th><Hint label="Share of all the company's shares"><span className="cursor-help">Of company</span></Hint></th>
                <th>Value</th>
                <th><Hint label="Shares now against their report before"><span className="cursor-help">Change</span></Hint></th>
                <th>Reported</th>
              </tr>
            </thead>
            <tbody>
              {top.map((h) => (
                <tr key={h.holder}>
                  <td className="max-w-[220px] truncate">{h.holder}</td>
                  <td>{shares(h.shares)}</td>
                  <td>{pct(h.pct, 2)}</td>
                  <td>{usd(h.value)}</td>
                  <td className={tone(h.change)}>{h.change == null ? '–' : pctSigned(h.change, 1)}</td>
                  <td className="whitespace-nowrap text-muted-foreground">{day(h.date)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-3 text-xs text-muted-foreground">From Yahoo, based on the holders' 13F reports.{y?.stale ? ' Yahoo could not be reached: the last stored list.' : ''}</p>
    </Card>
  )
}
