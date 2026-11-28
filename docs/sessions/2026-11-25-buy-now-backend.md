# Buy Now documents: the backend — 25 November 2026

## Owner decision

The product owner, on 25 November 2026, with the backend specification "Telephony 'Buy Now' documents": "work on that as well please once done make it live". The specification lists seven decisions with a recommended option each and says "Until a decision is made, build with the recommended option". This was built with the recommended options. The exception to the feature freeze is recorded in `docs/production-readiness/execution-tracker.json` (`scope_policy`).

Before this change the "Buy Now" window in Settings → Telephony collected two documents and sent nothing. Now Submit can be made real: the website part is a separate change that is merged only after this backend is live.

## What it does

- **A signed-in member sends two documents.** Business use: business registration and a photo ID. Personal use: a utility bill and a photo ID. `POST /api/v1/telephony/number-requests` (multipart, `Idempotency-Key` required); `GET /api/v1/telephony/number-requests/config` gives the accepted types and the size limit. The account comes from the session, never from the request.
- **Each file is checked while it is read** (`app/domain/services/number_requests/intake.py`): its real type from its first bytes (PDF, JPEG, PNG; the name and the declared type are not trusted), its size against the limit (10 MB by default), not empty, exactly the two documents of that use. The file name is cleaned; the stored name is random.
- **Each file is stored encrypted** (`store.py`): AES-256-GCM with its own random key, bound to the account, the document and its kind; the file key is wrapped by the existing key service and kept only in the database row. Files live in a private folder outside any public path (`number_request_documents/`, mode 700). No address or link to a file is ever returned.
- **Saved together** (`service.py`): the request row, the two document rows and the two files are one unit. A failure leaves no row and no file. When the outcome of the commit cannot be seen, the database is asked for that transaction's fate; only a transaction known to have rolled back has its files removed, and the customer is told the truth ("could not confirm … send the same files again").
- **The same key is the same request.** A retry with the same key and the same files gets the first answer and creates nothing; the same key with other files is refused.
- **Limits.** Ten submissions an hour per member; at most three requests waiting for review per account; no upload when the documents disk is below its free-space floor (5 GiB by default).
- **Platform staff review** (`/api/v1/admin/telephony/number-requests`): list, read one request, open one document, approve or reject. A document's bytes are sent only after the audit entry for that opening is committed. A decision is one conditional update: a second decision gets 409. A rejection needs a note. Every list, read, opening and decision is in the audit log (the note's text is not). The customer is emailed the decision after it is committed; a failed email never undoes it.
- **Retention** (`app/workers/number_request_retention.py`, called by the nightly clean-up): nothing is deleted until the product owner sets a time (`NUMBER_REQUEST_REJECTED_RETENTION_DAYS`, `NUMBER_REQUEST_RELEASED_RETENTION_DAYS`). The job obeys the clean-up's dry-run switch. Deleting marks the rows and destroys the file keys in one transaction, then removes the files. Leftover files of failed uploads, which no record refers to, are removed after an hour, but only when the job has proven that it can see that account's records and that the account exists in this database, and never more than twenty in one run.

## Database (Alembic `0108_telephony_number_requests`, on `0107_staff_suspension`)

Two new tables, `telephony_number_requests` and `telephony_number_request_documents`, with row security enabled and forced and the usual `tenant_isolation` policy. No existing table or row is changed. The previous code never reads them, so the code can be rolled back with the migration applied. No email, address or phone column is stored.

Every statement of this feature carries an explicit account predicate. The reviewed-SQL list (`tests/unit/tenant_sql_baseline.json`) gains no entry: two existing entries of `cleanup_worker.py` were re-keyed because that file changed.

## Built differently from the specification, on purpose

| Specification | Built as | Why |
|---|---|---|
| Staff paths `/{id}` | `/{tenant_id}/{request_id}` | Every staff statement then carries an account predicate, and the reviewed-SQL list needs no exception |
| A short-lived signed link for staff | An authenticated download route | A link is a bearer capability; the route checks the staff member and writes the audit entry first |
| Virus scan if the infrastructure supports it | Documents are marked `not_scanned`; the download says so (`X-Document-Scan`) | No scanner exists on the server |
| "The same key returns the same answer" | A replay is rebuilt from the database | After a decision it shows the current status |
| The body is never held in memory | The security middleware reads up to about 20 MB for this one path, then the route parses it in pieces | The existing middleware buffers every request; the larger limit applies to this path only |
| The request-wide "too large" answer | The standard error shape with a customer sentence (the old `detail` key is kept beside `error`, same sentence) | The per-file limit, checked first by the website, uses the standard error shape |

Known and accepted: while the disk is below its floor a retry of an unconfirmed save is refused like a new upload.

The product owner approved the four document refusal messages. They read “{Label} is empty.”, “{Label} is larger than 10 MB.”, “{Label} must be a PDF, JPG or PNG file.” and “Add {Label}.”

## Waiting for the product owner

- The retention times, and the clean-up's dry-run switch (the nightly clean-up in production runs as a dry run).
- The wording of the two decision emails and the other submission messages, including "Your earlier requests are still waiting for review. Please wait for a decision before sending more documents.", "We could not confirm that your documents were saved. Please send the same files again." and "Please choose a file for {Label}."
- Whether three waiting requests per account is the right limit.
- Whether any member of an account may submit (the recommended option) or only its admins.
- A virus scanner; an off-site copy of the documents folder (the files are encrypted, so a copy would be safe); a sentence in the Privacy Policy on how these documents are stored and for how long.
- Outside this change: choosing the number, price, payment and set-up after approval; an Admin console screen; showing the status to the customer.

## Evidence (25 November 2026, on the final code)

- Backend unit and security tests, whole, run in pieces on a small machine: 26,062 passed, 129 skipped, 0 failed. Twelve files that ran before the last change were run again afterwards: 212 passed, 5 skipped.
- The deploy and operations script tests, run separately: 50 passed.
- The three database test files (`tests/integration/test_telephony_number_request*_pg.py`) on PostgreSQL 16 prepared as CI prepares its own (the complete schema, every migration to 0108), as a role without superuser or row-security bypass: 28 passed. They are added to the database step of `.github/workflows/ci.yml`.
- Ruff gate clean; bandit (high severity) clean.
- Two independent reviews of the code, each followed by fixes and a second read.

Not verified here: a real upload through the production web server; the production key service; the first real submission (after release: `journalctl -u talky-api-web | grep number_request_saved`).
