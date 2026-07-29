-- 2026-07-29: widen refresh_tokens.revoked_reason CHECK to cover the reasons
-- the application actually writes.
--
-- Idempotent + low-lock. Applied manually via psql on prod — there is NO
-- auto-runner (mirrors the rest of this directory).
--
-- ---------------------------------------------------------------------------
-- WHY
-- ---------------------------------------------------------------------------
-- The constraint was created inline with the table in
-- Alembic/versions/0002_add_refresh_tokens.py:36-38 and permits only:
--
--     ('rotated','reuse_detected','logout','admin','expired')
--
-- It has never been widened (confirmed against the live production DB:
--   refresh_tokens_revoked_reason_check ::
--     CHECK (revoked_reason IS NULL OR revoked_reason = ANY
--            (ARRAY['rotated','reuse_detected','logout','admin','expired'])) )
-- and is reproduced at database/schema/baseline_2026-06-02.sql:1187.
--
-- Meanwhile FOUR code paths write reasons the constraint forbids. Each
-- raises asyncpg CheckViolationError the moment the UPDATE actually matches
-- a row (i.e. whenever the user holds an active refresh token). There is no
-- global CheckViolationError handler, so each surfaces as an HTTP 500:
--
--  1. app/api/v1/endpoints/auth/password.py:134  reason="password_change"
--     Runs INSIDE `async with conn.transaction()` opened at password.py:100,
--     which also contains the `UPDATE user_profiles SET password_hash`.
--     The violation rolls the whole transaction back: the user gets a 500
--     AND their password is silently NOT changed. Worst of the four.
--
--  2. app/api/v1/endpoints/mfa/status.py:135    reason="mfa_disabled"
--     No enclosing transaction (only `pool.acquire()` at status.py:71), so
--     the preceding statements autocommit: MFA ends up DISABLED, sessions
--     revoked, but the refresh tokens survive and the caller sees a 500.
--     Security-relevant partial state.
--
--  3. app/core/security/refresh_tokens.py:149   'expired_with_subsequent_use'
--     Hardcoded in the SQL of the expired-token branch of
--     rotate_refresh_token(). This one fires DETERMINISTICALLY: the branch is
--     only reached after line 127 has already established the presented row
--     has revoked_at IS NULL, so the row always matches the UPDATE's
--     `WHERE family_id = $1 AND revoked_at IS NULL`. Every presentation of an
--     expired-but-unrevoked refresh token (any user idle past the 7-day TTL —
--     a routine event) returns 500 from POST /auth/refresh instead of the
--     intended clean 401 (auth/refresh.py:58-63 never runs).
--
--  4. app/api/v1/endpoints/auth/password_reset.py:164  "password_reset"
--     Already guarded: attempted in a SAVEPOINT with a "logout" fallback
--     (password_reset.py:151-179). This is the only path that currently
--     survives, and it degrades its audit label to do so.
--
-- Sibling calls to revoke_all_user_sessions(reason=...) — password.py:119,
-- mfa/status.py:130 — are NOT affected: they write security_sessions.
-- revoke_reason (baseline_2026-06-02.sql:1283), a plain TEXT column with no
-- CHECK constraint.
--
-- ---------------------------------------------------------------------------
-- THE PERMITTED SET
-- ---------------------------------------------------------------------------
-- Every literal that can reach refresh_tokens.revoked_reason from app/ was
-- enumerated (hardcoded SQL literals + the `reason=` kwarg of both revoke
-- helpers). The invariant is now pinned by
-- tests/security/test_refresh_revocation_reasons.py, which re-derives that
-- set from source and fails if it ever drifts from the list below.
--
--   ADDED (written by code, previously rejected):
--     password_change              auth/password.py:134
--     mfa_disabled                 mfa/status.py:135
--     expired_with_subsequent_use  core/security/refresh_tokens.py:149
--     password_reset               auth/password_reset.py:147
--
--   KEPT — in use today:
--     reuse_detected               core/security/refresh_tokens.py:114
--     logout                       auth/sessions.py:59, and the
--                                  password_reset.py:148 fallback; also the
--                                  default of revoke_family_by_token()
--                                  (refresh_tokens.py:207)
--
--   KEPT — not written by app/ today, deliberately NOT dropped:
--     rotated, admin, expired
--     Narrowing is a separate, riskier change: existing production rows may
--     already carry these values, which would make the ADD below fail on
--     validation, and 'admin' is the natural label for out-of-band operator
--     revocation via psql. Widening only.
--
-- ---------------------------------------------------------------------------
-- LOCKING
-- ---------------------------------------------------------------------------
-- ADD CONSTRAINT normally holds ACCESS EXCLUSIVE for a full table scan.
-- Adding it NOT VALID takes the lock only momentarily (catalog write, no
-- scan); VALIDATE CONSTRAINT then takes the far weaker SHARE UPDATE
-- EXCLUSIVE, which does not block reads or writes. Validation is guaranteed
-- to succeed because the new set is a strict superset of the old one, which
-- was enforced for every row already present.
--
-- lock_timeout keeps the brief ACCESS EXCLUSIVE grab from queueing behind a
-- long-running transaction and stalling all traffic to the table: if the
-- lock is not free within 3s the migration fails fast and can simply be
-- re-run (it is idempotent).

