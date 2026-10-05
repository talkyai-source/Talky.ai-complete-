# AG06 Admin saved-receipt presentation

Source candidate: `deec6bbd`, based on `a42f2700`. This is a bounded presentation and existing read-response correction. AG06 operator resolution and external-account acceptance remain open.

## Observed defects and repair

- Admin displayed `unknown` and `scheduled` as Pending, and advertised safe Email/SMS/Reminder retries even though the backend disables generic retry. The table/detail now preserve both statuses, expose their existing filters, and explain that uncertain effects stay held. Unrecognized future statuses are not relabelled Pending.
- The detail endpoint now adds `saved_receipt` using the existing `public_action_receipt` projection after its existing tenant filter. The drawer replaces raw input/output viewers with allowlisted stored references and explicit acknowledgement proof. Missing or mismatched receipts remain unverified; current connector display names are separately labelled and never substituted for original account identity.
- The old drawer allowed late A detail or cancellation-refresh responses to replace B, while confirmation state survived the selection change. Three controls against the actual baseline drawer reproduced those failures. Selection lifetime now fences reads, errors, cancellation refresh and old confirmations. An API error remains an error rather than triggering a success refresh.

The new receipt display requires the matching action ID/status, `success=true`, `confirmation_allowed=true`, and completed/scheduled status before saying "Acknowledgement recorded". It explicitly states that saved evidence does not verify current provider state or recipient delivery. A remote reference alone is not confirmation.

## Validation

- Initial backend run: **6 failed, 69 passed**. The six new projection/proof cases failed because the response had no `saved_receipt`; both new foreign-tenant cases already passed.
- Initial Admin run: **5 failed**. A separate precise baseline-drawer run reproduced **3 selection/confirmation failures** using the final race assertions, then restored the repaired file in `finally`.
- Final backend: **75 passed, 0 skipped**, including 8 new ASGI projection cases and existing Admin/dashboard receipt controls. Dependencies used the declared-requirements overlay first, then the existing OP02 Lua test overlay. The preserved launcher blocks network/DNS while permitting Windows asyncio's internal socketpair only. No provider or database was contacted.
- Final full Admin suite: **34 passed, 0 skipped**, including 11 new actual-component/effect controls. TypeScript app and node projects, changed-file ESLint, CI Ruff and Vite production build passed. `git diff --check` passed after preserving existing line endings on unchanged lines.
- Independent read-only review by the audio audit agent found no additional material defect. It did not independently rerun tests or use PostgreSQL.

Exact commands, module/file inventory, source hashes, observation hashes and limitations are in [verification.json](artifacts/ag06-admin-receipts/verification.json). Logs preserve the red observations as well as final results. [run_backend.py](artifacts/ag06-admin-receipts/run_backend.py) reproduces the bounded backend command from this checkout with the documented dependency overlay.

The frontend controls execute actual component/effect code with the existing synthetic hook-scheduler style and server rendering. They do not establish browser accessibility, React concurrency or live deployment acceptance. The backend tests mount the actual detail route and response model with an overridden synthetic authenticated admin and tenant-filtered storage; they do not newly establish live authentication or RLS behavior.

## Contract and remaining boundaries

- Existing Admin role/tenant policy, cancellation/retry endpoints and provider effect behavior are unchanged. Tenant and partner admins keep their existing tenant predicate; platform-admin access remains explicit.
- The detail response addition is backward compatible. Existing authorized raw `input_data`/`output_data` API fields remain available under that existing contract; this slice removes their drawer rendering, not their server contract. It is not a claim that the entire legacy response is redacted.
- There is no provider inspection endpoint, automatic retry, resolution mutation, new audit primitive or new permission. Missing legacy original account/reference cannot be inferred from today's connected account.
- An interrupted in-flight cancellation may already have reached the backend. The selection fence prevents misattributed UI updates; it cannot undo that request or its effect.
- Resolving an unknown external effect still requires the original account and authoritative evidence, designated operator authority and an approved durable audit/decision contract. Empty search, timeout or missing local receipt is not proof of nonexecution. This correction does not close AG06 or lift the feature freeze.
