import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  BellOff, Briefcase, ChartColumn, CheckCheck, ChevronDown, CircleDollarSign, ExternalLink, Loader2, MoveVertical, Search,
  Send, Smartphone, Sparkles, Star, Sunrise, Target, TriangleAlert, X,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Link, Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { AlertBell } from '@/components/app/AlertBell'
import { Card, Page } from '@/components/app/Page'
import { Empty, H2 } from '@/components/app/bits'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Segmented } from '@/components/ui/segmented'
import { Switch } from '@/components/ui/switch'
import { TabLinks } from '@/components/ui/tabs'
import { Hint } from '@/components/ui/tooltip'
import { alertLink, kindIcon, limitDistance, useMarkRead, when } from '@/lib/alerts'
import type { AlertItem, AlertList, LastCheck, StockAlert } from '@/lib/alerts'
import { api } from '@/lib/api'
import { useSettings } from '@/lib/settings'
import { useToast } from '@/lib/toast'
import { cn } from '@/lib/utils'
import { sharePrice, t212Symbol } from '@/pages/portfolio/common'

// Alerts (stockterminal/alerts.py): price limits, big moves, results, dividend cuts, analysts, for your portfolio, your
// watchlists and any stock you gave an alert, in the app and on your phone (ntfy). The rules are your own settings:
// group "alerts" (one key per rule); a stock's own settings are group "alerts_stock" (see lib/alerts.tsx).

const KIND_FILTER = [
  { value: 'all', label: 'All' }, { value: 'limit', label: 'Price limits' }, { value: 'move', label: 'Moves' },
  { value: 'earnings', label: 'Results' }, { value: 'dividend', label: 'Dividends' }, { value: 'analysts', label: 'Analysts' },
  { value: 'portfolio', label: 'Portfolio' },
]
const FROM_FILTER = [
  { value: 'all', label: 'All stocks' }, { value: 'portfolio', label: 'Holdings' }, { value: 'watchlist', label: 'Watchlist' }, { value: 'other', label: 'Other' },
]
const PAGE = 100

export function AlertsPage() {
  const { tab } = useParams()
  if (tab !== 'list' && tab !== 'rules') return <Navigate to="/alerts/list" replace />
  return (
    <Page title="Alerts">
      <p className="-mt-3 text-[13px] text-muted-foreground">
        For your holdings, your watchlists and any stock you set an alert on (the bell on its page). Prices are checked every 5 minutes while
        markets are open, everything else every morning at 07:00.
      </p>
      <TabLinks label="Alerts" tabs={[{ to: '/alerts/list', label: 'Alerts' }, { to: '/alerts/rules', label: 'Rules' }]} />
      {tab === 'rules' ? <Rules /> : <Feed />}
    </Page>
  )
}

/** "Last check today 07:00: 3 new" and the problems of that check, visible. */
function CheckStatus({ lc, running }: { lc: LastCheck; running: boolean }) {
  if (running) return <span className="inline-flex items-center gap-1.5"><Loader2 className="size-3.5 animate-spin" /> Checking…</span>
  if (!lc) return <>Not checked yet</>
  return <>Last check {when(lc.at).toLowerCase()}: {lc.new} new{lc.pushed ? `, ${lc.pushed} to your phone` : ''}</>
}

