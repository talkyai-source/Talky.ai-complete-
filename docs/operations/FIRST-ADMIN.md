# First administrator on a new installation

`backend/scripts/bootstrap_first_admin.py` creates the first platform administrator. It is an operator command for a newly migrated database, not a public signup route, account promotion, password reset or MFA recovery tool. It refuses to run if **any** platform administrator already exists, including an inactive one, or if the requested email already belongs to any user. Use the normal Admin and recovery workflows for an existing installation.

The command creates no tenant, subscription, session, API key or default password. It does not send email or start workers. A successfully created account already has MFA enabled: password login issues a challenge, and the normal MFA endpoint must accept a fresh authenticator code before issuing a session.

## Prerequisites

- A reviewed Linux checkout and its installed backend Python dependencies; database migrations must match that checkout's current Alembic heads.
- A direct PostgreSQL connection using `sslmode=verify-full` with the database CA. Log in directly as the migration owner of `public.user_profiles`, with `NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION` and no other role memberships; runtime credentials and `SET ROLE` are refused. No roles or grants are changed by this command.
- The application's protected environment file containing its **existing** `TOTP_ENCRYPTION_KEY` and audit signing key (`AUDIT_LOG_SIGNING_KEY`, `APP_AUDIT_LOG_SIGNING_KEY`, or the application's JWT/secret-key fallback). Use the same keys as the API. Do not generate a different encryption key for bootstrap, especially when restoring data.
- A private interactive terminal, an independently verified operator-owned mailbox, and a unique password satisfying the application policy (8–128 characters with MFA). The explicit mailbox attestation is the initial operator identity check; this command does not prove inbox access by sending email.
- A **fresh cryptographically random** base32 TOTP secret, at least 32 uppercase characters, privately generated and already imported into the operator's authenticator. Use the existing [TOTP generator](../../backend/app/core/security/totp.py) (`generate_totp_secret`) or an approved local password-manager/authenticator workflow. Configure 6 digits, SHA-1, 30 seconds. Do not copy example secrets, reuse another account's factor, paste a secret into shell arguments, record the terminal, or put secrets in a ticket or CI log. Bootstrap verifies a code from this enrolled factor; it does not generate or display the secret.

## Check and create

Store the migration connection URL in a root/operator-owned regular file, mode `0600`, outside the checkout. Its shape is `postgresql://OWNER:PERCENT_ENCODED_PASSWORD@DB_HOST:5432/DB_NAME?sslmode=verify-full&sslrootcert=/absolute/path/database-ca.crt`; a protected `passfile` URL parameter is also supported. Do not put the real URL on the command line. The application environment file may be `0640` for its trusted application group; group-write and all world permissions are refused. Symlinks are refused for these input files.

Use the actual installation paths and database/owner names. For the fresh Ubuntu profile:

```bash
sudo /opt/talky/backend/venv/bin/python /opt/talky/backend/scripts/bootstrap_first_admin.py \
  --env-file /opt/talky/backend/.env \
  --database-url-file /root/talklee-bootstrap-database-url \
  --expected-database talklee \
  --expected-owner talklee_migration \
  --email administrator@example.com \
  --name 'Your administrator name'
```

Confirm that the database name, role and mailbox are the intended real values. Without `--apply`, this is a read-only eligibility check. Then run the same command with:

```text
--apply --confirm-owned-email
```

The private prompts request the new password twice, the enrolled TOTP secret and the current code. Piped/noninteractive secret input is refused. Nothing is created unless the authenticator proof succeeds. The account, encrypted factor, consumed TOTP counter and existing-format chained audit receipt commit together. The shared Admin authority lock and a bounded table lock prevent competing bootstrap or account writes from creating a partial/duplicate first account.

Output contains only status, the new user ID and audit event ID; it never contains the password, secret, database URL, recovery codes or a login token. Keep those nonsecret IDs in the operator release record. A timeout or broken connection during commit can leave an uncertain outcome: rerun the read-only check and inspect the audit receipt privately before any recovery action. The command never overwrites or silently repairs an account.

## First sign-in and recovery codes

Wait for the authenticator's next code after bootstrap; the provisioning code is already consumed. Sign in to the deployed Admin portal with the chosen email/password and then the fresh code. Confirm the signed-in profile is the expected platform administrator.

Bootstrap does not print or pre-create recovery codes. After normal MFA sign-in, generate and securely retain them through the existing authenticated `POST /api/v1/auth/mfa/recovery-codes/regenerate` workflow using a fresh TOTP code. The existing [MFA recovery endpoint](../../backend/app/api/v1/endpoints/mfa/recovery.py) returns the codes once; use the application's trusted security-settings client or an approved private API client, and store them in the operator's password vault. This endpoint requires the real session and existing origin/CSRF policy; bootstrap does not bypass it. Confirm that recovery material is safely stored before relying on this account operationally.

Remove the temporary protected migration URL file after successful verification according to the operator's credential-handling policy. Retain the application's original encryption keys in its approved recovery store. A generated account alone does not establish fresh-host installer acceptance, provider readiness, external backups, a live deployment or paid-production approval.
