# Onboarding and infrastructure packages: review and integration (27 Oct 2026)

The two local packages recorded in
[local customer onboarding](2026-10-27-local-customer-onboarding.md) and
[local infrastructure deliverables](2026-10-27-local-infrastructure-deliverables.md)
were built on the stale working tree at `a0706b6f`. `main` had moved on: it
already held newer versions of most files they touched, and since 27 October
it runs paid-only billing. Copying the files would have reverted that work.
Each change was therefore reviewed against `main` and ported only where `main`
lacked it. The source hashes in the two packages' `verification.json` files
describe their working tree, not what shipped.

## Shipped

- **Infrastructure tooling, as delivered:** `install_talklee.sh`,
  `scripts/installation/`, `scripts/database/`, `INSTALLATION.md`,
  `docs/production-readiness/database-recovery.md` and the handover example.
  Nothing runs automatically; the backup timers are opt-in operator units.
  `test_install.py` now puts `backend/` on the import path, because
  `prod_gate.py` imports the billing mode since 27 October.
- **`backend/Alembic/env.py`:** the DSN goes straight to asyncpg (TLS query
  options and percent-encoded passwords survive), and an optional
  `MIGRATION_DATABASE_URL` overrides `DATABASE_URL`. The row-security bypass
  for migrations is unchanged.
- **Signup (`signup.py`):** a mail-service exception or Redis write failure is
  the same 503 as a refused send, not a raw 500. An audit-log outage after the
  account commits no longer reports a failed signup.
- **Password recovery (`password_reset.py`):** a Redis or mail failure answers
  exactly like an unknown address. It used to return 500 for registered
  emails only, which revealed that the account existed.
- **Email-link callback:** the token is removed from the address bar, checked
  with `/auth/me` before it replaces any login, never used to create a
  profile, and refused in cookie-only mode in favour of normal sign-in.
  `getMe(token)` and `skipAuthRefresh` support that check.
- **Sign-in screens:** exactly six-digit codes, normalised emails, the
  server's normalised email adopted after a send, a "request a new code"
  action on recovery, and the login email prefilled from `?email=`.
- **Dashboard:** the "Prepare your first campaign" guide (step 1 reworded for
  paid-only plans), a retry button, and accessible loading and error states.
- **Voice picker:** a failed voice catalog shows an error with a retry
  instead of an empty list.

## Not taken, and why

- **Package `signup.py`:** it inserted the `free` plan with its minutes,
  removed the five-guess cap on the signup code and the per-email send cap,
  and changed the duplicate-email response. `main` keeps no plan and zero
  minutes, both caps, and its 409.
- **Package `password_reset.py`:** it removed the five-guess cap, the
  password-generation binding and the lockout reset. Its "consume the code
  first" step duplicated what the generation binding and the row lock
  already guarantee on `main`, and it would have burned the code when the
  database failed, which `main` deliberately allows the customer to retry.
- **Package `registration.py`:** `main` already retires `/auth/register` with
  a structured 410 that needs no request body.
- **Campaign wizard, campaign page, campaign form, lead fields, lead-details
  API:** `main` already has the guidance budget, duplicate-safe creation, a
  knowledge-upload failure path, a load retry and in-page start errors. The
  package's lead-field and wizard versions would have removed `main`'s
  lead-field picker.
- **Rejected inbound calls panel:** `main` has `GET /calls/rejected` and a
  working panel; the package's "history unavailable" version would have
  hidden real data. Inbound UI is outside this work's fence.
- **`http-client.ts` credential-exchange list and `auth-context.tsx`:** `main`
  already keeps login, signup and recovery failures away from refresh and
  global session expiry (`managesSession`), and no longer calls the retired
  register endpoint.

## Verification

- New backend tests `tests/unit/test_signup_recovery_delivery_failures.py`
  fail on `main`'s code (5 tests) and pass on the port; the seven
  `test_alembic_database_connection.py` tests fail on `main`'s `env.py` and
  pass on the port.
- Frontend: `auth-journey.test.tsx`, `credential-exchange.test.ts` and
  `voice-provider-picker.test.tsx` fail on `main`'s pages (11 of 15) and pass
  on the port.
- Installer tests 28 passed, recovery tool tests 23 passed.

Still not done, as both packages state: a clean Ubuntu installation, a
production-shaped restore, update and rollback rehearsals, real inbox
recovery on a device and a browser walkthrough.
