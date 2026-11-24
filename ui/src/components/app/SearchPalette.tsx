import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Download, Layers, SlidersHorizontal } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from '@/components/ui/command'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { api } from '@/lib/api'
import type { SearchHit } from '@/lib/api'
import { NAV, SEARCH_PAGES } from '@/lib/nav'
import { SETTING_INDEX } from '@/pages/Settings'
import { Logo } from '@/components/app/Logo'

function useDebounced<T>(value: T, ms: number) {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = window.setTimeout(() => setV(value), ms)
    return () => window.clearTimeout(t)
  }, [value, ms])
  return v
}

/** One search for everything: companies and ETFs, pages, and settings. Opens with Ctrl+K or "/". */
export function SearchPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const navigate = useNavigate()
  const [text, setText] = useState('')
  const q = useDebounced(text.trim(), 120)
  const { data: hits = [], isPlaceholderData } = useQuery({
    queryKey: ['find', q],
    queryFn: () => api.get<SearchHit[]>(`/api/find?q=${encodeURIComponent(q)}&limit=8`),
    enabled: open,
    placeholderData: keepPreviousData,
  })
  // The results belong to the text in the box only once the (debounced) search for that exact text is back.
  // Until then they are the previous search's, and Enter must never open one of those (typing "coca" + Enter
  // used to open Microsoft from the search before).
  const fresh = !isPlaceholderData && q === text.trim()
  const [selected, setSelected] = useState('')
  const pendingEnter = useRef(false)
  const moved = useRef(false) // the user picked a row with the arrow keys or the mouse

  // A fresh, empty box every time the search opens.
  useEffect(() => {
    setText('')
    pendingEnter.current = false
    moved.current = false
  }, [open])

  const low = text.trim().toLowerCase()
  const pages = low ? [...NAV, ...SEARCH_PAGES].filter((n) => `${n.label} ${n.words ?? ''}`.toLowerCase().includes(low)) : NAV
  const settings = low.length >= 2 ? SETTING_INDEX.filter((s) => s.text.toLowerCase().includes(low)).slice(0, 5) : []
  const go = (to: string) => {
    pendingEnter.current = false
    onOpenChange(false)
    navigate(to)
  }
  const openHit = (h: SearchHit) => go(h.loaded ? `/${h.etf ? 'etf' : 'stock'}/${h.ticker}` : `/load/${h.ticker}`)
  const first = fresh && hits.length ? `t-${hits[0].ticker}` : pages.length ? `p-${pages[0].to}` : settings.length ? `s-${settings[0].id}` : ''

  // The first result is highlighted as soon as the results are current, so Enter opens it without the arrow keys.
  useEffect(() => {
    setSelected(first)
  }, [first])
  // Enter pressed while the search was still running: act on it as soon as the results arrive.
  useEffect(() => {
    if (fresh && pendingEnter.current) {
      pendingEnter.current = false
      if (hits.length) openHit(hits[0])
      else if (pages.length) go(pages[0].to)
      else if (settings.length) go(`/settings?find=${settings[0].id}`)
    }
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent aria-describedby={undefined}>
        <DialogTitle className="sr-only">Search</DialogTitle>
        <DialogDescription className="sr-only">Search companies, ETFs, pages and settings</DialogDescription>
        <Command
          shouldFilter={false}
          loop
          value={selected}
          onValueChange={setSelected}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown' || e.key === 'ArrowUp') moved.current = true
            // Results still being fetched for what was just typed: wait for them instead of opening an old one.
            if (e.key === 'Enter' && !fresh && text.trim() && !moved.current) {
              e.preventDefault()
              e.stopPropagation()
              pendingEnter.current = true
            }
          }}
        >
          <CommandInput
            value={text}
            onValueChange={(v) => {
              moved.current = false
              setText(v)
            }} placeholder="Search companies, ETFs, pages and settings" />
          <CommandList>
            {fresh ? <CommandEmpty>Nothing found for "{text}".</CommandEmpty> : <CommandEmpty>Searching…</CommandEmpty>}
            {hits.length > 0 && (fresh || !text.trim()) && (
              <CommandGroup heading={low ? 'Companies and ETFs' : 'Your companies and ETFs'}>
                {hits.map((h) => (
                  <CommandItem
                    key={h.ticker}
                    value={`t-${h.ticker}`}
                    onSelect={() => openHit(h)}
                  >
                    {h.loaded ? <Logo t={h.ticker} size={20} keep /> : <span aria-hidden className="size-5 shrink-0" />}
                    <span className="num inline-flex h-6 min-w-12 items-center justify-center rounded bg-secondary px-1.5 text-xs font-semibold">
                      {h.ticker}
                    </span>
                    <span className="min-w-0 flex-1 truncate">{h.name}</span>
                    {h.etf && <Layers aria-label="ETF" />}
                    {!h.loaded && (
                      <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                        <Download className="size-3.5" />
                        Add
                      </span>
                    )}
                  </CommandItem>
                ))}
              </CommandGroup>
            )}
            {pages.length > 0 && (
              <CommandGroup heading="Pages">
                {pages.map((p) => (
                  <CommandItem key={p.to} value={`p-${p.to}`} onSelect={() => go(p.to)}>
                    <p.icon />
                    {p.label}
                  </CommandItem>
                ))}
              </CommandGroup>
            )}
            {settings.length > 0 && (
              <CommandGroup heading="Settings">
                {settings.map((s) => (
                  <CommandItem key={s.id} value={`s-${s.id}`} onSelect={() => go(`/settings?find=${s.id}`)}>
                    <SlidersHorizontal />
                    {s.name}
                    <span className="text-xs text-muted-foreground">{s.section}</span>
                  </CommandItem>
                ))}
              </CommandGroup>
            )}
          </CommandList>
        </Command>
      </DialogContent>
    </Dialog>
  )
}
