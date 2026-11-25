import { NotebookPen } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Hint } from '@/components/ui/tooltip'
import { useSettings } from '@/lib/settings'
import { cn } from '@/lib/utils'

/** Your own note about a stock (why you bought it, what to watch for). Kept with your settings, so it stays
    after a restart and comes along with a copy of your data folder. Shown under the name on the stock page. */
export function useStockNote(t: string) {
  const { pref, setPref } = useSettings()
  const note = pref<string>('notes', t.toUpperCase(), '')
  return [note, (v: string) => setPref('notes', t.toUpperCase(), v.trim().slice(0, 2000))] as const
}

export function StockNote({ t }: { t: string }) {
  const [note, save] = useStockNote(t)
  const [open, setOpen] = useState(false)
  const [text, setText] = useState(note)
  return (
    <Popover
      open={open}
      onOpenChange={(o) => {
        if (o) setText(note)
        else if (text.trim() !== note) save(text)
        setOpen(o)
      }}
    >
      <Hint label={note ? 'Your note. Click to change it' : 'Write a note about this stock (only for you)'}>
        <PopoverTrigger asChild>
          <Button size="icon" aria-label="Note" className={cn(note && 'text-primary')}>
            <NotebookPen />
          </Button>
        </PopoverTrigger>
      </Hint>
      <PopoverContent className="w-80 p-3">
        <textarea
          autoFocus
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={5}
          maxLength={2000}
          placeholder="Why you own or watch it, what to look for at the next results…"
          className="w-full resize-y rounded-md border bg-background p-2 text-[13px] outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
        />
        <div className="mt-2 flex items-center gap-2">
          <span className="text-xs text-muted-foreground">Saved when you close this.</span>
          <div className="grow" />
          {note && <Button size="sm" variant="ghost" onClick={() => { setText(''); save(''); setOpen(false) }}>Delete</Button>}
          <Button size="sm" onClick={() => { save(text); setOpen(false) }}>Save</Button>
        </div>
      </PopoverContent>
    </Popover>
  )
}

/** The note under the stock's name, when there is one. */
export function StockNoteLine({ t }: { t: string }) {
  const [note] = useStockNote(t)
  if (!note) return null
  return (
    <p className="-mt-2 flex max-w-3xl items-start gap-2 rounded-lg border border-dashed px-3 py-2 text-[13px] whitespace-pre-line text-muted-foreground">
      <NotebookPen className="mt-0.5 size-3.5 shrink-0" />
      {note}
    </p>
  )
}
