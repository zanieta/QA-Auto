import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Vite serves /fixtures/* from public/fixtures so the UI works
// against fixtures/sample_run_state.json before the backend exists.
// Once server.py is up, /runs/* and /manual/* are proxied to it (no CORS in dev).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/runs': 'http://127.0.0.1:8000',
      '/manual': 'http://127.0.0.1:8000',
      '/config': 'http://127.0.0.1:8000',
      '/reports': 'http://127.0.0.1:8000',
    },
  },
})
