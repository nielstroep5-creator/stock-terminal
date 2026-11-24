import { useQuery } from '@tanstack/react-query'
import { Minus, Plus, RotateCcw } from 'lucide-react'
import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Card } from '@/components/app/Page'
import { Empty, H2, Kv, Stat } from '@/components/app/bits'
import { BAR_FIT, BAR_TIP, EChart, FOCUS_SELF, STRIPES, baseOption, useChartColors } from '@/components/chart/EChart'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Segmented } from '@/components/ui/segmented'
import { Switch } from '@/components/ui/switch'
import { Hint } from '@/components/ui/tooltip'
import { api } from '@/lib/api'
import { big, day, fmt, money, pctSigned, tone } from '@/lib/format'
import { useSettings } from '@/lib/settings'
import { cn } from '@/lib/utils'

type N = number | null | undefined
type Year = { fy: number; end: string; fcf?: N; revenue?: N; sbc?: N }
export type DcfData = {
  ok: boolean
  reason?: string
  kind?: 'bank' | 'insurer' | null
  price?: N
  price_date?: string | null
  market_cap?: N
  shares?: N
  debt: number
  cash: number
  minority: number
  net_debt: number
  ttm: { fcf?: N; revenue?: N; sbc?: N; from_fy?: number }
  history: Year[]
  cagr: { revenue?: N; fcf?: N }
  revenue_est: { fy: number; growth: number }[]
  ltg?: N
}

type Start = 'ttm' | 'avg3' | 'own'
/** What one stock remembers (group "dcf", key = ticker): only what was changed, the rest follows the defaults. */
type Saved = { start?: Start; own?: number; sbc?: boolean; g1?: number; g2?: number }
type Inputs = { base: number; g1: number; g2: number; gt: number; r: number }

const YEARS = 10
const STAGE1 = 5
const RATE = 0.09
const TERMINAL = 0.025
const r1 = (x: number) => Math.round(x * 1000) / 1000
const usd = (v: N) => (typeof v === 'number' && Number.isFinite(v) ? (v < 0 ? '−$' : '$') + big(Math.abs(v)) : '–')

/** Growth of year i (1-based): stage 1, then stage 2. */
const growthOf = (x: Inputs, i: number) => (i <= STAGE1 ? x.g1 : x.g2)

/** The whole model: free cash flow per year, its value today, the terminal value and the value per share. */
function run(x: Inputs, netDebt: number, shares: number) {
  const flows: { fcf: number; pv: number }[] = []
  let f = x.base
  let pv = 0
  for (let i = 1; i <= YEARS; i++) {
    f *= 1 + growthOf(x, i)
    const p = f / (1 + x.r) ** i
    flows.push({ fcf: f, pv: p })
    pv += p
  }
  const tv = (f * (1 + x.gt)) / (x.r - x.gt)
  const pvTv = tv / (1 + x.r) ** YEARS
  const ev = pv + pvTv
  const equity = ev - netDebt
  return { flows, pv, tv, pvTv, ev, equity, perShare: equity / shares }
}

/** Reverse DCF: the one growth rate for all 10 years at which the model gives today's price. */
function impliedGrowth(x: Inputs, netDebt: number, shares: number, price: number): { g: number; edge?: 'low' | 'high' } | null {
  if (x.base <= 0 || x.r <= x.gt) return null
  const at = (g: number) => run({ ...x, g1: g, g2: g }, netDebt, shares).perShare
  let lo = -0.5
  let hi = 1
  if (at(hi) < price) return { g: hi, edge: 'high' }
  if (at(lo) > price) return { g: lo, edge: 'low' }
  for (let k = 0; k < 60; k++) {
    const m = (lo + hi) / 2
    if (at(m) < price) lo = m
    else hi = m
  }
  return { g: (lo + hi) / 2 }
}

