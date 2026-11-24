// The shapes the server sends for one company (stockterminal/app/server.py).
export type Company = {
  cik: number
  ticker: string
  name: string
  exchange?: string | null
  description?: string | null
  sector?: string | null
  sic?: string | number | null
  industry?: string | null
  employees?: number | null
  website?: string | null
  beta?: number | null
  fiscal_year_end?: string | null
}

type Nums = Record<string, number | null | undefined>

export type McapCheck = { text?: string; reason?: string; yahoo?: number; jumps?: { days: number; from: string; to: string }[] } | null

export type AccuracyItem = {
  item: string
  label: string
  worst: number | null
  missing: number
  periods: { end: string; ours: number | null; yahoo: number; diff: number | null }[]
}
export type Accuracy = { checked_at: string; compared: number; match: number; missing: number; off: AccuracyItem[] } | null

export type QualityNote = { text: string; count?: number; where?: string[]; level?: string }
export type Quality = {
  score: number
  checks: number
  failed: number
  checked_at: string
  details?: {
    issues?: QualityNote[]
    notes?: QualityNote[]
    history?: { first_fy?: number; last_fy?: number; predecessors?: { name?: string; cik: number; until?: string }[] }
  }
} | null

export type Analyst = {
  forward_pe?: number | null
  peg?: number | null
  target_mean?: number | null
  target_upside?: number | null
  rating_mean?: number | null
  analysts?: number | null
  next_fy?: string | null
  eps_growth_next?: number | null
  earnings_date?: string | null
  earnings_upcoming?: boolean
} | null

export type Quote = {
  regular?: { time?: number } | null
  extended?: { session: 'pre' | 'post' | 'overnight'; price: number; change?: number | null; change_pct: number; time: number; live?: boolean } | null
} | null

export type CompanyInfo = {
  company: Company
  price?: number | null
  change?: number | null
  price_date?: string | null
  price_live?: boolean
  valuation_stale?: string | null
  quote?: Quote
  valuation?: Nums | null
  ttm?: Nums | null
  metrics?: Nums | null
  balance?: Nums | null
  is_etf: boolean
  is_bank?: boolean
  is_insurer?: boolean
  has_financials: boolean
  needs_sec?: boolean
  has_segments: boolean
  has_prices: boolean
  has_dividends: boolean
  fy_range?: { a?: number; b?: number } | null
  quality?: Quality
  mcap_check?: McapCheck
  accuracy?: Accuracy
  analyst?: Analyst
}

export type Period = { key: string; end: string; fy: number; fq: number | null; cq: string; label: string }
export type Cell = { v: number | null; src?: string; m?: string; filed?: string; orig?: number; flag?: string } | null
export type Row = { key: string; label: string; statement?: string; group?: string; fmt: string; bold?: boolean; values: Record<string, Cell> }
export type Statements = { periods: Period[]; rows: Row[]; statements: Record<string, string>; currency?: string; converted_from?: string | null; annual_only?: boolean }

export type Prices = { dates: string[]; close: number[]; open?: (number | null)[]; high?: (number | null)[]; low?: (number | null)[] }
export type ChartEvents = {
  earnings: { date: string; form: string; period?: string | null; eps?: number | null }[]
  dividends: { date: string; amount: number }[]
}
export type Peer = { ticker: string; name: string; market_cap?: number | null; pe?: number | null; revenue_yoy?: number | null; operating_margin?: number | null; r_1y?: number | null; roe?: number | null; pb?: number | null; kind?: string }
export type Peers = { by: string; group?: string; me: Peer; peers: Peer[] }

/** The period one year before p (within a few weeks), used for growth against a year earlier. */
export function yearBefore(periods: Period[], p: Period): Period | undefined {
  const t = +new Date(p.end)
  return periods.find((q) => Math.abs(t - +new Date(q.end) - 365 * 864e5) < 25 * 864e5)
}
