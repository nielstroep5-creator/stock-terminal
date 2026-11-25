import { useState, useSyncExternalStore } from 'react'
import type { ReactNode } from 'react'
import { useSettings } from '@/lib/settings'
import { cn } from '@/lib/utils'

// One watcher of the page theme for every logo on the screen (the Screener can show hundreds).
function subscribe(cb: () => void) {
  const o = new MutationObserver(cb)
  o.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
  return () => o.disconnect()
}
const theme = () => (document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light')

// Tickers without a logo, so a list that draws again doesn't ask the server again.
const none = new Set<string>()

/**
 * A company's or ETF's logo (stockterminal/logos.py, downloaded once into data/logos). Shown next to the ticker,
 * never instead of it: without a logo nothing is drawn and the ticker stands on its own.
 * `keep`: in a list, leave the empty space so the tickers still line up.
 */
export function Logo({ t, size = 20, keep = false, fallback, className }: {
  t: string | null | undefined
  size?: number
  keep?: boolean
  /** drawn when there is no logo (the stock page keeps its ticker badge) */
  fallback?: ReactNode
  className?: string
}) {
  const { settings } = useSettings()
  const bg = useSyncExternalStore(subscribe, theme)
  const [, redraw] = useState(0)
  const blank = fallback ?? (keep ? <span aria-hidden className={cn('shrink-0', className)} style={{ width: size, height: size }} /> : null)
  if (!t || settings.show_logos === false) return fallback ?? null
  const key = t.toUpperCase()
  if (none.has(key)) return blank
  const style = settings.logo_style ?? 'dark'
  // the dark tile always gets the version made for a dark background; the others follow the page
  const src = `/api/logo/${encodeURIComponent(key)}?bg=${style === 'dark' ? 'dark' : bg}`
  const img = (
    <img
      src={src}
      alt=""
      loading="lazy"
      decoding="async"
      draggable={false}
      className="size-full object-contain"
      onLoad={(e) => {
        if (e.currentTarget.naturalWidth <= 1) {
          none.add(key) // the server's "no logo" answer is an empty 1x1 picture
          redraw((n) => n + 1)
        }
      }}
      onError={() => {
        none.add(key)
        redraw((n) => n + 1)
      }}
    />
  )
  if (style === 'plain') return <span className={cn('inline-flex shrink-0', className)} style={{ width: size, height: size }}>{img}</span>
  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center justify-center overflow-hidden',
        style === 'dark' ? 'bg-logo-tile ring-1 ring-white/10 ring-inset' : 'bg-secondary ring-1 ring-foreground/6 ring-inset',
        style === 'round' ? 'rounded-full' : 'rounded-[22%]',
        className,
      )}
      style={{ width: size, height: size, padding: Math.max(2, Math.round(size * 0.16)) }}
    >
      {img}
    </span>
  )
}
