import { Bell, Briefcase, CalendarDays, ChartLine, HeartPulse, Home, Layers, ListFilter, Receipt, Star } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

export type NavItem = { to: string; label: string; icon: LucideIcon; /** other paths that light up this item */ also?: string[]; /** shown indented under the item above */ sub?: boolean; /** more words the search finds it by */ words?: string }

// The main pages, in the order of the sidebar. Also offered in the search palette.
export const NAV: NavItem[] = [
  { to: '/', label: 'Home', icon: Home },
  { to: '/portfolio', label: 'Portfolio', icon: Briefcase },
  { to: '/tax', label: 'Tax', icon: Receipt, sub: true },
  { to: '/watchlist', label: 'Watchlist', icon: Star },
  { to: '/earnings', label: 'Earnings', icon: CalendarDays, sub: true, words: 'earnings calendar results dates surprises beat miss estimates reporting' },
  // the Screener is also the list of all companies (the old Companies page)
  { to: '/screener', label: 'Screener', icon: ListFilter, also: ['/stock', '/load', '/companies'] },
  { to: '/data-health', label: 'Data health', icon: HeartPulse, sub: true },
  { to: '/etfs', label: 'ETFs', icon: Layers, also: ['/etf'] },
  { to: '/compare', label: 'Compare', icon: ChartLine },
]

// Pages that are not in the sidebar but that the search palette finds (the bell in the top bar opens Alerts).
export const SEARCH_PAGES: NavItem[] = [
  { to: '/alerts/list', label: 'Alerts', icon: Bell, words: 'notifications bell' },
  { to: '/alerts/rules', label: 'Alert rules and price limits', icon: Bell, words: 'price limit quiet hours phone ntfy daily move' },
]

export function isActive(item: NavItem, pathname: string) {
  if (item.to === '/') return pathname === '/'
  return [item.to, ...(item.also ?? [])].some((p) => pathname === p || pathname.startsWith(p + '/'))
}
