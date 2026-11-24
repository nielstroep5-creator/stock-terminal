import { CalendarDays, ChevronDown } from 'lucide-react'
import { useState } from 'react'
import type { ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card } from '@/components/app/Page'
import { Empty, H2, Kv, Stat } from '@/components/app/bits'
import { useChartColors } from '@/components/chart/EChart'
import { Segmented } from '@/components/ui/segmented'
import { Hint } from '@/components/ui/tooltip'
import { big, fmt, tone } from '@/lib/format'
import { useSettings } from '@/lib/settings'
import { cn } from '@/lib/utils'
import { CUR_SYM, cash, pctTxt, t212Symbol, useMoney, usePortfolio, useStoredThenFresh } from './common'
import type { Fx, N } from './common'

const has = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const href = (t212: string) => `/portfolio/${encodeURIComponent(t212)}`
const Note = ({ children }: { children: ReactNode }) => <p className="text-xs leading-relaxed text-muted-foreground">{children}</p>
function Loading({ children }: { children: ReactNode }) {
  return <Card><Empty>{children}</Empty></Card>
}
/** The account and the € / $ choice, for the tabs (they come without their own rate). */
function usePfMoney() {
  const { data } = usePortfolio()
  return useMoney(data?.account_currency, data?.other_currency, data?.fx?.rate)
}

/* ------------------------------------------------------------------ allocation (stockterminal/allocation.py) */
type AlRow = { name: string; weight: number; bench: N; holdings: { name: string; weight: number }[] }
type Alloc = { empty: boolean; dims: Record<string, AlRow[]>; bench_label?: string; funds?: { name: string; source?: string }[]; notes?: string[] }

export function AllocationTab() {
  const { data: d, loading } = useStoredThenFresh<Alloc>('alloc', '/api/portfolio/allocation')
  const { pref, setPref } = useSettings()
  const c = useChartColors()
  if (loading || !d) return <Loading>Looking up sectors and countries…</Loading>
  if (d.empty) return <Loading>No open positions to split up.</Loading>
  const where = pref<string>('state', 'alWhere', 'region') === 'country' ? 'country' : 'region'
  const curDim = pref<string>('state', 'alCur', 'under') === 'trade' ? 'trade_currency' : 'currency'
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-4 text-[13px]">
        <span className="inline-flex items-center gap-1.5"><span className="h-2 w-4 rounded-full" style={{ background: c.series[0] }} /> Your portfolio</span>
        {d.bench_label && <span className="inline-flex items-center gap-1.5"><span className="h-2 w-4 rounded-full bg-muted-foreground/35" /> {d.bench_label}</span>}
        {d.bench_label && <span className="text-muted-foreground">Difference in percentage points</span>}
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="p-4"><H2>Sector</H2><AlBars rows={d.dims.sector ?? []} color={c.series[0]} /></Card>
        <Card className="p-4">
          <H2 side={<Segmented label="Region or country" value={where} onChange={(v) => setPref('state', 'alWhere', v)} options={[{ value: 'region', label: 'Region' }, { value: 'country', label: 'Country' }]} />}>Where</H2>
          <AlBars rows={d.dims[where] ?? []} color={c.series[0]} />
        </Card>
        <Card className="p-4">
          <H2 side={<Segmented label="Which currency" value={curDim === 'currency' ? 'under' : 'trade'} onChange={(v) => setPref('state', 'alCur', v)} options={[{ value: 'under', label: 'Underlying' }, { value: 'trade', label: 'Trading' }]} />}>Currency</H2>
          <AlBars rows={d.dims[curDim] ?? []} color={c.series[0]} />
        </Card>
      </div>
      <Note>
        {d.funds?.length ? `ETFs are split into what they hold: ${d.funds.map((f) => `${f.name} (${f.source || 'Yahoo fund data'})`).join(', ')}. ` : ''}
        Shares: sector and country from Yahoo. Underlying currency = the currency of each company's home country; trading currency = what you pay in. Hover a row to see which holdings make it up.
        {!!d.notes?.length && <span className="block text-down">{d.notes.join(' · ')}</span>}
      </Note>
    </div>
  )
}

