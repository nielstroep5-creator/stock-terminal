import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, ArrowRight, Eye, EyeOff } from 'lucide-react'
import { useMemo } from 'react'
import { AlertBell } from '@/components/app/AlertBell'
import { Link, useNavigate } from 'react-router-dom'
import { NewsCard } from '@/components/app/News'
import { Card, Page } from '@/components/app/Page'
import { Empty, H2, Kv, Stat, HowToRead } from '@/components/app/bits'
import { EChart, baseOption, useChartColors } from '@/components/chart/EChart'
import { Button } from '@/components/ui/button'
import { ChangePill } from '@/components/ui/pill'
import { Segmented } from '@/components/ui/segmented'
import { Hint } from '@/components/ui/tooltip'
import { api } from '@/lib/api'
import { big, dateAxis, day, fmt, tone } from '@/lib/format'
import { useSettings } from '@/lib/settings'
import { useToast } from '@/lib/toast'
import { cn } from '@/lib/utils'
import { sinceDate } from '@/pages/compare/state'
import { CUR_SYM, CurrencySwitch, cash, pctTxt, qty, sharePrice, t212Symbol, useMoney } from './common'
import type { Fx, N, Position } from './common'
import { Logo } from '@/components/app/Logo'

type Trade = { date: string; side: 'BUY' | 'SELL'; type: string; quantity: N; price: N; currency: string; amount: N; fees: N; chart_price?: N }
type Div = { date: string; quantity: N; amount: N; per_share: N; type: string }
type Info = Record<string, string | number | undefined>
type HoldingData = {
  found: boolean; t212_ticker: string; name: string; isin: string | null; currency: string | null; symbol: string | null; us_ticker: string | null
  loaded: 'etf' | 'stock' | 'no' | null; hidden?: boolean; position: Position | null; trades: Trade[]; dividends: Div[]; prices: { dates: string[]; close: number[] }
  info: Info; account_currency: string; other_currency: string | null; fx: Fx; live: boolean; as_of: string | null; errors: string[]; first_bought: string | null
}
const RANGES = ['1M', '3M', '6M', 'YTD', '1Y', '3Y', '5Y', '10Y', 'Max']
const has = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

