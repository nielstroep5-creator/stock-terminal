import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, Bookmark, ChartLine, Check, ChevronDown, Columns3, Download, Plus, Search, Star, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Card, Page } from '@/components/app/Page'
import { useWatchlists } from '@/components/app/WatchStar'
import type { WatchList } from '@/components/app/WatchStar'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Popover, PopoverAnchor, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Segmented } from '@/components/ui/segmented'
import { Hint } from '@/components/ui/tooltip'
import { api } from '@/lib/api'
import { big, day, fmt, niceName, pctSigned, tone } from '@/lib/format'
import { useSettings } from '@/lib/settings'
import { useToast } from '@/lib/toast'
import { cn, rowKeys } from '@/lib/utils'
import { Logo } from '@/components/app/Logo'

// Every loaded company with about 75 numbers (stockterminal/screener.py). Filtering, sorting and ranking happen
// here: 500 rows are instant. The screen, columns, sort and your saved screens live in the same settings as the
// old design (prefs "state": scrF, scrCols, scrSort, scrName, scrSaved), so screens saved before the redesign still show.
type Field = { key: string; label: string; group: string; fmt: string; tip: string }
type Row = { ticker: string; name: string; sector?: string | null; industry?: string | null; live?: boolean } & Record<string, number | string | boolean | null | undefined>
type Op = '>' | '<' | 'between'
type Cond = { k: string; op: Op; a: number | null; b: number | null }
type Screen = { f: Cond[]; sec: string[]; q: string }
type Saved = { name: string; s: Screen }

const PRESETS: [string, string, [string, Op, number, number?][]][] = [
  ['Quality compounders', 'High returns on capital, steady growth, little debt', [['roic', '>', 0.15], ['revenue_cagr_5y', '>', 0.08], ['net_margin', '>', 0.15], ['debt_to_equity', '<', 1]]],
  ['Cheap and profitable', 'Low P/E, high free cash flow yield, solid ROE', [['pe', '<', 15], ['fcf_yield', '>', 0.06], ['roe', '>', 0.12]]],
  ['Dividend growers', 'A decent yield that has grown for 10+ years and is covered by earnings', [['div_yield', '>', 0.02], ['div_years_growth', '>', 10], ['payout_ratio', '<', 0.75], ['div_cagr_5y', '>', 0.05]]],
  ['Fast growers', 'Revenue growing over 20% with analysts expecting more', [['revenue_yoy', '>', 0.2], ['rev_growth_next', '>', 0.15], ['gross_margin', '>', 0.4]]],
  ['Analyst favourites', 'Big upside to the price target, a buy rating from many analysts', [['target_upside', '>', 0.2], ['rating', '<', 2], ['analysts', '>', 10]]],
  ['Beaten-down quality', 'Well below the 52-week high, but still very cash generative', [['from_high', '<', -0.25], ['fcf_margin', '>', 0.15], ['roic', '>', 0.1]]],
  ['Fortress balance sheet', 'More cash than debt and strong free cash flow', [['net_cash', '>', 0], ['current_ratio', '>', 1.5], ['fcf_margin', '>', 0.15]]],
  ['Buyback machines', 'Shrinking share count, paid for by free cash flow', [['shares_diluted_yoy', '<', -0.02], ['fcf_yield', '>', 0.04]]],
  ['Reporting soon', 'Results in the next two weeks', [['earnings_in', 'between', 0, 14]]],
]
const presetScreen = (p: (typeof PRESETS)[number]): Screen => ({ f: p[2].map((c) => ({ k: c[0], op: c[1], a: c[2], b: c[3] ?? null })), sec: [], q: '' })
const DEFAULT_COLS = 'market_cap,price,change,pe,revenue_yoy,operating_margin,roic,div_yield,r_1y'
const EMPTY: Screen = { f: [], sec: [], q: '' }
const SIGNED = new Set(['change', 'r_1m', 'r_3m', 'r_6m', 'r_ytd', 'r_1y', 'r_3y', 'r_5y', 'from_high', 'from_low', 'target_upside'])
const UNIT: Record<string, string> = { pct: '%', usd: 'B', x: 'x', usdps: '$', days: 'days', int: '', num: '' }

