import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import { validateAdminBuildConfig } from './build-config'

// Live backend to wire the local admin panel against. Override with
// VITE_PROXY_TARGET when pointing at a different backend (e.g. a local
// uvicorn on :8000). The browser only ever talks to the Vite dev origin,
// so this proxy sidesteps the server's CORS allow-list (which does NOT
// include localhost) — requests are forwarded server-side instead.
// https://vite.dev/config/
export default defineConfig(({ command, mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_');
  const apiBase = command === 'build' ? validateAdminBuildConfig(env) : (env.VITE_API_BASE_URL || '/api/v1');
  return {
  plugins: [react()],
  define: { 'import.meta.env.VITE_API_BASE_URL': JSON.stringify(apiBase) },
  server: {
    proxy: {
      // Everything the admin client calls lives under /api/v1/*.
      '/api': {
        target: env.VITE_PROXY_TARGET || 'https://api.talkleeai.com',
        changeOrigin: true,
        secure: true,
      },
    },
  },
  };
})
