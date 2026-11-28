import type { MetadataRoute } from "next";
import { PUBLIC_ROUTES } from "@/lib/public-routes";

const SITE_URL = "https://talkleeai.com";

// No lastModified: the only date available is the build time, which would
// claim every page changed on every deploy. Omitted until a real per-page date exists.
// The route list lives in src/lib/public-routes.ts, shared with the Book a
// Demo popup so both change together.

export default function sitemap(): MetadataRoute.Sitemap {
  return PUBLIC_ROUTES.map((route) => ({
    // next.config.ts sets trailingSlash: true, so the canonical form of
    // every page (other than the root) carries a trailing slash.
    url: route === "/" ? `${SITE_URL}/` : `${SITE_URL}${route}/`,
  }));
}
