import { ChartLine } from 'lucide-react'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { DISCLAIMER } from '@/lib/about'
import { useSettings } from '@/lib/settings'

/** First start: the app needs a contact email for the SEC before it can download company data. Shown at every start
    until one is saved; "Later" closes it for now. */
export function Welcome() {
  const { settings, update } = useSettings()
  const [later, setLater] = useState(false)
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const open = !settings.sec_email && !later
  const save = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    const err = await update({ sec_email: text.trim() })
    setBusy(false)
    setError(err)
  }
  return (
    <Dialog open={open} onOpenChange={(o) => !o && setLater(true)}>
      <DialogContent className="max-w-md p-6">
        <span className="mb-3 flex size-10 items-center justify-center rounded-lg bg-primary text-primary-foreground"><ChartLine className="size-5" /></span>
        <DialogTitle className="text-lg font-semibold">Welcome to Stock Terminal</DialogTitle>
        <DialogDescription className="mt-1.5 text-[13px] leading-relaxed text-muted-foreground">
          Enter your email so the app can download company data. The SEC (the US source of all financial reports) asks every tool to include a contact email. It is only sent to the SEC and stored on this computer.
        </DialogDescription>
        <form onSubmit={save} className="mt-4 flex flex-col gap-2">
          <Input type="email" autoFocus value={text} onChange={(e) => setText(e.target.value)} placeholder="you@example.com" autoComplete="email" spellCheck={false} aria-label="Contact email for the SEC" aria-invalid={!!error} />
          {error && <span className="text-xs text-down">{error}</span>}
          <div className="mt-2 flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setLater(true)}>Later</Button>
            <Button type="submit" variant="default" disabled={busy || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(text.trim())}>Save</Button>
          </div>
        </form>
        <p className="mt-3 text-xs text-muted-foreground">You can change it later in Settings, under Data and accounts.</p>
        <p className="mt-3 border-t pt-3 text-xs leading-relaxed text-muted-foreground">{DISCLAIMER}</p>
      </DialogContent>
    </Dialog>
  )
}
