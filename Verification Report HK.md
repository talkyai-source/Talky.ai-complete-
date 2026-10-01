# Verification Report HK

Frontend verification of the Talky.ai project: the Next.js app in `Talk-Leee/` (public site, authentication, dashboard, in-app admin pages, white-label pages) and the separate Vite admin panel in `Admin/frontend/`.

Date of run: 2026-09-30. Branch `main` at commit `738b0027`. Nothing in the project was modified; this file is the only file created.

## 1. Read this first: the limits of this verification

These limits apply to every "WORKING" verdict below.

1. **The Python backend was never running.** The host has no Docker, no Postgres, no Redis and no backend virtualenv. Every `/api/v1/*` request was intercepted in the browser and answered from hand-written mock fixtures shaped to pass the frontend's own Zod schemas. "WORKING" therefore means: the control responded, sent the request shown, and the UI handled a well-formed mock answer. It does **not** prove the real backend accepts that request, returns that shape, enforces permissions, or stores anything. No save was confirmed against a real database, so "is it still there after reload" could not be verified for any server-side data.
2. **Backend presence of each endpoint was checked by reading backend code, not by calling it.** The list of 412 mounted endpoints comes from a static walk of `backend/app/api`. A route that exists in code was not exercised.
3. **Authentication was mocked.** A token was placed in `localStorage` (`talklee.auth.token`) and `GET /auth/me` was mocked (role `admin`, tenant `t1`, partner `acme`; other roles where stated).
4. **Server-side route protection could not be observed.** `Talk-Leee/src/proxy.ts` skips its auth and role checks when the host is `localhost`/`127.0.0.1`. Only the client-side guard was observed.
5. **Chromium only.** Playwright's Chromium (headless). Firefox, WebKit/Safari and Edge are not installed here. No real phones or tablets; device sizes were emulated viewports.
6. **Buttons were clicked at 1280x800 only.** At the other ten sizes the check was layout (horizontal scroll, elements past the viewport edge), not clicking. The exceptions are the public navbar drawer and the dashboard sidebar drawer, which were exercised at 390x844.
7. **The session was interrupted by a machine restart** part-way through. Results already written to disk survived and were reused. Lost and re-run after the restart: the dashboard button crawl, the form/flow checks, the admin and white-label responsive sweeps, and the Vite admin run. Nothing was lost permanently, but see section 11 for checks that were started and not finished.
8. **One harness mistake to declare.** The first Vite admin build had its API base path mangled by Git Bash, so that first run was invalid. The panel was rebuilt correctly and re-run; only the second run is reported.
9. **Where the production frontend points is unknown.** The repo's deploy script excludes the frontends and no production value for `NEXT_PUBLIC_API_BASE_URL` exists in the repo. If it is unset, the browser calls the Next.js route handler (`src/app/api/v1/[...path]/route.ts`), which in production serves only auth, white-label, platform and email paths and answers 404 for everything else. If it points at the Python API, the endpoints in section 9 do not exist. Either way some pages lose their backend; which ones depends on a setting I could not see.
10. **Code-only findings are labelled "code only".** They were read, not observed in a browser.

Verdict vocabulary used in every table: **WORKING** (observed working in the browser, against mocks), **BROKEN** (observed not doing what it presents itself as doing), **PARTLY WORKING** (the UI responds but something material is missing, such as no matching backend route or data kept only in the browser), **NOT IMPLEMENTED** (placeholder, "coming soon", decorative or hard-coded), **UNTESTED** (not checked, or checked inconclusively).

## 2. Summary

### What was covered

| Area | What was done |
|---|---|
| Talk-Leee production build | `npm run build` succeeded; served with `next start` on port 3400 |
| Routes loaded | 81 URLs (21 public, 4 auth, 33 dashboard, 11 admin, 7 white-label, 5 legacy/404 checks) |
| Responsive sweep (Talk-Leee) | 76 routes x 11 viewports x light and dark = 1,672 layout checks |
| Button clicks (Talk-Leee) | 917 visible, enabled buttons clicked one at a time, each on a fresh page load, at 1280x800 |
| Links (Talk-Leee) | 139 distinct internal link targets requested (3 returned 404) |
| Flows and forms | Login, register, forgot password, logout, session expiry, contact form, campaign create (wizard and classic), contacts add/edit/delete/import, inbound create/activate/edit, settings, security, AI options, billing, connectors, recordings, calls, white-label |
| Vite admin panel | Rebuilt, served on port 4300; 13 routes loaded, login and role checks, main actions on 8 pages, 242 layout checks |
| Backend cross-reference | 144 distinct frontend requests observed in the browser, matched against 412 backend routes read from code |
| Uncaught JavaScript errors | None on any page load or on any of the button clicks |

### Counts

Curated checks are the rows with an ID (P-, A-, D-, M-, W-, V-, R-) in sections 3 to 8 and 10. Button clicks are the automated one-by-one clicks listed in Appendix A.

| Verdict | Curated checks | Individual button clicks | Total |
|---|---|---|---|
| WORKING (against mocks). For a button click this means it navigated, sent a request, opened a dialog or changed page content | 132 | 578 | 710 |
| Responded with a change to its own state or appearance only, no request (toggles, tabs, theme, sidebar groups; seen on the second look) | 0 | 72 | 72 |
| BROKEN | 14 | 55 | 69 |
| PARTLY WORKING | 18 | 2 | 20 |
| NOT IMPLEMENTED | 20 | 0 | 20 |
| UNTESTED or unconfirmed | 37 | 198 | 235 |
| No effect after a second look (explained in section 11.3) | 0 | 12 | 12 |
| **Total items** | **221** | **917** | **1138** |

The 55 broken button clicks are the individual "Book a Demo"-style buttons of row P-11, each clicked and each landing on the top of the homepage. The 5 "Preview" buttons on `/ai-voices` appear in the "own state only" row because their label changes; they play nothing (P-18).

How to read the "unconfirmed" button clicks: the first-pass click test looked for navigation, a request, a dialog or a text change. Selection-style controls (sort headers, range buttons, star ratings, tag pills, toggles, sidebar group buttons) change only their own state, so the first pass saw nothing. A second, stricter pass was run for public, auth, admin and white-label pages and resolved most of them (72 of 84) as real state changes. For dashboard pages that second pass had not been run when I was told to stop, so 98 dashboard clicks stay unconfirmed (89 of them are the three sidebar group buttons repeated on every page, which did change state on the 11 admin pages where they were re-checked). None of these is counted as working.

### The short version

- The dashboard is broadly functional against well-formed data: every page rendered, no page crashed, and the large majority of controls sent a sensible request.
- The public site has three user-facing failures: the contact form throws messages away, 57 call-to-action buttons lead nowhere useful, and the contact details are placeholders.
- Twelve frontend requests have no matching route in the Python backend. Reminders, Meetings, Email, the assistant action runs, the "Audit & Access" admin page and white-label agent settings depend on them.
- Several things that look saved are kept only in the browser.
- The white-label area is mostly a shell: hard-coded numbers, browser-only tenants, a create button that can never be enabled, and breadcrumb links to 404 pages.
- The Vite admin panel works against mocks on a desktop screen but has no logout, no mobile layout, and several dead controls.

## 3. Public site

Files: `Talk-Leee/src/app/page.tsx`, `src/app/ai-*/`, `src/app/industries/*`, `src/app/use-cases/*`, `src/app/contact`, `src/app/terms`, `src/app/privacy`, `src/components/home/*`.

| ID | Item | Where | Expected | What actually happened | Verdict | How checked |
|---|---|---|---|---|---|---|
| P-01 | Homepage | `/` · `src/app/page.tsx`, `components/home/home-lazy-sections.tsx` | Loads and renders | HTTP 200, hero, sections and footer rendered, no uncaught errors | WORKING | browser |
| P-02 | Navbar links Home, Contact Us | `components/home/navbar.tsx` | Navigate | Both targets returned 200 on every public page | WORKING | browser |
| P-03 | Navbar dropdowns Products (3), Use Cases (2), Industries (10) | `components/home/navbar.tsx:123-168` | Open and list links | Opened on click on all 20 pages; all 15 targets returned 200 | WORKING | browser |
| P-04 | Navbar "Login" | `navbar.tsx:669-677` (href `/dashboard`) | Take a visitor to sign-in | Signed-out click landed on `/auth/login/?next=%2Fdashboard%2F` via the client redirect. The footer "Login" points at `/auth/login` instead, so the two differ | WORKING | browser |
| P-05 | Theme toggle | `navbar.tsx:678-695`, `components/providers/theme-provider.tsx` | Switch light/dark | `<html>` class changed light to dark; value stored in `localStorage` `talklee.theme` | WORKING | browser |
| P-06 | Mobile navbar drawer at 390x844 | `navbar.tsx:335-473` | Open, expand groups, navigate | Hamburger opened the drawer, the three groups expanded, "Healthcare" navigated, no horizontal scroll while open | WORKING | browser |
| P-07 | Footer (10 links) | `components/home/footer.tsx` | Present and valid | Present on all 20 public pages (not on `/403`); all targets returned 200 | WORKING | browser |
| P-08 | Homepage FAQ accordion (8 questions) | `components/home/home-lazy-sections.tsx:47-83` | Expand | Each expanded on click | WORKING | browser |
| P-09 | Homepage "Get Started Now" | `components/home/cta-section.tsx:32` | Go to sign-up | Navigated to `/auth/register/` | WORKING | browser |
| P-10 | Sign-up buttons on product, industry and use-case pages (51 links in the page markup) | e.g. `src/app/ai-voice-dialer/page.tsx:208` | Go to sign-up | 51 clicks across the public pages navigated to `/auth/register/` (this count includes the homepage button); none went anywhere else | WORKING | browser |
| P-11 | 57 buttons that link to `/#contact` on 15 pages ("Book a Demo", "Talk to Sales", "See a Live Call", "Watch Live Demo", "See Plans", "See Agency Pricing" and similar) | e.g. `src/app/ai-voice-dialer/page.tsx:213`, `src/app/industries/healthcare/page.tsx:529` | Take the visitor to a contact or demo form | The homepage has no element with id `contact` (ids present: `how-it-works`, `use-cases`, `services`, `faq`). 55 of the 57 were clicked individually and every one landed on the top of the homepage, scroll position 0 (the other 2 clicks were cut short by the test tool). No demo, pricing or contact content appears | BROKEN | browser |
| P-12 | Contact form validation | `/contact` · `components/home/contact-section.tsx:25-37` | Reject empty input | Showed "Name is required", "Email is required", "Message is required" | WORKING | browser |
| P-13 | Contact form submit | `components/home/contact-section.tsx:39-49` | Send the message somewhere | With valid input, zero network requests were made, then "Message sent successfully!" appeared and the fields cleared. The code is a 1.5 second timer labelled "Simulate API call". The message is discarded. There is no failure path | BROKEN | browser + code |
| P-14 | Contact details | `contact-section.tsx:164-179` | Real contact details | Phone is `+1 (555) 123-4567`, address is "123 AI Street, San Francisco", email is `contact@talk-lee.com` while the legal pages use `@talkleeai.com`. The `mailto:` and `tel:` links were not opened | BROKEN | browser (text) |
| P-15 | "Ask AI / Click to talk" widget, signed out | homepage only · `components/ui/voice-agent-popup.tsx:851-855` | Demo the voice agent | Click redirected to `/auth/login/?next=%2F`. The widget is shown to every visitor but only signed-in users can use it | PARTLY WORKING | browser |
| P-16 | "Ask AI" widget, signed in | `voice-agent-popup.tsx` | Start a voice session | Sent `POST /ai-options/voices/preview` (mock 200), then showed "Microphone error: NotSupportedError" because headless Chromium has no microphone. The WebSocket conversation could not be tested | UNTESTED | browser (partial) |
| P-17 | AI Voices page list | `/ai-voices` · `src/app/ai-voices/page.tsx` | Show voices | `GET /api/voices` (a real Next route) returned 200 and 5 voices rendered. The page is not linked from the navbar or footer | WORKING | browser |
| P-18 | AI Voices "Preview" buttons (5) | `src/app/ai-voices/page.tsx:53-65` | Play a voice sample | No request, no `<audio>` element; the label flips to "Stop Preview" for 3 seconds. No sound is ever played | NOT IMPLEMENTED | browser + code |
| P-19 | Product pages (3) | `/ai-voice-dialer`, `/ai-assist`, `/ai-voice-agent` | Load | 200, heading rendered, no errors | WORKING | browser |
| P-20 | Industry pages (10) | `/industries/*` | Load | 200, heading rendered, no errors | WORKING | browser |
| P-21 | Use-case pages (2) | `/use-cases/*` | Load | 200, heading rendered, no errors | WORKING | browser |
| P-22 | Terms and Privacy | `/terms`, `/privacy` | Load | 200, heading rendered | WORKING | browser |
| P-23 | 403 page | `/403` | Explain and offer a way out | "403 Unauthorized" with "Go to dashboard" and "Go to home" | WORKING | browser |
| P-24 | Unknown URL | `/does-not-exist` | 404 page | HTTP 404 with a "Page not found" page and "Back to home" | WORKING | browser |
| P-25 | Legacy `/inbound*` redirects (4) | `next.config.ts` redirects | Redirect to the new pages | `/inbound` and `/inbound/abc/edit` went to `/inbound-campaigns/`, `/inbound/new` to `/inbound-campaigns/new/`, `/inbound/calls` to `/calls/?direction=inbound` | WORKING | browser |
| P-26 | Healthcare "Practice" plan price | `src/app/industries/healthcare/page.tsx:227` | A price | The price string is "patients / per month" with no number | BROKEN | code only |
| P-27 | Signed-in visitor on marketing pages | all public pages | No side effects | Each public page fired `GET /ai-options/providers`, `/voices`, `/config` and `/auth/me` when a token was present. When those failed (mock 404) a "Request failed" toast appeared on the marketing page | PARTLY WORKING | browser |
| P-28 | External links | whole public site | Open correctly | The only non-internal links are the `mailto:` and `tel:` on `/contact`. No `target="_blank"` links exist. Neither was opened | UNTESTED | browser (enumeration only) |

Hard-coded or unsourced content visible to visitors (read in code; the homepage hero figures were also seen in screenshots):

- Homepage hero: "<500ms", "1000+ Concurrent Calls", "94% Completion" (`home-lazy-sections.tsx:311-315`).
- "Why 5,000+ Businesses Worldwide Choose Us" with 55% / 40% / 25% (`components/home/stats-section.tsx:8-10,44`).
- "Trusted by Industry Leaders" is a list of industry names, not customers (`trusted-by-section.tsx:15,105`).
- Industry and use-case stat blocks such as "500+ Businesses Served", "99.9% AI Accuracy", "500K+ Candidates Screened" (`industries/healthcare/page.tsx:62-66`, `recruitment/page.tsx:38-41`, and the equivalents on the other pages).
- Ask AI timeout text reads "Is the backend running on port 8000?" (`voice-agent-popup.tsx:768`), which is developer wording.

## 4. Authentication

Files: `Talk-Leee/src/app/auth/*`, `src/lib/auth-context.tsx`, `src/lib/http-client.ts`, `src/components/layout/dashboard-layout.tsx`, `src/components/layout/sidebar.tsx`.

| ID | Item | Where | Expected | What actually happened | Verdict | How checked |
|---|---|---|---|---|---|---|
| A-01 | Login page | `/auth/login` · `login-client.tsx` | Load | Rendered "Welcome back" with email step | WORKING | browser |
| A-02 | Login, empty email | same | Block and explain | Native browser validation "Please fill out this field."; no request | WORKING | browser |
| A-03 | Login, email then "Continue with Password" | same | Show password step | Password step shown; no request yet | WORKING | browser |
| A-04 | Login, empty password | same | Block and explain | Native validation; no request | WORKING | browser |
| A-05 | Login, wrong credentials | `login-client.tsx:319-343` | Show an error | `POST /auth/login` (mock 401) then "Invalid email or password" shown. A `POST /auth/refresh` was also fired after the 401 | WORKING | browser |
| A-06 | Login, correct credentials | `login-client.tsx:203-276` | Sign in and open the dashboard | `POST /auth/login` (mock 200), token stored in `localStorage`, full navigation to `/dashboard/`, `GET /auth/me` 200 | WORKING | browser |
| A-07 | Return to the page you came from (`?next=`) | `login-client.tsx` (`postAuthDashboard`) | After signing in from `/auth/login?next=/campaigns`, open `/campaigns` | Landed on `/dashboard/`. The `next` value that the guard adds is ignored | BROKEN | browser |
| A-08 | "Remember me" checkbox | `login-client.tsx:177,213` | Change session length | When ticked the code writes `remember_me=true` to `localStorage`; nothing in the codebase reads it. Unticked, nothing was written | NOT IMPLEMENTED | code + browser |
| A-09 | Two-factor step | `components/auth/mfa-verification.tsx` | Ask for a code and sign in | MFA step appeared, `POST /auth/mfa/verify` (mock 200), landed on `/dashboard/` | WORKING | browser |
| A-10 | Passkey sign-in | `components/auth/passkey-login.tsx` | Sign in with a passkey | The passkey step opened; no request in 3 seconds. The WebAuthn prompt cannot run headless | UNTESTED | browser (partial) |
| A-11 | Register, three steps | `/auth/register` · `register-client.tsx` | Create an account | `POST /auth/signup/start` then code step, `POST /auth/signup/verify-code` then password step, `POST /auth/signup/complete`, token stored, landed on `/dashboard/` | WORKING | browser |
| A-12 | Register error messages | same | Show server and validation errors | Empty form blocked by native validation; email in use (mock 409) showed the server message; wrong code (mock 400) showed "Invalid or expired verification code"; mismatched passwords showed "Passwords do not match." | WORKING | browser |
| A-13 | Register "Resend" code | `register-client.tsx:166-183` | Send a new code | Not clicked | UNTESTED | not checked |
| A-14 | Forgot password | `/auth/forgot-password` | Send a code, set a new password | `POST /auth/forgot-password` then code and new-password form, `POST /auth/reset-password`, redirected to `/auth/login/?email=…` | WORKING | browser |
| A-15 | Callback without a token | `/auth/callback` | Explain | "Authentication Failed. No authentication token found." with "Try Again" | WORKING | browser |
| A-16 | Callback with a token | `src/app/auth/callback/page.tsx:70` | Complete sign-in | Called `POST /auth/create-profile`, which exists in neither backend, then `GET /auth/me`, then opened `/dashboard/` | PARTLY WORKING | browser + code |
| A-17 | Logout | sidebar "Logout" · `sidebar.tsx:268-276` | End the session | `POST /auth/logout` (mock 200), token removed from `localStorage`, landed on the homepage `/`. Opening `/dashboard/` afterwards redirected to login | WORKING | browser |
| A-18 | Signed-out visitor opens a dashboard page | `dashboard-layout.tsx:55-80` | Redirect to login | `/dashboard`, `/campaigns`, `/settings`, `/admin`, `/billing` all redirected to `/auth/login/?next=…`. On `/dashboard` four data requests and on `/campaigns` one were sent before the redirect | WORKING | browser |
| A-19 | Session already expired when the page loads | `http-client.ts` 401 handling | Redirect and clear the session | `GET /auth/me` 401 and `POST /auth/refresh` 401, then redirect to login. The old token was still in `localStorage` afterwards | PARTLY WORKING | browser |
| A-20 | Session expires while using the app | same | Redirect and clear the session | After the mock started answering 401, the next navigation redirected to login and the token was removed | WORKING | browser |
| A-21 | Server-side protection of pages | `src/proxy.ts` | Redirect before the page is served | Skipped on localhost by design, so not observable here | UNTESTED | code only |
| A-22 | Email verification route | `src/app/auth/` | A page for verification links | No such page exists in `src/app/auth` (login, register, forgot-password, callback only). The backend has `GET /auth/verify-email`. Whether any email links to the frontend is unknown | UNTESTED | code only |
| A-23 | Cloudflare Turnstile and breached-password check | `login-client.tsx:80,138` | Work | One real request to `api.pwnedpasswords.com/range/<prefix>` was seen on a password submit. Turnstile was not observed loading. Neither result path was tested | UNTESTED | browser (partial) |
| A-24 | One unexplained bounce to login | `/ai-options` | Stay signed in | In one of seven loads with a valid mock session, `/ai-options` redirected to login. Five further loads did not reproduce it | UNTESTED | browser (not reproduced) |

