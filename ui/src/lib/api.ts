// Talks to the local Python server. Every page of the new screen uses the same /api endpoints as the old one.
export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const j = await res.json()
      if (typeof j?.detail === 'string') detail = j.detail
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail)
  }
  return (await res.json()) as T
}

export const api = {
  get: <T,>(path: string) => request<T>('GET', path),
  put: <T,>(path: string, body: unknown) => request<T>('PUT', path, body),
  post: <T,>(path: string, body?: unknown) => request<T>('POST', path, body),
  del: <T,>(path: string) => request<T>('DELETE', path),
}

export type SearchHit = { ticker: string; name: string; loaded: boolean; etf?: boolean }

/** One row of /api/companies (Home: biggest moves). */
export type CompanyRow = { ticker: string; name: string; sector?: string | null; close?: number | null; change?: number | null; market_cap?: number | null; pe_ttm?: number | null; date?: string | null; live?: boolean; in_screener?: number | boolean }
