# CP05 reseller operations: first paid release exclusion

Status: excluded from the first paid release by the user's explicit scope
decision on 28 October 2026. Reseller operations and staff invitations remain
future work; neither feature has been built or completed by this exclusion.
The operational containment below remains in place. Branding preview is retained.
This scope revision does not close unrelated production gates or lift the global
feature freeze.

The white-label tenant UI persisted create/edit/suspend/resume operations only
in browser storage while claiming suspension prevented calls. Its generated
tenant IDs led to an agent-settings screen with unavailable mutation routes.
Partner billing displayed fixed paid invoice rows and fabricated usage/charges;
the partner dashboard derived totals from the branding slug. Partner analytics
converted backend failures into empty data, and the workspace exposed partner
creation, administrator-token and transfer controls without a routed contract.

These operational routes now explicitly say reseller operations and staff
invitations are not included in this release:

- `/white-label/dashboard`: partner provisioning, administrator invitations and
  transfer controls.
- `/white-label/[partner]/dashboard`: partner activity and billing summaries.
- `/white-label/[partner]/analytics`: partner usage analytics.
- `/white-label/[partner]/billing` and its subtree: invoices, charges and usage.
- `/white-label/[partner]/tenants` and its subtree, including a directly entered
  `/tenants/[tenant]/agent-settings`: tenant creation, edits, limits, suspension,
  resume and agent settings.

Tenant and billing layouts discard operational children. The nested agent page
contains no client request or save action. Legacy browser storage is neither
loaded nor treated as a tenant/account receipt. Unmounted prototype components
are not evidence of working reseller operations.

The existing server-side white-label role checks and branding provider remain.
No new role mapping, partner authorization or backend contract is inferred from
a cosmetic slug. Branding preview, logo/color/favicons and its cosmetic buttons
remain available and are explicitly described as previews. Customer billing at
`/billing` and canonical platform-admin partner/tenant controls are unchanged.

The native component/request tests use real Next request-cookie scopes and the
actual server route exports, with only unrelated dashboard chrome substituted.
They cover direct nested routes with a child that fails if mounted, seeded local
storage, no reseller reads/writes, existing-role refusal, anonymous access and
session revocation, workspace controls removed, and retained branding preview.
All 11 native request/component tests passed locally without skips. The local
dependency reuse runs Next 16.3.3; the candidate lock specifies 16.4.1, so locked
release CI still needs to qualify the exact dependency set. The exclusion copy
was subsequently updated to match the user's accepted release scope. This is
containment under an explicit exclusion, not reseller operating acceptance.