## 5. Dashboard

Files: `Talk-Leee/src/app/{dashboard,campaigns,inbound-campaigns,calls,contacts,analytics,recordings,connectors,ai-options,security,billing,settings,assistant,meetings,reminders,notifications,email,reviews}`, `src/components/**`, `src/lib/*-api.ts`. All "WORKING" rows are against the mock backend.

### 5.1 Shell (sidebar, header, floating assistant)

| ID | Item | Where | Expected | What actually happened | Verdict | How checked |
|---|---|---|---|---|---|---|
| D-01 | Sidebar links (22 for an admin) | `components/layout/sidebar.tsx:59-117` | Navigate | Dashboard, Campaigns, Inbound, Call History, Contacts, Analytics, Recordings, Connectors, AI Options, Security, Billing, Audit Logs, Audit & Access, Voice Security, Abuse Detection, Agent Quality, API Keys, Webhooks, Rate Limiting, Secrets, Settings, plus the logo. All targets returned 200 | WORKING | browser |
| D-02 | Collapse and expand sidebar | `sidebar.tsx` | Resize and remember | Width 72 px collapsed, 200 px expanded; state saved in `localStorage` `talklee.sidebar.state.v1` | WORKING | browser |
| D-03 | Sidebar group buttons (Billing & Logs, Security Center, Developer Hub) | `sidebar.tsx:85-111` | Open or close the group | Changed their own state when re-checked on the 11 admin pages. Not re-checked on the dashboard pages | WORKING | browser |
| D-04 | Hamburger drawer at 390x844 | `sidebar.tsx`, `dashboard-layout.tsx` | Open, navigate, close | "Open sidebar" opened a drawer with the same 22 links; "Contacts" navigated and the drawer closed; the close button, a click outside and Escape each closed it | WORKING | browser |
| D-05 | Notification bell | header · `src/lib/notifications.ts` | Show notifications | Opened a panel listing notifications. The list lives only in `localStorage` | WORKING | browser |
| D-06 | Health badge | `components/layout/health-indicator.tsx` | Show backend health | `GET /health` every 30 s; showed "Healthy" for the mock answer | WORKING | browser |
| D-07 | Floating AI assistant | `components/assistant/floating-assistant.tsx` | Chat | Opened, requested `GET /assistant/model` and `GET /assistant/ws-token` (mock 200), then stayed on "CONNECTING…" because the chat is a WebSocket and there was no server. Console: "WebSocket connection to …/assistant/chat failed" | UNTESTED | browser (partial) |
| D-08 | Sidebar for a non-admin | `sidebar.tsx` `adminOnly` | Hide admin items | With role `user` the sidebar had 13 links and none of the admin items | WORKING | browser |

### 5.2 Dashboard home

| ID | Item | Where | Expected | What actually happened | Verdict | How checked |
|---|---|---|---|---|---|---|
| D-09 | Page load | `/dashboard` · `src/app/dashboard/page.tsx` | KPIs and charts | Rendered from `GET /dashboard/summary`, `/campaigns`, `/analytics/calls` (day and hour), `/analytics/calls/by-campaign`, `/analytics/best-time`, `/analytics/retry-effectiveness` | WORKING | browser |
| D-10 | Range buttons 1h/4h/8h/24h/Custom and view buttons Status/Campaigns/Outcomes | `page.tsx:1505-1531,1775-1805` | Change the chart | "Custom" revealed two date-time inputs and "Campaigns" changed the panel. 1h, 4h, 8h, 24h, Status and Outcomes showed no change detectable by the first-pass test and were not re-checked | UNTESTED | browser (inconclusive) |
| D-11 | Dismiss the call-issue banner | `components/calls/call-issues-banner.tsx:77-84` | Hide it | Hidden; local only, no request | WORKING | browser |
| D-12 | Recent campaigns links and "View all" | `page.tsx:1951-2008` | Navigate | Five campaign links and "View all" returned 200 | WORKING | browser |
| D-13 | Live updates over WebSocket | `page.tsx:1074-1138` | Update in real time | No WebSocket server was available. Code says it falls back to polling after three failures | UNTESTED | code only |

### 5.3 Campaigns

| ID | Item | Where | Expected | What actually happened | Verdict | How checked |
|---|---|---|---|---|---|---|
| D-14 | Campaign list | `/campaigns` · `components/campaigns/campaign-performance-table.tsx` | List campaigns | Five mock campaigns rendered from `GET /campaigns` | WORKING | browser |
| D-15 | "New Campaign" | same | Open the create page | Navigated to `/campaigns/new/` | WORKING | browser |
| D-16 | Campaign name and row expander | `campaign-performance-table.tsx:973-998` | Show details | Name opened a details dialog; the chevron expanded the row | WORKING | browser |
| D-17 | Row "⋯" menu (Pause, Resume, Duplicate, Delete from the list) | `campaign-performance-table.tsx:1038-1140` | Open a menu and act | The first-pass click saw no change and it was not re-checked, so none of the menu actions was exercised. Code: "Duplicate" inserts a fake row in memory and sends no request (`src/app/campaigns/page.tsx:100-116`) | UNTESTED | browser (inconclusive) + code |
| D-18 | Column sort buttons (7) | `campaign-performance-table.tsx:567-570` | Sort | No change detectable by the first-pass test; not re-checked | UNTESTED | browser (inconclusive) |
| D-19 | Name search, status filter, saved table preferences | `campaign-performance-table.tsx:405-416,670-833` | Filter | Typing wrote ten `campaigns.performance.*` keys to `localStorage`. Whether the rows filtered correctly was not established | UNTESTED | browser (inconclusive) |
| D-20 | Export | `campaign-performance-table.tsx:1208-1315` | Export the list | Dialog opened; CSV produced a download named `campaigns-export.csv` built in the browser, no request | WORKING | browser |
| D-21 | Scheduled reports inside the export dialog | `campaign-performance-table.tsx:1438-1487` | Schedule a recurring report | The dialog itself says "Save recurring settings locally (prototype mode)". Settings go to `localStorage` only; no schedule is created anywhere and nothing is ever sent | NOT IMPLEMENTED | browser + code |
| D-22 | Command bar (Ctrl + K) | `components/campaigns/command-bar.tsx` | Search and act | Opened and listed the campaigns. Code also mixes in hard-coded people "Alex Operator", "Morgan Analyst", "Jamie Admin" marked "(prototype)" (`command-bar.tsx:75-86`) | WORKING | browser + code |
| D-23 | Event stream and alert timeline panels | `components/campaigns/event-stream.tsx`, `alert-timeline.tsx` | Show events and alerts | Rendered from `GET /events` and `GET /alerts`, both polled every 10 s | WORKING | browser |
| D-24 | Alert dialog actions (Acknowledge, Resolve, Snooze, Create Rule) | `alert-timeline.tsx:167-180,471-611` | Act on an alert | Not exercised. Code: Snooze is a local 30-minute hide, "Save rule" writes `localStorage` only, and the Root Cause, Timeline, Recommended Actions and Related Incidents tabs are static prototype text | UNTESTED | code only |
| D-25 | Pagination (First, Prev, Next, Last) | `campaign-performance-table.tsx:893-926` | Page through | Disabled with five rows, so not testable | UNTESTED | browser (disabled) |
| D-26 | Create campaign, guided wizard | `/campaigns/new` · `components/campaigns/campaign-wizard.tsx` | Create | "Next" stays disabled until name, company, agent name, opening objective and a voice are set. Then: `POST /campaigns/preview-prompt`, `POST /campaigns` (201), `PUT /campaigns/{id}/lead-fields`, redirect to the new campaign | WORKING | browser |
| D-27 | Create campaign, detailed form | `components/campaigns/campaign-form.tsx` | Create | `POST /campaigns` (201), `PUT /campaigns/{id}/lead-fields`, redirect to the new campaign | WORKING | browser |
| D-28 | Detailed form, empty submit | same | Block | Native validation on the five required fields; no request | WORKING | browser |
| D-29 | Knowledge file upload in the wizard | `campaign-wizard.tsx:480-505,322` | Upload a file | Not exercised | UNTESTED | not checked |
| D-30 | Voice preview play button | `components/campaigns/voice-provider-picker.tsx:136-165` | Play a sample | Sent `POST /ai-options/voices/preview` (mock 200). Audible playback not verified | WORKING | browser |
| D-31 | Campaign detail page | `/campaigns/[id]` · `src/app/campaigns/[id]/page.tsx` | Show the campaign | Stats, live calls, contact lists, contacts, knowledge and call scripts rendered | WORKING | browser |
| D-32 | Pause and Stop | `page.tsx:292-320` | Pause or stop | `POST /campaigns/{id}/pause` and `POST /campaigns/{id}/stop` (mock 200) | WORKING | browser |
| D-33 | Start (the "Who speaks first?" dialog) | `page.tsx:267-290,905-1112` | Start a campaign | Not reached; the check that covered it timed out after the restart | UNTESTED | not checked |
| D-34 | Hang up a live call (2) | `components/calls/live-calls-panel.tsx:431-482` | End the call | `POST /calls/{id}/hangup` (mock 200) | WORKING | browser |
| D-35 | Contact list on/off and "Call this list" | `components/contacts/contact-lists.tsx:102-171` | Toggle and dial | `PATCH /contact-lists/{id}`; "Call this list" asked for confirmation then `POST /contact-lists/{id}/call` | WORKING | browser |
| D-36 | Delete a contact (5 rows) | `page.tsx:363-376` | Remove | Browser confirm, then `DELETE /campaigns/{id}/contacts/{cid}` | WORKING | browser |
| D-37 | "Add Contact" inline form | `page.tsx:650-772` | Add | The form opened. My submit attempt targeted the wrong field, so no `POST` was sent. The same form was verified on `/contacts` (D-58) | UNTESTED | browser (harness miss) |
| D-38 | Import CSV, "Paste numbers" | `components/contacts/smart-csv-import.tsx:80-92` | Import pasted numbers | `POST /contacts/campaigns/{id}/paste` (mock 200). The "Upload CSV" tab here was not exercised | WORKING | browser |
| D-39 | Knowledge panel | `components/campaigns/knowledge-panel.tsx` | Manage knowledge | "Test a question" sent `POST …/knowledge/test`; pin and enable/disable sent `PATCH …/knowledge/nodes/{id}`; delete source sent `DELETE …/knowledge/sources/{id}`. File upload not exercised | WORKING | browser |
| D-40 | "Test agent" | `components/campaigns/test-agent-button.tsx` | Talk to the agent | Needs a WebSocket (`/ws/campaign-test/{id}`) and a microphone | UNTESTED | not checked |
| D-41 | Edit link and Back | `page.tsx:385-391,445-448` | Navigate | Both navigated | WORKING | browser |
| D-42 | Edit campaign, save | `/campaigns/[id]/edit` | Save | `PUT /campaigns/{id}` and `PUT /campaigns/{id}/lead-fields` (mock 200), then back to the campaign | WORKING | browser |

### 5.4 Inbound campaigns

| ID | Item | Where | Expected | What actually happened | Verdict | How checked |
|---|---|---|---|---|---|---|
| D-43 | Inbound list | `/inbound-campaigns` | List and filter | Three campaigns rendered. "Archived" refetched with `include_archived=true`. All, Draft and Paused showed no change detectable by the first-pass test | WORKING | browser |
| D-44 | "Disable all inbound" | `src/app/inbound-campaigns/page.tsx:74-118` | Kill switch with a reason | Confirm button disabled until a reason is typed; then `PATCH /inbound-campaigns/controls` and a toast | WORKING | browser |
| D-45 | New inbound campaign, step 2 (number and routing) | `components/inbound/inbound-campaign-form.tsx` | Create | Empty submit showed "Enter a name…", "Choose a verified phone number", "Choose an inbound-capable SIP trunk". Fully filled: `POST /inbound-campaigns` (201), redirect to the new campaign, toast "Draft created". Step 1 inside the inbound flow was not run; it is the same wizard as D-26 | WORKING | browser |
| D-46 | Draft kept while filling the form | `src/lib/inbound-campaign-draft.ts` | Survive a refresh | The draft is mirrored to `sessionStorage` (`talky:inbound-campaign-new:step2:<id>`) and cleared after a successful create | WORKING | browser |
| D-47 | Detail page actions | `/inbound-campaigns/[id]` | Activate, deactivate, refresh | "Activate" on a paused campaign: `POST …/activate` and toast; "Deactivate" on an active one: `POST …/deactivate` and toast; "Refresh readiness": `GET …/readiness`. "Archive" not exercised | WORKING | browser |
| D-48 | Edit | `/inbound-campaigns/[id]/edit` | Edit when allowed | An active campaign showed a read-only notice with no inputs. A paused one saved with `PUT /inbound-campaigns/{id}`, toast "Draft saved", and returned to the detail page | WORKING | browser |

### 5.5 Call history

| ID | Item | Where | Expected | What actually happened | Verdict | How checked |
|---|---|---|---|---|---|---|
| D-49 | Call list and filters | `/calls` · `src/app/calls/page.tsx` | List and filter | Rendered grouped by campaign. Inbound and Outbound refetched `GET /calls?direction=…`; Leads and Issues filtered in the browser | WORKING | browser |
| D-50 | Search by phone number | `page.tsx:987` | Filter rows | Typed a value; the result could not be measured reliably | UNTESTED | browser (inconclusive) |
| D-51 | Lead type, Notes and post-call Form on each call | `src/lib/call-history-workflow.ts:23,197-234` | Save against the call | No request is sent. Values are written to `localStorage` key `talklee.call-history.workflow.v1:<tenant>`. A note was still there after a reload in the same browser; it would not be on another device or for a colleague | PARTLY WORKING | browser |
| D-52 | AI summary, AI script, play recording | `page.tsx:251-309` | Show or play | `GET /calls/{id}/summary`, `GET /calls/{id}/transcript`, `GET /recordings/{id}/stream` | WORKING | browser |
| D-53 | "Best time to call" | `page.tsx:470-484,598-603` | Show a suggested time | Always shows "Best time to call is not available for this call yet." The call data has no such field | NOT IMPLEMENTED | browser + code |
| D-54 | Review button | `page.tsx:1149-1153` | Rate the call | Opened the "Call review" dialog and requested `GET /calls/{id}/review` | WORKING | browser |
| D-55 | Call detail page | `/calls/[id]` | Review, play, download, edit lead details | Review saved with `PUT /calls/{id}/review`; Play requested the stream; Download saved `recording-rec-1.wav`; a lead field saved with `PUT /calls/{id}/lead-details/email`; "Back to calls" returned; an unknown id showed "Call not found" with "Try again" | WORKING | browser |
| D-56 | Voice note recording and "Retry transcription" | `components/calls/voice-feedback-recorder.tsx` | Record and resend | Recording needs a microphone; the retry button was not on screen for the mock call | UNTESTED | not checked |

### 5.6 Contacts

| ID | Item | Where | Expected | What actually happened | Verdict | How checked |
|---|---|---|---|---|---|---|
| D-57 | Contacts page | `/contacts` · `src/app/contacts/page.tsx` | List per campaign | Rendered; clicking a list card refetched contacts for that list | WORKING | browser |
| D-58 | Add, edit, delete a contact | `page.tsx:325-405,844-1035` | Save | Empty phone blocked by native validation. Add: `POST /campaigns/{id}/contacts` (201). Edit: `PATCH …/contacts/{cid}`. Delete: browser confirm then `DELETE …/contacts/{cid}` | WORKING | browser |
| D-59 | Search | `page.tsx:270-317` | Search on the server | `GET …/contacts?search=555` | WORKING | browser |
| D-60 | Smart CSV importer: "Import N contacts" | `components/contacts/csv-import-mapper.tsx`, used at `src/app/contacts/page.tsx:1127` without an `onConfirm` handler | Import the previewed rows | Choosing a CSV sent `POST /contacts/import/preview` and showed the column mapping. Clicking "Import 3 contacts" sent nothing and changed nothing on the page. Tried twice | BROKEN | browser + code |
| D-61 | Second uploader: "Upload N contacts" | `page.tsx:565-741` | Import | `POST /contacts/campaigns/{id}/upload?skip_duplicates=true`, then an "Import Results" card (3 rows, 2 imported, 1 failed in the mock) | WORKING | browser |
| D-62 | "Download template" and "Download sample CSV" | `csv-import-mapper.tsx:69-84`, `page.tsx:482-499` | Download | "Download template" requested `GET /contacts/fields` and downloaded `talklee-contacts-template.csv`. "Download sample CSV" was not clicked | WORKING | browser |

### 5.7 Analytics and recordings

