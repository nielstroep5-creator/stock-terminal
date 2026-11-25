import { useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarDays, ChartLine, Loader2, Plus, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, Navigate, useParams } from 'react-router-dom'
import { Card } from '@/components/app/Page'
import { AlertBell } from '@/components/app/AlertBell'
import { WatchStar } from '@/components/app/WatchStar'
import { Button } from '@/components/ui/button'
import { ChangePill } from '@/components/ui/pill'
import { TabLinks } from '@/components/ui/tabs'
import { Hint } from '@/components/ui/tooltip'
import { api } from '@/lib/api'
import { day, money, niceName, pctSigned } from '@/lib/format'
import { useSettings } from '@/lib/settings'
import { cn } from '@/lib/utils'
import { Dividends } from '@/pages/stock/Dividends'
import { Estimates } from '@/pages/stock/Estimates'
import { Financials, Ratios } from '@/pages/stock/Financials'
import { Overview } from '@/pages/stock/Overview'
import { Ownership } from '@/pages/stock/Ownership'
import { ResultsBar } from '@/pages/stock/ResultsBar'
import { Segments } from '@/pages/stock/Segments'
import { Valuation } from '@/pages/stock/Valuation'
import type { CompanyInfo } from '@/pages/stock/types'
import { Logo } from '@/components/app/Logo'
import { ExtendedPrice, etDay, etTime } from '@/components/app/ExtendedPrice'

/** Price-only SEC companies sent to the load page this session (see needs_sec). */
const secTried = new Set<string>()

const TABS = [
  ['overview', 'Overview'],
  ['financials', 'Financials'],
  ['segments', 'Segments'],
  ['ratios', 'Ratios'],
  ['valuation', 'Valuation'],
  ['estimates', 'Estimates'],
  ['dividends', 'Dividends'],
  ['ownership', 'Ownership'],
] as const
type TabKey = (typeof TABS)[number][0]

// opening a company checks once per 5 minutes whether its stored data is stale (prices, analyst data, a new filing)
const autoChecked: Record<string, number> = {}

type UpdateResult = { steps?: Record<string, { ok: boolean; changed?: boolean }>; sec?: { running?: boolean } }

function hasTab(info: CompanyInfo, k: TabKey) {
  if (k === 'overview') return true
  if (k === 'estimates') return !!info.analyst
  if (k === 'dividends') return info.has_dividends
  if (k === 'ownership') return !!info.company?.cik && !info.is_etf
  return info.has_financials && (k !== 'segments' || info.has_segments)
}

