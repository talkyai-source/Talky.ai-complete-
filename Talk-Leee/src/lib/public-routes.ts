// The public, unauthenticated routes, in one place for two consumers: the
// sitemap (src/app/sitemap.ts) publishes exactly these, and the sign-up popup
// (src/components/ui/signup-popup.tsx) shows only on these. A public page
// added here is published and gets the popup in a single edit; a page added
// anywhere else gets neither by default.
//
// Every entry must be a confirmed public, unauthenticated route (see
// isPublicPath in src/proxy.ts and the static marketing pages under
// industries/ and use-cases/). Do not add authenticated, admin, or
// white-label paths. Entries carry no trailing slash; next.config.ts adds it
// to canonical URLs, and in that form usePathname() reports it too, so
// isPublicRoute drops one trailing slash before comparing.
export const PUBLIC_ROUTES = [
  "/",
  "/ai-voice-dialer",
  "/ai-assist",
  "/ai-voice-agent",
  "/ai-voices",
  "/contact",
  "/pricing",
  "/privacy",
  "/terms",
  "/docs",
  "/docs/getting-started",
  "/docs/outbound-campaigns",
  "/docs/contacts",
  "/docs/inbound",
  "/docs/calls-and-recordings",
  "/docs/follow-ups",
  "/docs/connectors",
  "/docs/ai-options",
  "/docs/billing",
  "/docs/settings-and-security",
  "/docs/partners",
  "/docs/workflows",
  "/docs/troubleshooting",
  "/docs/glossary",
  "/industries/education",
  "/industries/financial-services",
  "/industries/healthcare",
  "/industries/marketing-automation",
  "/industries/professional-services",
  "/industries/real-estate",
  "/industries/recruitment",
  "/industries/retail-ecommerce",
  "/industries/software-tech-support",
  "/industries/travel-industry",
  "/use-cases/automated-lead-qualification",
  "/use-cases/customer-services-support",
] as const;

/**
 * True only for an exact entry of PUBLIC_ROUTES, read with or without the
 * canonical trailing slash ("/pricing" and "/pricing/" are the same route).
 * No prefix matching: /contacts is not permitted by /contact, and a docs
 * slug is permitted only because it is listed itself.
 */
export function isPublicRoute(pathname: string): boolean {
  const route = pathname.length > 1 && pathname.endsWith("/") ? pathname.slice(0, -1) : pathname;
  return (PUBLIC_ROUTES as readonly string[]).includes(route);
}