const has = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const parse = <T,>(s: string, fallback: T): T => { try { return JSON.parse(s) as T } catch { return fallback } }
// what you type <-> the stored number: percentages as %, dollar amounts in billions
const toInput = (f: string, v: number) => (f === 'pct' ? v * 100 : f === 'usd' ? v / 1e9 : v)
const fromInput = (f: string, v: number) => (f === 'pct' ? v / 100 : f === 'usd' ? v * 1e9 : v)
const short = (v: number) => String(+v.toFixed(4))
const ratingText = (m: number) => (m < 1.5 ? 'Strong buy' : m < 2.5 ? 'Buy' : m < 3.5 ? 'Hold' : m < 4.5 ? 'Sell' : 'Strong sell')
const active = (c: Cond) => has(c.a) || (c.op === 'between' && has(c.b)) // a filter without a value does nothing yet
const pass = (c: Cond, v: unknown) => {
  if (!active(c)) return true
  if (!has(v)) return false
  if (c.op === '>') return v >= (c.a as number)
  if (c.op === '<') return v <= (c.a as number)
  return (!has(c.a) || v >= c.a) && (!has(c.b) || v <= c.b)
}
function filterRows(rows: Row[], s: Screen, skip = -1) {
  const words = (s.q || '').toLowerCase().split(/\s+/).filter(Boolean)
  const sec = new Set(s.sec)
  return rows.filter((r) => (!sec.size || sec.has(r.sector || 'Other')) && words.every((w) => `${r.ticker} ${r.name} ${r.industry || ''}`.toLowerCase().includes(w)) && s.f.every((c, i) => i === skip || pass(c, r[c.k])))
}

function value(fl: Field, v: unknown): ReactNode {
  if (fl.fmt === 'text') return v ? String(v) : <span className="text-muted-foreground">–</span>
  if (!has(v)) return <span className="text-muted-foreground">–</span>
  if ((fl.key === 'div_yield' || fl.key === 'div_yield_ttm') && v === 0) return <span className="text-muted-foreground">No dividend</span>
  switch (fl.fmt) {
    case 'pct': return SIGNED.has(fl.key) || fl.key.endsWith('_yoy') ? <span className={tone(v)}>{pctSigned(v)}</span> : (fl.key.endsWith('yield') || fl.key.endsWith('yield_ttm') ? fmt(v, 'pct2') : fmt(v, 'pct'))
    case 'usd': return big(v)
    case 'int': return Math.round(v).toLocaleString('en-US')
    case 'days': return v === 0 ? 'today' : v === 1 ? 'tomorrow' : `${v} days`
    case 'num': return fl.key === 'rating' ? <span title={ratingText(v)} className={v < 2.5 ? 'text-up' : v >= 3.5 ? 'text-down' : ''}>{v.toFixed(2)}</span> : fmt(v, 'num')
    default: return fmt(v, fl.fmt)
  }
}
function condText(c: Cond, fl: Field) {
  const u = UNIT[fl.fmt]
  const show = (v: number) => (fl.fmt === 'usdps' ? '$' + short(toInput(fl.fmt, v)) : short(toInput(fl.fmt, v)) + (u && u !== '$' ? (u === 'days' ? ' days' : u) : ''))
  if (c.op === 'between' && has(c.a) && has(c.b)) return `${fl.label} ${show(c.a)} to ${show(c.b)}`
  if (c.op === 'between' && has(c.b)) return `${fl.label} ≤ ${show(c.b)}`
  return `${fl.label} ${c.op === '<' ? '≤' : '≥'} ${show(c.a as number)}`
}

const groupLabel = 'px-2 pt-2 pb-1 text-[11px] font-semibold tracking-wider text-muted-foreground uppercase'
const itemCls = 'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] outline-none hover:bg-secondary focus-visible:bg-secondary'

