import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Plus, Star } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Popover, PopoverAnchor, PopoverContent } from '@/components/ui/popover'
import { Hint } from '@/components/ui/tooltip'
import { api } from '@/lib/api'
import { useToast } from '@/lib/toast'
import { cn } from '@/lib/utils'

export type WatchList = { id: number; name: string; tickers: string[] }
type Lists = { lists: WatchList[] }

export function useWatchlists() {
  return useQuery({ queryKey: ['watchlists'], queryFn: () => api.get<Lists>('/api/watchlists').then((r) => r.lists) })
}

/** The star: no list yet makes "My watchlist" with this ticker; one list adds or removes; more lists lets you pick. */
export function WatchStar({ t, withLabel }: { t: string; withLabel?: boolean }) {
  const qc = useQueryClient()
  const toast = useToast()
  const { data: lists } = useWatchlists()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const on = (lists ?? []).filter((l) => l.tickers.includes(t))

  const send = async (method: 'post' | 'del', path: string, body?: unknown) => {
    const r = method === 'post' ? await api.post<Partial<Lists>>(path, body) : await api.del<Partial<Lists>>(path)
    if (r.lists) qc.setQueryData(['watchlists'], r.lists)
    void qc.invalidateQueries({ queryKey: ['watchlist-rows'] })
  }
  const toggle = (l: WatchList, add: boolean) => (add ? send('post', `/api/watchlists/${l.id}/items`, { ticker: t }) : send('del', `/api/watchlists/${l.id}/items/${encodeURIComponent(t)}`))
  const fail = (e: unknown) => toast('Could not change the watchlist: ' + (e instanceof Error ? e.message : 'unknown error'))

  const click = async () => {
    if (!lists) return
    try {
      if (!lists.length) {
        await send('post', '/api/watchlists', { name: 'My watchlist', ticker: t })
        toast(<>{t} added to <Link to="/watchlist">My watchlist</Link></>)
      } else if (lists.length === 1) {
        const l = lists[0]
        const was = l.tickers.includes(t)
        await toggle(l, !was)
        toast(was ? `${t} removed from ${l.name}` : <>{t} added to <Link to={`/watchlist/${l.id}`}>{l.name}</Link></>)
      } else setOpen(true)
    } catch (e) {
      fail(e)
    }
  }
  const make = async () => {
    if (!name.trim()) return
    try {
      await send('post', '/api/watchlists', { name: name.trim(), ticker: t })
      setName('')
    } catch (e) {
      fail(e)
    }
  }

  const label = on.length ? `On ${on.map((l) => l.name).join(', ')}. Click to change.` : 'Add to your watchlist'
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverAnchor asChild>
        <span>
          <Hint label={label}>
            <Button size={withLabel ? 'default' : 'icon'} aria-label={label} aria-pressed={on.length > 0} onClick={() => void click()}>
              <Star className={cn(on.length > 0 && 'fill-amber-400 text-amber-500')} />
              {withLabel && (on.length ? 'Watching' : 'Watch')}
            </Button>
          </Hint>
        </span>
      </PopoverAnchor>
      <PopoverContent>
        <div className="px-2 pt-1 pb-1.5 text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">{t} on your watchlists</div>
        {(lists ?? []).map((l) => {
          const has = l.tickers.includes(t)
          return (
            <button key={l.id} type="button" role="menuitemcheckbox" aria-checked={has} onClick={() => void toggle(l, !has).catch(fail)} className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] outline-none hover:bg-secondary focus-visible:bg-secondary">
              <span className={cn('flex size-4 items-center justify-center rounded border', has && 'border-primary bg-primary text-primary-foreground')}>{has && <Check className="size-3" />}</span>
              <span className="min-w-0 flex-1 truncate">{l.name}</span>
              <span className="num text-xs text-muted-foreground">{l.tickers.length}</span>
            </button>
          )
        })}
        <div className="mt-1.5 flex gap-1.5 border-t pt-2">
          <Input value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void make()} placeholder="New list…" maxLength={60} spellCheck={false} className="h-8" />
          <Button size="icon" aria-label="Make this list" onClick={() => void make()}>
            <Plus />
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  )
}
