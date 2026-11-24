import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

const ToastContext = createContext<(message: ReactNode) => void>(() => {})

/** A short confirmation at the bottom of the window ("AAPL added to My watchlist"). It leaves by itself. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [message, setMessage] = useState<ReactNode>(null)
  const timer = useRef<number | undefined>(undefined)
  const toast = useCallback((m: ReactNode) => {
    setMessage(m)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setMessage(null), 3200)
  }, [])
  // Parts outside this provider (the settings store) raise a toast with a window event.
  useEffect(() => {
    const on = (e: Event) => toast(String((e as CustomEvent).detail ?? ''))
    window.addEventListener('st-toast', on)
    return () => window.removeEventListener('st-toast', on)
  }, [toast])
  return (
    <ToastContext.Provider value={toast}>
      {children}
      <div role="status" aria-live="polite" className="pointer-events-none fixed inset-x-0 bottom-6 z-[60] flex justify-center">
        {message && <div className="pointer-events-auto rounded-lg bg-foreground px-3.5 py-2 text-[13px] text-background shadow-lg animate-in fade-in-0 slide-in-from-bottom-2 [&_a]:font-semibold [&_a]:underline">{message}</div>}
      </div>
    </ToastContext.Provider>
  )
}

export const useToast = () => useContext(ToastContext)
