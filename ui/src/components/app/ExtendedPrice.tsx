import { Moon, Sunrise, Sunset } from 'lucide-react'
import { Hint } from '@/components/ui/tooltip'
import { money, pctSigned, tone } from '@/lib/format'
import { cn } from '@/lib/utils'

export type Ext = { session: 'pre' | 'post' | 'overnight'; price: number; change?: number | null; change_pct: number | null; time?: number | null; live?: boolean }

export const EXT_LABEL = { pre: 'Pre-market', post: 'After hours', overnight: 'Overnight' } as const
export const EXT_ICON = { pre: Sunrise, post: Sunset, overnight: Moon } as const
export const EXT_TINT = { pre: 'text-amber-500 dark:text-amber-400', post: 'text-orange-500 dark:text-orange-400', overnight: 'text-indigo-500 dark:text-indigo-300' } as const
export const etTime = (s: number) => new Date(s * 1000).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' }) + ' ET'
export const etDay = (s: number) => new Date(s * 1000).toLocaleDateString('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric' })

/**
 * Pre-market, after-hours or overnight price with a sunrise / sunset / moon icon. Used under the big price on the
 * stock and ETF pages, and (compact: no dollar change, no time) on the Home market cards.
 */
export function ExtendedPrice({ ext, compact = false, plain = false }: { ext: Ext; compact?: boolean; plain?: boolean }) {
  const Icon = EXT_ICON[ext.session]
  const abs = ext.change ?? (ext.change_pct !== null && ext.change_pct !== undefined ? ext.price - ext.price / (1 + ext.change_pct) : null)
  const when = ext.time ? (ext.live ? etTime(ext.time) : `${etDay(ext.time)}, ${etTime(ext.time)}`) : ''
  const line = (
    <div className={cn('flex w-fit flex-wrap items-center gap-y-0.5', compact ? 'mt-1.5 gap-x-1.5 text-xs' : 'mt-1 gap-x-2 text-sm')}>
      <span className="inline-flex items-center gap-1 text-xs font-medium text-muted-foreground">
        <Icon aria-hidden className={cn(compact ? 'size-3.5' : 'size-4', EXT_TINT[ext.session])} />
        {EXT_LABEL[ext.session]}
      </span>
      <span className="num font-semibold">{plain ? ext.price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : money(ext.price)}</span>
      <span className={cn('num', tone(ext.change_pct))}>
        {!compact && abs !== null ? `${abs >= 0 ? '+' : '−'}${Math.abs(abs).toFixed(2)} (${pctSigned(ext.change_pct).replace(/^[+−]/, '')})` : pctSigned(ext.change_pct)}
      </span>
      {!compact && when && <span className="text-xs text-muted-foreground">{when}</span>}
    </div>
  )
  return (
    <Hint label={`${EXT_LABEL[ext.session]} price${ext.time ? `, last trade ${etDay(ext.time)} ${etTime(ext.time)}` : ''}. For information only: the day's change and statistics use the regular price.`}>
      {line}
    </Hint>
  )
}
