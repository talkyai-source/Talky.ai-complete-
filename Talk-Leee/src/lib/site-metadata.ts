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
//
// The share image is the one site-wide file public/og-image.png (1200 x 630,
// built from the navbar's logo mark and the homepage hero's own colours). Its
// root-relative URL is joined onto metadataBase by Next, so every route emits
// the same absolute https://talkleeai.com/og-image.png. The alt text describes
// what is drawn, not the product.
export const SHARE_IMAGE = {
  url: "/og-image.png",
  width: 1200,
  height: 630,
  alt: "The Talk-Lee headphone logo in white above the words Talk-Lee AI and AI Voice Agent Platform for Call Automation, on a dark teal background with two soft waves at the foot",
};

export const sharedMetadata = {
  metadataBase: new URL(SITE_URL),
  alternates: { canonical: "./" },
  openGraph: { type: "website", siteName: SITE_NAME, url: "./", images: [SHARE_IMAGE] },
  twitter: { card: "summary_large_image", images: [SHARE_IMAGE] },
} satisfies Metadata;
