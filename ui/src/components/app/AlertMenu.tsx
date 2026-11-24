import { Bell, CheckCheck, SlidersHorizontal } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Popover, PopoverAnchor, PopoverContent } from '@/components/ui/popover'
import { Hint } from '@/components/ui/tooltip'
import { alertLink, kindIcon, useLatestAlerts, useMarkRead, when } from '@/lib/alerts'
import type { AlertItem } from '@/lib/alerts'
import { useToast } from '@/lib/toast'
import { cn } from '@/lib/utils'

/** One alert in a short list (the bell's menu, Home): icon in the up / down colour, ticker, title, when. */
export function AlertRow({ i, onOpen, compact }: { i: AlertItem; onOpen: (i: AlertItem) => void; compact?: boolean }) {
  const Icon = kindIcon(i.kind)
  return (
    <button type="button" onClick={() => onOpen(i)}
      className="flex w-full items-start gap-2.5 rounded-md px-2 py-2 text-left outline-none hover:bg-secondary focus-visible:bg-secondary">
      <span className={cn('mt-px flex size-6 shrink-0 items-center justify-center rounded-md bg-secondary text-muted-foreground',
        i.level > 0 && 'bg-up-bg text-up', i.level < 0 && 'bg-down-bg text-down')}>
        <Icon className="size-3.5" />
      </span>
      <span className="min-w-0 flex-1">
        <span className={cn('block text-[13px] leading-snug', !i.read && 'font-semibold', compact && 'truncate')}>{i.title}</span>
        <span className="num block text-[11.5px] text-muted-foreground">{i.ticker ? `${i.ticker} · ` : ''}{when(i.created)}</span>
      </span>
      {!i.read && <span aria-label="unread" className="mt-2 size-1.5 shrink-0 rounded-full bg-primary" />}
    </button>
  )
}

/** The bell in the top bar: the unread count, a menu with the latest alerts, and a pop-up when a new one
 *  comes in while the app is open (asked every minute). */
export function AlertMenu() {
  const navigate = useNavigate()
  const toast = useToast()
  const markRead = useMarkRead()
  const [open, setOpen] = useState(false)
  const q = useLatestAlerts(8)
  const d = q.data
  const unread = d?.unread ?? 0

  const seen = useRef<number | null>(null)
  useEffect(() => {
    if (!d) return
    if (seen.current !== null && d.newest > seen.current) {
      const fresh = d.items.filter((i) => i.id > (seen.current ?? 0) && !i.read)
      if (fresh.length === 1) {
        const to = alertLink(fresh[0])
        toast(<span className="inline-flex items-center gap-2"><Bell className="size-3.5" /> {fresh[0].title}{to && <Link to={to}>Open</Link>}</span>)
      } else if (fresh.length > 1) {
        toast(<span className="inline-flex items-center gap-2"><Bell className="size-3.5" /> {fresh.length} new alerts: {fresh[0].title}, … <Link to="/alerts/list">See them</Link></span>)
      }
    }
    seen.current = d.newest
  }, [d, toast])

  const go = (i: AlertItem) => {
    setOpen(false)
    if (!i.read) void markRead({ ids: [i.id] })
    navigate(alertLink(i) ?? '/alerts/list')
  }
  const label = unread ? `Alerts: ${unread} unread` : 'Alerts'
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverAnchor asChild>
        <span>
          <Hint label={label}>
            <Button variant="ghost" size="icon" aria-label={unread ? `Alerts, ${unread} unread` : 'Alerts'} aria-expanded={open} className="relative" onClick={() => setOpen((o) => !o)}>
              <Bell />
              {unread > 0 && (
                <span className="absolute -top-0.5 -right-0.5 min-w-4 rounded-full bg-down px-1 text-center text-[10px] leading-4 font-bold text-white">
                  {unread > 99 ? '99+' : unread}
                </span>
              )}
            </Button>
          </Hint>
        </span>
      </PopoverAnchor>
      <PopoverContent className="w-[380px] p-0" onOpenAutoFocus={(e) => e.preventDefault()}>
        <div className="flex items-center gap-2 border-b px-4 py-2.5">
          <span className="text-[13.5px] font-semibold">Alerts</span>
          {unread > 0 && <span className="text-xs text-muted-foreground">{unread} unread</span>}
          <div className="grow" />
          <Hint label="Mark all read">
            <Button size="icon" variant="ghost" aria-label="Mark all read" disabled={!unread} onClick={() => void markRead()}><CheckCheck /></Button>
          </Hint>
          <Hint label="Alert rules and phone">
            <Button size="icon" variant="ghost" aria-label="Alert rules" onClick={() => { setOpen(false); navigate('/alerts/rules') }}><SlidersHorizontal /></Button>
          </Hint>
        </div>
        <div className="max-h-[60vh] overflow-y-auto p-1.5">
          {!d ? (
            <p className="p-3 text-[13px] text-muted-foreground">{q.isError ? 'Could not load the alerts.' : 'Loading…'}</p>
          ) : !d.items.length ? (
            <p className="p-3 text-[13px] text-muted-foreground">
              No alerts yet. Set a price limit with the bell on a stock's page, or choose what to hear about under <Link className="text-primary hover:underline" to="/alerts/rules" onClick={() => setOpen(false)}>Rules</Link>.
            </p>
          ) : (
            d.items.map((i) => <AlertRow key={i.id} i={i} onOpen={go} />)
          )}
        </div>
        <Link to="/alerts/list" onClick={() => setOpen(false)} className="block border-t px-4 py-2.5 text-center text-[13px] font-medium text-primary hover:bg-secondary">
          All alerts{d && d.total > d.items.length ? ` (${d.total})` : ''}
        </Link>
      </PopoverContent>
    </Popover>
  )
}
