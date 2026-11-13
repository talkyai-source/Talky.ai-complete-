# Campaign alerts (F3-08)

Apply migration `0090_campaign_alerts` before starting the new application and
reminder worker. New completed outbound calls and qualified leads record an
in-app event and email intent in the same transaction as their source change.
The existing reminder worker observes allowances and delivers committed intents.
No historical call/lead bulk email is generated. Its first allowance observation
can alert an already-low account.

The failure threshold is five consecutive completed outbound calls, excluding
test/inbound calls and job deferrals. Connected outcomes reset the streak.
Duplicate callbacks do not count again. Low minutes means strictly below 20
minutes **or** strictly below 10 percent of the canonical finite allowance.
Unknown allowance state does not reset an episode. Verified replenishment does.

The recipient is the single active tenant administrator. Missing or ambiguous
recipients remain `recipient_missing`; the in-app event remains available.
The existing email transport must be configured. `accepted` means transport
acceptance, not inbox delivery. Confirmed failures before sending retry at most
five times with five-minute spacing. An interrupted or uncertain send becomes
`unknown` and is never automatically replayed. Reconcile with the provider before
authorizing any manual resend. No resend command is included.

The dashboard observer reads the durable `/alerts` API for the three alert kinds.
Its local toast history is scoped by tenant/user and deduplicated by server event
ID. Initial historical observations are silent. The API returns its current
reader's tenant/user IDs; clients reject absent or different ownership receipts.
Deploy the backend before the new frontend. The Alert Timeline remains the server
record; browser toast history is not a delivery ledger.

Owner verification uses a synthetic tenant and approved destination: observe
four/five failures, a connected-call reset and a new episode; replenish then cross
the allowance threshold; qualify one owned lead. Confirm one event/intent per
episode, correct in-app visibility and delivery state. Do not test with unsolicited
customer mail. Real SMTP/DNS acceptance and deployed worker execution are unverified.

Rollback: quiesce the new worker/code and retain the schema/data while returning
to the previous application. A migration downgrade refuses any populated new
state or unowned objects. It is intended only for unused installations; never
delete receipts to force a downgrade. New tables force RLS and composite ownership
references, and application queries still bind tenant ownership explicitly.

DSAR exports include the new relations. A person's export links alert evidence
through actual owned call/lead rows and a same-tenant parent event. Account-level
allowance/streak aggregates are not person-specific records. Missing relations
make the export incomplete rather than silently omitting data.

Retention remains an owner decision: proposed redaction of recipient/body after
30 days for completed states, retaining minimal dedupe/status keys. Unknown claims
and legal holds need a separate reviewed window. Existing deletion-only cleanup
cannot safely implement that redaction policy. No destructive cleanup was added.
