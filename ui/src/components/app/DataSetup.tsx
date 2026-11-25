import { CircleCheck, Database, Download, FolderOpen, Loader2, RotateCw } from 'lucide-react'
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { api } from '@/lib/api'
import { useSettings } from '@/lib/settings'

/** /api/data/setup (stockterminal/datasetup.py): company data in one click for a new install. */
type Pack = { name: string; size: number; url: string; date: string }
type Job = { state: 'running' | 'ready' | 'error'; step?: string; done?: number; total?: number; error?: string; source?: string; companies?: number }
type DataInfo = {
  companies: number
  waiting: boolean
  waiting_info?: { source: string; companies: number; at: string }
  job: Job | null
  pack: Pack | null
  pack_error?: string
  window: boolean
  can_browse: boolean
}

const mb = (n: number) => `${Math.round(n / 1e6).toLocaleString()} MB`

function useDataSetup(enabled = true) {
  return useQuery({
    queryKey: ['data-setup'],
    queryFn: () => api.get<DataInfo>('/api/data/setup'),
    enabled,
    refetchInterval: (q) => (q.state.data?.job?.state === 'running' ? 1000 : false),
    retry: false,
  })
}

function Progress({ job }: { job: Job }) {
  const pct = job.total ? Math.min(100, Math.round(((job.done ?? 0) / job.total) * 100)) : null
  return (
    <div className="flex flex-col gap-1.5">
      <p className="flex items-center gap-2 text-[13px] text-muted-foreground">
        <Loader2 className="size-4 animate-spin" /> {job.step}
        {pct !== null && ` (${pct}%)`}
      </p>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
        <div className="h-full bg-primary transition-[width]" style={{ width: `${pct ?? 5}%` }} />
      </div>
    </div>
  )
}

