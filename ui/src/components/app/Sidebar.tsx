import { PanelLeftClose, PanelLeftOpen, SlidersHorizontal } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { Hint } from '@/components/ui/tooltip'
import { isActive, NAV } from '@/lib/nav'
import { useSettings } from '@/lib/settings'
import { cn } from '@/lib/utils'

function Item({ to, label, icon: Icon, active, collapsed, sub }: { to: string; label: string; icon: LucideIcon; active: boolean; collapsed: boolean; sub?: boolean }) {
  const link = (
    <Link
      to={to}
      aria-current={active ? 'page' : undefined}
      aria-label={collapsed ? label : undefined}
      className={cn(
        'flex h-9 items-center gap-2.5 rounded-md px-2.5 text-sm text-muted-foreground transition-colors outline-none hover:bg-secondary hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60',
        active && 'bg-accent font-semibold text-accent-foreground hover:bg-accent hover:text-accent-foreground',
        collapsed && 'justify-center px-0',
        sub && !collapsed && 'h-8 pl-8 text-[13px]',
      )}
    >
      <Icon className={cn('size-4 shrink-0', sub && !collapsed && 'size-3.5')} />
      {!collapsed && label}
    </Link>
  )
  return collapsed ? (
    <Hint label={label} side="right">
      {link}
    </Hint>
  ) : (
    link
  )
}

function useNarrow(px: number) {
  const [narrow, setNarrow] = useState(() => window.innerWidth < px)
  useEffect(() => {
    const m = window.matchMedia(`(max-width: ${px - 1}px)`)
    const on = () => setNarrow(m.matches)
    m.addEventListener('change', on)
    return () => m.removeEventListener('change', on)
  }, [px])
  return narrow
}

export function Sidebar() {
  const { pathname } = useLocation()
  const { pref, setPref } = useSettings()
  // Your own choice wins; without one the sidebar folds to icons on a narrow window (below 1200 px).
  const choice = pref('ui2', 'sidebar_choice', pref('ui2', 'sidebar_collapsed', false) ? 'closed' : '')
  const narrow = useNarrow(1200)
  const collapsed = choice ? choice === 'closed' : narrow
  const Toggle = collapsed ? PanelLeftOpen : PanelLeftClose

  return (
    <aside
      className={cn(
        'sticky top-0 flex h-screen shrink-0 flex-col gap-1 border-r bg-panel p-2.5 transition-[width] duration-150',
        collapsed ? 'w-[57px]' : 'w-52',
      )}
    >
      <Link to="/" className={cn('mb-3 flex h-9 items-center gap-2.5 px-2 text-[15px] font-bold', collapsed && 'justify-center px-0')}>
        <img src="/static/logo.svg" alt="" className="size-6 shrink-0" />
        {!collapsed && 'Stock Terminal'}
      </Link>
      <nav aria-label="Main" className="flex flex-col gap-0.5">
        {NAV.map((n) => (
          <Item key={n.to} {...n} active={isActive(n, pathname)} collapsed={collapsed} />
        ))}
      </nav>
      <div className="grow" />
      <Item to="/settings" label="Settings" icon={SlidersHorizontal} active={pathname === '/settings'} collapsed={collapsed} />
      <Hint label={collapsed ? 'Expand the sidebar' : 'Collapse the sidebar'} side="right">
        <button
          type="button"
          aria-label={collapsed ? 'Expand the sidebar' : 'Collapse the sidebar'}
          onClick={() => setPref('ui2', 'sidebar_choice', collapsed ? 'open' : 'closed')}
          className={cn(
            'flex h-9 items-center gap-2.5 rounded-md px-2.5 text-sm text-muted-foreground transition-colors outline-none hover:bg-secondary hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60',
            collapsed && 'justify-center px-0',
          )}
        >
          <Toggle className="size-4 shrink-0" />
          {!collapsed && 'Collapse'}
        </button>
      </Hint>
    </aside>
  )
}
