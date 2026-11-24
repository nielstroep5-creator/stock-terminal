import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Bell, Briefcase, CalendarClock, ChartColumn, CircleDollarSign, MoveVertical, Search, Target } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useCallback } from 'react'
import { api } from '@/lib/api'
import { useSettings } from '@/lib/settings'

// Alerts (stockterminal/alerts.py), shared by the Alerts page, the bell in the top bar, the bell on stock / ETF /
// holding pages, Home and the Watchlist. A stock's own settings are prefs group "alerts_stock", "<id>|below",
// "|above", "|move", "|off", "|on", "|mute"; <id> is the ticker (Trading 212's code for a holding without one).

export type AlertItem = {
  id: number; created: string; kind: string; t212_ticker: string | null; ticker: string | null; name: string | null
  title: string; body: string; level: number; push: number; pushed: string | null; read: number; etf?: boolean
}
export type LastCheck = { at: string; new: number; pushed: number; problems: string[]; failed?: boolean } | null
export type AlertList = {
  items: AlertItem[]; total: number; unread: number; newest: number; kinds: Record<string, string>
  last_check: LastCheck; last_live: { at: string; checked: number; new: number } | null; phone: boolean; running: boolean
  defaults?: Record<string, string | number | boolean>
  watched?: { ticker: string; name: string; lists: string[]; buy_below: number | null; held: boolean; price: number | null }[]
}
export type Origin = 'portfolio' | 'watchlist' | 'other' | 'none'
export type StockAlert = {
  sid: string; ticker: string | null; t212_ticker: string; name: string; origin: Origin; etf: boolean; currency: string
  price: number | null; lists: string[]; buy_below: number | null; below: number | null; above: number | null
  move: number | null; move_rule: number; off: string[]; on: string[]; mute: boolean; kinds: Record<string, boolean>
}
export type StockAlertDetail = StockAlert & { recent: AlertItem[]; recent_total: number; rules: AlertRules }
export type AlertRules = { move_pct: number; move_etf_pct: number; move_etf_on: boolean; move_on: boolean; limit_on: boolean; wl_on: boolean }

export const KIND_ICON: Record<string, LucideIcon> = {
  limit: Target, move: MoveVertical, portfolio: Briefcase, earnings_soon: CalendarClock, earnings: ChartColumn,
  dividend: CircleDollarSign, analysts: Search,
}
export const kindIcon = (k: string) => KIND_ICON[k] ?? Bell

/** What a stock's alerts can be about, besides price limits (prefs "|off" skips, "|on" adds for other stocks). */
export const STOCK_KINDS: [string, string][] = [['move', 'Big moves'], ['earnings', 'Results'], ['dividend', 'Dividend'], ['analysts', 'Analysts']]

/** Where an alert takes you: the ETF or stock page, or the holding page for a holding without a stock page. */
export function alertLink(i: AlertItem): string | null {
  if (i.ticker) return `/${i.etf ? 'etf' : 'stock'}/${encodeURIComponent(i.ticker)}`
  if (!i.t212_ticker) return i.kind === 'portfolio' ? '/portfolio' : null
  if (i.t212_ticker.startsWith('wl:') || i.t212_ticker.startsWith('st:')) return `/stock/${encodeURIComponent(i.t212_ticker.slice(3))}`
  return `/portfolio/${encodeURIComponent(i.t212_ticker)}`
}

/** "Today 07:00", "Yesterday 22:10", "Mon 16 Nov 07:00". */
export function when(created: string, withTime = true) {
  const d = new Date(created.replace(' ', 'T'))
  const today = new Date()
  const y = new Date(today)
  y.setDate(today.getDate() - 1)
  const same = (a: Date, b: Date) => a.toDateString() === b.toDateString()
  const dayTxt = same(d, today) ? 'Today' : same(d, y) ? 'Yesterday'
    : d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', ...(d.getFullYear() !== today.getFullYear() ? { year: 'numeric' } : {}) })
  return withTime ? `${dayTxt} ${created.slice(11, 16)}` : dayTxt
}

/** The latest alerts for the bell, Home and the new-alert pop-up: asked every minute, light (no rules). */
export function useLatestAlerts(limit = 8) {
  return useQuery({
    queryKey: ['alerts', 'latest', limit],
    queryFn: () => api.get<AlertList>(`/api/alerts?light=1&limit=${limit}`),
    refetchInterval: 60_000,
    retry: false,
  })
}

/** Mark alerts read (ids, all of one stock, or everything) and refresh every list that shows them. */
export function useMarkRead() {
  const qc = useQueryClient()
  return useCallback(async (what: { ids?: number[]; ticker?: string } = {}) => {
    await api.post('/api/alerts/read', what)
    void qc.invalidateQueries({ queryKey: ['alerts'] })
    void qc.invalidateQueries({ queryKey: ['alert-stock'] })
  }, [qc])
}

type PrefValue = string | number | boolean | null

/** A stock's own alert settings (prefs "alerts_stock") and a setter that also refreshes what the server shows. */
export function useStockPrefs() {
  const { settings, setPref } = useSettings()
  const qc = useQueryClient()
  const per = (settings.prefs.alerts_stock ?? {}) as Record<string, PrefValue>
  const get = (sid: string, what: string): PrefValue => {
    const v = per[`${sid}|${what}`]
    return v === undefined || v === '' ? null : v
  }
  const list = (sid: string, what: 'off' | 'on') => String(get(sid, what) ?? '').split(',').filter(Boolean)
  const set = (sid: string, what: string, value: PrefValue) => {
    setPref('alerts_stock', `${sid}|${what}`, value === '' ? null : value)
    // the server reads the settings file, saved 0.4 s after the last change
    window.setTimeout(() => {
      void qc.invalidateQueries({ queryKey: ['alert-stock'] })
      void qc.invalidateQueries({ queryKey: ['alert-stocks'] })
    }, 700)
  }
  const hasLimit = (sid: string) => !!Number(get(sid, 'below')) || !!Number(get(sid, 'above'))
  return { get, list, set, hasLimit, muted: (sid: string) => get(sid, 'mute') === true }
}

/** Percentage away from the nearest limit: 0.04 = 4% before it is reached. null without a limit or price. */
export function limitDistance(price: number | null, below: number | null, above: number | null) {
  if (!price) return null
  const d = [below ? price / below - 1 : null, above ? above / price - 1 : null].filter((x): x is number => x !== null)
  return d.length ? Math.min(...d) : null
}
