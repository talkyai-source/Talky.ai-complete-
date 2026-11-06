# Dial destinations and phone repair (QA F2-14 / F14-03 / F3-13)

Dial destinations must be valid according to the installed libphonenumber data.
The shared parser removes extensions and never invents a country prefix after
validation fails. Short SIP extensions are not contact dial destinations.
No email-based validation exceptions remain.

The country precedence is an explicit `default_country` import/contact override,
then `campaign.script_config.default_country_code` (or
`script_config.campaign_slots.default_country_code`), then the existing account
JSON setting `tenants.calling_rules.default_country`, then `US`. Country values
must be supported ISO alpha-2 codes. An explicit international `+` number keeps
its own country. The account JSON key is new; it needs no schema migration and
does not infer a country from the account's time zone or IP address.

Contact create/update, Salesforce import, and paste accept `default_country` in
JSON. CSV and legacy bulk upload accept it as a query parameter. Invalid lead
numbers return 422 with a row and `phone_number` field before contact-list or lead
writes. DNC add/import accept the same country input and parser; absent a DNC
request country, the account setting is used. Internal opt-outs carry canonical
international numbers. CallGuard uses the identical key and blocks invalid
destinations. DNC bulk responses retain their accepted/invalid counts contract.

The installed library rejects the reserved GB mobile example `07700 900123`.
It must be rejected under the strict-validity rule. Tests use the valid London
example `020 7946 0958` to prove national DNC input blocks `+442079460958`.
`001 4155552671` is valid international notation from GB but invalid from US.

## Owner repair procedure

Supply `DATABASE_URL` through the existing operator process environment. Do not
place it on the command line. The repair uses the existing AuditLogger on apply.
Start in `backend/` and review the default dry-run report:

```sh
python scripts/repair_non_e164_leads.py --batch-size 100 --sample-limit 10
python scripts/repair_non_e164_leads.py --tenant <TENANT_UUID> --batch-size 100 --sample-limit 10
```

Its aggregate count uses the exact QA population, separately scoped to each
tenant: `status <> 'deleted' AND phone_number !~ '^\+[1-9][0-9]{6,14}$'`.
Samples mask numbers. Valid country-based repairs are proposed; invalid numbers
and collisions are flagged. Already malformed `+0...` country prefixes are not
guessed back into national format. The report does not claim to find all
libphonenumber-invalid numbers that already have an E.164-shaped string.

Before applying, confirm campaign/account country settings, stop the affected
campaigns, and settle or rebuild pending jobs through the normal campaign flow:
jobs hold their own destination copy and this tool does not rewrite queues.
Then the owner may run:

```sh
python scripts/repair_non_e164_leads.py --tenant <TENANT_UUID> --apply --batch-size 100 --sample-limit 10
python scripts/repair_non_e164_leads.py --tenant <TENANT_UUID> --batch-size 100 --sample-limit 10
```

Each batch commits with an audit receipt. A failed batch rolls back; earlier
batches remain committed. Compare-and-set prevents overwriting a concurrent
number edit. No deletion or merging is performed. Repeated application is
idempotent. These commands have **not been run on PostgreSQL or production**.

## Insert-path inventory and deferred constraint

The contact destination paths are campaign create/update, CSV and legacy bulk
upload, paste, Salesforce import/callback, assistant `manage_lead`, and client
creation. The common bulk-ingest sink receives a strict normalization callback.
The concrete lead INSERT sinks are:

- `api/v1/endpoints/campaigns.py` — manual campaign contact.
- `api/v1/endpoints/contacts.py` — legacy CSV campaign import.
- `domain/services/dialer/bulk_ingest.py` — CSV, paste and Salesforce.
- `infrastructure/assistant/tools/campaign_admin.py` — assistant lead creation.
- `services/scripts/call_transcript_persister.py` — inbound lead creation still
  stores a raw caller number or `unknown`; untouched in this item.
- `domain/repositories/lead_repository.py` — generic dictionary insertion remains
  unvalidated; no active runtime create caller was found in the source audit.

No CHECK is shipped. It would reject unknown inbound callers today, and owner
repair has not run. Add a constraint only after the inbound/generic storage
contracts are resolved, all insert paths validate, and the dry-run count is zero.
Captured-as-said numbers in `call_lead_details` retain their separate owner-approved
capture contract; they are not dial destinations.

Historical DNC rows whose stored key included extension digits need separate
owner reconciliation. This change fixes new writes and reads; it does not rewrite
old suppressions or delete them.