// ------------------------------------------------------------------------------------------------ the list
function Feed() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const markRead = useMarkRead()
  const { pref, setPref } = useSettings()
  const [params, setParams] = useSearchParams()
  const stock = params.get('t') ?? ''
  // the old filter mixed kinds and "watchlist": that one moves to the stock filter
  const savedKind = pref<string>('ui2', 'alert_kind', 'all')
  const kind = savedKind === 'watchlist' ? 'all' : savedKind
  const from = pref<string>('ui2', 'alert_from', savedKind === 'watchlist' ? 'watchlist' : 'all')
  const unreadOnly = pref<boolean>('ui2', 'alert_unread', false)
  const [text, setText] = useState('')
  const [q, setQ] = useState('')
  const [pages, setPages] = useState(1)
  useEffect(() => {
    const t = window.setTimeout(() => setQ(text.trim()), 250)
    return () => window.clearTimeout(t)
  }, [text])
  useEffect(() => setPages(1), [kind, from, unreadOnly, q, stock])

  const qs = new URLSearchParams({ limit: String(PAGE * pages) })
  if (kind !== 'all') qs.set('kind', kind)
  if (from !== 'all') qs.set('origin', from)
  if (unreadOnly) qs.set('unread', '1')
  if (q) qs.set('q', q)
  if (stock) qs.set('ticker', stock)
  const query = useQuery({
    queryKey: ['alerts', 'feed', qs.toString()],
    queryFn: () => api.get<AlertList>(`/api/alerts?${qs.toString()}`),
    refetchInterval: (x) => (x.state.data?.running ? 3000 : 60_000),
    placeholderData: (prev) => prev,
  })
  const d = query.data
  if (!d) return <Card><Empty>{query.isError ? 'Could not load the alerts. Is the app still running?' : 'Loading…'}</Empty></Card>

  const days: [string, AlertItem[]][] = []
  for (const i of d.items) {
    const day = i.created.slice(0, 10)
    if (!days.length || days[days.length - 1][0] !== day) days.push([day, []])
    days[days.length - 1][1].push(i)
  }
  const check = async () => {
    await api.post('/api/alerts/check')
    qc.setQueriesData<AlertList>({ queryKey: ['alerts', 'feed'] }, (old) => (old ? { ...old, running: true } : old))
  }
  const open = (i: AlertItem) => {
    if (!i.read) void markRead({ ids: [i.id] })
    const to = alertLink(i)
    if (to) navigate(to)
  }
  const lc = d.last_check
  const filtered = kind !== 'all' || from !== 'all' || unreadOnly || !!q || !!stock

  return (
    <>
      <div className="flex flex-wrap items-center gap-2.5">
        <div className="relative w-full max-w-64">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input className="pl-8" value={text} onChange={(e) => setText(e.target.value)} placeholder="Search alerts (stock or words)" aria-label="Search alerts" />
        </div>
        {stock && (
          <span className="inline-flex h-8 items-center gap-1.5 rounded-md border bg-card pr-1 pl-2.5 text-[13px]">
            Only <b>{stock}</b>
            <Button size="icon" variant="ghost" className="size-6" aria-label="Show all stocks" onClick={() => { params.delete('t'); setParams(params) }}><X /></Button>
          </span>
        )}
        <Segmented label="Kind of alert" value={kind} onChange={(v) => setPref('ui2', 'alert_kind', v)} options={KIND_FILTER} />
        <Segmented label="Which stocks" value={from} onChange={(v) => setPref('ui2', 'alert_from', v)} options={FROM_FILTER} />
        <label className="flex h-8 items-center gap-2 text-[13px]">
          <Switch checked={unreadOnly} onCheckedChange={(v) => setPref('ui2', 'alert_unread', v)} />
          Unread only
        </label>
      </div>
      <div className="flex flex-wrap items-center gap-2.5 text-[12.5px] text-muted-foreground">
        <span><CheckStatus lc={lc} running={d.running} /></span>
        {d.last_live && <span>· prices {when(d.last_live.at).toLowerCase()}</span>}
        <div className="grow" />
        <Button onClick={check} disabled={d.running}>Check now</Button>
        <Button onClick={() => void markRead(stock ? { ticker: stock } : {})} disabled={!d.unread}><CheckCheck /> Mark all read</Button>
      </div>
      {lc && lc.problems.length > 0 && !d.running && (
        <div className="flex gap-2.5 rounded-lg border border-flag/40 bg-flag/10 px-3.5 py-2.5 text-[13px]">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-flag" />
          <div>
            <div className="font-medium">{lc.failed ? 'The last check stopped early' : 'Not everything could be checked last time'}</div>
            <ul className="text-muted-foreground">{lc.problems.map((p) => <li key={p}>{p}</li>)}</ul>
          </div>
        </div>
      )}
      <Card className="px-5 py-2">
        {!d.items.length ? (
          <Empty>
            {filtered ? 'No alerts match these filters.'
              : <>No alerts yet. Set a price limit with the bell on a stock's page, or choose what to hear about under <Link className="text-primary hover:underline" to="/alerts/rules">Rules</Link>.</>}
          </Empty>
        ) : (
          days.map(([day, list]) => (
            <div key={day}>
              <div className="pt-3 pb-1 text-[11.5px] font-semibold tracking-wide text-muted-foreground uppercase">
                {when(day + ' 12:00', false) === 'Today' || when(day + ' 12:00', false) === 'Yesterday'
                  ? `${when(day + ' 12:00', false)} · ${new Date(day + 'T12:00:00').toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long' })}`
                  : new Date(day + 'T12:00:00').toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long', ...(day.slice(0, 4) !== String(new Date().getFullYear()) ? { year: 'numeric' } : {}) })}
              </div>
              {list.map((i) => <FeedRow key={i.id} i={i} kinds={d.kinds} phoneSet={d.phone} onOpen={open} />)}
            </div>
          ))
        )}
        {d.total > d.items.length && (
          <div className="flex justify-center border-t py-3">
            <Button variant="ghost" onClick={() => setPages((p) => p + 1)}>Show more ({d.total - d.items.length} older)</Button>
          </div>
        )}
      </Card>
      {!d.phone && (
        <p className="text-[12.5px] text-muted-foreground">
          Tip: get the important ones on your phone too, see <Link className="text-primary hover:underline" to="/alerts/rules">Rules → Phone</Link>.
        </p>
      )}
    </>
  )
}

/** Body text with its line breaks kept and links shown as links ("Press release: https://..."). */
function Body({ text }: { text: string }) {
  return (
    <>
      {text.split('\n').map((line, n) => {
        const m = line.match(/^(.*?)(https?:\/\/\S+)(.*)$/)
        return (
          <div key={n}>
            {m ? <>{m[1]}<a href={m[2]} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}
              className="inline-flex items-center gap-0.5 text-primary hover:underline">{m[1].trim().endsWith(':') ? 'open' : new URL(m[2]).hostname}<ExternalLink className="size-3" /></a>{m[3]}</> : line}
          </div>
        )
      })}
    </>
  )
}

