import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Bookmark, BookmarkPlus, ChartColumn, ChartLine, Check, ChevronDown, Eye, EyeOff, FileSpreadsheet, Hash, ImageDown, Maximize2, Minimize2, Pencil, Plus, Search, Telescope, Trash2, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Card, Page } from '@/components/app/Page'
import { useChartColors } from '@/components/chart/EChart'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Segmented } from '@/components/ui/segmented'
import { IconToggle } from '@/components/ui/toggle'
import { Hint } from '@/components/ui/tooltip'
import { api } from '@/lib/api'
import type { SearchHit } from '@/lib/api'
import { niceName } from '@/lib/format'
import { useSettings } from '@/lib/settings'
import { useToast } from '@/lib/toast'
import { cn } from '@/lib/utils'
import { FinChart, Shape } from './FinChart'
import type { Exporter } from './FinChart'
import { PriceChart } from './PriceChart'
import { SideBySide } from './SideBySide'
import { EXTRA_COLORS, MAX_NUMBERS, PRICE_RANGES, colorMap, readChart, writeChart } from './state'
import type { Chart, ChartType, Field, FieldGroup, Mode, Period } from './state'
import { Logo } from '@/components/app/Logo'

const label11 = 'text-[11px] font-semibold tracking-wider text-muted-foreground uppercase'
const itemCls = 'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] outline-none'
const chip = 'inline-flex h-8 items-center gap-1.5 rounded-md border bg-card pr-1 pl-2 text-[13px]'
const chipBtn = 'rounded p-1 text-muted-foreground outline-none hover:bg-secondary hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60'

/** Every number you can chart, by group, filtered by what you type. Arrow keys and Enter work from the input above it. */
function NumberList({ groups, q, at, current, onPick, onHover }: { groups: FieldGroup[]; q: string; at: number; current?: string; onPick: (key: string) => void; onHover: (i: number) => void }) {
  const shown = matchNumbers(groups, q)
  let group = ''
  if (!shown.length) return <div className="px-2 py-4 text-center text-[13px] text-muted-foreground">No number with that name</div>
  return (
    <>
      {shown.map((f, i) => {
        const head = f.group !== group ? ((group = f.group), <div className={cn(label11, 'px-2 pt-2 pb-1')}>{f.group}</div>) : null
        return (
          <div key={f.key}>
            {head}
            <button type="button" ref={i === at ? (el) => el?.scrollIntoView({ block: 'nearest' }) : undefined} onMouseDown={(e) => e.preventDefault()} onClick={() => onPick(f.key)} onMouseMove={() => i !== at && onHover(i)} className={cn(itemCls, i === at && 'bg-secondary')}>
              <span className="flex-1">{f.label}</span>
              {f.key === current && <Check className="size-3.5 text-primary" />}
            </button>
          </div>
        )
      })}
    </>
  )
}
function matchNumbers(groups: FieldGroup[], q: string) {
  const words = q.toLowerCase().split(/\s+/).filter(Boolean)
  return groups.flatMap((g) => g.fields.map((f) => ({ ...f, group: g.group }))).filter((f) => words.every((w) => `${f.label} ${f.group}`.toLowerCase().includes(w)))
}
/** An input with the list of numbers under it. */
function NumberSearch({ groups, current, onPick, placeholder, autoFocus, floating }: { groups: FieldGroup[]; current?: string; onPick: (key: string) => void; placeholder: string; autoFocus?: boolean; floating?: boolean }) {
  const [q, setQ] = useState('')
  const [at, setAt] = useState(0)
  const [open, setOpen] = useState(!floating)
  const shown = matchNumbers(groups, q)
  const pick = (k: string) => { onPick(k); setQ(''); if (floating) setOpen(false) }
  return (
    <div className={cn('relative', floating && 'min-w-64 flex-1')}>
      <Search className="pointer-events-none absolute top-[17px] left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
      <Input
        value={q} autoFocus={autoFocus} placeholder={placeholder} spellCheck={false} autoComplete="off" aria-label={placeholder} className="pl-8"
        onChange={(e) => { setQ(e.target.value); setAt(0); setOpen(true) }} onFocus={() => setOpen(true)} onBlur={() => floating && window.setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') { e.preventDefault(); setAt((i) => Math.min(i + 1, shown.length - 1)) }
          else if (e.key === 'ArrowUp') { e.preventDefault(); setAt((i) => Math.max(i - 1, 0)) }
          else if (e.key === 'Enter' && shown[at]) pick(shown[at].key)
          else if (e.key === 'Escape' && floating) setOpen(false)
        }}
      />
      {open && <div className={cn('max-h-[360px] overflow-y-auto p-1', floating && 'absolute top-full left-0 z-40 mt-1 w-full rounded-lg border bg-popover shadow-lg')}><NumberList groups={groups} q={q} at={at} current={current} onPick={pick} onHover={setAt} /></div>}
    </div>
  )
}