/** A percentage box with − and + buttons (steps of half a percent). Typing is kept until you leave the box. */
function PctField({ label, tip, value, onChange, min = -0.5, max = 1, note }: { label: string; tip: string; value: number; onChange: (v: number) => void; min?: number; max?: number; note?: ReactNode }) {
  const [draft, setDraft] = useState<string | null>(null)
  const set = (v: number) => onChange(r1(Math.min(max, Math.max(min, v))))
  const commit = () => {
    if (draft === null) return
    const v = parseFloat(draft.replace(',', '.').replace('%', ''))
    if (Number.isFinite(v)) set(v / 100)
    setDraft(null)
  }
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <Hint label={tip}>
          <span className="cursor-help text-[13px] text-muted-foreground underline decoration-dotted decoration-border underline-offset-4">{label}</span>
        </Hint>
        <div className="grow" />
        <Button size="icon" variant="ghost" className="size-7" aria-label={`${label}: half a percent lower`} onClick={() => set(value - 0.005)}>
          <Minus />
        </Button>
        <div className="relative w-[76px]">
          <Input
            aria-label={label}
            inputMode="decimal"
            className="num h-7 pr-6 text-right"
            value={draft ?? (value * 100).toFixed(1)}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => e.key === 'Enter' && commit()}
          />
          <span className="pointer-events-none absolute top-1/2 right-2 -translate-y-1/2 text-xs text-muted-foreground">%</span>
        </div>
        <Button size="icon" variant="ghost" className="size-7" aria-label={`${label}: half a percent higher`} onClick={() => set(value + 0.005)}>
          <Plus />
        </Button>
      </div>
      {note && <div className="text-xs text-muted-foreground">{note}</div>}
    </div>
  )
}

/** An amount in dollars, typed in millions or billions (whichever fits the company). */
function MoneyField({ value, unit, onChange }: { value: number; unit: 1e6 | 1e9; onChange: (v: number) => void }) {
  const [draft, setDraft] = useState<string | null>(null)
  const commit = () => {
    if (draft === null) return
    const v = parseFloat(draft.replace(',', '.'))
    if (Number.isFinite(v)) onChange(v * unit)
    setDraft(null)
  }
  return (
    <div className="relative w-[120px]">
      <span className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-xs text-muted-foreground">$</span>
      <Input
        aria-label="Your own starting free cash flow"
        inputMode="decimal"
        className="num h-7 pr-7 pl-5 text-right"
        value={draft ?? String(Math.round((value / unit) * 100) / 100)}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === 'Enter' && commit()}
      />
      <span className="pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 text-xs text-muted-foreground">{unit === 1e9 ? 'B' : 'M'}</span>
    </div>
  )
}

/** The growth the stage-1 box starts at, and where it comes from. */
function suggested(d: DcfData): { g: number; why: string } {
  const clamp = (x: number) => Math.round(Math.min(0.25, Math.max(-0.05, x)) * 200) / 200
  const est = d.revenue_est.slice(0, 2)
  if (est.length) {
    const g = est.reduce((a, b) => a + b.growth, 0) / est.length
    return { g: clamp(g), why: `analysts expect revenue ${pctSigned(g, 1)} a year (FY${est[0].fy}${est.length > 1 ? `–FY${est[est.length - 1].fy}` : ''})` }
  }
  if (typeof d.cagr.revenue === 'number') return { g: clamp(d.cagr.revenue), why: `revenue grew ${pctSigned(d.cagr.revenue, 1)} a year over the last 5 years` }
  if (typeof d.cagr.fcf === 'number') return { g: clamp(d.cagr.fcf), why: `free cash flow grew ${pctSigned(d.cagr.fcf, 1)} a year over the last 5 years` }
  return { g: 0.05, why: 'no growth history or estimates, so 5%' }
}