| ID | Item | Where | Expected | What actually happened | Verdict | How checked |
|---|---|---|---|---|---|---|
| D-63 | Analytics | `/analytics` · `src/app/analytics/page.tsx` | Chart and table | Rendered from `GET /analytics/calls`. "Week" refetched with `group_by=week`. The date-range and direction dropdowns were not exercised. Code: the page ignores the `?campaign=` value that "View Analytics" on the campaigns table adds | WORKING | browser + code |
| D-64 | Recordings | `/recordings` · `components/recordings/recording-media-controls.tsx` | Play, download, delete, rate | Play fetched `GET /recordings/{id}/stream` into an audio element; Download saved `recording-rec-1.wav`; Delete needs a reason (confirm disabled when empty) then `DELETE /recordings/{id}` (204); thumbs sent `PUT /calls/{id}/review`; the voice-note button requested `GET /calls/{id}/feedback` | WORKING | browser |

### 5.8 Connectors, AI options, security

| ID | Item | Where | Expected | What actually happened | Verdict | How checked |
|---|---|---|---|---|---|---|
| D-65 | Connectors page | `/connectors` | Show five connectors | Five cards rendered from `GET /connectors/status` | WORKING | browser |
| D-66 | Connect and Reconnect | `components/connectors/connector-card.tsx:107-122` | Start OAuth | `GET /connectors/{type}/authorize?redirect_uri=…` then a popup opened to the returned URL | WORKING | browser |
| D-67 | OAuth round trip with a real provider | same | Finish connecting | The popup target was a mock URL; no provider was contacted | UNTESTED | not checked |
| D-68 | Disconnect | `connector-card.tsx:129-141` | Disconnect after confirming | Dialog, then `POST /connectors/calendar/disconnect`, toast "Disconnected" | WORKING | browser |
| D-69 | Salesforce panel | `components/connectors/salesforce-settings.tsx` | Test, reveal, rotate, import | "Test connection" `POST …/salesforce/test`; "Reveal URLs" `GET …/webhook-token`; "Rotate token" `POST …/webhook-token`; "Import now" `POST …/salesforce/import` with a result toast. "Save settings" is disabled until something changes and was not exercised | WORKING | browser |
| D-70 | Connector callback pages | `/connectors/callback`, `/connectors/[type]/callback` | Confirm and close | Showed "Connection complete". "Close window" did nothing when the page was opened directly; the browser logged "Scripts may close only the windows that were opened by them" | PARTLY WORKING | browser |
| D-71 | AI Options save | `/ai-options` · `src/app/ai-options/page.tsx:265-303` | Save configuration | `POST /ai-options/config`, then an "Apply this voice to campaigns?" dialog; "Apply to campaigns" sent `POST /campaigns/apply-tts-config` | WORKING | browser |
| D-72 | Benchmark, Test LLM, voice preview | `page.tsx:305-380` | Run | `POST /ai-options/benchmark`, `POST /ai-options/test/llm`, `POST /ai-options/voices/preview` | WORKING | browser |
| D-73 | Pipeline mode switch | `page.tsx:506-514` | Show realtime options | "Realtime" showed turn-detection and noise-reduction controls | WORKING | browser |
| D-74 | "Clone a voice" | `components/ai-options/voice-clone-modal.tsx` | Clone | The button did not appear for the mock provider; it needs ElevenLabs and a microphone | UNTESTED | not checked |
| D-75 | Change password | `/security` · `src/app/security/page.tsx:118-232` | Change | Empty form blocked by native validation; filled form sent `POST /auth/change-password` and showed "Password changed" | WORKING | browser |
| D-76 | Turn on two-factor, first step | `components/auth/mfa-setup.tsx:46` | Show a QR code | `POST /auth/mfa/setup` and a QR image | WORKING | browser |
| D-77 | Two-factor confirm, disable, regenerate codes | `mfa-setup.tsx:85`, `security/page.tsx:280-316` | Complete | The confirm request was not observed; disable and regenerate need two-factor already on | UNTESTED | not checked |
| D-78 | Passkeys | `components/auth/passkey-list.tsx`, `passkey-registration.tsx` | Add and remove | Remove: browser confirm then `DELETE /auth/passkeys/{id}`. Add needs a WebAuthn authenticator and was not run | WORKING | browser |
| D-79 | Sessions | `components/auth/device-list.tsx` | Sign out devices | One device: `DELETE /sessions/{id}`. "Sign out from all other devices": confirm, `GET /sessions/active`, then `DELETE` per session | WORKING | browser |
| D-80 | Tenant-wide security cards | `security/page.tsx:531-591` | Usable controls | The page links to `/admin/api-keys` and `/admin/voice-security` with the text "Not available here", and "IP allow-list" reads "Not available yet" | NOT IMPLEMENTED | browser |

### 5.9 Billing and settings

| ID | Item | Where | Expected | What actually happened | Verdict | How checked |
|---|---|---|---|---|---|---|
| D-81 | Billing overview | `/billing` · `components/billing/billing-overview.tsx` | Subscription, usage, invoices, top-ups | Rendered from nine billing endpoints | WORKING | browser |
| D-82 | Buy a top-up | `components/billing/topup-card.tsx:135-164` | Go to checkout | `POST /billing/topups/checkout`; the mock answered in "mock mode" so a notice appeared. The real redirect to Stripe was not tested | WORKING | browser |
| D-83 | "Manage plan" | `billing-overview.tsx:314-316` | Open plans | Navigated to `/billing/plans/` | WORKING | browser |
| D-84 | Plans | `/billing/plans` · `src/app/billing/plans/page.tsx` | Compare and change plan | Yearly switch changed prices in the browser ($49 to $488). Upgrade and Downgrade sent `POST /billing/create-checkout-session`. Code: the yearly choice is not included in that request (`page.tsx:58-62`). Stripe redirect not tested | WORKING | browser + code |
| D-85 | Invoices | `/billing/invoices`, `/billing/invoices/[id]` | List, open, go back | List of three; opening one and "All Invoices" worked. The PDF links open a Stripe URL in a new tab, which the test blocked. "Print" and "Download PDF" showed no observable effect in headless Chromium | WORKING | browser |
| D-86 | Remaining buttons on `/billing` | `/billing` | Each works | The one-by-one button crawl of this page did not finish. It was the last page in the run and I stopped it after roughly half an hour without a result. The page load, top-up purchase and plan link are covered by D-81 to D-83 | UNTESTED | not checked |
| D-87 | Profile | `/settings` Profile tab · `src/app/settings/page.tsx:67-93` | Save name | `PATCH /auth/me`, toast "Profile updated". After a reload the mock returned its fixed name, so persistence could not be shown | WORKING | browser |
| D-88 | Display and notification preferences | `settings/page.tsx:150-227`, `src/lib/notifications.ts:195` | Save preferences | Toggling sent no request. The value is written to `localStorage` `talklee.notifications.settings.v1` and survived a reload in the same browser only | PARTLY WORKING | browser |
| D-89 | Assistant tile | `settings/page.tsx:114-139` | Configure the assistant | "COMING SOON · V2" with a disabled "Coming soon" button | NOT IMPLEMENTED | browser |
| D-90 | Devices tab | `settings/page.tsx` | List sessions | `GET /sessions/active`; per-device and sign-out-all buttons present | WORKING | browser |
| D-91 | Telephony tab, load and provider test | `components/settings/telephony-providers-section.tsx` | Show providers and trunks, test credentials | Loaded providers, trunks, pool and pool assignment. "Test" sent `POST /telephony/providers/twilio/test` with a result toast | WORKING | browser |
| D-92 | Delete a SIP trunk | `src/lib/telephony-api.ts:371` | Delete | Browser confirm, then `DELETE /telephony/sip/trunks/{id}` and a "SIP trunk deleted" toast against the mock. The Python backend has no `DELETE` route for trunks (no `@router.delete` in `backend/app/api/v1/endpoints/telephony_sip/`) | PARTLY WORKING | browser + code |
| D-93 | Add trunk, save provider credentials, make active, trunk on/off | same | Save | "Add trunk" opened its dialog but my fill did not produce a `POST`. The others were not exercised | UNTESTED | browser (harness miss) |
| D-94 | Recording policy | `components/settings/recording-policy-section.tsx` | Save | "Save policy" is disabled until a field changes; then `PUT /recordings/policy` and toast "Recording policy saved" | WORKING | browser |

### 5.10 Assistant, meetings, reminders, email, notifications

| ID | Item | Where | Expected | What actually happened | Verdict | How checked |
|---|---|---|---|---|---|---|
| D-95 | Assistant hub | `/assistant` | Link to sub-pages | Three links, all 200 | WORKING | browser |
| D-96 | Assistant Actions | `/assistant/actions` · `src/app/assistant/actions/page.tsx:652` | Open for an admin | Signed in with role `admin`, the page showed "You do not have permission to view Assistant Actions. Required role: admin." The gate accepts only `platform_admin` and `tenant_admin`. Nothing else on the page could be tested | BROKEN | browser + code |
| D-97 | Assistant meetings | `/assistant/meetings` | List | Read-only list from `GET /meetings` | WORKING | browser |
| D-98 | Meetings | `/meetings` | List and create | With the calendar connector shown as connected, the list rendered from `GET /calendar/events`. "Create meeting" and the four detail dialogs opened. Submitting a new meeting was not completed. `GET /calendar/events` has no route in the Python backend | PARTLY WORKING | browser + code |
| D-99 | Reminders | `/reminders`, `/assistant/reminders` | List, create, cancel | The list rendered from `GET /reminders` and `GET /calendar/events`. Create and Cancel were not completed. Neither endpoint has a route in the Python backend | PARTLY WORKING | browser + code |
| D-100 | Email | `/email` | Send templated email | With the email connector disconnected the page showed "Connector setup required". With it connected, three templates rendered from `GET /email/templates`. Clicking "Send email" produced no request; the compose step was not driven further. `/email/templates` and `/email/send` exist in the Next.js route handler; in the Python backend `email.py` exists but is not mounted. Send history is kept in `localStorage` only | PARTLY WORKING | browser + code |
| D-101 | Notifications page | `/notifications` | Review and clear | The list is read from `localStorage`. "Clear" emptied it. The "Mark read" buttons showed no change detectable by the first-pass test | PARTLY WORKING | browser |
| D-102 | `/reviews` | `/reviews` | Show reviews | Redirected to `/admin/reviews/` | WORKING | browser |

## 6. Admin pages inside Talk-Leee

Files: `Talk-Leee/src/app/admin/*`, `src/components/admin/*`, `src/components/guards/*`.

| ID | Item | Where | Expected | What actually happened | Verdict | How checked |
|---|---|---|---|---|---|---|
| M-01 | Audit & Access | `/admin` | Audit logs, security events, suspensions, configuration | Page and its four tabs rendered against mocks. On load it requested `GET /admin/audit-logs` and `GET /admin/security-events`; the Suspensions tab requested `GET /admin/partners`. None of these three has a route in the Python backend (it has `/admin/audit/logs` and `/admin/security-events/events`, and no partners list) | PARTLY WORKING | browser + code |
| M-02 | Suspend and reactivate a partner or tenant | `/admin` Suspensions tab | Act | Not exercised; the check did not find the buttons | UNTESTED | not checked |
| M-03 | Audit Logs | `/admin/audit-logs` | List and filter | Seven rows from `GET /admin/audit/logs` (which exists in the backend); the seven category buttons filtered the table in the browser | WORKING | browser |
| M-04 | Agent reviews | `/admin/reviews` | Summary and filters | Rendered from `GET /reviews/summary`, `GET /reviews`, `GET /calls/reviews/options`, `GET /calls/reviews/rewards/balance`; prompt-version and tag filters refetched `GET /reviews` | WORKING | browser |
| M-05 | Voice Security | `/admin/voice-security` | Manage call guards | A static "not available" card; no buttons, no requests | NOT IMPLEMENTED | browser |
| M-06 | Abuse Detection | `/admin/abuse-detection` | Manage abuse rules | Static "not available" card; no buttons, no requests. The backend does have `/admin/abuse/*` routes | NOT IMPLEMENTED | browser + code |
| M-07 | API Keys | `/admin/api-keys` | Manage keys | Static "not available" card | NOT IMPLEMENTED | browser |
| M-08 | Webhooks | `/admin/webhooks` | Manage webhooks | Static "not available" card | NOT IMPLEMENTED | browser |
| M-09 | Rate Limiting | `/admin/rate-limiting` | Manage limits | Static "not available" card | NOT IMPLEMENTED | browser |
| M-10 | Secrets | `/admin/secrets` | Manage secrets | Static "not available" card. The backend does have `/admin/secrets/*` routes | NOT IMPLEMENTED | browser + code |
| M-11 | Partner Billing | `/admin/billing` | Cross-partner billing | "Partner billing is not available in this console" | NOT IMPLEMENTED | browser |
| M-12 | Tenant Billing | `/admin/billing/tenants` | Per-tenant billing | Static "not available" card | NOT IMPLEMENTED | browser |
| M-13 | Non-admin opens guarded admin pages | `/admin`, `/admin/secrets`, `/admin/api-keys` | Refuse | With role `user` each redirected to `/403/` | WORKING | browser |
| M-14 | Non-admin opens Audit Logs | `/admin/audit-logs` | Refuse | With role `user` the page opened and showed the seven mock audit rows. There is no client-side guard. The real backend requires the `audit:read` permission, so what a real user would see was not tested | PARTLY WORKING | browser + code |
| M-15 | Non-admin opens Agent reviews and Partner Billing | `/admin/reviews`, `/admin/billing` | Refuse | Both opened for role `user`. The backend requires an admin for `/reviews`; real behaviour not tested | PARTLY WORKING | browser + code |
| M-16 | Admin links in the sidebar | `sidebar.tsx:85-111` | Navigate | Nine admin links, all 200 | WORKING | browser |

## 7. White-label pages

Files: `Talk-Leee/src/app/white-label/**`, `src/components/white-label/*`.

| ID | Item | Where | Expected | What actually happened | Verdict | How checked |
|---|---|---|---|---|---|---|
| W-01 | White-label admin dashboard | `/white-label/dashboard` | Open for a white-label admin | Redirected to login even with a mocked `white_label_admin` user. The server layout reads an HttpOnly cookie that a browser mock cannot supply | UNTESTED | browser (blocked) |
| W-02 | Partner analytics | `/white-label/[partner]/analytics` | Show analytics | Redirected to login for the same reason | UNTESTED | browser (blocked) |
| W-03 | Partner billing | `/white-label/[partner]/billing` | Show billing | Redirected to login for the same reason. Code: invoices and usage are hard-coded in the server component | UNTESTED | browser (blocked) + code |
| W-04 | Partner dashboard | `/white-label/[partner]/dashboard` · `components/white-label/PartnerDashboard.tsx:16-34` | Live partner figures | Showed "TOTAL SUB-TENANTS 12", "ACTIVE CALLS 8", "MINUTES USED 1,420", "$320 This Month" with no data request at all. The numbers are hard-coded | NOT IMPLEMENTED | browser + code |
| W-05 | Sub-tenant management | `/white-label/[partner]/tenants` · `tenants-client.tsx` | Create and manage tenants | The list is read from `localStorage` (`white_label:<partner>:tenants:v1`). The create dialog opened, but "Create Tenant" stayed disabled with "Allocated minutes exceed remaining capacity (0)" because the partner limits are fixed at 0. Suspend and Resume only rewrote `localStorage`. No request was ever sent | BROKEN | browser + code |
| W-06 | Agent settings | `/white-label/[partner]/tenants/[tenant]/agent-settings` | Load and save | Loaded via `GET` and saved via `PATCH /white-label/partners/{p}/tenants/{t}/agent-settings` with "Saved changes." "Run Test" sent `POST /assistant/execute`. None of these has a route in the Python backend; the agent-settings path exists in the Next.js route handler only | PARTLY WORKING | browser + code |
| W-07 | Branding preview | `/white-label/[partner]/preview` | Show themed components | Rendered. The sample buttons do nothing, which fits a style preview | WORKING | browser |
| W-08 | Breadcrumb links | white-label layout | Navigate | "White Label" goes to `/white-label` (404), "Acme" to `/white-label/acme` (404), "T1" to `/white-label/acme/tenants/t1` (404). These 404s also appear in the console on every white-label page because Next prefetches them | BROKEN | browser |
| W-09 | Access by a normal user | `/white-label/[partner]/dashboard`, `/tenants` | Refuse | With role `user` and no partner, both pages opened and showed the hard-coded partner figures. This was on localhost where the server check is skipped. Reading `proxy.ts`, role `user` is only blocked from `/white-label` and `/white-label/dashboard`, not from `/white-label/<partner>/…` | PARTLY WORKING | browser + code |
| W-10 | Partner switcher on the preview page (Acme, Zen) | preview page | Switch branding | Not exercised | UNTESTED | not checked |

## 8. The separate Vite admin panel (`Admin/frontend`)

Built with `VITE_API_BASE_URL=/api/v1`, served with `vite preview` on port 4300, all API calls mocked. Signed in as `platform_admin` unless stated.