/** One holding: your position, a price chart with your buys and sells, the company, your trades and dividends. */
export function HoldingPage({ t212 }: { t212: string }) {
  const url = `/api/portfolio/holding/${encodeURIComponent(t212)}`
  const stored = useQuery({ queryKey: ['pf', 'holding', t212], queryFn: () => api.get<HoldingData>(url), retry: false })
  const live = useQuery({ queryKey: ['pf', 'holding', t212, 'live'], queryFn: () => api.get<HoldingData>(url + '?live=1'), retry: false, enabled: !!stored.data })
  const h = live.data?.found ? live.data : stored.data
  const { m, ms } = useMoney(h?.account_currency, h?.other_currency, h?.fx?.rate)
  const back = <Button asChild variant="ghost" size="sm" className="-ml-2 self-start"><Link to="/portfolio"><ArrowLeft /> Portfolio</Link></Button>

  if (stored.isLoading) return <Page title=""><Card><Empty>Loading…</Empty></Card></Page>
  if (!h || !h.found) return <Page title="Holding">{back}<Card><Empty>This holding is not in your portfolio.</Empty></Card></Page>

  const p = h.position
  const I = h.info || {}
  const sym = h.us_ticker || t212Symbol(h.t212_ticker)
  const full = h.us_ticker ? (h.loaded === 'etf' ? `/etf/${h.us_ticker}` : h.loaded === 'stock' ? `/stock/${h.us_ticker}` : null) : null
  const today = has(p?.today_pct) ? p.today_pct : null
  const sub = [I.fullExchangeName || I.exchange, h.symbol && h.symbol !== sym ? `Yahoo ${h.symbol}` : '', h.isin, I.sector, I.country].filter(Boolean).join(' · ')
  return (
    <main className="flex flex-col gap-5 p-6">
      {back}
      <div className="flex flex-wrap items-start gap-4">
        <Logo t={h.us_ticker || h.symbol} size={44} />
        <div className="min-w-0">
          <h1 className="text-[22px] font-bold tracking-tight">{h.name} <span className="font-medium text-muted-foreground">({sym})</span></h1>
          <div className="mt-0.5 text-[13px] text-muted-foreground">{sub}</div>
          {p && (
            <div className="mt-2 flex items-baseline gap-2">
              <span className="num text-[26px] font-bold">{sharePrice(p.current_price, p.currency)}</span>
              {today !== null && <ChangePill value={today}>{pctTxt(today)}</ChangePill>}
              <span className="text-xs text-muted-foreground">{p.currency} · {h.live ? `live ${h.as_of ?? ''}` : 'last sync'}</span>
            </div>
          )}
        </div>
        <div className="grow" />
        <div className="flex items-center gap-2">
          <CurrencySwitch acc={h.account_currency} other={h.other_currency} rate={h.fx?.rate} />
          {p && <AlertBell sid={h.us_ticker || h.t212_ticker} withLabel etf={h.loaded === 'etf'} />}
          <HideButton t212={h.t212_ticker} hidden={!!h.hidden} />
          {full && <Button asChild><Link to={full}>Full {h.loaded === 'etf' ? 'ETF' : 'company'} page <ArrowRight /></Link></Button>}
          {h.us_ticker && h.loaded === 'no' && <Button asChild><Link to={`/load/${h.us_ticker}`}>Load the full company page <ArrowRight /></Link></Button>}
        </div>
      </div>

      {h.hidden && (
        <p className="text-[13px] text-muted-foreground">
          Hidden from your portfolio lists (only on this computer). Totals, returns and box 3 still count it.
        </p>
      )}
      {p ? (
        <Card className="grid grid-cols-2 gap-x-6 gap-y-4 p-4 md:grid-cols-3 xl:grid-cols-6">
          <Stat label="Shares" value={qty(p.quantity)} sub={`held since ${h.first_bought ? day(h.first_bought) : '–'}`} />
          <Stat label="Value" value={m(p.value)} sub={`${fmt(p.weight, 'pct')} of your holdings`} />
          <Stat label="Avg. price" value={sharePrice(p.avg_price, p.currency)} sub={`you paid ${m(p.cost)}`} />
          <Stat label="Return" value={ms(p.pl)} className={tone(p.pl)} sub={pctTxt(p.pl_pct)} />
          <Stat label="Currency effect" value={ms(p.fx_impact)} className={tone(p.fx_impact)} sub="part of the return" />
          <Stat label="Dividends" value={m(p.dividends)} sub={`${h.dividends.length} payment${h.dividends.length === 1 ? '' : 's'}`} />
        </Card>
      ) : (
        <p className="text-[13px] text-muted-foreground">You no longer hold this. Your trades and dividends are below.</p>
      )}

      <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <PriceChart h={h} />
        <Card className="p-4">
          <H2>About</H2>
          <About I={I} h={h} />
        </Card>
      </div>
      <div className="grid items-start gap-4 lg:grid-cols-2">
        <Card className="min-w-0 p-4">
          <H2>Your trades</H2>
          {h.trades.length ? (
            <div className="overflow-x-auto">
              <table className="tbl tight">
                <thead><tr><th>Date</th><th /><th>Shares</th><th>Price</th><th>Amount</th><th>Fees</th></tr></thead>
                <tbody>
                  {h.trades.map((t, i) => (
                    <tr key={i}>
                      <td>{day(t.date)}</td>
                      <td className={cn('font-medium', t.side === 'BUY' ? 'text-up' : 'text-down')}>{t.type === 'TRADE' ? (t.side === 'BUY' ? 'Buy' : 'Sell') : (t.type || '').replace(/_/g, ' ').toLowerCase()}</td>
                      <td>{qty(t.quantity)}</td>
                      <td>{t.price ? sharePrice(t.price, t.currency) : '–'}</td>
                      <td>{m(t.amount)}</td>
                      <td className="text-muted-foreground">{t.fees ? m(t.fees) : '–'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-[13px] text-muted-foreground">No trades.</p>
          )}
        </Card>
        <Card className="min-w-0 p-4">
          <H2>Dividends</H2>
          {h.dividends.length ? (
            <div className="overflow-x-auto">
              <table className="tbl tight">
                <thead><tr><th>Paid</th><th>Shares</th><th>Per share</th><th>Received</th></tr></thead>
                <tbody>
                  {h.dividends.map((d, i) => (
                    <tr key={i}>
                      <td>{day(d.date)}</td>
                      <td>{qty(d.quantity)}</td>
                      <td>{has(d.per_share) ? `${d.per_share.toFixed(2)} ${h.currency === 'GBX' ? 'GBp' : h.currency || ''}` : '–'}</td>
                      <td>{m(d.amount)}</td>
                    </tr>
                  ))}
                  <tr className="font-semibold"><td>Total</td><td /><td /><td>{m(h.dividends.reduce((a, d) => a + (d.amount || 0), 0))}</td></tr>
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-[13px] text-muted-foreground">No dividends received yet.</p>
          )}
        </Card>
      </div>
      {(h.us_ticker || h.symbol) && <NewsCard ticker={h.us_ticker} symbol={h.symbol} />}
      {!!h.errors?.length && <p className="text-xs text-down">{h.errors[0]}</p>}
    </main>
  )
}

function PriceChart({ h }: { h: HoldingData }) {
  const c = useChartColors()
  const { settings, pref, setPref } = useSettings()
  const range = pref<string>('ui2', 'price_range', settings.price_range)
  const gbx = h.currency === 'GBX' || h.currency === 'GBp'
  const pc = gbx ? 'GBP' : h.currency
  const px = h.prices
  const view = useMemo(() => {
    if (!px.dates.length) return null
    const last = px.dates[px.dates.length - 1]
    const since = range === 'Max' ? px.dates[0] : sinceDate(range, last)
    const i0 = Math.max(0, px.dates.findIndex((d) => d >= since))
    const dates = px.dates.slice(i0)
    const close = px.close.slice(i0)
    return { dates, close, change: close[close.length - 1] / close[0] - 1 }
  }, [px, range])
  const option = useMemo(() => {
    if (!view) return null
    const { dates, close } = view
    const marks = (side: 'BUY' | 'SELL') =>
      h.trades
        .filter((t) => t.side === side && has(t.chart_price) && t.date >= dates[0])
        .map((t) => {
          const i = dates.findIndex((d) => d >= t.date)
          return i < 0 ? null : [dates[i], t.chart_price]
        })
        .filter(Boolean)
    const avg = h.position && has(h.position.avg_price) ? h.position.avg_price * (gbx ? 0.01 : 1) : null
    const o = baseOption(c)
    return {
      ...o,
      grid: { left: 8, right: 8, top: 16, bottom: 4, containLabel: true },
      xAxis: { ...o.xAxis, type: 'category', data: dates, boundaryGap: false, axisLabel: { ...o.xAxis.axisLabel, hideOverlap: true, ...dateAxis(dates) } },
      yAxis: {
        ...o.yAxis, position: 'right',
        min: avg ? (v: { min: number }) => Math.min(v.min, avg) * 0.98 : undefined,
        max: avg ? (v: { max: number }) => Math.max(v.max, avg) * 1.02 : undefined,
        axisLabel: { ...o.yAxis.axisLabel, formatter: (v: number) => (Math.abs(v) >= 100 ? Math.round(v).toLocaleString('en-US') : v.toFixed(2)) },
      },
      tooltip: {
        ...o.tooltip,
        formatter: (ps: { axisValue: string; seriesName: string; value: number }[]) => {
          const d = ps[0].axisValue
          const line = ps.find((x) => x.seriesName === 'Price')
          const tr = h.trades.filter((t) => t.date === d && t.type === 'TRADE')
          return `${day(d)}<br>${line ? cash(line.value, pc) : ''}` + tr.map((t) => `<br><b style="color:${t.side === 'BUY' ? c.up : c.down}">${t.side === 'BUY' ? 'Bought' : 'Sold'}</b> ${qty(t.quantity)} @ ${sharePrice(t.price, t.currency)}`).join('')
        },
      },
      series: [
        {
          name: 'Price', type: 'line', data: close, showSymbol: false, lineStyle: { width: 1.6, color: c.series[0] }, itemStyle: { color: c.series[0] },
          markLine: avg ? { symbol: 'none', silent: true, label: { formatter: `your avg. ${cash(avg, pc)}`, color: c.muted, position: 'insideStartTop', fontSize: 11 }, lineStyle: { type: 'dashed', color: c.muted }, data: [{ yAxis: avg }] } : undefined,
        },
        { name: 'Buys', type: 'scatter', data: marks('BUY'), symbol: 'triangle', symbolSize: 11, itemStyle: { color: c.up }, z: 5 },
        { name: 'Sells', type: 'scatter', data: marks('SELL'), symbol: 'triangle', symbolRotate: 180, symbolSize: 11, itemStyle: { color: c.down }, z: 5 },
      ],
    }
  }, [view, c, h, gbx, pc])
  return (
    <Card className="min-w-0 p-4">
      <H2 side={<><Segmented label="Range of the price chart" value={range} onChange={(v) => setPref('ui2', 'price_range', v)} options={RANGES.map((r) => ({ value: r, label: r }))} />{view && <ChangePill value={view.change}>{pctTxt(view.change)}</ChangePill>}</>}>Price</H2>
      {option ? <EChart option={option} className="h-[320px]" /> : <Empty className="h-[320px]">{h.symbol ? 'No price history from Yahoo yet.' : 'No Yahoo symbol found for this holding.'}</Empty>}
      <HowToRead className="mt-2">Green triangles: your buys, red: your sells, dashed line: your average price. Split-adjusted, in {pc}.</HowToRead>
    </Card>
  )
}

function About({ I, h }: { I: Info; h: HoldingData }) {
  const cur = I.currency === 'GBp' ? 'GBP' : (I.currency as string | undefined)
  const n = (k: string) => (typeof I[k] === 'number' ? (I[k] as number) : null)
  const dy = n('dividendYield')
  const rows: [string, string | null][] = [
    ['Sector', (I.sector as string) || null],
    ['Industry', (I.industry as string) || null],
    ['Country', (I.country as string) || null],
    ['Market cap', n('marketCap') ? (CUR_SYM[cur ?? ''] ?? '') + big(n('marketCap')) + (CUR_SYM[cur ?? ''] ? '' : ` ${cur ?? ''}`) : null],
    ['P/E (TTM)', n('trailingPE') ? fmt(n('trailingPE'), 'x') : null],
    ['P/E (forward)', n('forwardPE') ? fmt(n('forwardPE'), 'x') : null],
    ['Dividend yield', dy ? dy.toFixed(2) + '%' : null], // Yahoo gives this one in percent nowadays
    ['52-week range', n('fiftyTwoWeekLow') && n('fiftyTwoWeekHigh') ? `${n('fiftyTwoWeekLow')!.toFixed(2)} – ${n('fiftyTwoWeekHigh')!.toFixed(2)}` : null],
    ['Employees', n('fullTimeEmployees') ? n('fullTimeEmployees')!.toLocaleString('en-US') : null],
  ]
  const shown = rows.filter(([, v]) => v)
  const site = I.website as string | undefined
  if (!shown.length && !I.longBusinessSummary) return <p className="text-[13px] text-muted-foreground">{h.symbol ? 'Company details are loaded from Yahoo when you are online.' : 'No Yahoo symbol found for this holding.'}</p>
  return (
    <>
      {shown.map(([l, v]) => <Kv key={l} label={l}>{v}</Kv>)}
      {site && <Kv label="Website"><a href={site} target="_blank" rel="noreferrer" className="text-primary hover:underline">{site.replace(/^https?:\/\//, '')}</a></Kv>}
      {I.longBusinessSummary && <p className="mt-3 line-clamp-[8] text-[13px] leading-relaxed text-muted-foreground">{I.longBusinessSummary}</p>}
      <p className="mt-3 text-xs text-muted-foreground">From Yahoo Finance{h.us_ticker ? '; the full company page has the SEC financials' : ''}.</p>
    </>
  )
}

/** Leave this holding out of the portfolio lists (prefs "pf_hidden"); totals and box 3 still count it. */
function HideButton({ t212, hidden }: { t212: string; hidden: boolean }) {
  const { setPref } = useSettings()
  const qc = useQueryClient()
  const toast = useToast()
  const navigate = useNavigate()
  const flip = () => {
    setPref('pf_hidden', t212, hidden ? null : true)
    window.setTimeout(() => {                     // the setting is saved 0.4 s later
      void qc.invalidateQueries({ queryKey: ['portfolio'] })
      void qc.invalidateQueries({ queryKey: ['pf'] })
    }, 600)
    toast(hidden ? 'Shown in your portfolio lists again.' : 'Hidden from your portfolio lists. Undo under Settings, Hidden holdings.')
    if (!hidden) navigate('/portfolio')
  }
  return (
    <Hint label={hidden ? 'Show it in the portfolio lists again' : 'Hide it from the portfolio lists (only for you; totals and box 3 still count it)'}>
      <Button variant="ghost" size="icon" aria-label={hidden ? 'Show in lists' : 'Hide from lists'} onClick={flip}>
        {hidden ? <Eye /> : <EyeOff />}
      </Button>
    </Hint>
  )
}
