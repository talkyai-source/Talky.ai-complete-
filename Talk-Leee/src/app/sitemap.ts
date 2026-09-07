import type { MetadataRoute } from "next";

const SITE_URL = "https://talkleeai.com";

// Every entry here must be a confirmed public, unauthenticated route (see
// isPublicPath in src/proxy.ts and the static marketing pages under
// industries/ and use-cases/). Do not add authenticated, admin, or
// white-label paths.
const PUBLIC_ROUTES = [
  "/",
  "/ai-voice-dialer",
  "/ai-assist",
  "/ai-voice-agent",
  "/ai-voices",
  "/contact",
  "/privacy",
  "/terms",
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
];

export default function sitemap(): MetadataRoute.Sitemap {
  const lastModified = new Date();

  return PUBLIC_ROUTES.map((route) => ({
    // next.config.ts sets trailingSlash: true, so the canonical form of
    // every page (other than the root) carries a trailing slash.
    url: route === "/" ? `${SITE_URL}/` : `${SITE_URL}${route}/`,
    lastModified,
  }));
}
