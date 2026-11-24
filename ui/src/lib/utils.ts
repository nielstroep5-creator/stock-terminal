import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** Keyboard on clickable table rows (Screener, Watchlist): put `tabIndex={0}` on each row and this on the
 *  <tbody>. Up / down move between rows, Enter opens the row (its click). */
export function rowKeys(e: React.KeyboardEvent<HTMLTableSectionElement>, open?: (row: HTMLTableRowElement) => void) {
  const row = (e.target as HTMLElement).closest('tr')
  if (!row || row.parentElement !== e.currentTarget) return
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    const next = (e.key === 'ArrowDown' ? row.nextElementSibling : row.previousElementSibling) as HTMLElement | null
    if (next?.tabIndex === 0) {
      e.preventDefault()
      next.focus()
    }
  } else if (e.key === 'Enter' && e.target === row) {
    e.preventDefault()
    if (open) open(row)
    else row.click()
  }
}
