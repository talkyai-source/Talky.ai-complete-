import type { MetadataRoute } from "next";

const SITE_URL = "https://talkleeai.com";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      disallow: [
        "/dashboard",
        "/calls",
        "/campaigns",
        "/inbound-campaigns",
        "/settings",
        "/analytics",
        "/billing",
        "/admin",
        "/white-label",
        "/security",
        "/ai-options",
        "/reviews",
        "/contacts",
        "/connectors",
        "/api",
      ],
    },
    sitemap: `${SITE_URL}/sitemap.xml`,
  };
}
