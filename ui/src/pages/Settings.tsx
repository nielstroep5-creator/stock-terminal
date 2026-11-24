import { Bell, ChartLine, Database, Info, Monitor, Moon, PanelTop, RectangleHorizontal, Search, Sun, Table2 } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import type { ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { UpdateField } from '@/components/app/AppUpdate'
import { DataField } from '@/components/app/DataSetup'
import { ReportField } from '@/components/app/Report'
import { Card, Page } from '@/components/app/Page'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Segmented } from '@/components/ui/segmented'
import { Switch } from '@/components/ui/switch'
import { DISCLAIMER } from '@/lib/about'
import { api } from '@/lib/api'
import { useSettings } from '@/lib/settings'
import type { Settings as S } from '@/lib/settings'
import { cn } from '@/lib/utils'

type Row = { id: string; section: string; name: string; desc: string }

const SECTIONS: { id: string; label: string; icon: LucideIcon }[] = [
  { id: 'appearance', label: 'Appearance', icon: Sun },
  { id: 'charts', label: 'Price charts', icon: ChartLine },
  { id: 'financials', label: 'Financials and Compare', icon: Table2 },
  { id: 'data', label: 'Data and accounts', icon: Database },
  { id: 'alerts', label: 'Alerts', icon: Bell },
  { id: 'about', label: 'About', icon: Info },
]

// Every setting, in one list: the page draws from it and the search palette finds settings through it.
const ROWS: Row[] = [
  { id: 'theme', section: 'appearance', name: 'Theme', desc: 'Light, dark, or the same as your computer.' },
  { id: 'page_width', section: 'appearance', name: 'Page width', desc: 'Use the whole window, or a narrower column in the middle.' },
  { id: 'show_logos', section: 'appearance', name: 'Company logos', desc: 'Show the logo next to the ticker. Downloaded once per company; without one you just see the ticker.' },
  { id: 'logo_style', section: 'appearance', name: 'Logo style', desc: 'Logos as they are, on a dark tile like app icons, or on a tile or circle in the page colour.' },
  { id: 'price_range', section: 'charts', name: 'Range when a chart opens', desc: 'You can always change it on the chart itself.' },
  { id: 'show_extended', section: 'charts', name: 'Before and after hours price', desc: 'Show it under the main price when the market is closed.' },
  { id: 'chart_style', section: 'charts', name: 'Chart style', desc: 'A line, or candlesticks: open, high, low and close of each day.' },
  { id: 'range_change', section: 'charts', name: 'Change over the chosen range', desc: 'Show the percentage next to the range buttons always, or only while you point at them.' },
  { id: 'default_period', section: 'financials', name: 'Period shown first', desc: 'On Financials, Ratios and Segments.' },
  { id: 'compare_years', section: 'financials', name: 'Years in Compare', desc: "Each company's own fiscal year, or January to December for all." },
  { id: 'daily_update', section: 'data', name: 'Daily update at 07:00', desc: 'New prices, filings and analyst data every morning. Off: the scheduled update skips its run.' },
  { id: 'earnings_watch', section: 'data', name: 'Watch for new results', desc: "On a company's results day, look every 5 minutes for its results and load the official numbers as soon as they are filed. Works with the app closed; the computer has to be on." },
  { id: 'company_data', section: 'data', name: 'Company data', desc: 'Get the S&P 500 in one go (starter pack), or use the data of another Stock Terminal copy on this computer.' },
  { id: 'sec_email', section: 'data', name: 'Contact email for the SEC', desc: 'The SEC asks for one on every download. It stays on this computer.' },
  { id: 't212', section: 'data', name: 'Trading 212', desc: 'Connect your account to see your portfolio.' },
  { id: 'pf_hidden', section: 'data', name: 'Hidden holdings', desc: 'Leave holdings out of the portfolio lists. Only on this computer; totals, returns and box 3 still count them.' },
  { id: 'alerts_rules', section: 'alerts', name: 'Alert rules', desc: 'What you hear about: price limits, big daily moves, results, dividend cuts, analysts. Price limits and moves are checked every 5 minutes while markets are open.' },
  { id: 'alerts_limits', section: 'alerts', name: 'Price limits per stock', desc: 'Every price limit, own daily-move % and muted stock in one table. Or use the bell on a stock page.' },
  { id: 'alerts_phone', section: 'alerts', name: 'Phone messages and quiet hours', desc: 'Alerts on your phone through the free ntfy app, and the hours they wait until the morning.' },
  { id: 'version', section: 'about', name: 'Version', desc: 'The version of Stock Terminal on this computer. Check for a newer one and update it here.' },
  { id: 'update_check', section: 'about', name: 'Check for updates at start', desc: 'When a newer version is available, offer it in a pop-up when the app opens.' },
  { id: 'report', section: 'about', name: 'Report a problem', desc: 'Something broken or a wrong number? Make a short report (version and the last errors) to copy or send on GitHub. Nothing is sent by itself.' },
  { id: 'licence', section: 'about', name: 'Licence', desc: 'Free and open source under the MIT licence: you may use, change and share it.' },
  { id: 'disclaimer', section: 'about', name: 'Not investment advice', desc: DISCLAIMER },
]

