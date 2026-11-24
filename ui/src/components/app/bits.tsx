import type { ReactNode } from 'react'
import { Hint } from '@/components/ui/tooltip'
import { fmt } from '@/lib/format'
import { cn } from '@/lib/utils'

/** A card's title row, with room for switches on the right. */
export function H2({ children, side, className }: { children: ReactNode; side?: ReactNode; className?: string }) {
  return (
    <div className={cn('mb-3 flex flex-wrap items-center gap-2', className)}>
      <h2 className="text-[15px] font-semibold">{children}</h2>
      <div className="grow" />
      {side}
    </div>
  )
}

/** A label with its value on the right, one line of a key-facts list. */
export function Kv({ label, tip, children }: { label: string; tip?: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b py-2 text-[13px] last:border-0">
      {tip ? <Hint label={tip}><span className="cursor-help text-muted-foreground underline decoration-dotted decoration-border underline-offset-4">{label}</span></Hint> : <span className="text-muted-foreground">{label}</span>}
      <span className="num text-right font-medium">{children}</span>
    </div>
  )
}

/** Horizontal bars with a name and a value: sectors, countries, holdings. */
export function Bars({ items, color, unit }: { items: [string, number][]; color: string; unit?: (v: number) => string }) {
  const max = Math.max(...items.map(([, v]) => v), 0) || 1
  return (
    <div className="flex flex-col gap-1.5">
      {items.map(([k, v]) => (
        <div key={k} className="grid grid-cols-[minmax(0,10rem)_1fr_3.5rem] items-center gap-3 text-[13px]">
          <span className="truncate" title={k}>{k}</span>
          <span className="h-2 rounded-full bg-secondary"><span className="block h-2 rounded-full" style={{ width: `${(Math.max(v, 0) / max) * 100}%`, background: color }} /></span>
          <span className="num text-right text-muted-foreground">{unit ? unit(v) : fmt(v, 'pct')}</span>
        </div>
      ))}
    </div>
  )
}

/** One big number with a label above and a line below, in a row of numbers (portfolio totals and the like). */
export function Stat({ label, value, sub, tip, className }: { label: string; value: ReactNode; sub?: ReactNode; tip?: string; className?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-xs text-muted-foreground">
        {tip ? <Hint label={tip}><span className="cursor-help underline decoration-dotted decoration-border underline-offset-4">{label}</span></Hint> : label}
      </div>
      <div className={cn('num mt-0.5 truncate text-[17px] font-semibold', className)}>{value}</div>
      {sub !== undefined && <div className="num mt-0.5 truncate text-xs text-muted-foreground">{sub}</div>}
    </div>
  )
}

/** A centred message in an empty card: nothing to show yet, or loading. */
export function Empty({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('flex min-h-32 flex-col items-center justify-center gap-2 p-8 text-center text-[13px] text-muted-foreground', className)}>{children}</div>
}