/** The choices (starter pack, another copy) with progress, and "Restart now" when the new data is ready. */
export function DataSetupBody({ info, onDone }: { info: DataInfo; onDone?: () => void }) {
  const qc = useQueryClient()
  const [path, setPath] = useState('')
  const refresh = () => qc.invalidateQueries({ queryKey: ['data-setup'] })
  const start = useMutation({
    mutationFn: (body: { source: 'download' | 'path'; path?: string }) => api.post<Job>('/api/data/setup', body),
    onSettled: refresh,
  })
  const browse = async (kind: 'folder' | 'file') => {
    const r = await api.post<{ path: string | null }>('/api/data/browse', { kind })
    if (r.path) setPath(r.path)
  }
  const restart = useMutation({ mutationFn: () => api.post<{ restarted: boolean }>('/api/app/restart') })
  const cancel = useMutation({ mutationFn: () => api.del('/api/data/setup'), onSettled: refresh })
  const job = info.job
  const running = job?.state === 'running'

  if (info.waiting) {
    const w = info.waiting_info
    return (
      <div className="flex flex-col gap-3">
        <p className="flex items-center gap-2 text-[13px]">
          <CircleCheck className="size-4 text-up" />
          {w ? `${w.source}: ${w.companies.toLocaleString()} companies ready.` : 'New data ready.'}{' '}
          {info.window ? 'Restart the app to use it.' : 'Stop the server and start it again to use it.'}
        </p>
        <p className="text-xs text-muted-foreground">Your current data is kept once as a backup (data/backup). Portfolio, watchlists and alerts stay when you use a starter pack.</p>
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={() => cancel.mutate()}>Don't use it</Button>
          {info.window && (
            <Button variant="default" onClick={() => restart.mutate()} disabled={restart.isPending}>
              <RotateCw className="size-4" /> Restart now
            </Button>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      {running && job && <Progress job={job} />}
      {job?.state === 'error' && <p className="text-[13px] text-down">{job.error}</p>}
      {start.isError && <p className="text-[13px] text-down">{(start.error as Error).message}</p>}

      <div className="flex flex-col gap-1.5">
        <p className="text-[13px] font-medium">The S&amp;P 500, ready in a few minutes</p>
        <p className="text-[12.5px] text-muted-foreground">
          {info.pack
            ? `Every S&P 500 company with all its numbers (starter pack of ${info.pack.date}, ${mb(info.pack.size)} download). New results and prices come in with the daily update.`
            : info.pack_error ?? 'There is no starter pack on the download page yet. Use another copy below, or load companies one by one from the search.'}
        </p>
        {info.pack && (
          <div>
            <Button variant="default" onClick={() => start.mutate({ source: 'download' })} disabled={running || start.isPending}>
              <Download className="size-4" /> Get the S&amp;P 500
            </Button>
          </div>
        )}
      </div>

      <div className="flex flex-col gap-1.5 border-t pt-3">
        <p className="text-[13px] font-medium">Use data from another copy</p>
        <p className="text-[12.5px] text-muted-foreground">Another Stock Terminal folder on this computer (all companies, settings, portfolio and watchlists are copied; that copy is not changed), or a starter pack .zip.</p>
        <div className="flex flex-wrap gap-2">
          <Input className="min-w-[260px] flex-1" value={path} onChange={(e) => setPath(e.target.value)} placeholder="C:\Stock Terminal" spellCheck={false} aria-label="Folder of another copy, or a starter pack file" />
          {info.can_browse && (
            <>
              <Button onClick={() => void browse('folder')} disabled={running}><FolderOpen className="size-4" /> Folder</Button>
              <Button onClick={() => void browse('file')} disabled={running}>.zip</Button>
            </>
          )}
          <Button variant="default" onClick={() => start.mutate({ source: 'path', path })} disabled={running || start.isPending || !path.trim()}>Use this</Button>
        </div>
      </div>

      {onDone && (
        <div className="flex justify-end">
          <Button variant="ghost" onClick={onDone}>{running ? 'Close (keeps going)' : 'Not now'}</Button>
        </div>
      )}
    </div>
  )
}

type About = { version: string; data_dir: string; imported: { at: string; source: string } | null; kind: 'installed app' | 'test copy'; window: boolean }

/** Settings > Data and accounts > Data folder: which folder this copy reads and writes, so two copies on one
    computer (the installed app and a test copy) can't be mixed up. */
export function DataFolderField() {
  const q = useQuery({ queryKey: ['about-data'], queryFn: () => api.get<About>('/api/about'), staleTime: Infinity })
  const open = useMutation({ mutationFn: () => api.post<{ opened: boolean }>('/api/data/open-folder') })
  if (!q.data) return null
  const d = q.data
  return (
    <div className="flex w-full flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <code className="min-w-0 flex-1 break-all rounded-md border bg-panel px-2.5 py-1.5 text-[12.5px]">{d.data_dir}</code>
        {d.window && <Button onClick={() => open.mutate()} disabled={open.isPending}><FolderOpen className="size-4" /> Open folder</Button>}
      </div>
      <p className="text-xs text-muted-foreground">
        {d.kind === 'installed app'
          ? 'This is the installed app. Its data stays in your own user folder through updates and reinstalling.'
          : 'This is a test copy (the code folder), not the installed app. Its data is separate: changes here do not show in the installed app.'}
        {d.imported &&
          (d.imported.source.startsWith('Data from ')
            ? ` Copied from ${d.imported.source.slice(10)} on ${d.imported.at}; that folder is a separate copy, changes there don't reach this one.`
            : ` Filled from the ${d.imported.source.replace(/^Starter pack/, 'starter pack of')} on ${d.imported.at}.`)}
      </p>
    </div>
  )
}

/** Settings > Data and accounts > Company data. */
export function DataField() {
  const q = useDataSetup()
  if (!q.data) return null
  return (
    <div className="flex w-full flex-col gap-3">
      <span className="text-[13px] text-muted-foreground">{q.data.companies.toLocaleString()} companies on this computer</span>
      <div className="rounded-lg border bg-panel p-4">
        <DataSetupBody info={q.data} />
      </div>
    </div>
  )
}

/** Fresh install, after the welcome screen: offer the S&P 500 once. "Not now" is remembered (pref ui2.data_offer_done);
    it stays in Settings > Data and accounts. */
export function DataPrompt() {
  const { settings, pref, setPref } = useSettings()
  const done = pref('ui2', 'data_offer_done', false)
  const ready = !!settings.sec_email && !done
  const q = useDataSetup(ready)
  const [closed, setClosed] = useState(false)
  const info = q.data
  if (!info) return null
  const open = ready && !closed && (info.waiting || !!info.job || info.companies < 20)
  const close = () => {
    setPref('ui2', 'data_offer_done', true)
    setClosed(true)
  }
  return (
    <Dialog open={open} onOpenChange={(o) => !o && close()}>
      <DialogContent className="max-w-lg p-6">
        <span className="mb-3 flex size-10 items-center justify-center rounded-lg bg-primary text-primary-foreground"><Database className="size-5" /></span>
        <DialogTitle className="text-lg font-semibold">Get company data</DialogTitle>
        <DialogDescription className="mt-1.5 mb-4 text-[13px] leading-relaxed text-muted-foreground">
          The app starts empty. Get the S&amp;P 500 in one go, or use the data of a copy you already have. You can also do this later in Settings, under Data and accounts.
        </DialogDescription>
        <DataSetupBody info={info} onDone={close} />
      </DialogContent>
    </Dialog>
  )
}