export const SETTING_INDEX = ROWS.map((r) => ({
  id: r.id,
  name: r.name,
  section: SECTIONS.find((s) => s.id === r.section)?.label ?? '',
  text: `${r.name} ${r.desc}`,
}))

function OptionCards<T extends string>({ value, onChange, options, label }: { value: T; onChange: (v: T) => void; options: { value: T; label: string; icon: LucideIcon }[]; label: string }) {
  return (
    <div role="radiogroup" aria-label={label} className="flex flex-wrap gap-2">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            'flex min-w-[92px] flex-col items-center gap-1.5 rounded-lg border bg-card px-3.5 py-2.5 text-[13px] text-muted-foreground transition-colors outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60',
            value === o.value && 'border-primary bg-accent font-semibold text-accent-foreground hover:text-accent-foreground',
          )}
        >
          <o.icon className="size-5" />
          {o.label}
        </button>
      ))}
    </div>
  )
}

function EmailField() {
  const { settings, update } = useSettings()
  const [text, setText] = useState(settings.sec_email)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const save = async () => {
    if (text.trim() === settings.sec_email) return
    const err = await update({ sec_email: text.trim() })
    setError(err)
    setSaved(!err)
  }
  return (
    <div className="flex w-[260px] max-w-full flex-col gap-1">
      <Input
        id="set-sec_email"
        type="email"
        value={text}
        placeholder="you@example.com"
        aria-invalid={!!error}
        onChange={(e) => {
          setText(e.target.value)
          setSaved(false)
        }}
        onBlur={save}
        onKeyDown={(e) => e.key === 'Enter' && save()}
      />
      {error && <span className="text-xs text-down">{error}</span>}
      {saved && <span className="text-xs text-up">Saved</span>}
    </div>
  )
}

type T212Status = { connected: boolean; account_id?: number | string; currency?: string; key_hint?: string }
type T212Sync = { running?: boolean; step?: string; last_sync?: string; error?: string; last_error?: string; totals?: Record<string, number> }
const T212_STEPS: Record<string, string> = { starting: 'Starting', account: 'Account', positions: 'Positions', orders: 'Orders (first time: the whole history)', dividends: 'Dividends', transactions: 'Deposits and withdrawals' }

