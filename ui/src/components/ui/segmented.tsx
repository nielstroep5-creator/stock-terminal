import { ToggleGroup as ToggleGroupPrimitive } from 'radix-ui'
import type * as React from 'react'
import { Hint } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

export type SegmentedOption<T extends string> = {
  value: T
  /** Text shown in the button. Leave out for an icon-only option. */
  label?: string
  icon?: React.ReactNode
  /** Words for an icon-only option (tooltip and screen readers). */
  hint?: string
}

// Styled on aria-checked, not data-state: a tooltip around an icon option overwrites data-state.
/** One choice out of a few, shown as a row of buttons: periods, ranges, line or bars. */
function Segmented<T extends string>({
  value,
  onChange,
  options,
  label,
  className,
}: {
  value: T
  onChange: (value: T) => void
  options: SegmentedOption<T>[]
  label: string
  className?: string
}) {
  return (
    <ToggleGroupPrimitive.Root
      type="single"
      value={value}
      onValueChange={(v) => v && onChange(v as T)}
      aria-label={label}
      className={cn('inline-flex items-center gap-0.5 rounded-lg bg-secondary p-[3px]', className)}
    >
      {options.map((o) => {
        const item = (
          <ToggleGroupPrimitive.Item
            key={o.value}
            value={o.value}
            aria-label={o.label ? undefined : o.hint}
            className={cn(
              'inline-flex h-7 min-w-[30px] items-center justify-center gap-1.5 rounded-md px-2.5 text-[13px] text-muted-foreground transition-colors outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60 aria-checked:bg-card aria-checked:font-semibold aria-checked:text-foreground aria-checked:shadow-sm [&_svg]:size-4',
              !o.label && 'px-0',
            )}
          >
            {o.icon}
            {o.label}
          </ToggleGroupPrimitive.Item>
        )
        return o.hint ? (
          <Hint key={o.value} label={o.hint}>
            {item}
          </Hint>
        ) : (
          item
        )
      })}
    </ToggleGroupPrimitive.Root>
  )
}

export { Segmented }
