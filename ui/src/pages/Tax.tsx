import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, FileSpreadsheet, FileText, Landmark } from 'lucide-react'
import { useState } from 'react'
import type { ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Card } from '@/components/app/Page'
import { Empty, H2, Kv } from '@/components/app/bits'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Segmented } from '@/components/ui/segmented'
import { Switch } from '@/components/ui/switch'
import { TabLinks } from '@/components/ui/tabs'
import { api } from '@/lib/api'
import { tone } from '@/lib/format'
import { useSettings } from '@/lib/settings'
import { useToast } from '@/lib/toast'
import { cn } from '@/lib/utils'
import { SyncHistory, cash, pctTxt } from '@/pages/portfolio/common'

// Box 3 per year with that year's rules (forfait + tegenbewijs up to 2028, vermogenswinstbelasting from 2029), see
// stockterminal/tax.py. What you hold outside Trading 212 is kept in your settings (prefs group "tax", the same keys as
// the old screen), never sent anywhere.

type Rule = {
  system: 'forfait' | 'gains'; status: string; note: string
  other?: number; bank?: number; debt?: number; exempt?: number; rate?: number
  brackets?: [number | null, number][]; exempt_result?: number; loss_threshold?: number
}
type Year = {
  year: number; complete: boolean; start_value: number; end_value: number; money_in: number; cash_start: number; cash_end: number
  dividends: { net: number; gross: number; withheld: number; unknown_gross: number }
  dividends_by_country: { country: string; gross: number; withheld: number; net: number }[]
  interest: number; realised: { name: string; amount: number }[]; realised_total: number; actual: number; actual_pct: number | null
}
type Tax = {
  empty: boolean; connected?: boolean; reason?: string; account_currency: string; rules: Record<string, Rule>; status: Record<string, string>; years: Record<string, Year>
  today: string; checked: string; upcoming?: { date: string; text: string }[]; cash_check: { rebuilt: number; trading212: number | null }
  value_now: number; cost_now: number
}
type Block = { title: string; rows: [string, number, boolean?][]; notes: string[] }

const pct = (v: number, n = 2) => (v * 100).toFixed(n).replace(/\.?0+$/, '') + '%'
const STATUS_CLS: Record<string, string> = { final: 'bg-up-bg text-up', provisional: 'bg-primary/10 text-primary', law: 'bg-primary/10 text-primary', assumed: 'bg-down-bg text-down', proposal: 'bg-down-bg text-down' }
const Bold = ({ children }: { children: ReactNode }) => <span className="font-semibold text-foreground">{children}</span>