export function StockPage() {
  const params = useParams()
  const t = (params.ticker ?? '').toUpperCase()
  const { settings, pref, setPref } = useSettings()
  const qc = useQueryClient()

  const stored = useQuery({ queryKey: ['company', t], queryFn: () => api.get<CompanyInfo>(`/api/company/${t}`) })
  // the live regular-session price (and every ratio recalculated with it), every 60 seconds
  const live = useQuery({
    queryKey: ['company', t, 'live'],
    queryFn: () => api.get<CompanyInfo>(`/api/company/${t}?live=1`),
    enabled: !!stored.data?.has_prices,
    refetchInterval: 60_000,
    retry: false,
  })
  const info = live.data ?? stored.data

  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ text: string; spin?: boolean } | null>(null)
  const current = useRef(t)
  current.current = t

  const watchSec = useCallback(
    (ticker: string) => {
      setNote({ text: 'Updating financials from the SEC…', spin: true })
      const poll = async () => {
        if (current.current !== ticker) return
        const j = await api.get<{ state: string }>(`/api/job/${ticker}`).catch(() => ({ state: 'error' }))
        if (j.state === 'running') return void window.setTimeout(poll, 3000)
        setNote(j.state === 'done' ? null : { text: 'Could not update the financials from the SEC. Showing the stored data.' })
        if (j.state === 'done') qc.invalidateQueries({ queryKey: ['company', ticker] })
      }
      window.setTimeout(poll, 3000)
    },
    [qc],
  )

  /** force: the refresh button (everything now). Otherwise only what is stale. */
  const refresh = useCallback(
    async (ticker: string, force: boolean) => {
      setBusy(true)
      try {
        const r = await api.post<UpdateResult>(`/api/update/${ticker}?force=${force ? 1 : 0}`)
        if (current.current !== ticker) return
        const steps = r.steps ?? {}
        const failed = Object.entries(steps)
          .filter(([, v]) => !v.ok)
          .map(([k]) => k)
        setNote(failed.length ? { text: `Could not update ${failed.join(' and ')} (Yahoo did not answer). Showing the stored data.` } : null)
        const changed = force || Object.values(steps).some((v) => v.changed)
        await qc.invalidateQueries({ queryKey: changed ? ['company', ticker] : ['company', ticker, 'live'] })
        if (r.sec?.running) watchSec(ticker)
      } catch (e) {
        if (force) setNote({ text: 'Refresh failed: ' + (e instanceof Error ? e.message : 'unknown error') })
      } finally {
        setBusy(false)
      }
    },
    [qc, watchSec],
  )

  const hasPrices = !!stored.data?.has_prices
  useEffect(() => {
    setNote(null)
    if (!hasPrices) return
    if (!autoChecked[t] || Date.now() - autoChecked[t] > 5 * 60_000) {
      autoChecked[t] = Date.now()
      void refresh(t, false)
    }
  }, [t, hasPrices, refresh])

  // "Updated 3m ago"
  const [, tick] = useState(0)
  useEffect(() => {
    const id = window.setInterval(() => tick((n) => n + 1), 15_000)
    return () => window.clearInterval(id)
  }, [])

  const tab = params.tab as TabKey | undefined
  useEffect(() => {
    if (tab && TABS.some(([k]) => k === tab)) {
      setPref('ui2', 'stock_tab', tab)
      setPref('ui2', 'stock_tab_ticker', t)
    }
  }, [tab, t, setPref])

  if (stored.isError || (stored.data && !stored.data.is_etf && !stored.data.has_prices && !stored.data.has_financials))
    return (
      <main className="p-6">
        <Card className="flex flex-col items-center gap-3 px-6 py-14 text-center">
          <h2 className="text-base font-semibold">{t} is not in your database</h2>
          <p className="max-w-md text-muted-foreground">Add it to download its data from the SEC and Yahoo Finance (about a minute).</p>
          <Button asChild variant="default">
            <Link to={`/load/${t}`}>
              <Plus /> Add {t}
            </Link>
          </Button>
        </Card>
      </main>
    )
  if (!info) return <main className="p-6 text-muted-foreground">Loading {t}…</main>
  if (info.is_etf) return <Navigate to={`/etf/${t}`} replace />
  if (info.needs_sec && !info.has_financials && !secTried.has(t)) {
    secTried.add(t)               // once per session: if the SEC still can't be reached, show what is stored
    return <Navigate to={`/load/${t}`} replace />
  }

  const tabs = TABS.filter(([k]) => hasTab(info, k))
  if (!tab || !tabs.some(([k]) => k === tab)) {
    // A company opened from search, a list or a link starts on the Overview; the last tab only comes back
    // for the company it was used on (reopening the same company, or the app starting where you left it).
    const last = (pref('ui2', 'stock_tab_ticker', '') === t ? pref('ui2', 'stock_tab', 'overview') : 'overview') as TabKey
    return <Navigate to={`/stock/${t}/${tabs.some(([k]) => k === last) ? last : 'overview'}`} replace />
  }

  const c = info.company
  const name = niceName(c.name) || t
  const chg = info.change
  const abs = info.price && chg !== null && chg !== undefined ? info.price - info.price / (1 + chg) : null
  const reg = info.quote?.regular
  const ext = settings.show_extended ? info.quote?.extended : null
  const when = reg?.time ? (info.price_live ? `Live ${etTime(reg.time)}` : `Close ${etDay(reg.time)}, 4:00 PM ET`) : info.price_date ? `Close ${day(info.price_date)}` : ''
  const mins = live.dataUpdatedAt && live.data?.quote ? Math.floor((Date.now() - live.dataUpdatedAt) / 60000) : null
  const a = info.analyst

  return (
    <main className={cn('flex flex-col gap-5 p-6', settings.page_width === 'centered' && 'mx-auto w-full max-w-[1180px]')}>
      <header className="flex flex-wrap items-start gap-x-4 gap-y-3">
        <Logo
          t={t}
          size={44}
          fallback={<span aria-hidden className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-accent text-[13px] font-bold text-accent-foreground">{t.slice(0, 4)}</span>}
        />
        <div className="min-w-0">
          <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
            <h1 className="text-[22px] leading-7 font-bold tracking-tight">{name}</h1>
            <span className="text-muted-foreground">{[t, c.exchange, c.sector].filter(Boolean).join(' · ')}</span>
            {a?.earnings_upcoming && a.earnings_date && (
              <span className="inline-flex h-6 items-center gap-1 self-center rounded-md bg-secondary px-1.5 text-xs text-muted-foreground">
                <CalendarDays className="size-3.5" />
                Earnings {day(a.earnings_date, false)}
              </span>
            )}
          </div>
          {info.price ? (
            <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-1">
              <span className="num text-[26px] leading-8 font-bold tracking-tight">{money(info.price)}</span>
              <ChangePill value={chg}>
                {abs !== null ? `${abs >= 0 ? '+' : '−'}${Math.abs(abs).toFixed(2)} (${pctSigned(chg).replace(/^[+−]/, '')})` : pctSigned(chg)}
              </ChangePill>
              <span className="text-xs text-muted-foreground">USD · {when}</span>
            </div>
          ) : null}
          {info.price && ext ? <ExtendedPrice ext={ext} /> : null}
        </div>
        <div className="grow" />
        <div className="flex flex-col items-end gap-1.5">
          <div className="flex items-center gap-2">
            {mins !== null && <span className="text-xs text-muted-foreground">{mins < 1 ? 'Updated just now' : mins < 60 ? `Updated ${mins}m ago` : `Updated ${Math.floor(mins / 60)}h ago`}</span>}
            <Hint label="Refresh: live price, prices, valuation and analyst data">
              <Button size="icon" aria-label="Refresh" disabled={busy} onClick={() => void refresh(t, true)}>
                <RefreshCw className={cn(busy && 'animate-spin')} />
              </Button>
            </Hint>
            <WatchStar t={t} withLabel />
            <AlertBell sid={t} />
            <Button asChild>
              <Link to={`/compare?t=${t}`}>
                <ChartLine />
                Compare
              </Link>
            </Button>
          </div>
          {note && (
            <span className="flex max-w-xs items-center gap-1.5 text-right text-xs text-muted-foreground">
              {note.spin && <Loader2 className="size-3.5 animate-spin" />}
              {note.text}
            </span>
          )}
        </div>
      </header>

      <ResultsBar t={t} />
      <TabLinks label="Sections of this company" tabs={tabs.map(([k, l]) => ({ to: `/stock/${t}/${k}`, label: l }))} />

      {tab === 'overview' && <Overview t={t} info={info} />}
      {tab === 'financials' && <Financials t={t} name={name} />}
      {tab === 'ratios' && <Ratios t={t} name={name} />}
      {tab === 'segments' && <Segments t={t} />}
      {tab === 'valuation' && <Valuation t={t} info={info} />}
      {tab === 'estimates' && <Estimates t={t} />}
      {tab === 'dividends' && <Dividends t={t} />}
      {tab === 'ownership' && <Ownership t={t} />}
    </main>
  )
}