| ID | Item | Where | Expected | What actually happened | Verdict | How checked |
|---|---|---|---|---|---|---|
| V-01 | Login page | `/login` · `src/pages/LoginPage.tsx` | Load, validate | Rendered; empty submit blocked by native validation | WORKING | browser |
| V-02 | Wrong credentials | `src/lib/auth.tsx:134-141` | Show an error | `POST /auth/login` (mock 401) then "Invalid credentials" | WORKING | browser |
| V-03 | Correct credentials | `auth.tsx:160-172` | Sign in | Token stored in `localStorage` `admin_token`; landed on `/` | WORKING | browser |
| V-04 | Account with two-factor enabled | `auth.tsx:144-151` | Sign in | "This account requires MFA, which the admin panel does not support yet. Use a non-MFA admin account." | NOT IMPLEMENTED | browser |
| V-05 | Signed-out visit | `src/components/AdminRouteGuard.tsx:40-42` | Redirect to login | `/tenants` redirected to `/login` | WORKING | browser |
| V-06 | Logout | whole panel | A way to sign out | No logout control was found anywhere in the UI. Code: `auth.logout` is never called | NOT IMPLEMENTED | browser + code |
| V-07 | Unknown URL | `src/App.tsx:118` | Handle | Redirected to `/` | WORKING | browser |
| V-08 | Sidebar (10 links) | `src/components/Sidebar.tsx:24-35` | Navigate | Every route loaded | WORKING | browser |
| V-09 | Role gating | `AdminRouteGuard.tsx`, `App.tsx:17` | Restrict | `tenant_admin`: "Access Denied" on `/`, `/tenants`, `/users`; Actions, Incidents and System Health opened with a three-item sidebar. `user`: "Access Denied" everywhere | WORKING | browser |
| V-10 | Command Center | `/` | Live overview | Rendered from eight endpoints | WORKING | browser |
| V-11 | "Pause All Calls" and "End" call | `src/components/LiveCalls.tsx`, `CallTerminationAction.tsx` | Act after confirming | Pause: inline confirm then `PUT /admin/calls/pause`, button became "Resume Calls". End: inline confirm then `POST /admin/calls/{id}/terminate`, row showed "Ending…" | WORKING | browser |
| V-12 | "More +", "Alert", "Critical" buttons on the Command Center | `TopTenantsList.tsx:35,62-65`, `Incidents.tsx:77-80` | Do something | No request, no navigation, no dialog. They have no click handler | BROKEN | browser + code |
| V-13 | Header search, bell and message buttons | `src/components/Header.tsx:6-29` | Search and notify | Typing and pressing Enter did nothing; the two buttons did nothing. The "5" badge, the "Prod" label and the name "Admin" are hard-coded | NOT IMPLEMENTED | browser + code |
| V-14 | Footer text on the Command Center | `src/components/Footer.tsx:8` | No internal notes | Visible text: "Deliverable: Fully functional, integrated admin dashboard in 7 days." | BROKEN | browser |
| V-15 | Tenants | `/tenants` | Search, filter, suspend, quota | Search and status filter refetched; Suspend: modal then `POST /admin/tenants/{id}/suspend`; Edit Quota: `PATCH /admin/tenants/{id}/quota` | WORKING | browser |
| V-16 | Calls | `/calls` | Four tabs, play, delete, details | Live, History, Recordings and Feedback tabs loaded. Play fetched `GET /admin/recordings/{id}/audio` into an audio element. Delete needs a reason (confirm disabled when empty) then `DELETE /admin/recordings/{id}`. A history row opened a drawer with three requests. History search refetched | WORKING | browser |
| V-17 | Feedback "Retry transcript" | `src/components/FeedbackTable.tsx:105` | Retry | `POST /admin/feedback/{id}/transcription/retry` | WORKING | browser |
| V-18 | Inbound Control | `/inbound` | Kill switches, quarantine, approvals | Applying a switch without a reason showed "Enter an operational reason of at least 8 characters…"; with a reason it sent `PATCH /admin/inbound/controls` and a confirmation. Quarantine sent `POST …/quarantine`. "Approve reassignment" sent `POST …/approve`; the request raised by the same admin showed a disabled "Second admin required" | WORKING | browser |
| V-19 | Actions | `/actions` | Inspect, retry, cancel | Row opened a drawer (`GET /admin/actions/{id}`); Retry on a failed SMS sent `POST …/retry`; Cancel on a running and a pending action sent `POST …/cancel`; search and status filter refetched | WORKING | browser |
| V-20 | Connectors | `/connectors` | Reconnect, revoke, details | "Force Reconnect" sent `POST …/reconnect` immediately with no confirmation; "Revoke Access": modal then `POST …/revoke`; "View Details" opened a drawer; search filtered in the browser; status filter refetched | WORKING | browser |
| V-21 | Users: add, edit, search | `/users` · `src/pages/UsersPage.tsx` | Manage users | Empty add showed "Name, email and a temporary password are required."; add sent `POST /admin/users` (201); edit sent `PATCH /admin/users/{id}`; search refetched. The temporary password field is a plain text input | WORKING | browser |
| V-22 | Users: block | `UsersPage.tsx:107-115` | Block with care | One click sent `PATCH /admin/users/{id}` with no confirmation. In the mock data the first row (`admin@example.com`) is the signed-in admin's own account and the UI did not stop the self-block | PARTLY WORKING | browser |
| V-23 | Users: delete | `UsersPage.tsx:117-128` | Delete after confirming | The click timed out in my test | UNTESTED | browser (harness miss) |
| V-24 | Usage & Cost | `/usage-cost` | Change the period | Changing the From date refetched `GET /admin/usage/summary` and `/breakdown` | WORKING | browser |
| V-25 | Incidents | `/incidents` · `src/pages/IncidentsPage.tsx:29-33` | Manage incidents | "Incident Management Coming Soon". No requests, no controls | NOT IMPLEMENTED | browser |
| V-26 | System Health | `/system-health` | Show health | Four widgets rendered from four endpoints | WORKING | browser |
| V-27 | Dark theme | whole panel | Follow the system theme | Body background is identical in light and dark colour schemes | NOT IMPLEMENTED | browser |
| V-28 | Failed actions | `TenantsTable.tsx:118-137`, `ConnectorsTable.tsx:131-147`, `ActionDetailDrawer.tsx:152-171` | Tell the admin | Code: these handlers do not look at the error the client returns, so a failed suspend, revoke, retry or cancel would look like success. Not exercised with failing responses | UNTESTED | code only |
| V-29 | API address | `src/lib/api.ts:6` | Configurable | Defaults to `http://localhost:8000/api/v1` unless `VITE_API_BASE_URL` is set at build time. No `.env` file exists in the folder | UNTESTED | code only |

Every request the panel made during these runs has a matching route in the backend code.

## 9. Backend connectivity

Method: every request the Talk-Leee frontend sent during the browser runs was recorded (144 distinct method-and-path combinations after replacing ids). Each was matched against the 412 routes the Python backend mounts, read from `backend/app/api`. 132 have a matching route. 12 do not.

### 9.1 Frontend calls with no matching Python backend endpoint (observed in the browser)

| Request | Seen on | What the Python backend has instead | Consequence if the frontend talks to the Python API |
|---|---|---|---|
| `GET /admin/audit-logs` | `/admin` on load | `GET /admin/audit/logs` | Audit tab of "Audit & Access" has no data |
| `GET /admin/security-events` | `/admin` on load | `GET /admin/security-events/events` | Security Events tab has no data |
| `GET /admin/partners` | `/admin` Suspensions tab | No partner list route | Suspensions tab has no partners |
| `GET /assistant/runs` | `/assistant/actions` | None | Runs list fails |
| `POST /assistant/execute` | white-label agent settings "Run Test" | None | Test fails |
| `GET /calendar/events` | `/meetings`, `/reminders`, `/assistant/reminders` | `/meetings/` only | Meetings and reminders lists fail |
| `GET /reminders` | `/reminders`, `/assistant/reminders` | None | Reminders page fails |
| `GET /email/templates` | `/email` | `email.py` exists but is not mounted in `routes.py` | No templates |
| `GET /white-label/partners/{p}/tenants/{t}/agent-settings` | white-label agent settings | None | Settings do not load |
| `PATCH /white-label/partners/{p}/tenants/{t}/agent-settings` | same, Save | None | Settings do not save |
| `DELETE /telephony/sip/trunks/{id}` | Settings, Telephony, delete trunk | `PATCH`, activate, deactivate, test only | Delete fails |
| `POST /auth/create-profile` | `/auth/callback` with a token | None, in either backend | Call fails; the flow continued anyway |

These were confirmed absent by searching `backend/app/api` for the path strings. The failure itself is inferred: the backend was not running, so no 404 or 405 was actually observed.

The Next.js route handler in `Talk-Leee` does serve `/email/templates`, `/email/send`, `/white-label/partners/…`, `/assistant/plan` and its own auth paths. It serves none of the dashboard data endpoints in production mode. So neither single backend covers every page; see limit 9 in section 1.

### 9.2 Calls defined in frontend code but not observed being sent (code only)

Listed in `src/lib/backend-endpoints.ts` and `src/lib/backend-api.ts`, with no matching Python route: `POST /calendar/events`, `PATCH /calendar/events/{id}`, `DELETE /calendar/events/{id}`, `POST /reminders`, `PATCH /reminders/{id}`, `POST /reminders/{id}/cancel`, `POST /email/send`, `POST /assistant/plan`, `POST /assistant/runs/{id}/retry`, `POST /admin/partners/{id}/suspend`, `POST /admin/partners/{id}/reactivate`, `GET /connector-accounts`, `POST /connectors`, `POST /voice/calls/guard`, `POST /voice/calls/start`, `POST /auth/logout_all`, `GET /auth/sessions`, `POST /auth/sessions/revoke`, `POST /billing/plan/change`. I did not see any of these sent, so I cannot say which are still reachable from the UI.

### 9.3 Trailing slashes

The frontend calls `/campaigns`, `/calls`, `/meetings`, `/recordings` and `/inbound-campaigns` without a trailing slash. The backend declares those list routes as `/`. This relies on FastAPI's automatic redirect. Not verified live.

### 9.4 Every interactive element and its endpoint

Sections 3 to 8 name the endpoint for each feature. Appendix A lists every button that was clicked with the request it sent. Controls that send nothing and keep their data in the browser are in section 12.

### 9.5 Requests that return an error on a normal visit

Against the real backend: not measured, because it was not running. Against the mocks, with every endpoint answered correctly, the only error responses on a normal visit were:

- `GET /white-label/` and `GET /white-label/<partner>/` returning 404 on every white-label page (real 404s from the Next app; see W-08).
- `GET /campaigns/{id}/knowledge` 404 and `GET /calls/{id}/review` 404, which the mock returns on purpose for records without knowledge or a review; the pages handled both.

Predicted from section 9.1, if the frontend points at the Python API: the twelve requests in that table on the pages listed.

## 10. Responsive behaviour

Sizes tested: 320x568, 360x640, 390x844, 412x915, 667x375, 768x1024, 820x1180, 1024x768, 1280x800, 1440x900, 1920x1080, each in light and dark. The check was automatic: page-level horizontal scroll, and visible elements extending past the right edge. About 720 screenshots were saved; only a handful were looked at by eye.

| ID | Item | Where | Expected | What actually happened | Verdict | How checked |
|---|---|---|---|---|---|---|
| R-01 | Public pages, 21 routes, 462 checks | public site | No horizontal scroll | None, except R-02. A brief overflow of the hero text at 320 and 360 wide was an entrance animation and was gone on re-check | WORKING | browser |
| R-02 | Real Estate page at 768x1024 | `/industries/real-estate` | No horizontal scroll | Page is 773 px wide in both themes; the "Request an Enterprise Demo" button sticks out 5 px | BROKEN | browser |
| R-03 | Auth pages, 4 routes, 88 checks | `/auth/*` | No horizontal scroll | None | WORKING | browser |
| R-04 | Dashboard pages, 33 routes, 726 checks | dashboard | No horizontal scroll | No page-level horizontal scroll at any size | WORKING | browser |
| R-05 | Wide tables at 1024 and below | `/calls`, `/campaigns/[id]`, `/admin/audit-logs`, analytics chart | Remain reachable | Each sits in a container that scrolls sideways, so nothing is lost, but columns such as "Actions" on `/calls` are off-screen until the user scrolls inside the card | WORKING | browser |
| R-06 | Analytics "Group by" at 667x375 | `/analytics` | All three buttons visible | The "Month" button ends at 700 px on a 667 px screen and the page clips it, so it is cut off and cannot be scrolled into view | BROKEN | browser |
| R-07 | Admin pages, 11 routes, 242 checks | `/admin/*` | No horizontal scroll | None | WORKING | browser |
| R-08 | White-label pages that rendered, 4 routes, 88 checks | `/white-label/acme/*` | No horizontal scroll | None. The other three white-label routes redirected to login, so their layout was not tested | WORKING | browser |
| R-09 | Light and dark themes | all Talk-Leee pages | Theme applies | The `light` or `dark` class was applied on every page in every check. Contrast and legibility were not reviewed | WORKING | browser |
| R-10 | Vite admin panel | `Admin/frontend`, 11 routes, 242 checks | Usable on small screens | Horizontal scroll in 152 of 242 checks. The sidebar is a fixed 200 px at every size with no hamburger. At phone widths the content is over 1,000 px wide. Even at 1280x800 the Command Center, Calls, Inbound and Users pages scroll sideways. Only the login page fits at every size | BROKEN | browser |
| R-11 | Clicking at sizes other than 1280x800 | everything | Controls reachable and working | Only the two navigation drawers were exercised at 390x844 | UNTESTED | not checked |
| R-12 | Visual review for overlap and cut-off text | everything | Clean layout | Not reviewed beyond the automatic overflow test and about ten screenshots | UNTESTED | not checked |

## 11. Everything NOT tested, and why

### 11.1 Not possible in this environment

| What | Reason |
|---|---|
| Any behaviour against the real Python backend: real data, real errors, real permissions, real saves, persistence after reload | No Docker, Postgres, Redis or backend virtualenv on this machine |
| Firefox, Safari/WebKit, Edge, real phones and tablets | Only Playwright Chromium is installed |
| Server-side auth and role redirects in `proxy.ts` | The proxy bypasses its checks on localhost |
| `/white-label/dashboard`, `/white-label/[partner]/analytics`, `/white-label/[partner]/billing` | Their server layouts need an HttpOnly cookie; they redirected to login |
| All WebSocket features: Ask AI voice, assistant chat and voice, campaign "Test agent", dashboard live updates | No WebSocket server; browser route mocks do not cover WebSockets |
| Passkey sign-in and passkey registration | Needs a WebAuthn authenticator |
| Microphone features: voice notes, voice cloning, Ask AI | Headless Chromium has no microphone |
| Stripe checkout and customer portal redirects | Mock answered in "mock mode"; no Stripe account |
| OAuth connection to Google, HubSpot, Salesforce | No provider credentials; popup target was a mock URL |
| `mailto:`, `tel:` and invoice PDF links | External targets were blocked during the runs |
| Cloudflare Turnstile and the breached-password check result | External; not driven |
| Which API base URL production uses | Not in the repo |

### 11.2 Started but not finished, or stopped by instruction

| What | State |
|---|---|
| Second-pass check of 98 dashboard buttons that showed no effect in the first pass (plus 89 sidebar group buttons) | Not run; I was told to stop. Listed as unconfirmed in Appendix A |
| Button crawl of `/billing` | Did not finish; stopped after roughly half an hour on this one page. No results |
| Campaign "Start" dialog, campaign list row menu (Pause, Resume, Duplicate, Delete) | Not reached or inconclusive |
| Alert dialog actions on `/campaigns` | Not exercised |
| Reminder create and cancel, meeting create, email compose and send | Dialogs opened; submit not completed |
| Assistant Actions page controls | Blocked by the role bug (D-96) |
| `/admin` suspend and reactivate | Buttons not found by the check |
| Two-factor confirm, disable, regenerate codes | Not exercised |
| Settings: add trunk, save provider, make active, trunk on/off | Not exercised or my fill missed |
| Campaign detail "Add Contact" submit and CSV upload tab | My fill missed; verified on `/contacts` instead |
| Vite admin: delete user, behaviour on failed actions | Click timed out; not exercised |
| Register "Resend", white-label partner switcher, "Download sample CSV", "Archive" inbound campaign | Not clicked |
| 10 individual buttons whose click failed in the test tool | Listed as UNTESTED in Appendix A |
| Buttons that were disabled or hidden at 1280x800 | Not clicked; disabled ones are named per page in Appendix A |
| Error-state wording and recovery on each page | In the first sweep every API returned 404 and no page crashed, but I did not review each page's error message or retry control |
| Clicking at the ten other screen sizes; visual review of screenshots | Not done |

### 11.3 Buttons that showed no effect even on the second look

Twelve clicks still showed nothing after the stricter re-check. None is counted as working. My reading of each:

| Button | Page | Reading |
|---|---|---|
| "Continue with Password", "Get Started", "Send Reset Code" | `/auth/login`, `/auth/register`, `/auth/forgot-password` | Clicked with empty fields; native validation blocks the submit. Expected. The filled flows are A-05 to A-14 |
| "Continue with Password" (3) | the three white-label routes that redirected to login | Same as above; these were the login page |
| "Audit Logs" tab | `/admin` | Already the selected tab |
| "All" | `/admin/audit-logs` | Already the selected filter |
| "unrecorded", "Clear" | `/admin/reviews` | No filter was active; "Clear" had nothing to clear. "unrecorded" not understood |
| Two sample buttons | `/white-label/acme/preview` | Decorative buttons on a style preview |

## 12. Data that is saved only in the browser

None of the following reaches a server. It is lost on another device, in another browser, or when site data is cleared. Items marked "browser" were observed; the rest were read in code.

| What the user does | Where it is kept | Where in the app | How checked |
|---|---|---|---|
| Contact form message | Nowhere. It is discarded | `/contact` | browser |
| Lead type, notes and post-call form on each call | `localStorage` `talklee.call-history.workflow.v1:<tenant>` | `/calls` | browser |
| Display and notification preferences, including "webhook" routing | `localStorage` `talklee.notifications.settings.v1` | `/settings` | browser |
| Notification history | `localStorage` `talklee.notifications.v1` | bell, `/notifications` | browser |
| White-label sub-tenants (name, minutes, concurrency, status) | `localStorage` `white_label:<partner>:tenants:v1` | `/white-label/[partner]/tenants` | browser |
| Scheduled report settings (recurrence, recipients, webhook; defaults `ops@company.com`, `https://example.com/webhook`) | `localStorage` `campaigns.performance.reportSchedule` | `/campaigns` export dialog | browser (dialog text) + code |
| Alert rules from "Create Rule" | `localStorage` `campaigns.performance.rules` | `/campaigns` alert dialog | code only |
| "Snooze" on an alert | In memory, 30 minutes | `/campaigns` | code only |
| "Duplicate" campaign | In memory until the next refresh | `/campaigns` | code only |
| Assistant actions verification checklist ("UAT sign-off recorded" and similar) | `localStorage` `assistant.actions.verification.v1` | `/assistant/actions` | code only |
| Email send history | `localStorage` `talklee.email.audit.v1` | `/email` | code only |
| "Remember me" | `localStorage` `remember_me`, never read | `/auth/login` | code + browser |
| Campaign table sort, filters, page size, export preferences | `localStorage` `campaigns.performance.*` (ten keys seen) | `/campaigns` | browser |
| Assistant actions filters and view | `localStorage` `assistant.actions.audit.*` | `/assistant/actions` | code only |
| Inbound campaign drafts before submit | `sessionStorage` `talky:inbound-campaign-new:*` | `/inbound-campaigns/new` | browser |
| Dismissed call-issue banner, seen qualified-lead alerts | memory; `localStorage` `talklee.qlead.seen.v1` | dashboard, `/calls` | browser + code |
| Theme, sidebar state | `localStorage` `talklee.theme`, `talklee.sidebar.state.v1` | everywhere | browser |
| Access token | `localStorage` `talklee.auth.token` (also sent to the server as a Bearer token) | everywhere | browser |
| Vite admin access token | `localStorage` `admin_token` | admin panel | browser |