/** Trading 212: connect with an API key and secret (read-only), or sync now and disconnect. */
function T212Field() {
  const qc = useQueryClient()
  const status = useQuery({ queryKey: ['t212', 'status'], queryFn: () => api.get<T212Status>('/api/t212/status') })
  const sync = useQuery({
    queryKey: ['t212', 'sync'],
    queryFn: () => api.get<T212Sync>('/api/t212/sync'),
    enabled: !!status.data?.connected,
    refetchInterval: (q) => (q.state.data?.running ? 1500 : false),
  })
  const [key, setKey] = useState('')
  const [secret, setSecret] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const after = () => {
    void qc.invalidateQueries({ queryKey: ['t212'] })
    void qc.invalidateQueries({ queryKey: ['portfolio'] })
    void qc.invalidateQueries({ queryKey: ['pf'] })
    void qc.invalidateQueries({ queryKey: ['tax'] })
  }
  const connect = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setMsg(null)
    try {
      const r = await api.put<{ currency?: string; summary?: { totalValue?: number } }>('/api/t212/connect', { key, secret })
      setKey('')
      setSecret('')
      const v = r.summary?.totalValue
      setMsg({ ok: true, text: `Connected${typeof v === 'number' ? `. Account value ${v.toLocaleString('en-US', { style: 'currency', currency: r.currency || 'EUR' })}` : ''}. The first sync is running.` })
      after()
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : String(err) })
    } finally {
      setBusy(false)
    }
  }
  const s = status.data
  if (!s) return null
  if (!s.connected)
    return (
      <form onSubmit={connect} autoComplete="off" className="flex w-[300px] max-w-full flex-col gap-2">
        <Input value={key} onChange={(e) => setKey(e.target.value)} placeholder="API key" spellCheck={false} autoComplete="off" aria-label="Trading 212 API key" />
        <Input value={secret} onChange={(e) => setSecret(e.target.value)} type="password" placeholder="API secret" spellCheck={false} autoComplete="new-password" aria-label="Trading 212 API secret" />
        <Button type="submit" variant="default" disabled={busy || !key || !secret} className="self-start">{busy ? 'Checking…' : 'Connect'}</Button>
        {msg && <span className={cn('text-xs', msg.ok ? 'text-up' : 'text-down')}>{msg.text}</span>}
      </form>
    )
  const sy = sync.data
  const t = sy?.totals ?? {}
  const err = sy?.error || sy?.last_error
  return (
    <div className="flex w-[300px] max-w-full flex-col gap-2 text-[13px]">
      <div>
        <span className="font-semibold text-up">Connected</span> · account {s.account_id ?? ''} · {s.currency}
        <div className="text-xs text-muted-foreground">Key …{s.key_hint}</div>
      </div>
      <div className="text-xs text-muted-foreground">
        {sy?.running ? `Syncing: ${T212_STEPS[sy.step ?? ''] ?? sy.step ?? ''}…` : sy?.last_sync ? `Synced ${sy.last_sync}: ${t.pf_positions ?? 0} positions, ${t.pf_trades ?? 0} orders, ${t.pf_dividends ?? 0} dividends, ${t.pf_cash ?? 0} cash movements` : 'Not synced yet'}
      </div>
      <div className="flex gap-2">
        <Button size="sm" disabled={!!sy?.running} onClick={() => void api.post('/api/t212/sync').then(() => sync.refetch()).catch((e: Error) => setMsg({ ok: false, text: e.message }))}>Sync now</Button>
        <Button size="sm" variant="ghost" onClick={() => void api.del('/api/t212/connect').then(() => { setMsg({ ok: true, text: 'Disconnected. The key is removed from this computer; your synced portfolio stays.' }); after() })}>Disconnect</Button>
      </div>
      {(msg || err) && <span className={cn('text-xs', msg?.ok && !err ? 'text-up' : 'text-down')}>{err || msg?.text}</span>}
    </div>
  )
}

type Instrument = { t212_ticker: string; name: string; open: boolean; hidden: boolean }

/** Holdings left out of the portfolio lists (prefs group "pf_hidden", only on this computer). */
function HiddenField() {
  const qc = useQueryClient()
  const { settings, setPref, pref } = useSettings()
  const open = pref('ui2', 'hidden_open', false)
  const q = useQuery({ queryKey: ['portfolio', 'stored'], queryFn: () => api.get<{ instruments?: Instrument[] }>('/api/portfolio') })
  const all = q.data?.instruments ?? []
  const hidden = (settings.prefs.pf_hidden ?? {}) as Record<string, boolean | null>
  const isHidden = (t: string) => !!hidden[t]
  const n = all.filter((i) => isHidden(i.t212_ticker)).length
  if (!all.length) return <span className="text-xs text-muted-foreground">Connect Trading 212 first.</span>
  return (
    <div className="flex w-[300px] max-w-full flex-col gap-2 text-[13px]">
      <div className="flex items-center gap-2">
        <span className="text-muted-foreground">{n ? `${n} hidden` : 'Nothing hidden'}</span>
        <div className="grow" />
        <Button size="sm" onClick={() => setPref('ui2', 'hidden_open', !open)}>{open ? 'Done' : 'Choose'}</Button>
      </div>
      {open && (
        <div className="flex max-h-60 flex-col gap-1 overflow-y-auto rounded-md border bg-card p-2">
          {all.map((i) => (
            <label key={i.t212_ticker} className="flex items-center gap-2 rounded px-1 py-0.5 hover:bg-secondary">
              <input type="checkbox" className="accent-[var(--color-primary)]" checked={isHidden(i.t212_ticker)}
                onChange={(e) => {
                  setPref('pf_hidden', i.t212_ticker, e.target.checked ? true : null)
                  window.setTimeout(() => void qc.invalidateQueries({ queryKey: ['portfolio'] }), 600)
                }} />
              <span className="min-w-0 flex-1 truncate">{i.name}</span>
              {!i.open && <span className="text-xs text-muted-foreground">sold</span>}
            </label>
          ))}
        </div>
      )}
    </div>
  )
}