/** The box that adds a company or ETF: type, pick a result. */
function TickerSearch({ have, onPick }: { have: string[]; onPick: (hit: SearchHit) => void }) {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const [at, setAt] = useState(0)
  const { data: hits = [] } = useQuery({ queryKey: ['find', q], queryFn: () => api.get<SearchHit[]>(`/api/find?q=${encodeURIComponent(q)}&limit=8`), enabled: q.trim().length > 0, placeholderData: keepPreviousData })
  const shown = q.trim() ? hits : []
  const pick = (h: SearchHit) => { onPick(h); setQ(''); setOpen(false) }
  return (
    <div className="relative min-w-64 flex-1">
      <Plus className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
      <Input
        value={q} placeholder="Add a company or ETF" spellCheck={false} autoComplete="off" aria-label="Add a company or ETF" className="pl-8"
        onChange={(e) => { setQ(e.target.value); setOpen(true); setAt(0) }} onFocus={() => setOpen(true)} onBlur={() => window.setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') { e.preventDefault(); setAt((i) => Math.min(i + 1, shown.length - 1)) }
          else if (e.key === 'ArrowUp') { e.preventDefault(); setAt((i) => Math.max(i - 1, 0)) }
          else if (e.key === 'Enter' && shown[at]) pick(shown[at])
          else if (e.key === 'Escape') setOpen(false)
        }}
      />
      {open && shown.length > 0 && (
        <div className="absolute top-full left-0 z-40 mt-1 w-full rounded-lg border bg-popover p-1 shadow-lg">
          {shown.map((h, i) => (
            <button key={h.ticker} type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => pick(h)} onMouseEnter={() => setAt(i)} className={cn(itemCls, i === at && 'bg-secondary')}>
              <span className="w-14 shrink-0 font-semibold">{h.ticker}</span>
              <span className="min-w-0 flex-1 truncate text-muted-foreground">{niceName(h.name)}</span>
              {have.includes(h.ticker) ? <span className="text-xs text-muted-foreground">In the chart</span> : !h.loaded ? <span className="text-xs text-muted-foreground">Not loaded yet</span> : null}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function SavedCharts({ charts, onOpen, onChange }: { charts: Record<string, string>; onOpen: (query: string) => void; onChange: (charts: Record<string, string>) => void }) {
  const [open, setOpen] = useState(false)
  const [renaming, setRenaming] = useState('')
  const [name, setName] = useState('')
  const names = Object.keys(charts).sort((a, b) => a.localeCompare(b))
  const rename = (n: string) => {
    const nn = name.trim()
    if (nn && nn !== n) { const next = { ...charts, [nn]: charts[n] }; delete next[n]; onChange(next) }
    setRenaming('')
  }
  return (
    <Popover open={open} onOpenChange={(o) => { setOpen(o); setRenaming('') }}>
      <PopoverTrigger asChild><Button><Bookmark /> Saved charts{names.length ? <span className="num rounded bg-secondary px-1.5 text-[11px] text-muted-foreground">{names.length}</span> : null}</Button></PopoverTrigger>
      <PopoverContent className="max-h-[420px] w-80 overflow-y-auto p-1">
        <div className={cn(label11, 'px-2 pt-1 pb-1.5')}>Saved charts</div>
        {!names.length && <div className="px-2 py-3 text-[13px] text-muted-foreground">No saved charts yet. Use "Save chart" to keep the current one.</div>}
        {names.map((n) => (
          <div key={n} className="group flex items-center gap-0.5 rounded-md hover:bg-secondary">
            {renaming === n ? (
              <Input autoFocus value={name} maxLength={60} className="m-0.5 h-7" onChange={(e) => setName(e.target.value)} onFocus={(e) => e.target.select()} onBlur={() => rename(n)} onKeyDown={(e) => { if (e.key === 'Enter') rename(n); if (e.key === 'Escape') setRenaming('') }} />
            ) : (
              <>
                <button type="button" className="min-w-0 flex-1 truncate rounded-md px-2 py-1.5 text-left text-[13px] outline-none focus-visible:bg-secondary" onClick={() => { setOpen(false); onOpen(charts[n]) }}>{n}</button>
                <Hint label="Rename"><button type="button" aria-label={`Rename ${n}`} className={cn(chipBtn, 'opacity-0 group-hover:opacity-100 focus-visible:opacity-100')} onClick={() => { setRenaming(n); setName(n) }}><Pencil className="size-3.5" /></button></Hint>
                <Hint label="Delete"><button type="button" aria-label={`Delete ${n}`} className={cn(chipBtn, 'mr-1 opacity-0 group-hover:opacity-100 hover:text-down focus-visible:opacity-100')} onClick={() => { const next = { ...charts }; delete next[n]; onChange(next) }}><Trash2 className="size-3.5" /></button></Hint>
              </>
            )}
          </div>
        ))}
      </PopoverContent>
    </Popover>
  )
}

export function ComparePage() {
  const [params, setParams] = useSearchParams()
  const nav = useNavigate()
  const toast = useToast()
  const c = useChartColors()
  const { settings, pref, setPref, setPrefGroup } = useSettings()
  const exporter = useRef<Exporter | null>(null)
  const [full, setFull] = useState(false)
  const [changing, setChanging] = useState(-1)
  const [saving, setSaving] = useState(false)
  const [saveName, setSaveName] = useState('')

  // "Compare" in the menu, without an address: open where you left it; the very first time: your two largest companies
  const empty = [...params.keys()].length === 0
  const last = typeof settings.prefs.compare === 'string' ? settings.prefs.compare : ''
  const top = useQuery({ queryKey: ['companies'], queryFn: () => api.get<{ ticker: string; name: string }[]>('/api/companies') })
  useEffect(() => {
    if (!empty) return
    if (last) setParams(last, { replace: true })
    else if (top.data) setParams(writeChart({ ...readChart(new URLSearchParams()), t: top.data.slice(0, 2).map((r) => r.ticker) }), { replace: true })
  }, [empty, last, top.data]) // eslint-disable-line react-hooks/exhaustive-deps

  const chart = useMemo(() => readChart(params), [params])
  const query = writeChart(chart)
  useEffect(() => { if (!empty) setPrefGroup('compare', query) }, [empty, query, setPrefGroup])
  const go = (changes: Partial<Chart>, replace = false) => setParams(writeChart({ ...chart, ...changes }), { replace })

  const tk = chart.t.join(',')
  const { data: groups } = useQuery({ queryKey: ['compare-fields', tk], queryFn: () => api.get<FieldGroup[]>(`/api/fields?tickers=${tk}`), placeholderData: keepPreviousData, enabled: !empty })
  // only ETFs (no financial statements): open on the price comparison
  const view = chart.v || (chart.t.length && groups && !groups.length ? 'price' : 'fin')
  const all = useMemo(() => (groups ?? []).flatMap((g) => g.fields), [groups])
  const fields = useMemo<Field[]>(() => chart.f.map((k) => all.find((f) => f.key === k) ?? { key: k, label: (k.split(':').pop() || k).replace(/_/g, ' '), fmt: 'num' }), [chart.f, all])
  const nameOf = useMemo(() => Object.fromEntries((top.data ?? []).map((r) => [r.ticker, niceName(r.name)])), [top.data])

  const cmap = colorMap(pref<string>('state', 'cmpColors', ''))
  const palette = [...c.series, ...EXTRA_COLORS]
  const colorOf = (t: string) => cmap[t] || palette[chart.t.indexOf(t) % palette.length]
  const shownTickers = useMemo(() => chart.t.filter((t) => !chart.h.includes(t)), [chart.t, chart.h])
  const colorKey = shownTickers.map(colorOf).join()
  const shownColors = useMemo(() => colorKey.split(',').filter(Boolean), [colorKey])
  const setColor = (t: string, col: string) => {
    const m = { ...cmap }
    if (col) m[t] = col
    else delete m[t]
    setPref('state', 'cmpColors', Object.entries(m).map(([k, v]) => `${k}:${v}`).join(','))
  }

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const el = document.activeElement
      const typing = el instanceof HTMLElement && (/^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName) || el.isContentEditable)
      if (e.key === 'Escape') setFull(false)
      else if ((e.key === 'f' || e.key === 'F') && !typing && !e.ctrlKey && !e.altKey && !e.metaKey) setFull((v) => !v)
    }
    document.addEventListener('keydown', key)
    return () => document.removeEventListener('keydown', key)
  }, [])

  const charts = settings.prefs.charts && typeof settings.prefs.charts === 'object' ? (settings.prefs.charts as Record<string, string>) : {}
  const saveChart = () => {
    const n = saveName.trim()
    if (!n) return
    setPrefGroup('charts', { ...charts, [n]: query })
    setSaving(false)
    toast(`Saved as "${n}"`)
  }
  const doExport = async (kind: 'png' | 'csv') => {
    const ex = exporter.current
    const data = kind === 'png' ? ex?.png() : ex?.csv()
    if (!ex || !data) return
    try {
      const j = await api.post<{ path: string }>('/api/export', { kind, name: `Compare ${ex.title.replace(/[·]/g, '-')} ${new Date().toISOString().slice(0, 10)}`, data })
      toast(<>Saved: {j.path} <button type="button" className="font-semibold underline" onClick={() => void api.post('/api/reveal', { path: j.path }).catch(() => {})}>Show in folder</button></>)
    } catch (e) {
      toast('Could not save: ' + (e instanceof Error ? e.message : 'unknown error'))
    }
  }
  const addCompany = (h: SearchHit) => {
    if (!h.loaded) return nav(`/load/${h.ticker}`)
    if (!chart.t.includes(h.ticker)) go({ t: [...chart.t, h.ticker] })
  }
  const setType = (i: number, v: ChartType) => go({ c: chart.c.map((x, j) => (j === i ? v : x)) })

  if (empty) return <Page title="Compare"><Card className="p-4 text-muted-foreground">Loading…</Card></Page>
  return (
    <Page
      title="Compare"
      actions={
        <>
          <Segmented label="What to compare" value={view} onChange={(v) => go({ v })} options={[{ value: 'fin', label: 'Financials' }, { value: 'price', label: 'Price & return' }]} />
          <div className="grow" />
          <SavedCharts charts={charts} onOpen={(q) => setParams(q)} onChange={(next) => setPrefGroup('charts', next)} />
          <Popover open={saving} onOpenChange={(o) => { setSaving(o); if (o) setSaveName(`${chart.t.join(', ')}: ${view === 'price' ? 'price & return' : fields.map((f) => f.label).join(' + ')}`.slice(0, 60)) }}>
            <PopoverTrigger asChild><Button variant="default"><BookmarkPlus /> Save chart</Button></PopoverTrigger>
            <PopoverContent className="w-80">
              <div className="px-1 pb-2 text-[13px] font-semibold">Save this chart as</div>
              <div className="flex gap-1.5">
                <Input autoFocus value={saveName} maxLength={60} spellCheck={false} className="h-8" onChange={(e) => setSaveName(e.target.value)} onFocus={(e) => e.target.select()} onKeyDown={(e) => e.key === 'Enter' && saveChart()} />
                <Button variant="default" disabled={!saveName.trim()} onClick={saveChart}>Save</Button>
              </div>
            </PopoverContent>
          </Popover>
        </>
      }
    >
      <Card className="flex flex-col gap-3 p-3">
        <div className="flex flex-wrap gap-2">
          {view === 'fin' && (chart.f.length < MAX_NUMBERS ? <NumberSearch floating groups={(groups ?? []).map((g) => ({ ...g, fields: g.fields.filter((f) => !chart.f.includes(f.key)) }))} placeholder="Add a number: revenue, margin, ROIC, P/E" onPick={(k) => go({ f: [...chart.f, k], c: [...chart.c, 'line'] })} /> : <div className="flex min-w-64 flex-1 items-center rounded-md border border-dashed px-3 text-[13px] text-muted-foreground">Three numbers is the most one chart can hold. Remove one to add another.</div>)}
          <TickerSearch have={chart.t} onPick={addCompany} />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className={cn(label11, 'w-20')}>Companies</span>
          {chart.t.map((t) => {
            const hidden = chart.h.includes(t)
            return (
              <span key={t} className={cn(chip, hidden && 'opacity-55')}>
                <Popover>
                  <Hint label="Choose a colour"><PopoverTrigger asChild><button type="button" aria-label={`Colour of ${t}`} className="size-3.5 rounded-full outline-none ring-offset-2 ring-offset-card focus-visible:ring-2 focus-visible:ring-ring" style={{ background: colorOf(t) }} /></PopoverTrigger></Hint>
                  <PopoverContent align="start" className="w-56">
                    <div className={cn(label11, 'px-1 pb-2')}>Colour for {t}</div>
                    <div className="grid grid-cols-7 gap-1.5 px-1">
                      {palette.map((col) => <button key={col} type="button" aria-label={col} className={cn('size-6 rounded-full outline-none ring-offset-2 ring-offset-popover focus-visible:ring-2 focus-visible:ring-ring', colorOf(t) === col && 'ring-2 ring-foreground')} style={{ background: col }} onClick={() => setColor(t, col)} />)}
                    </div>
                    <Button size="sm" className="mt-3 w-full" onClick={() => setColor(t, '')}>Automatic colour</Button>
                  </PopoverContent>
                </Popover>
                <Logo t={t} size={16} />
                <span className="font-semibold">{t}</span>
                {nameOf[t] && <span className="max-w-40 truncate text-muted-foreground">{nameOf[t]}</span>}
                <Hint label={hidden ? 'Show in the chart' : 'Hide from the chart'}><button type="button" aria-label={`${hidden ? 'Show' : 'Hide'} ${t}`} aria-pressed={!hidden} className={chipBtn} onClick={() => go({ h: hidden ? chart.h.filter((x) => x !== t) : [...chart.h, t] })}>{hidden ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}</button></Hint>
                <Hint label="Remove"><button type="button" aria-label={`Remove ${t}`} className={cn(chipBtn, 'hover:text-down')} onClick={() => go({ t: chart.t.filter((x) => x !== t), h: chart.h.filter((x) => x !== t) })}><X className="size-3.5" /></button></Hint>
              </span>
            )
          })}
          {!chart.t.length && <span className="text-[13px] text-muted-foreground">None yet. Add one with the box above.</span>}
        </div>
        {view === 'fin' && (
          <div className="flex flex-wrap items-center gap-2">
            <span className={cn(label11, 'w-20')}>Numbers</span>
            {fields.map((f, i) => (
              <span key={f.key} className={chip}>
                <Shape i={i} color={shownTickers.length === 1 ? [shownColors[0], ...c.series.filter((x) => x !== shownColors[0])][i] : c.muted} />
                <Popover open={changing === i} onOpenChange={(o) => setChanging(o ? i : -1)}>
                  <Hint label="Change this number"><PopoverTrigger asChild><button type="button" className="inline-flex items-center gap-1 rounded px-1 py-0.5 font-medium outline-none hover:bg-secondary focus-visible:ring-2 focus-visible:ring-ring/60">{f.label}<ChevronDown className="size-3.5 text-muted-foreground" /></button></PopoverTrigger></Hint>
                  <PopoverContent align="start" className="w-80 p-1"><NumberSearch autoFocus groups={groups ?? []} current={f.key} placeholder="Search a number" onPick={(k) => { setChanging(-1); if (!chart.f.includes(k)) go({ f: chart.f.map((x, j) => (j === i ? k : x)) }) }} /></PopoverContent>
                </Popover>
                <Segmented label="Draw as" className="scale-90" value={chart.c[i]} onChange={(v) => setType(i, v)} options={[{ value: 'line', icon: <ChartLine />, hint: 'Line' }, { value: 'bar', icon: <ChartColumn />, hint: 'Bars' }]} />
                {fields.length > 1 && <Hint label="Remove this number"><button type="button" aria-label={`Remove ${f.label}`} className={cn(chipBtn, 'hover:text-down')} onClick={() => go({ f: chart.f.filter((_, j) => j !== i), c: chart.c.filter((_, j) => j !== i) })}><X className="size-3.5" /></button></Hint>}
              </span>
            ))}
          </div>
        )}
      </Card>

      <Card className={cn('min-w-0 p-4', full && 'fixed inset-0 z-40 overflow-auto rounded-none border-0')}>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          {view === 'fin' ? (
            <>
              <Segmented label="Period" value={chart.p} onChange={(v: Period) => go({ p: v })} options={[{ value: 'FY', label: 'Annual' }, { value: 'Q', label: 'Quarterly' }, { value: 'TTM', label: 'TTM' }]} />
              <Segmented label="Show as" value={chart.m} onChange={(v: Mode) => go({ m: v })} options={[{ value: 'value', label: 'Value' }, { value: 'growth', label: 'Growth %' }, { value: 'index', label: 'Indexed' }]} />
            </>
          ) : (
            <>
              <Segmented label="Price or total return" value={chart.pm} onChange={(v) => go({ pm: v })} options={[{ value: 'price', label: 'Price change' }, { value: 'total', label: 'Total return' }]} />
              <Segmented label="Range" value={chart.r} onChange={(v) => go({ r: v })} options={PRICE_RANGES.map((k) => ({ value: k, label: k === 'Max' ? 'Max' : k === 'All' ? 'All' : k, hint: k === 'Max' ? 'From the first day every company has prices, so all lines start together' : k === 'All' ? "Each line from its own first day (different starting points)" : undefined }))} />
              <Segmented label="Scale" value={chart.sc} onChange={(v) => go({ sc: v })} options={[{ value: 'lin', label: 'Linear' }, { value: 'log', label: 'Log' }]} />
            </>
          )}
          <div className="grow" />
          {view === 'fin' && (
            <>
              <IconToggle on={chart.vl !== 'off'} onChange={(on) => go({ vl: on ? 'auto' : 'off' })} label="Numbers on the chart" text="Labels"><Hash /></IconToggle>
              <IconToggle on={chart.e} onChange={(on) => go({ e: on })} label="Analyst estimates for the coming years (annual revenue and EPS)" text="Estimates"><Telescope /></IconToggle>
              <span className="mx-1 h-5 w-px bg-border" />
            </>
          )}
          <Hint label="Save the chart as a picture"><Button size="icon" aria-label="Save the chart as a picture" onClick={() => void doExport('png')}><ImageDown /></Button></Hint>
          <Hint label="Save the numbers as a spreadsheet"><Button size="icon" aria-label="Save the numbers as a spreadsheet" onClick={() => void doExport('csv')}><FileSpreadsheet /></Button></Hint>
          <Hint label={full ? 'Leave full screen (Esc)' : 'Full screen (F)'}><Button size="icon" aria-label="Full screen" aria-pressed={full} onClick={() => setFull(!full)}>{full ? <Minimize2 /> : <Maximize2 />}</Button></Hint>
        </div>
        {view === 'fin' ? (
          <FinChart chart={chart} tickers={shownTickers} colors={shownColors} fields={fields} full={full} exporter={exporter} onRange={(r) => go(r, true)} />
        ) : (
          <PriceChart chart={chart} tickers={shownTickers} colors={shownColors} full={full} exporter={exporter} />
        )}
      </Card>

      <SideBySide tickers={chart.t} colors={chart.t.map(colorOf)} />
    </Page>
  )
}
