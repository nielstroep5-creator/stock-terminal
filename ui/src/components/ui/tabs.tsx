import { NavLink } from 'react-router-dom'
import { cn } from '@/lib/utils'

/** Tabs that are real addresses, so back / forward and links from other pages land on the right tab. */
export function TabLinks({ tabs, label }: { tabs: { to: string; label: string }[]; label: string }) {
  return (
    <nav aria-label={label} className="flex gap-1 overflow-x-auto overflow-y-hidden border-b [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
      {tabs.map((t) => (
        <NavLink
          key={t.to}
          to={t.to}
          replace={false}
          className={({ isActive }) =>
            cn(
              '-mb-px border-b-2 border-transparent px-3 py-2 text-[13.5px] font-medium whitespace-nowrap text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60',
              isActive && 'border-primary text-foreground',
            )
          }
        >
          {t.label}
        </NavLink>
      ))}
    </nav>
  )
}
