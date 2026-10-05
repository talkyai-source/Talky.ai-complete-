# Legacy cloud callback production boundary

Date: 5 October 2026. Base: `ed53981221a0ec2515c5e349b31a13286fac0496`.
Source: `1bf06a28`, branch `codex/legacy-cloud-production-boundary-20261005`.
This is bounded AG01/AG07 containment of an existing unqualified entry path,
not implementation or acceptance of a new cloud campaign integration.

## Existing ownership gap

The separately enabled Twilio/Vonage answer callbacks are outbound-only. Their
signed destination and media token bind provider call identity but do not carry
the canonical tenant/campaign admission used by the current SIP campaign worker.
The builders look up the destination as a DID and can reach tenantless defaults
when that lookup is unavailable or unresolved. A signed outbound callee number
does not establish the owning campaign tenant. The retained actual-handler
[probe and source inventory](2026-10-05-selected-outbound-warmup-lifecycle.md)
document that gap; no current customer incident or live usage is inferred.

Default-deny flags alone were insufficient: explicitly enabling either legacy
flag also enabled those callbacks in production. The new baseline controls
produced **30 failures and 12 passes**. These include configuration permutations,
actual mounted HTTP/WebSocket entry points and startup collection. They are not
30 unique human scenarios. Synthetic credentials and locally minted valid tokens
were used; no provider, database or external socket service was contacted.

## Bounded change

Both existing `_bridge_enabled` predicates now return false when normalized
`ENVIRONMENT` is `production`, even if their enable flag is true. The same
predicate already protects answer, event and WebSocket handlers. Production
HTTP handlers return the existing unavailable-route 404, and WebSockets close
with 1008 before authentication/session work. This also applies when a router is
mounted without normal application startup.

The existing production-startup gate names an enabled `TWILIO_BRIDGE_ENABLED` or
`VONAGE_BRIDGE_ENABLED` setting and explains that it must be unset/false. Explicit
opt-in remains available in nonproduction for qualification; it still does not
solve the canonical ownership gap. Logs now distinguish disabled/unavailable
bridges instead of claiming the flag must be unset. Supported SIP origination,
provider selection, token validation, tenant lookup and campaign behavior are
unchanged. No new provider, origination flow, bypass flag or ownership framework
was introduced.

## Verification

The nine-module run passed **241 checks, zero failures or skips**, with 12
warnings in 11.49 seconds. It covers the new boundary, existing socket
authentication, production startup/secrets/caller-ID checks, transfer startup
wiring, tenant-profile admission and Twilio session/media controls. Mounted
HTTP checks prove no provider verification is reached; WebSocket checks reject
valid local tokens before the session factory. Existing explicit nonproduction
authentication tests remain green.

Ruff passed the existing CI F selection with `F401,F841` exclusions;
`git diff --check` passed. The independent LLM agent read-reviewed the final
source/tests and found no material defect. It did not rerun the suite or use a
provider/database. Final test-source hashes match `1bf06a28`.

Evidence: [baseline](artifacts/legacy-cloud-production-boundary/initial.txt),
[final output](artifacts/legacy-cloud-production-boundary/focused.txt),
[command/source snapshot](artifacts/legacy-cloud-production-boundary/focused-command.json),
and [source/quality check](artifacts/legacy-cloud-production-boundary/verification.json).

The exact command is retained in the snapshot. The runner used Python 3.12.12,
the existing exact-direct-dependency/test-only Lua overlays, explicit test
credentials and an unavailable unit database. No worktree `.env` or new package
installation was used.

## Rollout and remaining work

Use the existing reviewed deployment freeze/drain procedure. **Do not hot-toggle
this change around active legacy bridge calls.** The gated Vonage event handler
also performs teardown; previously enabled sessions must be drained before
promotion, and unsupported true flags must be removed from the production
configuration. No installed settings, active sessions or deployed version were
inspected or changed here.

This makes the unsafe legacy path unavailable in production. It does not make
Twilio/Vonage campaigns functional, create canonical cloud ownership, establish
development/staging safety for real customers, or close AG01/AG07 or a release
gate. Those capabilities remain explicitly unqualified. The selected supported
offer still requires designated-profile, provider, carrier and operator
acceptance. All commits remain local.
