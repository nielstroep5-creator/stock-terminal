import { Suspense, lazy, useCallback, useState } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { PageError } from '@/components/app/PageError'
import { SearchPalette } from '@/components/app/SearchPalette'
import { Sidebar } from '@/components/app/Sidebar'
import { Topbar } from '@/components/app/Topbar'
import { UpdatePrompt } from '@/components/app/AppUpdate'
import { DataPrompt } from '@/components/app/DataSetup'
import { Welcome } from '@/components/app/Welcome'
import { useApplyTheme, useSettings } from '@/lib/settings'
import { ComingSoon } from '@/pages/ComingSoon'
import { HomePage } from '@/pages/Home'
import { LoadPage } from '@/pages/Load'
import { SettingsPage } from '@/pages/Settings'
import { StockPage } from '@/pages/stock/StockPage'

// Pages loaded when they are first opened, so the app starts with less JavaScript (Home and the stock page
// are in the first file).
const AlertsPage = lazy(() => import('@/pages/Alerts').then((m) => ({ default: m.AlertsPage })))
const ComparePage = lazy(() => import('@/pages/compare/ComparePage').then((m) => ({ default: m.ComparePage })))
const DataHealthPage = lazy(() => import('@/pages/DataHealth').then((m) => ({ default: m.DataHealthPage })))
const EarningsPage = lazy(() => import('@/pages/Earnings').then((m) => ({ default: m.EarningsPage })))
const EtfPage = lazy(() => import('@/pages/Etf').then((m) => ({ default: m.EtfPage })))
const EtfsPage = lazy(() => import('@/pages/Etf').then((m) => ({ default: m.EtfsPage })))
const PortfolioRoute = lazy(() => import('@/pages/portfolio/PortfolioPage').then((m) => ({ default: m.PortfolioRoute })))
const ScreenerPage = lazy(() => import('@/pages/Screener').then((m) => ({ default: m.ScreenerPage })))
const TaxPage = lazy(() => import('@/pages/Tax').then((m) => ({ default: m.TaxPage })))
const WatchlistPage = lazy(() => import('@/pages/Watchlist').then((m) => ({ default: m.WatchlistPage })))


export default function App() {
  const { settings } = useSettings()
  useApplyTheme(settings.theme)
  const [searching, setSearching] = useState(false)
  const openSearch = useCallback(() => setSearching(true), [])

  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar onSearch={openSearch} />
        <PageError>
        <Suspense fallback={<main className="p-6 text-muted-foreground">Loading…</main>}>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/portfolio/:sub?" element={<PortfolioRoute />} />
          <Route path="/tax/:year?" element={<TaxPage />} />
          <Route path="/watchlist/:id?" element={<WatchlistPage />} />
          <Route path="/earnings/:scope?" element={<EarningsPage />} />
          <Route path="/companies" element={<Navigate to="/screener" replace />} />
          <Route path="/screener" element={<ScreenerPage />} />
          <Route path="/data-health" element={<DataHealthPage />} />
          <Route path="/etfs" element={<EtfsPage />} />
          <Route path="/etf/:ticker/:tab?" element={<EtfPage />} />
          <Route path="/stock/:ticker/:tab?" element={<StockPage />} />
          <Route path="/load/:ticker" element={<LoadPage />} />
          <Route path="/compare" element={<ComparePage />} />
          <Route path="/alerts/:tab?" element={<AlertsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<ComingSoon title="Page not found" />} />
        </Routes>
        </Suspense>
        </PageError>
      </div>
      <SearchPalette open={searching} onOpenChange={setSearching} />
      <Welcome />
      <DataPrompt />
      <UpdatePrompt />
    </div>
  )
}
