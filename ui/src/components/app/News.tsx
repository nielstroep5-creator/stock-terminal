import { useQuery } from '@tanstack/react-query'
import { ExternalLink, Newspaper } from 'lucide-react'
import { Card } from '@/components/app/Page'
import { Empty, H2 } from '@/components/app/bits'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import { useSettings } from '@/lib/settings'

// News per stock (stockterminal/news.py): the stored news at once, then Yahoo's latest (kept half an hour).
// Used on the stock page (Overview) and on a holding's page. Give the app's ticker, or a Yahoo symbol.

type NewsItem = { id: string | null; title: string; source: string | null; url: string; published: string | null; summary: string | null; thumb: string | null }
type News = { symbol: string; items: NewsItem[]; fetched: number | null; stale: boolean; error?: string }

const FIRST = 6

function ago(iso: string | null) {
  if (!iso) return ''
  const d = new Date(iso)
  const min = Math.round((Date.now() - d.getTime()) / 60000)
  if (min < 1) return 'just now'
  if (min < 60) return `${min} min ago`
  if (min < 24 * 60) return `${Math.round(min / 60)} h ago`
  if (min < 7 * 24 * 60) return `${Math.round(min / 60 / 24)} d ago`
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', ...(d.getFullYear() !== new Date().getFullYear() ? { year: 'numeric' } : {}) })
}

export function NewsCard({ ticker, symbol, name }: { ticker?: string | null; symbol?: string | null; name?: string | null }) {
  const { pref, setPref } = useSettings()
  const q = ticker ? `ticker=${encodeURIComponent(ticker)}` : symbol ? `symbol=${encodeURIComponent(symbol)}` : ''
  const stored = useQuery({ queryKey: ['news', q, 'stored'], queryFn: () => api.get<News>(`/api/news?${q}`), enabled: !!q })
  const fresh = useQuery({ queryKey: ['news', q, 'fresh'], queryFn: () => api.get<News>(`/api/news?${q}&refresh=1`), enabled: !!q, retry: false, staleTime: 10 * 60_000 })
  if (!q) return null
  const d = fresh.data ?? stored.data
  const all = pref<boolean>('ui2', 'news_all', false)
  const items = d?.items ?? []
  const shown = all ? items : items.slice(0, FIRST)
  const status = fresh.isFetching ? 'Looking for the latest…' : d?.stale ? 'Offline: the news from before' : d?.fetched ? `From Yahoo · ${ago(new Date(d.fetched * 1000).toISOString())}` : ''
  return (
    <Card className="min-w-0">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b px-4 py-3">
        <H2>News{name ? ` about ${name}` : ''}</H2>
        <span className="text-xs text-muted-foreground">{status}</span>
      </div>
      {!items.length ? (
        <Empty className="py-8"><Newspaper className="size-5" />{stored.isLoading || fresh.isFetching ? 'Loading news…' : 'No news found for this stock.'}</Empty>
      ) : (
        <ul className="divide-y">
          {shown.map((n) => (
            <li key={n.url}>
              <a href={n.url} target="_blank" rel="noreferrer" className="group flex gap-4 px-4 py-3 hover:bg-panel">
                <div className="min-w-0 flex-1">
                  <div className="text-[13.5px] font-medium leading-snug group-hover:text-primary">
                    {n.title}
                    <ExternalLink className="ml-1.5 inline size-3 align-baseline text-muted-foreground opacity-0 group-hover:opacity-100" />
                  </div>
                  {n.summary && <p className="mt-1 line-clamp-2 text-[12.5px] text-muted-foreground">{n.summary}</p>}
                  <div className="mt-1 text-xs text-muted-foreground">{[n.source, ago(n.published)].filter(Boolean).join(' · ')}</div>
                </div>
                {n.thumb && <img src={n.thumb} alt="" loading="lazy" referrerPolicy="no-referrer" className="hidden h-16 w-24 shrink-0 rounded-md bg-secondary object-cover sm:block" onError={(e) => { e.currentTarget.style.display = 'none' }} />}
              </a>
            </li>
          ))}
        </ul>
      )}
      {items.length > FIRST && (
        <div className="border-t px-4 py-2">
          <Button size="sm" variant="ghost" onClick={() => setPref('ui2', 'news_all', !all)}>{all ? 'Show less' : `Show all ${items.length}`}</Button>
        </div>
      )}
    </Card>
  )
}