Theme, sidebar state and table preferences are reasonable to keep in the browser. The ones a user is likely to believe are stored on the server are the first six rows.

## 13. Console errors and failed requests

Uncaught JavaScript errors: **none** on any page load or on any of the 917 button clicks.

| Message or failure | Where | Cause |
|---|---|---|
| `Failed to load resource: 404` for `GET /white-label/`, `GET /white-label/<partner>/`, `GET /white-label/<partner>/tenants/<tenant>/` and their `?_rsc=` prefetches | Every white-label page that rendered (4 routes) | Real. Breadcrumb links to pages that do not exist (W-08) |
| `WebSocket connection to 'ws://…/api/v1/assistant/chat' failed` | Every dashboard page after opening the floating assistant | Expected here: no WebSocket server |
| `[VoiceAgent] Microphone error: NotSupportedError` | Homepage after clicking Ask AI while signed in | Expected here: headless browser has no microphone |
| `Scripts may close only the windows that were opened by them.` (warning) | `/connectors/callback`, `/connectors/[type]/callback` | Real when the page is opened directly (D-70) |
| `Failed to load resource: 404` for `GET /campaigns/{id}/knowledge` and `GET /calls/{id}/review` | Campaign and inbound detail, `/recordings`, `/calls` | The mock answers 404 on purpose for records with no knowledge or review; the UI handled it |
| `Failed to load resource: 404` for `GET /ai-options/providers`, `/voices`, `/config` | All 21 public pages, first sweep only | Mock had no fixture yet. Shows that these three requests fire on marketing pages for a signed-in visitor, and that a failure raises a "Request failed" toast there (P-27) |
| `POST /auth/create-profile` 404 | `/auth/callback` with a token | Real: no such route in either backend |
| `POST /auth/refresh` after a failed login | `/auth/login` with wrong credentials | Real extra request; harmless |
| `net::ERR_ABORTED` on `?_rsc=` requests, one `blob:` URL and one `/_next/image` request | Many pages | Next.js link prefetches and media loads cancelled by the next navigation. Not a fault |
| `net::ERR_ABORTED` on `/api/v1/events`, `/health`, `/assistant/runs` | `/assistant/actions`, `/admin` | Polling requests cancelled when the test closed the page |
| Four data requests sent before the login redirect | `/dashboard` opened while signed out | Real: the page starts loading data before the guard redirects (A-18) |

Against the real backend the list would differ; see section 9.5.

## 14. Everything BROKEN, ordered by how badly it affects users

### 14.1 Observed in the browser

1. **Contact form discards every message and reports success** (P-13). Every enquiry from the public site is lost and the visitor is told it was sent.
2. **57 "Book a Demo", "Talk to Sales", "See Plans" and similar buttons lead to the top of the homepage** (P-11). The `#contact` target does not exist. This is the main conversion path on 15 marketing pages.
3. **Placeholder contact details are live** (P-14): a 555 phone number and "123 AI Street".
4. **White-label sub-tenants cannot be created, and nothing in that screen reaches a server** (W-05). The create button can never be enabled.
5. **Assistant Actions refuses an admin** with "Required role: admin" (D-96).
6. **The smart CSV importer's "Import N contacts" button does nothing** (D-60). A user who previews a file there and presses Import gets no import and no message. The separate uploader on the same page works.
7. **White-label breadcrumb links go to 404 pages** (W-08).
8. **Vite admin panel is unusable below desktop width** (R-10) and scrolls sideways on four pages even at 1280x800.
9. **Vite admin dead controls**: "More +", "Alert", "Critical" (V-12); an internal note "Deliverable: …in 7 days." is shown to admins (V-14).
10. **Login ignores where the user was going** (A-07).
11. **Analytics "Month" button is cut off on a landscape phone** (R-06).
12. **Real Estate page scrolls sideways on a 768 px tablet** (R-02).

### 14.2 Read in code only

13. **Healthcare "Practice" plan has no price number** (P-26).

### 14.3 Broken if the frontend talks to the Python backend (request observed, missing route read in code, failure not observed)

14. **Reminders**: list, and by code also create and cancel (D-99).
15. **Meetings**: calendar events list, and by code also create, edit and cancel (D-98).
16. **"Audit & Access" admin page**: audit logs, security events and partners (M-01).
17. **Assistant runs and "Run Test"** (section 9.1).
18. **White-label agent settings** load and save (W-06).
19. **Email templates and send** (D-100).
20. **Delete SIP trunk** (D-92).
21. **`POST /auth/create-profile`** on the callback page (A-16).

### 14.4 Not broken, but presented as available and not implemented

Eight in-app admin pages are "not available" cards (M-05 to M-12). Also: AI Voices preview (P-18), white-label partner dashboard with hard-coded figures (W-04), scheduled reports (D-21), "Best time to call" (D-53), Settings assistant tile (D-89), IP allow-list and tenant security links (D-80), "Remember me" (A-08), and in the Vite admin panel: logout (V-06), MFA sign-in (V-04), header search and notifications (V-13), Incidents (V-25), dark theme (V-27).

### 14.5 Controls or pages visible to a user who should not have them

- `/admin/audit-logs`, `/admin/reviews` and `/admin/billing` open for role `user` (M-14, M-15). The data they show depends on the backend refusing; that was not tested.
- `/white-label/<partner>/dashboard` and `/tenants` open for role `user` on localhost and, reading `proxy.ts`, would not be blocked in production either (W-09).
- The "Ask AI" widget is shown to signed-out visitors who cannot use it (P-15).

## 15. Closing assessment

The dashboard front end is in reasonable shape as a piece of UI. Every page rendered at every size without a crash, there was not one uncaught JavaScript error in several hundred page loads and button clicks, the layout holds from 320 px to 1920 px, and the core flows for campaigns, contacts, inbound numbers, recordings, billing, security and AI options each sent a sensible request and handled the answer. Sign-in, registration, password reset, logout and session expiry behaved correctly against mocks.

That is a statement about the frontend against well-formed mock data. I could not run the backend, so I cannot confirm that a single feature works end to end, that anything is actually stored, or that permissions are enforced. Treat every "WORKING" here as "the frontend half looks right".

Three things are wrong regardless of the backend. The public site loses its leads: the contact form throws messages away and most demo and sales buttons go nowhere. Twelve requests the frontend sends have no route in the Python backend, which takes out reminders, meetings, email, assistant runs, one admin page and white-label settings unless production is wired differently from what the repo shows. And a set of features that look finished are not: data that stays in the browser, eight admin pages that are placeholders, and a white-label area that is mostly a mock-up.

The Vite admin panel does its main jobs against mocks on a desktop screen. It has no logout, no support for accounts with two-factor, no small-screen layout and several dead buttons.

What I would want before calling the frontend verified: the same run repeated against a real backend with a test tenant, the second-pass check of the unconfirmed dashboard buttons, the unfinished flows in section 11.2, and a pass in Safari and Firefox.

## Appendix A. Every button clicked, page by page

Each visible, enabled button was clicked once on a freshly loaded page at 1280x800 in Chromium with the mock backend. "What happened" is what the test observed within about 1.5 seconds. Shared shell buttons (sidebar, header, navbar) are listed for the first page of each group only. "mock 200" means the mock answered; it says nothing about the real backend.

### Public site — every visible, enabled button clicked at 1280x800 (Chromium, mocked backend)

#### `/`

- Heading: AI VOICE AGENT PLATFORM FORSEAMLESS CALL AUTOMATION / AI VOICE AGENT PLATFORM FOR AI VOICE AGENT PLATFORM FOR SEAM. Requests on load: 4 distinct.
- Links: 49 anchors, 22 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 19 in DOM, 14 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Products | page content changes | WORKING (mocked backend) | browser |
| Use Cases | page content changes | WORKING (mocked backend) | browser |
| Industries | page content changes | WORKING (mocked backend) | browser |
| Switch to dark theme | no navigation, request, dialog or text change within ~1.5 s | UNCLEAR (click-failed) | browser |
| Ask AI Click to talk | page content changes; calls `POST /ai-options/voices/preview` (mock 200) | WORKING (mocked backend) | browser |
| Get Started Now | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| What is Talk-Lee AI? | page content changes | WORKING (mocked backend) | browser |
| Can Talk-Lee AI integrate with my existing phone system or C | page content changes | WORKING (mocked backend) | browser |
| Can Talk-Lee AI integrate with my existing phone system or C | page content changes | WORKING (mocked backend) | browser |
| How does Talk-Lee AI learn my company information? | page content changes | WORKING (mocked backend) | browser |
| Can calls be transferred to human agents? | page content changes | WORKING (mocked backend) | browser |
| Is Talk-Lee AI secure and compliant? | page content changes | WORKING (mocked backend) | browser |
| Can I resell Talk-Lee AI under my own brand? | page content changes | WORKING (mocked backend) | browser |
| Do you provide phone numbers for campaigns? | page content changes | WORKING (mocked backend) | browser |

#### `/ai-voice-dialer/`

- Heading: AI Voice Dialer. Requests on load: 4 distinct.
- Links: 57 anchors, 22 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 18 in DOM, 13 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Start Calling with AI | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| See a Live Call | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| See AI in Action | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Launch Your First Campaign | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Stop Dialing. Start Closing. | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Still Curious? See It Live | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Still Have Questions? Talk to Us → | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Start Your AI Calling Campaign Now | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Talk to a Growth Strategist | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |

#### `/ai-assist/`

- Heading: AI Assist for smarter business automation. Requests on load: 4 distinct.
- Links: 54 anchors, 22 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 15 in DOM, 10 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Get Started | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Book a Demo | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| See How Setup Works → | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Explore Every Feature → | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Get Your Custom Quote | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Book an AI Assist Demo | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |

#### `/ai-voice-agent/`

- Heading: AI Voice Agents. Requests on load: 4 distinct.
- Links: 56 anchors, 22 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 17 in DOM, 12 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Book a Demo | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Build Your AI Agent | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Stop Losing Calls to Voicemail → | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Turn Your Calls Into Actions → | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Find Out How It Fits Your Industry | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Hear Your AI in Action → | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Book a Demo | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Talk to an AI Expert | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |

#### `/ai-voices/`

- Heading: AI Voices. Requests on load: 4 distinct.
- Links: 48 anchors, 22 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 14 in DOM, 9 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Preview voice of Sarah | changes its own state/DOM | RESPONDS-VISUAL (its own state or appearance changes; no request) | browser |
| Preview voice of Michael | changes its own state/DOM | RESPONDS-VISUAL (its own state or appearance changes; no request) | browser |
| Preview voice of Amelia | changes its own state/DOM | RESPONDS-VISUAL (its own state or appearance changes; no request) | browser |
| Preview voice of David | changes its own state/DOM | RESPONDS-VISUAL (its own state or appearance changes; no request) | browser |
| Preview voice of Olivia | changes its own state/DOM | RESPONDS-VISUAL (its own state or appearance changes; no request) | browser |

#### `/industries/education/`

- Heading: AI for Education. Requests on load: 4 distinct.
- Links: 57 anchors, 22 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 18 in DOM, 13 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Start Free — 2 Minutes | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| See How It Works | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Start Free — 2 Minutes | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Book a Demo | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Start Free | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Book a Demo | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Talk to an AI Expert | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Start Free | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Book a Live Demo | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |

#### `/industries/financial-services/`

- Heading: AI for Financial Services. Requests on load: 4 distinct.
- Links: 55 anchors, 22 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 16 in DOM, 11 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Book a Demo | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| See How It Works | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Start Free | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Book a Demo | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Talk to an AI Expert | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Book a Demo | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| See Plans | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |

#### `/industries/healthcare/`

- Heading: Healthcare Call Routing AI. Requests on load: 4 distinct.
- Links: 57 anchors, 22 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 18 in DOM, 13 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Start Free — 2 Minutes | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| See How It Works | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Watch Live Demo | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Start Free | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Book Free Consultation | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Talk to an AI Expert | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Book Free Consultation | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Create Your Free Account | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Book a Live Demo | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |

#### `/industries/marketing-automation/`

- Heading: AI Marketing Automation - Built for Agencies. Requests on load: 4 distinct.
- Links: 55 anchors, 22 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 16 in DOM, 11 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Book a Demo | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| See How It Works | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Start Free | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Book a Demo | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Talk to an AI Expert | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Book a Demo | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| See Agency Pricing | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |

#### `/industries/professional-services/`

- Heading: AI for Professional Services. Requests on load: 4 distinct.
- Links: 54 anchors, 22 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 15 in DOM, 10 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| See What AI Can Do | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Book a Private Demo | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Try It Free | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Book a Private Demo | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Request Enterprise Access | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Book a Demo | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |

#### `/industries/real-estate/`

- Heading: AI for Real Estate. Requests on load: 4 distinct.
- Links: 58 anchors, 22 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 19 in DOM, 14 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| See It Handle a Real Estate Call | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Book a Demo | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| See AI in Action | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Book a Demo | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| See It Handle a Real Estate Call | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Try It Free | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| See Team Plans | navigates to `chrome-error://chromewebdata/`; page content changes | WORKING (mocked backend) | browser |
| Request an Enterprise Demo | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Book a Demo | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| See AI in Action | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |

#### `/industries/recruitment/`

- Heading: AI Hiring Automation. Requests on load: 4 distinct.
- Links: 55 anchors, 22 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 16 in DOM, 11 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Book a Demo | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| See How It Works | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Start Free | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Book a Demo | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Talk to an AI Expert | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Book a Demo | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| See Recruitment Plans | navigates to `chrome-error://chromewebdata/`; page content changes | WORKING (mocked backend) | browser |

#### `/industries/retail-ecommerce/`

- Heading: AI for Retail & E-commerce. Requests on load: 4 distinct.
- Links: 53 anchors, 22 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 14 in DOM, 9 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| See AI Handle a Retail Call | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Book a Demo | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Start Automating Customer Calls | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Book a Demo | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| See AI in Action | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |

#### `/industries/software-tech-support/`

- Heading: Smarter Software Support With AI. Requests on load: 4 distinct.
- Links: 56 anchors, 22 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 17 in DOM, 12 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| See AI in Action | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Start Your Free Trial | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Try It Free | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Explore Growth Plans | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Request a Custom Demo | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Try AI Free | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Book a Strategy Call | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| See a Live Demo | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |

#### `/industries/travel-industry/`

- Heading: AI for Travel Industry. Requests on load: 4 distinct.
- Links: 55 anchors, 22 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 16 in DOM, 11 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Book a Demo | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| See How It Works | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Start Free | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Book a Demo | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Talk to an AI Expert | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Book a Demo | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Explore Hospitality Plans | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |

#### `/use-cases/automated-lead-qualification/`

- Heading: Lead Qualification Services. Requests on load: 4 distinct.
- Links: 52 anchors, 22 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 13 in DOM, 8 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Book a Demo | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| See How It Works | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Book a Demo | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| Talk to Sales | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |

#### `/use-cases/customer-services-support/`