BEGIN;

SET LOCAL lock_timeout = '3s';

-- DROP IF EXISTS + ADD makes this re-runnable. The original constraint was
-- declared inline in CREATE TABLE, so Postgres auto-named it
-- <table>_<column>_check.
ALTER TABLE refresh_tokens
    DROP CONSTRAINT IF EXISTS refresh_tokens_revoked_reason_check;

ALTER TABLE refresh_tokens
    ADD CONSTRAINT refresh_tokens_revoked_reason_check
    CHECK (revoked_reason IS NULL OR revoked_reason = ANY (ARRAY[
        -- pre-existing
        'rotated'::text,
        'reuse_detected'::text,
        'logout'::text,
        'admin'::text,
        'expired'::text,
        -- added 2026-07-29 — written by code, previously rejected
        'password_change'::text,
        'password_reset'::text,
        'mfa_disabled'::text,
        'expired_with_subsequent_use'::text
    ]))
    NOT VALID;

COMMIT;

-- Separate transaction: takes only SHARE UPDATE EXCLUSIVE, safe to run
-- against live traffic. Flips the constraint from NOT VALID to validated so
-- it is enforced identically to an originally-valid constraint and shows up
-- as valid in \d refresh_tokens.
ALTER TABLE refresh_tokens
    VALIDATE CONSTRAINT refresh_tokens_revoked_reason_check;

COMMENT ON CONSTRAINT refresh_tokens_revoked_reason_check ON refresh_tokens IS
    'Permitted revoked_reason values. Widened 2026-07-29 to admit the '
    'reasons the application actually writes (password_change, '
    'password_reset, mfa_disabled, expired_with_subsequent_use); before '
    'that these raised CheckViolationError -> HTTP 500. Adding a new reason '
    'in app/ REQUIRES widening this list — enforced by '
    'tests/security/test_refresh_revocation_reasons.py.';


-- ===========================================================================
-- ROLLBACK / DOWN
-- ===========================================================================
-- Restores the original 5-value constraint exactly as
-- Alembic/versions/0002_add_refresh_tokens.py:36-38 created it.
--
-- WARNING: this is only safe once no deployed code writes any of the four
-- added values — i.e. roll the CODE back first, then run this. Rows written
-- while the wider constraint was live may already carry the new values;
-- ADD CONSTRAINT (or VALIDATE) will then FAIL. Clear them first if so:
--
--   UPDATE refresh_tokens SET revoked_reason = 'logout'
--    WHERE revoked_reason IN ('password_change','password_reset',
--                             'mfa_disabled','expired_with_subsequent_use');
--
-- BEGIN;
-- SET LOCAL lock_timeout = '3s';
-- ALTER TABLE refresh_tokens
--     DROP CONSTRAINT IF EXISTS refresh_tokens_revoked_reason_check;
-- ALTER TABLE refresh_tokens
--     ADD CONSTRAINT refresh_tokens_revoked_reason_check
--     CHECK (revoked_reason IS NULL OR revoked_reason = ANY (ARRAY[
--         'rotated'::text, 'reuse_detected'::text, 'logout'::text,
--         'admin'::text, 'expired'::text
--     ]))
--     NOT VALID;
-- COMMIT;
-- ALTER TABLE refresh_tokens
--     VALIDATE CONSTRAINT refresh_tokens_revoked_reason_check;
