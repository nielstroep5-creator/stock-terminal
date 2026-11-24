import { ChevronLeft, ChevronRight, Keyboard, Monitor, Moon, Search, Sun, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate, useNavigationType } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { Hint } from '@/components/ui/tooltip'
import { useSettings } from '@/lib/settings'
import type { Settings } from '@/lib/settings'
import { AlertMenu } from '@/components/app/AlertMenu'

const KEYS: [string[], string][] = [
  [['Ctrl', 'K'], 'Search companies, ETFs, pages and settings'],
  [['/'], 'Search too'],
  [['Alt', '←'], 'Back (the mouse back button works too)'],
  [['Alt', '→'], 'Forward'],
  [['F'], 'Full-screen chart (Compare)'],
  [['Esc'], 'Close a menu, a dialog or full screen'],
  [['↑', '↓', 'Enter'], 'Pick a search result'],
  [['?'], 'Show this list'],
]

const NEXT_THEME: Record<Settings['theme'], Settings['theme']> = { light: 'dark', dark: 'system', system: 'light' }
const THEME_LABEL: Record<Settings['theme'], string> = {
  light: 'Light theme. Click for dark',
  dark: 'Dark theme. Click to follow your computer',
  system: 'Theme follows your computer. Click for light',
}

/** Where we are in this window's history, so Back and Forward can be greyed out at the ends. */
function useHistoryEnds() {
  const location = useLocation()
  const type = useNavigationType()
  const idx = (window.history.state?.idx as number | undefined) ?? 0
  const max = useRef(idx)
  if (type === 'PUSH' || idx > max.current) max.current = idx
  void location
  return { canBack: idx > 0, canForward: idx < max.current }
}

export function Topbar({ onSearch }: { onSearch: () => void }) {
  const navigate = useNavigate()
  const { settings, update } = useSettings()
  const { canBack, canForward } = useHistoryEnds()
  const ThemeIcon = settings.theme === 'light' ? Sun : settings.theme === 'dark' ? Moon : Monitor

  const [keysOpen, setKeysOpen] = useState(false)
  const ends = useRef({ canBack, canForward })
  ends.current = { canBack, canForward }

  useEffect(() => {
    const back = () => ends.current.canBack && navigate(-1)
    const fwd = () => ends.current.canForward && navigate(1)
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null
      const typing = !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable)
      if ((e.key === 'k' && (e.ctrlKey || e.metaKey)) || (e.key === '/' && !typing)) {
        e.preventDefault()
        onSearch()
      } else if (e.key === '?' && !typing) {
        e.preventDefault()
        setKeysOpen((o) => !o)
      } else if (e.altKey && !e.ctrlKey && !e.shiftKey && !e.metaKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
        e.preventDefault()
        if (e.key === 'ArrowLeft') back()
        else fwd()
      }
    }
    // mouse side buttons (3 = back, 4 = forward); the browser's own handling is blocked so a click never moves twice
    const onUp = (e: MouseEvent) => {
      if (e.button !== 3 && e.button !== 4) return
      e.preventDefault()
      if (e.button === 3) back()
      else fwd()
    }
    const onDown = (e: MouseEvent) => (e.button === 3 || e.button === 4) && e.preventDefault()
    window.addEventListener('keydown', onKey)
    window.addEventListener('mouseup', onUp)
    window.addEventListener('mousedown', onDown)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('mouseup', onUp)
      window.removeEventListener('mousedown', onDown)
    }
  }, [onSearch, navigate])

  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-1.5 border-b bg-background/90 px-6 backdrop-blur">
      <Hint label="Back (Alt + ←)">
        <Button variant="ghost" size="icon" aria-label="Back" disabled={!canBack} onClick={() => navigate(-1)}>
          <ChevronLeft />
        </Button>
      </Hint>
      <Hint label="Forward (Alt + →)">
        <Button variant="ghost" size="icon" aria-label="Forward" disabled={!canForward} onClick={() => navigate(1)}>
          <ChevronRight />
        </Button>
      </Hint>
      <button
        type="button"
        onClick={onSearch}
        className="ml-1.5 flex h-9 w-full max-w-[520px] items-center gap-2 rounded-lg border bg-panel px-3 text-left text-muted-foreground transition-colors outline-none hover:border-ring/50 focus-visible:ring-2 focus-visible:ring-ring/60"
      >
        <Search className="size-4 shrink-0" />
        <span className="min-w-0 flex-1 truncate">Search companies, ETFs, pages and settings</span>
        <kbd className="rounded border bg-card px-1.5 py-px font-sans text-[11px] whitespace-nowrap">Ctrl K</kbd>
      </button>
      <div className="grow" />
      <AlertMenu />
      <Hint label="Keyboard shortcuts (?)">
        <Button variant="ghost" size="icon" aria-label="Keyboard shortcuts" onClick={() => setKeysOpen(true)}>
          <Keyboard />
        </Button>
      </Hint>
      <Hint label={THEME_LABEL[settings.theme]}>
        <Button variant="ghost" size="icon" aria-label={THEME_LABEL[settings.theme]} onClick={() => update({ theme: NEXT_THEME[settings.theme] })}>
          <ThemeIcon />
        </Button>
      </Hint>
      <Dialog open={keysOpen} onOpenChange={setKeysOpen}>
        <DialogContent className="max-w-md p-5" onOpenAutoFocus={(e) => e.preventDefault()}>
          <div className="mb-3 flex items-center">
            <DialogTitle className="text-[15px] font-semibold">Keyboard shortcuts</DialogTitle>
            <div className="grow" />
            <Button variant="ghost" size="icon" aria-label="Close" onClick={() => setKeysOpen(false)}><X /></Button>
          </div>
          <DialogDescription className="sr-only">The keys that work everywhere in the app.</DialogDescription>
          <div className="flex flex-col">
            {KEYS.map(([keys, what]) => (
              <div key={what} className="flex items-center justify-between gap-4 border-b py-2 text-[13px] last:border-0">
                <span>{what}</span>
                <span className="flex shrink-0 gap-1">{keys.map((k) => <kbd key={k} className="min-w-6 rounded border bg-panel px-1.5 py-0.5 text-center font-sans text-xs">{k}</kbd>)}</span>
              </div>
            ))}
          </div>
        </DialogContent>
      </Dialog>
    </header>
  )
}
