import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { api } from '@/lib/api'

// Mirrors stockterminal/settings.py. Settings live in data/settings.json on the user's PC (never in the
// browser), so they survive restarts and every install keeps its own.
type PrefValue = string | number | boolean | null
export type Settings = {
  theme: 'light' | 'dark' | 'system'
  sec_email: string
  default_period: 'FY' | 'Q' | 'TTM'
  price_range: '1M' | '3M' | '6M' | 'YTD' | '1Y' | '3Y' | '5Y' | '10Y' | 'Max'
  show_extended: boolean
  range_change: 'always' | 'hover'
  compare_years: 'fy' | 'cal'
  page_width: 'full' | 'centered'
  daily_update: boolean
  earnings_watch: boolean
  chart_style: 'line' | 'candles'
  show_logos: boolean
  logo_style: 'plain' | 'dark' | 'soft' | 'round'
  update_check: boolean
  prefs: Record<string, PrefValue | Record<string, PrefValue>>
}

type Ctx = {
  settings: Settings
  /** Save one or more settings. Resolves to an error text when the server refuses the value. */
  update: (changes: Partial<Settings>) => Promise<string | null>
  /** The remembered value of a view choice (a toggle, a tab, a sort order). */
  pref: <T extends PrefValue>(group: string, key: string, fallback: T) => T
  /** Remember a view choice. Every toggle in the app goes through this. */
  setPref: (group: string, key: string, value: PrefValue) => void
  /** A whole pref at once, the way the old screen keeps some ("compare": one text, "charts": name -> address). */
  setPrefGroup: (group: string, value: PrefValue | Record<string, PrefValue>) => void
}

const SettingsContext = createContext<Ctx | null>(null)

/** A view choice could not be saved: say so (the toast at the bottom listens for this event) instead of
 *  silently forgetting it after the next restart. */
function savePrefsFailed() {
  window.dispatchEvent(new CustomEvent('st-toast', { detail: "Couldn't save your view choices. They will be lost after a restart." }))
}

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [failed, setFailed] = useState(false)
  const latest = useRef<Settings | null>(null)
  const timer = useRef<number | undefined>(undefined)

  useEffect(() => {
    api
      .get<Settings>('/api/settings')
      .then((s) => {
        latest.current = s
        setSettings(s)
      })
      .catch(() => setFailed(true))
  }, [])

  const update = useCallback(async (changes: Partial<Settings>) => {
    const before = latest.current
    if (!before) return 'Settings are not loaded yet.'
    latest.current = { ...before, ...changes }
    setSettings(latest.current)
    try {
      const saved = await api.put<Settings>('/api/settings', changes)
      latest.current = { ...saved, prefs: latest.current?.prefs ?? saved.prefs }
      setSettings(latest.current)
      return null
    } catch (e) {
      latest.current = { ...latest.current, ...Object.fromEntries(Object.keys(changes).map((k) => [k, before[k as keyof Settings]])) } as Settings
      setSettings(latest.current)
      return e instanceof Error ? e.message : 'Could not save this setting.'
    }
  }, [])

  const setPref = useCallback((group: string, key: string, value: PrefValue) => {
    const cur = latest.current
    if (!cur) return
    const g = cur.prefs[group]
    const groupObj = g && typeof g === 'object' ? g : {}
    if (groupObj[key] === value) return
    latest.current = { ...cur, prefs: { ...cur.prefs, [group]: { ...groupObj, [key]: value } } }
    setSettings(latest.current)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      if (latest.current) api.put('/api/settings', { prefs: latest.current.prefs }).catch(savePrefsFailed)
    }, 400)
  }, [])

  const setPrefGroup = useCallback((group: string, value: PrefValue | Record<string, PrefValue>) => {
    const cur = latest.current
    if (!cur || JSON.stringify(cur.prefs[group]) === JSON.stringify(value)) return
    latest.current = { ...cur, prefs: { ...cur.prefs, [group]: value } }
    setSettings(latest.current)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      if (latest.current) api.put('/api/settings', { prefs: latest.current.prefs }).catch(savePrefsFailed)
    }, 400)
  }, [])

  const value = useMemo<Ctx | null>(() => {
    if (!settings) return null
    return {
      settings,
      update,
      setPref,
      setPrefGroup,
      pref: (group, key, fallback) => {
        const g = settings.prefs[group]
        const v = g && typeof g === 'object' ? g[key] : undefined
        return (v === undefined || typeof v !== typeof fallback ? fallback : v) as typeof fallback
      },
    }
  }, [settings, update, setPref, setPrefGroup])

  if (failed)
    return (
      <div className="flex h-screen items-center justify-center p-8 text-center text-muted-foreground">
        The app could not read its settings. Close Stock Terminal and start it again.
      </div>
    )
  if (!value) return null
  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>
}

export function useSettings() {
  const ctx = useContext(SettingsContext)
  if (!ctx) throw new Error('useSettings must be used inside SettingsProvider')
  return ctx
}

/** Puts the chosen theme on the page; "system" follows the computer and reacts when it changes. */
export function useApplyTheme(theme: Settings['theme']) {
  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const apply = () => {
      const dark = theme === 'dark' || (theme === 'system' && media.matches)
      document.documentElement.dataset.theme = dark ? 'dark' : 'light'
    }
    apply()
    media.addEventListener('change', apply)
    return () => media.removeEventListener('change', apply)
  }, [theme])
}