export function TaxPage() {
  const { year: yearArg } = useParams()
  const toast = useToast()
  const [saving, setSaving] = useState(false)
  const [blocks, setBlocks] = useState<Block[]>([])
  const q = useQuery({ queryKey: ['tax'], queryFn: () => api.get<Tax>('/api/tax') })
  const d = q.data
  const back = <Button asChild variant="ghost" size="sm" className="-ml-2 self-start"><Link to="/portfolio"><ArrowLeft /> Portfolio</Link></Button>
  const shell = (children: ReactNode) => (
    <main className="flex flex-col gap-5 p-6">
      {back}
      <div>
        <h1 className="text-[22px] font-bold tracking-tight">Tax (box 3)</h1>
        <p className="mt-1 text-[13px] text-muted-foreground">Your Trading 212 numbers per year, worked out with that year's box 3 rules. An aid, not tax advice: the Belastingdienst decides.</p>
      </div>
      {children}
    </main>
  )
  if (q.isLoading || !d) return shell(<Card><Empty>Loading…</Empty></Card>)
  if (d.empty)
    return shell(
      <Card>
        <Empty className="min-h-64">
          <span className="mb-1 flex size-11 items-center justify-center rounded-full bg-secondary text-primary"><Landmark className="size-5" /></span>
          {d.connected ? (
            d.reason === 'no_prices' ? (
              <span>Your trades are in, but the price history of your holdings isn't downloaded yet. Open the Portfolio page once (Performance) and come back.</span>
            ) : (
              <SyncHistory what="your yearly numbers" />
            )
          ) : (
            <>
              <span>Connect Trading 212 first, then your yearly numbers appear here.</span>
              <Button asChild variant="default" className="mt-2"><Link to="/settings?find=t212">Connect Trading 212</Link></Button>
            </>
          )}
        </Empty>
      </Card>,
    )

  const acc = d.account_currency
  const m = (v: number) => cash(v, acc)
  const ms = (v: number) => (v > 0 ? '+' : v < 0 ? '−' : '') + cash(Math.abs(v), acc)
  const first = Math.min(...Object.keys(d.years).map(Number))
  const years = Object.keys(d.rules).map(Number).filter((y) => y >= first)
  const thisYear = +d.today.slice(0, 4)
  const y = years.includes(Number(yearArg)) ? Number(yearArg) : years.includes(thisYear) ? thisYear : years[years.length - 1]
  const R = d.rules[y]
  const Y = d.years[y]
  const checkedOld = (Date.now() - +new Date(d.checked + 'T12:00:00')) / 864e5 > 92
  const nextUp = (d.upcoming || []).find((u) => u.date >= d.today.slice(0, 7))
  const ruleLine =
    R.system === 'forfait'
      ? `Investments ${pct(R.other!)} · savings ${pct(R.bank!)} · tax-free ${m(R.exempt!)} per person · rate ${pct(R.rate!, 1)}`
      : `Tax on realised gains, dividend and interest · tax-free result ${m(R.exempt_result!)} per person · ${R.brackets!.map(([lim, r]) => (lim ? `${pct(r, 1)} up to ${m(lim)}` : `${pct(r, 1)} above`)).join(', ')}`

  const save = async (format: 'pdf' | 'csv') => {
    setSaving(true)
    try {
      const j = await api.post<{ path: string }>('/api/tax/export', { year: y, format, page: { blocks } })
      toast(<>Saved: {j.path} <button type="button" className="font-semibold underline" onClick={() => void api.post('/api/reveal', { path: j.path }).catch(() => {})}>Show in folder</button></>)
    } catch (e) {
      toast(`Could not save: ${e instanceof Error ? e.message : e}`)
    } finally {
      setSaving(false)
    }
  }

  return shell(
    <>
      <TabLinks label="Tax year" tabs={years.map((x) => ({ to: `/tax/${x}`, label: `${x}${d.rules[x].system === 'gains' ? ' (new rules)' : ''}` }))} />
      <Card className="p-4">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-[15px] font-semibold">{y}: {R.system === 'forfait' ? 'fictitious return (forfait) with tegenbewijs' : 'capital gains tax (vermogenswinstbelasting)'}</h2>
          <span className={cn('rounded-md px-2 py-0.5 text-xs font-medium', STATUS_CLS[R.status])}>{d.status[R.status]}</span>
          <div className="grow" />
          <Button size="sm" disabled={saving} onClick={() => void save('pdf')}><FileText /> Save as PDF</Button>
          <Button size="sm" disabled={saving} onClick={() => void save('csv')}><FileSpreadsheet /> Save as CSV</Button>
        </div>
        <p className="mt-2 text-[13px]">{ruleLine}</p>
        <p className="mt-1 text-xs text-muted-foreground">{R.note}</p>
        <p className={cn('mt-2 text-xs text-muted-foreground', checkedOld && 'text-down')}>
          Rules checked on {new Date(d.checked + 'T12:00:00').toLocaleDateString('en-US', { day: 'numeric', month: 'long', year: 'numeric' })}
          {checkedOld && ' · more than 3 months ago: the figures may be outdated'}
          {nextUp && ` · next expected: ${nextUp.text} (${new Date(nextUp.date + '-15').toLocaleDateString('en-US', { month: 'long', year: 'numeric' })})`}
        </p>
      </Card>
      {R.system === 'forfait' ? (
        Y ? <Forfait key={y} d={d} y={y} R={R} Y={Y} m={m} ms={ms} onBlocks={setBlocks} /> : <Card><Empty>No Trading 212 data for {y} yet.</Empty></Card>
      ) : (
        <Gains key={y} d={d} y={y} R={R} m={m} ms={ms} onBlocks={setBlocks} />
      )}
    </>,
  )
}

