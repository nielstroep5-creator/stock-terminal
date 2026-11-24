import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, FolderOpen, Loader2, Plus, Trash2, Upload } from 'lucide-react'
import { useState } from 'react'
import { Card } from '@/components/app/Page'
import { Empty, H2 } from '@/components/app/bits'
import { Logo } from '@/components/app/Logo'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Segmented } from '@/components/ui/segmented'
import { Hint } from '@/components/ui/tooltip'
import { api } from '@/lib/api'
import { day } from '@/lib/format'
import { useToast } from '@/lib/toast'
import { cn } from '@/lib/utils'
import { qty, sharePrice } from './common'

// Your own trades, for a portfolio without Trading 212 (stockterminal/own.py). Every change rebuilds the portfolio
// on the server, so the totals, the holdings and the Performance chart above follow at once.

type Trade = { id: number; date: string; symbol: string; name: string | null; currency: string | null; side: 'BUY' | 'SELL'; quantity: number; price: number; fees: number | null }
type Skip = { line: number; reason: string }
export type Own = {
  trades: Trade[]; currency: string; currencies: string[]; active: boolean; t212: boolean; t212_data: boolean; warnings: string[]
  updated: string | null; example_csv: string; imported?: { added: number; skipped: Skip[]; dates: string }
}
type Found = { symbol: string; name: string; currency: string; price: number | null }

const today = () => {
  const d = new Date()
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10)
}
const errText = (e: unknown) => (e instanceof Error ? e.message : 'Something went wrong')

export function useOwn() {
  return useQuery({ queryKey: ['own'], queryFn: () => api.get<Own>('/api/portfolio/own') })
}

