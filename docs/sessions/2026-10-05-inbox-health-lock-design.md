# Inbox health-write concurrency design probe

The current inbox health writer issues separate connector-wide account and parent
updates after an authentication failure. A reconnect can replace the account
between the provider request and those updates, causing an old rejection to mark
the new connection expired. Pinning an inbox read to its original account does
not, by itself, fix this write race.

On source candidate `3648fe70`, a **SQL design probe** passed seven cases against
the existing disposable loopback PostgreSQL database at migration head
`0061_dnc_runtime_contract`. This is not a test of a repaired application method.
The proposed application repair still requires source implementation, review,
and tests of the actual resolver, snapshot propagation, and health writer.

The proposal uses one tenant-scoped transaction: lock the exact connector row
with `FOR UPDATE`, then use a fresh statement to lock and inspect the active
authorization row. Only matching row and credential-generation evidence may
expire that account and its parent. A changed, missing, or ambiguous authorization
does not justify updating the current connection. The refreshed generation must
be retained after an acknowledged token write, including when a subsequent read
returns another authentication failure.

The existing account-to-connector foreign key serializes a reconnect insert with
the parent lock. The probe observed real PostgreSQL lock waits in both orders:
an expiry already holding the parent lock blocks the new account insert until
commit; an uncommitted insert blocks the expiry lock, and the next statement then
sees the replacement account. No new lock service or schema is proposed.

Verified cases were current-authorization expiry, replacement committed before
the lock, both concurrent insert/lock orders, a same-account token refresh during
the parent lock, rollback after the first status write, and cross-tenant denial.

The [program](artifacts/inbox-health-lock-design/probe.py) and
[result](artifacts/inbox-health-lock-design/result.json) preserve the evidence.
It used private `LIKE ... INCLUDING ALL` copies of two tables, the canonical
account-to-connector foreign key, a restrictive tenant policy, and a temporary
`NOBYPASSRLS` role without public write privileges. It did not copy the tenant
foreign key, application triggers, or full production grants/policies. The
tested transaction isolation was PostgreSQL `read committed`.

Public connector/account row digests, table security/grant metadata, and migration
head were unchanged. The private schema and role were removed. No provider,
customer account, deployment, migration, push, or action replay occurred. The
experiment used only synthetic credential strings and did not inspect saved
credential values. AG06 and its live/operator acceptance remain open.
