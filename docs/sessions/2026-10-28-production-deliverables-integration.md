# Production deliverables integration — 28 October 2026

The user requested completion of the four production deliverables, including
their deployment and demonstration. This checkpoint records implementation and
verification work; it does not declare paid-use readiness or lift the existing
feature freeze. The original Word document requires a fresh Ubuntu rebuild,
working Admin account operations, verified Stripe subscription activation and a
production database outside the application server.

## Current operating evidence

A read-only inspection of the existing Ubuntu 24.04 production host found:

- Main checkout `5c4c908ede3a736354c6f5620664de33db60fc6c`, with API-web
  pinned to `92c943e2b2e88faa57eca27ceb46c7768ff5ad0a`.
- Both API health endpoints returned 200 and the existing C++ gateway reported
  ready. The application and worker services were active.
- The scheduled backup's latest result was successful. Its maintenance source
  remained pinned to `eae0465eea83209a30051b4dae6e626d8714bc6f`; this supersedes
  earlier reports of a failing backup service, without establishing off-host
  recovery or retention/PITR acceptance.
- PostgreSQL was still on the application server, without database TLS. Its
  normal application role was neither superuser nor BYPASSRLS. Most Python
  systemd services still used the root OS account; deployed OS permissions
  remain an acceptance item.
- The actual schema head was `0073_payment_test_plan`. The legacy incidents
  table had eleven columns, text identifiers, no RLS/policies and zero rows.
- The configured Stripe key was a test-mode key. Required voice-provider and
  webhook credentials were present; presence is not provider acceptance.
- SSH worked. Noninteractive sudo required operator authentication. The server
  checkout contained an untracked `releases/` directory; the existing clean-tree
  deployment guard must be reconciled using an approved immutable source layout.

No customer records, secrets, payment, call, notification, service restart or
production mutation was used to obtain these observations. The separate
[Admin billing checkpoint](2026-10-28-admin-current-billing-compatibility.md)
retains the existing production-backup restore evidence and its limitations.

## Verified release-history repair

Commit `d1a5b03af7c8e00b59ab025f336b166bb06739a8` integrates current main into
the Ubuntu work. Metadata-only `0075_release_history_merge` joins the published
`0074_incidents_bootstrap` and `0073_payment_test_plan` histories without
renaming or reparenting either revision.

Two owned synthetic databases passed actual PostgreSQL 16 upgrades, invoked
from Ubuntu 24.04 with verified TLS and a restricted migration owner:

1. An existing 0074 installation upgraded to 0075 while preserving incident and
   event rows, relation identities, ownership, ACLs, indexes and RLS policies.
2. The exact main canonical schema was bootstrapped, stamped at 0008, upgraded
   to the actual published billing 0073, then upgraded to 0075. The missing
   incident/event tables acquired their required forced RLS. Existing catalog
   rows and a synthetic post-release payment-plan marker survived without
   replaying the applied billing migration.

Both repeated upgrades passed. Payment-test terms remained one dollar, two
minutes, one agent and one concurrent call. These fixtures contained no
production data; neither the local demonstration database nor production was
changed. The earlier focused graph and billing/catalog selections passed eight
and 46 tests respectively; these are separate selections, not a whole-product
test count.

The nonsecret [billing/Ubuntu history receipt](artifacts/production-integration-20261028/billing-ubuntu-history.json)
records these bounded results.

The combined Admin merge, `03e1d79241b59ac50e99a468b378dac1b0a2b080`, adds
metadata-only `0082_ubuntu_admin_merge`. It extends 0072's strict adopter to
accept the exact published Ubuntu policy as well as the original policy-free
manual table. Revision identities and parents remain unchanged. The existing
policy is retained, and unexpected policies, dependencies, malformed UUIDs or
incomplete receipts fail transactionally.

All **55** actual PostgreSQL adoption/preservation/rejection cases passed from
Ubuntu using the restricted owner and verified TLS. Both retained databases
then upgraded from 0075 to 0082 and repeated the upgrade, preserving existing
incident/event records, incident policy identity, ownership/ACLs, tenant fields
and billing rows. The [unified history receipt](artifacts/production-integration-20261028/unified-history.json)
binds all 90 Alembic Python inputs to the tested source. A separate 77-test
graph/billing selection and 16 current Admin intent checks passed. Counts from
different and overlapping selections must not be added into a product score.

