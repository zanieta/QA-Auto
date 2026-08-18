import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Vite serves /fixtures/* from public/fixtures so the UI works
// against fixtures/sample_run_state.json before the backend exists.
// Once server.py is up, the API prefixes below are proxied to it (no CORS in dev).
//
// EVERY prefix server.py answers must be listed here. An unlisted prefix does
// NOT 404 — it falls through to Vite's SPA fallback and returns index.html, so
// the caller's res.json() dies on "Unexpected token '<', "<!doctype "...". That
// is exactly what happened to /cycles and /testcases: they shipped with the
// TR/TC rail browser and were never added here, so the rail read "0 of 0" in
// dev while working fine in production (where server.py serves the built app
// same-origin and no proxy is involved). Add new prefixes as you add endpoints.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/runs': 'http://127.0.0.1:8000',
      '/manual': 'http://127.0.0.1:8000',
      '/config': 'http://127.0.0.1:8000',
      '/reports': 'http://127.0.0.1:8000',
      '/cycles': 'http://127.0.0.1:8000',
      '/testcases': 'http://127.0.0.1:8000',
    },
  },
})