function AlBars({ rows: all, color }: { rows: AlRow[]; color: string }) {
  let rows = all
  const max = 12
  if (rows.length > max) {
    // the long tail as one line
    const rest = rows.slice(max - 1).filter((r) => r.name !== 'Unknown')
    const unk = rows.find((r) => r.name === 'Unknown')
    const other: AlRow = { name: `Other (${rest.length})`, weight: rest.reduce((a, r) => a + (r.weight || 0), 0), bench: rest.some((r) => has(r.bench)) ? rest.reduce((a, r) => a + (r.bench || 0), 0) : null, holdings: [] }
    rows = [...rows.slice(0, max - 1).filter((r) => r.name !== 'Unknown'), other, ...(unk ? [unk] : [])]
  }
  const top = Math.max(...rows.map((r) => Math.max(r.weight || 0, r.bench || 0)), 0.0001)
  if (!rows.length) return <p className="text-[13px] text-muted-foreground">Nothing yet.</p>
  return (
    <div className="flex flex-col gap-2.5">
      {rows.map((r) => {
        const diff = has(r.bench) ? (r.weight - r.bench) * 100 : null
        const body = (
          <div className={cn('grid grid-cols-[minmax(0,1fr)_3.2rem_3.2rem] items-baseline gap-x-2 text-[13px]', r.name === 'Unknown' && 'opacity-60')}>
            <span className="truncate">{r.name}</span>
            <span className="num text-right font-medium">{(r.weight * 100).toFixed(1)}%</span>
            <span className={cn('num text-right text-xs', diff === null ? '' : diff >= 0 ? 'text-up' : 'text-down')}>{diff === null ? '' : `${diff >= 0 ? '+' : '−'}${Math.abs(diff).toFixed(1)}`}</span>
            <span className="col-span-3 mt-1 flex flex-col gap-0.5">
              <span className="block h-1.5 rounded-full" style={{ width: `${(r.weight / top) * 100}%`, background: color }} />
              {has(r.bench) && <span className="block h-1.5 rounded-full bg-muted-foreground/35" style={{ width: `${(r.bench / top) * 100}%` }} />}
            </span>
          </div>
        )
        return r.holdings?.length ? (
          <Hint key={r.name} label={<span className="flex flex-col">{r.holdings.map((h) => <span key={h.name}>{h.name}: {(h.weight * 100).toFixed(1)}%</span>)}</span>}>
            <div className="cursor-default">{body}</div>
          </Hint>
        ) : (
          <div key={r.name}>{body}</div>
        )
      })}
    </div>
  )
}

/* ------------------------------------------------------------------ X-ray (stockterminal/xray.py) */
type XRow = { key: string; name: string; isin: string | null; total: number; direct: number; via: Record<string, number>; t212_ticker: string | null }
type XEtf = { id: string; name: string; n: number; same?: boolean; twin?: string; as_of?: string; cash: number; top: { name: string; weight: number; own?: boolean; t212_ticker?: string | null }[] }
type Xray = {
  empty: boolean; total: number; account_currency: string; rows: XRow[]; rest?: { count: number; weight: number }; cash_in_etfs?: number
  blind?: { name: string; weight: number }[]; etfs: XEtf[]; overlap?: { a: string; b: string; overlap: number }[]
  stats: { companies: number; effective: N; top10: number; direct: number; looked: number }
  double?: { name: string; total: number; direct: number; via: number }[]; hidden?: { name: string; total: number }[]; notes?: string[]
}
const p1 = (v: N) => (!has(v) ? '–' : v > 0 && v < 0.00005 ? '<0.01%' : (v * 100).toFixed(v >= 0.1 ? 1 : 2) + '%')