/** All numbers by group, with a search box. With `on` it is a list of checkboxes (columns), without it you pick one (a filter). */
function FieldList({ fields, on, onPick, footer }: { fields: Field[]; on?: Set<string>; onPick: (key: string) => void; footer?: ReactNode }) {
  const [q, setQ] = useState('')
  const [act, setAct] = useState(0) // the highlighted row: Enter picks it, the arrow keys move it
  const words = q.toLowerCase().split(/\s+/).filter(Boolean)
  const shown = fields.filter((f) => words.every((w) => `${f.label} ${f.group} ${f.key}`.toLowerCase().includes(w)))
  const at = Math.min(act, Math.max(0, shown.length - 1))
  const listRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    listRef.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: 'nearest' })
  }, [at])
  const keys = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      setAct(Math.max(0, Math.min(shown.length - 1, at + (e.key === 'ArrowDown' ? 1 : -1))))
    } else if (e.key === 'Enter' && shown[at]) {
      e.preventDefault()
      onPick(shown[at].key)
    }
  }
  let group = ''
  return (
    <>
      <div className="relative p-1">
        <Search className="pointer-events-none absolute top-1/2 left-3.5 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input autoFocus value={q} onChange={(e) => { setQ(e.target.value); setAct(0) }} onKeyDown={keys} placeholder="Search a number" spellCheck={false} className="h-8 pl-8" />
      </div>
      <div ref={listRef} className="max-h-[380px] overflow-y-auto px-1 pb-1">
        {shown.map((f, i) => {
          const head = f.group !== group ? ((group = f.group), <div className={groupLabel}>{f.group}</div>) : null
          const checked = on?.has(f.key)
          return (
            <div key={f.key}>
              {head}
              <button type="button" title={f.tip} role={on ? 'menuitemcheckbox' : 'menuitem'} aria-checked={on ? checked : undefined} data-active={i === at} onMouseEnter={() => setAct(i)} onClick={() => onPick(f.key)} className={cn(itemCls, i === at && 'bg-secondary')}>
                {on && <span className={cn('flex size-4 shrink-0 items-center justify-center rounded border', checked && 'border-primary bg-primary text-primary-foreground')}>{checked && <Check className="size-3" />}</span>}
                {f.label}
              </button>
            </div>
          )
        })}
        {!shown.length && <div className="px-2 py-4 text-center text-[13px] text-muted-foreground">No number with that name</div>}
      </div>
      {footer}
    </>
  )
}

/** The box that sets one filter: at least / at most / between, the value, and how the values are spread out. */
function FilterEditor({ c, fl, rows, screen, index, onChange, onDone }: { c: Cond; fl: Field; rows: Row[]; screen: Screen; index: number; onChange: (c: Cond) => void; onDone: () => void }) {
  const [a, setA] = useState(has(c.a) ? short(toInput(fl.fmt, c.a)) : '')
  const [b, setB] = useState(has(c.b) ? short(toInput(fl.fmt, c.b)) : '')
  const vals = useMemo(() => filterRows(rows, screen, index).map((r) => r[c.k]).filter(has).sort((x, y) => x - y), [rows, screen, index, c.k])
  const pick = (p: number) => vals[Math.min(vals.length - 1, Math.floor(p * vals.length))]
  const read = (t: string) => { const s = t.trim().replace(/[%$,x\s]/gi, '').replace(/b$/i, ''); return s === '' || Number.isNaN(+s) ? null : fromInput(fl.fmt, +s) }
  const apply = (op: Op, ta: string, tb: string) => onChange({ k: c.k, op, a: read(ta), b: op === 'between' ? read(tb) : null })
  const n = filterRows(rows, screen).length
  const unit = UNIT[fl.fmt]
  const lo = vals.length ? pick(0.03) : 0
  const hi = vals.length ? pick(0.97) : 0
  const bins = useMemo(() => {
    if (vals.length < 5 || !(hi > lo)) return null
    const out = Array<number>(28).fill(0)
    for (const v of vals) out[Math.max(0, Math.min(27, Math.floor(((v - lo) / (hi - lo)) * 28)))]++
    return out
  }, [vals, lo, hi])
  const top = bins ? Math.max(...bins) : 1
  const field = 'h-8 w-24 text-right'
  return (
    <div className="p-1.5">
      <div className="text-[13px] font-semibold">{fl.label}</div>
      <p className="mt-0.5 text-xs text-muted-foreground">{fl.tip}</p>
      <div className="mt-2.5">
        <Segmented label="Kind of filter" value={c.op} onChange={(v) => apply(v, a, b)} options={[{ value: '>', label: 'At least' }, { value: '<', label: 'At most' }, { value: 'between', label: 'Between' }]} />
      </div>
      <div className="mt-2.5 flex items-center gap-1.5 text-[13px]">
        <Input autoFocus inputMode="decimal" value={a} placeholder={c.op === '<' ? 'At most' : c.op === 'between' ? 'From' : 'At least'} className={field} onChange={(e) => { setA(e.target.value); apply(c.op, e.target.value, b) }} onKeyDown={(e) => e.key === 'Enter' && onDone()} />
        <span className="text-muted-foreground">{unit}</span>
        {c.op === 'between' && (
          <>
            <span className="px-1 text-muted-foreground">to</span>
            <Input inputMode="decimal" value={b} placeholder="To" className={field} onChange={(e) => { setB(e.target.value); apply(c.op, a, e.target.value) }} onKeyDown={(e) => e.key === 'Enter' && onDone()} />
            <span className="text-muted-foreground">{unit}</span>
          </>
        )}
      </div>
      {bins && (
        <div className="mt-3">
          <svg aria-hidden viewBox="0 0 288 46" className="h-[46px] w-full">
            {bins.map((m, k) => {
              const h = Math.max(1, (m / top) * 44)
              const inside = active(c) && pass(c, lo + ((k + 0.5) / 28) * (hi - lo))
              return <rect key={k} x={(k * 288) / 28 + 0.5} y={46 - h} width={288 / 28 - 1.5} height={h} rx={1} className={inside ? 'fill-primary' : 'fill-border'} />
            })}
          </svg>
          <div className="num flex justify-between text-[11px] text-muted-foreground"><span>{value(fl, lo)}</span><span>{value(fl, hi)}</span></div>
        </div>
      )}
      <p className="mt-2 text-xs text-muted-foreground">
        {vals.length ? <>Median <b className="text-foreground">{value(fl, pick(0.5))}</b>, middle half {value(fl, pick(0.25))} to {value(fl, pick(0.75))}. {vals.length} companies have a value (with your other filters).</> : 'No company has a value for this with your other filters.'}
      </p>
      <div className="mt-3 flex items-center">
        <span className="text-[13px]"><b>{n}</b> {n === 1 ? 'company matches' : 'companies match'}</span>
        <div className="grow" />
        <Button variant="default" size="sm" onClick={onDone}>Done</Button>
      </div>
    </div>
  )
}