- Heading: AI Customer Support. Requests on load: 4 distinct.
- Links: 53 anchors, 22 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 14 in DOM, 9 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Book a Demo | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| See It in Action | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Book a Demo | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |
| See It in Action | navigates to `/#contact`; page content changes | BROKEN (lands on the top of the homepage; no #contact section exists) | browser |
| Book a Demo | navigates to `/auth/register/`; page content changes | WORKING (mocked backend) | browser |

#### `/contact/`

- Heading: (none). Requests on load: 4 distinct.
- Links: 50 anchors, 22 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 10 in DOM, 5 clicked. Inputs visible: 4.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Submit | page content changes | WORKING (mocked backend) | browser |

#### `/terms/`

- Heading: Terms of Service. Requests on load: 4 distinct.
- Links: 48 anchors, 22 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 9 in DOM, 4 clicked.


#### `/privacy/`

- Heading: Privacy Policy. Requests on load: 4 distinct.
- Links: 48 anchors, 22 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 9 in DOM, 4 clicked.


#### `/403/`

- Heading: (none). Requests on load: 4 distinct.
- Links: 2 anchors, 2 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 0 in DOM, 0 clicked.


### Authentication pages — every visible, enabled button clicked at 1280x800 (Chromium, mocked backend)

#### `/auth/login/`

- Heading: Welcome back. Requests on load: 4 distinct.
- Links: 4 anchors, 4 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 2 in DOM, 2 clicked. Inputs visible: 2.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Continue with Password | no navigation, request, dialog or text change within ~1.5 s | NO EFFECT OBSERVED (re-checked) | browser |
| Sign in with Passkey | page content changes | WORKING (mocked backend) | browser |

#### `/auth/register/`

- Heading: Create your account. Requests on load: 4 distinct.
- Links: 6 anchors, 4 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 1 in DOM, 1 clicked. Inputs visible: 3.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Get Started | no navigation, request, dialog or text change within ~1.5 s | NO EFFECT OBSERVED (re-checked) | browser |

#### `/auth/forgot-password/`

- Heading: Reset your password. Requests on load: 4 distinct.
- Links: 2 anchors, 2 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 1 in DOM, 1 clicked. Inputs visible: 1.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Send Reset Code | no navigation, request, dialog or text change within ~1.5 s | NO EFFECT OBSERVED (re-checked) | browser |

#### `/auth/callback/`

- Heading: (none). Requests on load: 4 distinct.
- Links: 0 anchors, 0 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 1 in DOM, 1 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Try Again | navigates to `/auth/login/`; page content changes | WORKING (mocked backend) | browser |

### Dashboard — every visible, enabled button clicked at 1280x800 (Chromium, mocked backend)

Not crawled (run did not reach them before the stop instruction): `/billing/`.

#### `/dashboard/`

- Heading: Dashboard. Requests on load: 12 distinct.
- Links: 28 anchors, 26 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 17 in DOM, 16 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Collapse sidebar | page content changes; expands/collapses | WORKING (mocked backend) | browser |
| Billing & Logs | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED-SHELL (no effect seen by first-pass heuristic; same sidebar group button re-checked on admin pages and did change state) | browser |
| Security Center | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED-SHELL (no effect seen by first-pass heuristic; same sidebar group button re-checked on admin pages and did change state) | browser |
| Developer Hub | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED-SHELL (no effect seen by first-pass heuristic; same sidebar group button re-checked on admin pages and did change state) | browser |
| Logout | navigates to `/`; page content changes; calls `POST /auth/logout` (mock 200) | WORKING (mocked backend) | browser |
| Open notification center | opens a dialog ("Notifications Qualified lead: Jane Doe · +15550001…"); page content changes; expands/collapses | WORKING (mocked backend) | browser |
| Dismiss call issue: Call could not start | page content changes | WORKING (mocked backend) | browser |
| 1h | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| 4h | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| 8h | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| 24h | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Custom | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Status | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Campaigns | page content changes | WORKING (mocked backend) | browser |
| Outcomes | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Open AI assistant | opens a dialog ("Assistant CONNECTING… Llama 3.3 70B GPT-4.1 mini H…"); page content changes; calls `GET /assistant/model` (mock 200), `GET /assistant/ws-token` (mock 200) | WORKING (mocked backend) | browser |

#### `/campaigns/`

- Heading: Campaign Performance. Requests on load: 8 distinct.
- Links: 23 anchors, 22 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 67 in DOM, 61 clicked. Disabled (not clicked): "First", "Prev", "Next", "Last". Inputs visible: 12.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Ctrl + K | opens a dialog ("Command Bar Search campaigns, navigate, and run ac…"); page content changes | WORKING (mocked backend) | browser |
| All statuses | page content changes; expands/collapses | WORKING (mocked backend) | browser |
| Export | opens a dialog ("Export & Reporting Exports the current page. Expor…"); page content changes | WORKING (mocked backend) | browser |
| New Campaign | navigates to `/campaigns/new/`; page content changes; calls `GET /contacts/fields` (mock 200) | WORKING (mocked backend) | browser |
| Sort by Campaign | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Sort by Status | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Sort by Completion | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Sort by Success Rate | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Sort by Leads | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Sort by Completed | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Sort by Failed | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Expand row details | page content changes | WORKING (mocked backend) | browser |
| Q4 Roofing Outreach Homeowners in greater Phoenix — book fre | opens a dialog ("Q4 Roofing Outreach Active Completion 30.4% Succes…"); page content changes | WORKING (mocked backend) | browser |
| Row actions | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Expand row details | page content changes | WORKING (mocked backend) | browser |
| Solar Follow-ups Re-engage last quarter's solar quote reques | opens a dialog ("Solar Follow-ups Paused Completion 35.0% Success R…"); page content changes | WORKING (mocked backend) | browser |
| Row actions | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Expand row details | page content changes | WORKING (mocked backend) | browser |
| Dental Reactivation Knowledge-driven: answers from the uploa | opens a dialog ("Dental Reactivation Draft Completion 0.0% Success…"); page content changes | WORKING (mocked backend) | browser |
| Row actions | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Expand row details | page content changes | WORKING (mocked backend) | browser |
| Spring Promo Completed seasonal promotion. | opens a dialog ("Spring Promo Completed Completion 94.0% Success Ra…"); page content changes | WORKING (mocked backend) | browser |
| Row actions | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Expand row details | page content changes | WORKING (mocked backend) | browser |
| Insurance Cross-sell Stopped after compliance review. | opens a dialog ("Insurance Cross-sell Failed Completion 11.1% Succe…"); page content changes | WORKING (mocked backend) | browser |
| Row actions | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| All | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Campaigns | page content changes | WORKING (mocked backend) | browser |
| System | page content changes | WORKING (mocked backend) | browser |
| Alerts | page content changes | WORKING (mocked backend) | browser |
| User Actions | page content changes | WORKING (mocked backend) | browser |
| Hide | page content changes; expands/collapses | WORKING (mocked backend) | browser |
| ⚠︎ Qualified lead captured 05:40 PM Daniel Reyes booked an i | opens a dialog ("Qualified lead captured Alerts • 9/30/2026, 5:40:2…"); page content changes | WORKING (mocked backend) | browser |
| 🖥︎ Voice pipeline latency elevated 04:18 PM P95 first-token | opens a dialog ("Voice pipeline latency elevated System • 9/30/2026…"); page content changes | WORKING (mocked backend) | browser |
| 🖥︎ Call could not start 03:18 PM Text-to-speech provider re | opens a dialog ("Call could not start System • 9/30/2026, 3:18:25 P…"); page content changes | WORKING (mocked backend) | browser |
| 🖥︎ Nightly recording retention run 09:38 AM 12 recordings o | opens a dialog ("Nightly recording retention run System • 9/30/2026…"); page content changes | WORKING (mocked backend) | browser |
| 🏁︎ 50 calls completed 04:38 PM Q4 Roofing Outreach passed 5 | opens a dialog ("50 calls completed Milestones • 9/29/2026, 4:38:25…"); page content changes | WORKING (mocked backend) | browser |
| 👤︎ Campaign paused 06:08 PM Solar Follow-ups paused by veri | opens a dialog ("Campaign paused User Actions • 9/28/2026, 6:08:25…"); page content changes | WORKING (mocked backend) | browser |
| 📢︎ Campaign started 06:38 PM Q4 Roofing Outreach started wi | opens a dialog ("Campaign started Campaign • 9/27/2026, 6:38:25 PM…"); page content changes | WORKING (mocked backend) | browser |
| 📢︎ Contact list imported 06:38 PM Roofing leads - Sept.csv: | opens a dialog ("Contact list imported Campaign • 9/19/2026, 6:38:2…"); page content changes | WORKING (mocked backend) | browser |
| Hide | page content changes; expands/collapses | WORKING (mocked backend) | browser |
| Critical | page content changes | WORKING (mocked backend) | browser |
| Warning | page content changes | WORKING (mocked backend) | browser |
| Info | page content changes | WORKING (mocked backend) | browser |
| Network | page content changes | WORKING (mocked backend) | browser |
| API | page content changes | WORKING (mocked backend) | browser |
| Campaign | page content changes | WORKING (mocked backend) | browser |
| System | page content changes | WORKING (mocked backend) | browser |
| Active | page content changes | WORKING (mocked backend) | browser |
| Resolved | page content changes | WORKING (mocked backend) | browser |
| Investigating |  | UNTESTED (click failed in harness) | browser |
| Critical Network Active New Carrier rejecting outbound calls | opens a dialog ("Carrier rejecting outbound calls Critical • Networ…"); page content changes | WORKING (mocked backend) | browser |
| Warning API Investigating Ack TTS provider latency Cartesia | opens a dialog ("TTS provider latency Warning • API • Investigating…"); page content changes | WORKING (mocked backend) | browser |
| Info Campaign Resolved Ack Campaign finished Spring Promo co | opens a dialog ("Campaign finished Info • Campaign • Resolved Impac…"); page content changes | WORKING (mocked backend) | browser |

#### `/campaigns/new/`

- Heading: Create Campaign. Requests on load: 7 distinct.
- Links: 23 anchors, 21 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 25 in DOM, 23 clicked. Disabled (not clicked): "Next: Knowledge". Inputs visible: 18.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Back to campaigns | navigates to `about:blank`; page content changes | WORKING (mocked backend) | browser |
| Prefer the detailed form? → | page content changes | WORKING (mocked backend) | browser |
| Lead Generation Outbound calls — qualify leads and book cons | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Customer Support Inbound — resolve issues, handle escalation | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| AI Receptionist Inbound — answer, route, book appointments, | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Select Emma - Warm, Professional | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Play voice preview | calls `POST /ai-options/voices/preview` (mock 200) | WORKING (mocked backend) | browser |
| Select Liam - Calm, Confident | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Play voice preview | calls `POST /ai-options/voices/preview` (mock 200) | WORKING (mocked backend) | browser |
| Mon | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Tue | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Wed | page content changes | WORKING (mocked backend) | browser |
| Thu | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Fri | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Sat | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Sun | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |

#### `/campaigns/11111111-1111-4111-8111-111111111111/`

- Heading: Q4 Roofing Outreach. Requests on load: 17 distinct.
- Links: 23 anchors, 21 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 62 in DOM, 57 clicked. Disabled (not clicked): "Test". Inputs visible: 3.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Back to campaigns | navigates to `/campaigns/`; page content changes; calls `GET /calls/{id}` (mock 200), `GET /campaigns` (mock 200) | WORKING (mocked backend) | browser |
| Test agent | opens a dialog ("Test this campaign's agent Talk to the exact agent…"); page content changes | WORKING (mocked backend) | browser |
| Edit | navigates to `/campaigns/11111111-1111-4111-8111-111111111111/edit/`; page content changes; calls `GET /contacts/fields` (mock 200), `GET /campaigns/{id}/lead-fields` (mock 200) | WORKING (mocked backend) | browser |
| Pause | calls `POST /campaigns/{id}/pause` (mock 200), `GET /campaigns/{id}/contacts` (mock 200), `GET /campaigns/{id}/contact-lists` (mock 200), `GET /campaigns/{id}/knowledge` (mock 200), `GET /campaigns/{id}/calls` (mock 200), `GET /calls/{id}` (mock 200) | WORKING (mocked backend) | browser |
| Stop | calls `POST /campaigns/{id}/stop` (mock 200), `GET /campaigns/{id}/contacts` (mock 200), `GET /campaigns/{id}/contact-lists` (mock 200), `GET /campaigns/{id}/knowledge` (mock 200), `GET /campaigns/{id}/calls` (mock 200), `GET /calls/{id}` (mock 200) | WORKING (mocked backend) | browser |
| View more | page content changes | WORKING (mocked backend) | browser |
| Hang up call to +14805550140 | page content changes; calls `POST /calls/{id}/hangup` (mock 200) | WORKING (mocked backend) | browser |
| Hang up call to +14805550141 | page content changes; calls `POST /calls/{id}/hangup` (mock 200) | WORKING (mocked backend) | browser |
| Call issues 2 problems · 3 numbers | page content changes; expands/collapses | WORKING (mocked backend) | browser |
| Roofing leads - Sept.csv Active 120 contacts | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Set “Roofing leads - Sept.csv” inactive | page content changes; calls `PATCH /contact-lists/list-a-1` (mock 200) | WORKING (mocked backend) | browser |
| Call this list | browser confirm:Call all eligible contacts in “Roofing leads - Sep; page content changes; calls `POST /contact-lists/list-a-1/call` (mock 200), `GET /campaigns/{id}/contacts` (mock 200), `GET /campaigns/{id}/contact-lists` (mock 200), `GET /campaigns/{id}/knowledge` (mock 200), `GET /campaigns/{id}/calls` (mock 200), `GET /calls/{id}` (mock 200) | WORKING (mocked backend) | browser |
| Paste batch 2026-09-20 Inactive 15 contacts | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Set “Paste batch 2026-09-20” active | page content changes; calls `PATCH /contact-lists/list-a-2` (mock 200) | WORKING (mocked backend) | browser |
| Call this list | browser confirm:Call all eligible contacts in “Paste batch 2026-09; page content changes; calls `POST /contact-lists/list-a-2/call` (mock 200), `GET /campaigns/{id}/contacts` (mock 200), `GET /campaigns/{id}/contact-lists` (mock 200), `GET /campaigns/{id}/knowledge` (mock 200), `GET /campaigns/{id}/calls` (mock 200), `GET /calls/{id}` (mock 200) | WORKING (mocked backend) | browser |
| Ungrouped 3 contacts | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Import CSV | opens a dialog ("Import contacts Upload a CSV or paste a list of nu…"); page content changes | WORKING (mocked backend) | browser |
| Add Contact | page content changes | WORKING (mocked backend) | browser |
| All | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Leads | page content changes | WORKING (mocked backend) | browser |
| Edit +14155550123 | page content changes | WORKING (mocked backend) | browser |
| Delete +14155550123 | browser confirm:Remove +14155550123 from this campaign? It will no; calls `DELETE /campaigns/{id}/contacts/{id}` (mock 200), `GET /campaigns/{id}/contacts` (mock 200), `GET /campaigns/{id}/contact-lists` (mock 200), `GET /campaigns/{id}/knowledge` (mock 200), `GET /campaigns/{id}/calls` (mock 200), `GET /calls/{id}` (mock 200) | WORKING (mocked backend) | browser |
| Edit +16473476870 | page content changes | WORKING (mocked backend) | browser |
| Delete +16473476870 | browser confirm:Remove +16473476870 from this campaign? It will no; calls `DELETE /campaigns/{id}/contacts/{id}` (mock 200), `GET /campaigns/{id}/contacts` (mock 200), `GET /campaigns/{id}/contact-lists` (mock 200), `GET /campaigns/{id}/knowledge` (mock 200), `GET /campaigns/{id}/calls` (mock 200), `GET /calls/{id}` (mock 200) | WORKING (mocked backend) | browser |
| Edit +12125550000 | page content changes | WORKING (mocked backend) | browser |
| Delete +12125550000 | browser confirm:Remove +12125550000 from this campaign? It will no; calls `DELETE /campaigns/{id}/contacts/{id}` (mock 200), `GET /campaigns/{id}/contacts` (mock 200), `GET /campaigns/{id}/contact-lists` (mock 200), `GET /campaigns/{id}/knowledge` (mock 200), `GET /campaigns/{id}/calls` (mock 200), `GET /calls/{id}` (mock 200) | WORKING (mocked backend) | browser |
| Edit +13105550199 | page content changes | WORKING (mocked backend) | browser |
| Delete +13105550199 | browser confirm:Remove +13105550199 from this campaign? It will no; page content changes; calls `DELETE /campaigns/{id}/contacts/{id}` (mock 200), `GET /campaigns/{id}/contacts` (mock 200), `GET /campaigns/{id}/contact-lists` (mock 200), `GET /campaigns/{id}/knowledge` (mock 200), `GET /campaigns/{id}/calls` (mock 200) | WORKING (mocked backend) | browser |
| Edit +447429916656 | page content changes | WORKING (mocked backend) | browser |
| Delete +447429916656 | browser confirm:Remove +447429916656 from this campaign? It will n; calls `DELETE /campaigns/{id}/contacts/{id}` (mock 200), `GET /campaigns/{id}/contacts` (mock 200), `GET /campaigns/{id}/contact-lists` (mock 200), `GET /campaigns/{id}/knowledge` (mock 200), `GET /campaigns/{id}/calls` (mock 200), `GET /calls/{id}` (mock 200) | WORKING (mocked backend) | browser |
| Upload .md / .txt | opens file chooser | WORKING (mocked backend) | browser |
| Delete this source and its sections | calls `DELETE /campaigns/{id}/knowledge/sources/src-1` (mock 200), `GET /campaigns/{id}/knowledge` (mock 200) | WORKING (mocked backend) | browser |
| Collapse all | page content changes; calls `GET /campaigns/{id}/contacts` (mock 200), `GET /campaigns/{id}/contact-lists` (mock 200), `GET /campaigns/{id}/knowledge` (mock 200), `GET /campaigns/{id}/calls` (mock 200), `GET /calls/{id}` (mock 200) | WORKING (mocked backend) | browser |
| Expand all | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Collapse | page content changes | WORKING (mocked backend) | browser |
| Edit | page content changes | WORKING (mocked backend) | browser |
| Unpin | calls `PATCH /campaigns/{id}/knowledge/nodes/kn-1` (mock 200) | WORKING (mocked backend) | browser |
| Disable | calls `PATCH /campaigns/{id}/knowledge/nodes/kn-1` (mock 200) | WORKING (mocked backend) | browser |
| Edit | page content changes | WORKING (mocked backend) | browser |
| Pin (prioritise) | calls `PATCH /campaigns/{id}/knowledge/nodes/kn-1a` (mock 200) | WORKING (mocked backend) | browser |
| Disable | calls `PATCH /campaigns/{id}/knowledge/nodes/kn-1a` (mock 200) | WORKING (mocked backend) | browser |
| Edit | page content changes | WORKING (mocked backend) | browser |
| Pin (prioritise) | calls `PATCH /campaigns/{id}/knowledge/nodes/kn-2` (mock 200) | WORKING (mocked backend) | browser |
| Disable | calls `PATCH /campaigns/{id}/knowledge/nodes/kn-2` (mock 200) | WORKING (mocked backend) | browser |
| Edit | page content changes | WORKING (mocked backend) | browser |
| Pin (prioritise) | calls `PATCH /campaigns/{id}/knowledge/nodes/kn-3` (mock 200) | WORKING (mocked backend) | browser |
| Enable | calls `PATCH /campaigns/{id}/knowledge/nodes/kn-3` (mock 200) | WORKING (mocked backend) | browser |
| +14155550123 9/30/2026, 5:03:25 PM 3m 7s answered 7 turns | page content changes | WORKING (mocked backend) | browser |
| +16473476870 9/30/2026, 5:38:25 PM 4m 1s goal_achieved 3 tur | page content changes | WORKING (mocked backend) | browser |
| +12125550000 9/30/2026, 6:08:25 PM -- no_answer 0 turns | page content changes | WORKING (mocked backend) | browser |

#### `/campaigns/11111111-1111-4111-8111-111111111111/edit/`

- Heading: Edit Campaign. Requests on load: 9 distinct.
- Links: 24 anchors, 22 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 16 in DOM, 15 clicked. Inputs visible: 45.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Back to campaign | navigates to `/campaigns/11111111-1111-4111-8111-111111111111/`; page content changes; calls `GET /campaigns/{id}/contacts` (mock 200), `GET /campaigns/{id}/contact-lists` (mock 200), `GET /campaigns/{id}/knowledge` (mock 200), `GET /campaigns/{id}/calls` (mock 200), `GET /calls/{id}` (mock 200) | WORKING (mocked backend) | browser |
| Play voice preview | calls `POST /ai-options/voices/preview` (mock 200) | WORKING (mocked backend) | browser |
| Emma - Warm, Professional cartesia female EN | page content changes | WORKING (mocked backend) | browser |
| (unnamed) | calls `POST /ai-options/voices/preview` (mock 200) | WORKING (mocked backend) | browser |
| (unnamed) | calls `POST /ai-options/voices/preview` (mock 200) | WORKING (mocked backend) | browser |
| Show preview | page content changes | WORKING (mocked backend) | browser |
| Save Campaign | calls `PUT /campaigns/{id}` (mock 200), `PUT /campaigns/{id}/lead-fields` (mock 200), `GET /campaigns/{id}/contacts` (mock 200), `GET /campaigns/{id}/contact-lists` (mock 200), `GET /campaigns/{id}/knowledge` (mock 200), `GET /campaigns/{id}/calls` (mock 200) | WORKING (mocked backend) | browser |
| Cancel |  | UNTESTED (click failed in harness) | browser |

#### `/inbound-campaigns/`

- Heading: Inbound Campaigns. Requests on load: 9 distinct.
- Links: 24 anchors, 22 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 14 in DOM, 13 clicked. Inputs visible: 1.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Disable all inbound | opens a dialog ("Disable all tenant inbound calling? This is the te…"); page content changes | WORKING (mocked backend) | browser |
| All | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Draft | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Active |  | UNTESTED (click failed in harness) | browser |
| Paused | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Archived | calls `GET /inbound-campaigns` (mock 200) | WORKING (mocked backend) | browser |

#### `/inbound-campaigns/new/`

- Heading: New Inbound Campaign. Requests on load: 8 distinct.
- Links: 24 anchors, 21 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 17 in DOM, 15 clicked. Disabled (not clicked): "Next: Knowledge". Inputs visible: 14.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Prefer the detailed form? → | page content changes | WORKING (mocked backend) | browser |
| Lead Generation Outbound calls — qualify leads and book cons | navigates to `/inbound-campaigns/new/?draft=muo6713a-rvyfgiac` | WORKING (mocked backend) | browser |
| Customer Support Inbound — resolve issues, handle escalation | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| AI Receptionist Inbound — answer, route, book appointments, | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Select Emma - Warm, Professional | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Play voice preview | calls `POST /ai-options/voices/preview` (mock 200) | WORKING (mocked backend) | browser |
| Select Liam - Calm, Confident | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Play voice preview | calls `POST /ai-options/voices/preview` (mock 200) | WORKING (mocked backend) | browser |

#### `/inbound-campaigns/22222222-2222-4222-8222-222222222222/`

- Heading: Inbound Campaign. Requests on load: 14 distinct.
- Links: 26 anchors, 23 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 11 in DOM, 10 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Deactivate | opens a dialog ("Deactivate inbound calling? New calls will stop ro…"); page content changes | WORKING (mocked backend) | browser |
| Refresh readiness | calls `GET /inbound-campaigns/{id}/readiness` (mock 200) | WORKING (mocked backend) | browser |
| Refresh rejected inbound calls | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |

#### `/inbound-campaigns/22222222-2222-4222-8222-222222222222/edit/`

- Heading: Edit Inbound Campaign. Requests on load: 8 distinct.
- Links: 24 anchors, 22 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 8 in DOM, 7 clicked.


#### `/calls/`

- Heading: Call History. Requests on load: 8 distinct.
- Links: 34 anchors, 24 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 52 in DOM, 37 clicked. Inputs visible: 9.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Dismiss call issue: Call could not start | page content changes | WORKING (mocked backend) | browser |
| All | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Inbound | page content changes; calls `GET /calls` (mock 200) | WORKING (mocked backend) | browser |
| Outbound | page content changes; calls `GET /calls` (mock 200) | WORKING (mocked backend) | browser |
| All | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Leads | page content changes | WORKING (mocked backend) | browser |
| Issues | page content changes | WORKING (mocked backend) | browser |
| Q4 Roofing Outreach 3 calls · 7:08 total 2 answered 1 failed | page content changes; expands/collapses | WORKING (mocked backend) | browser |
| Call time Wednesday, September 30, 2026 at 6:08:25 PM | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Show AI summary | page content changes; expands/collapses; calls `GET /calls/{id}/summary` (mock 200) | WORKING (mocked backend) | browser |
| Show best time to call | page content changes; expands/collapses | WORKING (mocked backend) | browser |
| Show AI script | page content changes; expands/collapses; calls `GET /calls/{id}/transcript` (mock 200) | WORKING (mocked backend) | browser |
| Open form for +12125550000 | opens a dialog ("Post-call form Capture the key details from the AI…"); page content changes | WORKING (mocked backend) | browser |
| Review call with +12125550000 | opens a dialog ("Call review How did the AI handle the call with +1…"); page content changes; calls `GET /calls/{id}/review` (mock 404) | WORKING (mocked backend) | browser |
| Call time Wednesday, September 30, 2026 at 5:38:25 PM | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Show AI summary | page content changes; expands/collapses; calls `GET /calls/{id}/summary` (mock 200) | WORKING (mocked backend) | browser |
| Show best time to call | page content changes; expands/collapses | WORKING (mocked backend) | browser |
| Show AI script | page content changes; expands/collapses; calls `GET /calls/{id}/transcript` (mock 200) | WORKING (mocked backend) | browser |
| Open form for +16473476870 | opens a dialog ("Post-call form Capture the key details from the AI…"); page content changes | WORKING (mocked backend) | browser |
| Play recording | calls `GET /recordings/{id}/stream` (mock 200) | WORKING (mocked backend) | browser |
| Review call with +16473476870 | opens a dialog ("Call review How did the AI handle the call with +1…"); page content changes; calls `GET /calls/{id}/review` (mock 404) | WORKING (mocked backend) | browser |
| Call time Wednesday, September 30, 2026 at 5:03:25 PM | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Show AI summary | page content changes; expands/collapses; calls `GET /calls/{id}/summary` (mock 200) | WORKING (mocked backend) | browser |
| Show best time to call | page content changes; expands/collapses | WORKING (mocked backend) | browser |
| Show AI script | page content changes; expands/collapses; calls `GET /calls/{id}/transcript` (mock 200) | WORKING (mocked backend) | browser |
| Open form for +14155550123 | opens a dialog ("Post-call form Capture the key details from the AI…"); page content changes | WORKING (mocked backend) | browser |
| Play recording | calls `GET /recordings/{id}/stream` (mock 200) | WORKING (mocked backend) | browser |
| Review call with +14155550123 | opens a dialog ("Call review How did the AI handle the call with +1…"); page content changes; calls `GET /calls/{id}/review` (mock 200) | WORKING (mocked backend) | browser |
| Main line 1 call · 1:36 total 1 answered 0 failed | page content changes; expands/collapses | WORKING (mocked backend) | browser |
| Solar Follow-ups 1 call · -- total 0 answered 1 failed | page content changes; expands/collapses | WORKING (mocked backend) | browser |

#### `/calls/33333333-3333-4333-8333-333333333333/`

- Heading: Call Details. Requests on load: 14 distinct.
- Links: 23 anchors, 21 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 34 in DOM, 33 clicked. Inputs visible: 1.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Back to calls | navigates to `about:blank`; page content changes | WORKING (mocked backend) | browser |
| Play | calls `GET /recordings/{id}/stream` (mock 200) | WORKING (mocked backend) | browser |
| Download | downloads `recording-rec-1.wav`; calls `GET /recordings/{id}/download` (mock 200) | WORKING (mocked backend) | browser |
| Record again | opens a dialog ("Replace this feedback note? A call keeps one note.…"); page content changes | WORKING (mocked backend) | browser |
| (unnamed) | calls `GET /calls/{id}/feedback/audio` (mock 200) | WORKING (mocked backend) | browser |
| 1 out of 5 | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| 2 out of 5 | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| 3 out of 5 | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| 4 out of 5 | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| 5 out of 5 | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Didn't understand | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Interrupted the caller | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Didn't answer the question | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Response too long | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Response too slow | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Repeated itself | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Wrong qualifying question | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Wrong call outcome | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Poor objection handling | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Incorrect information | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Good conversation | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Update review | page content changes; calls `PUT /calls/{id}/review` (mock 200), `GET /calls/{id}/reviews` (mock 200) | WORKING (mocked backend) | browser |
| About captured lead details | page content changes | WORKING (mocked backend) | browser |
| Edit Email | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Edit Company Name | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Edit Best Time To Call | page content changes | WORKING (mocked backend) | browser |

#### `/contacts/`

- Heading: Contacts. Requests on load: 9 distinct.
- Links: 22 anchors, 21 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 38 in DOM, 27 clicked. Inputs visible: 1.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Select campaign | page content changes; expands/collapses | WORKING (mocked backend) | browser |
| Add more contacts | page content changes; expands/collapses | WORKING (mocked backend) | browser |
| Roofing leads - Sept.csv Active 120 contacts | page content changes; calls `GET /campaigns/{id}/contacts` (mock 200) | WORKING (mocked backend) | browser |
| Set “Roofing leads - Sept.csv” inactive |  | UNTESTED (click failed in harness) | browser |
| Call this list | browser confirm:Call all eligible contacts in “Roofing leads - Sep; page content changes; calls `POST /contact-lists/list-a-1/call` (mock 200) | WORKING (mocked backend) | browser |
| Paste batch 2026-09-20 Inactive 15 contacts | page content changes; calls `GET /campaigns/{id}/contacts` (mock 200) | WORKING (mocked backend) | browser |
| Set “Paste batch 2026-09-20” active | page content changes; calls `PATCH /contact-lists/list-a-2` (mock 200) | WORKING (mocked backend) | browser |
| Call this list | browser confirm:Call all eligible contacts in “Paste batch 2026-09; page content changes; calls `POST /contact-lists/list-a-2/call` (mock 200) | WORKING (mocked backend) | browser |
| Ungrouped 3 contacts | page content changes; calls `GET /campaigns/{id}/contacts` (mock 200) | WORKING (mocked backend) | browser |
| Add contact | page content changes; expands/collapses | WORKING (mocked backend) | browser |
| Edit +14155550123 | page content changes | WORKING (mocked backend) | browser |
| Delete +14155550123 | browser confirm:Remove +14155550123 from this campaign? It will no; calls `DELETE /campaigns/{id}/contacts/{id}` (mock 200), `GET /campaigns/{id}/contacts` (mock 200), `GET /campaigns/{id}/contact-lists` (mock 200) | WORKING (mocked backend) | browser |
| Edit +16473476870 | page content changes | WORKING (mocked backend) | browser |
| Delete +16473476870 |  | UNTESTED (click failed in harness) | browser |
| Edit +12125550000 | page content changes | WORKING (mocked backend) | browser |
| Delete +12125550000 | browser confirm:Remove +12125550000 from this campaign? It will no; calls `DELETE /campaigns/{id}/contacts/{id}` (mock 200), `GET /campaigns/{id}/contacts` (mock 200), `GET /campaigns/{id}/contact-lists` (mock 200) | WORKING (mocked backend) | browser |
| Edit +13105550199 | page content changes | WORKING (mocked backend) | browser |
| Delete +13105550199 | browser confirm:Remove +13105550199 from this campaign? It will no; calls `DELETE /campaigns/{id}/contacts/{id}` (mock 200), `GET /campaigns/{id}/contacts` (mock 200), `GET /campaigns/{id}/contact-lists` (mock 200) | WORKING (mocked backend) | browser |
| Edit +447429916656 | page content changes | WORKING (mocked backend) | browser |
| Delete +447429916656 | browser confirm:Remove +447429916656 from this campaign? It will n; calls `DELETE /campaigns/{id}/contacts/{id}` (mock 200), `GET /campaigns/{id}/contacts` (mock 200), `GET /campaigns/{id}/contact-lists` (mock 200) | WORKING (mocked backend) | browser |

#### `/analytics/`

- Heading: Analytics. Requests on load: 7 distinct.
- Links: 22 anchors, 21 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 13 in DOM, 12 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Select date range | page content changes; expands/collapses | WORKING (mocked backend) | browser |
| Select call direction | page content changes; expands/collapses | WORKING (mocked backend) | browser |
| Day | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Week | page content changes | WORKING (mocked backend) | browser |
| Month | page content changes | WORKING (mocked backend) | browser |

#### `/recordings/`

- Heading: Recordings. Requests on load: 9 distinct.
- Links: 22 anchors, 21 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 28 in DOM, 27 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Play recording |  | UNTESTED (click failed in harness) | browser |
| Download recording | downloads `recording-rec-1.wav`; calls `GET /recordings/{id}/download` (mock 200) | WORKING (mocked backend) | browser |
| Delete recording | opens a dialog ("Delete recording permanently? The audio will be pe…"); page content changes | WORKING (mocked backend) | browser |
| Rated good — click to keep | calls `PUT /calls/{id}/review` (mock 200) | WORKING (mocked backend) | browser |
| Rate this conversation poor | calls `GET /calls/{id}/review` (mock 200), `GET /calls/{id}/review` (mock 404), `PUT /calls/{id}/review` (mock 200) | WORKING (mocked backend) | browser |
| Record a voice note | page content changes; calls `GET /calls/{id}/feedback` (mock 200) | WORKING (mocked backend) | browser |
| Edit your written feedback | page content changes | WORKING (mocked backend) | browser |
| Play recording | calls `GET /recordings/{id}/stream` (mock 200) | WORKING (mocked backend) | browser |
| Download recording | downloads `recording-rec-2.wav`; calls `GET /recordings/{id}/download` (mock 200) | WORKING (mocked backend) | browser |
| Rate this conversation good | calls `PUT /calls/{id}/review` (mock 200) | WORKING (mocked backend) | browser |
| Rate this conversation poor | calls `PUT /calls/{id}/review` (mock 200) | WORKING (mocked backend) | browser |
| Record a voice note | page content changes; calls `GET /calls/{id}/feedback` (mock 404) | WORKING (mocked backend) | browser |
| Write feedback | page content changes | WORKING (mocked backend) | browser |
| Play recording | calls `GET /recordings/{id}/stream` (mock 200) | WORKING (mocked backend) | browser |
| Download recording | downloads `recording-rec-3.wav`; calls `GET /recordings/{id}/download` (mock 200) | WORKING (mocked backend) | browser |
| Delete recording | opens a dialog ("Delete recording permanently? The audio will be pe…"); page content changes | WORKING (mocked backend) | browser |
| Rate this conversation good | calls `PUT /calls/{id}/review` (mock 200) | WORKING (mocked backend) | browser |
| Rate this conversation poor | calls `PUT /calls/{id}/review` (mock 200) | WORKING (mocked backend) | browser |
| Record a voice note | page content changes; calls `GET /calls/{id}/feedback` (mock 404) | WORKING (mocked backend) | browser |
| Write feedback | page content changes | WORKING (mocked backend) | browser |

#### `/connectors/`

- Heading: Connectors. Requests on load: 9 distinct.
- Links: 22 anchors, 21 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 29 in DOM, 25 clicked. Disabled (not clicked): "Save settings", "Copy JSON callback URL", "Copy Outbound Message URL". Inputs visible: 6.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Disconnect | opens a dialog ("Disconnect This will stop syncing and revoke acces…"); page content changes | WORKING (mocked backend) | browser |
| Connect | opens a popup window; calls `GET /connectors/{id}/authorize` (mock 200) | WORKING (mocked backend) | browser |
| Reconnect | calls `GET /connectors/{id}/authorize` (mock 200) | WORKING (mocked backend) | browser |
| Disconnect | opens a dialog ("Disconnect This will stop syncing and revoke acces…"); page content changes | WORKING (mocked backend) | browser |
| Reconnect | opens a popup window; calls `GET /connectors/{id}/authorize` (mock 200) | WORKING (mocked backend) | browser |
| Disconnect | opens a dialog ("Disconnect This will stop syncing and revoke acces…"); page content changes | WORKING (mocked backend) | browser |
| Disconnect | opens a dialog ("Disconnect This will stop syncing and revoke acces…"); page content changes | WORKING (mocked backend) | browser |
| Test connection | page content changes; calls `POST /connectors/salesforce/test` (mock 200) | WORKING (mocked backend) | browser |
| Log calls as Salesforce Tasks | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Create Salesforce Leads for unknown callees | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Include inbound calls in Salesforce sync | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Salesforce callback campaign | page content changes; expands/collapses | WORKING (mocked backend) | browser |
| Reveal URLs | calls `GET /connectors/salesforce/webhook-token` (mock 200) | WORKING (mocked backend) | browser |
| Rotate token | page content changes; calls `POST /connectors/salesforce/webhook-token` (mock 200) | WORKING (mocked backend) | browser |
| Setup guide | page content changes | WORKING (mocked backend) | browser |
| Salesforce object to import | expands/collapses | WORKING (mocked backend) | browser |
| Campaign to import into | page content changes; expands/collapses | WORKING (mocked backend) | browser |
| Import now | page content changes; calls `POST /connectors/salesforce/import` (mock 200), `GET /campaigns` (mock 200) | WORKING (mocked backend) | browser |

#### `/connectors/callback/`

- Heading: (none). Requests on load: 4 distinct.
- Links: 1 anchors, 1 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 1 in DOM, 1 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Close window | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |

#### `/connectors/calendar/callback/`

- Heading: (none). Requests on load: 4 distinct.
- Links: 1 anchors, 1 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 1 in DOM, 1 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Close window | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |

#### `/ai-options/`

- Heading: AI Options. Requests on load: 6 distinct.
- Links: 22 anchors, 21 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 25 in DOM, 23 clicked. Disabled (not clicked): "Send". Inputs visible: 10.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Standard Pipeline | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Realtime (speech-to-speech) | page content changes | WORKING (mocked backend) | browser |
| Hide creativity and max length dials | page content changes | WORKING (mocked backend) | browser |
| About Temp | page content changes | WORKING (mocked backend) | browser |
| About Tokens | page content changes | WORKING (mocked backend) | browser |
| Cartesia (2) | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Elevenlabs (1) | page content changes | WORKING (mocked backend) | browser |
| Google (0) | page content changes | WORKING (mocked backend) | browser |
| Deepgram (1) | page content changes | WORKING (mocked backend) | browser |
| Emma - Warm, Professional Warm and professional American fem | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Preview voice | calls `POST /ai-options/voices/preview` (mock 200) | WORKING (mocked backend) | browser |
| Liam - Calm, Confident Calm and confident male voice for out | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Preview voice | calls `POST /ai-options/voices/preview` (mock 200) | WORKING (mocked backend) | browser |
| Preview | calls `POST /ai-options/voices/preview` (mock 200) | WORKING (mocked backend) | browser |
| Run Benchmark | calls `POST /ai-options/benchmark` (mock 200) | WORKING (mocked backend) | browser |
| Save Configuration | opens a dialog ("Apply this voice to campaigns? Use cartesia-voice-…"); page content changes; calls `POST /ai-options/config` (mock 200), `GET /campaigns` (mock 200) | WORKING (mocked backend) | browser |

#### `/security/`

- Heading: Security. Requests on load: 9 distinct.
- Links: 28 anchors, 21 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 18 in DOM, 17 clicked. Inputs visible: 3.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| About Password and account | page content changes | WORKING (mocked backend) | browser |
| Change password | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Add a passkey | page content changes | WORKING (mocked backend) | browser |
| Remove passkey Windows Hello | browser confirm:Remove passkey "Windows Hello"?; page content changes; calls `DELETE /auth/passkeys/{id}` (mock 200) | WORKING (mocked backend) | browser |
| About Two-factor authentication | page content changes | WORKING (mocked backend) | browser |
| Enable two-factor authentication | page content changes; calls `POST /auth/mfa/setup` (mock 200) | WORKING (mocked backend) | browser |
| About Active sessions | page content changes | WORKING (mocked backend) | browser |
| Logout from Safari on iOS | page content changes; calls `DELETE /sessions/{id}` (mock 200) | WORKING (mocked backend) | browser |
| Sign out from all other devices | browser confirm:Are you sure you want to sign out from all other d; page content changes; calls `GET /sessions/active` (mock 200), `DELETE /sessions/{id}` (mock 200) | WORKING (mocked backend) | browser |
| About Data retention and recordings | page content changes | WORKING (mocked backend) | browser |

#### `/billing/plans/`

- Heading: Plans. Requests on load: 8 distinct.
- Links: 24 anchors, 21 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 13 in DOM, 11 clicked. Disabled (not clicked): "Current Plan".

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| (unnamed) | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Downgrade | page content changes; calls `POST /billing/create-checkout-session` (mock 200) | WORKING (mocked backend) | browser |
| Upgrade | page content changes; calls `POST /billing/create-checkout-session` (mock 200) | WORKING (mocked backend) | browser |
| Upgrade | page content changes; calls `POST /billing/create-checkout-session` (mock 200) | WORKING (mocked backend) | browser |

#### `/billing/invoices/`

- Heading: Invoices. Requests on load: 7 distinct.
- Links: 29 anchors, 24 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 11 in DOM, 9 clicked. Disabled (not clicked): "No PDF available (title: No PDF available)".

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Download PDF | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Download PDF | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |

#### `/billing/invoices/inv-1/`

- Heading: Invoice inv-1. Requests on load: 7 distinct.
- Links: 26 anchors, 22 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 10 in DOM, 9 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Print | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Download PDF | opens a popup window | WORKING (mocked backend) | browser |

#### `/settings/`

- Heading: Settings. Requests on load: 6 distinct.
- Links: 22 anchors, 21 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 28 in DOM, 25 clicked. Disabled (not clicked): "Coming soon", "Save changes". Inputs visible: 4.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Enable notification sounds | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Enable success notifications | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| success priority | expands/collapses | WORKING (mocked backend) | browser |
| success routing |  | UNTESTED (click failed in harness) | browser |
| Enable warning notifications | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| warning priority | expands/collapses | WORKING (mocked backend) | browser |
| warning routing | page content changes; expands/collapses | WORKING (mocked backend) | browser |
| Enable error notifications | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| error priority | expands/collapses | WORKING (mocked backend) | browser |
| error routing | page content changes; expands/collapses | WORKING (mocked backend) | browser |
| Enable info notifications | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| info priority | expands/collapses | WORKING (mocked backend) | browser |
| info routing | page content changes; expands/collapses | WORKING (mocked backend) | browser |
| Profile | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Security | page content changes | WORKING (mocked backend) | browser |
| Devices | page content changes; calls `GET /sessions/active` (mock 200) | WORKING (mocked backend) | browser |
| Telephony | page content changes; calls `GET /telephony/providers` (mock 200), `GET /telephony/sip/trunks/pool` (mock 200), `GET /telephony/sip/trunks/pool-assignment` (mock 200) | WORKING (mocked backend) | browser |
| Recording | page content changes; calls `GET /recordings/policy` (mock 200) | WORKING (mocked backend) | browser |

#### `/assistant/`

- Heading: Assistant. Requests on load: 6 distinct.
- Links: 25 anchors, 24 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 8 in DOM, 7 clicked.


#### `/assistant/actions/`

- Heading: Assistant Actions. Requests on load: 10 distinct. **No matching Python backend route:** `GET /assistant/runs`.
- Links: 23 anchors, 22 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 8 in DOM, 7 clicked.


#### `/assistant/meetings/`

- Heading: Meetings. Requests on load: 8 distinct.
- Links: 23 anchors, 22 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 8 in DOM, 7 clicked.


#### `/assistant/reminders/`

- Heading: Reminders. Requests on load: 7 distinct.
- Links: 24 anchors, 23 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 9 in DOM, 8 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Refresh status | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |

#### `/meetings/`

- Heading: Meetings. Requests on load: 8 distinct. **No matching Python backend route:** `GET /calendar/events`.
- Links: 22 anchors, 21 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 15 in DOM, 14 clicked. Inputs visible: 1.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Create meeting | opens a dialog ("Create meeting Schedule a meeting and attach notes…"); page content changes; calls `GET /campaigns` (mock 200), `GET /campaigns/{id}/contacts` (mock 200) | WORKING (mocked backend) | browser |
| Sort key | page content changes; expands/collapses | WORKING (mocked backend) | browser |
| Sort direction | expands/collapses | WORKING (mocked backend) | browser |
| Open details for Demo with Jane Doe | opens a dialog ("Demo with Jane Doe 10/1/2026, 6:38:24 PM – 10/1/20…"); page content changes | WORKING (mocked backend) | browser |
| Open details for Pricing follow-up | opens a dialog ("Pricing follow-up 10/3/2026, 6:38:24 PM – 10/3/202…"); page content changes | WORKING (mocked backend) | browser |
| Open details for Cancelled onboarding | opens a dialog ("Cancelled onboarding 9/25/2026, 6:38:24 PM Cancell…"); page content changes | WORKING (mocked backend) | browser |
| Open details for Dental recall review | opens a dialog ("Dental recall review 9/28/2026, 6:38:24 PM – 9/28/…"); page content changes | WORKING (mocked backend) | browser |

#### `/reminders/`

- Heading: Reminders. Requests on load: 7 distinct.
- Links: 23 anchors, 22 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 9 in DOM, 8 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Refresh status | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |

#### `/notifications/`

- Heading: Notifications. Requests on load: 6 distinct.
- Links: 22 anchors, 21 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 14 in DOM, 13 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Mark all notifications as read | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Clear notification history | page content changes | WORKING (mocked backend) | browser |
| Mark notification as read | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Mark notification as read | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Notification | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Mark notification as read | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |

#### `/email/`

- Heading: Email. Requests on load: 7 distinct.
- Links: 23 anchors, 22 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 9 in DOM, 8 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Refresh status | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |

#### `/reviews/` (landed on `/admin/reviews/`)

- Heading: Agent reviews. Requests on load: 10 distinct.
- Links: 28 anchors, 26 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 18 in DOM, 17 clicked. Inputs visible: 3.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| lead_gen@3 | calls `GET /reviews/summary` (mock 200), `GET /reviews` (mock 200) | WORKING (mocked backend) | browser |
| receptionist@1 | calls `GET /reviews/summary` (mock 200), `GET /reviews` (mock 200) | WORKING (mocked backend) | browser |
| unrecorded | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |
| Interrupted the caller 1 2 avg | calls `GET /reviews` (mock 200) | WORKING (mocked backend) | browser |
| Response too slow 1 2 avg | calls `GET /reviews` (mock 200) | WORKING (mocked backend) | browser |
| Wrong call outcome 1 1 avg | calls `GET /reviews` (mock 200) | WORKING (mocked backend) | browser |
| Incorrect information 1 1 avg | calls `GET /reviews` (mock 200) | WORKING (mocked backend) | browser |
| Repeated itself 1 3 avg | calls `GET /reviews` (mock 200) | WORKING (mocked backend) | browser |
| Good conversation 1 5 avg | calls `GET /reviews` (mock 200) | WORKING (mocked backend) | browser |
| Clear | no navigation, request, dialog or text change within ~1.5 s | UNCONFIRMED (no effect seen by first-pass heuristic; not re-checked) | browser |

### Admin pages (inside Talk-Leee) — every visible, enabled button clicked at 1280x800 (Chromium, mocked backend)

#### `/admin/`

- Heading: Audit & Access. Requests on load: 8 distinct. **No matching Python backend route:** `GET /admin/audit-logs`, `GET /admin/security-events`.
- Links: 22 anchors, 21 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 15 in DOM, 12 clicked. Disabled (not clicked): "Previous", "Next". Inputs visible: 5.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Collapse sidebar | page content changes; expands/collapses | WORKING (mocked backend) | browser |
| Billing & Logs | changes its own state/DOM | RESPONDS-VISUAL (its own state or appearance changes; no request) | browser |
| Security Center | changes its own state/DOM | RESPONDS-VISUAL (its own state or appearance changes; no request) | browser |
| Developer Hub | changes its own state/DOM | RESPONDS-VISUAL (its own state or appearance changes; no request) | browser |
| Logout | navigates to `/`; page content changes; calls `POST /auth/logout` (mock 200) | WORKING (mocked backend) | browser |
| Open notification center | opens a dialog ("Notifications Qualified lead: Jane Doe · +15550001…"); page content changes; expands/collapses | WORKING (mocked backend) | browser |
| Audit Logs | no navigation, request, dialog or text change within ~1.5 s | NO EFFECT OBSERVED (re-checked) | browser |
| Security Events | page content changes | WORKING (mocked backend) | browser |
| Suspensions | page content changes; calls `GET /admin/partners` (mock 200), `GET /admin/tenants` (mock 200) | PARTLY WORKING (UI responds; endpoint not in Python backend) | browser |
| Configuration | page content changes | WORKING (mocked backend) | browser |
| Audit event type | page content changes; expands/collapses | WORKING (mocked backend) | browser |
| Open AI assistant | opens a dialog ("Assistant CONNECTING… Llama 3.3 70B GPT-4.1 mini H…"); page content changes; calls `GET /assistant/model` (mock 200), `GET /assistant/ws-token` (mock 200) | WORKING (mocked backend) | browser |

#### `/admin/audit-logs/`

- Heading: Audit Logs. Requests on load: 7 distinct.
- Links: 23 anchors, 21 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 16 in DOM, 15 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| All | no navigation, request, dialog or text change within ~1.5 s | NO EFFECT OBSERVED (re-checked) | browser |
| authentication | page content changes | WORKING (mocked backend) | browser |
| authorization | page content changes | WORKING (mocked backend) | browser |
| user management | page content changes | WORKING (mocked backend) | browser |
| tenant admin | page content changes | WORKING (mocked backend) | browser |
| security | page content changes | WORKING (mocked backend) | browser |
| data access | page content changes | WORKING (mocked backend) | browser |
| system | page content changes | WORKING (mocked backend) | browser |

#### `/admin/voice-security/`

- Heading: Voice Security. Requests on load: 6 distinct.
- Links: 23 anchors, 21 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 8 in DOM, 7 clicked.


#### `/admin/abuse-detection/`

- Heading: Abuse Detection. Requests on load: 6 distinct.
- Links: 23 anchors, 21 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 8 in DOM, 7 clicked.


#### `/admin/reviews/`

- Heading: Agent reviews. Requests on load: 10 distinct.
- Links: 28 anchors, 26 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 18 in DOM, 17 clicked. Inputs visible: 3.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| lead_gen@3 | calls `GET /reviews/summary` (mock 200), `GET /reviews` (mock 200) | WORKING (mocked backend) | browser |
| receptionist@1 | calls `GET /reviews/summary` (mock 200), `GET /reviews` (mock 200) | WORKING (mocked backend) | browser |
| unrecorded | no navigation, request, dialog or text change within ~1.5 s | NO EFFECT OBSERVED (re-checked) | browser |
| Interrupted the caller 1 2 avg | calls `GET /reviews` (mock 200) | WORKING (mocked backend) | browser |
| Response too slow 1 2 avg | calls `GET /reviews` (mock 200) | WORKING (mocked backend) | browser |
| Wrong call outcome 1 1 avg | calls `GET /reviews` (mock 200) | WORKING (mocked backend) | browser |
| Incorrect information 1 1 avg | calls `GET /reviews` (mock 200) | WORKING (mocked backend) | browser |
| Repeated itself 1 3 avg | calls `GET /reviews` (mock 200) | WORKING (mocked backend) | browser |
| Good conversation 1 5 avg | calls `GET /reviews` (mock 200) | WORKING (mocked backend) | browser |
| Clear | no navigation, request, dialog or text change within ~1.5 s | NO EFFECT OBSERVED (re-checked) | browser |

#### `/admin/api-keys/`

- Heading: API Keys. Requests on load: 6 distinct.
- Links: 23 anchors, 21 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 8 in DOM, 7 clicked.


#### `/admin/webhooks/`

- Heading: Webhooks. Requests on load: 6 distinct.
- Links: 23 anchors, 21 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 8 in DOM, 7 clicked.


#### `/admin/rate-limiting/`

- Heading: Rate Limiting. Requests on load: 6 distinct.
- Links: 23 anchors, 21 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 8 in DOM, 7 clicked.


#### `/admin/secrets/`

- Heading: Secrets Management. Requests on load: 6 distinct.
- Links: 23 anchors, 21 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 8 in DOM, 7 clicked.


#### `/admin/billing/`

- Heading: Partner Billing. Requests on load: 6 distinct.
- Links: 24 anchors, 21 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 8 in DOM, 7 clicked.


#### `/admin/billing/tenants/`

- Heading: Tenant Billing. Requests on load: 6 distinct.
- Links: 26 anchors, 22 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 8 in DOM, 7 clicked.


### White-label pages — every visible, enabled button clicked at 1280x800 (Chromium, mocked backend)

#### `/white-label/dashboard/` (landed on `/auth/login/?next=%2Fwhite-label%2Fdashboard`)

- Heading: Welcome back. Requests on load: 4 distinct.
- Links: 4 anchors, 4 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 2 in DOM, 2 clicked. Inputs visible: 2.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Continue with Password | no navigation, request, dialog or text change within ~1.5 s | NO EFFECT OBSERVED (re-checked) | browser |
| Sign in with Passkey | page content changes | WORKING (mocked backend) | browser |

#### `/white-label/acme/dashboard/`

- Heading: Acme Partner Dashboard. Requests on load: 6 distinct.
- Links: 24 anchors, 24 distinct internal targets; **White Label -> /white-label [404]; Acme -> /white-label/acme [404]**. Checked by HTTP request to each target.
- Buttons: 8 in DOM, 7 clicked.


#### `/white-label/acme/analytics/` (landed on `/auth/login/?next=%2Fwhite-label%2Facme%2Fanalytics`)

- Heading: Welcome back. Requests on load: 4 distinct.
- Links: 4 anchors, 4 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 2 in DOM, 2 clicked. Inputs visible: 2.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Continue with Password | no navigation, request, dialog or text change within ~1.5 s | NO EFFECT OBSERVED (re-checked) | browser |
| Sign in with Passkey | page content changes | WORKING (mocked backend) | browser |

#### `/white-label/acme/billing/` (landed on `/auth/login/?next=%2Fwhite-label%2Facme%2Fbilling`)

- Heading: Welcome back. Requests on load: 4 distinct.
- Links: 4 anchors, 4 distinct internal targets; all returned HTTP 200. Checked by HTTP request to each target.
- Buttons: 2 in DOM, 2 clicked. Inputs visible: 2.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Continue with Password | no navigation, request, dialog or text change within ~1.5 s | NO EFFECT OBSERVED (re-checked) | browser |
| Sign in with Passkey | page content changes | WORKING (mocked backend) | browser |

#### `/white-label/acme/preview/`

- Heading: Acme Preview. Requests on load: 6 distinct.
- Links: 28 anchors, 26 distinct internal targets; **White Label -> /white-label [404]; Acme -> /white-label/acme [404]**. Checked by HTTP request to each target.
- Buttons: 11 in DOM, 10 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| ? | no navigation, request, dialog or text change within ~1.5 s | NO EFFECT OBSERVED (re-checked) | browser |
| Secondary Button |  | UNTESTED (click failed in harness) | browser |
| Outline Button | no navigation, request, dialog or text change within ~1.5 s | NO EFFECT OBSERVED (re-checked) | browser |

#### `/white-label/acme/tenants/`

- Heading: Acme Tenants. Requests on load: 6 distinct.
- Links: 27 anchors, 27 distinct internal targets; **White Label -> /white-label [404]; Acme -> /white-label/acme [404]**. Checked by HTTP request to each target.
- Buttons: 15 in DOM, 14 clicked.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Create Sub-Tenant | opens a dialog ("Create Sub-Tenant All allocations are enforced aga…"); page content changes | WORKING (mocked backend) | browser |
| Edit | opens a dialog ("Edit Tenant All allocations are enforced against p…"); page content changes | WORKING (mocked backend) | browser |
| Suspend | opens a dialog ("Suspend tenant Suspended tenants cannot place call…"); page content changes | WORKING (mocked backend) | browser |
| Edit | opens a dialog ("Edit Tenant All allocations are enforced against p…"); page content changes | WORKING (mocked backend) | browser |
| Suspend | opens a dialog ("Suspend tenant Suspended tenants cannot place call…"); page content changes | WORKING (mocked backend) | browser |
| Edit | opens a dialog ("Edit Tenant All allocations are enforced against p…"); page content changes | WORKING (mocked backend) | browser |
| Resume | changes its own state/DOM and localStorage | RESPONDS-VISUAL (its own state or appearance changes; writes localStorage; no request) | browser |

#### `/white-label/acme/tenants/t1/agent-settings/`

- Heading: Acme Agent Settings. Requests on load: 7 distinct. **No matching Python backend route:** `GET /white-label/partners/{id}/tenants/{id}/agent-settings`.
- Links: 26 anchors, 26 distinct internal targets; **White Label -> /white-label [404]; Acme -> /white-label/acme [404]; T1 -> /white-label/acme/tenants/t1 [404]**. Checked by HTTP request to each target.
- Buttons: 12 in DOM, 9 clicked. Disabled (not clicked): "Reset", "Save Changes". Inputs visible: 3.

| Control | What happened when clicked | Verdict | How checked |
|---|---|---|---|
| Enable call transfer | changes its own state/DOM | RESPONDS-VISUAL (its own state or appearance changes; no request) | browser |
| Run Test | page content changes; calls `POST /assistant/execute` (mock 200) | PARTLY WORKING (UI responds; endpoint not in Python backend) | browser |

## Appendix B. How the mocks were built

- Fixtures were written per endpoint from the frontend's own Zod schemas and TypeScript types, so that pages render populated states (running, paused and draft campaigns; answered and failed calls; connected and expired connectors; an active subscription with invoices; and so on).
- Mutations return fixed answers and do not change the mock data. After a delete or a suspend the refetched list is unchanged. For that reason the checks assert on the request that was sent, not on the list afterwards.
- The mock user was role `admin`, tenant `t1`, partner `acme`. Role `user` was used for the permission checks in sections 5, 6 and 7. The Vite admin panel used `platform_admin`, `tenant_admin` and `user`.
- Some `localStorage` keys were pre-seeded so that browser-only screens had content (notifications, white-label tenants).
