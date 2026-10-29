# CP06 canonical billing-notice recipient

Invoice payment and payment-failure notices previously searched for
`user_profiles.role='owner'`, which the canonical account model does not use.
The existing webhook transaction now resolves the default recipient through
current `tenant_users` membership and the canonical `roles` table.

The default is exactly one active, verified profile whose current tenant is the
invoice's tenant and whose membership in that same tenant is active with role
`tenant_admin`. The membership role is authoritative even if the legacy profile
role label differs. Global platform-admin profiles are excluded. Removed,
suspended or pending memberships, inactive or unverified profiles, a foreign
tenant, a different role and blank email addresses are ineligible. Billing
subscription status does not suppress payment-failure notices.

With no eligible administrator or more than one, the recipient remains missing.
The durable notification row records `recipient_missing`; no global fallback,
arbitrary first administrator, guessed billing contact or multi-recipient send
is introduced. Selecting a separate billing contact remains an explicit future
product decision where needed.

The first persisted intent retains its captured recipient and delivery key.
Repeated provider events cannot replace that recipient, create a second intent,
reset consumed minutes or replay financial effects. Membership changes do not
silently retarget an already captured billing notice. Existing accepted/unknown
transport outcomes and operator reconciliation remain unchanged; transport
acceptance is not proof of inbox delivery.

The existing migrated-PostgreSQL integration suite now includes both invoice
outcomes, canonical membership authority, thirteen ineligible-recipient cases,
multiple-administrator ambiguity and repeated events after an administrator
change. It runs under the existing non-owner NOBYPASSRLS fixture role with
explicit SELECT access to membership and role tables. Provider reads use the
existing synthetic Stripe fixture; no provider or email transport is called.
Native PostgreSQL execution is pending the integrated CI run; this source change
does not by itself close CP06 delivery, deployed acceptance or paid readiness.
