/** Release configuration must be explicit; the dev proxy is not shipped. */
export function validateAdminBuildConfig(env: Record<string, string | undefined>): string {
  for (const flag of ['VITE_USE_DUMMY_AUTH', 'VITE_ADMIN_DEV_MODE']) {
    if (env[flag]?.trim().toLowerCase() === 'true') {
      throw new Error(`${flag} is forbidden in an Admin build. Use the development server for local bypasses.`);
    }
  }
  const base = env.VITE_API_BASE_URL?.trim();
  if (!base) throw new Error('VITE_API_BASE_URL is required for an Admin build.');
  // A same-origin deployment must actually route /api at its web server.
  if (base === '/api/v1') return base;
  let url: URL;
  try { url = new URL(base); }
  catch { throw new Error('VITE_API_BASE_URL must be an HTTPS API URL ending in /api/v1, or /api/v1 for a configured same-origin proxy.'); }
  const host = url.hostname.toLowerCase();
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash
      || url.pathname.replace(/\/$/, '') !== '/api/v1'
      || host === 'localhost' || host.endsWith('.localhost') || host.endsWith('.local')
      || host === '[::1]' || /^127\./.test(host) || host === '0.0.0.0') {
    throw new Error('VITE_API_BASE_URL must point to an HTTPS deployment API, with no credentials, query or fragment. Loopback URLs are development-only.');
  }
  return base.replace(/\/$/, '');
}
