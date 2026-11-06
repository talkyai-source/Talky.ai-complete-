# Call history search and continuation (F6-03)

`GET /api/v1/calls/` accepts a literal `search` substring across phone number,
caller ANI, called DID and the owned campaign name. `%`, `_` and backslash are
escaped, not treated as user-supplied wildcard syntax. Search and total count
have the same tenant/filter predicates. Input is bounded to 200 characters;
control characters and malformed cursors return 422.

For a large history, start with `?pagination=cursor&page_size=100`. The result
keeps `items` and adds `next_cursor`; `total` is null because a cursor request
does not scan the whole matching set for a count. Continue with
`?cursor=<URL-encoded-next_cursor>&page_size=100` and the same status, direction,
inbound campaign, date and search filters. Stop when next_cursor is null.
Do not combine a cursor with page>1. Changing filters starts a new cursor.

Ordering is `(created_at DESC,id DESC)` inside an explicit tenant predicate.
The opaque value retains the timestamp/UUID boundary and a tenant/filter hash;
it is not an authorization token or a database snapshot. Authorization still
comes from the authenticated request. Concurrent new rows preceding that
boundary do not shift subsequent pages. Existing rows modified/deleted during
pagination can change what remains, as with any live listing. Refresh to see
new calls. Page-number clients retain their existing offset/count response;
they must opt into continuation to receive its performance benefit.

Migration 0091 adds the matching tenant/timestamp/ID B-tree without CONCURRENTLY.
It adopts only a compatible valid existing named index and removes only its
own index on downgrade. Expect seconds for an ordinary index around 50k rows
under light load, with writes blocked through the migration transaction; actual
host timing is unmeasured. Lock acquisition is capped at 5s, statement duration at 120s.

Campaign statistics cache only aggregate counts for up to 10 seconds, per
pool/tenant/campaign/version, with at most 128 entries per API process.
Campaign ownership, existence/status and the live blocking reason are checked
on every request. No error is cached. This is a bounded burst/polling cache,
not a durable rollup or a claim that the 1M-call staging target has been met.

For Hisham: `Talk-Leee/src/app/calls/page.tsx` and its API helper should use
the first cursor request above, keep a stack of returned cursor values for
back/next navigation, reset it on any filter/tenant change, and show next/previous
instead of an invented total-page count when `total` is null. Keep the search
term on subsequent requests. No call-history frontend file was edited for item 39.

Owner validation: on staging, seed the assessed 1M calls/500k contacts and repeat
the QA call-history/search/campaign-stats timings. Record PostgreSQL EXPLAIN
(ANALYZE,BUFFERS) for first and continued pages, cold and repeated stats, CPU,
memory and p95 latency. The original 2-second target and real lock time remain
unverified until that run; no production query or seed was executed here.
