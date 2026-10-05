# Exact calendar receipt observation — bounded AG06 evidence

Source: `2010a1bf10b1305280ba6047099e0af44f3b7147`, based on `e81f525f6c476e74e22b6a41901e1d50c5c184dc`.

The existing Admin action drawer can now explicitly observe an exact saved Google/Outlook calendar event reference through the original saved authorization. It does not resolve the action, infer an active booking, retry a mutation, or change saved status. This completes the bounded calendar observation implementation; AG06 durable operator resolution and live acceptance remain outside this change and remain unfinished.

## Root cause and repair

Neither concrete calendar provider exposed a safe exact-reference read. Google's existing exact GET lived inside update_event, followed by a PUT; Outlook had listing and mutation methods. Time-window listing cannot establish absence of the saved event. The preserved baseline has two failures for absent get_event_reference methods, one per existing provider.

Exactly five production files changed: the two concrete calendar providers, the existing Admin actions endpoint, existing receipt panel and API client types. Exactly two test paths changed. The resolver, meeting service, durable executor, base calendar interface, OAuth scopes, capabilities, schema and existing mutation methods are unchanged.

- Each provider validates a nonempty visible-ASCII ID up to 512 characters, rejects standalone dot/dot-dot, encodes it as one path segment, sends one GET requesting only id, and requires exact returned-ID equality. There is no redirect following, pagination, search, window guessing, mutation, refresh or retry.
- The platform-admin-only route permits book_meeting, update_meeting and cancel_meeting only. It admits a complete authorization_row_v1 proof plus external_event_id co-persisted in one raw output object. Direct or nested provider_result bundles are supported; incomplete, contradictory, cross-tenant, malformed, wrong-action and bulk-shaped evidence is rejected. Input proof may contradict/deny but never supplies missing output proof. There are no meeting or child-action lookups.
- The private extraction helper now serves Gmail and calendar with separate action/provider/reference gates. All prior Gmail guards remain, including the direct/outer action ownership guard, no stitching, canonical UUID proof, optional genuine external identity, recorded-intent comparison and bulk-receipt rejection. Calendar additionally denies event_ids/external_event_ids plural payloads.
- The unchanged read_only resolver selects the exact original account row and only uses a usable unexpired token; no refresh or account substitution occurs. Both stages have timeouts and return only safe outcomes/reference/time.
- observed_event means only that this saved reference was observed. Typed method/provider-matched HTTP404/410 becomes not_observed with absence_is_inconclusive. Missing proof, unavailable authorization, denied/rate-limited/failed/malformed/mismatched reads become unavailable. Saved outcome and confirmation never change.
- The existing panel uses explicit click, duplicate-click suppression and generation/selection guards including action, type, status, account and event reference. It checks outcome/reason consistency, a nonempty matching returned string ID, and a bounded parseable timestamp. Mixed response fields cannot substitute the displayed reference. No private event body, attendees, diagnostic, token or subject is forwarded.

## Actual action evidence coverage

MeetingService persists a proof bundle for book_meeting; the returned external ID joins it only after provider acknowledgement. A create whose provider never returns an ID remains unavailable. Update/cancel initialize their receipt with the known event ID and proof, and preserve that bundle on confirmed or unconfirmed saved output. A running record with no co-persisted output proof/ID stays unavailable. No historical backfill or linkage inference was added.

The new route/resolver/provider tests use synthetic saved rows matching these formats. Existing meeting-service controls and voice-to-Gmail durable handoff controls are included in the affected regression. This is not a new actual calendar write-to-inspection handoff execution or real-SQL execution of calendar service-to-receipt storage, nor a live provider-state qualification.

## Validation and preserved failures

- Final backend: **296 passed, 0 failed, 0 skipped, 1 warning**, seven modules, including **87 new calendar controls**. The warning is an existing datetime.utcnow deprecation in Admin health. The first focused calendar/Gmail run was 151 passed before two plural-ID controls were added; counts overlap.
- Final Admin: **99 passed, 0 failed, 0 skipped**, including **58 receipt-module controls**, of which **31 were added here**. The earlier 48-control focused log and 95-test pre-reference-guard log are retained; counts overlap.
- Final TypeScript, scoped ESLint, Vite production build, staged diff check and repository CI F rule with F401/F841 exclusions passed. Strict F-only lint failed on the two pre-existing typing.Any imports. Separate baseline-source lint logs reproduce those same two findings; they were intentionally left unchanged.
- Initial harness execution failed because the test constructed the connectors without their required connector_id, and its socket guard outlived the async test loop and blocked Windows cleanup self-pipes. That log is preserved as baseline-initial-harness.txt. Constructor arguments, awaited token setup and guard restoration were corrected before the real missing-method baseline. These are test setup failures, not additional production defects.
- A preceding path setup attempt failed redirection before pytest launched; commands.json records this explicitly.
- Independent draft review caught mixed Gmail/calendar response-field rendering; root review added malformed timestamp and missing-reference controls. Final source and tests incorporate those fixes. Root and independent account_review source reviews are clear, with no reviewer test execution or edits.

commands.json records exact executable/argv/cwd, explicit Python environment, logs and child-exit capture limits. source-hashes.json freezes seven source/test and five unchanged dependency hashes. record_evidence.py validates those frozen hashes against committed Git bytes and records LF-normalized artifact hashes in manifest.json. No app/test edits or runtime reruns followed final reviews except the requested reviewed UI validation controls and their final checks.

## Official contract references

Google supports an exact events.get with primary calendar and the already-requested calendar/calendar.events scopes. [Google events.get](https://developers.google.com/workspace/calendar/api/v3/reference/events/get). Its fields parameter can restrict response data to id. [Google partial responses](https://developers.google.com/workspace/calendar/api/guides/performance).

Google may retain cancelled/deleted tombstones with only an ID; therefore a found reference is not proof of an active booking or successful mutation. [Google event resource](https://developers.google.com/workspace/calendar/api/v3/reference/events). A 404 can also reflect unavailable access. [Google errors](https://developers.google.com/workspace/calendar/api/guides/errors). Treating an exact-read 410 as inconclusive missing/gone is a conservative observation policy, not a claim that this response proves cancellation or that Google documents 410 specifically for this get case.

Microsoft supports GET /me/calendar/events/{id} with $select. [Graph get event](https://learn.microsoft.com/en-us/graph/api/event-get?view=graph-rest-1.0). The existing Calendars.ReadWrite scope includes event reads. [Microsoft permission reference](https://learn.microsoft.com/en-us/graph/permissions-reference#calendarsreadwrite). Outlook IDs can change when events move; no retrospective immutable-ID header or replacement-ID search was added. [Microsoft event resource](https://learn.microsoft.com/en-us/graph/api/resources/event?view=graph-rest-1.0). Missing/gone and other HTTP errors remain observations or unavailable states, never mutation success. [Graph error responses](https://learn.microsoft.com/en-us/graph/errors).

## Limits

New backend controls use actual routes/resolver/concrete HTTP method code with synthetic query/HTTP ports and a socket guard during each new test. Existing tests keep their existing synthetic boundaries; no global network-attempt count is claimed. Admin tests use the actual components/API with the existing synthetic hook scheduler, not browser/React-concurrency acceptance. Python dependencies use the documented existing overlays, Admin dependencies an ignored local junction. No actual SQL, live OAuth account/provider, browser, push or deployment was used. Paid-production readiness, operator resolution, historical proof repair and live acceptance are not established by these checks.