function Control({ id }: { id: string }) {
  const { settings, update } = useSettings()
  const set = <K extends keyof S>(key: K) => (value: S[K]) => void update({ [key]: value } as Partial<S>)
  switch (id) {
    case 'theme':
      return (
        <OptionCards
          label="Theme"
          value={settings.theme}
          onChange={set('theme')}
          options={[
            { value: 'light', label: 'Light', icon: Sun },
            { value: 'dark', label: 'Dark', icon: Moon },
            { value: 'system', label: 'Computer', icon: Monitor },
          ]}
        />
      )
    case 'page_width':
      return (
        <OptionCards
          label="Page width"
          value={settings.page_width}
          onChange={set('page_width')}
          options={[
            { value: 'full', label: 'Full', icon: RectangleHorizontal },
            { value: 'centered', label: 'Centered', icon: PanelTop },
          ]}
        />
      )
    case 'show_logos':
      return <Switch aria-label="Company logos" checked={settings.show_logos} onCheckedChange={set('show_logos')} />
    case 'logo_style':
      return (
        <Segmented
          label="Logo style"
          value={settings.logo_style}
          onChange={set('logo_style')}
          options={[
            { value: 'plain', label: 'Plain' },
            { value: 'dark', label: 'Dark tile' },
            { value: 'soft', label: 'Soft tile' },
            { value: 'round', label: 'Round' },
          ]}
        />
      )
    case 'chart_style':
      return <Segmented label="Chart style" value={settings.chart_style} onChange={set('chart_style')} options={[{ value: 'line', label: 'Line' }, { value: 'candles', label: 'Candles' }]} />
    case 'earnings_watch':
      return <Switch checked={settings.earnings_watch} onCheckedChange={set('earnings_watch')} aria-label="Watch for new results" />
    case 'daily_update':
      return <Switch checked={settings.daily_update} onCheckedChange={set('daily_update')} aria-label="Daily update at 07:00" />
    case 'price_range':
      return (
        <Segmented
          label="Range when a chart opens"
          value={settings.price_range}
          onChange={set('price_range')}
          options={(['1M', '3M', '6M', 'YTD', '1Y', '3Y', '5Y', '10Y', 'Max'] as const).map((r) => ({ value: r, label: r }))}
        />
      )
    case 'show_extended':
      return <Switch aria-label="Before and after hours price" checked={settings.show_extended} onCheckedChange={set('show_extended')} />
    case 'range_change':
      return (
        <Segmented
          label="Change over the chosen range"
          value={settings.range_change}
          onChange={set('range_change')}
          options={[
            { value: 'always', label: 'Always' },
            { value: 'hover', label: 'On hover' },
          ]}
        />
      )
    case 'default_period':
      return (
        <Segmented
          label="Period shown first"
          value={settings.default_period}
          onChange={set('default_period')}
          options={[
            { value: 'FY', label: 'Annual' },
            { value: 'Q', label: 'Quarterly' },
            { value: 'TTM', label: 'TTM' },
          ]}
        />
      )
    case 'compare_years':
      return (
        <Segmented
          label="Years in Compare"
          value={settings.compare_years}
          onChange={set('compare_years')}
          options={[
            { value: 'fy', label: 'Fiscal' },
            { value: 'cal', label: 'Calendar' },
          ]}
        />
      )
    case 'company_data':
      return <DataField />
    case 'sec_email':
      return <EmailField />
    case 't212':
      return <T212Field />
    case 'pf_hidden':
      return <HiddenField />
    case 'version':
      return <UpdateField />
    case 'report':
      return <ReportField />
    case 'update_check':
      return <Switch aria-label="Check for updates at start" checked={settings.update_check} onCheckedChange={set('update_check')} />
    case 'alerts_rules':
    case 'alerts_phone':
    case 'alerts_limits':
      return <Button asChild><Link to="/alerts/rules">Open in Alerts</Link></Button>
    default:
      return null
  }
}

