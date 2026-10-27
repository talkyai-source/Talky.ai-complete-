import type { Metadata } from "next";

// Canonical origin of the public site. The same literal is declared in
// app/sitemap.ts and app/robots.ts; site-metadata.test.ts checks they agree.
export const SITE_URL = "https://talkleeai.com";

export const SITE_NAME = "Talk-Lee";

// Spread into the root layout's `metadata` so every route inherits it.
//
// "./" resolves against the route being rendered, and because next.config.ts
// sets `trailingSlash: true` Next appends the slash: the root becomes
// https://talkleeai.com/ and every other page https://talkleeai.com/<route>/,
// the same form the sitemap emits and the proxy redirects to.
//
// og:title / og:description and the Twitter equivalents are filled by Next from
// each page's own resolved title and description, so no copy is repeated here.
// There is deliberately no og:image: the repository holds no site-wide share
// image, and none is to be invented.
export const sharedMetadata = {
  metadataBase: new URL(SITE_URL),
  alternates: { canonical: "./" },
  openGraph: { type: "website", siteName: SITE_NAME, url: "./" },
  twitter: { card: "summary" },
} satisfies Metadata;
