# Fresh Ubuntu release and recovery acceptance — 28 October 2026

Status: candidate `0a9f4ee3a26f35ab1a1a5e20fd3af07b76bdd788` passed the actual blank installation/reboot job in [run 40685595900](https://github.com/talkyai-source/Talky.ai-complete-/actions/runs/40685595900); the separate update/recovery job failed at Asterisk runtime reconciliation. The failure remains retained below and requires repair and another real VM run. No live backend or production database is changed by this qualification. The independently released customer dashboard is documented in [the customer release record](2026-10-28-customer-first-paid-release-scope.md).

## Purpose and supported topology

The fresh installer creates the Ubuntu 24.04 amd64 `talky` service profile, with Linux-hosted frontends and an external TLS PostgreSQL database. The existing legacy deployer assumes a different service owner and deployment topology. Reusing it unchanged would not qualify updates to a newly installed server.

The new [fresh-release procedure](../../scripts/installation/FRESH-RELEASE.md) stages a clean, exact descendant commit and pinned build artifacts. It keeps the current host networking, certificates, provider setup and application data. Activation requires a fresh candidate-bound release manifest, frozen producers, measured zero drain counters, a verified backup and compatible database identities. Configuration, encryption keys, release artifacts and persistent data identity are checked before activation, recovery and reopening. Staged Node builds use a separate unprivileged build identity; privileged operations use an isolated operator environment.

The release journal records the actual original database name, endpoint fingerprint and schema before runtime shutdown. The backup, migration connection and activated runtime must agree with it. Runtime and migration credentials have separate roles, and explicit absolute protected passfiles are required. A migration-start flag is durable before Alembic runs. Once migration begins, code rollback is refused even if a failed transaction leaves the old schema head visible. Recovery after a migration requires a reviewed forward repair or separate compatible restore.

## Review and native evidence

Separate engineering and database reviews accepted updater SHA256 `97c3b3e8e49f5ecc6f36e390eb262af43a116d3094f236e57ac46988dc994011`. Review corrected transient secret permissions, unbound build artifacts, maintenance-job races, database URL overrides, configuration drift and privileged environment inheritance before qualification. The guest helper was independently checked against the final passfile and recovery contracts.

The native Linux root suite passed 33 tests without skips. It exercises actual filesystem permissions, Git changes and release links in owned temporary fixtures, with systemd and database boundaries mocked. The nonroot run executed 12 tests and explicitly skipped 21 root-permission cases. These counts do not substitute for the real VM exercise.

All five [supporting workflows](artifacts/ubuntu-update-20261028/supporting-ci-0a9f4ee3.json) passed on this candidate, including full CI, voice, installer, TLS recovery and monitoring. The main backend suite reported 15,728 passed, 242 skips and 72.60% coverage. The separate migrated PostgreSQL groups reported 503 inbound/billing, 701 Admin and 68 bootstrap/adoption passing tests without skips. The retained receipt distinguishes overlapping suites, permission skips and audit scope.

## Actual VM acceptance contract

The passing [installation receipt](artifacts/ubuntu-update-20261028/install-passed-6a56921d.json) records 12:10:53–12:18:43 UTC, all 12 expected services/timers before and after reboot, verified application identities, actual API/gateway source, HTTPS, preserved database routing and loopback Redis. Cleanup completed. Its SHA256 is `f2ce334fb18c409cadcca89ab69bf250f98c6e0c76fec7f1fd0663ad1c52534e`. The tested PR merge `6a56921d5e44f52fc6cb7904d497239ecacaddee` and candidate have identical tree `3efb416f79be6d647ebce9d15a029937b2479601`.

The [failed update receipt](artifacts/ubuntu-update-20261028/update-failed-6a56921d.json) proves the original installation/reboot and the preactivation artifact-tampering and activating-oneshot refusals. Activation and attempted recovery then failed because the shared carrier endpoint did not satisfy the managed inbound-context verification. Runtime remained stopped and traffic frozen; owned VM/database cleanup completed. No update, rollback reboot or migration-refusal pass is claimed from this run. Its logs also show one synthetic-key Groq warmup attempt returning HTTP 401; no real provider credential or customer call was used. Subsequent fixture isolation must prevent those startup attempts reaching provider hosts.

The [native Asterisk reproduction](artifacts/ubuntu-update-20261028/asterisk-emptyglob-proof.json) confirmed the cause using actual Asterisk 20.6 in a private network namespace and separate temporary configuration/control socket. A mandatory `#include pjsip.d/*.conf` with no matching files prevented endpoint loading; changing only to `#tryinclude` loaded the expected managed context. Existing services/configuration remained untouched and owned processes/files were cleaned up. Its SHA256 is `03cf598bd894ab95e545d02c046fa86007ae3389db6d7b4648f688ba3e907c44`. The original fresh-install pass checked service activity but not this endpoint; that omission is now explicit, and the next VM verifies the actual endpoint/context before and after reboot. The production updater's runtime verification is preserved.

The correction requires the exact optional managed PJSIP include and makes the fresh installer synchronously run the existing guarded Asterisk reconciliation after Redis/Asterisk start, before starting application workers or enabling the reconciliation timer. Its digest, writer lock, active-channel check and runtime/rollback checks are unchanged. Native installer validation and startup-order/failure checks passed all 58 cases; 15 preparation cases also passed. The fixture now confines its three configured AI provider hostnames to loopback before installation and verifies that confinement on initial/reboot/recovery checks. Actual dashboard MainPID/executable/version checks bind Node evidence to the running service. These corrections still require the new full VM run.

A later [Node runtime security review](2026-10-28-node-runtime-security.md) identified the old 22.22.0 pin as affected by published security fixes. These old-runtime passes remain functional historical evidence. They do not qualify the corrected 22.23.4 runtime; a patched blank baseline and its descendant update require fresh VM evidence. The updater's guard against changing host runtimes remains intact.

The two matrix jobs use independent blank official Ubuntu cloud-image overlays on KVM. The installation job applies the current candidate and performs a real reboot. The update job first installs previously qualified base `9c06d8c6510b288b190a20a6a17354259c8bb6b6`, then exercises:

1. A real staged update, verified backup, source/process identity and HTTPS checks, followed by reboot without starting services from the verification helper.
2. Artifact tampering, an activating maintenance job and an overridden migration destination, each requiring the relevant refusal before unsafe effects.
3. A guest-only descendant whose voice worker deliberately fails; automatic pre-migration recovery must restore the prior source, runtime, keys and data, and survive another reboot.
4. A guest-only migration that begins and deliberately fails; the durable migration-start record must prevent code rollback and leave application services and scheduled writers stopped.

The helper requires the marked disposable VM, protected fixture inputs, verified TLS fixture identities and disabled provider actions. Original encryption keys, configuration, persistent-directory identity and seeded database records must survive the allowed update/recovery operations. Outer cleanup must complete before the overall receipt can report success. Guest-only fault commits are never published or deployed to production.

## Limits and operating follow-up

This exercise uses synthetic provider credentials and disabled telephony/billing. Both PostgreSQL containers and the application VM use one CI runner. It does not establish public DNS, real calls or emails, paid customer acceptance, off-host recovery, measured RPO/RTO, independent engineer handover, or a successful future forward migration. Each actual schema change requires its own reviewed grants profile and migration acceptance.

The database lock is local to the release host. A production database needs one coordinated migration operator; it is not safe to run independent migration jobs from multiple hosts. Existing call/session state and some rate limits remain process-local, and protected database writes have serialization points. Reproducible installations and separate runtime/migration authority support future scaling work without claiming that arbitrary worker counts or call capacity are already qualified.

Production currently retains the legacy backend and same-host non-TLS database. The separate database destination and secure privileged-access reference remain missing. This evidence does not close the paid-use gates or lift the feature freeze.
