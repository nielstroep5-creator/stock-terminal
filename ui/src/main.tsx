import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router-dom'
import App from '@/App'
import { TooltipProvider } from '@/components/ui/tooltip'
import { SettingsProvider } from '@/lib/settings'
import { ToastProvider } from '@/lib/toast'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, refetchOnWindowFocus: false, retry: 1 } },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <SettingsProvider>
        <TooltipProvider>
          <HashRouter>
            <ToastProvider>
              <App />
            </ToastProvider>
          </HashRouter>
        </TooltipProvider>
      </SettingsProvider>
    </QueryClientProvider>
  </StrictMode>,
)
