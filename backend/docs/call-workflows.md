# Durable call notes and post-call forms — F1-26

`GET /api/v1/calls/{call_id}/workflow` and `PUT` at the same path require the
authenticated tenant and the existing calls-read / calls-create permissions.
An absent or foreign call returns 404. A valid call with no saved workflow
returns `{"workflow": null}`. The database, not a browser, owns the shared state.

PUT accepts this shape; all text fields are plain text, not HTML:

```json
{
  "leadType": "hot",
  "notes": "Requested a callback",
  "form": {
    "contact": "Synthetic person",
    "interest": "Pricing",
    "nextStep": "Call tomorrow",
    "completed": false
  },
  "create_only": false
}
```

`leadType` is cold/warm/hot/follow_up. Maximum lengths are notes 4000,
contact 160, interest 1000 and nextStep 1000 characters. Extra fields, invalid
UUIDs, NULs and lone surrogates return 422. `updatedAt` is server-generated.
PUT returns `{workflow, created}`; GET returns `{workflow}`.

The tenant-owned call is locked before reading/writing its workflow. The write
and required audit receipt commit together. Audit before/after records include
classification, completion, actor and text lengths/hashes, not another copy of
the notes. Audit failure rolls back the write. Identical saves add no receipt.
The stored notes/forms are included in the DSAR operational export registry.

For browser migration, send each existing local entry with `create_only: true`.
If another user has already saved that call, the API returns the canonical
server entry without overwriting it. Retain the local backup until a successful
response. Failed saves must remain visibly unsaved and retryable. A server
failure is not permission to mark a browser-only draft as saved. Ordinary PUT
uses serialized last-write-wins behavior; edit-version conflict detection for
call workflows is beyond this finding (campaign version checks are separate).

Migration `0086_call_workflows` follows `0085_legal_acceptance`. It creates a
tenant/call primary key, a composite FK that prevents cross-tenant ownership,
text bounds, an actor index and forced RLS. Existing-data checks are not added:
this is a new empty table. The parent calls index is plain, transactional SQL;
it briefly blocks writes while building. Its duration has not been measured on
PostgreSQL and must be rehearsed with the owner's database copy. No production
data or database was accessed. **Not run on PostgreSQL.**

An empty table can be downgraded; a populated table refuses rather than silently
discarding notes. The composite parent index remains on downgrade because it is
non-destructive and later foreign keys may depend on it. Restore/export notes
and review a forward migration before attempting a destructive schema reversal.
