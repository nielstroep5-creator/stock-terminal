import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Segmented } from '@/components/ui/segmented'
import { api } from '@/lib/api'
import { useSettings } from '@/lib/settings'

// Shared by the Portfolio page, its tabs and one holding. Amounts come in the account currency (usually EUR);
// the € / $ switch converts them at today's rate. The switch is the same choice as in the old screen (state.pfCur).

export type N = number | null | undefined
export type Fx = { rate: N; source?: string | null; pair?: string | null }
export type Position = {
  t212_ticker: string; ticker: string | null; isin: string | null; name: string | null; currency: string | null
  quantity: number; avg_price: N; current_price: N; value: N; cost: N; pl: N; pl_pct: N; fx_impact: N; dividends: N
  opened_at?: string; link: 'etf' | 'stock' | null; weight: N; today_pct?: N; yahoo?: string | null
}
export type Totals = {
  total_value: N; invested: number; cost: number; unrealized_pl: number; unrealized_pct: N; fx_impact: number; realized_pl: N
  dividends: number; cash: N; deposits: N; withdrawals: N; interest: N; today_pl: N; today_pct: N; today_count: number
}
export type Portfolio = {
  connected: boolean; source?: 'own' | 't212' | null; synced_at?: string | null; live?: boolean; errors?: string[]; as_of?: string | null
  account_currency: string; other_currency: string | null; positions: Position[]; totals: Totals; fx: Fx
}

export const CUR_SYM: Record<string, string> = { EUR: '€', USD: '$', GBP: '£' }
const has = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

/** €1,234.56 (any currency; a code the browser does not know is written after the number). */
export function cash(v: N, cur: string | null | undefined, digits?: number): string {
  if (!has(v)) return '–'
  try {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: cur || 'EUR', ...(digits !== undefined ? { minimumFractionDigits: digits, maximumFractionDigits: digits } : {}) }).format(v)
  } catch {
    return `${v.toLocaleString('en-US', { maximumFractionDigits: 2 })} ${cur}`
  }
}
/** A price per share: London quotes in pence (GBX) are shown in pounds. */
export const sharePrice = (v: N, cur: string | null | undefined) => (cur === 'GBX' || cur === 'GBp' ? cash(has(v) ? v / 100 : v, 'GBP') : cash(v, cur))
/** Trading 212 code -> symbol: AAPL_US_EQ -> AAPL, ASMLa_EQ -> ASML (the small letter is the exchange). */
export const t212Symbol = (t: string | null | undefined) => (t || '').replace(/_US_EQ$/, '').replace(/_EQ$/, '').replace(/[a-z]+$/, '')
export const qty = (v: N) => (has(v) ? v.toLocaleString('en-US', { maximumFractionDigits: 4 }) : '–')
/** "+1.23%", for money results (two decimals, a real minus sign). */
export const pctTxt = (v: N) => (has(v) ? `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v * 100).toFixed(2)}%` : '–')

/** The currency the amounts are shown in, and formatters that convert to it. */
export function useMoney(acc: string | undefined, other: string | null | undefined, rate: N) {
  const { pref, setPref } = useSettings()
  const want = pref<string>('state', 'pfCur', '')
  const cur = other && want === other && has(rate) ? other : acc || 'EUR'
  const r = cur === acc ? 1 : (rate as number)
  const m = (v: N, digits?: number) => (has(v) ? cash(v * r, cur, digits) : '–')
  const ms = (v: N) => (has(v) ? (v > 0 ? '+' : v < 0 ? '−' : '') + cash(Math.abs(v * r), cur) : '–')
  return { cur, rate: r, m, ms, setCur: (c: string) => setPref('state', 'pfCur', c) }
}

/** The € / $ switch, shown only when there is another currency to switch to. */
export function CurrencySwitch({ acc, other, rate }: { acc?: string; other?: string | null; rate: N }) {
  const { cur, setCur } = useMoney(acc, other, rate)
  if (!acc || !other || !has(rate)) return null
  return <Segmented label="Currency" value={cur} onChange={setCur} options={[acc, other].map((c) => ({ value: c, label: `${CUR_SYM[c] ?? ''} ${c}`.trim() }))} />
}

/** The portfolio: the last sync right away, then live from Trading 212 (or Yahoo, for your own trades) every minute while the page is open. */
export function usePortfolio() {
  const stored = useQuery({ queryKey: ['portfolio', 'stored'], queryFn: () => api.get<Portfolio>('/api/portfolio') })
  const live = useQuery({
    queryKey: ['portfolio', 'live'],
    queryFn: () => api.get<Portfolio>('/api/portfolio?live=1'),
    enabled: !!stored.data?.connected || stored.data?.source === 'own',
    refetchInterval: 60_000,
    retry: false,
  })
  const data = live.data?.positions ? live.data : stored.data
  return { data, loading: stored.isLoading, refreshing: live.isFetching, refresh: () => void live.refetch() }
}

/** Stored first, then fetched again with refresh=1 (Yahoo, SEC) to fill in what is missing. */
export function useStoredThenFresh<T>(key: string, url: string) {
  const stored = useQuery({ queryKey: ['pf', key, 'stored'], queryFn: () => api.get<T>(url) })
  const fresh = useQuery({ queryKey: ['pf', key, 'fresh'], queryFn: () => api.get<T>(url + (url.includes('?') ? '&' : '?') + 'refresh=1'), retry: false, staleTime: 10 * 60_000 })
  return { data: fresh.data ?? stored.data, loading: stored.isLoading, updating: fresh.isFetching }
}

type SyncState = { running?: boolean; step?: string; last_sync?: string | null; error?: string | null; last_error?: string | null; totals?: Record<string, number> }

/** Connected, but the trade history is not in the database yet: sync it from here (never "Connect" again). */
export function SyncHistory({ what }: { what: string }) {
  const qc = useQueryClient()
  const [started, setStarted] = useState(false)
  const sync = useQuery({
    queryKey: ['t212', 'sync'],
    queryFn: () => api.get<SyncState>('/api/t212/sync'),
    refetchInterval: (q) => (q.state.data?.running ? 1500 : false),
  })
  const s = sync.data
  const wasRunning = useRef(false)
  useEffect(() => {
    if (wasRunning.current && s && !s.running) {
      for (const k of ['portfolio', 'pf', 'tax', 'pf-history']) void qc.invalidateQueries({ queryKey: [k] })
    }
    wasRunning.current = !!s?.running
  }, [s, qc])
  const start = async () => {
    setStarted(true)
    await api.post('/api/t212/sync').catch(() => {})
    void sync.refetch()
  }
  const trades = s?.totals?.pf_trades
  return (
    <span className="flex flex-col items-center gap-2">
      <span>
        Trading 212 is connected, but your trade history is not in the app yet, so {what} can't be worked out.
        {s?.last_sync ? ` Last sync: ${s.last_sync}${trades !== undefined ? ` (${trades} orders)` : ''}.` : ' It has not been synced yet.'}
      </span>
      {s?.running ? (
        <span className="text-muted-foreground">Syncing… the first time the whole history is fetched, which can take a few minutes.</span>
      ) : (
        <Button variant="default" onClick={() => void start()}>{started ? 'Sync again' : 'Sync now'}</Button>
      )}
      {(s?.error || s?.last_error) && <span className="text-down">{s.error || s.last_error}</span>}
    </span>
  )
}
