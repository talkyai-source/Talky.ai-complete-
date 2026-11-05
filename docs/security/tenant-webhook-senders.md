# Tenant webhook timestamp cutover — 6 November 2026

The call outcome routes under `/api/v1/webhooks/call/*` and
`/api/v1/webhooks/secure/call/*`, plus `/secure/idempotent-example`, require
`X-Webhook-Timestamp` and a signature of `timestamp.payload`. A plain payload
signature without the timestamp now returns 401. This implements QA F4-06.
Stripe, Twilio, Vonage, gateway and Salesforce callbacks use separate contracts.

The repository has no identified runtime HTTP sender to these mutation routes.
That does not establish whether external tenant integrations use them.

| Identified caller/example | Destination | Timestamp |
|---|---|---|
| `backend/tests/unit/test_webhooks_call_hmac.py` | Both legacy call outcome routes | Present via helper in positive cases; deliberately absent in rejection cases. |
| `backend/tests/security/test_qa_edge_request_webhook_security.py` | Both legacy call outcome routes | Present, then explicitly removed to verify 401. |
| `backend/tests/unit/test_bh2_webhook_secret_encryption.py` | Secure goal-achieved | Present via helper. |
| `backend/tests/unit/test_bh2_webhook_signature_before_idempotency.py` | Secure idempotent-example | Present via helper. |
| `backend/docs/security/day_6_api_security.md` | Secure goal-achieved HTTP/curl examples | Explicit timestamp placeholder. |
| `docs/security/day_6_api_security.md` | Secure goal-achieved curl example | Explicit timestamp placeholder. |
| Both day-6 documents' `requests.post(url, ...)` examples | Variable URL; destination unspecified | Present through the default helper. These are examples, not identified deployments. |
| External tenant integrations and owner scripts | Unknown | Must be inventoried and verified by the owner before release. |

`create_webhook_signature_headers` defaults to `include_timestamp=True`.
Explicit `False` produces a signature these routes reject; the only identified
use of that option is a helper unit test. `/secure/verify-test` returns generated
headers to the caller and sends no HTTP request. No sender to secure mark-spam
was found beyond route lists and definitions.

`/secure/admin/configure` is an internal-token-authenticated configuration route,
not a timestamp-signed webhook receiver. Its local test callers in
`test_bh2_webhook_secret_encryption.py` and `test_webhooks_call_idor.py` correctly
omit a timestamp; `test_bh_calls_recordings_webhook_configure.py` invokes the
Python function directly.

Before release, inventory external senders and verify a current signed timestamp
over the exact transmitted body. Older documentation describing optional
timestamps or legacy compatibility does not describe the current verifier.
