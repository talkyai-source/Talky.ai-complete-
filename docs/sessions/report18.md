# Documentation: sidebar scrolling, image caching, page weight, overlap proof (2026-10-26)

Base: `main` at 454b8efe, clean. All measurements below come from a production build (`next build --webpack`) served by `next start -p 3107` on this machine and probed with Playwright/Chromium 1200 through the Chrome DevTools Protocol. Scripts and raw JSON/logs/screenshots live in the session scratchpad (`harness/`, `before/`, `after/`). Nothing was committed, staged or pushed.

## Outcome

| Part | Result |
|---|---|
| A sidebar | Done. `docs-sidebar.tsx` aside: `lg:sticky lg:top-[calc(var(--home-navbar-height)+3rem)] lg:max-h-[calc(100vh-var(--home-navbar-height)-3rem)] lg:overflow-y-auto lg:overscroll-y-contain hide-scrollbar`. 15/15 entries reachable at every width/height; no scroll chaining; scrollbar hidden exactly like the dashboard sidebar; collapsible panel byte-identical in markup and measurement. |
| B caching | Done. `next.config.ts` adds `/docs/:path*.webp` → `public, max-age=86400, stale-while-revalidate=604800`. Repeat visits: 0 image requests, 0 image bytes (was 12 revalidation round trips). ETag + Last-Modified retained. HTML untouched. |
| C weight | Investigated, no code change (none met "provably safe + confined to shared client code loading"). 2.8 MB is the uncompressed figure; 733,559 B on the wire; 45% is RSC link prefetch, the largest chunk is mostly Sentry. |
| D overlap | 4,440 combinations × 2 runs, 3.44 M element pairs, 767,858 elements per run. Zero overlaps, zero window-edge crossings, zero horizontal scroll, zero clipped images, before and after. The only non-zero after-counts are the two sidebar entries that sit beyond the sidebar's box at viewport height 600 and are reached by scrolling the sidebar (the behaviour Part A requires). |
| Gates | typecheck 0 errors, lint 0 errors, tests 1011 (1006 + 5 new) pass 1009 / fail 0 / skip 2, route table identical (93 rows, 86 static pages). Branding check fails identically before and after on two pre-existing test files. |

