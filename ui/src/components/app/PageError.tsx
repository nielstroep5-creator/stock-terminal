import { Component, type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import { TriangleAlert } from 'lucide-react'
import { api } from '@/lib/api'
import { Button } from '@/components/ui/button'

type Props = { children: ReactNode; resetKey: string }
type State = { error: Error | null }

/** Catches an error on a page, so one broken page shows a message instead of blanking the whole window.
    The error is written to data/logs/app_errors.log (POST /api/client-error) so it can be looked up later. */
class Boundary extends Component<Props, State> {
  state: State = { error: null }
  static getDerivedStateFromError(error: Error): State { return { error } }
  componentDidCatch(error: Error, info: { componentStack?: string | null }) {
    api.post('/api/client-error', { page: location.hash || location.pathname, message: String(error?.message || error), stack: `${error?.stack || ''}\n${info.componentStack || ''}`.slice(0, 6000) }).catch(() => {})
  }
  componentDidUpdate(prev: Props) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null })   // another page: try again
  }
  render() {
    const e = this.state.error
    if (!e) return this.props.children
    return (
      <main className="flex flex-1 items-start justify-center p-10">
        <div className="max-w-lg rounded-xl border bg-card p-6">
          <h1 className="flex items-center gap-2 text-[17px] font-semibold"><TriangleAlert className="size-5 text-flag" /> Something went wrong on this page</h1>
          <p className="mt-2 text-[13px] text-muted-foreground">The error is saved in data\logs\app_errors.log. Reload the page, or go back to where you were.</p>
          <p className="mt-3 rounded-md bg-panel px-3 py-2 font-mono text-xs break-words">{String(e.message || e)}</p>
          <div className="mt-4 flex gap-2">
            <Button variant="default" onClick={() => location.reload()}>Reload</Button>
            <Button variant="outline" onClick={() => history.back()}>Back</Button>
            <Button variant="ghost" onClick={() => { location.hash = `#/settings?find=report&page=${encodeURIComponent(location.hash.slice(1))}` }}>Report this</Button>
          </div>
        </div>
      </main>
    )
  }
}

export function PageError({ children }: { children: ReactNode }) {
  const loc = useLocation()
  return <Boundary resetKey={loc.pathname}>{children}</Boundary>
}
