# Campaign edit conflicts (QA F3-03)

Send the campaign's last-read `updated_at` as `expected_updated_at` in
`PUT /api/v1/campaigns/{id}`. Use its full timestamp, including timezone and
fractional seconds. The tenant, outbound direction and timestamp are compared
in the final UPDATE, so a competing save cannot overwrite the winning save.

A stale save returns HTTP 409 with the shared error envelope:
`error.code = campaign_version_conflict` and
`error.details.current = <current tenant-owned campaign>`. Show that the campaign
changed and offer to reload those values. Do not automatically retry the stale
payload with the new timestamp. A different tenant's campaign remains 404.

Omitting `expected_updated_at` preserves legacy saves and logs
`campaign_update_without_version`. These saves also advance `updated_at`, even
on a historical schema whose timestamp trigger is disabled. This compatibility
mode is deliberately still susceptible to two legacy editors overwriting each
other until the editor sends the version.

The campaign editor was changed by Hisham on 4 November (`cb646fee`), so this
round's per-file ownership rule leaves its wiring to Hisham. No migration is
required. PostgreSQL concurrency/locking was not exercised locally; targeted
tests cover the final compare-and-set query contract and HTTP conflict response.
