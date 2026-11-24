import { Check, Copy, ExternalLink, RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'

type Report = { title: string; text: string; issue_url: string; last_error: string | null }

/** Settings > About > Report a problem. Nothing is sent by itself: you read the report, then copy it or open it
    as an issue on the public download page (needs a free GitHub account). stockterminal/report.py */
export function ReportField() {
  const [params] = useSearchParams()
  const page = params.get('page') ?? ''
  const [open, setOpen] = useState(!!page)
  const [note, setNote] = useState('')
  const [copied, setCopied] = useState(false)
  const q = useQuery({
    queryKey: ['report', page],
    queryFn: () => api.get<Report>(`/api/report?page=${encodeURIComponent(page)}`),
    enabled: open,
    staleTime: 0,
  })
  const withNote = (t: string) => (note.trim() ? t.replace('(describe what you did and what went wrong)', note.trim()) : t)
  const copy = async () => {
    if (!q.data) return
    await navigator.clipboard.writeText(withNote(q.data.text))
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }
  const issueUrl = (() => {
    if (!q.data) return ''
    const u = new URL(q.data.issue_url)
    u.searchParams.set('body', withNote(u.searchParams.get('body') ?? ''))
    return u.toString()
  })()
  if (!open) return <Button onClick={() => setOpen(true)}>Make a report</Button>
  return (
    <div className="flex w-full flex-col gap-2">
      <textarea
        className="min-h-16 rounded-md border bg-background px-3 py-2 text-[13px]"
        placeholder="What did you do, and what went wrong? (optional)"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        aria-label="What went wrong"
      />
      {q.data && (
        <pre className="max-h-56 overflow-auto rounded-md bg-panel px-3 py-2 font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap">{withNote(q.data.text)}</pre>
      )}
      {q.isError && <p className="text-[13px] text-down">{(q.error as Error).message}</p>}
      <p className="text-xs text-muted-foreground">This is everything in the report. Email addresses and your user name are left out. Nothing is sent unless you send it.</p>
      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="ghost" onClick={() => void q.refetch()}><RefreshCw className="size-4" /> Refresh</Button>
        <Button onClick={() => void copy()} disabled={!q.data}>{copied ? <Check className="size-4" /> : <Copy className="size-4" />} {copied ? 'Copied' : 'Copy'}</Button>
        {q.data && (
          <Button asChild variant="default"><a href={issueUrl} target="_blank" rel="noreferrer"><ExternalLink className="size-4" /> Send on GitHub</a></Button>
        )}
      </div>
    </div>
  )
}
