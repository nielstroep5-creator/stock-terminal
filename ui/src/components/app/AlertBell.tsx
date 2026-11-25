import { useQuery } from '@tanstack/react-query'
import { Bell, BellOff, BellRing, Check, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Popover, PopoverAnchor, PopoverContent } from '@/components/ui/popover'
import { Switch } from '@/components/ui/switch'
import { Hint } from '@/components/ui/tooltip'
import { STOCK_KINDS, alertLink, kindIcon, limitDistance, useMarkRead, useStockPrefs, when } from '@/lib/alerts'
import type { StockAlertDetail } from '@/lib/alerts'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { sharePrice } from '@/pages/portfolio/common'

/** A number field that saves while you type (empty = no value) and follows changes made elsewhere. */
function NumField({ value, onSave, label, placeholder, className }: {
  value: number | null; onSave: (v: number | null) => void; label: string; placeholder?: string; className?: string
}) {
  const [text, setText] = useState(value ? String(value) : '')
  useEffect(() => {
    setText((t) => (Number(t) === (value ?? 0) || (!t && !value) ? t : value ? String(value) : ''))
  }, [value])
  return (
    <Input className={cn('h-8 w-[5.5rem] bg-card', className)} type="number" min={0} step="any" inputMode="decimal" aria-label={label}
      value={text} placeholder={placeholder}
      onChange={(e) => {
        setText(e.target.value)
        const n = Number(e.target.value)
        onSave(e.target.value.trim() === '' || !(n > 0) ? null : n)
      }} />
  )
}

/** The bell next to the star on a stock, ETF or holding page: this stock's own alerts.
 *  sid = the ticker, or Trading 212's code for a holding without one. */
export function AlertBell({ sid, withLabel, etf }: { sid: string; withLabel?: boolean; etf?: boolean }) {
  const what = etf ? 'ETF' : 'stock'
  const [open, setOpen] = useState(false)
  const sp = useStockPrefs()
  const limit = sp.hasLimit(sid)
  const muted = sp.muted(sid)
  const Icon = muted ? BellOff : limit ? BellRing : Bell
  const label = muted ? `Alerts for this ${what} are muted` : limit ? `Your price limits and alerts for this ${what}` : `Set an alert for this ${what}`
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverAnchor asChild>
        <span>
          <Hint label={label}>
            <Button size={withLabel ? 'default' : 'icon'} aria-label={label} aria-pressed={limit} onClick={() => setOpen((o) => !o)}>
              <Icon className={cn(limit && !muted && 'text-primary')} />
              {withLabel && (limit ? <LimitText sid={sid} /> : 'Alert')}
            </Button>
          </Hint>
        </span>
      </PopoverAnchor>
      <PopoverContent className="w-[360px] p-0" onOpenAutoFocus={(e) => e.preventDefault()}>
        {open && <Panel sid={sid} close={() => setOpen(false)} />}
      </PopoverContent>
    </Popover>
  )
}

/** "< 175 · > 200" on the bell button. */
function LimitText({ sid }: { sid: string }) {
  const sp = useStockPrefs()
  const b = Number(sp.get(sid, 'below')) || null
  const a = Number(sp.get(sid, 'above')) || null
  return <span className="num">{[b && `< ${b.toLocaleString('en-US')}`, a && `> ${a.toLocaleString('en-US')}`].filter(Boolean).join(' · ')}</span>
}

