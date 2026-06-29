import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Live backend to wire the local admin panel against. Override with
// VITE_PROXY_TARGET when pointing at a different backend (e.g. a local
// uvicorn on :8000). The browser only ever talks to the Vite dev origin,
// so this proxy sidesteps the server's CORS allow-list (which does NOT
// include localhost) — requests are forwarded server-side instead.
const PROXY_TARGET = process.env.VITE_PROXY_TARGET || 'https://api.talkleeai.com'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Everything the admin client calls lives under /api/v1/*.
      '/api': {
        target: PROXY_TARGET,
        changeOrigin: true,
        secure: true,
      },
    },
  },
})
