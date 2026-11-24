// How numbers are written, the same on every page (the same rules as the old screen).
export type Fmt = 'pct' | 'pct2' | 'x' | 'usdps' | 'usd' | 'shares' | 'num'
export type Unit = 'M' | 'B'

const DASH = '–'
const isNum = (v: unknown): v is number => typeof v === 'number' && !Number.isNaN(v)

/** 5.18T, 478.83B, 12.3M */
export function big(v: number | null | undefined): string {
  if (!isNum(v)) return DASH
  const a = Math.abs(v)
  if (a >= 1e12) return (v / 1e12).toFixed(2) + 'T'
  if (a >= 1e9) return (v / 1e9).toFixed(a >= 1e11 ? 1 : 2) + 'B'
  if (a >= 1e6) return (v / 1e6).toFixed(1) + 'M'
  return v.toLocaleString('en-US', { maximumFractionDigits: 2 })
}

export function fmt(v: number | null | undefined, f: Fmt | string = 'num', opts: { unit?: Unit; noX?: boolean } = {}): string {
  if (!isNum(v)) return DASH
  const a = Math.abs(v)
  switch (f) {
    case 'pct':
      return (v * 100).toFixed(1) + '%'
    case 'pct2':
      return (v * 100).toFixed(2) + '%'
    case 'x':
      return v.toFixed(a >= 100 ? 0 : 1) + (opts.noX ? '' : 'x')
    case 'usdps':
      return v.toFixed(2)
    case 'shares':
    case 'usd':
      if (opts.unit === 'M') return (v / 1e6).toLocaleString('en-US', { maximumFractionDigits: a < 1e8 ? 1 : 0 })
      if (opts.unit === 'B') return (v / 1e9).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
      return big(v)
    default:
      return v.toLocaleString('en-US', { maximumFractionDigits: 2 })
  }
}

/** +1.17% / −0.40% */
export function pctSigned(v: number | null | undefined, digits = 2): string {
  if (!isNum(v)) return DASH
  return `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v * 100).toFixed(digits)}%`
}

export const money = (v: number | null | undefined) => (isNum(v) ? '$' + v.toFixed(2) : DASH)

/** Text colour for a number where up is good and down is bad. */
export const tone = (v: number | null | undefined) => (!isNum(v) || v === 0 ? '' : v > 0 ? 'text-up' : 'text-down')

/** SEC names come in capitals ("NVIDIA CORP"): make them readable. */
export function niceName(n: string | null | undefined): string {
  if (!n || n !== n.toUpperCase()) return n || ''
  return n
    .toLowerCase()
    .replace(/(^|[\s\-&(.])([a-z])/g, (_m, a: string, c: string) => a + c.toUpperCase())
    .replace(/ \/[a-z]{2}\/?$/i, '')
}

/** Nov 13, 2026 */
export function day(d: string | null | undefined, year = true): string {
  if (!d) return DASH
  return new Date(d.slice(0, 10) + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', ...(year ? { year: 'numeric' } : {}) })
}

/** Axis labels for daily dates: "Mar 4" on short ranges, "Mar '26" on longer ones, years on very long ones. */
export function dateTick(dates: string[]) {
  const span = dates.length > 1 ? (+new Date(dates[dates.length - 1]) - +new Date(dates[0])) / 864e5 : 0
  return (v: string) => {
    if (!/^\d{4}-\d\d-\d\d/.test(v)) return v
    const d = new Date(v.slice(0, 10) + 'T12:00:00')
    if (span > 12 * 366) return String(d.getFullYear())
    if (span > 100) return d.toLocaleDateString('en-US', { month: 'short' }) + " '" + String(d.getFullYear()).slice(2)
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  }
}

/** Axis labels for a daily date axis: only on the first day of a month (or year, or week for short ranges),
 *  thinned to about 8, so a label never repeats ("Mar '26 Mar '26") and the newest month shows too. */
export function dateAxis(dates: string[]) {
  const span = dates.length > 1 ? (+new Date(dates[dates.length - 1]) - +new Date(dates[0])) / 864e5 : 0
  const unit = span > 12 * 366 ? 'y' : span > 100 ? 'm' : 'd'
  const key = (v: string) => (unit === 'y' ? v.slice(0, 4) : unit === 'm' ? v.slice(0, 7) : v.slice(0, 10))
  const starts: number[] = []
  dates.forEach((v, i) => {
    if (i > 0 && key(v) !== key(dates[i - 1])) starts.push(i)
  })
  // for short ranges (days) label about weekly; otherwise every n-th month or year start
  const pool = unit === 'd' ? dates.map((_, i) => i).filter((i) => i > 0) : starts
  const n = Math.max(1, Math.ceil(pool.length / 8))
  const show = new Set(pool.filter((_, k) => (pool.length - 1 - k) % n === 0))
  return { formatter: dateTick(dates), interval: (i: number) => show.has(i) }
}
