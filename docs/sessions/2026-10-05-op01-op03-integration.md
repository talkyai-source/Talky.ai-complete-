# OP01–OP03 integrated local verification — 5 October 2026

The allowance, provider-admission and media-ownership repairs are committed locally on the isolated production-readiness branch. They preserve CP08 identity/credential guarantees and the existing billing, telephony and Realtime boundaries. The fixed plan remains unfinished; nothing was pushed or deployed.

## What changed and why

**OP01:** A failed usage query previously could look like unlimited allowance or fall back to stale stored usage. One canonical result now distinguishes known, verified unlimited and unavailable values. Worker and final admission defer the same job/attempt when usage is unavailable. Auth, dashboard and billing display the same state without fabricated zero/unlimited values. Tenant-scoped reads prevent combining one tenant's entitlement with another tenant's hidden usage. Password login reads metering in a rollbackable savepoint on its existing connection; MFA reads after its credential transaction releases its connection. Metering failure cannot undo a valid login, and signing/refresh failures still roll back credential issuance. Existing calendar-month, rounding and admission policy were preserved; strict active-call caps and reset semantics are not inferred.

**OP02:** Flux prewarm and cold streams acquire the existing provider slot at the actual connection boundary and transfer/release ownership once. Cancellation and late completion cannot resurrect a cleaned-up stream. Existing outbound global voice admission fails closed. Atomic Redis scripts and owner-specific failure cleanup preserve a prior valid lease when an idempotent acquisition fails. Refresh/release serialization prevents the reproduced local stale-refresh resurrection. Global diagnostic APIs cannot mutate leases; tenant lease and hangup boundaries retain current ownership checks. Provider slots remain process-local and require topology/account-budget validation. Outbound global admission remains post-answer; no pre-ring cap is claimed.

**OP03:** Closing forwarded STT iterators now closes the owned provider stream. Playback completion, interruption, clear and delayed sends remain attached to their originating utterance. A late completion cannot complete a newer reply, and dropped audio cannot yield a successful completion receipt. Unknown/partial transport delivery remains unknown/partial. These are transport ownership guarantees, not proof that the listener heard or understood the agent.

## Verification

The exact commands, source hashes, commit identities, intermediate failures and limitations are in the [integrated manifest](artifacts/op01-03-integration/verification.json).

| Check | Observed result |
|---|---|
| Combined backend contracts, 240 modules | 3,704 passed; nine skipped; 2,798 warnings retained |
| Skip boundaries | Eight standalone Redis variants unavailable locally; corresponding Lua-script cases ran. One Unix permission assertion unavailable on Windows |
| Disposable PostgreSQL, 13 modules | 179 passed, zero skipped; 154 actual public-schema cases and 25 isolated-schema cases |
| CP08/OP01/OP04 frontend union, 25 modules | 176 passed, zero skipped |
| Two updated auth fixture modules | Nine passed, overlapping the union above |
| Production frontend build, post-build TypeScript, scoped ESLint, OpenAPI check | Passed |
| Changed Python files, existing CI Ruff selection | Passed |

The public-schema database remained at `0059_auth_identity_contract`. The isolated action schema's seven cases are not evidence of restricted-role RLS. Trunk fixture audits and their referenced synthetic parents remain retained because the real audit immutability contract prohibits deleting them. No production/customer data was used.

The first frontend build exposed two test fixtures missing `minutes_state`. The fixtures now explicitly use `known`; no production type was weakened. The failed build log is retained. A test-wrapper console encoding failure occurred after all 176 frontend tests had passed; its recorded subprocess exit code and source-integrity record distinguish that printing failure from a test failure.

The CI workflow now includes 12 newly verified database modules and the actual offline conversation path. Explicit disposable database environment variables prevent CRM/action fixtures silently skipping. The existing Redis CI step will exercise standalone Redis when the workflow runs. YAML/reference checks passed locally; no remote CI result is claimed. Independent review identified a separate minimal-schema fixture update needed before the later OP06 integration; it is not an OP01–OP03 failure.

## Remaining acceptance

OP01 still needs the billing owner's reset/rounding and active-call exhaustion contract, plus approved paid-outcome acceptance. OP02 needs account/topology budgets, standalone Redis deployment validation and selected-profile overload/resource-cleanup evidence. OP03 needs permitted audio, human listening, actual interruption timing and provider/carrier failure acceptance. Their package statuses remain `in_progress`.

All 15 final gates and 22 scenario gates remain `not_run`. The three deferred packages CP05, CP06 and CP09 remain deferred. AG03 remains candidate verified; this report does not turn local tests into a production-readiness or acoustic-quality claim.

## Rollback

Local implementation commits are listed in the manifest. This slice has no migration or new ledger. Review rollback compatibility across the nullable allowance DTO and its frontend consumer before restoring either component. Stop new affected calls rather than treating an unavailable meter as unlimited, releasing another call's lease, replaying uncertain jobs or fabricating audio completion. Keep durable prior evidence and receipts.
