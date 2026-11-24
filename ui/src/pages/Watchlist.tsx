import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, Bell, BellOff, CalendarDays, ChartLine, Check, Columns3, Download, Ellipsis, FolderOpen, GripVertical, Loader2, Pencil, Plus, Search, Star, Trash2, Upload, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Card, Page } from '@/components/app/Page'
import { useWatchlists } from '@/components/app/WatchStar'
import type { WatchList } from '@/components/app/WatchStar'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Hint } from '@/components/ui/tooltip'
import { useStockPrefs } from '@/lib/alerts'
import { api } from '@/lib/api'
import type { SearchHit } from '@/lib/api'
import { big, fmt, niceName, pctSigned, tone } from '@/lib/format'
import { useSettings } from '@/lib/settings'
import { useToast } from '@/lib/toast'
import { cn, rowKeys } from '@/lib/utils'
import { Spark } from '@/pages/Home'
import { Logo } from '@/components/app/Logo'

type N = number | null | undefined
type Row = {
  ticker: string; name: string; etf: boolean; loaded: boolean; job?: string | null; position: number
  price: N; price_live?: boolean; change: N
  extended?: { session: 'pre' | 'post' | 'overnight'; price: number; change_pct: number; live?: boolean } | null
  spark?: number[]; r_1w?: N; r_1m?: N; r_3m?: N; r_6m?: N; r_ytd?: N; r_1y?: N; from_high?: N
  market_cap: N; assets: N; pe: N; fwd_pe: N; ps: N; pfcf: N; ev_ebitda: N; fcf_yield: N; div_yield: N
  rev_growth: N; eps_growth: N; net_margin: N; expense_ratio: N; target_upside: N; target: N; rating: N
  earnings_date?: string | null; sector?: string | null; added?: string | null; since_added?: N
  buy_below: N; to_buy?: N; note?: string | null
}
type Ev = { ticker: string; type: 'earnings' | 'ex_dividend' | 'dividend'; date: string; confirmed?: boolean; eps_avg?: N; revenue_avg?: N; amount?: N }
type Lists = { lists: WatchList[]; id?: number }

const has = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const dash = <span className="text-muted-foreground">–</span>
const pct = (v: N) => (has(v) ? <span className={tone(v)}>{pctSigned(v)}</span> : dash)
const shortDay = (d: string) => {
  const x = new Date(d + 'T12:00:00')
  return x.toLocaleDateString('en-US', { month: 'short', day: 'numeric', ...(x.getFullYear() !== new Date().getFullYear() ? { year: 'numeric' } : {}) })
}
const ratingText = (m: number) => (m < 1.5 ? 'Strong buy' : m < 2.5 ? 'Buy' : m < 3.5 ? 'Hold' : m < 4.5 ? 'Sell' : 'Strong sell')
const EXT = { pre: 'Pre-market', post: 'After', overnight: 'Overnight' } as const
const EV_LABEL = { earnings: 'Results', ex_dividend: 'Ex-dividend', dividend: 'Dividend paid' } as const