/** Discounted cash flow: what the company is worth if free cash flow grows the way you expect, and the growth today's price assumes. */
export function Dcf({ t, switcher }: { t: string; switcher: ReactNode }) {
  const { pref, setPref } = useSettings()
  const c = useChartColors()
  const q = useQuery({ queryKey: ['company', t, 'dcf'], queryFn: () => api.get<DcfData>(`/api/dcf/${t}?live=1`) })
  const d = q.data

  // discount rate and long-term growth are your own yardsticks: the same for every stock
  const r = pref<number>('ui2', 'dcf_rate', RATE)
  const gt = pref<number>('ui2', 'dcf_terminal', TERMINAL)
  const savedText = pref<string>('dcf', t, '')
  const saved = useMemo<Saved>(() => {
    try {
      return savedText ? (JSON.parse(savedText) as Saved) : {}
    } catch {
      return {}
    }
  }, [savedText])
  const save = (ch: Partial<Saved>) => setPref('dcf', t, JSON.stringify({ ...saved, ...ch }))

  const sug = useMemo(() => (d?.ok ? suggested(d) : null), [d])
  const g1 = saved.g1 ?? sug?.g ?? 0.05
  const g2 = saved.g2 ?? r1(Math.round(((g1 + gt) / 2) * 200) / 200)
  const start: Start = saved.start ?? 'ttm'
  const sbc = saved.sbc ?? false

  const hist = d?.history ?? []
  const last3 = hist.slice(-3)
  const avg = (k: 'fcf' | 'sbc') => (last3.length ? last3.reduce((a, y) => a + (y[k] ?? 0), 0) / last3.length : null)
  const ttmFcf = d?.ttm.fcf ?? null
  const startFcf = start === 'avg3' ? avg('fcf') : start === 'own' ? (saved.own ?? ttmFcf) : ttmFcf
  const sbcAmt = start === 'avg3' ? avg('sbc') : start === 'own' ? 0 : (d?.ttm.sbc ?? null)
  const base = (startFcf ?? 0) - (sbc && sbcAmt ? sbcAmt : 0)
  const unit: 1e6 | 1e9 = Math.abs(ttmFcf ?? 0) >= 1e9 ? 1e9 : 1e6

  const shares = d?.shares ?? 0
  const netDebt = d?.net_debt ?? 0
  const price = d?.price ?? null
  const x: Inputs = { base, g1, g2, gt, r }
  const valid = !!d?.ok && shares > 0 && r > gt
  const m = useMemo(() => (valid ? run(x, netDebt, shares) : null), [valid, base, g1, g2, gt, r, netDebt, shares]) // eslint-disable-line react-hooks/exhaustive-deps
  const implied = useMemo(() => (valid && price ? impliedGrowth(x, netDebt, shares, price) : null), [valid, base, gt, r, netDebt, shares, price]) // eslint-disable-line react-hooks/exhaustive-deps

  const option = useMemo(() => {
    if (!m) return null
    const labels = [...hist.map((y) => `FY${String(y.fy).slice(2)}`), d?.ttm.from_fy ? 'Start' : 'TTM', ...m.flows.map((_, i) => `Yr ${i + 1}`)]
    const past = [...hist.map((y) => (y.fcf ?? 0) - (sbc ? (y.sbc ?? 0) : 0)), base]
    const o = baseOption(c)
    const nPast = past.length
    return {
      ...o,
      grid: { left: 4, right: 8, top: 14, bottom: 0, containLabel: true },
      xAxis: { ...o.xAxis, type: 'category', data: labels, axisLabel: { ...o.xAxis.axisLabel, hideOverlap: true } },
      yAxis: { ...o.yAxis, scale: false, axisLabel: { ...o.yAxis.axisLabel, formatter: (v: number) => usd(v) } },
      tooltip: {
        ...o.tooltip, ...BAR_TIP,
        formatter: (ps: { dataIndex: number }[]) => {
          const i = ps[0].dataIndex
          if (i < nPast) return `<b>${i < hist.length ? `FY${hist[i].fy}` : 'Starting point'}</b><br>Free cash flow${sbc ? ' after stock pay' : ''}: <b>${usd(past[i])}</b>`
          const f = m.flows[i - nPast]
          const g = growthOf(x, i - nPast + 1)
          return `<b>Year ${i - nPast + 1} (projected)</b><br>Free cash flow: <b>${usd(f.fcf)}</b> (${pctSigned(g, 1)})<br>Worth today: <b>${usd(f.pv)}</b>`
        },
      },
      series: [
        { name: 'Reported', type: 'bar', ...BAR_FIT, ...FOCUS_SELF, itemStyle: { color: c.primary, borderRadius: [3, 3, 0, 0] }, data: [...past, ...m.flows.map(() => null)] },
        { name: 'Projected', type: 'bar', ...BAR_FIT, ...FOCUS_SELF, barGap: '-100%', itemStyle: { color: c.primary + 'b3', decal: STRIPES, borderRadius: [3, 3, 0, 0] }, data: [...past.map(() => null), ...m.flows.map((f) => f.fcf)] },
        { name: 'Worth today', type: 'line', symbol: 'circle', symbolSize: 5, silent: true, lineStyle: { width: 1.5, type: 'dashed', color: c.muted }, itemStyle: { color: c.muted }, data: [...past.map(() => null), ...m.flows.map((f) => f.pv)] },
      ],
    }
  }, [m, hist, base, sbc, c, d]) // eslint-disable-line react-hooks/exhaustive-deps

  const head = <div className="flex flex-wrap items-center gap-2">{switcher}</div>
  if (!d) return <>{head}<Card><Empty>{q.isError ? 'Could not load the numbers for the DCF.' : 'Loading…'}</Empty></Card></>
  if (!d.ok) return <>{head}<Card><Empty>No financial statements for {t}, so there is nothing to value.</Empty></Card></>
  if (d.kind)
    return (
      <>
        {head}
        <Card>
          <Empty>
            <b className="text-foreground">A DCF on free cash flow does not fit {d.kind === 'bank' ? 'a bank' : 'an insurer'}.</b>
            {d.kind === 'bank' ? 'For a bank, deposits and loans are the business, so its cash flow says little about what it earns.' : "An insurer's cash flow moves with the premiums it holds until claims are paid, not with what it earns."} Price to book and P/E on the Multiples view fit better.
          </Empty>
        </Card>
      </>
    )

  const upside = m && price ? m.perShare / price - 1 : null
  const changed = Object.keys(saved).length > 0
  const tvShare = m && m.ev > 0 ? m.pvTv / m.ev : null
  const sens = m
    ? [-0.02, -0.01, 0, 0.01, 0.02].map((dr) => ({
        r: r + dr,
        cells: [-0.04, -0.02, 0, 0.02, 0.04].map((dg) => (r + dr > gt ? run({ ...x, r: r + dr, g1: g1 + dg, g2: g2 + dg }, netDebt, shares).perShare : null)),
      }))
    : []

  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        {switcher}
        <div className="grow" />
        {changed && (
          <Button size="sm" variant="ghost" onClick={() => setPref('dcf', t, '')}>
            <RotateCcw /> Back to the suggested numbers
          </Button>
        )}
      </div>

      <Card className="grid grid-cols-2 gap-x-6 gap-y-4 p-4 md:grid-cols-4">
        <Stat label="Fair value per share" tip="What one share is worth if free cash flow grows the way you set it, valued at your discount rate." value={m && base > 0 ? money(m.perShare) : '–'} sub={m && base > 0 ? `equity ${usd(m.equity)}` : 'needs positive cash flow'} />
        <Stat label="Price" value={money(price)} sub={d.price_date ? day(d.price_date) : undefined} />
        <Stat
          label={upside !== null && base > 0 && upside < 0 ? 'Price above fair value' : 'Upside to fair value'}
          tip="Fair value against today's price. Positive: the model says the stock is cheap; negative: expensive."
          value={<span className={tone(upside)}>{base > 0 ? pctSigned(upside, 1) : '–'}</span>}
          sub={upside !== null && base > 0 ? (upside >= 0 ? 'cheaper than your model' : 'dearer than your model') : undefined}
        />
        <Stat
          label="Growth the price assumes"
          tip="Reverse DCF: the one yearly growth of free cash flow, for 10 years, at which the model gives today's price. Same discount rate, long-term growth and starting cash flow."
          value={implied ? (implied.edge === 'high' ? '> 100%' : implied.edge === 'low' ? '< −50%' : pctSigned(implied.g, 1)) : '–'}
          sub={implied ? 'a year for 10 years' : base <= 0 ? 'needs positive cash flow' : undefined}
        />
      </Card>

      <div className="grid gap-5 lg:grid-cols-[340px_minmax(0,1fr)]">
        <Card className="flex flex-col gap-3 p-4">
          <H2 className="mb-0">Your assumptions</H2>
          <div className="flex flex-col gap-2">
            <Hint label="The free cash flow year 1 grows from. Last 12 months, the average of the last 3 fiscal years (smooths out a lumpy year), or your own number.">
              <span className="w-fit cursor-help text-[13px] text-muted-foreground underline decoration-dotted decoration-border underline-offset-4">Starting free cash flow</span>
            </Hint>
            <div className="flex flex-wrap items-center gap-2">
              <Segmented
                label="Starting free cash flow"
                value={start}
                onChange={(v) => save({ start: v, own: v === 'own' ? (saved.own ?? ttmFcf ?? 0) : saved.own })}
                options={[
                  { value: 'ttm', label: d.ttm.from_fy ? `FY${d.ttm.from_fy}` : 'Last 12m' },
                  ...(last3.length === 3 ? [{ value: 'avg3' as const, label: '3Y avg' }] : []),
                  { value: 'own', label: 'Own' },
                ]}
              />
              <div className="grow" />
              {start === 'own' ? <MoneyField value={saved.own ?? ttmFcf ?? 0} unit={unit} onChange={(v) => save({ own: v })} /> : <span className="num text-[13px] font-semibold">{usd(startFcf)}</span>}
            </div>
            {start !== 'own' && (
              <label className="flex items-center gap-2 text-[13px]">
                <Switch checked={sbc} onCheckedChange={(v) => save({ sbc: v })} aria-label="Count stock-based pay as a cost" />
                <Hint label="Free cash flow does not count shares handed to employees as a cost, but they dilute you. On: stock-based pay is taken off the starting cash flow.">
                  <span className="cursor-help underline decoration-dotted decoration-border underline-offset-4">Count stock-based pay as a cost</span>
                </Hint>
                <div className="grow" />
                {sbc && sbcAmt ? <span className="num text-xs text-muted-foreground">−{usd(sbcAmt)}</span> : null}
              </label>
            )}
            {base <= 0 && <p className="text-xs text-flag">The starting free cash flow is not positive, so the model has nothing to grow. Choose Own and type what you expect the company to make.</p>}
          </div>
          <div className="border-t" />
          <PctField
            label="Growth, years 1–5"
            tip="How fast free cash flow grows each year for the first five years."
            value={g1}
            onChange={(v) => save({ g1: v })}
            note={saved.g1 === undefined && sug ? <>Suggested: {sug.why}</> : sug ? <>Suggested {fmt(sug.g, 'pct')}: {sug.why}</> : undefined}
          />
          <PctField label="Growth, years 6–10" tip="Growth usually slows as a company gets bigger. Starts halfway between years 1–5 and the long-term growth." value={g2} onChange={(v) => save({ g2: v })} />
          <div className="border-t" />
          <PctField
            label="Long-term growth"
            tip="Growth every year after year 10, forever. Keep it at or below the economy's long-run growth (2–3%). The same for every stock."
            value={gt}
            min={-0.02}
            max={0.06}
            onChange={(v) => setPref('ui2', 'dcf_terminal', v)}
          />
          <PctField
            label="Discount rate"
            tip="The yearly return you want for the risk. Higher = future cash is worth less today. 8–10% is common for a large company. The same for every stock."
            value={r}
            min={0.03}
            max={0.25}
            onChange={(v) => setPref('ui2', 'dcf_rate', v)}
          />
          {r <= gt && <p className="text-xs text-flag">The discount rate has to be above the long-term growth.</p>}
        </Card>

        <Card className="min-w-0 p-4">
          <H2 side={<span className="text-xs text-muted-foreground">{sbc ? 'Free cash flow after stock-based pay' : 'Free cash flow'} · dashed: worth today</span>}>Free cash flow, past and projected</H2>
          {option ? <EChart option={option} className="h-[300px]" focusPeriod /> : <Empty>Set a discount rate above the long-term growth.</Empty>}
          <div className="mt-3 grid gap-x-6 sm:grid-cols-2">
            <Kv label="Past 5 years, free cash flow" tip="Yearly growth over the last five fiscal years.">{d.cagr.fcf != null ? `${pctSigned(d.cagr.fcf, 1)} a year` : '–'}</Kv>
            <Kv label="Past 5 years, revenue">{d.cagr.revenue != null ? `${pctSigned(d.cagr.revenue, 1)} a year` : '–'}</Kv>
            <Kv label="Analysts, revenue" tip="Revenue growth the analysts expect for the coming fiscal years.">
              {d.revenue_est.length ? d.revenue_est.map((e) => `FY${String(e.fy).slice(2)} ${pctSigned(e.growth, 1)}`).join(' · ') : '–'}
            </Kv>
            <Kv label="Analysts, EPS next 5 years" tip="Earnings per share growth a year the analysts expect over the next five years (Yahoo).">{d.ltg != null ? `${pctSigned(d.ltg, 1)} a year` : '–'}</Kv>
          </div>
        </Card>
      </div>

      {m && base > 0 && (
        <div className="grid gap-5 lg:grid-cols-2">
          <Card className="min-w-0 p-4">
            <H2>How the value adds up</H2>
            <Kv label="Free cash flow, years 1–10" tip="Each year's projected free cash flow, discounted back to today and added up.">{usd(m.pv)}</Kv>
            <Kv label="After year 10" tip="All free cash flow after year 10 (growing at the long-term rate), worth today. Its share of the total says how much rests on the far future.">
              {usd(m.pvTv)} <span className="text-muted-foreground">({fmt(tvShare, 'pct')} of the total)</span>
            </Kv>
            <Kv label="= Value of the business" tip="Enterprise value: what the whole company is worth, before debt and cash.">{usd(m.ev)}</Kv>
            <Kv label="− Debt" tip="Borrowings and finance leases on the latest balance sheet.">{usd(d.debt)}</Kv>
            <Kv label="+ Cash and investments">{usd(d.cash)}</Kv>
            {d.minority ? <Kv label="− Minority interests" tip="The part of subsidiaries that other shareholders own.">{usd(d.minority)}</Kv> : null}
            <Kv label="= Value of the shares">{usd(m.equity)}</Kv>
            <Kv label="÷ Shares" tip="The share count behind the market cap shown in the app.">{big(shares)}</Kv>
            <Kv label="= Fair value per share"><b>{money(m.perShare)}</b></Kv>
          </Card>
          <Card className="min-w-0 overflow-x-auto">
            <div className="px-4 pt-4">
              <H2 side={<span className="text-xs text-muted-foreground">fair value per share</span>}>If you are a bit off</H2>
            </div>
            <table className="tbl">
              <thead>
                <tr>
                  <th>
                    <Hint label="Rows: discount rate. Columns: growth in years 1–5 and 6–10, both moved by the same amount. Green: above today's price.">
                      <span className="cursor-help underline decoration-dotted decoration-border underline-offset-4">Rate \ growth</span>
                    </Hint>
                  </th>
                  {[-0.04, -0.02, 0, 0.02, 0.04].map((dg) => (
                    <th key={dg} className={cn(dg === 0 && 'text-foreground!')}>{fmt(g1 + dg, 'pct')}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sens.map((row, i) => (
                  <tr key={row.r}>
                    <td className={cn(i === 2 && 'font-semibold')}>{fmt(row.r, 'pct')}</td>
                    {row.cells.map((v, j) => (
                      <td key={j} className={cn(price && v !== null && (v >= price ? 'text-up' : 'text-down'), i === 2 && j === 2 && 'bg-accent/60 font-semibold')}>
                        {v === null ? '–' : money(v)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        A simple two-stage discounted cash flow: free cash flow grows at your rates for 10 years and at the long-term rate after that, and every year is
        discounted back to today at your discount rate. Debt, cash and shares come from the latest filing. The numbers you change are remembered for {t};
        discount rate and long-term growth are the same for every stock. A model, not advice: small changes in the assumptions move the result a lot.
      </p>
    </>
  )
}