function SettingRow({ row, highlight, children }: { row: Row; highlight: boolean; children: ReactNode }) {
  return (
    <div
      id={`row-${row.id}`}
      className={cn('-mx-5 flex flex-wrap items-center gap-x-6 gap-y-3 border-t px-5 py-3.5 transition-colors duration-700', highlight && 'bg-accent')}
    >
      <div className="min-w-[220px] flex-1">
        <label htmlFor={`set-${row.id}`} className="font-medium">
          {row.name}
        </label>
        <p className="text-[12.5px] text-muted-foreground">{row.desc}</p>
      </div>
      {children}
    </div>
  )
}

export function SettingsPage() {
  const [params] = useSearchParams()
  const find = params.get('find')
  const [filter, setFilter] = useState('')
  const [flash, setFlash] = useState<string | null>(null)
  const low = filter.trim().toLowerCase()
  const visible = ROWS.filter((r) => !low || `${r.name} ${r.desc}`.toLowerCase().includes(low))
  const broken = useQuery({ queryKey: ['settings', 'broken'], queryFn: () => api.get<{ copy: string | null }>('/api/settings/broken'), retry: false })

  // arriving from the search palette: scroll to the setting and light it up briefly
  useEffect(() => {
    if (!find) return
    setFilter('')
    const el = document.getElementById(`row-${find}`)
    el?.scrollIntoView({ block: 'center' })
    setFlash(find)
    const t = window.setTimeout(() => setFlash(null), 1600)
    return () => window.clearTimeout(t)
  }, [find])

  return (
    <Page
      title="Settings"
      actions={
        <div className="relative w-[280px] max-w-full">
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input aria-label="Search settings" placeholder="Search settings" value={filter} onChange={(e) => setFilter(e.target.value)} className="h-9 bg-panel pl-9" />
        </div>
      }
    >
      <div className="flex flex-wrap items-start gap-6">
        <nav aria-label="Settings sections" className="sticky top-20 flex w-[220px] max-w-full flex-col gap-0.5">
          {SECTIONS.map((s) => (
            <a
              key={s.id}
              href={`#sec-${s.id}`}
              onClick={(e) => {
                e.preventDefault()
                document.getElementById(`sec-${s.id}`)?.scrollIntoView({ block: 'start', behavior: 'smooth' })
              }}
              className="flex h-9 items-center gap-2.5 rounded-md px-2.5 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
            >
              <s.icon className="size-4" />
              {s.label}
            </a>
          ))}
        </nav>
        <div className="flex min-w-0 flex-[999_1_480px] flex-col gap-5">
          {broken.data?.copy && (
            <Card className="border-flag px-5 py-3.5">
              Your settings file was damaged, so the app started with the default settings. The old file is kept as{' '}
              <span className="font-mono text-[13px] break-all">{broken.data.copy}</span>.
            </Card>
          )}
          {SECTIONS.map((s) => {
            const rows = visible.filter((r) => r.section === s.id)
            if (!rows.length) return null
            return (
              <Card key={s.id} id={`sec-${s.id}`} className="scroll-mt-20 overflow-hidden px-5">
                <h2 className="py-3.5 text-[15px] font-semibold">{s.label}</h2>
                {rows.map((r) => (
                  <SettingRow key={r.id} row={r} highlight={flash === r.id}>
                    <Control id={r.id} />
                  </SettingRow>
                ))}
              </Card>
            )
          })}
          {!visible.length && <Card className="px-6 py-12 text-center text-muted-foreground">No setting matches "{filter}".</Card>}
        </div>
      </div>
    </Page>
  )
}