Deviation to know about: the sidebar stays `sticky`, not `fixed` like the dashboard. Reason in A1 below (a fixed column would cover the footer's first column at the end of every page; the dashboard has no footer).

---

## Stage 1 — Verification

### Q1–Q5 Dashboard sidebar

File/component: `Talk-Leee/src/components/layout/sidebar.tsx`, `Sidebar` (rendered by `components/layout/dashboard-layout.tsx:137`).

Declarations governing position, height and scrolling:

- `sidebar.tsx:554` `<aside className="talklee-sidebar group/sidebar hidden lg:block fixed left-0 top-0 bottom-0 z-20 transition-[width] ease-in-out">` with inline `width: desktopWidth` (`--sidebar-expanded-width` 232px default, measured 200px in the probe / `--sidebar-collapsed-width` 72px).
- `sidebar.tsx:564` inner `div.w-full.h-full ... overflow-hidden`.
- `sidebar.tsx:301` scroller `div.flex.flex-col.h-full.overflow-y-auto.hide-scrollbar`.
- `globals.css:110-117` `.hide-scrollbar { scrollbar-width: none; -ms-overflow-style: none } .hide-scrollbar::-webkit-scrollbar { display: none }`.

| Behaviour | Declaration |
|---|---|
| Stays in place while the page scrolls | `fixed left-0 top-0 bottom-0` (sidebar.tsx:554). Measured: top 0 at scroll 0 and at page bottom, both themes. |
| Scrolls within its own area when taller | `h-full overflow-y-auto` on the scroller (sidebar.tsx:301); measured at 1280×400: scrollHeight 581 > clientHeight 400, scrolls to 181. |
| Not passing scroll to the page at its ends | **Nothing declares it**: computed `overscroll-behavior-y: auto` on the scroller. The probe could not observe chaining because the mocked dashboard page had no page overflow (docHeight = innerHeight). |
| Content shorter than the space | The scroller is still `h-full` (box fills the column); `overflow-y-auto` shows no scrollbar and the scrollbar is hidden anyway. At 600/900/1000 px the content fit (scrollHeight = clientHeight). |
| Top offset | None: `top-0`. The dashboard has no navbar above the sidebar. |
| Scrollbar | `scrollbar-width: none`, `::-webkit-scrollbar { display: none }` — width 0 px, no colour, never visible, both themes (measured gutter 0 with classic scrollbars forced on; `scrollbar-color: auto`). |

### Q6–Q9 Documentation sidebar (before)

File/component: `Talk-Leee/src/components/docs/docs-sidebar.tsx`, `DocsSidebar`, rendered in `src/app/docs/layout.tsx:18` inside `div.lg:grid.lg:grid-cols-[16rem_minmax(0,1fr)].lg:gap-12.lg:items-start` (layout.tsx:17).

Before (docs-sidebar.tsx:156): `<aside data-docs-sidebar className="hidden lg:block lg:sticky lg:top-4 self-start">`, inner `nav.rounded-xl.border.border-border/60.bg-card.p-2` (line 157). No height bound, no overflow, no scrollbar rule.

Q6 measured on `/docs/outbound-campaigns/` (longest page), identical in both themes:

| Width | Left/Top/Right/Bottom at scroll 0 | Content height | Available (vh − top) | Entries fully visible | Own scrollbar | Movement when page scrolls |
|---|---|---|---|---|---|---|
| 1024 | 32/96/288/722 | 626 | vh − 96 | h1000: 15, h900: 15, h800: 15, h700: 14, h600: 12 | no | sticks at top 16 after 80 px of page scroll; at page bottom pushed up to top −191 (h1000) … −591 (h600) |
| 1280 | 32/96/288/722 | 626 | vh − 96 | same | no | same (−191 … −591) |
| 1440 | 112/96/368/722 | 626 | vh − 96 | same | no | same |
| 1920 | 352/96/608/722 | 626 | vh − 96 | same | no | same |

Entries a reader cannot reach without scrolling the page (per height, every width, both themes): h1000 0, h900 0, h800 0, **h700 1, h600 3**.

Q8 Navbar on a /docs page: `nav.home-navbar-fixed`, computed `position: absolute` (`globals.css:280-282`: `position: absolute; top: 8px`), height `var(--home-navbar-height)` = 48 px at ≥768 (44 px below; globals.css:61,121), measured rect top 10 → bottom 58. It is absolute, so it scrolls away with the page; the only element offset against it is the page padding `.home-navbar-offset { padding-top: var(--home-navbar-height) }` (globals.css:418) and the anchor rule `scroll-margin-top: calc(var(--home-navbar-height) + 16px)` (globals.css:425, docs-markdown.tsx:85).

Q9 Things that rely on the page being the scrolling element (all measured, all still work after, see V7):

- Anchored headings: `docs-markdown.tsx:85` `scroll-mt-[calc(var(--home-navbar-height)+16px)]`; `/docs/outbound-campaigns/#campaign-statuses` lands the heading at viewport y 64 (window scrollY 1474).
- Same-document hash change scrolls the window (heading at 64, scrollY 2102).
- Sidebar link navigation: Next app router resets window scroll to 0 and the `<details key={pathname}>` panel remounts closed (docs-sidebar.tsx:148).
- Back navigation restores window scroll (1200 → 0 → 1200 at 1280 wide; 0 at 414 wide, before and after).
- `history.scrollRestoration` = auto, `document.scrollingElement` = html, body overflow auto.
- The navbar's scroll listener is home-only (`navbar.tsx:300 if (!isHome) return`). No IntersectionObserver or progress indicator in the docs tree.
None of these are affected by a sidebar that scrolls within itself; the page remains the scroller.

### Q10–Q14 Images (before)

Q10 `next.config.ts:61-96` (before):

```
61  async headers() {
62      const isDev = process.env.NODE_ENV === "development";
63      return [
64-69   { source: "/images/:path*", headers: [{ key: "Cache-Control", value: "public, max-age=86400, stale-while-revalidate=604800" }] },
70-75   { source: "/:path*.mp4",    headers: [{ key: "Cache-Control", value: "public, max-age=86400, stale-while-revalidate=604800" }] },
76-79   { source: "/:path*.svg",    headers: [{ key: "Cache-Control", value: "public, max-age=604800, stale-while-revalidate=2592000" }] },
80-83   { source: "/openapi.json",  headers: [{ key: "Cache-Control", value: "public, max-age=3600, stale-while-revalidate=86400" }] },
84-87   { source: "/site.webmanifest", headers: [{ key: "Cache-Control", value: "public, max-age=3600, stale-while-revalidate=86400" }] },
88-94   ...(isDev ? [] : [{ source: "/_next/static/:path*", headers: [{ key: "Cache-Control", value: "public, max-age=31536000, immutable" }] }]),
95      ];
96  },
```
Covers: `/images/**`, any `.mp4`, any `.svg`, `/openapi.json`, `/site.webmanifest`, `/_next/static/**` (production only). Nothing covers `/docs/*.webp`.

Q11 `GET /docs/ai-options-01-pipeline-mode.webp` (before):
`HTTP/1.1 200 OK · Accept-Ranges: bytes · Cache-Control: public, max-age=0 · Last-Modified: Fri, 23 Oct 2026 16:22:18 GMT · ETag: W/"1e1e-1a16991ff95" · Content-Type: image/webp · Content-Length: 7710`. The value is Next's static-file default for `public/` because no `headers()` source matches the path.

Q12 `/docs/getting-started/` (12 images), measured without request routing (routing disables Chromium's cache):

| Load | Page bytes on the wire | Images requested | Image wire status | Image bytes |
|---|---|---|---|---|
| 1 cold | 1,415,702 | 12 | 200 | 638,837 |
| 2 same session | 320,015 | 12 | 304 (If-None-Match sent) | 2,920 (243 B each) |
| 3 fresh browser, cache retained | 321,072 | 12 | 304 | 2,920 |

So the second load did not re-download bytes, but every image cost a network round trip on every visit.

Q13 Filenames carry no content hash or version marker, e.g. `/docs/getting-started-09-dashboard.webp`. With a long cache, a reader who had cached an image would keep seeing the old screenshot after a replacement under the same name until their copy expired or was revalidated; with `immutable` they would never revalidate within the max-age.

Q14 `/_next/static/:path*` uses `public, max-age=31536000, immutable`. Safe there because every file name under `/_next/static/chunks|css|media` contains a build content hash (e.g. `9680-a2ac179d20418bfe.js`), so a changed file is a new URL. The docs images have stable names, so `immutable` would pin a wrong screenshot for a year.

### Q15–Q19 Page weight

Q15 `/docs/billing/` (no images), cold load, 105 resources: **733,559 B transferred, 2,812,998 B uncompressed** (the 2.8 MB figure is the uncompressed one, what DevTools shows as "resources").

| Type | Count | Transferred | Uncompressed |
|---|---|---|---|
| Script | 42 | 394,371 | 1,197,186 |
| Fetch (RSC `?_rsc=` prefetch payloads) | 58 | 263,024 | 1,263,973 |
| Stylesheet | 3 | 35,814 | 230,643 |
| Font | 1 | 24,876 | 24,576 |
| Document | 1 | 15,474 | 96,620 |

Largest resources: `9680-a2ac179d20418bfe.js` 162,443 (546,277 raw) · `4bd1b696-04d4e6726f46d1d5.js` 63,744 (201,060 raw) · `5399-23432c3e170813ef.js` 40,449 (119,854 raw) · CSS 33,924 · font 24,876 · document 15,474 · `/docs/outbound-campaigns/?_rsc=` 12,338 · `/ai-voice-agent/?_rsc=` 11,470 · ten `/industries/*?_rsc=` 7,882–10,675 each · `115-…js` 9,775 · `8409-…js` 8,495 · `1430-…js` 7,532 · `app/contact/page-…js` 7,465 · `app/page-…js` 7,407 · `app/pricing/page-…js` 6,160 · `app/docs/layout-…js` 6,078.

Of the 42 scripts, 18 are in the page's HTML (296,246 transferred / 940,137 raw) and 24 are prefetched route chunks for links on the page (98,125 transferred). Of the 58 RSC fetches, 20 are the docs sidebar/index links (97,009 B) and 38 are navbar/footer links (industries, use-cases, pricing, contact, home…).

Q16 What the large bundles contain (grep of the chunk bodies, see `before/first-load.log`):

- `9680-…js` 546,277 raw / 161,927 gz: the Sentry browser SDK (`@sentry/*` markers from byte 68,003 to the end — ~478 KB raw span, replay/feedback not included), zod (~53 KB raw span), plus app code (auth-context, http-client, white-label, Breadcrumbs, BroadcastChannel sync).
- `4bd1b696-…js` 201,060 raw / 63,307 gz: `react-dom` client runtime (197,609-byte `react-dom-client.production.js`).
- `5399-…js` 119,854 raw / 40,449 transferred: framer-motion (`motion` ×76) — not in the docs HTML; prefetched for the home/industries/use-cases routes linked from the navbar.
- `polyfills-…js` 112,594 raw, `nomodule` (modern browsers skip it).
- `115-…js` 29,869: @tanstack/react-query. `8409`, `1430` (theme, voice-agent refs), `7529`, `1159` (BroadcastChannel), `8771` (zod bits), `8027` (lucide/usePathname), `1226`, `8500`: shared app client code; `app/docs/layout-…js` 17,100: navbar + footer + DocsSidebar client parts.

Q17 Does a /docs page need them at runtime? Markdown is rendered on the server (`docs-markdown.tsx` ships no renderer). Needed: react-dom (hydration of the navbar, theme toggle, sidebar `usePathname`), the small theme/navbar chunks. Not needed by a reader of a /docs page: the Sentry SDK (only initialises if `NEXT_PUBLIC_SENTRY_DSN` is set, but is always downloaded), zod (API response validation for the signed-in app), react-query, BroadcastChannel auth sync, white-label branding, notifications identity, the prefetched framer-motion and other-route chunks, and the 58 RSC payloads for pages the reader has not asked for.

Q18 How shared client code reaches a /docs page:

- `src/app/layout.tsx:54-58` wraps every route in `<AppProviders>` → `<SuspensionStateProvider>`; `app-providers.tsx:96-105` mounts `QueryClientProvider`, `ThemeProvider`, `AuthProvider`, `NotificationsIdentityProvider`, `PrefetchOnAuth`, `NotificationToaster`.
- `src/instrumentation-client.ts:1` `import * as Sentry from "@sentry/nextjs"` (static; `Sentry.init` is conditional at line 7, the import is not), loaded on every page by Next.
- `src/app/docs/layout.tsx:2-4` imports the public `Navbar`/`Footer` (client components) and `DocsSidebar`.
- Every `<Link>` in navbar/footer/sidebar prefetches its route (RSC payload + route chunks) when it enters the viewport (Next default).

Q19 Cold-load totals: `/pricing/` 633,244 transferred / 2,513,047 uncompressed (86 resources) · `/terms/` 649,502 / 2,512,147 · `/privacy/` 652,143 / 2,554,052 · `/docs/glossary/` 732,950 / 2,812,698. The weight is shared by every public page; /docs carries ~100 KB more because its sidebar adds 20 prefetched RSC payloads.

### Baseline (before)

| Gate | Result |
|---|---|
| build | `✓ Compiled successfully in 33.7s`, 86/86 static pages, exit 0 |
| typecheck | exit 0, no errors |
| lint | exit 0, 0 errors |
| test | tests 1006, pass 1004, fail 0, skipped 2 |
| check-branding | **exit 1 (pre-existing)**: `src/lib/notifications-isolation.test.tsx` lines 80, 81, 90, 113, 116 and `src/lib/webauthn-client-contract.test.tsx` line 69 contain "Talky" |
| route list | 93 rows (65 ○ static, 23 ƒ dynamic, /docs/[slug] with 14 ● SSG paths) |
| First Load JS | Next 16 prints no size table; derived from the `<script>` tags of the prerendered HTML: `/docs` and `/docs/[slug]` 19 scripts, 1,052,731 B raw / 328,590 B gzip (identical for both routes) |

Screenshots: 960 (15 pages × 8 widths × 2 themes × 2 heights × {viewport, full page}) in `before/shots/`.

---

## Stage 2 — Implementation

### Changed lines

`Talk-Leee/src/components/docs/docs-sidebar.tsx:65-68` (was line 156 in the pre-change numbering of the earlier listing; file line 59 before):

```
- <aside data-docs-sidebar className="hidden lg:block lg:sticky lg:top-4 self-start">
+ <aside
+   data-docs-sidebar
+   className="hidden lg:block lg:sticky lg:top-[calc(var(--home-navbar-height)+3rem)] lg:max-h-[calc(100vh-var(--home-navbar-height)-3rem)] lg:overflow-y-auto lg:overscroll-y-contain hide-scrollbar self-start"
+ >
```
and the component doc comment (lines 19-30) rewritten to describe the behaviour.

`Talk-Leee/next.config.ts:80-91` inserted between the `.svg` rule and `/openapi.json`:

```
+ // Documentation screenshots (public/docs/*.webp, rendered by docs-markdown.tsx). ... (comment)
+ {
+     source: "/docs/:path*.webp",
+     headers: [{ key: "Cache-Control", value: "public, max-age=86400, stale-while-revalidate=604800" }],
+ },
```

New tests: `src/components/docs/docs-sidebar.test.tsx` (2 tests: lg classes present and lg-scoped, panel markup unchanged) and `src/app/docs/docs-image-headers.test.ts` (3 tests: the rule and its value, no HTML-catching source, pre-existing rules unchanged, loaded from the real `next.config.ts`). Both fail on HEAD (the asserted strings are absent: `git show HEAD:… | grep -c` = 0) and pass now.

### A1–A8 decisions

- A1 Mechanism mirrored from the dashboard: bounded height + `overflow-y-auto` + `hide-scrollbar` (the same global class). Not mirrored: `position: fixed`. A fixed column is out of flow, so at the end of every page it would be drawn over the footer's first column (the footer is full-width directly under the grid); the dashboard has no footer. `sticky` is the docs sidebar's existing positioning and keeps it inside the grid column, so it can never overlap the footer. Consequence: at the very end of the page the footer pushes the sidebar up (V2).
- A2 Top offset = `var(--home-navbar-height) + 3rem`: the navbar token plus the layout's own `md:py-12`, i.e. exactly the content top at page load (96 px), so the sidebar's resting place and its stuck place are the same line. The navbar is `absolute` and scrolls away, so the sidebar is never under it.
- A3 `max-h` = `100vh − that offset`, so when the list is taller its bottom edge is the viewport bottom; when shorter the box ends with its content (A4: no scrollbar, no empty track).
- A5 `overscroll-y-contain` (added; the dashboard declares nothing here). Measured side effect: none. At 1280×900 and 1280×1000 (sidebar fits, nothing to scroll) a wheel over the sidebar scrolls the page normally (scrollY 1200 after 4 ticks, same as over the content); at 1280×700 (sidebar scrolls) the wheel scrolls the sidebar and the page stays at 0 (`after/wheel-nonscroll.log`).
- A6 Page scrolling untouched (`document.scrollingElement` html, body overflow auto).
- A7 `hide-scrollbar`: 0 px, hidden, both themes, same as the dashboard.
- A8 Everything is `lg:`-scoped; the `<details>` panel markup is unchanged (test asserts the exact class strings).

### B1–B4

- B1 Source `/docs/:path*.webp` matches only `.webp` files under `/docs/` (all 49 docs images are `.webp`; no other files in `public/docs`). `/docs/getting-started/` and `/docs/` do not match.
- B2 Value `public, max-age=86400, stale-while-revalidate=604800` — the same pair the repo already uses for `/images/:path*`. Worst case for a reader holding a replaced screenshot: up to 24 h of the old image with no request; between day 1 and day 8 one more page view with the old image while the browser revalidates in the background, the next view is fresh; after day 8 a conditional request before display. Not `immutable`, because the names carry no hash and a corrected screenshot must reach readers within a day. A documentation screenshot being a day stale is harmless; 639 KB of images per visit is not.
- B3 Next's static handler still emits `ETag` (weak) and `Last-Modified`; measured after (V9).
- B4 Existing rules untouched (test `the pre-existing header rules are unchanged` compares all five + the `_next/static` rule).

### C1–C4

No code change. Findings are the deliverable (Q15–Q19). Candidate changes, all declined under C3:

| Change | Saves on a cold /docs load | Why not done |
|---|---|---|
| Lazy-import `@sentry/nextjs` in `instrumentation-client.ts` only when a DSN is set, or move it out of the shared bundle | ≈140 KB transferred (≈480 KB raw of the 546 KB chunk, estimate from marker span) | Changes how the signed-in app loads Sentry; `onRouterTransitionStart` is exported synchronously from that file |
| Sentry `bundleSizeOptimizations.excludeTracing/excludeReplay*` in `next.config.ts` | tens of KB | Changes tracing for the signed-in app |
| Route group for public pages with a lighter root layout (no QueryClient/Auth/Notifications providers) | ≈60–90 KB of shared client chunks + react-query/zod | Layout restructure / provider move |
| `prefetch={false}` on the 15 docs sidebar links | 97,009 B (20 RSC fetches) | Not shared client code; changes navigation timing (fetch on hover/click instead of on view) — outside C2's fence |
| `prefetch={false}` on navbar/footer links | ≈166 KB RSC + ≈98 KB route chunks | Navbar/footer are out of scope |

First Load JS before/after (derived from HTML script tags): `/docs` 1,052,731 → 1,052,880 raw, 328,590 → 328,657 gzip (the +149 B are the new sidebar classes in `app/docs/layout-*.js` 17,100 → 17,249); `/docs/[slug]` identical to `/docs`. Pricing/terms/privacy byte-identical.

---

## Stage 3 — Verification

### V1 Sidebar after, `/docs/outbound-campaigns/`, both themes identical

| Width | Height | Edges L/T/R/B | Bottom at viewport bottom | All 15 reachable without page scroll | Own scrollbar | Needed one |
|---|---|---|---|---|---|---|
| 1024/1280 | 1000 | 32/96/288/722 | no (content ends at 722, box ends with content) | yes | no | no |
| 1024/1280 | 900 | 32/96/288/722 | no (722) | yes | no | no |
| 1024/1280 | 800 | 32/96/288/722 | no (722) | yes | no | no |
| 1024/1280 | 700 | 32/96/288/700 | yes | yes | yes (hidden) | yes |
| 1024/1280 | 600 | 32/96/288/600 | yes | yes | yes (hidden) | yes |
| 1440 | 1000/900/800 | 112/96/368/722 | no (722) | yes | no | no |
| 1440 | 700 / 600 | 112/96/368/700 · /600 | yes | yes | yes | yes |
| 1920 | 1000/900/800 | 352/96/608/722 | no (722) | yes | no | no |
| 1920 | 700 / 600 | 352/96/608/700 · /600 | yes | yes | yes | yes |

Before, the same cells read: all 15 reachable only at h ≥ 800 (h700: 14, h600: 12); bottom edge 722 at every height (box overflowed the 600/700 viewports).

### V2 Edges before/after scrolling the reading column to the bottom

Top at page scroll 500: **96 at every width/height/theme** (before: 16). Top at the very bottom of the page (footer fully in view): h1000 −191 · h900 −291 · h800 −391 · h700 −469 · h600 −469 (before: −191, −291, −391, −491, −591). Not identical at the page end: the sticky column is pushed up by the footer's visible height so it never covers the footer. At all other scroll positions the four edges are the scroll-0 edges with the bottom capped at the viewport.

### V3 Wheel inside the sidebar past its own ends

At h700 and h600 (the heights where it scrolls), every width, both themes: wheel ×6 at the sidebar's bottom → page scrollY 0 (did not move); from page scrollY 400, wheel up ×6 at the sidebar's top → scrollY 400 (did not move). At heights where it does not need to scroll the test is n/a (no scroller).

### V4 Content shorter than the space

h800/900/1000: aside box 96→722 = content height 626; `scrollHeight == clientHeight`, no scrollbar, no empty track (the box is `max-h`, not `h`).

### V5 Scrollbar, classic scrollbars forced on (page scrollbar measured 15 px)

| | docs sidebar light | docs sidebar dark | dashboard light | dashboard dark |
|---|---|---|---|---|
| gutter (offsetWidth − clientWidth) | 0 | 0 | 0 | 0 |
| `scrollbar-width` | none | none | none | none |
| `::-webkit-scrollbar` display | none | none | none | none |
| `scrollbar-color` | auto | auto | auto | auto |
| `overscroll-behavior-y` | contain | contain | auto | auto |

### V6 Scrolling element

`document.scrollingElement` = `html`, body `overflow-y: auto`, document heights 14,435–14,793 px, at every width/height/theme; unchanged from before.

### V7 Q9 behaviours after

Identical numbers to before: anchor via URL lands at y 64 (scrollY 1474), hash change y 64 (scrollY 2102), sidebar navigation scrollY 1200 → 0 with the panel remounted closed, back restores 1200 (1280 wide) / 0 (414 wide, same as before); scrollRestoration auto.

### V8 Collapsible panel, opened (both themes identical)

| Width | Height | Edges L/T/R/B | Outside viewport | Overlaps | Entries reachable | Visible at scroll 0 | Page scrolls | Closes |
|---|---|---|---|---|---|---|---|---|
| 320 | 900/600/500 | 16/84/304/735 | no | 0 | 15 | 15/11/9 | yes (body auto) | yes |
| 375 | 900/600/500 | 16/84/359/715 | no | 0 | 15 | 15/12/9 | yes | yes |
| 414 | 900/600/500 | 16/84/398/715 | no | 0 | 15 | 15/12/9 | yes | yes |
| 768 | 900/600/500 | 24/96/744/727 | no | 0 | 15 | 15/11/9 | yes | yes |

The panel pushes the content down (`main.top ≥ panel.bottom` true in every case). All 1,800 panel-open combinations and all 1,680 closed sub-lg combinations are identical to the before set in rect, flags and every count.

### V9 Image response headers now

`GET /docs/connectors-01-list.webp`: `HTTP/1.1 200 OK · Cache-Control: public, max-age=86400, stale-while-revalidate=604800 · Accept-Ranges: bytes · Last-Modified: Fri, 23 Oct 2026 16:22:20 GMT · ETag: W/"fd66-1a16992095a" · Content-Type: image/webp · Content-Length: 64870`.

### V10 Three loads of `/docs/getting-started/`

| Load | Images hitting the network | Status | Image bytes | Page bytes (after) | Page bytes (before) |
|---|---|---|---|---|---|
| 1 cold | 12 | 200 | 639,257 | 1,417,914 | 1,415,702 |
| 2 same session | **0** (12 served from cache, no request) | — | 0 | 316,981 | 320,015 (12 × 304) |
| 3 fresh browser, cache retained | **0** | — | 0 | 316,980 | 321,072 (12 × 304) |

### V11 Replacing an image under the same name

`connectors-01-list.webp` (sha256 `ecbcf758…baf0`, 64,870 B) replaced by the bytes of `getting-started-11-sidebar-collapsed.webp` (sha256 `08767e84…d8d`, 5,814 B), server restarted (no rebuild needed: `public/` is served as-is, but `next start` snapshots the file list at startup).

| Step | Request | Wire status | Bytes | Image shown (natural size / pixel hash) |
|---|---|---|---|---|
| 1 reader fills cache | yes | 200 | 65,187 | 1536×1268 / 40d9962f (original) |
| 2 same reader returns within 24 h | **no request, from disk cache** | — | 0 | 1536×1268 / 40d9962f (**old**) |
| 3 same URL with cache bypassed (= after max-age or a conditional revalidation) | yes | 200 | 6,130 | 144×1800 / 49155cf2 (**new**) |
| 4 brand-new reader | yes | 200 | 6,130 | new |
| 5 original restored, cache bypassed | yes | 200 | 65,187 | 1536×1268 / 40d9962f (original) |

A reader holding the cached version keeps the old screenshot for up to 24 h; after that the next view revalidates (ETag/Last-Modified changed, so the server answers 200 with the new bytes) and the view after that, or any view after day 8, shows the new one. Restored: sha256 `ecbcf7583f0c8a2ecb2bb43b9610f922b5cafaec9314eb187be3bdb67a56baf0`, 64,870 B, `git status` clean under `public/docs`.

### V12 HTML untouched

`/docs/getting-started/` → `Cache-Control: s-maxage=31536000` (+ `ETag: "byo522xymq2yhn"`); `/docs/` → `s-maxage=31536000`. Same as before.

### V13 Existing rules unchanged

Asserted by `docs-image-headers.test.ts` against the real config: `/images/:path*` `public, max-age=86400, stale-while-revalidate=604800` · `/:path*.mp4` same · `/:path*.svg` `public, max-age=604800, stale-while-revalidate=2592000` · `/openapi.json` `public, max-age=3600, stale-while-revalidate=86400` · `/site.webmanifest` same · `/_next/static/:path*` `public, max-age=31536000, immutable`. `curl /favicon.svg` → `public, max-age=604800, stale-while-revalidate=2592000`.

### V14 Weight before/after

First Load JS `/docs` and `/docs/[slug]`: 1,052,731 → 1,052,880 raw, 328,590 → 328,657 gzip. Cold `/docs/billing/`: 733,559 → 733,715 transferred, 2,812,998 → 2,813,607 uncompressed, 105 resources both (the +600 B is the new class string in the layout chunk and HTML).

### V15 Changed / not changed

Changed for weight: nothing. Not changed, with savings: see C1–C4 table (Sentry ≈140 KB wire, public route group ≈60–90 KB, sidebar prefetch 97,009 B, navbar/footer prefetch ≈264 KB).

### V16 / V18 Pixel comparison, 960 pairs

783 identical, 177 differing, 0 missing. Every differing pixel (18,723,845) lies inside either the sidebar box (3,739,680 px, 120 full-page shots at lg+ × h600: the sidebar now ends at the 600 px line instead of overflowing to 722) or an `<img>` box (14,984,165 px, 57 full-page shots). Differing pixels outside both: **0**. The 57 image-box differences are `loading="lazy"` images that had or had not finished loading when the full-page capture was taken (in both directions, before and after); all 480 viewport screenshots are identical. Checked arithmetically (`diff-images.mjs`), not by eye.

### V17 Horizontal scrollbars / overflow / overlap

Combinations with `documentElement.scrollWidth > clientWidth`: 0 before, 0 after. Overlap counts: see Part D.

### V19 Rules keyed on anything other than viewport width

Written by me: none (`lg:` = `min-width: 1024px`; `100vh` is the viewport height, which A3 requires). Found, pre-existing, not changed: `globals.css:1029` `@media (hover: none) and (pointer: coarse) and (max-width: 1024px)` and `globals.css:1047-1052` exact-dimension/orientation queries (768×1024, 820×1180, 1024×1366, 912×1368, 1064–1069×1598–1602 portrait, 1024×600 landscape) for the navbar.

### V20 Other pages

`/pricing` `/terms` `/privacy`: First Load JS byte-identical (1,052,588 / 1,051,134 / 1,051,134 raw), cold loads 631,561 / 652,486 / 652,222 transferred (before 633,244 / 649,502 / 652,143; the ±3 KB is RSC prefetch ids that change per build). Dashboard: all 8 probes (2 themes × 1280×400/600/900, 1920×1000) identical before/after in aside rect, scroller, scrollbar and page values; `/dashboard` First Load JS 1,553,155 raw both.

---

## Part D

Coverage: 15 pages × 22 widths × 4 heights × 2 themes = 2,640 closed combinations, + 14 sub-lg widths × 4 heights × 15 × 2 = 1,680 with the panel open, + 4 panel widths × height 500 × 15 × 2 = 120 → **4,440 combinations per run, none skipped**, run twice (before and after). Per run: 767,858 elements tested, 3,444,326 (before) / 3,439,138 (after) pairs, 466,002 hit-tests for occlusion (0 untested). Tolerance 1 px for collapsed table borders. Text blocks = h1–h6, p, li, td, th, blockquote, pre, code, figcaption, summary, dt, dd, caption, a, button, label; images = `<img>`.

| Check | Before | After |
|---|---|---|
| V21 text over text | 0 | 0 |
| V22 image over text / image / sidebar / navbar / footer | 0 / 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 / 0 |
| V23 content into sidebar / sidebar element out of sidebar | 0 / 0 | 0 / 0 |
| V24 left edge < 0 / right edge > viewport | 0 / 0 (min left 0, max right = viewport on every page) | 0 / 0 |
| V25 zero-size / clipped / occluded | 0 / 0 / 0 | 0 / **960 / 960** (see below) |
| V25 content wider than its box | 6,510 | 6,510 (same elements) |
| V26 image outside column / wrong ratio (>1 px) | 0 / 0 (14,800 image rows, all complete) | 0 / 0 |
| V27 long text wider than container at 320 | 0 | 0 |
| V28 horizontal scroll | 0 | 0 |
| V29 panel open, all of the above | 0 | 0 (1,800 combinations identical to before) |

Named elements in non-zero counts:

- After, clipped 960 and occluded 960: `li "Troubleshooting"`, `a "Troubleshooting"`, `li "Glossary"`, `a "Glossary"` (4 per combination × 240 combinations = every page × 8 lg widths × 2 themes at **height 600 only**). They are clipped by `aside[data-docs-sidebar]` (reported `scrollable: true`) because the sidebar's box is 504 px tall there and the list is 626 px; scrolling the sidebar reveals them (V1: 15/15 reachable). The "occluded" hits are the same elements: their layout position below the sidebar's box is painted by the footer. This is the in-sidebar scrolling Part A requires, not an overlap; at heights ≥ 700 the counts are 0.
- "Wider than box" 6,510 in both runs, only at widths ≥ 1024: `ul.navbarDesktopNav` (930), `li.relative` (2,790) and `div.group.relative` (2,790) in the public navbar — their absolutely positioned hover menus (hidden, `invisible`) extend `scrollWidth`. Pre-existing, navbar, not a visible overflow (no horizontal scroll, V28 = 0).
- V23 gap: sidebar right edge → `main` left edge = **48 px** at every lg width (sidebar rects: 1024/1177/1280 32–288, 1366 75–331, 1440 112–368, 1600 192–448, 1920 352–608, 2560 672–928).
- V27 at 320 px: longest unbroken tokens are `call-state/captured-facts` (25), `Turn-taking/end-of-turn` (23), `administrator/technical` (23), `interruption/barge-in` (21), `Business/organization` (21), `emergency/escalation` (20); all wrap within their container, none widens the page. Tables sit in `overflow-x-auto` wrappers and did not need to scroll at 320.
- V26: every image sits inside `main` with rendered height within 1 px of `renderedWidth × intrinsicHeight / intrinsicWidth` (content box; the 1 px frame border excluded).

Failure screenshots: none for overlaps; the 240 height-600 sidebar combinations were captured to `after/partd-fails/` (first offender per combination).

---

## Gates after

| Gate | Before | After |
|---|---|---|
| build | compiled, 86/86 pages, exit 0 | compiled in 87 s, 86/86, exit 0; route table identical (93 rows) |
| typecheck | exit 0 | exit 0 |
| lint | exit 0, 0 errors | exit 0, 0 errors |
| test | 1006 / pass 1004 / fail 0 / skip 2 | 1011 / pass 1009 / fail 0 / skip 2 |
| check-branding | exit 1, same 6 pre-existing lines | exit 1, same 6 lines (not a regression; not in scope) |

## V32 git status

```
 M Talk-Leee/next.config.ts
 M Talk-Leee/src/components/docs/docs-sidebar.tsx
?? Talk-Leee/src/app/docs/docs-image-headers.test.ts
?? Talk-Leee/src/components/docs/docs-sidebar.test.tsx
?? docs/sessions/report18.md
```

## Not done / decided myself / found beyond the brief

- Not mirrored: `position: fixed` (see A1). V2 therefore differs only at the final footer-height of scroll.
- Sticky top is the content top (96 px = token + 3rem) rather than the anchor rule's token + 16 px, so the sidebar's resting and stuck positions coincide and the before/after viewport screenshots stay identical; the 16 px variant would make the box overflow the viewport at scroll 0 at height 600.
- `overscroll-y-contain` was added although the dashboard does not declare it; A5 asked for it explicitly.
- Dashboard scroll chaining could not be observed (the mocked dashboard has no page overflow); its scroller declares no containment.
- Next 16 prints no First Load JS; the figures are summed from the HTML's script tags.
- The branding check fails on `main` already (two test files); untouched.
- Found, not changed: the navbar's device/orientation media queries (V19); the Sentry SDK is downloaded on every public page even when no DSN is configured; every link on every public page prefetches its RSC payload on view (45 % of the uncompressed weight).