/** The "My trades" tab: add a trade, import a broker's CSV, see and delete what you added. */
export function OwnTrades() {
  const qc = useQueryClient()
  const toast = useToast()
  const own = useOwn()
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [form, setForm] = useState({ date: today(), symbol: '', side: 'BUY', quantity: '', price: '', fees: '' })
  const [found, setFound] = useState<Found | null>(null)
  const [importOpen, setImportOpen] = useState(false)
  const [csv, setCsv] = useState('')
  const [result, setResult] = useState<Own['imported'] | null>(null)

  const after = (d: Own) => {
    qc.setQueryData(['own'], d)
    for (const k of ['portfolio', 'pf', 'tax']) void qc.invalidateQueries({ queryKey: [k] })
  }
  const run = async (fn: () => Promise<Own>) => {
    setBusy(true)
    setErr('')
    try {
      const d = await fn()
      after(d)
      return d
    } catch (e) {
      setErr(errText(e))
      return null
    } finally {
      setBusy(false)
    }
  }
  const look = async () => {
    const s = form.symbol.trim()
    setFound(null)
    if (!s) return
    try {
      setFound(await api.get<Found>(`/api/portfolio/own/lookup?q=${encodeURIComponent(s)}`))
      setErr('')
    } catch (e) {
      setErr(errText(e))
    }
  }
  const add = async () => {
    const d = await run(() => api.post<Own>('/api/portfolio/own/trades', { ...form, price: form.price || null, fees: form.fees || null }))
    if (d) {
      toast(`${form.side === 'BUY' ? 'Buy' : 'Sale'} of ${form.symbol.toUpperCase()} added`)
      setForm({ ...form, symbol: '', quantity: '', price: '', fees: '' })
      setFound(null)
    }
  }
  const doImport = async () => {
    const d = await run(() => api.post<Own>('/api/portfolio/own/import', { csv }))
    if (d) {
      setResult(d.imported)
      setCsv('')
    }
  }
  const saveExample = async () => {
    try {
      const j = await api.post<{ path: string }>('/api/export', { kind: 'csv', name: 'My trades example', data: own.data?.example_csv ?? '' })
      toast(<>Saved: {j.path} <button type="button" className="font-semibold underline" onClick={() => void api.post('/api/reveal', { path: j.path }).catch(() => {})}>Show in folder</button></>)
    } catch (e) {
      toast('Could not save: ' + errText(e))
    }
  }

  const d = own.data
  if (own.isLoading) return <Card><Empty>Loading…</Empty></Card>
  if (!d) return <Card><Empty>{own.error ? errText(own.error) : 'Could not load your trades.'}</Empty></Card>
  if (d.t212) return <Card><Empty>Trading 212 is connected, so the portfolio shows your Trading 212 account. Disconnect it in Settings to use your own trades instead (they are kept).</Empty></Card>

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value })
  const cur = found?.currency
  return (
    <div className="flex flex-col gap-4">
      {d.t212_data && d.trades.length > 0 && (
        <Card className="flex flex-wrap items-center gap-3 p-4 text-[13px]">
          <span className="grow">The portfolio still shows your last Trading 212 sync. Switch to the trades below?</span>
          <Button variant="default" disabled={busy} onClick={() => void run(() => api.post<Own>('/api/portfolio/own/rebuild'))}>Use my own trades</Button>
        </Card>
      )}
      <Card className="p-4">
        <H2
          side={
            <>
              <Hint label="The currency your totals, returns and chart are in. The € / $ switch shows the other one.">
                <span>
                  <Segmented label="Portfolio currency" value={d.currency} onChange={(v) => void run(() => api.put<Own>('/api/portfolio/own/currency', { currency: v }))} options={d.currencies.map((c) => ({ value: c, label: c }))} />
                </span>
              </Hint>
              <Button onClick={() => { setResult(null); setImportOpen(true) }}><Upload /> Import CSV…</Button>
            </>
          }
        >
          Add a trade
        </H2>
        <form
          className="grid grid-cols-2 items-end gap-3 sm:grid-cols-3 lg:grid-cols-[9.5rem_minmax(9rem,1fr)_auto_7rem_8rem_7rem_auto]"
          onSubmit={(e) => {
            e.preventDefault()
            void add()
          }}
        >
          <Field label="Date"><Input type="date" max={today()} value={form.date} onChange={set('date')} required /></Field>
          <Field label="Ticker" hint="Yahoo's symbol: AAPL, ASML.AS (Amsterdam), VWRL.L (London), SAP.DE (Xetra). An ISIN works too.">
            <Input value={form.symbol} onChange={(e) => { setForm({ ...form, symbol: e.target.value }); setFound(null) }} onBlur={() => void look()} placeholder="AAPL" spellCheck={false} required />
          </Field>
          <Field label="Buy or sell">
            <Segmented label="Buy or sell" value={form.side} onChange={(v) => setForm({ ...form, side: v })} options={[{ value: 'BUY', label: 'Buy' }, { value: 'SELL', label: 'Sell' }]} />
          </Field>
          <Field label="Shares"><Input type="number" inputMode="decimal" min="0" step="any" value={form.quantity} onChange={set('quantity')} placeholder="10" required /></Field>
          <Field label={`Price${cur ? ` (${cur})` : ''}`} hint="Per share, in the currency the share trades in. Leave empty to use that day's closing price.">
            <Input type="number" inputMode="decimal" min="0" step="any" value={form.price} onChange={set('price')} placeholder="day's close" />
          </Field>
          <Field label={`Fees${cur ? ` (${cur})` : ''}`}><Input type="number" inputMode="decimal" min="0" step="any" value={form.fees} onChange={set('fees')} placeholder="0" /></Field>
          <Button type="submit" variant="default" disabled={busy}>{busy ? <Loader2 className="animate-spin" /> : <Plus />} Add</Button>
        </form>
        <div className="mt-2 min-h-5 text-[13px]">
          {err ? <span className="text-down">{err}</span> : found ? <span className="text-muted-foreground">{found.name} · {found.symbol} · {found.currency}{found.price ? ` · now ${sharePrice(found.price, found.currency)}` : ''}</span> : null}
        </div>
      </Card>

      <Card className="min-w-0 overflow-x-auto">
        {d.trades.length ? (
          <table className="tbl sticky-first">
            <thead>
              <tr><th>Holding</th><th>Date</th><th>Side</th><th>Shares</th><th>Price</th><th>Fees</th><th aria-label="Delete" /></tr>
            </thead>
            <tbody>
              {d.trades.map((t) => (
                <tr key={t.id}>
                  <td className="max-w-72">
                    <div className="flex items-center gap-2.5">
                      <Logo t={t.symbol.includes('.') ? t.symbol : t.symbol.replace('-', '.')} size={24} keep />
                      <div className="min-w-0">
                        <div className="truncate font-medium">{t.name || t.symbol}</div>
                        <div className="text-xs text-muted-foreground">{t.symbol}</div>
                      </div>
                    </div>
                  </td>
                  <td>{day(t.date)}</td>
                  <td className={cn('font-medium', t.side === 'BUY' ? 'text-up' : 'text-down')}>{t.side === 'BUY' ? 'Buy' : 'Sell'}</td>
                  <td>{qty(t.quantity)}</td>
                  <td>{sharePrice(t.price, t.currency)}</td>
                  <td>{t.fees ? sharePrice(t.fees, t.currency) : '–'}</td>
                  <td className="w-10">
                    <Hint label="Delete this trade">
                      <Button size="icon" variant="ghost" aria-label="Delete this trade" disabled={busy} onClick={() => void run(() => api.del<Own>(`/api/portfolio/own/trades/${t.id}`))}><Trash2 /></Button>
                    </Hint>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty className="min-h-40">No trades yet. Add your first buy above, or import the CSV export of your broker.</Empty>
        )}
      </Card>
      <p className="text-xs text-muted-foreground">
        Your trades stay on this computer. Prices, splits, exchange rates and dividends come from Yahoo: dividends are worked out from the shares you held before each ex-dividend date, before tax. Enter shares as you bought them; later splits are applied for you. Cash is not tracked.
        {d.updated && ` Prices updated ${d.updated}.`}
        {!!d.warnings.length && <span className="block text-down">{d.warnings.join(' · ')}</span>}
      </p>

      <Dialog open={importOpen} onOpenChange={(o) => !o && setImportOpen(false)}>
        <DialogContent className="max-w-lg p-5">
          <DialogTitle className="text-base font-semibold">Import trades from a CSV file</DialogTitle>
          <DialogDescription className="mt-2 text-[13px] text-muted-foreground">
            Use your broker's export, or make your own file. It needs a date, a ticker (or ISIN), buy or sell and the number of shares; a price, currency and fees are used when they are there. Other lines (dividends, deposits) are skipped, and so are trades you already have, so you can import the same file again.
          </DialogDescription>
          {result ? (
            <div className="mt-3 text-[13px]">
              <p className="font-medium">{result.added === 1 ? '1 trade added.' : `${result.added} trades added.`} <span className="font-normal text-muted-foreground">Dates read as {result.dates}.</span></p>
              {!!result.skipped.length && (
                <div className="mt-2 max-h-48 overflow-y-auto rounded-md border bg-panel p-2 text-xs text-muted-foreground">
                  {result.skipped.length} {result.skipped.length === 1 ? 'line' : 'lines'} skipped:
                  {result.skipped.slice(0, 200).map((s) => <div key={s.line}>Line {s.line}: {s.reason}</div>)}
                </div>
              )}
              <div className="mt-3 flex justify-end"><Button variant="default" onClick={() => setImportOpen(false)}>Done</Button></div>
            </div>
          ) : (
            <>
              <textarea value={csv} onChange={(e) => setCsv(e.target.value)} rows={7} placeholder={'date,ticker,side,shares,price,currency,fees\n2024-03-15,AAPL,buy,10,172.50,USD,1'} spellCheck={false} className="mt-3 w-full rounded-md border bg-card p-2 font-mono text-xs outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30" />
              {err && <p className="mt-1 text-[13px] text-down">{err}</p>}
              <div className="mt-3 flex flex-wrap gap-2">
                <Button asChild>
                  <label className="cursor-pointer">
                    <FolderOpen /> Choose file…
                    <input type="file" accept=".csv,.txt,text/csv,text/plain" hidden onChange={(e) => void e.target.files?.[0]?.text().then(setCsv)} />
                  </label>
                </Button>
                <Button variant="ghost" onClick={() => void saveExample()}><Download /> Example file</Button>
                <div className="grow" />
                <Button onClick={() => setImportOpen(false)}>Cancel</Button>
                <Button variant="default" disabled={!csv.trim() || busy} onClick={() => void doImport()}>{busy && <Loader2 className="animate-spin" />} Import</Button>
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="flex min-w-0 flex-col gap-1 text-xs font-medium text-muted-foreground">
      {hint ? <Hint label={hint}><span className="w-fit cursor-help underline decoration-dotted underline-offset-2">{label}</span></Hint> : label}
      {children}
    </label>
  )
}
