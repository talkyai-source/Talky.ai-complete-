# Clean install runbook

Goal: an empty PostgreSQL database plus this repository gives a working product.
Run every command from the `backend/` directory. Nothing here holds a secret or a real host name.

`tests/integration/test_clean_install.py` runs steps 2 to 4 and the isolation checks in CI.

## 1. What you need

- PostgreSQL 15 or newer (CI runs 15). One empty database.
- Three extensions, created by the schema file: `uuid-ossp`, `pgcrypto`, `pg_trgm`. On PostgreSQL 13 or newer all three are "trusted": the owner role only needs `CREATE` on the database. On older versions a superuser must create them first.
- Redis 7. Sign-up codes, rate limits and call counters live there.
- A mail account the service can send from (sign-up sends a code). Settings are in step 5.
- Python 3.12 with `pip install -r requirements.txt`.

## 2. Roles and database

Two roles. Run as a PostgreSQL administrator. Names and passwords are examples.

```sql
CREATE ROLE talky_owner LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD '<secret 1>';
CREATE ROLE talky_runtime LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD '<secret 2>';
CREATE DATABASE talkyai OWNER talky_owner;
```

- `talky_owner` runs migrations. `talky_runtime` is the application role. It must have no SUPERUSER and no BYPASSRLS, and it must not be a member of `talky_owner`. A superuser or BYPASSRLS role makes every row-level-security policy decorative.
- After step 3, connect to `talkyai` as `talky_owner` and run `database/runtime_role_grants.sql`. That file is the one copy of the grants. If your runtime role has another name, replace `talky_runtime` in the file first.

```bash
psql "$MIGRATION_DATABASE_URL" -X -v ON_ERROR_STOP=1 -f database/runtime_role_grants.sql
```

## 3. Build the schema

`MIGRATION_DATABASE_URL` is the owner DSN. Never stamp `head`.

```bash
psql "$MIGRATION_DATABASE_URL" -X -v ON_ERROR_STOP=1 -f database/complete_schema.sql
alembic stamp 0008_tenant_voice_tuning
alembic upgrade head
```

Migration `0099_clean_install_tables` creates `tenant_recording_policy` and `tenant_provider_cost_events`. On a database that already has them (production) it changes nothing and prints one `NOTICE` per table.

## 4. Check it

```bash
alembic heads        # one line: 0101_paypal_webhook_events (head)
alembic current      # must print the same revision
DATABASE_URL="$MIGRATION_DATABASE_URL" python scripts/verify_alembic_current_heads.py
psql "$MIGRATION_DATABASE_URL" -c "SELECT id FROM plans ORDER BY id;"          # basic, business, payg, payment_test, plus older rows
psql "$MIGRATION_DATABASE_URL" -c "SELECT count(*) FROM roles;"                # 8
psql "$MIGRATION_DATABASE_URL" -c "SELECT count(*) FROM platform_runtime_controls;"   # 1
DATABASE_URL="$RUNTIME_DATABASE_URL" python scripts/verify_rls.py            # runtime role: read every FAIL line
DATABASE_URL="$RUNTIME_DATABASE_URL" python scripts/verify_admin_runtime_privileges.py   # prints JSON, "status": "passed"
```

- `verify_rls.py --posture` skips the scratch-role part. Run it with the runtime DSN: its first check fails for a superuser or BYPASSRLS role.
- `verify_admin_runtime_privileges.py` reads `DATABASE_URL` (and `backend/.env` if present) and exits 1 unless the runtime role has every table, sequence and function right the admin routes use.

## 5. Minimum settings to start

Names only. In production (`ENVIRONMENT=production`) `app/core/prod_gate.py` refuses to start without:

- `JWT_SECRET` (not a placeholder), `SECRETS_MASTER_KEY`
- `INTERNAL_SERVICE_TOKEN` (32+ characters), `VOICE_GATEWAY_AUTH_TOKEN` (32+ characters, different from the previous one)
- `TELEPHONY_METRICS_TOKEN`
- `VOICE_GATEWAY_CALLBACK_HOST` (one loopback IPv4 address) and `BACKEND_INTERNAL_URL` (`http://<same address>:<port>`)
- `FROM_EMAIL` (a bare address on the owned sending domain)
- `ASTERISK_ARI_PASSWORD` or `FREESWITCH_ESL_PASSWORD` (not a default) when that adapter is selected
- Billing: `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET`, or `STRIPE_BILLING_DISABLED=1`. A test key also needs `STRIPE_TEST_MODE_IN_PRODUCTION=1`.

The application also needs `DATABASE_URL` (runtime DSN), `REDIS_URL`, `EMAIL_USER`, `EMAIL_PASS` (and `EMAIL_SMTP_HOST` if the mailbox is not on Microsoft 365) and `TOTP_ENCRYPTION_KEY` (MFA set-up fails without it). `.env.example` lists the rest.

## 6. The first platform administrator

There is no script on `main`. The platform-admin check (`require_platform_admin` in `app/api/v1/dependencies.py`, after `load_current_principal` in `app/core/security/principal.py`) needs one thing: a `user_profiles` row with `role = 'platform_admin'`, `is_active` and `is_verified`. No membership and no tenant are needed. A tenant membership can never grant it.

1. Sign up through the website or API and enter the emailed code. This creates a verified, active account.
2. As `talky_owner`, promote that account (replace the address):

```sql
UPDATE user_profiles SET role = 'platform_admin' WHERE lower(email) = lower('<email>') AND is_active AND is_verified;
```

3. It must print `UPDATE 1`. Sign out and sign in again so the new role reaches the token.

`PLATFORM_ADMIN_MFA_REQUIRED` is off by default. If you turn it on, the administrator must complete two-factor sign-in.

## 7. Payments in test mode

No price options exist until this runs. Set `STRIPE_SECRET_KEY` (a test key), `STRIPE_TEST_MODE_IN_PRODUCTION=1` and unset `STRIPE_BILLING_DISABLED`, then:

```bash
python scripts/stripe_catalog_setup.py --env-file .env --webhook-url https://<your-host>/api/v1/billing/webhooks --return-url https://<your-host>/billing
python scripts/stripe_catalog_setup.py --env-file .env --webhook-url https://<your-host>/api/v1/billing/webhooks --return-url https://<your-host>/billing --apply
```

The first command is a dry run. Restart the services afterwards.

## 8. A smoke check

1. Sign up and sign in.
2. Create a campaign.
3. Save a recording policy (`PUT /api/v1/recordings/policy`), then read it back (`GET /api/v1/recordings/policy`): `configured` must be `true`.
4. Sign in as the platform administrator and open the admin area.

## 9. Known gaps

- Sign-up through the service layer is not covered by an automated clean-install test: it needs Redis and the mail path (`app/api/v1/endpoints/auth/signup.py`).
- `database/runtime_role_grants.sql` grants SELECT, INSERT, UPDATE and DELETE on every table. It is not least privilege (for example the audit table is deletable by the runtime role). A per-table list does not exist yet.
- `provider_cost_ledger.py` (the `_flush_once` write) sends `tenant_id` and `call_id` as strings to `COPY`; the clean-install test exercises this once, nothing else guards it.
- `tests/integration/test_release_migration_merge.py` still pins `HEAD = "0081_admin_payment_merge"` (line 35); it is not in the CI list.
