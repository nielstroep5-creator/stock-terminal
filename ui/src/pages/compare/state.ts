// The Compare page keeps the whole chart in its address, exactly like the old screen, so Back / Forward, saved
// charts and links from other pages work in both designs:
//   #/compare?t=AAPL,MSFT&v=fin|price
//   fin:   f=number~number~number (up to 3), c=line~bar~line, p=FY|Q|TTM, m=value|growth|index, from=year, to=year,
//          vl=auto|on|off (numbers on the chart), e=0 (no analyst estimates), h=AAPL (hidden companies)
//   price: r=1D..All, pm=price|total, sc=lin|log
export type Period = 'FY' | 'Q' | 'TTM'
export type Mode = 'value' | 'growth' | 'index'
export type ChartType = 'line' | 'bar'
export type Chart = {
  t: string[]; v: '' | 'fin' | 'price'; f: string[]; c: ChartType[]; p: Period; m: Mode; from: string; to: string
  r: string; pm: 'price' | 'total'; sc: 'lin' | 'log'; vl: 'auto' | 'on' | 'off'; e: boolean; h: string[]; y: string
}
export type Field = { key: string; label: string; fmt: string }
export type FieldGroup = { group: string; fields: Field[] }

export const MAX_NUMBERS = 3
export const SHAPES = ['circle', 'diamond', 'triangle'] as const
export const PRICE_RANGES = ['1D', '5D', '1M', '3M', '6M', 'YTD', '1Y', '3Y', '5Y', '10Y', 'Max', 'All']
const RANGE_DAYS: Record<string, number> = { '1M': 31, '3M': 92, '6M': 183, '1Y': 365, '3Y': 1096, '5Y': 1826, '10Y': 3652 }
export const EXTRA_COLORS = ['#0b3d91', '#c0392b', '#16a085', '#f39c12', '#2c3e50', '#7f8c8d', '#a0522d', '#ff6f91']

const list = (s: string | null, sep: string) => (s || '').split(sep).filter(Boolean)
const oneOf = <T extends string>(v: string | null, all: readonly T[], fallback: T) => (all.includes(v as T) ? (v as T) : fallback)

export function readChart(p: URLSearchParams): Chart {
  const f = list(p.get('f') || 'item:revenue', '~').slice(0, MAX_NUMBERS)
  const c = list(p.get('c') || 'line', '~')
  return {
    t: list(p.get('t'), ',').map((x) => x.toUpperCase()),
    v: oneOf(p.get('v'), ['', 'fin', 'price'] as const, ''),
    f,
    c: f.map((_, i) => (c[i] === 'bar' ? 'bar' : 'line')),
    p: oneOf(p.get('p'), ['FY', 'Q', 'TTM'] as const, 'TTM'),
    m: oneOf(p.get('m'), ['value', 'growth', 'index'] as const, 'value'),
    from: p.get('from') || '',
    to: p.get('to') || '',
    r: oneOf(p.get('r'), PRICE_RANGES, '1Y'),
    pm: oneOf(p.get('pm'), ['price', 'total'] as const, 'price'),
    sc: oneOf(p.get('sc'), ['lin', 'log'] as const, 'lin'),
    vl: oneOf(p.get('vl'), ['auto', 'on', 'off'] as const, 'auto'),
    e: p.get('e') !== '0',
    h: list(p.get('h'), ','),
    y: p.get('y') || '',
  }
}

export function writeChart(c: Chart): string {
  const p = new URLSearchParams({ t: c.t.join(','), v: c.v, f: c.f.join('~'), c: c.c.join('~'), p: c.p, m: c.m, from: c.from, to: c.to, r: c.r, pm: c.pm, sc: c.sc, vl: c.vl })
  if (!c.v) p.delete('v')
  if (!c.e) p.set('e', '0')
  if (c.h.length) p.set('h', c.h.join(','))
  if (c.y) p.set('y', c.y)
  return p.toString()
}

export function sinceDate(range: string, lastDate: string) {
  const last = new Date(lastDate)
  if (range === 'YTD') return `${last.getFullYear()}-01-01`
  return new Date(last.getTime() - (RANGE_DAYS[range] ?? 1e6) * 864e5).toISOString().slice(0, 10)
}

/** Own colour per company, kept like the old screen does: "AAPL:#1d5fd1,NVDA:#27ae60". */
export const colorMap = (text: string): Record<string, string> => Object.fromEntries(text.split(',').filter(Boolean).map((x) => x.split(':') as [string, string]))

const mix = (hex: string, k: number) => {
  const h = hex.replace('#', '')
  if (h.length !== 6) return hex
  const ch = (i: number) => Math.round(parseInt(h.slice(i, i + 2), 16) * (1 - k) + 255 * k).toString(16).padStart(2, '0')
  return `#${ch(0)}${ch(2)}${ch(4)}`
}
/** Colour of one company's number. One company: every number its own colour. Several companies: the
    company's colour, a lighter shade of it for the 2nd and 3rd number. */
export function numberColor(colors: string[], palette: string[], ti: number, mi: number) {
  const own = colors[ti]
  if (colors.length === 1) return [own, ...palette.filter((c) => c !== own)][mi]
  return mi ? mix(own, mi === 1 ? 0.35 : 0.55) : own
}

export const csvLine = (cells: unknown[]) => cells.map((v) => (/[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v))).join(',')