## Installer and recovery validation

Commit `62aee997` makes installer preflight reject absent, blank or template
Deepgram, Cartesia and Groq keys before host changes. This matches the existing
API startup requirements. All 55 installer/preparation checks passed. Credential
validity and a real call remain separate acceptance requirements.

Commit `96634365` adds a full-schema recovery rehearsal using two disposable
PostgreSQL instances, verified TLS, separate migration/runtime identities and
synthetic tenant data. It exercises the existing backup/restore CLI. The first
Ubuntu CI run failed during fixture setup; its result is retained. Follow-ups
repaired private Docker endpoint discovery and retained the next strict
schema-comparison failure. That run already preserved all records and sequences;
PostgreSQL had re-rendered equivalent CHECK expressions during restore.

At `226ff9d659d0eb1bb8cec9dec8dc3bf96a55ecdc`, the full rehearsal passed in
[Ubuntu run 40594877208](https://github.com/talkyai-source/Talky.ai-complete-/actions/runs/40594877208).
All **111 public tables' records and five sequence states** matched, restricted
runtime reads/writes and tenant isolation passed on both clusters, three
negative TLS/HBA cases refused connections for their intended reasons, the
source remained unchanged and owned resources were removed. The schema check
independently replays the source's schema-only export into a new comparison
database, then compares its exact PostgreSQL-parsed dump with the actual
restore. All three hashes are retained; the parsed reference and restored
schema hashes match. No broad regex normalization or omitted constraint hides
a mismatch.

The [receipt](artifacts/production-integration-20261028/tls-recovery-0075.json)
binds this result to the **0075** source. The combined **0082** candidate's
corresponding CI result is separate and must pass on PR26. Two containers
sharing one runner do not demonstrate off-host disaster
recovery, production RPO/RTO, retention or PITR.

## Coordinated candidate and outstanding inputs

The newer Admin release is integrated with the Ubuntu/billing branch in
[draft PR 26](https://github.com/talkyai-source/Talky.ai-complete-/pull/26).
Its measured health, durable incidents, authentication and operational workflows
supersede the earlier narrow Admin fixes. The combined migration history and
incident-table adoption have the separate checks described above. Previous Admin
CI and browser results remain attached to their original source revisions.

The existing authenticated metrics path now reports measured worker heartbeat,
bounded queue and backup-status observations. Missing or invalid observations
remain unavailable. [Operational signal evidence](2026-10-28-operational-signals.md)
records source checks and native Prometheus rule evaluation; none proves alert
delivery to an operator. Both supported backup entry points use
one atomic nonsecret status receipt, with the external backup identity writing
its own status directory and the API granted only read access.

The complete authenticated local frontend artifact build passed on Ubuntu in
[run 40594146173](https://github.com/talkyai-source/Talky.ai-complete-/actions/runs/40594146173).
Admin uses the supported `/api/v1` same-origin configuration; its local nginx
listener must supply that proxy before this artifact is activated. The existing
demonstration runtime has not been silently replaced by that build. This local
artifact is not the public production frontend configuration.

| Deliverable | Remaining evidence or input |
|---|---|
| Blank Ubuntu installation | A designated clean, isolated Ubuntu host; completed installer apply, restart/reboot/update/recovery checks, controlled call and independent engineer demonstration |
| Separate database | Designated external PostgreSQL destination and securely configured access; TLS, runtime grants, production migration, monitoring, backup retention/PITR where supported, off-host restore and measured recovery |
| Stripe subscriptions | Correct live configuration and designated purchase resources; the document's purchase, cancel, failure, renewal, upgrade, duplicate-webhook and browser-close acceptance cases |
| Admin | Coordinated backend/frontend activation; actual hosted MFA/role/account/billing/operating journeys and designated provider/finance resources |
| Operating alerts | Approved receiver and actual delivery, acknowledgement, recovery and outage/restart demonstration |
| Release and handover | Privileged deployment access, candidate-bound traffic freeze/drain and distinct operator attestations, compatible rollback, documentation reconciliation and the requested three video demonstrations |

Deployment authorization already exists. Missing host/access references and
operating attestations are factual prerequisites, not a request to re-authorize
the work. Idle counters cannot prove an ingress/outbound freeze. The existing
[deployment contract](../DEPLOYMENT.md#production-drain-manifest-contract)
continues to apply. No acceptance gate is closed solely by this checkpoint.
