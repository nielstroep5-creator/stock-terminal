import { useQueryClient } from '@tanstack/react-query'
import { ChevronDown, CircleAlert, Loader2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Card, Page } from '@/components/app/Page'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'

type Job = { ticker?: string; state: 'running' | 'done' | 'error' | 'none'; lines: string[] }
const OFFLINE = /ConnectionError|ProxyError|Max retries|getaddrinfo|timed out|Temporary failure|NameResolution/i

/** /load/<ticker>: downloads a company or ETF that is not in the database yet, shows the progress, then opens it. */
export function LoadPage() {
  const { ticker = '' } = useParams()
  const t = ticker.toUpperCase()
  const nav = useNavigate()
  const qc = useQueryClient()
  const [job, setJob] = useState<Job | null>(null)
  const [run, setRun] = useState(0)
  const [open, setOpen] = useState(false)
  const log = useRef<HTMLPreElement>(null)

  useEffect(() => {
    let stop = false
    let timer = 0
    setJob(null)
    let misses = 0
    const poll = async () => {
      const j = await api.get<Job>(`/api/job/${encodeURIComponent(t)}`).catch(() => null)
      if (stop) return
      if (!j) {
        // a missed answer (the app busy writing) is no reason to stop: try again, give up after about a minute
        misses += 1
        if (misses < 75) timer = window.setTimeout(poll, 800)
        else setJob((old) => ({ state: 'error', lines: [...(old?.lines ?? []), 'The app stopped answering about this load. Try again.'] }))
        return
      }
      misses = 0
      setJob(j)
      if (j.state === 'running') timer = window.setTimeout(poll, 800)
      else if (j.state === 'done') {
        void qc.invalidateQueries()
        nav(`/stock/${j.ticker || t}`, { replace: true }) // the stock page sends an ETF on to its own page
      }
    }
    api.post(`/api/load/${encodeURIComponent(t)}`).then(poll, (e: Error) => !stop && setJob({ state: 'error', lines: [e.message] }))
    return () => {
      stop = true
      window.clearTimeout(timer)
    }
  }, [t, run, nav, qc])

  useEffect(() => {
    if (log.current) log.current.scrollTop = log.current.scrollHeight
  }, [job?.lines.length])

  const failed = job?.state === 'error'
  const all = job?.lines.join('\n') ?? ''
  return (
    <Page title={`Adding ${t}`}>
      <Card className="flex flex-col gap-3 p-5">
        <div className="flex items-start gap-3 text-[13.5px]">
          {failed ? <CircleAlert className="mt-0.5 size-5 shrink-0 text-down" /> : <Loader2 className="mt-0.5 size-5 shrink-0 animate-spin text-primary" />}
          <div className="flex flex-col gap-2">
            {failed ? (
              <>
                <span>{OFFLINE.test(all) ? "Couldn't reach the SEC or Yahoo Finance. Check your internet connection and try again." : `Couldn't load ${t}. Check the ticker (for example BRK-B, not BRK/B) and try again.`}</span>
                <Button variant="default" size="sm" className="self-start" onClick={() => setRun((r) => r + 1)}>Try again</Button>
              </>
            ) : (
              <>
                <span className="font-medium">Downloading data from the SEC and Yahoo Finance.</span>
                <span className="text-muted-foreground">The first time takes about a minute. The page opens by itself when it is ready.</span>
              </>
            )}
          </div>
        </div>
        {!!job?.lines.length && failed && (
          <button type="button" onClick={() => setOpen(!open)} className="inline-flex items-center gap-1 self-start text-xs font-medium text-muted-foreground hover:text-foreground">
            <ChevronDown className={cn('size-3.5 transition-transform', open && 'rotate-180')} /> Technical details
          </button>
        )}
        {!!job?.lines.length && (open || !failed) && (
          <pre ref={log} className="max-h-80 overflow-auto rounded-lg bg-panel p-3 font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap text-muted-foreground">{all}</pre>
        )}
      </Card>
    </Page>
  )
}