type Fns = { m: (v: number) => string; ms: (v: number) => string; onBlocks: (b: Block[]) => void }

function Forfait({ d, y, R, Y, m, ms, onBlocks }: { d: Tax; y: number; R: Rule; Y: Year } & Fns) {
  const { pref, setPref } = useSettings()
  const savings = pref<number>('tax', `${y}_savings`, 0)
  const invest = pref<number>('tax', `${y}_invest`, 0)
  const partner = pref<boolean>('tax', 'partner', false)
  const cashAs = pref<string>('tax', 'cash_as', 'bank')
  const persons = partner ? 2 : 1
  const bank = savings + (cashAs === 'bank' ? Y.cash_start : 0)
  const other = invest + Y.start_value + (cashAs === 'other' ? Y.cash_start : 0)
  const base = bank + other
  const R0 = bank * R.bank! + other * R.other!
  const exempt = R.exempt! * persons
  const taxable = Math.max(0, base - exempt)
  const benefit = base > 0 ? (R0 * taxable) / base : 0
  const tax = benefit * R.rate!
  const forf = Y.start_value * R.other! + Y.cash_start * (cashAs === 'bank' ? R.bank! : R.other!)
  const share = R0 > 0 ? forf / R0 : 0
  const lower = Y.actual < forf
  const end = Y.complete ? 'on 31 Dec' : 'today'
  const blocks: Block[] = [
    {
      title: `Box 3 on the forfait, ${y} (with what you entered on the page)`,
      rows: [
        ['Trading 212 holdings on 1 Jan', Y.start_value], ['Trading 212 cash on 1 Jan', Y.cash_start], ['Savings (bank) outside Trading 212', savings], ['Other investments outside Trading 212', invest],
        ['Box 3 total', base, true], [`Tax-free (${persons === 2 ? '2 × ' : ''}${m(R.exempt!)})`, -exempt], [`Fictitious return (${base ? pct(R0 / base) : '–'} on average)`, R0],
        ['Taxable return (benefit)', benefit], [`Box 3 tax (${pct(R.rate!, 1)})`, tax, true], ['of which from your Trading 212 account', tax * share],
      ],
      notes: [`${persons === 2 ? 'With a fiscal partner: amounts are for both of you.' : 'Without a fiscal partner.'} Trading 212 cash counted as ${cashAs === 'bank' ? 'savings (bank)' : 'investments'}. Debts are not included.`],
    },
    {
      title: 'Actual return of this account vs the forfait (tegenbewijs)',
      rows: [
        ['Holdings on 1 Jan', Y.start_value], [`Holdings ${end}`, Y.end_value], ['Bought minus sold', Y.money_in], ['Dividends (gross) and interest', Y.dividends.gross + Y.interest],
        [`Actual return${Y.actual_pct !== null ? ` (about ${pctTxt(Y.actual_pct)})` : ''}`, Y.actual, true], ['Forfait on this account', forf, true],
      ],
      notes: [
        !Y.complete ? 'The year is not over: these are the numbers up to today.' : lower ? 'The actual return on this account was lower than the forfait. The tegenbewijsregeling compares your whole box 3 (savings and other investments too), not just this account.' : 'The actual return on this account was higher than the forfait.',
        'Actual return = value at the end - value at the start - bought + sold + dividends (gross) + interest; unrealised gains count.',
      ],
    },
  ]
  const key = JSON.stringify(blocks)
  useBlocks(key, blocks, onBlocks)
  const Dv = Y.dividends
  return (
    <>
      <div className="grid items-start gap-4 lg:grid-cols-2">
        <Card className="p-4">
          <H2>Outside Trading 212 on 1 January {y}</H2>
          <div className="grid gap-3 sm:grid-cols-2">
            <MoneyField label="Savings (bank)" value={savings} onChange={(v) => setPref('tax', `${y}_savings`, v)} />
            <MoneyField label="Other investments" value={invest} onChange={(v) => setPref('tax', `${y}_invest`, v)} />
          </div>
          <label className="mt-4 flex items-center gap-3 text-[13px]">
            <Switch checked={partner} onCheckedChange={(v) => setPref('tax', 'partner', v)} aria-label="Fiscal partner" />
            Fiscal partner <span className="text-muted-foreground">(the amounts above are for both of you)</span>
          </label>
          <div className="mt-4 flex flex-wrap items-center gap-3 text-[13px]">
            Cash at Trading 212 counts as
            <Segmented label="Cash at Trading 212 counts as" value={cashAs} onChange={(v) => setPref('tax', 'cash_as', v)} options={[{ value: 'bank', label: 'Savings' }, { value: 'other', label: 'Investments' }]} />
          </div>
          <p className="mt-4 text-xs text-muted-foreground">Kept on this computer only. Ask the Belastingdienst or check your annual statement if you are unsure how your broker cash counts.</p>
        </Card>
        <Card className="p-4">
          <H2>Box 3 on the forfait, {y}</H2>
          <Kv label="Trading 212 holdings on 1 Jan">{m(Y.start_value)}</Kv>
          <Kv label="Trading 212 cash on 1 Jan">{m(Y.cash_start)}</Kv>
          <Kv label="Savings + other investments">{m(savings + invest)}</Kv>
          <Kv label="Box 3 total"><Bold>{m(base)}</Bold></Kv>
          <Kv label={`Tax-free (${persons === 2 ? '2 × ' : ''}${m(R.exempt!)})`}>− {m(exempt)}</Kv>
          <Kv label={`Fictitious return (${base ? pct(R0 / base) : '–'} on average)`}>{m(R0)}</Kv>
          <Kv label="Taxable return (benefit)">{m(benefit)}</Kv>
          <Kv label={`Box 3 tax (${pct(R.rate!, 1)})`}><span className="text-[15px] font-bold">{m(tax)}</span></Kv>
          <Kv label="of which from your Trading 212 account">{m(tax * share)}</Kv>
        </Card>
      </div>
      <div className="grid items-start gap-4 lg:grid-cols-2">
        <Card className="p-4">
          <H2>Actual return vs the forfait</H2>
          <Kv label="Holdings on 1 Jan">{m(Y.start_value)}</Kv>
          <Kv label={`Holdings ${end}`}>{m(Y.end_value)}</Kv>
          <Kv label="Bought minus sold">{ms(Y.money_in)}</Kv>
          <Kv label="Dividends (gross) and interest">{m(Y.dividends.gross + Y.interest)}</Kv>
          <Kv label="Actual return"><span className={cn('font-semibold', tone(Y.actual))}>{ms(Y.actual)}</span>{Y.actual_pct !== null && <span className="font-normal text-muted-foreground"> (about {pctTxt(Y.actual_pct)})</span>}</Kv>
          <Kv label="Forfait on this account"><Bold>{m(forf)}</Bold></Kv>
          <div className={cn('mt-3 rounded-lg px-3 py-2.5 text-[13px]', lower && Y.complete ? 'bg-up-bg' : 'bg-secondary')}>
            {!Y.complete ? (
              'The year is not over: compare again after 31 December.'
            ) : lower ? (
              <>Your actual return on this account was <b>lower</b> than the forfait. With the tegenbewijsregeling you can then be taxed on the actual return instead. Note: that compares your <i>whole</i> box 3 (savings and other investments too), not just this account.</>
            ) : (
              'Your actual return on this account was higher than the forfait, so the forfait is the lower of the two here.'
            )}
          </div>
          <p className="mt-2 text-xs text-muted-foreground">Actual return = value at the end − value at the start − bought + sold + dividends (gross) + interest; unrealised gains count, as in the tegenbewijsregeling.</p>
        </Card>
        <Card className="min-w-0 p-4">
          <H2>Dividends {y}</H2>
          {Dv.gross ? (
            <>
              <table className="tbl tight">
                <thead><tr><th>From</th><th>Gross</th><th>Tax withheld</th><th>Received</th></tr></thead>
                <tbody>
                  {Y.dividends_by_country.map((r) => <tr key={r.country}><td>{r.country}</td><td>{m(r.gross)}</td><td>{r.withheld > 0.005 ? m(r.withheld) : '–'}</td><td>{m(r.net)}</td></tr>)}
                  <tr className="font-semibold"><td>Total</td><td>{m(Dv.gross)}</td><td>{m(Dv.withheld)}</td><td>{m(Dv.net)}</td></tr>
                </tbody>
              </table>
              <p className="mt-2 text-xs text-muted-foreground">
                Withheld tax = gross − what Trading 212 paid you, converted at that day's rate. Dutch dividend tax and foreign withholding tax can usually be settled in your tax return; you need these amounts for that.
                {Dv.unknown_gross > 0.01 && ` For ${m(Dv.unknown_gross)} the gross amount is unknown: counted as received.`}
              </p>
            </>
          ) : (
            <p className="text-[13px] text-muted-foreground">No dividends in {y}.</p>
          )}
        </Card>
      </div>
      <Card className="p-4">
        <H2 side={Y.realised.length ? <span className={cn('text-[15px] font-semibold', tone(Y.realised_total))}>{ms(Y.realised_total)}</span> : undefined}>Realised gains {y}</H2>
        {Y.realised.length ? (
          <>
            <p className="mb-2 text-xs text-muted-foreground">From shares you sold (Trading 212's own figure). Not taxed separately under the forfait; from 2029 this is what box 3 taxes.</p>
            <div className="grid gap-x-8 md:grid-cols-2">{Y.realised.map((r) => <Kv key={r.name} label={r.name}><span className={tone(r.amount)}>{ms(r.amount)}</span></Kv>)}</div>
          </>
        ) : (
          <p className="text-[13px] text-muted-foreground">Nothing sold in {y}.</p>
        )}
      </Card>
      <p className="text-xs text-muted-foreground">
        Values from your synced Trading 212 history: holdings at the last close of the year before (the 1 January peildatum), Trading 212 cash rebuilt from every booking (now {m(d.cash_check.rebuilt)}
        {d.cash_check.trading212 !== null && d.cash_check.trading212 !== undefined && `; Trading 212 says ${m(d.cash_check.trading212)}`}). Check them against Trading 212's annual statement before you file.
      </p>
    </>
  )
}

function Gains({ d, y, R, m, ms, onBlocks }: { d: Tax; y: number; R: Rule } & Fns) {
  const { pref, setPref } = useSettings()
  const ys = Object.keys(d.years).map(Number).sort()
  const want = Number(pref<string>('state', 'taxBase', ''))
  const base = d.years[want] ? want : ys.filter((x) => d.years[x].complete).pop() || ys[ys.length - 1]
  const Y = d.years[base]
  const persons = pref<boolean>('tax', 'partner', false) ? 2 : 1
  const result = Y.realised_total + Y.dividends.gross + Y.interest
  const taxable = result - R.exempt_result! * persons
  let tax = 0
  let rest = Math.max(0, taxable)
  let lowerPart = 0
  for (const [lim, r] of R.brackets!) {
    const part = lim === null ? rest : Math.min(rest, lim * persons - lowerPart)
    if (part <= 0) break
    tax += part * r
    rest -= part
    lowerPart += part
    if (lim === null || rest <= 0) break
  }
  const blocks: Block[] = [
    {
      title: `Example: ${y} rules (proposal) on your ${base} numbers`,
      rows: [['Realised gains (shares sold)', Y.realised_total], ['Dividends (gross)', Y.dividends.gross], ['Interest', Y.interest], ['Result', result, true], [`Tax-free result (${persons === 2 ? '2 × ' : ''}${m(R.exempt_result!)})`, -R.exempt_result! * persons], ['Estimated box 3 tax', tax, true]],
      notes: ["Unrealised gains are not taxed: only when you sell. What you own on 1 January 2029 gets that day's value as purchase price.", 'Savings interest and investments outside Trading 212 are not included.'],
    },
  ]
  useBlocks(JSON.stringify(blocks), blocks, onBlocks)
  const unreal = d.value_now - d.cost_now
  return (
    <div className="grid items-start gap-4 lg:grid-cols-2">
      <Card className="p-4">
        <H2 side={<Segmented label="Which year's numbers" value={String(base)} onChange={(v) => setPref('state', 'taxBase', v)} options={ys.map((x) => ({ value: String(x), label: String(x) }))} />}>Example: {y} rules on your {base} numbers</H2>
        <Kv label="Realised gains (shares sold)"><span className={tone(Y.realised_total)}>{ms(Y.realised_total)}</span></Kv>
        <Kv label="Dividends (gross)">{m(Y.dividends.gross)}</Kv>
        <Kv label="Interest">{m(Y.interest)}</Kv>
        <Kv label="Result"><Bold>{ms(result)}</Bold></Kv>
        <Kv label={`Tax-free result (${persons === 2 ? '2 × ' : ''}${m(R.exempt_result!)})`}>− {m(R.exempt_result! * persons)}</Kv>
        <Kv label="Estimated box 3 tax"><span className="text-[15px] font-bold">{m(tax)}</span></Kv>
        {taxable < 0 && result < 0 && <p className="mt-2 text-xs text-muted-foreground">A loss: under the proposal it can be set off against later years (the part above {m(R.loss_threshold!)} a year).</p>}
        <p className="mt-2 text-xs text-muted-foreground">Unrealised gains are not taxed here: only when you sell. Your own savings interest and other investments outside Trading 212 would count too. Dividend tax withheld ({m(Y.dividends.withheld)} in {base}) can usually still be settled.</p>
      </Card>
      <Card className="p-4">
        <H2>What changes for you</H2>
        <Kv label="Your holdings now">{m(d.value_now)}</Kv>
        <Kv label="Unrealised gain now"><span className={tone(unreal)}>{ms(unreal)}</span></Kv>
        <div className="mt-3 rounded-lg bg-secondary px-3 py-2.5 text-[13px]">
          Under the proposal, what you own on <b>1 January 2029</b> gets that day's value as its purchase price. Gains made before then (like the {ms(unreal)} you have now) are not taxed in box 3, also not when you sell later. From 2029 you pay when you sell at a gain, and on dividend and interest each year.
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          {R.note} Rates: {R.brackets!.map(([lim, r]) => (lim ? `${pct(r, 1)} up to ${m(lim)} result per person` : `${pct(r, 1)} above`)).join(', ')} (the split per person is an assumption of this page).
        </p>
      </Card>
    </div>
  )
}

/** Hands what the year shows to the page, for the PDF / CSV export; only when it changed. */
function useBlocks(key: string, blocks: Block[], onBlocks: (b: Block[]) => void) {
  const [last, setLast] = useState('')
  if (last !== key) {
    setLast(key)
    queueMicrotask(() => onBlocks(blocks))
  }
}

/** An amount in euros that saves when you leave the field (or press Enter), not on every key. */
function MoneyField({ label, value, onChange }: { label: string; value: number; onChange: (v: number) => void }) {
  const [text, setText] = useState(String(value || ''))
  const commit = () => onChange(Math.max(0, Number(text.replace(/[^\d.]/g, '')) || 0))
  return (
    <label className="flex flex-col gap-1 text-[13px] text-muted-foreground">
      {label}
      <span className="relative">
        <span className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-foreground">€</span>
        <Input inputMode="decimal" value={text} placeholder="0" onChange={(e) => setText(e.target.value)} onBlur={commit} onKeyDown={(e) => e.key === 'Enter' && commit()} className="num pl-6 text-foreground" />
      </span>
    </label>
  )
}
