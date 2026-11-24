import type { ReactNode } from 'react'
import { Hint } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

/** An icon that switches something on or off. It always has a tooltip that says what. */
/** A switch shown as a button. `text` adds a word next to the icon for toggles whose icon alone is hard to
 *  guess ("%" growth rows, "#" labels). */
export function IconToggle({ on, onChange, label, children, className, text }: { on: boolean; onChange: (on: boolean) => void; label: string; children: ReactNode; className?: string; text?: string }) {
  return (
    <Hint label={label}>
      <button
        type="button"
        aria-label={label}
        aria-pressed={on}
        onClick={() => onChange(!on)}
        className={cn(
          text ? 'inline-flex h-8 shrink-0 items-center justify-center gap-1.5 rounded-md border px-2.5 text-[13px] font-medium' : 'inline-flex size-8 shrink-0 items-center justify-center rounded-md border',
          'text-muted-foreground transition-colors outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60 [&_svg]:size-4',
          on ? 'border-primary/40 bg-accent text-accent-foreground hover:text-accent-foreground' : 'bg-card hover:bg-secondary',
          className,
        )}
      >
        {children}
        {text}
      </button>
    </Hint>
  )
}
