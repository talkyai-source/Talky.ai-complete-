# Paid readiness follow-up — 28 October 2026

The user requested completion for paid use with final Stripe account integration
as the only intended handover item. Required non-Stripe work is resumed,
including the earlier CP05/CP06 deferrals. This instruction does not turn missing
operating evidence into a pass or silently exclude an assessed feature.

The user subsequently accepted the recommended first paid-release scope:
reseller operations and staff invitations are excluded from this launch and
retained as future work. Cosmetic branding previews remain available. This is
an explicit launch-scope revision; the excluded capabilities are not recorded
as built or feature-complete. [CP05 scope and interface evidence](2026-10-28-cp05-reseller-containment.md)
records the affected routes and their access checks.

The starting tested candidate was `64621dd6f470c1162986fb05a2ca17adf07e7921` in
[draft PR 26](https://github.com/talkyai-source/Talky.ai-complete-/pull/26).
During this work the parallel [Admin coordinated release](2026-10-28-admin-live-release.md)
deployed backend/gateway `10a8b9f0`, database head `0081_admin_payment_merge`,
and both hosted frontends. Fresh read-only inspection corroborated the backend
source, health and gateway identity. The five earlier hosted Admin read failures
passed that release's QA. This newer live deployment remains separate from the
candidate here. Its version-2 sole-owner authorization is valid for its recorded
release; a new release still requires its own fresh candidate-bound freeze,
drain, backup and single-use manifest. This work has not changed production
services, schema, calls, payments or email delivery.

## Implemented changes awaiting combined acceptance

- [Separate runtime grants](../../scripts/database/RUNTIME_GRANTS.md) validate
  the real migration-owner and runtime TLS logins against the canonical 0082
  profile, preserve RLS, revoke elevated/effective privileges and keep new
  objects inaccessible until review. The backend remains a trusted client;
  its existing application context is not a sandbox for arbitrary tenant SQL.
- [First administrator](../operations/FIRST-ADMIN.md) requires private password
  input, independently verified mailbox ownership and a proved authenticator.
  Account, MFA and audit creation are atomic. Existing accounts/admins are
  refused rather than promoted or reset. Ordinary password/MFA login is tested
  separately from provisioning.
- Fresh-host systemd setup keeps API/workers under `talky` and privileged
  operator services under root, with a separate root-owned migration file.
  The trunk loop replaces its obsolete timer; guarded Asterisk reconciliation
  is scheduled. Legacy backup, synthetic-call and destructive-cleanup schedules
  are not automatically activated. TOTP encryption input is validated before
  host changes. Existing encryption keys must be retained on restore.
- [CP06 billing recipients](2026-10-28-cp06-billing-recipient.md) use the one
  eligible current verified tenant administrator. Missing or ambiguous
  recipients remain explicit, and replay preserves the original delivery
  identity and financial outcome. No live email/payment outcome is inferred.

Offline checks are bounded: 51 installer tests, 14 actual Ubuntu shell/environment
regressions, nine runtime-grants boundary
tests, 25 bootstrap tests with one Windows-only permissions skip, and ten
existing billing unit tests passed. Nineteen new billing PostgreSQL cases were
collected within the existing 45-case integration module. These counts overlap
other suites and are not a whole-product readiness score. Native grants,
bootstrap/authentication and billing checks require their actual CI results.
The first native grants run successfully exercised ordinary password/MFA login,
then exposed ownership-validation ordering in a negative test. Validation now
precedes table locks and is repeated after acquiring them. The billing run
exposed a test fixture using a nonexistent role; it now uses the canonical
`user` role. Combined native acceptance remains required after these fixes.

## Actual blank-host rehearsal

The new [Ubuntu VM workflow](../../.github/workflows/ubuntu-blank-host.yml)
uses a checksum-verified official Ubuntu 24.04 cloud image, a newly owned QEMU
overlay with KVM, pinned SSH host identity, generated credentials and isolated
PostgreSQL containers on the runner. It executes the actual installer including
both frontend builds, verifies service identities and HTTPS, reboots the guest,
requires a changed kernel boot ID and repeats checks. Resource cleanup must
succeed before the receipt can pass. Sanitized failure diagnostics are retained.

The first run on `6f31fc21` reached installed OS/Python packages, gateway and
frontend builds, then failed at a service-state check. The
[failed run](https://github.com/talkyai-source/Talky.ai-complete-/actions/runs/40649554742)
and receipt are retained; owned resources were cleaned. No exact failing unit
could be inferred from that first diagnostic. Follow-up code identifies only
approved systemctl unit arguments and captures sanitized synthetic guest logs.

The second run on `ccd45f10` identified Asterisk startup failure and database
connection failures after Docker created an overlapping private subnet. The
fixture now uses Ubuntu's actual amd64 Asterisk module directory. The fresh-only
Redis setup creates no Docker bridge and binds authenticated Redis to host
loopback. The rehearsal checks the external database route before installation,
after installation and after reboot. Existing Docker installations are refused.
Both reconciliation entry points now parse literal dotenv values without shell
evaluation, preserving TLS query parameters containing `&`. Managed inbound
dialplan inclusion is required before installation. These repairs have focused
tests; only a successful complete VM run can qualify installation and reboot.

## Engineering and database review

Separate engineering and database review examined the installer, restricted
runtime grants, first-admin transaction, recovery guards and current deployment
protocol. Reviews do not substitute for operating evidence. The live runtime
role still owns tables and has `CREATEROLE`/`CREATEDB`; its same-host database
does not use TLS. The new profile's migration/runtime separation must be applied
and verified on the designated production topology before closing that work.

The architecture keeps provider interfaces and independently supervised workers,
and preserves a separate migration identity and fail-closed object grants. These
boundaries support later worker and database scaling. Current call state remains
process-local, authentication limits are per-process, and protected writes use
global audit/authority locks. Multiple API processes, independent call workers,
read replicas and capacity claims need their own state/coordination design and
load evidence. Logical backup success is not off-host recovery or a measured
RPO/RTO. No unlimited or measured production capacity is claimed here.

This exercise uses disabled calls/billing and synthetic provider credentials.
Its database and application VM share the CI runner. Even a successful install
and reboot therefore do not prove public DNS, real provider/caller experience,
independent engineer handover or off-host disaster recovery.

## Remaining execution inputs

The user has already authorized deployment work. These are factual missing
inputs, not another request to authorize the same work:

- Secure privileged-access reference for the live host, designated clean
  acceptance host and separate PostgreSQL destination.
- Owned telephone destinations, actual test accounts/profile, approved alert
  receiver and operating window for call, onboarding, failure and monitoring
  acceptance. No email, message or call is sent to an undesignated recipient.
- The actual release freeze/drain record, compatible recovery and the required
  independent operating review. Idle services cannot establish a traffic freeze.
- The accepted first-release exclusion of reseller operations and staff
  invitations must be reflected in the deployed surfaces and handover. Their
  missing partner authorization/billing and invitation contracts remain future
  implementation work, outside this paid-launch acceptance scope.

The complete fresh-host update/rollback path also remains unqualified. The
legacy updater assumes the `admins` profile, API-web and different scheduling;
changing one filesystem group would not qualify a Linux frontend/backend update.
The original document's real restore, handover and three walkthrough videos,
and the plan's real conversation, connector, capacity and purchased-journey
acceptance retain their separate evidence requirements. The feature freeze
remains active; no final gate is closed by this checkpoint.
