# CP07 notification isolation and delivery truth

Work started 4 October 2026 and continued 5 October 2026. Base: `8f11f335e21bd29731e880ef0116472acc41f59d` on `codex/production-ready-20261004`, in the existing isolated worktree. CP05 and CP06 remain deferred at the user's request. No unrelated features were added.

Implementation candidate: `40232996372305b8ab100bc8fc57a3957296f4e4`. Local checks passed against this unchanged candidate; release and external-delivery acceptance remain open. **CP07 remains unfinished:** reliable external background alerts do not have a suitable existing authorized delivery contract. The browser-send option is unavailable; this containment does not count as completing that feature or lifting the production-readiness freeze.

## Reproduced problem and root causes

A deterministic synthetic test populated legacy account-A lead history and a destination, then authenticated account B through the actual AuthProvider. The old notification view still displayed A's private lead title. The preserved failing test proves the code defect with fixtures; it does not establish a real customer disclosure.

| Problem | Repair and boundary |
|---|---|
| Shared localStorage keys and a singleton hydrated once for every account | Notification history/settings now use a verified tenant/user scope. Unknown legacy history and destinations are discarded rather than assigned to the next account. Missing verified tenant identity keeps notifications inactive. |
| In-memory history, toast callbacks and old requests survived identity changes | Notification operations capture their originating scope and generation. Logout/account changes invalidate old work; current-account refresh preserves ordinary verified identity. Authentication verification and cross-tab session changes fence notification state. Cookie-only sessions publish secret-free change/logout markers through storage and BroadcastChannel with nonce deduplication. Notifications remain inactive if neither synchronization transport is available; ordinary authentication is unchanged. |
| Merely binding a React callback did not protect in-flight mutations | TanStack can replace callbacks while a request is running. A small wrapper captures the stable mutation execution context before asynchronous preparation, preserves rollback context, and rejects stale completion callbacks. Global query errors capture identity at fetch start. |
| Lead events and deduplication IDs were shared across accounts | Event queries and saved seen IDs are scoped; an identity change remounts the observer. The request is aborted/fenced across an account change and checked again after completion. The server stamps each event response with its authenticated tenant and user; the client rejects missing/mismatched ownership before caching even if another tab’s cookie changes before its identity marker arrives. First observation seeds history silently, including an empty initial stream; subsequent fresh events alert normally. |
| Connector popup/storage messages could carry unattributed notification text | Those broadcasts now request an authoritative status refresh without publishing their unverified text as a success/error notification. This does not claim completion of the wider connector/session review. |
| Saved browser webhook settings could export lead details and ignored send failures | External routing choices and browser sends are removed; restored settings cannot reactivate them or retain a destination. The screen explains that live lead alerts require an open, visible dashboard. Existing in-app alerts remain available. |
| An unmounted backend webhook test handler invented a successful delivery ID | An owned configuration returns structured 501 unavailable/no-send; foreign/missing configurations remain 404 and storage failures return sanitized 503. The router remains unmounted. No sender, worker, new privilege or outbound request was added. |

History parsing validates saved records, bounds retention and record count, and tolerates unavailable browser storage. Scoped storage updates and reads before mutation prevent sequential stale-tab writes from restoring cleared history or old privacy settings, including storage-quota recovery. A scoped nonce also signals Clear history when persistence is off and identical/absent-key writes would otherwise produce no browser storage event. Cross-tab clear requires writable storage; authentication has its separate BroadcastChannel fallback. This is not a transactional multi-tab event queue or a guarantee against simultaneous writes/double popups. Browser storage is still local storage, not an authoritative lead or delivery ledger.

## External delivery remains blocked / not done

The [delivery-contract review](2026-10-04-cp07-delivery-contracts.md) records the exact existing paths and missing guarantees. Internal stream events are best-effort activity records. Webhook endpoint/history tables have no registered delivery worker. Existing billing-email and CRM receipts are bound to their own recipients, provider identities and authorization; they cannot safely be repurposed for arbitrary alert destinations.

Completing the existing external alert promise requires a bounded, reviewed destination/event authorization contract, immutable event/payload/destination identity, safe HTTP egress, accepted/failed/unknown receipts and receiver-specific retry/reconciliation rules. No arbitrary customer URL was moved to a server fetch. No delivery or bank/inbox receipt is inferred from a local toast, navigation, configuration row or synthetic test.

## Verification

Final commands, candidate commit, test results and artifact digests are recorded in [the CP07 verification manifest](artifacts/cp07/verification.json). Tests use synthetic identities, storage, deferred responses and provider results. Local Chromium checks render the real notification components and store with synthetic identity controls. Authentication lifecycle tests use the actual AuthProvider and bridge with synthetic `/me` responses and an in-process BroadcastChannel-compatible test transport. Neither is deployed authenticated-account acceptance or evidence of background delivery.

| Check | Observed result |
|---|---|
| Focused frontend and adjacent regression tests, 17 modules | 140 passed, 0 failed, 0 skipped |
| Backend response ownership, unavailable webhook delivery and tenant isolation, 4 modules | 45 passed; existing dependency deprecation warnings only |
| Changed/new frontend ESLint; backend Ruff F/E9 | Passed |
| Full frontend TypeScript check and Next production build | Passed; existing custom Cache-Control warning retained |
| Real Chromium two-tab fixture | 12 states passed; no outbound fetch attempts; four screenshots inspected |
| Candidate integrity | All 29 changed source/test files unchanged during final checks; source commit pinned |

The browser replay first reproduced a same-account clear-history failure when identical settings/absent-history writes emitted no storage event. The final scoped clear marker fixes that case; failing and passing evidence are retained. Earlier exploratory logs are not final candidate acceptance. The first broad test run also caught a profile refresh started after logout; its admission fence is covered by the final auth tests. Test-fixture type errors found during intermediate validation were corrected before the passing final build.

The test browser and loopback server were stopped. Automatic approval review rejected deletion of the ignored temporary test directory with `blocked by policy`; that inactive directory is retained. No destructive cleanup retry was made.

Independent responsibilities: realtime agent owns scoped store/authentication lifecycle; LLM agent owns mutation/query attribution and consumer review; audio agent owns delivery-contract/backend review and browser checks; root owns event-stream/lead integration, direct UI consumers, final validation and documentation.

## Remaining acceptance and adjacent identity work

- Review the external-alert completion contract and designated receiver behavior. Until that is implemented and evidenced, closed-browser delivery, receiver acknowledgment, network-failure/restart recovery and safe egress are unproved; external routing stays unavailable.
- Deploy the additive authenticated `/events` response fields with the backend before (or together with) the new frontend. The new frontend deliberately rejects older unstamped event responses; the older frontend tolerates additive fields. Verify the coordinated candidate in the designated environment, including actual cookie/bearer account transitions, role/tenant identity, logout and another browser tab. Refresh already-open dashboard tabs so old bundles cannot retain the removed browser sender in memory. Local component and synthetic browser tests do not certify production deployment.
- CP06 billing recipients remain deferred. CP08 still owns the wider identity journey: existing feature query caches and email-audit history have independent scoping gaps, and shared HTTP stale-401 redirect/token behavior requires its own review. CP07 does not claim that all client-side business data is isolated by repairing notifications.
- Retain authoritative lead, action, billing and server event records. Do not restore global legacy notification keys or the silent browser webhook sender during rollback. Use a compatible scoped client or keep affected notification surfaces unavailable.

The original checkout's application changes are preserved. No real email, webhook, customer call, production mutation, push or deployment was performed.
