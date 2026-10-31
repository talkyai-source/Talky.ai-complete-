# CP01 / G01 public claim corrections — 2026-10-04

This is a bounded copy and rendering correction on the integration worktree based on `a63a20b9969a7395c333870bc036efeece86a018`. It does not publish the site, enable a runtime capability, certify regulatory compliance, or establish production readiness. Contact intake and its operator workflow are separate CP01 changes owned by the coordinating agent.

## Before / after inventory

All source paths below are relative to `Talk-Leee/src/`.

| Files | Removed or corrected claim | Replacement |
| --- | --- | --- |
| `app/page.tsx`, `components/home/home-lazy-sections.tsx` | Faster/higher-conversion positioning; `<500ms`, `1000+`, `94%`; blanket secure/compliant answer; working live human transfer | Configured calling, campaign settings and call records; explicitly unavailable live transfer; review access, consent, recording and intended-use requirements |
| `components/home/stats-section.tsx` | 55% higher contact, 40% less handle time, 25% faster resolution, 5,000 businesses | Campaign, calling-window and record capabilities; smaller value typography accommodates words |
| `components/home/features-section.tsx` | 50+ parallel calls without queues; human transfer capability | Configured account/provider limits; requests for team review, with live transfer unavailable |
| `components/home/packages-section.tsx` | 100+ languages, over 100 languages, 30+ languages/hundreds of accents; blanket voice quality/latency and no-wait promises | Voice/model-dependent language options, preview/testing guidance, configured call limits |
| `components/home/cta-section.tsx` | Stay compliant | Configure workflows and review results |
| `components/home/trusted-by-section.tsx` | Fortune 500 wording in the currently unmounted `TrustedBySection` | Industry examples and requirements review; the mounted marquee's plain industry labels remain |
| `app/industries/education/page.tsx` | `<2 sec`, 99.9% uptime, universal instant answers and live department connection | Enquiry intake, configured hours, call records, approved information and requests for staff review |
| `app/industries/financial-services/page.tsx` | 1M+ conversations, `<2 sec`, 99.9% uptime; automatic sensitive/fraud handoff, unlimited conversations and universal instant support | Configured intake/hours/records; request review; existing institution reporting channels for fraud/security; configured limits |
| `app/industries/healthcare/page.tsx` | 100% coverage, 10-second handling, 99.9% accuracy, 500 businesses, `<2 sec`, zero missed calls; HIPAA/BAA blanket statements; emergency/live transfer; unlimited scale and always-available performance | Administrative intake and review; explicitly no emergency response/live clinical transfer; review data handling, intended workflow and agreements; configured hours/limits |
| `app/industries/marketing-automation/page.tsx` | 500+ campaigns, 100K+ conversations, 98% lead response, 99.9% uptime; no missed opportunities/unlimited client accounts | Campaign settings, call records, lead details, configured hours/limits and client-account configuration |
| `app/industries/professional-services/page.tsx` | 50%/40%/30% outcome cards; instant answers, automatic specialist connection and guaranteed response | Enquiry intake, request details and call records; approved answers and team review |
| `app/industries/real-estate/page.tsx` | 100% lead response/focus, zero unnecessary transfers, live handoff, universal instant response | Configured hours, lead details and appointment requests; review captured requests; actions depend on workflow configuration |
| `app/industries/recruitment/page.tsx` | 500K+, 100K+, 98%, 99.9% uptime, unlimited workspaces and live complex-call routing | Candidate enquiries, call records, configured hours, campaign/workspace settings and recruiter review |
| `app/industries/retail-ecommerce/page.tsx` | 1,000 happier customers, instant/no-wait response and human handoff | Configured hours, enquiry intake, records, approved answers and review requests |
| `app/industries/software-tech-support/page.tsx` | 500+ clients, `<2 sec`, instant answers and live specialist escalation | Intake, configured hours, call records, approved troubleshooting information and requests for review |
| `app/industries/travel-industry/page.tsx` | 500K+, `<2 sec`, 99.9% uptime, universal instant booking/response, live staff routing, unlimited properties | Guest enquiries, configured hours, requests, records and workflow settings; recording a request does not confirm a reservation or transport arrangement |

Healthcare additionally removes the two JSX image renderings for `live-call-preview.png` and `smarter-conversations-better-patient-care.png`. The first depicts a completed reschedule and transfer as a live call; the second embeds HIPAA/privacy, scale and customer-outcome assertions. The assets remain in `public/`; no image replacement or redesign is introduced. The text conversation is labelled **Illustrative Request Intake** and does not claim a completed appointment change or live transfer. The retained `request-a-demo.png` is outside those two claim-bearing renderings.

Existing prices/trial offers, integration names, industry labels and unrelated product descriptions have not received a new commercial or connector audit in this slice. No customer-logo relationship is asserted by the industry marquee. This inventory is not a claim that all public content is independently verified.

## Evidence and limits

- `backend/app/domain/services/telephony/inbound_transfer.py:25` declares `CONTROLLED_INBOUND_TRANSFER_RUNTIME_AVAILABLE = False`. The ordinary execution path refuses unavailable transfer; narrowly scoped staging proof settings are not a public availability promise. Capturing a request does not schedule a callback.
- `telephony/docs/phase_3/day10_concurrency_soak_evidence.md:3` explicitly invalidates the old SIP-only, no-RTP concurrency/soak run as release evidence. It cannot substantiate parallel-call performance or uptime marketing.
- `docs/sessions/artifacts/2026-10-01-audio-provider-eval.json:2` limits the two synthetic Cartesia-to-Flux checks to provider adapters/wrappers. There was no telephone, speaker, browser, customer call or failover fault injection. Provider first-audio timing does not measure the caller's full conversational response latency.
- The bounded repository review did not locate an attributable benchmark cohort, customer-count register, signed BAA or compliance evidence package supporting the removed assertions. This is an evidence gap, not proof that every assertion is false.
- Numbers in the production-readiness plan are proposed acceptance targets until the corresponding candidate-bound evidence exists. They are not measured marketing results. No new production checks were performed for this copy change.
- Disclosing an unavailable feature contains a false promise; it does not complete that feature or lift the paid-readiness feature freeze.

## Validation

- `git diff --check` for the 17 owned source files: passed.
- Targeted ESLint on all 17 owned marketing files: **passed again using the isolated exact-lock dependency installation (Next 16.3.8)**. The earlier shared-directory run was preliminary; that directory had installed Next 16.3.3 versus locked 16.3.8.
- Shared whole-frontend TypeScript check: coordinated with the other agent; final candidate-lock result is recorded by the coordinating agent. A duplicate invocation was stopped without changing dependencies.
- Preliminary bounded server-render smoke using that shared dependency directory: **16/16 passed** (ten actual industry pages and six actual homepage components). Actual component arrays, icons, buttons and Next Link/Image rendered. The navigation component and Next dynamic-loading boundary were stubbed; this is not candidate-lock, full Next routing, hydration, browser visual QA or production-build proof. The retained healthcare demo image produced an existing Next `quality=100` configuration warning; it did not fail rendering and was not changed in this slice.
- No static copy assertion tests or source-mirroring tests were added. Temporary editing/render scripts remain ignored under `tmp/`.
