import { CircleCheck, Download, Loader2, RefreshCw, RotateCw } from 'lucide-react'
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { api } from '@/lib/api'
import { useSettings } from '@/lib/settings'

/** /api/app/update (stockterminal/appupdate.py). 'target' names the newer version: a commit (git copy) or a release. */
export type UpdateInfo = {
  current: string
  mode: 'git' | 'installed'
  available: boolean
  latest: string
  target: string
  changes?: string[]
  behind?: number
  notes?: string | null
  url?: string
  setup_url?: string | null
  can_install: boolean
  error?: string
  checked_at?: string
  window: boolean
}
type InstallResult = { ok: boolean; version?: string; message: string; installing?: boolean }

export function useUpdateCheck(enabled = true) {
  return useQuery({
    queryKey: ['app-update'],
    queryFn: () => api.get<UpdateInfo>('/api/app/update'),
    enabled,
    staleTime: 60 * 60 * 1000,
    retry: false,
  })
}

/** What is new + the "Update now" button and its progress. Used in Settings > About and in the pop-up at start. */
export function UpdateBody({ info, onDone }: { info: UpdateInfo; onDone?: () => void }) {
  const qc = useQueryClient()
  const install = useMutation({
    mutationFn: () => api.post<InstallResult>('/api/app/update'),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['about'] }),
  })
  const restart = useMutation({ mutationFn: () => api.post<{ restarted: boolean }>('/api/app/restart') })
  const res = install.data

  const label = info.latest !== info.current ? `Version ${info.latest}` : 'A newer version'
  return (
    <div className="flex flex-col gap-3">
      <p className="text-[13px]">
        <span className="font-semibold">{label} is available.</span>{' '}
        <span className="text-muted-foreground">You have {info.current}.</span>
      </p>
      {!!info.changes?.length && (
        <div>
          <p className="mb-1 text-xs font-medium text-muted-foreground">What's new{info.behind && info.behind > info.changes.length ? ` (latest ${info.changes.length} of ${info.behind} changes)` : ''}</p>
          <ul className="max-h-48 list-disc overflow-y-auto pl-5 text-[13px] leading-relaxed">
            {info.changes.map((c, i) => <li key={i}>{c}</li>)}
          </ul>
        </div>
      )}
      {info.notes && <pre className="max-h-48 overflow-y-auto font-sans text-[13px] leading-relaxed whitespace-pre-wrap">{info.notes}</pre>}

      {install.isPending && (
        <p className="flex items-center gap-2 text-[13px] text-muted-foreground"><Loader2 className="size-4 animate-spin" /> Downloading and testing the new version. This takes about half a minute.</p>
      )}
      {install.isError && <p className="text-[13px] text-down">{(install.error as Error).message}</p>}
      {res && !res.ok && <p className="text-[13px] text-down">{res.message}</p>}
      {res?.ok && (
        <p className="flex items-center gap-2 text-[13px]">
          <CircleCheck className="size-4 text-up" />
          {res.installing ? res.message : info.window ? `Updated to ${res.version}. Restart to use it.` : `Updated to ${res.version}. Stop the server and start it again to use it.`}
        </p>
      )}

      <div className="flex flex-wrap justify-end gap-2">
        {onDone && !res?.ok && <Button variant="ghost" onClick={onDone} disabled={install.isPending}>Later</Button>}
        {!res?.ok && info.can_install && (
          <Button variant="default" onClick={() => install.mutate()} disabled={install.isPending}>
            <Download className="size-4" /> Update now
          </Button>
        )}
        {!info.can_install && info.url && (
          <Button asChild variant="default"><a href={info.url} target="_blank" rel="noreferrer"><Download className="size-4" /> Download</a></Button>
        )}
        {res?.ok && !res.installing && info.window && (
          <Button variant="default" onClick={() => restart.mutate()} disabled={restart.isPending}>
            <RotateCw className="size-4" /> Restart now
          </Button>
        )}
        {res?.ok && onDone && <Button variant="ghost" onClick={onDone}>Close</Button>}
      </div>
    </div>
  )
}

/** Settings > About: the version, a check button and, when there is one, the newer version. */
export function UpdateField() {
  const qc = useQueryClient()
  const about = useQuery({ queryKey: ['about'], queryFn: () => api.get<{ version: string; commit: string | null }>('/api/about'), staleTime: Infinity })
  const check = useUpdateCheck()
  const [checking, setChecking] = useState(false)
  const recheck = async () => {
    setChecking(true)
    try {
      qc.setQueryData(['app-update'], await api.get<UpdateInfo>('/api/app/update?refresh=1'))
    } finally {
      setChecking(false)
    }
  }
  const info = check.data
  return (
    <div className="flex w-full flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        {about.data && (
          <span className="font-mono text-[13px] tabular-nums">
            {about.data.version}
            {about.data.commit && <span className="text-muted-foreground"> · {about.data.commit}</span>}
          </span>
        )}
        <span className="flex-1" />
        {info && !info.available && !info.error && !checking && <span className="text-[12.5px] text-muted-foreground">You have the latest version</span>}
        {info?.error && !checking && <span className="text-[12.5px] text-down">{info.error}</span>}
        <Button onClick={recheck} disabled={checking}>
          <RefreshCw className={checking ? 'size-4 animate-spin' : 'size-4'} /> Check for updates
        </Button>
      </div>
      {info?.available && !checking && (
        <div className="rounded-lg border bg-panel p-4">
          <UpdateBody info={info} />
        </div>
      )}
    </div>
  )
}

/** At start: a pop-up when a newer version is available. "Skip this version" is remembered (pref ui2.update_skip);
    "Check for updates at start" in Settings > About turns it off. Waits until the welcome screen is done. */
export function UpdatePrompt() {
  const { settings, pref, setPref } = useSettings()
  const ready = settings.update_check && !!settings.sec_email
  const check = useUpdateCheck(ready)
  const [closed, setClosed] = useState(false)
  const info = check.data
  const skipped = pref('ui2', 'update_skip', '')
  const open = ready && !closed && !!info?.available && info.target !== skipped
  if (!info) return null
  return (
    <Dialog open={open} onOpenChange={(o) => !o && setClosed(true)}>
      <DialogContent className="max-w-lg p-6">
        <span className="mb-3 flex size-10 items-center justify-center rounded-lg bg-primary text-primary-foreground"><Download className="size-5" /></span>
        <DialogTitle className="text-lg font-semibold">Update available</DialogTitle>
        <DialogDescription className="sr-only">A newer version of Stock Terminal is available.</DialogDescription>
        <div className="mt-2">
          <UpdateBody info={info} onDone={() => setClosed(true)} />
        </div>
        <button
          type="button"
          className="mt-3 self-start text-xs text-muted-foreground underline-offset-2 hover:underline"
          onClick={() => {
            setPref('ui2', 'update_skip', info.target)
            setClosed(true)
          }}
        >
          Skip this version
        </button>
      </DialogContent>
    </Dialog>
  )
}
