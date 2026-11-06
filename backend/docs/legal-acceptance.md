# Versioned acceptance (QA F12-10)

Legal must supply approved Terms/Privacy text and version identifiers. No policy
text or approved version has been invented by this change.

Settings:

| Setting | Default | Purpose |
|---|---|---|
| `LEGAL_ACCEPTANCE_REQUIRED` | `false` | Refuse signup without current acceptance only after the checkbox ships. |
| `LEGAL_TERMS_VERSION` | unset | Current legal-approved Terms identifier, at most 128 characters. |
| `LEGAL_PRIVACY_VERSION` | unset | Current legal-approved Privacy identifier, at most 128 characters. |

Ship revision `0085_legal_acceptance` before enabling or submitting acceptance.
Signup completion accepts optional `terms_version` and `privacy_version`. Both
must match configured current versions when supplied. With enforcement off and
neither supplied, signup works as before and records no invented acceptance.
With enforcement on, missing/stale acceptance returns 422; missing legal
configuration returns 503 before account creation.

The receipt's tenant and user come from the created account, its timestamp from
PostgreSQL, and IP/user agent from the existing trusted request helpers. Receipt
failure rolls back the account transaction before login cookies are issued.
The table rejects UPDATE, DELETE and TRUNCATE. Its downgrade works only when
empty; populated legal evidence requires a reviewed forward migration. Legal
must decide retention/erasure exceptions before a tenant-deletion workflow can
remove these records. This migration has **not been run on PostgreSQL** here.

`GET /auth/me` returns current `terms_version`, `privacy_version`, and
`legal_acceptance_required` when a configured version lacks a matching latest
receipt. This signal does not claim the user accepted anything while versions
are unset. Platform users without a tenant have no tenant acceptance requirement.
After showing both approved documents and receiving explicit confirmation,
the screen calls `POST /auth/legal-acceptance` with both versions; the server
appends a receipt for the authenticated user and tenant. It accepts no user or
tenant identity from the payload. Refresh `/auth/me` after success.

Owner order: approve/publish legal text and version identifiers, apply the
migration, configure both identifiers, ship/test the frontend checkbox and
reacceptance flow, then set `LEGAL_ACCEPTANCE_REQUIRED=true`. No production
setting has been changed by this work.