type Col = { k: string; label: string; cell: (r: Row) => ReactNode; sort?: (r: Row) => number | string | null | undefined; def?: boolean; tip: string; left?: boolean }
// The two cells you can type in (buy below, note) are drawn by the table itself.
const COLS: Col[] = [
  { k: 'spark', label: '', cell: (r) => <Spark values={r.spark} />, def: true, tip: 'Price over the last 3 months' },
  { k: 'price', label: 'Price', cell: () => null, sort: (r) => r.price, def: true, tip: 'Regular-session price: live during market hours, otherwise the last close. The small line is the pre-market or after-hours price.' },
  { k: 'change', label: 'Today', cell: (r) => pct(r.change), sort: (r) => r.change, def: true, tip: 'Change today (regular session)' },
  { k: 'r_1w', label: '1W', cell: (r) => pct(r.r_1w), sort: (r) => r.r_1w, tip: 'Price change over 1 week' },
  { k: 'r_1m', label: '1M', cell: (r) => pct(r.r_1m), sort: (r) => r.r_1m, tip: 'Price change over 1 month' },
  { k: 'r_3m', label: '3M', cell: (r) => pct(r.r_3m), sort: (r) => r.r_3m, tip: 'Price change over 3 months' },
  { k: 'r_6m', label: '6M', cell: (r) => pct(r.r_6m), sort: (r) => r.r_6m, tip: 'Price change over 6 months' },
  { k: 'r_ytd', label: 'YTD', cell: (r) => pct(r.r_ytd), sort: (r) => r.r_ytd, tip: 'Price change since the last close of last year' },
  { k: 'r_1y', label: '1Y', cell: (r) => pct(r.r_1y), sort: (r) => r.r_1y, def: true, tip: 'Price change over 1 year' },
  { k: 'from_high', label: 'From 52W high', cell: (r) => pct(r.from_high), sort: (r) => r.from_high, tip: 'How far below its highest close of the last 52 weeks' },
  { k: 'market_cap', label: 'Market cap', cell: (r) => (r.market_cap ? big(r.market_cap) : r.assets ? big(r.assets) : dash), sort: (r) => r.market_cap || r.assets, def: true, tip: 'Market cap (stocks) or total assets (ETFs)' },
  { k: 'pe', label: 'P/E', cell: (r) => fmt(r.pe, 'x'), sort: (r) => r.pe, def: true, tip: 'Price / earnings, last 12 months' },
  { k: 'fwd_pe', label: 'Fwd P/E', cell: (r) => fmt(r.fwd_pe, 'x'), sort: (r) => r.fwd_pe, tip: "Price / analysts' expected earnings for the next 12 months" },
  { k: 'ps', label: 'P/S', cell: (r) => fmt(r.ps, 'x'), sort: (r) => r.ps, tip: 'Price / sales, last 12 months' },
  { k: 'pfcf', label: 'P/FCF', cell: (r) => fmt(r.pfcf, 'x'), sort: (r) => r.pfcf, tip: 'Price / free cash flow, last 12 months' },
  { k: 'ev_ebitda', label: 'EV/EBITDA', cell: (r) => fmt(r.ev_ebitda, 'x'), sort: (r) => r.ev_ebitda, tip: 'Enterprise value / EBITDA, last 12 months' },
  { k: 'fcf_yield', label: 'FCF yield', cell: (r) => fmt(r.fcf_yield, 'pct2'), sort: (r) => r.fcf_yield, tip: 'Free cash flow / market cap' },
  { k: 'div_yield', label: 'Div. yield', cell: (r) => (r.div_yield === 0 ? <span className="text-muted-foreground">No dividend</span> : fmt(r.div_yield, 'pct2')), sort: (r) => r.div_yield, def: true, tip: 'Forward dividend yield: the latest regular dividend x payments a year / price' },
  { k: 'rev_growth', label: 'Rev. growth', cell: (r) => pct(r.rev_growth), sort: (r) => r.rev_growth, tip: 'Revenue growth, last 12 months vs. the 12 months before' },
  { k: 'eps_growth', label: 'EPS growth', cell: (r) => pct(r.eps_growth), sort: (r) => r.eps_growth, tip: 'EPS growth, last 12 months vs. the 12 months before' },
  { k: 'net_margin', label: 'Net margin', cell: (r) => fmt(r.net_margin, 'pct'), sort: (r) => r.net_margin, tip: 'Net income / revenue, last 12 months' },
  { k: 'expense_ratio', label: 'Expense ratio', cell: (r) => fmt(r.expense_ratio, 'pct2'), sort: (r) => r.expense_ratio, tip: 'Yearly cost of an ETF' },
  { k: 'target_upside', label: 'Target', cell: (r) => (has(r.target_upside) ? <span title={`Analysts' average price target: ${fmt(r.target, 'usdps')}`}>{pct(r.target_upside)}</span> : dash), sort: (r) => r.target_upside, tip: "Upside to the analysts' average price target" },
  { k: 'rating', label: 'Rating', cell: (r) => (has(r.rating) ? <span className={r.rating < 2.5 ? 'text-up' : r.rating >= 3.5 ? 'text-down' : ''}>{ratingText(r.rating)}</span> : dash), sort: (r) => r.rating, tip: "Analysts' average rating (1 = strong buy, 5 = strong sell)" },
  { k: 'earnings_date', label: 'Earnings', cell: (r) => (r.earnings_date ? shortDay(r.earnings_date) : dash), sort: (r) => r.earnings_date || '9999', def: true, tip: 'Next results date (Yahoo)' },
  { k: 'sector', label: 'Sector', cell: (r) => r.sector || dash, sort: (r) => r.sector || '~', tip: 'Sector (stocks) or category (ETFs)', left: true },
  { k: 'added', label: 'Added', cell: (r) => (r.added ? shortDay(r.added) : dash), sort: (r) => r.added, tip: 'Date you added it to this list' },
  { k: 'since_added', label: 'Since added', cell: (r) => pct(r.since_added), sort: (r) => r.since_added, def: true, tip: 'Price change since you added it to this list' },
  { k: 'buy_below', label: 'Buy below', cell: () => null, sort: (r) => (r.buy_below ? r.to_buy : null), def: true, tip: 'Your own price: the row lights up when the price is at or under it, and you get an alert (bell: on). Click to set or change it.' },
  { k: 'note', label: 'Note', cell: () => null, sort: (r) => (r.note || '~').toLowerCase(), def: true, tip: 'Your own note. Click to write one.', left: true },
]
const DEFAULT_COLS = COLS.filter((c) => c.def).map((c) => c.k).join(',')
const ASC_FIRST = new Set(['earnings_date', 'buy_below'])

/** A small window that asks for the name of a list (new list, rename). */
function NameDialog({ title, value, open, onClose, onOk }: { title: string; value: string; open: boolean; onClose: () => void; onOk: (name: string) => Promise<void> }) {
  const [name, setName] = useState(value)
  useEffect(() => { if (open) setName(value) }, [open, value])
  const ok = () => { if (name.trim()) void onOk(name.trim()) }
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-sm p-5">
        <DialogTitle className="text-base font-semibold">{title}</DialogTitle>
        <DialogDescription className="sr-only">Name of the list</DialogDescription>
        <Input autoFocus value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && ok()} onFocus={(e) => e.target.select()} placeholder="Name of the list" maxLength={60} spellCheck={false} className="mt-3" />
        <div className="mt-4 flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="default" disabled={!name.trim()} onClick={ok}>Save</Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