function Panel({ sid, close }: { sid: string; close: () => void }) {
  const sp = useStockPrefs()
  const navigate = useNavigate()
  const markRead = useMarkRead()
  const q = useQuery({ queryKey: ['alert-stock', sid], queryFn: () => api.get<StockAlertDetail>(`/api/alerts/stock/${encodeURIComponent(sid)}`) })
  const d = q.data
  if (!d) return <p className="p-4 text-[13px] text-muted-foreground">{q.isError ? 'Could not load the alerts.' : 'Loading…'}</p>
  const what = d.etf ? 'ETF' : 'stock'

  const cur = d.currency
  const below = Number(sp.get(sid, 'below')) || null
  const above = Number(sp.get(sid, 'above')) || null
  const muted = sp.muted(sid)
  const other = d.origin === 'other' || d.origin === 'none'
  const off = sp.list(sid, 'off')
  const on = sp.list(sid, 'on')
  const kindOn = (k: string) => (other ? on.includes(k) : !off.includes(k))
  const toggleKind = (k: string) => {
    if (other) sp.set(sid, 'on', (on.includes(k) ? on.filter((x) => x !== k) : [...on, k]).join(',') || null)
    else sp.set(sid, 'off', (off.includes(k) ? off.filter((x) => x !== k) : [...off, k]).join(',') || null)
  }
  const dist = limitDistance(d.price, below, above)
  const unit = cur === 'GBX' || cur === 'GBp' ? 'pence' : cur
  const where = d.origin === 'portfolio' ? `You hold it${d.lists.length ? ` and watch it on ${d.lists.join(', ')}` : ''}.`
    : d.origin === 'watchlist' ? `On your watchlist ${d.lists.join(', ')}.`
      : 'Not in your portfolio or watchlists: you get the price limits below and what you tick.'
  const clear = () => {
    for (const k of ['below', 'above', 'move', 'off', 'on', 'mute']) sp.set(sid, k, null)
  }
  const rule = d.etf && !d.rules.move_etf_on ? 0 : d.move_rule

  return (
    <div className="flex flex-col text-[13px]">
      <div className="flex items-baseline gap-2 border-b px-4 pt-3 pb-2.5">
        <span className="min-w-0 flex-1 truncate font-semibold">Alerts for {d.name}</span>
        {d.price ? <span className="num text-xs text-muted-foreground">now {sharePrice(d.price, cur)}</span> : null}
      </div>
      <div className={cn('flex flex-col gap-3 px-4 py-3', muted && 'opacity-50')}>
        <p className="text-xs text-muted-foreground">{where}</p>
        <div>
          <div className="mb-1.5 font-medium">Price limit</div>
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            below <NumField value={below} label="Alert below" placeholder={d.buy_below ? String(d.buy_below) : ''} onSave={(v) => sp.set(sid, 'below', v)} />
            above <NumField value={above} label="Alert above" onSave={(v) => sp.set(sid, 'above', v)} />
            <span>{unit}</span>
          </div>
          <p className="mt-1.5 text-xs text-muted-foreground">
            {dist !== null ? `${(dist * 100).toFixed(1)}% away from your nearest limit. ` : ''}
            {d.buy_below && !below ? `Your buy-below price ${sharePrice(d.buy_below, 'USD')} on the watchlist also alerts you. ` : ''}
            Checked every 5 minutes while the market is open.
          </p>
        </div>
        <div>
          <div className="mb-1.5 font-medium">Also tell me about</div>
          <div className="flex flex-wrap gap-1.5">
            {STOCK_KINDS.filter(([k]) => !(d.etf && (k === 'analysts' || k === 'insider'))).map(([k, label]) => {
              const Icon = kindIcon(k === 'earnings' ? 'earnings' : k)
              const isOn = kindOn(k)
              return (
                <button key={k} type="button" aria-pressed={isOn} onClick={() => toggleKind(k)} disabled={muted}
                  className={cn('inline-flex h-7 items-center gap-1.5 rounded-md border px-2 text-xs outline-none hover:bg-secondary focus-visible:ring-2 focus-visible:ring-ring/60',
                    isOn ? 'border-primary/40 bg-primary/10 text-primary' : 'text-muted-foreground')}>
                  {isOn ? <Check className="size-3.5" /> : <Icon className="size-3.5" />}
                  {label}
                </button>
              )
            })}
          </div>
          {kindOn('move') && (
            <div className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
              Big move: more than
              <NumField className="h-7 w-14" value={Number(sp.get(sid, 'move')) || null} label={`Daily move for this ${what}`}
                placeholder={rule ? String(rule) : 'off'} onSave={(v) => sp.set(sid, 'move', v)} />
              % a day{!sp.get(sid, 'move') && rule ? ' (your rule)' : ''}
            </div>
          )}
        </div>
      </div>
      <div className="flex items-center gap-2 border-t px-4 py-2.5 text-xs">
        {other ? (
          <Button size="sm" variant="ghost" onClick={clear} disabled={!below && !above && !on.length}><Trash2 /> Remove alerts</Button>
        ) : (
          <label className="flex items-center gap-2">
            <Switch checked={muted} onCheckedChange={(v) => sp.set(sid, 'mute', v || null)} aria-label={`Mute this ${what}`} />
            Mute this {what}
          </label>
        )}
        <div className="grow" />
        <Link to="/alerts/rules" onClick={close} className="text-primary hover:underline">All rules</Link>
      </div>
      {d.recent.length > 0 && (
        <div className="border-t px-2 py-1.5">
          {d.recent.slice(0, 3).map((i) => {
            const Icon = kindIcon(i.kind)
            return (
              <button key={i.id} type="button" onClick={() => { void markRead({ ids: [i.id] }); close(); navigate(alertLink(i) ?? '/alerts/list') }}
                className="flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left outline-none hover:bg-secondary focus-visible:bg-secondary">
                <Icon className={cn('mt-0.5 size-3.5 shrink-0 text-muted-foreground', i.level > 0 && 'text-up', i.level < 0 && 'text-down')} />
                <span className={cn('min-w-0 flex-1 text-[12.5px]', !i.read && 'font-semibold')}>{i.title}</span>
                <span className="num shrink-0 text-[11px] text-muted-foreground">{when(i.created, false)}</span>
              </button>
            )
          })}
          <Link to={`/alerts/list?t=${encodeURIComponent(d.ticker ?? d.t212_ticker)}`} onClick={close} className="block px-2 py-1 text-xs text-primary hover:underline">
            All {d.recent_total} alert{d.recent_total === 1 ? '' : 's'} for {d.ticker ?? `this ${what}`} →
          </Link>
        </div>
      )}
    </div>
  )
}