function FeedRow({ i, kinds, phoneSet, onOpen }: { i: AlertItem; kinds: Record<string, string>; phoneSet: boolean; onOpen: (i: AlertItem) => void }) {
  const Icon = kindIcon(i.kind)
  const to = alertLink(i)
  const phone = !i.push ? null : i.pushed && i.pushed !== 'no' ? `Sent to your phone at ${i.pushed.slice(11)}`
    : i.pushed === 'no' ? 'Not sent to your phone: one message per stock per day' : phoneSet ? 'Waiting for the end of quiet hours' : 'No phone set up (Rules → Phone)'
  const watched = !!i.t212_ticker?.startsWith('wl:')
  return (
    <div
      role={to ? 'link' : undefined}
      tabIndex={to ? 0 : undefined}
      onClick={() => onOpen(i)}
      onKeyDown={(e) => e.key === 'Enter' && onOpen(i)}
      className={cn(
        'grid grid-cols-[2rem_minmax(0,1fr)_auto] items-start gap-3 border-b py-3 last:border-0',
        to && 'cursor-pointer rounded-md hover:bg-secondary/60',
        !i.read && 'shadow-[inset_3px_0_0_var(--color-primary)]',
      )}
    >
      <span className={cn('ml-1.5 flex size-8 items-center justify-center rounded-lg bg-secondary text-muted-foreground',
        i.level > 0 && 'bg-up-bg text-up', i.level < 0 && 'bg-down-bg text-down')}>
        <Icon className="size-4" />
      </span>
      <div className="min-w-0">
        <div className={cn('flex flex-wrap items-baseline gap-x-2 text-[13.5px]', !i.read && 'font-semibold')}>
          {i.ticker && <span className="num rounded bg-secondary px-1.5 text-[11.5px] font-semibold text-foreground">{i.ticker}</span>}
          {watched && <Hint label="On your watchlist"><Star className="inline size-3.5 fill-current text-amber-500" /></Hint>}
          <span className="min-w-0">{i.title}</span>
        </div>
        <div className="text-[13px] text-muted-foreground"><Body text={i.body} /></div>
      </div>
      <div className="flex items-center gap-2.5 pr-1.5 text-xs whitespace-nowrap text-muted-foreground">
        <span className="hidden sm:inline">{kinds[i.kind] ?? i.kind}</span>
        {phone && (
          <Hint label={phone}>
            <Smartphone className={cn('size-3.5', !(i.pushed && i.pushed !== 'no') && 'opacity-30')} />
          </Hint>
        )}
        <span className="num">{i.created.slice(11)}</span>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------------------------------------ the rules
// A calm list: one line per kind of alert with what it does now, an on/off switch and a phone button. The numbers
// only show when you open a line (one at a time; which one is open is remembered, ui2 "alert_open").
type Val = string | number | boolean

function useRule() {
  const { settings, setPref } = useSettings()
  const q = useQuery({ queryKey: ['alerts', 'rules'], queryFn: () => api.get<AlertList>('/api/alerts?limit=1') })
  const defaults = q.data?.defaults ?? {}
  const group = (settings.prefs.alerts ?? {}) as Record<string, Val | null>
  const get = <T extends Val>(k: string, fallback: T): T => {
    const v = group[k]
    return (v === undefined || v === null ? (defaults[k] ?? fallback) : v) as T
  }
  return { get, set: (k: string, v: Val | null) => setPref('alerts', k, v), ready: !!q.data }
}

function useOpen() {
  const { pref, setPref } = useSettings()
  const open = pref<string>('ui2', 'alert_open', '')
  return { open, toggle: (id: string) => setPref('ui2', 'alert_open', open === id ? '' : id) }
}

/** One line of the list; opens to show its numbers. */
function Line({ id, icon: Icon, title, summary, on, push, children }: {
  id: string; icon: LucideIcon; title: ReactNode; summary: ReactNode; on?: string; push?: string; children?: ReactNode
}) {
  const r = useRule()
  const { open, toggle } = useOpen()
  const isOn = on ? r.get(on, true) : true
  const isPush = push ? r.get(push, false) : false
  const expanded = open === id && !!children
  return (
    <div className="border-b last:border-0">
      <div className="flex items-center gap-3 py-3">
        <button type="button" onClick={() => children && toggle(id)} aria-expanded={expanded}
          className={cn('flex min-w-0 flex-1 items-center gap-3 rounded-md text-left outline-none focus-visible:ring-2 focus-visible:ring-ring/60', !children && 'cursor-default')}>
          <span className={cn('flex size-8 shrink-0 items-center justify-center rounded-lg bg-secondary text-muted-foreground', isOn && 'bg-primary/10 text-primary')}>
            <Icon className="size-4" />
          </span>
          <span className="min-w-0 flex-1">
            <span className={cn('block text-[13.5px] font-medium', !isOn && 'text-muted-foreground')}>{title}</span>
            <span className={cn('block text-[12.5px] text-muted-foreground', !expanded && 'truncate')}>{isOn ? summary : 'Off'}</span>
          </span>
          {children && <ChevronDown className={cn('size-4 shrink-0 text-muted-foreground transition-transform', expanded && 'rotate-180')} />}
        </button>
        {push && (
          <Hint label={isPush ? 'Also to my phone. Click for the app only' : 'Only in the app. Click to also send it to my phone'}>
            <button type="button" aria-pressed={isPush} aria-label="To my phone" disabled={!isOn} onClick={() => r.set(push, !isPush)}
              className={cn('flex size-8 shrink-0 items-center justify-center rounded-md text-muted-foreground/50 outline-none hover:bg-secondary focus-visible:ring-2 focus-visible:ring-ring/60 disabled:opacity-30',
                isPush && 'text-primary')}>
              <Smartphone className="size-4" />
            </button>
          </Hint>
        )}
        {on && <Switch checked={isOn} onCheckedChange={(v) => r.set(on, v)} aria-label={`${typeof title === 'string' ? title : id} on or off`} />}
      </div>
      {expanded && <div className="mb-3 ml-11 flex flex-wrap items-end gap-x-5 gap-y-3 rounded-lg bg-secondary/50 p-3.5">{children}</div>}
    </div>
  )
}

function Num({ k, label, unit = '%', step = 0.5 }: { k: string; label: string; unit?: string; step?: number }) {
  const r = useRule()
  const v = r.get<number>(k, 0)
  const [text, setText] = useState(String(v))
  useEffect(() => setText(String(v)), [v])
  return (
    <label className="flex flex-col gap-1 text-xs text-muted-foreground">
      {label}
      <span className="flex items-center gap-1.5 text-[13px] text-foreground">
        <Input className="w-20 bg-card" type="number" min={0} step={step} value={text}
          onChange={(e) => { setText(e.target.value); if (e.target.value !== '' && Number(e.target.value) > 0) r.set(k, Number(e.target.value)) }} />
        {unit}
      </span>
    </label>
  )
}

function Toggle({ k, label, phone, fallback = false }: { k: string; label: string; phone?: boolean; fallback?: boolean }) {
  const r = useRule()
  return (
    <label className="flex h-8.5 items-center gap-2 text-[13px]">
      <Switch checked={r.get(k, fallback)} onCheckedChange={(v) => r.set(k, v)} />
      {phone && <Smartphone className="size-3.5 text-muted-foreground" />}
      {label}
    </label>
  )
}

function Rules() {
  const r = useRule()
  if (!r.ready) return <Card><Empty>Loading…</Empty></Card>
  const n = (k: string) => r.get<number>(k, 0)
  const sign = (up: string) => (r.get(up, false) ? '±' : '−')
  const wl = [r.get('wl_move', true) && 'moves', r.get('wl_earn', true) && 'results', r.get('wl_an', true) && 'analysts', r.get('wl_div', false) && 'dividends']
    .filter(Boolean).join(', ')
  return (
    <>
      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <Card className="px-5 py-1">
          <Line id="limit" icon={Target} title="Price limits" on="limit_on" push="limit_push"
            summary={`When a stock passes your limit or the buy-below price on your watchlist${r.get('live_on', true) ? ', checked every 5 minutes while markets are open' : ', checked at 07:00'}`}>
            <Toggle k="live_on" label="Check prices every 5 minutes while markets are open (limits and big moves)" fallback />
            <Num k="limit_rearm" label="Again after moving back" />
            <p className="basis-full text-xs text-muted-foreground">
              Set a limit with the bell on any stock or ETF page, or below under Your stocks. Watchlist buy-below prices count too.
              The 5-minute check runs with the earnings watch (Settings → Data), also with the app closed; the computer has to be on.
            </p>
          </Line>
          <Line id="move" icon={MoveVertical} title="Daily move" on="move_on" push="move_push"
            summary={`Shares ${sign('move_up')}${n('move_pct')}%${r.get('move_etf_on', true) ? `, ETFs ${sign('move_up')}${n('move_etf_pct')}%` : ', not for ETFs'} in one day`}>
            <Num k="move_pct" label="Shares: more than" />
            <Toggle k="move_etf_on" label="ETFs too" fallback />
            {r.get('move_etf_on', true) && <Num k="move_etf_pct" label="ETFs: more than" />}
            <Toggle k="move_up" label="Also when it rises" />
            <p className="basis-full text-xs text-muted-foreground">A different % for one stock: the bell on its page, or Your stocks below.</p>
          </Line>
          <Line id="pf" icon={Briefcase} title="Portfolio move" on="pf_on" push="pf_push"
            summary={`Your whole portfolio ${sign('pf_up')}${n('pf_pct')}% in one day`}>
            <Num k="pf_pct" label="More than" />
            <Toggle k="pf_up" label="Also when it rises" />
          </Line>
          <Line id="earn" icon={ChartColumn} title="Results" on="earn_on" push="earn_push"
            summary={`A reminder ${n('earn_days')} day${n('earn_days') === 1 ? '' : 's'} before, and within minutes of the results: revenue and EPS against expectations`}>
            <Num k="earn_days" label="Remind me" unit="days before" step={1} />
            <Toggle k="earn_soon_push" label="Reminder to my phone too" phone />
            {r.get('quiet_on', true) && <Toggle k="earn_quiet" label="Results reach my phone in quiet hours too (US results come around 22:00)" phone fallback />}
          </Line>
          <Line id="div" icon={CircleDollarSign} title="Dividend" on="div_on" push="div_cut_push"
            summary={`A cut of more than ${n('div_pct')}%${r.get('div_up', false) ? ', or a raise' : ''}, against the payment before and a year before`}>
            <Num k="div_pct" label="Change of more than" />
            <Toggle k="div_up" label="Also when it is raised" />
            <Toggle k="div_up_push" label="Raise to my phone too" phone />
          </Line>
          <Line id="an" icon={Search} title="Analysts" on="an_on" push="an_push"
            summary={`EPS estimate ±${n('an_eps')}%, price target ±${n('an_target')}%${r.get('an_rec', true) ? ', a new advice' : ''} within 30 days`}>
            <Num k="an_eps" label="EPS estimate more than" />
            <Num k="an_target" label="Price target more than" />
            <Toggle k="an_rec" label="Advice changes (buy → hold)" fallback />
            <p className="basis-full text-xs text-muted-foreground">To your phone: only big ones (twice the limit, or a new advice). Not for ETFs. Price-target alerts start a month after the first check.</p>
          </Line>
          <Line id="wl" icon={Star} title="Watchlist" on="wl_on"
            summary={`Buy-below prices${wl ? `, and ${wl}` : ''} for the stocks you watch`}>
            <Toggle k="wl_move" label="Daily moves" fallback />
            <Toggle k="wl_earn" label="Results" fallback />
            <Toggle k="wl_an" label="Analysts" fallback />
            <Toggle k="wl_div" label="Dividend changes" />
            <Link to="/watchlist" className="flex h-8.5 items-center text-[13px] text-primary hover:underline">Buy-below prices →</Link>
          </Line>
          <SummaryLine />
        </Card>
        <Phone />
      </div>
      <YourStocks />
    </>
  )
}

// ------------------------------------------------------------------------------------------------ morning summary
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const WEEKDAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

/** The morning summary (stockterminal/summary.py): one phone message with your holdings, the movers and the week ahead. */
function SummaryLine() {
  const r = useRule()
  const toast = useToast()
  const [preview, setPreview] = useState<{ title: string; body: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const daily = r.get('sum_daily', false)
  const weekly = r.get('sum_weekly', false)
  const day = Number(r.get<number>('sum_weekday', 0)) % 7
  const phone = !!r.get<string>('ntfy_topic', '').trim()
  const summary = !daily && !weekly ? 'Off: one message with your holdings, the biggest movers and the week ahead'
    : `${[daily && 'Every weekday morning', weekly && `${daily ? 'a' : 'A'} week overview every ${WEEKDAY_NAMES[day]}`].filter(Boolean).join(', ')}, after the 07:00 check${phone ? '' : ' (set up your phone first)'}`
  const run = async (send: boolean) => {
    setBusy(true)
    try {
      const j = await api.post<{ title: string; body: string; sent: boolean }>('/api/alerts/summary', { weekly: weekly && !daily, send })
      setPreview(j)
      if (j.sent) toast('Summary sent: check your phone.')
    } catch (e) {
      toast(`Could not make it: ${e instanceof Error ? e.message : 'unknown error'}`)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Line id="summary" icon={Sunrise} title="Morning summary" summary={summary}>
      <Toggle k="sum_daily" label="Every weekday morning (Monday: Friday's moves)" phone />
      <Toggle k="sum_weekly" label="A week overview" phone />
      {weekly && <Segmented label="Week overview on" value={String(day)} onChange={(v) => r.set('sum_weekday', Number(v))} options={WEEKDAYS.map((d, i) => ({ value: String(i), label: d }))} />}
      <div className="flex basis-full gap-2">
        <Button size="sm" variant="outline" disabled={busy} onClick={() => void run(false)}>{busy ? <Loader2 className="animate-spin" /> : <Sparkles />} Preview</Button>
        <Button size="sm" variant="outline" disabled={busy || !phone} onClick={() => void run(true)}><Send /> Send one now</Button>
      </div>
      {preview && (
        <div className="basis-full rounded-lg border bg-card p-3 text-[13px]">
          <div className="font-semibold">{preview.title}</div>
          <div className="mt-1 whitespace-pre-line text-muted-foreground">{preview.body}</div>
        </div>
      )}
      <p className="basis-full text-xs text-muted-foreground">
        Your holdings as they are now, with the closing prices of the check: a buy or sell in between does not count as a gain or loss.
        Never in quiet hours; a computer that was off sends it at the next check.
      </p>
    </Line>
  )
}

// ------------------------------------------------------------------------------------------------ phone
function Phone() {
  const r = useRule()
  const toast = useToast()
  const { open, toggle } = useOpen()
  const saved = r.get<string>('ntfy_topic', '')
  const [topic, setTopic] = useState(saved)
  const [sending, setSending] = useState(false)
  const editing = open === 'phone' || !saved
  const make = () => {
    const abc = 'abcdefghijkmnpqrstuvwxyz23456789'
    const t = 'stockterminal-' + Array.from(crypto.getRandomValues(new Uint8Array(8)), (b) => abc[b % abc.length]).join('')
    setTopic(t)
    r.set('ntfy_topic', t)
  }
  const test = async () => {
    setSending(true)
    await new Promise((res) => setTimeout(res, 500)) // the topic is saved 0.4 s after typing
    try {
      await api.post('/api/alerts/test')
      toast('Test message sent: check your phone.')
    } catch (e) {
      toast(`Could not send: ${e instanceof Error ? e.message : 'unknown error'}`)
    } finally {
      setSending(false)
    }
  }
  const step = (n: number, body: ReactNode) => (
    <div className="flex gap-2.5 text-[13px]">
      <span className="num flex size-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-[11px] font-semibold text-primary">{n}</span>
      <div className="min-w-0 flex-1">{body}</div>
    </div>
  )
  const web = topic.trim() ? `https://ntfy.sh/${encodeURIComponent(topic.trim())}` : null
  return (
    <Card className="p-5">
      <H2 side={saved && <Button size="sm" variant="ghost" onClick={() => toggle('phone')}>{editing ? 'Done' : 'Change'}</Button>}>
        <span className="inline-flex items-center gap-1.5"><Smartphone className="size-4" /> Phone</span>
      </H2>
      {saved && !editing ? (
        <div className="flex flex-wrap items-center gap-2.5 text-[13px]">
          <span className="min-w-0 flex-1 text-muted-foreground">
            To the ntfy app, topic <span className="font-medium break-all text-foreground">{saved}</span> · {r.get('quiet_on', true) ? `quiet ${r.get<string>('quiet_from', '22:00')}–${r.get<string>('quiet_to', '07:00')}` : 'no quiet hours'}
          </span>
          <Button size="sm" onClick={test} disabled={sending}>{sending ? <Loader2 className="animate-spin" /> : <Send />} Test</Button>
        </div>
      ) : (
        <div className="flex flex-col gap-3.5">
          <p className="text-[13px] text-muted-foreground">
            Get the alerts you mark with <Smartphone className="inline size-3.5 -translate-y-px" /> as a message on your phone, through the free app ntfy. No account needed.
          </p>
          {step(1, <>Install <b>ntfy</b>: <a className="text-primary hover:underline" href="https://play.google.com/store/apps/details?id=io.heckel.ntfy" target="_blank" rel="noreferrer">Android</a> or <a className="text-primary hover:underline" href="https://apps.apple.com/app/ntfy/id1625396347" target="_blank" rel="noreferrer">iPhone</a>.</>)}
          {step(2, (
            <div className="flex flex-col gap-2">
              <span>Pick a topic name. It works like a password: anyone who knows it can read your messages.</span>
              <div className="flex flex-wrap items-center gap-2">
                <Input className="min-w-48 flex-1" value={topic} placeholder="Topic name" aria-label="Topic name"
                  onChange={(e) => { setTopic(e.target.value); r.set('ntfy_topic', e.target.value.trim()) }} />
                <Button size="sm" onClick={make}><Sparkles /> Make one</Button>
              </div>
            </div>
          ))}
          {step(3, <>In the app, tap <b>+</b> and subscribe to that topic name.{web && <> Or follow it in a browser: <a className="text-primary hover:underline" href={web} target="_blank" rel="noreferrer">open the topic</a>.</>}</>)}
          {step(4, (
            <div className="flex flex-wrap items-center gap-2">
              <span>Send a test.</span>
              <Button size="sm" onClick={test} disabled={sending || !topic.trim()}>{sending ? <Loader2 className="animate-spin" /> : <Send />} Test</Button>
            </div>
          ))}
          <div className="flex flex-wrap items-center gap-2 border-t pt-3 text-[13px] text-muted-foreground">
            <label className="flex h-8.5 items-center gap-2 text-foreground">
              <Switch checked={r.get('quiet_on', true)} onCheckedChange={(v) => r.set('quiet_on', v)} />
              Quiet hours
            </label>
            {r.get('quiet_on', true) && (
              <>
                <Input className="w-32" type="time" aria-label="Quiet from" value={r.get<string>('quiet_from', '22:00')} onChange={(e) => r.set('quiet_from', e.target.value)} />
                to
                <Input className="w-32" type="time" aria-label="Quiet until" value={r.get<string>('quiet_to', '07:00')} onChange={(e) => r.set('quiet_to', e.target.value)} />
              </>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            {r.get('quiet_on', true) ? 'During quiet hours messages wait until the morning' : 'Messages are sent at any time of day'}; at most one message
            per stock per day, except price limits and results.
          </p>
        </div>
      )}
    </Card>
  )
}

// ------------------------------------------------------------------------------------------------ your stocks
const SHOW = [
  { value: 'set', label: 'With your settings' }, { value: 'all', label: 'All' }, { value: 'portfolio', label: 'Holdings' },
  { value: 'watchlist', label: 'Watchlist' }, { value: 'other', label: 'Other' },
]
const SHORT: Record<string, string> = { move: 'moves', earnings: 'results', dividend: 'dividend', analysts: 'analysts', limit: 'price' }

/** Every stock alerts know about in one table: limits, how far the price is from them, own move %, what is skipped.
 *  The bell on each row edits it (the same panel as on the stock's page). */
function YourStocks() {
  const { pref, setPref } = useSettings()
  const show = pref<string>('ui2', 'alert_stocks_show', 'set')
  const [text, setText] = useState('')
  const q = useQuery({ queryKey: ['alert-stocks'], queryFn: () => api.get<{ stocks: StockAlert[] }>('/api/alerts/stocks') })
  const all = q.data?.stocks ?? []
  const custom = (s: StockAlert) => !!(s.below || s.above || s.move || s.off.length || s.on.length || s.mute || s.buy_below)
  const low = text.trim().toLowerCase()
  const rows = all
    .filter((s) => show === 'all' || (show === 'set' ? custom(s) : s.origin === show))
    .filter((s) => !low || `${s.ticker ?? ''} ${s.name}`.toLowerCase().includes(low))
    .map((s) => ({ s, dist: limitDistance(s.price, s.below ?? (s.off.includes('limit') ? null : s.buy_below), s.above) }))
    .sort((a, b) => (a.dist ?? 9) - (b.dist ?? 9) || a.s.name.localeCompare(b.s.name))
  const from = (s: StockAlert) => (s.origin === 'portfolio' ? 'Holding' : s.origin === 'watchlist' ? s.lists.join(', ') : 'Alert only')
  return (
    <Card className="p-5">
      <H2 side={
        <div className="flex flex-wrap items-center gap-2">
          <Input className="h-8 w-44" value={text} onChange={(e) => setText(e.target.value)} placeholder="Find a stock" aria-label="Find a stock" />
          <Segmented label="Which stocks" value={show} onChange={(v) => setPref('ui2', 'alert_stocks_show', v)} options={SHOW} />
        </div>
      }>
        <span className="inline-flex items-center gap-1.5"><Target className="size-4" /> Your stocks</span>
      </H2>
      <p className="mb-3 text-[13px] text-muted-foreground">
        Every price limit and stock setting in one place, nearest limit first. Click the bell to change one. To add a stock that is not listed,
        use the bell on its page (search it with Ctrl K). Hidden holdings get no alerts.
      </p>
      {!q.data ? (
        <Empty>{q.isError ? 'Could not load your stocks.' : 'Loading…'}</Empty>
      ) : !rows.length ? (
        <Empty>{show === 'set' && !low ? 'No price limits or stock settings yet. Use the bell on a stock page, or choose "All" to set one here.' : 'No stocks match.'}</Empty>
      ) : (
        <div className="-mx-5 overflow-x-auto">
          <table className="tbl tight">
            <thead>
              <tr>
                <th>Stock</th>
                <th className="text-left!">From</th>
                <th>Price</th>
                <th>Below</th>
                <th>Above</th>
                <th><Hint label="How far the price still has to move to reach your nearest limit"><span className="cursor-help">To limit</span></Hint></th>
                <th>Daily move</th>
                <th className="text-left!">Not about</th>
                <th aria-label="Change" />
              </tr>
            </thead>
            <tbody>
              {rows.map(({ s, dist }) => {
                const below = s.below ?? (s.buy_below && !s.off.includes('limit') ? s.buy_below : null)
                return (
                  <tr key={s.sid} className={cn(s.mute && 'text-muted-foreground')}>
                    <td>
                      <Link to={s.ticker ? `/${s.etf ? 'etf' : 'stock'}/${s.ticker}` : `/portfolio/${encodeURIComponent(s.t212_ticker)}`}
                        className="flex max-w-[260px] items-center gap-2 hover:text-primary">
                        <span className="w-14 shrink-0 font-semibold">{s.ticker ?? t212Symbol(s.t212_ticker)}</span>
                        <span className="min-w-0 truncate">{s.name}</span>
                      </Link>
                    </td>
                    <td className="max-w-40 truncate text-left! text-muted-foreground">{from(s)}</td>
                    <td>{s.price ? sharePrice(s.price, s.currency) : '–'}</td>
                    <td>
                      {below ? (s.below ? sharePrice(below, s.currency)
                        : <Hint label="Your buy-below price on the watchlist"><span className="cursor-help text-muted-foreground">{sharePrice(below, 'USD')} <Star className="inline size-3 -translate-y-px" /></span></Hint>) : '–'}
                    </td>
                    <td>{s.above ? sharePrice(s.above, s.currency) : '–'}</td>
                    <td className={cn(dist !== null && dist < 0.03 && 'font-semibold text-flag')}>{dist === null ? '–' : dist <= 0 ? <Hint label="The price is past this limit; the alert comes again after it moves back"><span className="cursor-help">passed</span></Hint> : `${(dist * 100).toFixed(1)}%`}</td>
                    <td>{s.move ? `${s.move}%` : <span className="text-muted-foreground">{s.move_rule ? `${s.move_rule}%` : 'off'}</span>}</td>
                    <td className="text-left! text-muted-foreground">
                      {s.mute || s.off.length >= 5 ? <span className="inline-flex items-center gap-1"><BellOff className="size-3.5" /> muted</span>
                        : s.origin === 'other' ? (s.on.length ? `only price${s.on.map((k) => `, ${SHORT[k] ?? k}`).join('')}` : 'only price')
                          : s.off.length ? s.off.map((k) => SHORT[k] ?? k).join(', ') : ''}
                    </td>
                    <td><AlertBell sid={s.sid} etf={s.etf} /></td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

