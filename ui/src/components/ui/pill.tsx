import { ArrowDownRight, ArrowUpRight } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/** A small green or red label for a change: +1.17%. The arrow says the direction without relying on colour alone. */
export function ChangePill({ value, children, arrow = true, className }: { value: number | null | undefined; children: ReactNode; arrow?: boolean; className?: string }) {
  if (value === null || value === undefined) return null
  const up = value >= 0
  return (
    <span
      className={cn(
        'num inline-flex h-6 items-center gap-0.5 rounded-md px-1.5 text-xs font-semibold',
        up ? 'bg-up-bg text-up' : 'bg-down-bg text-down',
        className,
      )}
    >
      {arrow && (up ? <ArrowUpRight className="size-3.5" /> : <ArrowDownRight className="size-3.5" />)}
      {children}
    </span>
  )
}