/** The box that adds a stock or ETF to the list: type, pick a result. */
function AddBox({ list, onAdd }: { list: WatchList; onAdd: (hit: SearchHit) => void }) {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const [at, setAt] = useState(0)
  const { data: hits = [] } = useQuery({ queryKey: ['find', q], queryFn: () => api.get<SearchHit[]>(`/api/find?q=${encodeURIComponent(q)}&limit=8`), enabled: q.trim().length > 0, placeholderData: keepPreviousData })
  const shown = q.trim() ? hits : []
  const pick = (h: SearchHit) => { onAdd(h); setQ(''); setOpen(false) }
  return (
    <div className="relative w-80 max-w-full">
      <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
      <Input
        value={q} placeholder={`Add a stock or ETF to ${list.name}`} spellCheck={false} autoComplete="off" aria-label="Add a stock or ETF" className="pl-8"
        onChange={(e) => { setQ(e.target.value); setOpen(true); setAt(0) }}
        onFocus={() => setOpen(true)} onBlur={() => window.setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') { e.preventDefault(); setAt((i) => Math.min(i + 1, shown.length - 1)) }
          else if (e.key === 'ArrowUp') { e.preventDefault(); setAt((i) => Math.max(i - 1, 0)) }
          else if (e.key === 'Enter' && shown[at]) pick(shown[at])
          else if (e.key === 'Escape') setOpen(false)
        }}
      />
      {open && shown.length > 0 && (
        <div className="absolute top-full left-0 z-40 mt-1 w-full rounded-lg border bg-popover p-1 shadow-lg">
          {shown.map((h, i) => {
            const on = list.tickers.includes(h.ticker)
            return (
              <button key={h.ticker} type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => pick(h)} onMouseEnter={() => setAt(i)} className={cn('flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px]', i === at && 'bg-secondary')}>
                <span className="w-14 shrink-0 font-semibold">{h.ticker}</span>
                <span className="min-w-0 flex-1 truncate text-muted-foreground">{niceName(h.name)}</span>
                {on ? <span className="text-xs text-muted-foreground">On the list</span> : !h.loaded ? <span className="text-xs text-muted-foreground">New</span> : <Plus className="size-3.5 text-muted-foreground" />}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

function Events({ id }: { id: number }) {
  const stored = useQuery({ queryKey: ['watchlist-events', id], queryFn: () => api.get<{ events: Ev[] }>(`/api/watchlists/${id}/events`) })
  const fresh = useQuery({ queryKey: ['watchlist-events', id, 'fresh'], queryFn: () => api.get<{ events: Ev[] }>(`/api/watchlists/${id}/events?refresh=1`), retry: false, staleTime: 3600e3 })
  const ev = (fresh.data ?? stored.data)?.events.slice(0, 14) ?? []
  if (!ev.length) return null
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="mr-1 inline-flex items-center gap-1.5 text-xs font-semibold text-muted-foreground"><CalendarDays className="size-3.5" /> Coming up</span>
      {ev.map((e) => {
        const tip = `${EV_LABEL[e.type]} ${e.ticker}: ${shortDay(e.date)}` + (e.type === 'earnings' ? (e.confirmed ? '' : ' (not confirmed yet)') + (has(e.eps_avg) ? `. Expected EPS ${fmt(e.eps_avg, 'usdps')}` : '') + (has(e.revenue_avg) ? `, revenue ${big(e.revenue_avg)}` : '') : has(e.amount) ? `. Last dividend $${e.amount.toFixed(4).replace(/0+$/, '').replace(/\.$/, '')} per share` : '')
        return (
          <Hint key={e.ticker + e.type + e.date} label={tip}>
            <Link to={`/stock/${e.ticker}`} className="inline-flex items-center gap-1.5 rounded-md border bg-card px-2 py-1 text-xs hover:bg-secondary">
              <span className={cn('size-1.5 rounded-full', e.type === 'earnings' ? 'bg-primary' : 'bg-up')} />
              <b className="font-semibold">{shortDay(e.date)}</b> {e.ticker}
              <span className="text-muted-foreground">{EV_LABEL[e.type].toLowerCase()}{e.type === 'earnings' && !e.confirmed ? '?' : ''}</span>
            </Link>
          </Hint>
        )
      })}
    </div>
  )
}

/** The small bell in front of a buy-below price: it also works as a price limit (alerts.py), unless watchlist alerts,
 *  price limits or this stock's limit are off. Click: the stock's alert settings. */
function BuyAlert({ t }: { t: string }) {
  const { settings } = useSettings()
  const sp = useStockPrefs()
  const rules = (settings.prefs.alerts ?? {}) as Record<string, unknown>
  const own = Number(sp.get(t, 'below')) || null
  const on = rules.wl_on !== false && rules.limit_on !== false && !sp.muted(t) && !sp.list(t, 'off').includes('limit') && !own
  return (
    <Hint label={on ? 'You get an alert when the price reaches it (phone too, if set up). Click to change.' : own ? `Your own price limit for ${t} is used instead` : 'No alert for this price (turned off in Alerts)'}>
      <Link to="/alerts/rules" onClick={(e) => e.stopPropagation()} className="mr-1 inline-flex align-middle text-muted-foreground hover:text-primary" aria-label="Alert settings">
        {on ? <Bell className="size-3" /> : <BellOff className="size-3" />}
      </Link>
    </Hint>
  )
}

/** A cell you type in: your buy-below price or your note. Enter or clicking away saves, Esc cancels. */
function EditCell({ row, k, onSave }: { row: Row; k: 'buy_below' | 'note'; onSave: (value: string) => void }) {
  const [editing, setEditing] = useState(false)
  const cancelled = useRef(false)
  if (editing)
    return (
      <input
        autoFocus defaultValue={row[k] ?? ''} maxLength={k === 'note' ? 500 : 20} inputMode={k === 'note' ? 'text' : 'decimal'} placeholder={k === 'note' ? 'Your note' : 'Price'} spellCheck={false}
        className={cn('h-6 rounded border bg-card px-1.5 text-[13px] outline-none focus:border-ring', k === 'note' ? 'w-44' : 'w-20 text-right')}
        onFocus={(e) => e.target.select()}
        onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); if (e.key === 'Escape') { cancelled.current = true; e.currentTarget.blur() } }}
        onBlur={(e) => { setEditing(false); if (!cancelled.current) onSave(k === 'buy_below' ? e.target.value.replace(/[$,\s]/g, '') : e.target.value.trim()); cancelled.current = false }}
      />
    )
  const btn = 'cursor-text rounded px-1 py-0.5 -mx-1 hover:bg-secondary'
  if (k === 'note')
    return <button type="button" title={row.note || undefined} onClick={() => setEditing(true)} className={cn(btn, 'inline-block max-w-40 truncate align-bottom', !row.note && 'text-muted-foreground')}>{row.note || '+ note'}</button>
  if (!row.buy_below) return <button type="button" onClick={() => setEditing(true)} className={cn(btn, 'text-muted-foreground')}>+ set</button>
  const hit = has(row.to_buy) && row.to_buy <= 0
  return (
    <>
      <BuyAlert t={row.ticker} />
      <button type="button" onClick={() => setEditing(true)} className={btn}>{fmt(row.buy_below, 'usdps')}</button>
      <span className={cn('ml-1.5 text-xs', hit ? 'font-semibold text-up' : 'text-muted-foreground')}>{hit ? 'below' : has(row.to_buy) ? `+${(row.to_buy * 100).toFixed(1)}%` : ''}</span>
    </>
  )
}

function Ago({ at }: { at: number }) {
  const [, tick] = useState(0)
  useEffect(() => { const i = window.setInterval(() => tick((n) => n + 1), 15000); return () => window.clearInterval(i) }, [])
  if (!at) return null
  const m = Math.floor((Date.now() - at) / 60000)
  return <span className="text-xs text-muted-foreground">{m < 1 ? 'Prices updated just now' : `Prices updated ${m}m ago`}</span>
}

export function WatchlistPage() {
  const { id: idArg } = useParams()
  const nav = useNavigate()
  const qc = useQueryClient()
  const toast = useToast()
  const { settings, pref, setPref } = useSettings()
  const { data: lists } = useWatchlists()
  const [first, setFirst] = useState('My watchlist')
  const [dialog, setDialog] = useState<'new' | 'rename' | 'delete' | 'import' | null>(null)
  const [menu, setMenu] = useState(false)
  const [saved, setSaved] = useState('')
  const [imp, setImp] = useState('')
  const [impErr, setImpErr] = useState('')
  const [drag, setDrag] = useState<string | null>(null)
  const [order, setOrder] = useState<string[] | null>(null)
  const [tabDrag, setTabDrag] = useState<number | null>(null)

  const want = Number(idArg) || pref('ui2', 'wl_id', 0)
  const list = lists?.find((l) => l.id === want) ?? lists?.[0]
  const lid = list?.id
  useEffect(() => {
    if (!lid) return
    if (Number(idArg) !== lid) nav(`/watchlist/${lid}`, { replace: true })
    if (pref('ui2', 'wl_id', 0) !== lid) setPref('ui2', 'wl_id', lid)
    setSaved('')
  }, [lid, idArg]) // eslint-disable-line react-hooks/exhaustive-deps

  const stored = useQuery({
    queryKey: ['watchlist-rows', lid, 'stored'], queryFn: () => api.get<{ rows: Row[] }>(`/api/watchlists/${lid}/rows?live=0`), enabled: !!lid,
    refetchInterval: (q) => (q.state.data?.rows.some((r) => r.job === 'running') ? 4000 : false),
  })
  const live = useQuery({ queryKey: ['watchlist-rows', lid, 'live'], queryFn: () => api.get<{ rows: Row[] }>(`/api/watchlists/${lid}/rows?live=1`), enabled: !!lid, refetchInterval: 60_000, retry: false })
  // the newest answer wins: stored numbers first, then live prices; a change you make refetches both
  const data = live.data && live.dataUpdatedAt >= stored.dataUpdatedAt ? live.data : (stored.data ?? live.data)
  const rows = useMemo(() => data?.rows ?? [], [data])

  const cols = useMemo(() => {
    const on = pref<string>('ui2', 'wl_cols', DEFAULT_COLS).split(',')
    return COLS.filter((c) => on.includes(c.k))
  }, [pref])
  const [sk, sd] = pref<string>('ui2', 'wl_sort', '').split(':')
  const sorted = useMemo(() => {
    const col = COLS.find((c) => c.k === sk)
    if (!col?.sort) {
      const own = [...rows].sort((a, b) => a.position - b.position)
      return order ? [...own].sort((a, b) => order.indexOf(a.ticker) - order.indexOf(b.ticker)) : own
    }
    const s = sd === 'asc' ? 1 : -1
    const ok = (v: unknown) => v !== null && v !== undefined && v !== ''
    return [...rows].sort((a, b) => {
      const x = col.sort!(a)
      const y = col.sort!(b)
      if (!ok(x) && !ok(y)) return a.position - b.position
      if (!ok(x)) return 1
      if (!ok(y)) return -1
      return (x! < y! ? -1 : x! > y! ? 1 : 0) * s
    })
  }, [rows, sk, sd, order])
  const own = !sk

  const refresh = () => { setOrder(null); void qc.invalidateQueries({ queryKey: ['watchlist-rows', lid] }) }
  const setLists = (r: Partial<Lists>) => { if (r.lists) qc.setQueryData(['watchlists'], r.lists) }
  const fail = (e: unknown) => toast(e instanceof Error ? e.message : 'That did not work')
  const act = async (f: () => Promise<void>) => { try { await f() } catch (e) { fail(e) } }

  const sortBy = (k: string) => {
    const firstDir = ASC_FIRST.has(k) ? 'asc' : 'desc'
    setPref('ui2', 'wl_sort', !k ? '' : sk !== k ? `${k}:${firstDir}` : sd === firstDir ? `${k}:${firstDir === 'asc' ? 'desc' : 'asc'}` : '')
  }
  const add = (h: SearchHit) => act(async () => {
    if (!list) return
    if (list.tickers.includes(h.ticker)) return toast(`${h.ticker} is already on ${list.name}`)
    setLists(await api.post<Lists>(`/api/watchlists/${list.id}/items`, { ticker: h.ticker }))
    if (!h.loaded) {
      await api.post(`/api/load/${encodeURIComponent(h.ticker)}`).catch(() => {})
      toast(`${h.ticker} added. Its data is loading in the background (about a minute).`)
    }
    refresh()
  })
  const remove = (t: string) => act(async () => { setLists(await api.del<Lists>(`/api/watchlists/${lid}/items/${encodeURIComponent(t)}`)); refresh() })
  const loadData = (t: string) => act(async () => { await api.post(`/api/load/${encodeURIComponent(t)}`); refresh() })
  const edit = (t: string, k: 'buy_below' | 'note', v: string) => act(async () => { await api.put(`/api/watchlists/${lid}/items/${encodeURIComponent(t)}`, { [k]: v }); refresh() })
  const dropRow = () => act(async () => {
    setDrag(null)
    if (order) await api.put(`/api/watchlists/${lid}/order`, { tickers: order })
    refresh()
  })
  const overRow = (t: string) => {
    if (!drag || drag === t) return
    const cur = order ?? sorted.map((r) => r.ticker)
    const next = cur.filter((x) => x !== drag)
    next.splice(cur.indexOf(t), 0, drag)
    setOrder(next)
  }
  const overTab = (id: number) => {
    if (tabDrag === null || tabDrag === id || !lists) return
    const ids = lists.map((l) => l.id)
    const next = ids.filter((x) => x !== tabDrag)
    next.splice(ids.indexOf(id), 0, tabDrag)
    qc.setQueryData(['watchlists'], next.map((x) => lists.find((l) => l.id === x)))
  }
  const dropTab = () => act(async () => { setTabDrag(null); if (lists) await api.put('/api/watchlists/order', { ids: lists.map((l) => l.id) }) })
  const compare = () => {
    const ts = sorted.filter((r) => r.loaded).map((r) => r.ticker).slice(0, 10)
    if (ts.length) nav(`/compare?t=${ts.join(',')}&v=price`)
    else toast('Load the data of a ticker first: Compare needs stored prices.')
  }
  const toggleCol = (k: string) => {
    const on = new Set(cols.map((c) => c.k))
    if (on.has(k)) on.delete(k)
    else on.add(k)
    setPref('ui2', 'wl_cols', COLS.map((c) => c.k).filter((x) => on.has(x)).join(',') || 'price')
  }
  const doExport = () => act(async () => { setMenu(false); setSaved((await api.post<{ path: string }>(`/api/watchlists/${lid}/export`)).path) })
  const doImport = async () => {
    try {
      const j = await api.post<Lists & { added: number; found: number; unknown: string[] }>(`/api/watchlists/${lid}/import`, { text: imp })
      setLists(j)
      setDialog(null)
      setImp('')
      toast(`${j.added} of ${j.found} tickers added${j.found > j.added ? ' (the others were on the list already)' : ''}.` + (j.unknown.length ? ` Not in the SEC list: ${j.unknown.slice(0, 8).join(', ')}${j.unknown.length > 8 ? '…' : ''} (use "Load data" for funds).` : ''))
      refresh()
    } catch (e) {
      setImpErr(e instanceof Error ? e.message : 'Could not import')
    }
  }

  if (!lists) return <Page title="Watchlist"><Card className="p-4 text-muted-foreground">Loading…</Card></Page>
  if (!list) {
    const make = () => act(async () => { const j = await api.post<Lists>('/api/watchlists', { name: first.trim() || 'My watchlist' }); setLists(j); nav(`/watchlist/${j.id}`) })
    return (
      <Page title="Watchlist">
        <Card className="flex flex-col items-center gap-3 px-6 py-14 text-center">
          <span className="flex size-11 items-center justify-center rounded-full bg-secondary text-muted-foreground"><Star className="size-5" /></span>
          <h2 className="text-base font-semibold">Keep an eye on stocks and ETFs</h2>
          <p className="max-w-lg text-muted-foreground">Make a list of companies you are interested in: live prices, returns, valuation, analyst targets and the next results date in one table. Add tickers here, or with the star on any stock page. You can keep several lists, for example "Buy ideas" and "Dividend".</p>
          <div className="mt-1 flex gap-2">
            <Input value={first} onChange={(e) => setFirst(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void make()} placeholder="Name of the list" maxLength={60} className="w-60" />
            <Button variant="default" onClick={() => void make()}>Make the list</Button>
          </div>
        </Card>
      </Page>
    )
  }

  const hits = rows.filter((r) => has(r.to_buy) && r.to_buy <= 0)
  const item = 'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] outline-none hover:bg-secondary focus-visible:bg-secondary [&_svg]:size-4 [&_svg]:text-muted-foreground'
  return (
    <Page title="Watchlist" actions={<Ago at={live.dataUpdatedAt} />}>
      <div className="-mt-1 flex flex-wrap items-center gap-1 border-b">
        {lists.map((l) => (
          <Link
            key={l.id} to={`/watchlist/${l.id}`} draggable
            onDragStart={(e) => { setTabDrag(l.id); e.dataTransfer.effectAllowed = 'move' }} onDragOver={(e) => { if (tabDrag !== null) { e.preventDefault(); overTab(l.id) } }} onDrop={(e) => { e.preventDefault(); void dropTab() }} onDragEnd={() => setTabDrag(null)}
            className={cn('-mb-px flex items-center gap-1.5 border-b-2 border-transparent px-3 py-2 text-[13px] font-medium text-muted-foreground hover:text-foreground', l.id === list.id && 'border-primary text-foreground', tabDrag === l.id && 'opacity-50')}
          >
            {l.name}
            <span className="num rounded bg-secondary px-1.5 text-[11px] text-muted-foreground">{l.tickers.length}</span>
          </Link>
        ))}
        <Button variant="ghost" size="sm" className="ml-1" onClick={() => setDialog('new')}><Plus /> New list</Button>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <AddBox list={list} onAdd={(h) => void add(h)} />
        <div className="grow" />
        <Hint label="Open Compare with the first 10 tickers of this list, in the order shown"><Button onClick={compare}><ChartLine /> Compare</Button></Hint>
        <Popover>
          <PopoverTrigger asChild><Button><Columns3 /> Columns</Button></PopoverTrigger>
          <PopoverContent className="max-h-[460px] w-56 overflow-y-auto">
            <div className="px-2 pt-1 pb-1.5 text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">Columns to show</div>
            {COLS.map((c) => {
              const on = cols.includes(c)
              return (
                <button key={c.k} type="button" role="menuitemcheckbox" aria-checked={on} title={c.tip} onClick={() => toggleCol(c.k)} className={item}>
                  <span className={cn('flex size-4 items-center justify-center rounded border', on && 'border-primary bg-primary')}>{on && <Check className="size-3! text-primary-foreground!" />}</span>
                  {c.k === 'spark' ? '3-month chart' : c.label}
                </button>
              )
            })}
            <div className="mt-1.5 border-t pt-2"><Button size="sm" className="w-full" onClick={() => setPref('ui2', 'wl_cols', DEFAULT_COLS)}>Default columns</Button></div>
          </PopoverContent>
        </Popover>
        <Popover open={menu} onOpenChange={setMenu}>
          <Hint label="Rename, import, export, delete"><PopoverTrigger asChild><Button size="icon" aria-label="More for this list"><Ellipsis /></Button></PopoverTrigger></Hint>
          <PopoverContent className="w-56 p-1" onCloseAutoFocus={(e) => e.preventDefault()}>
            <button type="button" className={item} onClick={() => { setMenu(false); setDialog('rename') }}><Pencil /> Rename list</button>
            <button type="button" className={item} onClick={() => { setMenu(false); setImpErr(''); setDialog('import') }}><Upload /> Import tickers…</button>
            <button type="button" className={item} onClick={() => void doExport()}><Download /> Save as spreadsheet</button>
            <button type="button" className={cn(item, 'text-down [&_svg]:text-down')} onClick={() => { setMenu(false); setDialog('delete') }}><Trash2 /> Delete list…</button>
          </PopoverContent>
        </Popover>
      </div>

      <Events id={list.id} />
      {saved && (
        <div className="flex items-center gap-2 rounded-lg border bg-panel px-3 py-2 text-[13px]">
          <span className="min-w-0 truncate">Saved: {saved}</span>
          <Button size="sm" onClick={() => void api.post('/api/reveal', { path: saved }).catch(() => {})}><FolderOpen /> Show in folder</Button>
          <div className="grow" />
          <Button variant="ghost" size="icon-sm" aria-label="Close" onClick={() => setSaved('')}><X /></Button>
        </div>
      )}
      {stored.isError && <Card className="p-4 text-down">Could not load the list: {stored.error.message}</Card>}

      <Card className="min-w-0 overflow-x-auto">
        <table className="tbl tight sticky-first">
          <thead>
            <tr>
              <th>
                <Hint label={sk ? 'Back to your own order (drag rows to change it)' : 'Your own order: drag a row to move it'}>
                  <button type="button" onClick={() => sortBy('')} className={cn('hover:text-foreground', own && 'font-semibold text-foreground')}>{rows.length} {rows.length === 1 ? 'ticker' : 'tickers'}</button>
                </Hint>
              </th>
              {cols.map((c) => (
                <th key={c.k} className={cn(c.left && 'text-left!')} aria-sort={c.k === sk ? (sd === 'asc' ? 'ascending' : 'descending') : undefined}>
                  {c.sort ? (
                    <Hint label={c.tip}>
                      <button type="button" onClick={() => sortBy(c.k)} className={cn('inline-flex items-center gap-1 hover:text-foreground', c.k === sk && 'font-semibold text-foreground')}>
                        {c.label}
                        {c.k === sk && (sd === 'asc' ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />)}
                      </button>
                    </Hint>
                  ) : c.label}
                </th>
              ))}
              <th />
            </tr>
          </thead>
          <tbody onKeyDown={(e) => rowKeys(e, (row) => row.dataset.href && nav(row.dataset.href))}>
            {sorted.map((r) => {
              const hit = has(r.to_buy) && r.to_buy <= 0
              const x = settings.show_extended ? r.extended : null
              return (
                <tr
                  key={r.ticker} tabIndex={0} data-href={r.etf ? `/etf/${r.ticker}` : r.loaded ? `/stock/${r.ticker}` : `/load/${r.ticker}`} draggable={own} className={cn('group outline-none focus-visible:bg-panel', hit && 'hit [&>td]:bg-up-bg/60', drag === r.ticker && 'opacity-40')}
                  onDragStart={(e) => { setDrag(r.ticker); e.dataTransfer.effectAllowed = 'move' }} onDragOver={(e) => { if (drag) { e.preventDefault(); overRow(r.ticker) } }} onDrop={(e) => { e.preventDefault(); void dropRow() }} onDragEnd={() => { setDrag(null); setOrder(null) }}
                >
                  <td>
                    <div className="flex items-center gap-2">
                      {own && <GripVertical className="-ml-1.5 size-3.5 shrink-0 cursor-grab text-muted-foreground/50" aria-label="Drag to move" />}
                      <Logo t={r.ticker} size={18} keep />
                      <Link to={r.etf ? `/etf/${r.ticker}` : `/stock/${r.ticker}`} className="w-14 shrink-0 font-semibold hover:text-primary hover:underline">{r.ticker}</Link>
                      <span className="max-w-40 truncate text-muted-foreground" title={niceName(r.name)}>{r.name && r.name !== r.ticker ? niceName(r.name) : ''}</span>
                      {r.etf && <span className="rounded bg-secondary px-1 text-[10px] font-semibold text-muted-foreground">ETF</span>}
                      {r.job === 'running' ? (
                        <span className="inline-flex items-center gap-1 text-xs text-muted-foreground"><Loader2 className="size-3 animate-spin" /> loading</span>
                      ) : !r.loaded && r.job === 'error' ? (
                        // the download failed: say so instead of quietly going back to "Load data"
                        <span className="inline-flex items-center gap-1.5 text-xs text-down">
                          Couldn't download
                          <button type="button" onClick={() => void loadData(r.ticker)} className="rounded border px-1.5 text-primary hover:bg-secondary">Try again</button>
                        </span>
                      ) : !r.loaded ? (
                        <Hint label="Download its data (SEC filings and prices, about a minute) for returns, valuation and charts">
                          <button type="button" onClick={() => void loadData(r.ticker)} className="inline-flex items-center gap-1 rounded border px-1.5 text-xs text-primary hover:bg-secondary"><Download className="size-3" /> Load data</button>
                        </Hint>
                      ) : null}
                    </div>
                  </td>
                  {cols.map((c) => (
                    <td key={c.k} className={cn(c.left && 'text-left!')}>
                      {c.k === 'price' ? (
                        has(r.price) ? (
                          <>
                            <span className="font-semibold">{fmt(r.price, 'usdps')}</span>
                            {r.price_live && <span title="Live price" className="ml-1 inline-block size-1.5 rounded-full bg-up align-middle" />}
                            {x?.price ? <div className="text-[11px] text-muted-foreground">{EXT[x.session]} {fmt(x.price, 'usdps')} <span className={tone(x.change_pct)}>{pctSigned(x.change_pct)}</span></div> : null}
                          </>
                        ) : dash
                      ) : c.k === 'buy_below' || c.k === 'note' ? (
                        <EditCell row={r} k={c.k} onSave={(v) => void edit(r.ticker, c.k as 'note', v)} />
                      ) : c.cell(r)}
                    </td>
                  ))}
                  <td className="w-8 px-1!">
                    <Hint label="Remove from this list"><button type="button" aria-label={`Remove ${r.ticker}`} onClick={() => void remove(r.ticker)} className="rounded p-1 text-muted-foreground opacity-0 hover:bg-secondary hover:text-down group-hover:opacity-100 focus-visible:opacity-100"><X className="size-3.5" /></button></Hint>
                  </td>
                </tr>
              )
            })}
            {!sorted.length && (
              <tr><td colSpan={cols.length + 2} className="py-10! text-center! text-muted-foreground">{stored.isLoading ? 'Loading…' : 'This list is empty. Add a ticker with the box above, or with the star on a stock page.'}</td></tr>
            )}
          </tbody>
        </table>
      </Card>
      <p className="text-xs text-muted-foreground">
        {hits.length > 0 && <><b className="text-up">{hits.map((r) => r.ticker).join(', ')}</b> {hits.length === 1 ? 'is' : 'are'} at or under your buy-below price. </>}
        Prices are live during market hours (updated every minute), otherwise the last close. Returns are price changes, without dividends.
      </p>

      <NameDialog title="New list" value="" open={dialog === 'new'} onClose={() => setDialog(null)} onOk={(name) => act(async () => { const j = await api.post<Lists>('/api/watchlists', { name }); setLists(j); setDialog(null); nav(`/watchlist/${j.id}`) })} />
      <NameDialog title="Rename list" value={list.name} open={dialog === 'rename'} onClose={() => setDialog(null)} onOk={(name) => act(async () => { setLists(await api.put<Lists>(`/api/watchlists/${list.id}`, { name })); setDialog(null) })} />
      <Dialog open={dialog === 'delete'} onOpenChange={(o) => !o && setDialog(null)}>
        <DialogContent className="max-w-sm p-5">
          <DialogTitle className="text-base font-semibold">Delete "{list.name}"?</DialogTitle>
          <DialogDescription className="mt-2 text-muted-foreground">{rows.length ? `Its ${rows.length} ${rows.length === 1 ? 'ticker is' : 'tickers are'} removed from this list only: the companies stay in your database.` : 'The list is empty.'}</DialogDescription>
          <div className="mt-4 flex justify-end gap-2">
            <Button onClick={() => setDialog(null)}>Cancel</Button>
            <Button variant="default" className="bg-down hover:bg-down/90" onClick={() => void act(async () => { setLists(await api.del<Lists>(`/api/watchlists/${list.id}`)); setDialog(null); setPref('ui2', 'wl_id', 0); nav('/watchlist') })}>Delete list</Button>
          </div>
        </DialogContent>
      </Dialog>
      <Dialog open={dialog === 'import'} onOpenChange={(o) => !o && setDialog(null)}>
        <DialogContent className="max-w-md p-5">
          <DialogTitle className="text-base font-semibold">Import into {list.name}</DialogTitle>
          <DialogDescription className="mt-2 text-[13px] text-muted-foreground">Paste tickers (AAPL, MSFT, NVDA) or choose a CSV file with a "Ticker" or "Symbol" column, for example an export from this app or from another site. Buy-below prices and notes come along when the file has them.</DialogDescription>
          <textarea value={imp} onChange={(e) => setImp(e.target.value)} rows={6} placeholder="AAPL, MSFT, NVDA" spellCheck={false} className="mt-3 w-full rounded-md border bg-card p-2 text-[13px] outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30" />
          {impErr && <p className="mt-1 text-[13px] text-down">{impErr}</p>}
          <div className="mt-3 flex gap-2">
            <Button asChild>
              <label className="cursor-pointer">
                <FolderOpen /> Choose file…
                <input type="file" accept=".csv,.txt,text/csv,text/plain" hidden onChange={(e) => void e.target.files?.[0]?.text().then(setImp)} />
              </label>
            </Button>
            <div className="grow" />
            <Button onClick={() => setDialog(null)}>Cancel</Button>
            <Button variant="default" disabled={!imp.trim()} onClick={() => void doImport()}>Import</Button>
          </div>
        </DialogContent>
      </Dialog>
    </Page>
  )
}
