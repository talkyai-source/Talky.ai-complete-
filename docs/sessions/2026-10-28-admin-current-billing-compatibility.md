# Admin release compatibility with current billing

This checkpoint continues the existing Admin remediation scope. It does not
record full production activation or lift the production-readiness freeze.

The previous candidate `8df88718522406a3522a974cfc8175c4538f9ebe` passed all
14 CI checks, including 701 Admin PostgreSQL tests and 943 Admin frontend tests.
Its runtime-equivalent `614685d4` also passed an isolated production-backup
restore and restricted-role migration from 0072 to 0080. Earlier failed runs
remain historical evidence in the [preceding session](2026-10-27-admin-remaining-remediation.md).

## Changed dependency and repair

A fresh read-only server observation at 2026-10-27 21:48 UTC found an independent
deployment of `5c4c908ede3a736354c6f5620664de33db60fc6c`, schema
`0073_payment_test_plan`, and API-web maintenance source `92c943e2`. All eight
observed application/telephony services were active. The verified `eae0465e`
backup pin and active backup timer were retained; the last backup result was
successful. This supersedes the previous f892/0072/ec96 baseline.

Merge `50dda882ce7ffe1bded3dd69fd53e25765211340` preserves that latest main and
the Admin candidate. Published migration identities and contents remain intact.
The new `0081_admin_payment_merge` joins `0080_supplier_allowance_merge` and
`0073_payment_test_plan` without business SQL; downgrade refuses to split the
released histories. The incoming customer frontend and billing changes remain
preserved. An omitted/null tenant plan still grants no plan or included minutes;
explicit administrator plan choices retain their existing behavior.

Focused migration/payment/catalog checks passed 68 tests; the selected tenant
checks passed nine. These selections have no skips. The expanded owned-database
matrix covers ten branch starts, including the deployed 0073 and both heads,
repeated upgrades, exact payment-plan behavior and preservation of existing data.
Final execution receipts and combined CI are attached to
[PR 22](https://github.com/talkyai-source/Talky.ai-complete-/pull/22); the prior
candidate's successful CI does not qualify changed runtime code automatically.

A fresh 24,720,029-byte production archive contains 121 TABLE DATA entries and
has SHA-256 `6e85462ece158d5e757995a517694b34a80e0e691d38350f96aea27e993fb348`.
Its same-image PostgreSQL15.17 restore passed in a new network-isolated container,
preserving ownership, role/extension metadata and the five representative counts.
The application role was demoted before application checks. Candidate migration,
selected table fingerprints, service invariance and exact resource cleanup are
separate final evidence; a successful restore alone does not establish them.

## Remaining activation and acceptance

The existing [deployment procedure](../DEPLOYMENT.md#production-drain-manifest-contract)
still requires the actual carrier/inbound and outbound freeze, fresh four-counter
drain, topology/change references and two distinct operator attestations. User
deployment authorization is already recorded. These operating facts have not
been supplied and cannot be replaced by a code review or an old zero-count probe.
The configured synthetic-call file passes format validation; that establishes
neither destination ownership nor authorization for recurring real calls.

The full candidate must deploy coherently before matching frontend promotion.
Users operation discovery, Inbound All history, connector summary and media
history were last reproduced against the previous ec96 API-web release. They
are repaired in the candidate; the new production billing diff does not contain
those Admin repairs. No fresh hosted business-flow success is claimed here.

After activation, designated MFA, role, enquiry, call, connector, media and
provider journeys remain. Groq/Cartesia statement mappings, supplier-account
allocation and finance acceptance require actual account-period source inputs.
Off-host recovery/RTO/RPO, load and policy/customer acceptance remain explicit
in the existing plan. CP09 remains in progress; unrelated feature work stays
frozen. New evidence must close these items individually, without treating CI
or a narrow maintenance release as whole-product completion.

Private working receipts are retained under `output/admin-resume-20261028/`.
No raw production archive, credential or customer rows are added to Git.