export function XrayTab() {
  const nav = useNavigate()
  const c = useChartColors()
  const { pref, setPref } = useSettings()
  const { data: d, loading } = useStoredThenFresh<Xray>('xray', '/api/portfolio/xray')
  if (loading || !d) return <Loading>Looking inside your ETFs (SEC holdings reports)… the first time takes a little while.</Loading>
  if (d.empty) return <Loading>No open positions to look through.</Loading>
  const S = d.stats
  const etfs = d.etfs || []
  const col: Record<string, string> = { direct: c.series[0] }
  etfs.forEach((e, i) => (col[e.id] = c.series[(i + 1) % c.series.length]))
  const short = (e?: XEtf) => (e ? t212Symbol(e.id) || e.name : '')
  const money = pref<string>('state', 'xrUnit', 'pct') === 'money'
  const val = (w: number) => (money ? cash(w * d.total, d.account_currency, 0) : p1(w))
  const all = pref<string>('state', 'xrAll', '') === 'all'
  const top = Math.max(...d.rows.map((r) => r.total), 0.0001)
  const pick = etfs.find((e) => e.id === pref<string>('state', 'xrEtf', '')) ?? etfs[0]
  const restTxt = [
    d.rest?.count ? `${d.rest.count.toLocaleString('en-US')} smaller companies together ${val(d.rest.weight)}` : '',
    (d.cash_in_etfs ?? 0) > 0.0005 ? `cash and other assets inside ETFs ${val(d.cash_in_etfs!)}` : '',
    ...(d.blind || []).map((b) => `${b.name} ${val(b.weight)} (not looked through)`),
  ].filter(Boolean).join(' · ')
  return (
    <div className="flex flex-col gap-4">
      <Card className="grid grid-cols-2 gap-x-6 gap-y-4 p-4 md:grid-cols-4">
        <Stat label="Companies you own" tip="Every company you hold, yourself or inside an ETF." value={S.companies.toLocaleString('en-US')} sub={`${etfs.length} ETF${etfs.length === 1 ? '' : 's'} looked through`} />
        <Stat label="Acts like" tip="1 / sum of squared weights per company. Many tiny positions add little spread; a few big ones dominate." value={has(S.effective) ? `${S.effective.toFixed(0)} equal` : '–'} sub="companies (effective number)" />
        <Stat label="Top 10 companies" value={p1(S.top10)} sub="of your invested money" />
        <Stat label="Direct shares" value={p1(S.direct)} sub={`ETFs ${p1(1 - S.direct)}`} />
      </Card>
      <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <Card className="min-w-0 p-4">
          <H2 side={<Segmented label="Show as" value={money ? 'money' : 'pct'} onChange={(v) => setPref('state', 'xrUnit', v)} options={[{ value: 'pct', label: '%' }, { value: 'money', label: CUR_SYM[d.account_currency] ?? d.account_currency }]} />}>Your real positions</H2>
          <div className="mb-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1.5"><span className="size-2.5 rounded-sm" style={{ background: col.direct }} /> Direct</span>
            {etfs.map((e) => <span key={e.id} className="inline-flex items-center gap-1.5"><span className="size-2.5 rounded-sm" style={{ background: col[e.id] }} /> via {short(e)}</span>)}
          </div>
          <div className="overflow-x-auto">
            <table className="tbl tight [&_td:nth-child(2)]:text-left! [&_th:nth-child(2)]:text-left!">
              <thead>
                <tr><th /><th>Company</th><th>Total</th><th>Direct</th>{etfs.map((e) => <th key={e.id} title={e.name}>via {short(e)}</th>)}<th className="w-40" /></tr>
              </thead>
              <tbody>
                {(all ? d.rows : d.rows.slice(0, 25)).map((r, i) => {
                  const parts = [['direct', r.direct] as const, ...etfs.map((e) => [e.id, r.via[e.id] || 0] as const)].filter(([, w]) => w > 0)
                  return (
                    <tr key={r.key} className={cn(r.t212_ticker && 'cursor-pointer')} onClick={() => r.t212_ticker && nav(href(r.t212_ticker))}>
                      <td className="text-muted-foreground">{i + 1}</td>
                      <td className="max-w-64 truncate font-medium" title={r.isin || ''}>{r.name}{!!r.direct && !!Object.keys(r.via).length && <span className="ml-1.5 rounded bg-secondary px-1.5 py-0.5 text-[10.5px] font-medium text-muted-foreground">twice</span>}</td>
                      <td className="font-semibold">{val(r.total)}</td>
                      <td>{r.direct ? val(r.direct) : '–'}</td>
                      {etfs.map((e) => <td key={e.id}>{r.via[e.id] ? val(r.via[e.id]) : '–'}</td>)}
                      <td>
                        <div className="flex h-2 overflow-hidden rounded-full" style={{ width: `${(r.total / top) * 100}%` }}>
                          {parts.map(([k, w]) => <span key={k} style={{ flex: w, background: col[k] }} />)}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            {restTxt && <span>{restTxt}.</span>}
            {d.rows.length > 25 && <button type="button" className="font-medium text-primary hover:underline" onClick={() => setPref('state', 'xrAll', all ? '' : 'all')}>{all ? 'Show top 25' : `Show all ${d.rows.length}`}</button>}
          </div>
        </Card>
        <div className="flex flex-col gap-4">
          <Card className="p-4">
            <H2>Bought twice</H2>
            <p className="mb-2 text-xs text-muted-foreground">Shares you hold yourself and also through ETFs: your real position is bigger than the holdings list shows.</p>
            {d.double?.length ? (
              d.double.map((x) => (
                <div key={x.name} className="flex items-baseline justify-between gap-3 border-b py-2 text-[13px] last:border-0">
                  <span className="min-w-0"><span className="block truncate font-medium">{x.name}</span><span className="text-xs text-muted-foreground">{val(x.direct)} direct + {val(x.via)} via ETFs</span></span>
                  <span className="num font-semibold">{val(x.total)}</span>
                </div>
              ))
            ) : (
              <p className="text-[13px] text-muted-foreground">None of your own shares is also inside your ETFs.</p>
            )}
            {!!d.hidden?.length && (
              <>
                <div className="mt-4 mb-1 text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">Big without buying them</div>
                {d.hidden.map((x) => <Kv key={x.name} label={x.name}>{val(x.total)}</Kv>)}
              </>
            )}
          </Card>
          {!!d.overlap?.length && (
            <Card className="p-4">
              <H2>Overlap between your ETFs</H2>
              {d.overlap.map((o) => (
                <div key={o.a + o.b} className="py-1.5 text-[13px]">
                  <div className="flex justify-between"><span>{short(etfs.find((e) => e.id === o.a))} & {short(etfs.find((e) => e.id === o.b))}</span><span className="num font-semibold">{p1(o.overlap)}</span></div>
                  <div className="mt-1 h-1.5 rounded-full bg-secondary"><div className="h-1.5 rounded-full bg-primary" style={{ width: `${o.overlap * 100}%` }} /></div>
                </div>
              ))}
              <p className="mt-2 text-xs text-muted-foreground">The part of the two ETFs that holds the same companies in the same amounts. High overlap: the second ETF adds little spread.</p>
            </Card>
          )}
        </div>
      </div>
      {pick && (
        <Card className="p-4">
          <H2 side={etfs.length > 1 ? <Segmented label="Which ETF" value={pick.id} onChange={(v) => setPref('state', 'xrEtf', v)} options={etfs.map((e) => ({ value: e.id, label: short(e) }))} /> : undefined}>Inside {etfs.length > 1 ? 'your ETFs' : pick.name}</H2>
          <div className="grid gap-x-8 gap-y-1.5 md:grid-cols-2">
            {pick.top.map((t, i) => (
              <div key={t.name} className={cn('grid grid-cols-[1.5rem_minmax(0,1fr)_4rem] items-center gap-x-2 text-[13px]', t.t212_ticker && 'cursor-pointer')} onClick={() => t.t212_ticker && nav(href(t.t212_ticker))}>
                <span className="text-muted-foreground">{i + 1}</span>
                <span className="truncate">{t.name}{t.own && <span className="ml-1.5 rounded bg-up-bg px-1.5 py-0.5 text-[10.5px] font-medium text-up">you own it</span>}</span>
                <span className="num text-right">{p1(t.weight)}</span>
                <span className="col-start-2 col-end-4 h-1 rounded-full bg-secondary"><span className="block h-1 rounded-full" style={{ width: `${(t.weight / pick.top[0].weight) * 100}%`, background: col[pick.id] }} /></span>
              </div>
            ))}
          </div>
          <p className="mt-3 text-xs text-muted-foreground">
            {pick.name}: {pick.n.toLocaleString('en-US')} companies{pick.same ? ', from its own SEC holdings report' : `, from the SEC holdings report of ${pick.twin} (a US ETF on the same index; close for the big names, small positions can differ)`}
            {pick.as_of ? ` of ${pick.as_of}` : ''}. {pick.cash > 0.0005 ? `Cash and other assets: ${p1(pick.cash)}.` : ''}
          </p>
        </Card>
      )}
      <Note>
        Companies are matched by ISIN, else by name; share classes of one company (like Alphabet A and C) count as one. Percentages are of your invested money (cash left out).
        {!!d.notes?.length && <span className="block text-down">{d.notes.join(' · ')}</span>}
      </Note>
    </div>
  )
}

/* ------------------------------------------------------------------ calendar (stockterminal/events.py) */
type Rating = { step: number; reasons: string[]; surprise?: number }
type CalItem = {
  t212_ticker: string; name: string; type: 'earnings' | 'ex_dividend' | 'dividend_payment' | 'dividend_received'; date: string; date_to?: string | null
  confirmed?: boolean; expected?: boolean; past?: boolean; older?: boolean; quarter_end?: boolean; eps_avg?: N; eps_low?: N; eps_high?: N; revenue_avg?: N; eps_est?: N; eps_act?: N; reaction?: N
  per_share?: N; estimate?: N; estimate_currency?: string; per_year?: N; amount?: N; amount_currency?: string; rating: Rating | null; rating_kind: string | null
}
type Cal = { today: string; items: CalItem[]; missing?: string[]; no_results?: string[] }
const KIND: Record<CalItem['type'], [string, string]> = {
  earnings: ['Results', 'bg-primary/10 text-primary'],
  ex_dividend: ['Ex-dividend', 'bg-up-bg text-up'],
  dividend_payment: ['Dividend payout', 'bg-up-bg text-up'],
  dividend_received: ['Dividend received', 'bg-secondary text-foreground'],
}
const RATE: Record<string, [string, string, string]> = {
  '2': ['▲▲', 'Very positive', 'text-up'], '1': ['▲', 'Positive', 'text-up'], '0': ['●', 'Neutral', 'text-muted-foreground'],
  '-1': ['▼', 'Negative', 'text-down'], '-2': ['▼▼', 'Very negative', 'text-down'],
}
const calDay = (d: string, short?: boolean) => new Date(d + 'T12:00:00').toLocaleDateString('en-US', short ? { month: 'short', day: 'numeric' } : { weekday: 'short', month: 'short', day: 'numeric' })
const num2 = (v: N) => (has(v) ? v.toFixed(2) : '–')

export function CalendarTab() {
  const nav = useNavigate()
  const { pref, setPref } = useSettings()
  const { data: d, loading } = useStoredThenFresh<Cal>('cal', '/api/portfolio/calendar')
  if (loading || !d) return <Loading>Looking up results and dividend dates…</Loading>
  const show = (['earnings', 'dividends'].includes(pref<string>('state', 'calShow', 'all')) ? pref<string>('state', 'calShow', 'all') : 'all') as 'all' | 'earnings' | 'dividends'
  // The next 3 months and the last 6 (stockterminal/events.py: AHEAD_DAYS, PAST_DAYS); older results come marked older.
  const items = d.items.filter((i) => !i.older && (show === 'all' || (show === 'earnings') === (i.type === 'earnings')))
  // Upcoming first (soonest at the top), then what already happened (most recent first).
  const ahead = items.filter((i) => i.date >= d.today)
  const before = items.filter((i) => i.date < d.today).reverse()
  const rows: ReactNode[] = []
  const section = (key: string, text: ReactNode) => <div key={key} className="flex items-center gap-3 py-2 text-xs font-semibold text-primary"><span className="h-px flex-1 bg-primary/40" />{text}<span className="h-px flex-1 bg-primary/40" /></div>
  const addRows = (list: CalItem[], part: string) => {
    let month = ''
    for (const i of list) {
      const m = i.date.slice(0, 7)
      if (m !== month) {
        month = m
        rows.push(<div key={part + m} className="pt-3 pb-1 text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">{new Date(m + '-15').toLocaleDateString('en-US', { month: 'long', year: 'numeric' })}</div>)
      }
      const [label, cls] = KIND[i.type]
      rows.push(
        <div key={rows.length} onClick={() => nav(href(i.t212_ticker))} className={cn('-mx-2 grid cursor-pointer grid-cols-[6.5rem_8.5rem_minmax(0,12rem)_7.5rem_minmax(0,1fr)] items-start gap-3 rounded-lg px-2 py-2 text-[13px] hover:bg-panel', i.date < d.today && 'opacity-70')}>
          <span className="num text-muted-foreground">{i.expected && '≈ '}{calDay(i.date)}</span>
          <span><span className={cn('rounded-md px-1.5 py-0.5 text-xs font-medium', cls, i.expected && 'border border-dashed border-current bg-transparent')}>{label}</span></span>
          <span className="truncate font-medium">{i.name || i.t212_ticker}</span>
          <span><RateBadge i={i} /></span>
          <span className="text-[12.5px] leading-snug">{calDetail(i, d.today)}</span>
        </div>,
      )
    }
  }
  rows.push(section('today', <>Today · {calDay(d.today)} · coming up</>))
  if (ahead.length) addRows(ahead, 'a')
  else rows.push(<p key="none" className="py-3 text-[13px] text-muted-foreground">Nothing coming up in the next 3 months.</p>)
  if (before.length) {
    rows.push(section('past', 'Already happened'))
    addRows(before, 'b')
  }
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-[13px] text-muted-foreground">{show === 'earnings' ? 'Results of your holdings' : show === 'dividends' ? "Your holdings' dividends" : "Your holdings' results and dividends"}: the next 3 months at the top, scroll down for the last 6 months.</span>
        <div className="grow" />
        <Segmented label="What to show" value={show} onChange={(v) => setPref('state', 'calShow', v)} options={[{ value: 'all', label: 'All' }, { value: 'earnings', label: 'Results' }, { value: 'dividends', label: 'Dividends' }]} />
      </div>
      <Card className="overflow-x-auto px-4 pb-2">
        {items.length ? <div className="min-w-[760px]">{rows}</div> : <Empty><CalendarDays className="size-5" />No dates found for your holdings yet.</Empty>}
      </Card>
      <Note>
        Dates and analyst expectations from Yahoo. A results date shown as a range is not confirmed by the company yet. A date with ≈ and a dashed label is not announced yet:
        it is expected from the company's own rhythm (results about 52 weeks after the same quarter last year; ex-dividend a year after last year's, or the usual gap for monthly payers), and moves to the real date once it is announced. The rating (hover it for the reasons):
        for results still to come it weighs expected EPS growth, analysts raising or lowering their estimate, and how often the company beat lately; for results already out, EPS against the estimate; for dividends, against the previous one.
        Dividend estimates = your shares × the last dividend per share, before dividend tax.
        {!!d.missing?.length && <span className="block">No calendar found for: {d.missing.join(', ')}.</span>}
        {show === 'earnings' && !!d.no_results?.length && <span className="block">No past results from Yahoo for: {d.no_results.join(', ')}.</span>}
      </Note>
    </div>
  )
}

function RateBadge({ i }: { i: CalItem }) {
  if (!i.rating) return null
  const [sym, label, cls] = RATE[String(i.rating.step)] ?? RATE['0']
  return (
    <Hint label={<span className="flex flex-col">{i.rating.reasons.map((r) => <span key={r}>{r}</span>)}</span>}>
      <span className="flex cursor-help flex-col leading-tight">
        <span className={cn('text-xs font-semibold', cls)}>{sym} {label}</span>
        <span className="text-[11px] text-muted-foreground">{i.rating_kind === 'expected' ? 'expectation' : 'result'}</span>
      </span>
    </Hint>
  )
}

function calDetail(i: CalItem, today: string): ReactNode {
  const mut = (t: ReactNode) => <span className="text-muted-foreground">{t}</span>
  if (i.type === 'earnings' && i.past) {
    return (
      <>
        {i.quarter_end && <div>{mut('quarter ended (report date unknown)')}</div>}
        <div>EPS <b>{num2(i.eps_act)}</b>{has(i.eps_est) && <> vs {num2(i.eps_est)} expected{has(i.rating?.surprise) && <span className={tone(i.rating!.surprise)}> ({pctTxt(i.rating!.surprise)})</span>}</>}</div>
        {has(i.reaction) && <div>{mut('share price')} <span className={tone(i.reaction)}>{pctTxt(i.reaction)}</span> {mut('from the close before to the close after')}</div>}
      </>
    )
  }
  if (i.type === 'earnings') {
    return (
      <>
        {i.expected && <div>{mut("not announced yet · expected around this date, from the company's usual rhythm")}</div>}
        {!i.confirmed && !i.expected && <div>{mut(`expected${i.date_to ? `, between ${calDay(i.date, true)} and ${calDay(i.date_to, true)}` : ''} · not confirmed yet`)}</div>}
        <div>
          {has(i.eps_avg) && <>EPS expected {num2(i.eps_avg)}{has(i.eps_low) && has(i.eps_high) && mut(` (${num2(i.eps_low)} – ${num2(i.eps_high)})`)}</>}
          {has(i.eps_avg) && has(i.revenue_avg) && ' · '}
          {has(i.revenue_avg) && <>revenue expected {big(i.revenue_avg)}</>}
        </div>
      </>
    )
  }
  if (i.type === 'dividend_received') return <><b>{cash(i.amount, i.amount_currency)}</b> {mut('paid into your account')}</>
  return (
    <>
      {has(i.estimate) && <div>≈ <b>{cash(i.estimate, i.estimate_currency)}</b> for your shares {mut('(gross, before tax)')}</div>}
      {has(i.per_share) && <div>{mut(`last dividend ${cash(i.per_share, i.estimate_currency)} per share${i.per_year ? `, ${i.per_year}× a year` : ''}`)}</div>}
      {i.expected && <div>{mut('not announced yet · expected from the usual dividend rhythm')}</div>}
      {i.type === 'ex_dividend' && i.date >= today && !i.expected && <div>{mut('own it before this day to get the next dividend')}</div>}
    </>
  )
}

/* ------------------------------------------------------------------ analysis (stockterminal/analysis.py) */
type AnVal = { value: N; coverage: number; world: N }
type Analysis = {
  empty: boolean; account_currency: string; bench_label: string; company: Record<string, AnVal>; notes?: string[]
  risk: {
    since?: string; actual: { vol: N; max_drop: N; beta: N; corr: N }; today: { vol: N; beta: N; corr: N; max_drop: N }; world: { vol: N }
    holdings: { name: string; t212_ticker: string; weight: number; vol: number; beta: N; risk_share: number }[]
    corr: { names: string[]; matrix: (number | null)[][] }; high_pairs: { a: string; b: string; corr: number }[]
    concentration: { top1: number; top5: number; effective_n: number; n: number }
  } | null
}
const AN_ROWS: [string, string, 'x' | 'pct', string][] = [
  ['pe', 'P/E', 'x', 'Price / earnings of everything you own together: the earnings your money owns vs what it costs (weighted harmonic mean). Loss-makers lower the earnings.'],
  ['forward_pe', 'Forward P/E', 'x', "The same with next year's expected earnings (analysts)."],
  ['ps', 'Price / sales', 'x', 'Price / sales of everything you own together.'],
  ['dividend_yield', 'Dividend yield', 'pct', 'Dividends of the last 12 months / price, weighted by what you hold.'],
  ['revenue_growth', 'Revenue growth', 'pct', 'Sales growth vs a year ago (Yahoo: latest quarter), weighted by the sales your money owns. Shares only.'],
  ['earnings_growth', 'Earnings growth', 'pct', 'Earnings growth vs a year ago (latest quarter), weighted the same way. Shares only.'],
  ['net_margin', 'Net margin', 'pct', 'Profit / sales of all your companies added up. Shares only.'],
  ['operating_margin', 'Operating margin', 'pct', 'Operating profit / sales of all your companies added up. Shares only.'],
]
const anF = (v: N, kind: 'x' | 'pct') => (!has(v) ? '–' : kind === 'x' ? fmt(v, 'x') : (v * 100).toFixed(1) + '%')

export function AnalysisTab() {
  const nav = useNavigate()
  const c = useChartColors()
  const [corrOpen, setCorrOpen] = useState(false)
  const { data: d, loading } = useStoredThenFresh<Analysis>('analysis', '/api/portfolio/analysis')
  if (loading || !d) return <Loading>Looking up your holdings…</Loading>
  if (d.empty) return <Loading>No open positions to analyse.</Loading>
  const R = d.risk
  const A = R?.actual
  const pick = (a: N, b: N) => (has(a) ? a : b)
  return (
    <div className="flex flex-col gap-4">
      <div className="grid items-start gap-4 lg:grid-cols-2">
        <Card className="min-w-0 p-4">
          <H2>Your portfolio as one company</H2>
          <table className="tbl tight">
            <thead><tr><th /><th>Yours</th><th>{d.bench_label}</th><th>Covers</th></tr></thead>
            <tbody>
              {AN_ROWS.map(([k, label, kind, tip]) => {
                const v = d.company[k] ?? { value: null, world: null, coverage: 0 }
                return (
                  <tr key={k}>
                    <td><Hint label={tip}><span className="cursor-help underline decoration-dotted decoration-border underline-offset-4">{label}</span></Hint></td>
                    <td className="font-semibold">{anF(v.value, kind)}</td>
                    <td className="text-muted-foreground">{anF(v.world, kind)}</td>
                    <td className="text-muted-foreground">{v.coverage ? Math.round(v.coverage * 100) + '%' : '–'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          <p className="mt-3 text-xs text-muted-foreground">From Yahoo for every holding. ETFs count with their own P/E and yield; growth and margins are about your shares only ("Covers" = the part of your portfolio a figure is based on).</p>
        </Card>
        <Card className="p-4">
          <H2>Risk</H2>
          {!R ? (
            <p className="text-[13px] text-muted-foreground">Not enough price history yet for the risk numbers (needs about 3 months).</p>
          ) : (
            <>
              <Kv label="Volatility (a year)" tip="How much your return swings in a year (standard deviation of daily returns × √252). Higher = a bumpier ride.">
                {fmt(pick(A?.vol, R.today.vol), 'pct')} <span className="font-normal text-muted-foreground">· world {fmt(R.world.vol, 'pct')}</span>
              </Kv>
              <Kv label="Beta vs the world" tip="If the FTSE All-World moves 10%, your portfolio moved this many times 10% on average over the last year.">{num2(pick(A?.beta, R.today.beta))}</Kv>
              <Kv label="Correlation with the world" tip="1 = moves exactly with the world, 0 = no relation.">{num2(pick(A?.corr, R.today.corr))}</Kv>
              <Kv label="Biggest drop (a year)" tip="The largest fall from a high to a later low."><span className="text-down">{pctTxt(pick(A?.max_drop, R.today.max_drop))}</span></Kv>
              <Kv label="Largest holding">{fmt(R.concentration.top1, 'pct')}</Kv>
              <Kv label="Top 5">{fmt(R.concentration.top5, 'pct')}</Kv>
              <Kv label="Acts like" tip="Effective number of holdings: 1 / sum of squared weights. 10 holdings of which one is 60% act like fewer than 3. An ETF counts as one holding here.">
                {R.concentration.effective_n.toFixed(1)} equal holdings <span className="font-normal text-muted-foreground">(of {R.concentration.n})</span>
              </Kv>
              <p className="mt-3 text-xs text-muted-foreground">Last year of daily prices in {d.account_currency} (since {R.since}). Volatility, beta and biggest drop are of your actual return.</p>
            </>
          )}
        </Card>
      </div>
      {R && (
        <Card className="min-w-0 p-4">
          <H2 side={<span className="text-xs text-muted-foreground">A year of daily prices, today's weights</span>}>Where your risk comes from</H2>
          <div className="overflow-x-auto">
            <table className="tbl tight">
              <thead><tr><th>Holding</th><th>Weight</th><th>Share of the risk</th><th className="w-56" /><th>Volatility</th><th>Beta</th></tr></thead>
              <tbody>
                {R.holdings.map((h) => {
                  const hot = h.risk_share > h.weight * 1.4 && h.risk_share > 0.05
                  return (
                    <tr key={h.t212_ticker} className="cursor-pointer" onClick={() => nav(href(h.t212_ticker))}>
                      <td className="max-w-64 truncate font-medium">{h.name}</td>
                      <td>{fmt(h.weight, 'pct')}</td>
                      <td className={cn('font-semibold', hot && 'text-down')}>{fmt(h.risk_share, 'pct')}</td>
                      <td>
                        <div className="flex flex-col gap-0.5">
                          <span className="block h-1.5 rounded-full" style={{ width: `${Math.max(0, h.risk_share) * 100}%`, background: c.series[0] }} />
                          <span className="block h-1.5 rounded-full bg-muted-foreground/35" style={{ width: `${h.weight * 100}%` }} />
                        </div>
                      </td>
                      <td>{(h.vol * 100).toFixed(0)}%</td>
                      <td>{num2(h.beta)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-xs text-muted-foreground">Share of the risk: how much of the portfolio's swings each holding causes (its weight × how it moves with the rest). Red: clearly more risk than its weight. Blue bar: share of the risk, grey: weight.</p>
          <button type="button" onClick={() => setCorrOpen(!corrOpen)} className="mt-3 inline-flex items-center gap-1 text-[13px] font-medium text-primary hover:underline">
            <ChevronDown className={cn('size-4 transition-transform', corrOpen && 'rotate-180')} /> Which holdings move together
          </button>
          {corrOpen && <CorrMatrix R={R} />}
        </Card>
      )}
      {!!d.notes?.length && <Note>{d.notes.join(' · ')}</Note>}
    </div>
  )
}

function CorrMatrix({ R }: { R: NonNullable<Analysis['risk']> }) {
  const N = R.corr.names
  const short = (n: string) => (n.length > 14 ? n.slice(0, 13) + '…' : n)
  return (
    <div className="mt-3">
      <div className="overflow-x-auto">
        <table className="tbl tight">
          <thead><tr><th />{N.map((n) => <th key={n} title={n} className="text-center!">{short(n)}</th>)}</tr></thead>
          <tbody>
            {R.corr.matrix.map((row, i) => (
              <tr key={N[i]}>
                <td title={N[i]} className="font-medium">{short(N[i])}</td>
                {row.map((v, j) =>
                  v === null || i === j ? <td key={j} /> : (
                    <td key={j} title={`${N[i]} / ${N[j]}`} className="text-center! text-foreground" style={{ background: `color-mix(in srgb, var(${v >= 0 ? '--down' : '--up'}) ${Math.round(Math.min(Math.abs(v), 1) * 55)}%, transparent)` }}>{v.toFixed(2)}</td>
                  ),
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        Correlation of daily returns over the last year: 1 = always move together, 0 = unrelated. Strong red pairs add the least spreading.{' '}
        {R.high_pairs.length ? `Above 0.7: ${R.high_pairs.slice(0, 6).map((p) => `${p.a} & ${p.b} (${p.corr.toFixed(2)})`).join(', ')}.` : 'No pairs above 0.7.'}
      </p>
    </div>
  )
}

/* ------------------------------------------------------------------ costs (stockterminal/costs.py) */
type Costs = {
  account_currency: string; other_currency: string | null; fx: Fx; total: number; trades: number; traded: number; pct_of_traded: N
  by_kind: { name: string; amount: number }[]; by_year: { year: string; amount: number }[]; by_holding: { name: string; amount: number }[]
  other_currency_fees: { amount: number; currency: string }[]; cash_fees: { amount: number; currency: string }[]
  funds: { name: string; t212_ticker: string; value: number; ter: N; yearly: number }[]; fund_cost: number; fund_cost_pct: N; funds_missing: string[]
}

export function CostsTab() {
  const nav = useNavigate()
  const { data: d, loading } = useStoredThenFresh<Costs>('costs', '/api/portfolio/costs')
  const { m, cur } = usePfMoney()
  if (loading || !d) return <Loading>Reading your costs…</Loading>
  const acc = d.account_currency
  const otherFees = d.cash_fees.filter((f) => f.currency === acc).reduce((a, f) => a + f.amount, 0)
  const conv = d.by_kind.find((k) => k.name === 'Currency conversion')
  const list = (rows: { label: string; amount: number }[], more = 0) =>
    rows.length ? (
      <>
        {rows.map((x) => <Kv key={x.label} label={x.label}>{m(x.amount)}</Kv>)}
        {more > 0 && <p className="pt-1 text-xs text-muted-foreground">and {more} more</p>}
      </>
    ) : (
      <p className="text-[13px] text-muted-foreground">No trade costs.</p>
    )
  return (
    <div className="flex flex-col gap-4">
      <Card className="grid grid-cols-2 gap-x-6 gap-y-4 p-4 md:grid-cols-4">
        <Stat label="Trade costs" tip="Everything Trading 212 booked on your trades: currency conversion, transaction taxes and fees. Trading 212 itself charges no commission." value={m(d.total)} sub={has(d.pct_of_traded) ? `${(d.pct_of_traded * 100).toFixed(2)}% of the ${m(d.traded, 0)} you traded` : 'no trades yet'} />
        <Stat label="Currency conversion" tip="Trading 212 charges a fee each time it converts your money into another currency, e.g. euros into dollars for a US share." value={m(conv ? conv.amount : 0)} sub="buying or selling in another currency" />
        <Stat label="ETF costs per year" tip="The yearly expense ratio (TER) of each ETF times what you hold. Not charged separately: the fund takes it from its value, so it is already in your return." value={m(d.fund_cost)} sub={has(d.fund_cost_pct) ? `${(d.fund_cost_pct * 100).toFixed(3)}% of your holdings` : ''} />
        <Stat label="Other fees" tip="Fees Trading 212 booked apart from trades, e.g. on deposits." value={m(otherFees)} sub="not tied to a trade" />
      </Card>
      <div className="grid items-start gap-4 lg:grid-cols-3">
        <Card className="p-4"><H2>By kind</H2>{list(d.by_kind.map((x) => ({ label: x.name, amount: x.amount })))}</Card>
        <Card className="p-4"><H2>By year</H2>{list(d.by_year.map((x) => ({ label: x.year, amount: x.amount })))}</Card>
        <Card className="p-4"><H2>By holding</H2>{list(d.by_holding.slice(0, 10).map((x) => ({ label: x.name, amount: x.amount })), d.by_holding.length - 10)}</Card>
      </div>
      <Card className="min-w-0 p-4">
        <H2>Your ETFs</H2>
        {d.funds.length ? (
          <table className="tbl tight">
            <thead><tr><th>Fund</th><th>Value</th><th>Yearly cost (TER)</th><th>Per year for you</th></tr></thead>
            <tbody>
              {d.funds.map((f) => (
                <tr key={f.t212_ticker} className="cursor-pointer" onClick={() => nav(href(f.t212_ticker))}>
                  <td className="font-medium">{f.name}</td><td>{m(f.value)}</td><td>{has(f.ter) ? (f.ter * 100).toFixed(2) + '%' : '–'}</td><td>{m(f.yearly)}</td>
                </tr>
              ))}
              <tr className="font-semibold"><td>Total</td><td>{m(d.funds.reduce((a, f) => a + f.value, 0))}</td><td /><td>{m(d.fund_cost)}</td></tr>
            </tbody>
          </table>
        ) : (
          <p className="text-[13px] text-muted-foreground">You hold no ETFs.</p>
        )}
      </Card>
      <Note>
        Trade costs as Trading 212 booked them on each trade, in {acc}{cur !== acc ? `, converted to ${cur} at today's rate` : ''}.{' '}
        {d.other_currency_fees.length ? `Also charged in another currency: ${d.other_currency_fees.map((f) => cash(f.amount, f.currency)).join(', ')}. ` : ''}
        ETF costs: the expense ratio from Yahoo{d.funds_missing.length ? ` (not found for ${d.funds_missing.join(', ')})` : ''}. Dividend tax withheld abroad is not a cost here; it belongs with taxes.
      </Note>
    </div>
  )
}
