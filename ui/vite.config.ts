import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The finished files go into the Python package, so the app serves them without Node.js.
// `npm run dev` proxies /api to the running app (python -m stockterminal.app --serve).
export default defineConfig({
  base: '/static/new/',
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': path.resolve(import.meta.dirname, 'src') } },
  build: {
    outDir: '../stockterminal/app/static/new',
    emptyOutDir: true,
    chunkSizeWarningLimit: 1500,
  },
  server: { proxy: { '/api': 'http://127.0.0.1:8765', '/static/logo.svg': 'http://127.0.0.1:8765' } },
})