function Ago({ at, live, asOf }: { at: number; live: boolean; asOf?: string | null }) {
  const [, tick] = useState(0)
  useEffect(() => { const i = window.setInterval(() => tick((x) => x + 1), 15000); return () => window.clearInterval(i) }, [])
  // no live price came back (offline, market data down): say which close the numbers are from
  if (!live) return asOf ? <span className="text-xs text-muted-foreground">Prices of the {day(asOf, false)} close</span> : null
  if (!at) return null
  const m = Math.floor((Date.now() - at) / 60000)
  return <span className="text-xs text-muted-foreground">{m < 1 ? 'Prices updated just now' : `Prices updated ${m}m ago`}</span>
}

export function ScreenerPage() {
  const nav = useNavigate()
  const qc = useQueryClient()
  const toast = useToast()
  const { pref, setPref } = useSettings()
  const { data: lists } = useWatchlists()
  const stored = useQuery({ queryKey: ['screener', 'stored'], queryFn: () => api.get<{ rows: Row[]; fields: Field[] }>('/api/screener') })
  const live = useQuery({ queryKey: ['screener', 'live'], queryFn: () => api.get<{ rows: Row[]; fields: Field[] }>('/api/screener?live=1'), enabled: !!stored.data?.rows.length, refetchInterval: 120_000, retry: false })
  const data = live.data ?? stored.data
  const rows = useMemo(() => data?.rows ?? [], [data])
  const fields = useMemo(() => data?.fields ?? [], [data])
  const byKey = useMemo(() => Object.fromEntries(fields.map((f) => [f.key, f])), [fields])

  const scrF = pref<string>('state', 'scrF', '')
  const screen = useMemo<Screen>(() => ({ ...EMPTY, ...parse<Partial<Screen>>(scrF || '{}', {}) }), [scrF])
  const name = pref<string>('state', 'scrName', '')
  const saved = useMemo(() => parse<Saved[]>(pref<string>('state', 'scrSaved', '[]'), []), [pref])
  const [sk, sd] = (pref<string>('state', 'scrSort', '') || 'market_cap:desc').split(':')
  const [editing, setEditing] = useState(-1)
  const [adding, setAdding] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveName, setSaveName] = useState('')
  const [toList, setToList] = useState(false)
  const [newList, setNewList] = useState('')
  const [limit, setLimit] = useState(100)

  const write = (s: Screen, as = '') => { setPref('state', 'scrF', JSON.stringify(s)); if (as !== name) setPref('state', 'scrName', as); setLimit(100) }
  const closeEditor = () => {
    setEditing(-1)
    if (screen.f.some((c) => !active(c))) write({ ...screen, f: screen.f.filter(active) }, name) // a filter left without a value: drop it
  }
  const hits = useMemo(() => {
    const s = sd === 'asc' ? 1 : -1
    const key = (r: Row) => (sk === 'name' ? (r.name || '').toLowerCase() : r[sk])
    const ok = (v: unknown) => v !== null && v !== undefined && v !== ''
    return filterRows(rows, screen).sort((x, y) => {
      const p = key(x)
      const q = key(y)
      if (!ok(p) && !ok(q)) return ((y.market_cap as number) || 0) - ((x.market_cap as number) || 0)
      if (!ok(p)) return 1
      if (!ok(q)) return -1
      return (p! < q! ? -1 : p! > q! ? 1 : 0) * s
    })
  }, [rows, screen, sk, sd])
  const ownCols = (pref<string>('state', 'scrCols', '') || DEFAULT_COLS).split(',').filter((k) => byKey[k])
  const cols = [...ownCols, ...screen.f.map((c) => c.k).filter((k, i, all) => byKey[k] && !ownCols.includes(k) && all.indexOf(k) === i)].map((k) => byKey[k]) // what you filter on is always shown
  const sectors = useMemo(() => [...new Set(rows.map((r) => r.sector || 'Other'))].sort(), [rows])
  const sectorCount = useMemo(() => {
    const n: Record<string, number> = {}
    for (const r of filterRows(rows, { ...screen, sec: [] })) n[r.sector || 'Other'] = (n[r.sector || 'Other'] || 0) + 1
    return n
  }, [rows, screen])
  const filtered = screen.f.length > 0 || screen.sec.length > 0 || !!screen.q

  const sortBy = (k: string) => setPref('state', 'scrSort', sk !== k ? `${k}:${k === 'name' ? 'asc' : 'desc'}` : `${k}:${sd === 'asc' ? 'desc' : 'asc'}`)
  const toggleCol = (k: string) => {
    const on = new Set(ownCols)
    if (on.has(k)) on.delete(k)
    else on.add(k)
    setPref('state', 'scrCols', fields.map((f) => f.key).filter((x) => on.has(x)).join(',') || 'market_cap')
  }
  const saveScreen = () => {
    const n = saveName.trim()
    if (!n) return
    const text = JSON.stringify([...saved.filter((p) => p.name !== n), { name: n, s: screen }])
    if (text.length > 2000) return toast('There is no room for another saved screen. Delete one first.')
    setPref('state', 'scrSaved', text)
    setPref('state', 'scrName', n)
    setSaving(false)
  }
  const addTo = async (l: Pick<WatchList, 'id' | 'name'>) => {
    setToList(false)
    try {
      const j = await api.post<{ added: number; found: number; lists: WatchList[] }>(`/api/watchlists/${l.id}/import`, { text: hits.slice(0, 100).map((r) => r.ticker).join('\n') })
      qc.setQueryData(['watchlists'], j.lists)
      void qc.invalidateQueries({ queryKey: ['watchlist-rows'] })
      toast(<>{j.added} added to <Link to={`/watchlist/${l.id}`}>{l.name}</Link>{j.found - j.added ? ` (${j.found - j.added} were already on it)` : ''}.</>)
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Could not add them')
    }
  }
  const makeList = async () => {
    const n = newList.trim()
    if (!n) return
    try {
      const j = await api.post<{ id: number }>('/api/watchlists', { name: n })
      await addTo({ id: j.id, name: n })
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Could not make the list')
    }
  }
  const exportCsv = async () => {
    const q = (v: unknown) => (/[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v))
    const cell = (f: Field, v: unknown) => (f.fmt === 'text' ? (v ?? '') : !has(v) ? '' : f.fmt === 'pct' ? +(v * 100).toFixed(4) : +v.toFixed(4))
    const text = [['Ticker', 'Company', ...cols.map((c) => c.label + (c.fmt === 'pct' ? ' (%)' : ''))].map(q).join(','), ...hits.map((r) => [r.ticker, niceName(r.name), ...cols.map((c) => cell(c, r[c.key]))].map(q).join(','))].join('\r\n')
    try {
      const j = await api.post<{ path: string }>('/api/export', { kind: 'csv', name: `Screener ${name || 'results'} ${new Date().toISOString().slice(0, 10)}`, data: text })
      toast(<>Saved {hits.length} rows: {j.path} <button type="button" className="font-semibold underline" onClick={() => void api.post('/api/reveal', { path: j.path }).catch(() => {})}>Show in folder</button></>)
    } catch (e) {
      toast('Could not save: ' + (e instanceof Error ? e.message : 'unknown error'))
    }
  }

  if (stored.isLoading) return <Page title="Screener"><Card className="p-4 text-muted-foreground">Loading companies…</Card></Page>
  if (!rows.length)
    return (
      <Page title="Screener">
        <Card className="p-4 text-muted-foreground">{stored.isError ? `Could not load the companies: ${stored.error.message}` : <>No companies in your database yet. Add one with the search box (Ctrl K), or load a first set from <Link to="/" className="font-medium text-primary hover:underline">Home, Get started</Link>.</>}</Card>
      </Page>
    )

  const chip = 'inline-flex h-8 items-center gap-1.5 rounded-md border bg-card px-2.5 text-[13px] font-medium outline-none hover:bg-secondary focus-visible:ring-2 focus-visible:ring-ring/60'
  const presetCls = (on: boolean) => cn('inline-flex h-7 items-center gap-1 rounded-full border px-3 text-xs font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/60', on ? 'border-primary bg-primary text-primary-foreground' : 'bg-card text-muted-foreground hover:bg-secondary hover:text-foreground')
  return (
    <Page title="Screener" actions={<Ago at={live.dataUpdatedAt} live={rows.some((r) => r.live)} asOf={rows.reduce<string | null>((m, r) => (typeof r.as_of === 'string' && (!m || r.as_of > m) ? r.as_of : m), null)} />}>
      <div className="-mt-2 flex flex-wrap items-center gap-1.5">
        {PRESETS.map((p) => (
          <Hint key={p[0]} label={`${p[1]}: ${p[2].map((c) => (byKey[c[0]] ? condText({ k: c[0], op: c[1], a: c[2], b: c[3] ?? null }, byKey[c[0]]) : c[0])).join(', ')}`}>
            <button type="button" aria-pressed={name === p[0]} className={presetCls(name === p[0])} onClick={() => (name === p[0] ? write(EMPTY) : write(presetScreen(p), p[0]))}>{p[0]}</button>
          </Hint>
        ))}
        {saved.length > 0 && <span className="ml-2 inline-flex items-center gap-1 text-xs text-muted-foreground"><Bookmark className="size-3.5" /> Your screens</span>}
        {saved.map((p) => (
          <span key={p.name} className={cn(presetCls(name === p.name), 'pr-1')}>
            <button type="button" aria-pressed={name === p.name} className="outline-none" onClick={() => (name === p.name ? write(EMPTY) : write(p.s, p.name))}>{p.name}</button>
            <Hint label="Delete this saved screen">
              <button type="button" aria-label={`Delete ${p.name}`} className="rounded-full p-0.5 opacity-60 hover:opacity-100" onClick={() => { setPref('state', 'scrSaved', JSON.stringify(saved.filter((x) => x.name !== p.name))); if (name === p.name) setPref('state', 'scrName', '') }}><X className="size-3" /></button>
            </Hint>
          </span>
        ))}
      </div>

      <Card className="flex flex-col gap-3 p-3">
        <div className="flex flex-wrap items-center gap-2">
          {screen.f.map((c, i) => {
            const fl = byKey[c.k]
            if (!fl) return null
            return (
              <Popover key={i} open={editing === i} onOpenChange={(o) => (o ? setEditing(i) : closeEditor())}>
                <span className={cn(chip, 'gap-0 p-0', !active(c) && 'border-dashed')}>
                  <PopoverTrigger asChild><button type="button" className="h-full rounded-l-md pr-1.5 pl-2.5 outline-none">{active(c) ? condText(c, fl) : <>{fl.label}: <i className="font-normal text-muted-foreground">set a value</i></>}</button></PopoverTrigger>
                  <button type="button" aria-label={`Remove the filter on ${fl.label}`} className="h-full rounded-r-md pr-2 pl-1 text-muted-foreground hover:text-down" onClick={() => { setEditing(-1); write({ ...screen, f: screen.f.filter((_, j) => j !== i) }) }}><X className="size-3.5" /></button>
                </span>
                <PopoverContent align="start" className="w-[330px]">
                  <FilterEditor c={c} fl={fl} rows={rows} screen={screen} index={i} onDone={closeEditor} onChange={(next) => write({ ...screen, f: screen.f.map((x, j) => (j === i ? next : x)) })} />
                </PopoverContent>
              </Popover>
            )
          })}
          <Popover open={adding} onOpenChange={setAdding}>
            <PopoverTrigger asChild><Button variant="default"><Plus /> Add filter</Button></PopoverTrigger>
            <PopoverContent align="start" className="w-72 p-1">
              <FieldList
                fields={fields.filter((f) => f.fmt !== 'text')}
                onPick={(k) => {
                  setAdding(false)
                  write({ ...screen, f: [...screen.f, { k, op: k === 'pe' || k.startsWith('debt') || k === 'payout_ratio' ? '<' : '>', a: null, b: null }] })
                  window.setTimeout(() => setEditing(screen.f.length), 50)
                }}
              />
            </PopoverContent>
          </Popover>
          <Popover>
            <PopoverTrigger asChild><button type="button" className={cn(chip, !screen.sec.length && 'text-muted-foreground')}>{screen.sec.length ? (screen.sec.length === 1 ? screen.sec[0] : `${screen.sec.length} sectors`) : 'All sectors'} <ChevronDown className="size-3.5" /></button></PopoverTrigger>
            <PopoverContent align="start" className="w-64 p-1">
              <div className={groupLabel}>Sectors</div>
              {sectors.map((k) => {
                const on = screen.sec.includes(k)
                return (
                  <button key={k} type="button" role="menuitemcheckbox" aria-checked={on} className={itemCls} onClick={() => write({ ...screen, sec: sectors.filter((x) => (x === k ? !on : screen.sec.includes(x))) })}>
                    <span className={cn('flex size-4 shrink-0 items-center justify-center rounded border', on && 'border-primary bg-primary text-primary-foreground')}>{on && <Check className="size-3" />}</span>
                    <span className="flex-1">{k}</span>
                    <span className="num text-xs text-muted-foreground">{sectorCount[k] || 0}</span>
                  </button>
                )
              })}
              <div className="mt-1 border-t p-1 pt-2"><Button size="sm" className="w-full" onClick={() => write({ ...screen, sec: [] })}>All sectors</Button></div>
            </PopoverContent>
          </Popover>
          <label className="relative">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input value={screen.q} onChange={(e) => write({ ...screen, q: e.target.value })} placeholder="Name, ticker or industry" spellCheck={false} aria-label="Search in the results" className="h-8 w-56 pl-8" />
          </label>
          {filtered && (
            <>
              <Button variant="ghost" onClick={() => write(EMPTY)}>Clear all</Button>
              <Popover open={saving} onOpenChange={(o) => { setSaving(o); if (o) setSaveName(name && !PRESETS.some((p) => p[0] === name) ? name : '') }}>
                <PopoverTrigger asChild><Button variant="ghost"><Bookmark /> Save this screen</Button></PopoverTrigger>
                <PopoverContent align="start" className="w-72">
                  <div className="px-1 pb-2 text-[13px] font-semibold">Save this screen</div>
                  <div className="flex gap-1.5">
                    <Input autoFocus value={saveName} onChange={(e) => setSaveName(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && saveScreen()} maxLength={40} placeholder="Name, e.g. My value ideas" spellCheck={false} className="h-8" />
                    <Button variant="default" disabled={!saveName.trim()} onClick={saveScreen}>Save</Button>
                  </div>
                </PopoverContent>
              </Popover>
            </>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2 border-t pt-3">
          <span className="text-[13px]"><b className="text-[15px]">{hits.length.toLocaleString('en-US')}</b> of {rows.length.toLocaleString('en-US')} companies{filtered ? ' match' : ''}</span>
          <div className="grow" />
          <Hint label="Open Compare with the first 10 results, in the order shown"><Button disabled={!hits.length} onClick={() => nav(`/compare?t=${hits.slice(0, 10).map((r) => r.ticker).join(',')}`)}><ChartLine /> Compare</Button></Hint>
          <Popover open={toList} onOpenChange={(o) => { setToList(o); if (o) setNewList(name) }}>
            <Hint label="Put the results on one of your watchlists"><PopoverAnchor asChild><Button disabled={!hits.length} onClick={() => setToList(true)}><Star /> Add to watchlist</Button></PopoverAnchor></Hint>
            <PopoverContent className="w-72 p-1">
              <div className={groupLabel}>Add {hits.length > 100 ? 'the first 100' : `all ${hits.length}`} results to</div>
              {(lists ?? []).map((l) => (
                <button key={l.id} type="button" className={itemCls} onClick={() => void addTo(l)}><span className="flex-1 truncate">{l.name}</span><span className="num text-xs text-muted-foreground">{l.tickers.length}</span></button>
              ))}
              <div className="mt-1 flex gap-1.5 border-t p-1 pt-2">
                <Input value={newList} onChange={(e) => setNewList(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void makeList()} maxLength={60} placeholder="Or a new list: name" spellCheck={false} className="h-8" />
                <Button disabled={!newList.trim()} onClick={() => void makeList()}>Make</Button>
              </div>
            </PopoverContent>
          </Popover>
          <Hint label="Save the results as a spreadsheet (CSV) in your Downloads folder"><Button disabled={!hits.length} onClick={() => void exportCsv()}><Download /> Export</Button></Hint>
          <Popover>
            <PopoverTrigger asChild><Button><Columns3 /> Columns</Button></PopoverTrigger>
            <PopoverContent className="w-72 p-1">
              <FieldList fields={fields} on={new Set(ownCols)} onPick={toggleCol} footer={<div className="border-t p-1 pt-2"><Button size="sm" className="w-full" onClick={() => setPref('state', 'scrCols', DEFAULT_COLS)}>Default columns</Button></div>} />
            </PopoverContent>
          </Popover>
        </div>
      </Card>

      <Card className="min-w-0 overflow-x-auto">
        <table className="tbl tight sticky-first">
          <thead>
            <tr>
              {[{ key: 'name', label: 'Company', tip: '' }, ...cols].map((c) => (
                <th key={c.key} aria-sort={c.key === sk ? (sd === 'asc' ? 'ascending' : 'descending') : undefined}>
                  <button type="button" title={c.tip || undefined} onClick={() => sortBy(c.key)} className={cn('inline-flex items-center gap-1 hover:text-foreground', c.key === sk && 'font-semibold text-foreground')}>
                    {c.label}
                    {c.key === sk && (sd === 'asc' ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />)}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody onKeyDown={rowKeys}>
            {hits.slice(0, limit).map((r, i) => (
              <tr key={r.ticker} tabIndex={0} className="cursor-pointer outline-none focus-visible:bg-panel" onClick={() => nav(`/stock/${r.ticker}`)}>
                <td>
                  <div className="flex items-center gap-2">
                    <span className="num w-6 shrink-0 text-right text-xs text-muted-foreground">{i + 1}</span>
                    <Logo t={r.ticker} size={18} keep />
                    <span className="w-14 shrink-0 font-semibold">{r.ticker}</span>
                    <span className="max-w-40 truncate text-muted-foreground" title={niceName(r.name)}>{niceName(r.name)}</span>
                  </div>
                </td>
                {cols.map((c) => (
                  <td key={c.key}>
                    {value(c, r[c.key])}
                    {c.key === 'price' && r.live && <span title="Live price" className="ml-1 inline-block size-1.5 rounded-full bg-up align-middle" />}
                  </td>
                ))}
              </tr>
            ))}
            {!hits.length && <tr><td colSpan={cols.length + 1} className="py-10! text-center! text-muted-foreground">No company matches all filters. Loosen or remove one of them.</td></tr>}
          </tbody>
        </table>
      </Card>
      {hits.length > limit && <div className="flex justify-center"><Button onClick={() => setLimit(1e9)}>Show all {hits.length} results</Button></div>}
      <p className="text-xs text-muted-foreground">A company without a value for a filtered number (for example free cash flow for a bank) is left out by that filter. P/E is empty when earnings are negative. Prices are live during market hours, otherwise the last close; growth and margins use the last 12 months.</p>
    </Page>
  )
}
