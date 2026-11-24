import type { ReactNode } from 'react'
import { useSettings } from '@/lib/settings'
import { cn } from '@/lib/utils'

/** The body of every page: same padding everywhere, full width or a centred column (a setting). */
export function Page({ title, actions, children }: { title: string; actions?: ReactNode; children: ReactNode }) {
  const { settings } = useSettings()
  return (
    <main className={cn('flex flex-col gap-5 p-6', settings.page_width === 'centered' && 'mx-auto w-full max-w-[1180px]')}>
      <div className="flex min-h-8 flex-wrap items-center gap-3">
        <h1 className="text-[22px] font-bold tracking-tight">{title}</h1>
        <div className="grow" />
        {actions}
      </div>
      {children}
    </main>
  )
}

/** The shared look for a panel. */
export function Card({ className, children, id }: { className?: string; children: ReactNode; id?: string }) {
  return (
    <section id={id} className={cn('rounded-xl border bg-card text-card-foreground', className)}>
      {children}
    </section>
  )
}
