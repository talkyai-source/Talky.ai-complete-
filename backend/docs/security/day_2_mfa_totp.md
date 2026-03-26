# Day 2 — MFA (TOTP): Google Authenticator & Recovery Codes

> **Date:** 2026
> **Security Phase:** Day 2 of 8
> **Status:** ✅ Implemented
> **OWASP References:**
> - [Multifactor Authentication Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Multifactor_Authentication_Cheat_Sheet.html)
> - [Authentication Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)
> - [RFC 6238 — TOTP: Time-Based One-Time Password Algorithm](https://tools.ietf.org/html/rfc6238)
> - [pyotp 2.9.0 — Official PyPI page & checklist](https://pypi.org/project/pyotp/)

---

## Table of Contents

1. [What Was Built](#1-what-was-built)
2. [Research Sources](#2-research-sources)
3. [TOTP Architecture](#3-totp-architecture)
4. [Secret Encryption at Rest](#4-secret-encryption-at-rest)
5. [Replay-Attack Prevention](#5-replay-attack-prevention)
6. [Two-Step Login Flow](#6-two-step-login-flow)
7. [Recovery Codes](#7-recovery-codes)
8. [MFA Management Endpoints](#8-mfa-management-endpoints)
9. [Database Migration](#9-database-migration)
10. [Files Created or Modified](#10-files-created-or-modified)
11. [Implementation Checkpoints](#11-implementation-checkpoints)
12. [Security Decisions Log](#12-security-decisions-log)
13. [What Day 3 Builds On This](#13-what-day-3-builds-on-this)

---

## 1. What Was Built

Day 2 delivers the complete TOTP MFA layer — compatible with Google Authenticator,
Authy, and every RFC 6238 compliant app. No assumptions were made. Every decision
was driven by live OWASP pages, the official RFC 6238 text, and the pyotp 2.9.0
PyPI checklist fetched directly before implementation.

| Component | Before Day 2 | After Day 2 |
|-----------|-------------|-------------|
| MFA method | None | **TOTP** (RFC 6238, Google Authenticator compatible) |
| TOTP secret storage | N/A | **Fernet encrypted** (AES-128-CBC + HMAC-SHA256) |
| Login flow | Single-step (password → JWT) | **Two-step** when MFA enabled (password → challenge → TOTP → JWT) |
| Replay prevention | None | **Per-slot tracking** via `user_mfa.last_used_at` |
| Recovery codes | None | **10 single-use codes**, 96-bit entropy, SHA-256 hashed |
| MFA challenge token | N/A | SHA-256 hashed, 5-min TTL, single-use |
| MFA enable/disable | N/A | Full enable / confirm / disable flow |
| Code regeneration | N/A | TOTP-authenticated recovery code regeneration |
| Session MFA flag | None | `security_sessions.mfa_verified` column |

---

## 2. Research Sources

Every decision below was verified against live official documentation before coding.

### OWASP Multifactor Authentication Cheat Sheet — Key Findings

**TOTP recommendation (direct quote):**
> "Provide the option for users to enable MFA on their accounts using TOTP."

**Recovery codes (direct quote):**
> "Providing the user with a number of single-use recovery codes when they first
> setup MFA."

**Disabling MFA reauthentication (direct quote):**
> "Require reauthentication with an existing enrolled factor before allowing changes."
> "Do not rely solely on the active session, as it may be hijacked."

**OTP handling (direct quote):**
> "OTP implementations SHOULD enforce a short time-to-live (TTL), ensure OTPs are
> single use, apply strict attempt limits, and invalidate the OTP on successful
> verification."

**OTP storage (direct quote):**
> "OTP implementations SHOULD NOT log OTP values or store OTPs in long-term
> plaintext form."

Decision: TOTP secrets are never logged. Only Fernet ciphertext is stored.
Raw codes (challenge tokens, recovery codes) are hashed before storage.

**SMS warning (direct quote):**
> "SMS messages... should not be used to protect applications that hold PII or
> where there is financial risk. NIST SP 800-63 does not allow these factors for
> applications containing PII."

Decision: SMS is not implemented. Only TOTP + recovery codes for Day 2.

---

### pyotp 2.9.0 Official PyPI Checklist — Key Findings

The official PyPI page (https://pypi.org/project/pyotp/) lists this mandatory checklist
for implementers:

| Requirement | Implementation |
|-------------|---------------|
| Ensure transport confidentiality by using HTTPS | Enforced by nginx/deployment |
| Ensure HOTP/TOTP secret confidentiality by storing secrets in a controlled access database | Fernet encryption + `TOTP_ENCRYPTION_KEY` env var |
| **Deny replay attacks by storing the most recently authenticated timestamp** | `user_mfa.last_used_at` — same time slot rejected |
| Throttle (rate limit) brute-force attempts | slowapi (IP) + `login_attempts` table (account) from Day 1 |

The replay prevention requirement is **explicitly mandatory** per the pyotp checklist.
`verify_totp_code()` in `totp.py` checks `is_replay_attack(last_used_at)` before
calling `pyotp.TOTP.verify()`.

---

### RFC 6238 §5.2 — Clock Skew

**From the RFC (direct):**
> "We RECOMMEND that at most one time step is allowed as the network delay."

The standard explicitly allows a ±1 step (±30 seconds) window to accommodate
clock drift. `valid_window=1` in `pyotp.TOTP.verify()` implements this exactly.

---

### Authgear — 5 Common TOTP Mistakes (2026 Article)

| Mistake | Our Fix |
|---------|---------|
| Clock drift — codes fail intermittently | `valid_window=1` (±30s accepted) |
| Base32 format error — all codes fail | `pyotp.random_base32()` always produces correct format |
| RFC 6238 parameter mismatch | digits=6, interval=30, SHA-1 (Google Authenticator defaults) |
| Bad provisioning URI | `pyotp.TOTP.provisioning_uri()` — the standard implementation |
| Weak verification logic — no replay protection | `is_replay_attack(last_used_at)` check before `pyotp.verify()` |

---

### SHA-1 Algorithm Choice

pyotp defaults to SHA-1 for TOTP. RFC 6238 defines SHA-1, SHA-256, and SHA-512
variants, but **Google Authenticator and Authy only support SHA-1** for the TOTP
algorithm. Using SHA-256 or SHA-512 would break compatibility with the most popular
authenticator apps. We use SHA-1 (the default) to maximise app compatibility.

The security of TOTP does not critically depend on SHA-1 collision resistance
because:
1. Codes are only 6 digits (verification is rate-limited, not brute-forced offline)
2. HMAC-SHA1 is distinct from raw SHA-1 collision resistance
3. RFC 6238 still specifies HMAC-SHA1 as the primary algorithm

---

## 3. TOTP Architecture

**File:** `backend/app/core/security/totp.py`

### Parameters

| Parameter | Value | Reason |
|-----------|-------|--------|
| Algorithm | HMAC-SHA1 | RFC 6238 default; required for Google Authenticator compat |
| Digits | 6 | Standard; Google Authenticator requires 6 |
| Interval | 30 seconds | RFC 6238 default |
| valid_window | 1 | ±30 s clock skew tolerance (RFC 6238 §5.2) |
| Secret length | 32 base32 chars | pyotp default = 160-bit entropy (os.urandom) |

### Key Functions

| Function | Purpose |
|----------|---------|
| `generate_totp_secret()` | Returns random base32 secret via `pyotp.random_base32()` |
| `encrypt_totp_secret(raw)` | Fernet-encrypts the secret for DB storage |
| `decrypt_totp_secret(enc)` | Decrypts the Fernet ciphertext from DB |
| `get_provisioning_uri(secret, email)` | Returns `otpauth://totp/...` URI |
| `generate_qr_code_data_uri(uri)` | Renders QR as `data:image/png;base64,...` |
| `is_replay_attack(last_used_at)` | True if current slot == last used slot |
| `verify_totp_code(secret, code, last_used_at)` | Full verify: normalise + replay + pyotp.verify |

### Verification Flow (Inside `verify_totp_code`)

```
Input: raw_secret, code, last_used_at

1. Normalise code: strip spaces, hyphens, whitespace
2. Reject if not exactly 6 digits
3. Replay check: int(now.timestamp()) // 30  ==  int(last_used_at.timestamp()) // 30
   → if same slot → return False (replay attack)
4. pyotp.TOTP(raw_secret).verify(code, valid_window=1)
   → constant-time comparison via utils.strings_equal()
5. If True → caller must UPDATE user_mfa SET last_used_at = NOW()
```

---

## 4. Secret Encryption at Rest

TOTP secrets are encrypted with **Fernet** (from the Python `cryptography` library
which is already in `requirements.txt`).

Fernet = AES-128-CBC + HMAC-SHA256 + timestamp. It provides:
- **Confidentiality**: AES-128-CBC encryption
- **Integrity**: HMAC-SHA256 — detects tampered ciphertext
- **Self-describing**: ciphertext includes a timestamp (useful for key rotation)

### Key Setup

```
# Generate once — add to .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Add to `.env`:
```
TOTP_ENCRYPTION_KEY=gAAAAABa...  (44-character URL-safe base64 Fernet key)
```

The key is loaded by `_get_fernet()` in `totp.py`. If the key is missing or
malformed, the function raises `RuntimeError` with a clear diagnostic message —
misconfiguration is caught immediately, not silently.

### What Is Stored in the Database

| Data | Storage | Notes |
|------|---------|-------|
| Raw TOTP base32 secret | **NEVER stored** | Only in memory during current call |
| Encrypted TOTP secret | `user_mfa.totp_secret_enc` | Fernet ciphertext, TEXT column |
| Raw challenge token | **NEVER stored** | Given to client once |
| Challenge token hash | `mfa_challenges.challenge_hash` | SHA-256 hex |
| Raw recovery codes | **NEVER stored** | Shown to user once |
| Recovery code hashes | `recovery_codes.code_hash` | SHA-256 hex |

---

## 5. Replay-Attack Prevention

**Requirement from pyotp official checklist:**
> "Deny replay attacks by rejecting one-time passwords that have been used by the
> client (this requires storing the most recently authenticated timestamp, OTP, or
> hash of the OTP in your database, and rejecting the OTP when a match is seen)."

### Problem

`pyotp.TOTP.verify()` is stateless. If a user enters a valid code and an attacker
intercepts it and immediately submits it again, pyotp would accept it twice within
the same 30-second window.

### Solution

```
user_mfa.last_used_at  TIMESTAMPTZ
```

After every successful TOTP verification:
```sql
UPDATE user_mfa SET last_used_at = NOW() WHERE user_id = $1
```

Before accepting any code:
```
current_slot = int(now.timestamp()) // 30
last_slot    = int(last_used_at.timestamp()) // 30

if current_slot == last_slot:
    REJECT — replay attack in same time window
```

This prevents replay within the same 30-second slot while still accepting valid
codes from adjacent windows (via `valid_window=1`).

---

## 6. Two-Step Login Flow

When a user has MFA enabled (`user_profiles.mfa_enabled = TRUE`), the login
process is split into two API calls.

### Step 1 — Password Verification

```
POST /auth/login
Body: { "email": "user@example.com", "password": "..." }

Password verified ✅ (all Day 1 checks: lockout, rehash, etc.)

IF user.mfa_enabled = TRUE:
  1. Create mfa_challenges row (SHA-256 hash stored, 5-min TTL)
  2. Return early — NO JWT, NO session cookie

  Response HTTP 200:
  {
    "access_token": "",          ← empty — not a valid JWT
    "mfa_required": true,
    "mfa_challenge_token": "raw_token_abc123...",
    "user_id": "...",
    "message": "MFA verification required. Use mfa_challenge_token with POST /auth/mfa/verify."
  }

IF user.mfa_enabled = FALSE:
  → Continue normal flow (create session, return JWT + cookie)  ← Day 1 behavior
```

### Step 2 — TOTP Verification

```
POST /auth/mfa/verify
Body: {
  "challenge_token": "raw_token_abc123...",
  "code": "123456"              ← TOTP from authenticator app
  // OR
  "recovery_code": "AbCdEfGh-IjKlMnOp"
}

Security checks:
  1. Resolve challenge: SHA-256(token) must exist, not used, not expired
  2. Load user, check is_active
  3. Load user_mfa, check enabled=TRUE
  4. Per-account lockout check (login_attempts)
  5. Verify TOTP code (or recovery code) — with replay prevention
  6. On success:
     - Consume challenge (used=TRUE)
     - UPDATE user_mfa SET last_used_at = NOW()
     - Create security_sessions row (mfa_verified=TRUE)
     - Record successful login in login_attempts
     - Issue full JWT + httpOnly session cookie

Response HTTP 200:
  {
    "access_token": "eyJ...",    ← full JWT
    "mfa_verified": true,
    "user_id": "...",
    ...
  }
  Set-Cookie: talky_sid=...; HttpOnly; Secure; SameSite=Strict
```

### Why No JWT Until After TOTP

If a JWT were issued after only password verification, an attacker who intercepts
the password (e.g. from a phishing page) would receive a usable token before MFA
can block them. The challenge token is meaningless for API access — it can only be
exchanged for a real JWT by also knowing the TOTP code.

### MFA Challenge Token Design

```
Client receives: raw_token  (secrets.token_urlsafe(32) = 256-bit)
Database stores: SHA-256(raw_token)  in mfa_challenges.challenge_hash

TTL: 5 minutes (expires_at = NOW() + 5 min)
Single-use: used = TRUE after consumption
```

Same principle as session tokens from Day 1 — the raw token is never stored.

---

## 7. Recovery Codes

**File:** `backend/app/core/security/recovery.py`

### Design

| Property | Value | Reason |
|----------|-------|--------|
| Count | 10 per setup | Matches GitHub, Google Workspace, AWS Cognito |
| Entropy | 96 bits each | `secrets.token_urlsafe(12)` = 12 bytes |
| Format | `AbCdEfGh-IjKlMnOp` | Split for readability |
| Storage | SHA-256 hash only | Raw code shown once, never recoverable |
| Reuse | Single-use | OWASP: codes must be invalidated after use |

### Recovery Code Lifecycle

```
POST /auth/mfa/confirm  (first confirmation)
  → generate_recovery_codes()   → 10 raw codes (secrets.token_urlsafe(12))
  → store_recovery_codes()      → SHA-256(each code) stored in recovery_codes table
  → Return raw codes to user    ← ONLY TIME SHOWN — user must save them

POST /auth/mfa/verify  (with recovery_code instead of code)
  → verify_and_consume_recovery_code()
    1. Normalise (strip hyphens/spaces)
    2. SHA-256(normalised)
    3. SELECT id FROM recovery_codes WHERE user_id=$1 AND code_hash=$2 AND used=FALSE
    4. If found: UPDATE SET used=TRUE, used_at=NOW()
    5. Race-condition guard: check exactly 1 row updated

POST /auth/mfa/disable
  → invalidate_all_codes()  → DELETE FROM recovery_codes WHERE user_id=$1

POST /auth/mfa/recovery-codes/regenerate  (requires valid TOTP)
  → invalidate_all_codes()      → delete all existing codes
  → generate_recovery_codes()   → fresh batch
  → store_recovery_codes()      → new hashes in DB
  → Return new raw codes        ← ONLY TIME SHOWN
```

### Recovery Code Table Schema

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID PK | Primary key |
| `user_id` | UUID FK | Links to `user_profiles`, CASCADE DELETE |
| `code_hash` | TEXT UNIQUE | SHA-256 hex of the raw code |
| `batch_id` | UUID | Groups codes from one generation |
| `used` | BOOLEAN | FALSE = available, TRUE = consumed |
| `used_at` | TIMESTAMPTZ | When the code was consumed |
| `created_at` | TIMESTAMPTZ | When the code was generated |

---

## 8. MFA Management Endpoints

All management endpoints require a full auth JWT (`get_current_user` dependency).

### Endpoint Reference

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| `POST` | `/auth/mfa/setup` | JWT required | Generate secret + QR code |
| `POST` | `/auth/mfa/confirm` | JWT required | Verify first TOTP code, activate MFA, get recovery codes |
| `POST` | `/auth/mfa/verify` | Challenge token | Step-2 login: verify TOTP → issue full JWT |
| `GET` | `/auth/mfa/status` | JWT required | Check MFA enabled status |
| `POST` | `/auth/mfa/disable` | JWT + password | Disable MFA (reauthentication required) |
| `POST` | `/auth/mfa/recovery-codes/regenerate` | JWT + TOTP code | Replace all recovery codes |

### POST /auth/mfa/setup

```
Requires: valid JWT

Actions:
  1. Generate TOTP secret (pyotp.random_base32() = 160-bit)
  2. Encrypt with Fernet (TOTP_ENCRYPTION_KEY)
  3. Upsert into user_mfa (enabled=FALSE — not yet confirmed)
  4. Set user_profiles.mfa_enabled = FALSE
  5. Delete any stale recovery codes

Response:
  {
    "provisioning_uri": "otpauth://totp/Talky.ai:user%40example.com?secret=...&issuer=Talky.ai",
    "qr_code": "data:image/png;base64,...",    ← embed in <img src="...">
    "issuer": "Talky.ai",
    "account": "user@example.com"
  }
```

The QR code is generated locally (no external HTTP call). The `qrcode` library
renders the provisioning URI as a PNG, which is returned as a base64 data URI.

### POST /auth/mfa/confirm

```
Requires: valid JWT
Body: { "code": "123456" }

Actions:
  1. Load user_mfa row (must exist, enabled=FALSE)
  2. Decrypt TOTP secret
  3. Verify code with replay prevention
  4. SET enabled=TRUE, verified_at=NOW(), last_used_at=NOW()
  5. SET user_profiles.mfa_enabled = TRUE
  6. Generate 10 recovery codes, store hashes

Response (ONCE):
  {
    "enabled": true,
    "recovery_codes": ["AbCdEfGh-IjKlMnOp", ...],   ← 10 codes, shown once
    "recovery_codes_count": 10,
    "message": "MFA activated. Save these recovery codes..."
  }
```

### POST /auth/mfa/disable

```
Requires: valid JWT + current password
Body: { "password": "current_password" }

OWASP: "Require reauthentication with an existing enrolled factor before
        allowing changes."

Actions:
  1. Verify current password (constant-time)
  2. Check user_mfa.enabled = TRUE
  3. SET user_mfa.enabled=FALSE, verified_at=NULL, last_used_at=NULL
  4. SET user_profiles.mfa_enabled = FALSE
  5. DELETE all recovery codes for user

Response: { "detail": "MFA disabled. All recovery codes deleted." }
```

### GET /auth/mfa/status

```
Requires: valid JWT

Response:
  {
    "enabled": true,
    "verified_at": "2026-03-16T10:00:00Z",
    "recovery_codes_remaining": 8
  }
```

### POST /auth/mfa/recovery-codes/regenerate

```
Requires: valid JWT + valid current TOTP code
Body: { "code": "123456" }

Actions:
  1. Load and verify TOTP code (with replay prevention)
  2. DELETE all existing recovery codes
  3. Generate fresh 10-code batch
  4. Store new hashes

Response (ONCE):
  {
    "recovery_codes": ["AbCdEfGh-IjKlMnOp", ...],
    "recovery_codes_count": 10,
    "message": "Recovery codes regenerated. All previous codes are now invalid."
  }
```

---

## 9. Database Migration

**File:** `backend/database/migrations/day2_mfa_tables.sql`

Apply with:
```
psql postgresql://talkyai:talkyai_secret@localhost:5432/talkyai \
     -f backend/database/migrations/day2_mfa_tables.sql
```

**Idempotent** — safe to run multiple times. Wrapped in `BEGIN; ... COMMIT;`.

### Tables Created

| Table | Purpose |
|-------|---------|
| `user_mfa` | Encrypted TOTP secret + MFA state per user |
| `recovery_codes` | Single-use backup code hashes |
| `mfa_challenges` | Ephemeral 5-min tokens for two-step login |

### Columns Added

| Table | Column | Type | Default | Purpose |
|-------|--------|------|---------|---------|
| `user_profiles` | `mfa_enabled` | BOOLEAN | FALSE | Fast MFA status lookup at login |
| `security_sessions` | `mfa_verified` | BOOLEAN | FALSE | Whether session completed MFA |

### Key Indexes Created

| Index | Table | Covers |
|-------|-------|--------|
| `idx_user_mfa_user_id` | `user_mfa` | Fast lookup by user |
| `idx_user_mfa_enabled` | `user_mfa` | Active MFA users (partial) |
| `idx_rc_user_unused` | `recovery_codes` | Unused codes per user (partial) |
| `idx_rc_code_hash` | `recovery_codes` | Code hash lookup during verify |
| `idx_mc_hash_active` | `mfa_challenges` | Challenge token validation (partial) |
| `idx_mc_cleanup` | `mfa_challenges` | Purge job for expired challenges |
| `idx_user_profiles_mfa_enabled` | `user_profiles` | MFA reporting (partial) |
| `idx_ss_mfa_verified` | `security_sessions` | MFA-complete sessions (partial) |

### Database Constraints

| Constraint | Table | Rule |
|------------|-------|------|
| `chk_user_mfa_verified_before_enabled` | `user_mfa` | `enabled=TRUE` requires `verified_at IS NOT NULL` |
| `chk_recovery_codes_used_at` | `recovery_codes` | `used=TRUE` requires `used_at IS NOT NULL` |
| `chk_mfa_challenge_used_at` | `mfa_challenges` | `used=TRUE` requires `used_at IS NOT NULL` |
| `chk_mfa_challenge_expires_after_created` | `mfa_challenges` | `expires_at > created_at` |

---

## 10. Files Created or Modified

| File | Action | Purpose |
|------|--------|---------|
| `backend/requirements.txt` | Modified | Added `pyotp==2.9.0`, `qrcode==8.0`, `Pillow>=10.0.0` |
| `backend/app/core/security/totp.py` | **Created** | TOTP ops: generate, encrypt/decrypt, QR, verify + replay prevention |
| `backend/app/core/security/recovery.py` | **Created** | Recovery code generate, hash, store, verify-and-consume |
| `backend/app/api/v1/endpoints/mfa.py` | **Created** | All MFA endpoints + challenge create/resolve/consume helpers |
| `backend/database/migrations/day2_mfa_tables.sql` | **Created** | `user_mfa` + `recovery_codes` + `mfa_challenges` + column additions |
| `backend/app/api/v1/endpoints/auth.py` | Modified | Login flow: MFA check + challenge token issuance |
| `backend/app/api/v1/routes.py` | Modified | Register `mfa_router` |
| `backend/docs/security/day_2_mfa_totp.md` | **Created** | This document |

---

## 11. Implementation Checkpoints

Work through each section in order. Only mark a phase complete when every item is ticked.

---

### 11.1 — Environment & Dependencies

- [ ] `pyotp==2.9.0` added to `requirements.txt`
- [ ] `qrcode==8.0` added to `requirements.txt`
- [ ] `Pillow>=10.0.0` added to `requirements.txt`
- [ ] `pip install pyotp qrcode Pillow` run in venv — confirmed installed
- [ ] `TOTP_ENCRYPTION_KEY` generated and added to `.env`
- [ ] `TOTP_ENCRYPTION_KEY` verified as a valid 44-char Fernet key (not empty)
- [ ] `from cryptography.fernet import Fernet; Fernet(key)` — no exception
- [ ] `TOTP_ISSUER_NAME` set in `.env` (optional, defaults to "Talky.ai")

---

### 11.2 — Database Migration

- [ ] `day2_mfa_tables.sql` applied to the database
- [ ] `user_mfa` table exists with all columns
- [ ] `recovery_codes` table exists with all columns
- [ ] `mfa_challenges` table exists with all columns
- [ ] `user_profiles.mfa_enabled` column exists, defaults to FALSE
- [ ] `security_sessions.mfa_verified` column exists, defaults to FALSE
- [ ] All indexes created (verify with `\d user_mfa` in psql)
- [ ] All CHECK constraints present (verify with `\d+ user_mfa`)
- [ ] `user_mfa.chk_user_mfa_verified_before_enabled` works: INSERT enabled=TRUE without verified_at → ERROR

---

### 11.3 — TOTP Module (`totp.py`)

- [ ] `generate_totp_secret()` returns a 32-char base32 string
- [ ] `encrypt_totp_secret(secret)` returns a non-empty Fernet ciphertext string
- [ ] `decrypt_totp_secret(ciphertext)` returns the original secret
- [ ] `decrypt_totp_secret("garbage")` raises RuntimeError
- [ ] `get_provisioning_uri(secret, "user@test.com")` returns string starting with `otpauth://totp/`
- [ ] `generate_qr_code_data_uri(uri)` returns string starting with `data:image/png;base64,`
- [ ] `verify_totp_code(secret, valid_code)` returns True
- [ ] `verify_totp_code(secret, "000000")` returns False (invalid code)
- [ ] `verify_totp_code(secret, valid_code, last_used_at=datetime.now())` returns False (replay)
- [ ] `verify_totp_code(secret, valid_code, last_used_at=old_time)` returns True (different slot)
- [ ] `is_replay_attack(None)` returns False (no previous use)
- [ ] `is_replay_attack(datetime.now(utc))` returns True (same slot)

---

### 11.4 — Recovery Code Module (`recovery.py`)

- [ ] `generate_recovery_codes()` returns a list of 10 strings
- [ ] Each code is approximately 16 characters (URL-safe base64, 12 bytes)
- [ ] `format_recovery_code(code)` inserts a hyphen at the midpoint
- [ ] `store_recovery_codes(conn, user_id, codes)` inserts 10 rows in recovery_codes
- [ ] Stored rows have `used=FALSE`, `code_hash` is SHA-256 hex (64 chars)
- [ ] `count_remaining_codes(conn, user_id)` returns 10 after store
- [ ] `verify_and_consume_recovery_code(conn, user_id, raw_code)` returns True for valid unused code
- [ ] After consumption, `used=TRUE` and `used_at` is set in DB
- [ ] `verify_and_consume_recovery_code` returns False for the same code a second time
- [ ] `verify_and_consume_recovery_code` returns False for unknown code
- [ ] `verify_and_consume_recovery_code` handles hyphenated format correctly
- [ ] `invalidate_all_codes(conn, user_id)` deletes all rows for that user

---

### 11.5 — MFA Endpoints

#### Setup flow
- [ ] `POST /auth/mfa/setup` returns 200 with `provisioning_uri`, `qr_code`, `issuer`, `account`
- [ ] `provisioning_uri` starts with `otpauth://totp/Talky.ai:`
- [ ] `qr_code` starts with `data:image/png;base64,`
- [ ] After setup, `user_mfa` row exists in DB with `enabled=FALSE`
- [ ] After setup, `user_profiles.mfa_enabled = FALSE` confirmed

#### Confirm flow
- [ ] `POST /auth/mfa/confirm` with wrong code → 400 "Invalid TOTP code"
- [ ] `POST /auth/mfa/confirm` with valid code → 200 with `recovery_codes` list (10 items)
- [ ] After confirm, `user_mfa.enabled = TRUE` in DB
- [ ] After confirm, `user_mfa.verified_at` is set in DB
- [ ] After confirm, `user_profiles.mfa_enabled = TRUE` in DB
- [ ] After confirm, `recovery_codes` table has 10 rows for user
- [ ] `POST /auth/mfa/confirm` a second time (already enabled) → 400 "MFA is already active"

#### Two-step login flow
- [ ] `POST /auth/login` with MFA-enabled account → 200 with `mfa_required=true` and `mfa_challenge_token`
- [ ] `POST /auth/login` response has empty `access_token` (not a usable JWT)
- [ ] `POST /auth/login` does NOT set `talky_sid` cookie when MFA is pending
- [ ] `POST /auth/mfa/verify` with valid challenge token + valid TOTP → 200 with full `access_token`
- [ ] `POST /auth/mfa/verify` response sets `talky_sid` httpOnly cookie
- [ ] `POST /auth/mfa/verify` with expired challenge token → 401
- [ ] `POST /auth/mfa/verify` with used challenge token → 401 (single-use)
- [ ] `POST /auth/mfa/verify` with valid challenge + wrong TOTP → 401 `_GENERIC_MFA_ERROR`
- [ ] `POST /auth/mfa/verify` with valid challenge + valid recovery_code → 200
- [ ] After verify, `security_sessions.mfa_verified = TRUE` in DB
- [ ] After verify, `mfa_challenges.used = TRUE` in DB
- [ ] After verify, `user_mfa.last_used_at` is updated in DB
- [ ] Replay: same TOTP code submitted twice within 30 seconds → second attempt returns 401

#### Status endpoint
- [ ] `GET /auth/mfa/status` returns `enabled=false` before setup
- [ ] `GET /auth/mfa/status` returns `enabled=true` after confirm
- [ ] `GET /auth/mfa/status` returns `recovery_codes_remaining=10` after confirm
- [ ] `GET /auth/mfa/status` returns `recovery_codes_remaining=9` after consuming one code

#### Disable flow
- [ ] `POST /auth/mfa/disable` with wrong password → 401 "Current password is incorrect."
- [ ] `POST /auth/mfa/disable` with correct password → 200
- [ ] After disable, `user_mfa.enabled = FALSE` in DB
- [ ] After disable, `user_profiles.mfa_enabled = FALSE` in DB
- [ ] After disable, `recovery_codes` table has 0 rows for that user
- [ ] `POST /auth/login` after disable → normal single-step flow (no MFA challenge)

#### Recovery code regeneration
- [ ] `POST /auth/mfa/recovery-codes/regenerate` with wrong TOTP → 401
- [ ] `POST /auth/mfa/recovery-codes/regenerate` with valid TOTP → 200 with 10 new codes
- [ ] After regenerate, old codes no longer work in `POST /auth/mfa/verify`
- [ ] After regenerate, `recovery_codes` table has 10 new rows with different hashes
- [ ] `POST /auth/mfa/recovery-codes/regenerate` updates `user_mfa.last_used_at`

---

### 11.6 — Security Properties (End-to-End)

- [ ] TOTP secret is NEVER returned in any API response
- [ ] TOTP secret is NEVER logged (check application logs after setup + verify)
- [ ] Raw recovery codes are only returned once (on confirm / regenerate) and never again
- [ ] Raw challenge token is only in the response body — never in a cookie or DB
- [ ] `mfa_challenges.challenge_hash` is SHA-256 hex (64 chars) — not the raw token
- [ ] `recovery_codes.code_hash` is SHA-256 hex — not the raw code
- [ ] All MFA failure responses return exactly `"MFA verification failed."` regardless of reason
- [ ] `TOTP_ENCRYPTION_KEY` not present in any source file or git history
- [ ] Decrypting `user_mfa.totp_secret_enc` with a wrong key raises RuntimeError (not silent fail)

---

## 12. Security Decisions Log

| Decision | Rationale | Source |
|----------|-----------|--------|
| TOTP over SMS | OWASP explicitly warns SMS is unsuitable for PII/financial apps; NIST 800-63 disallows it | OWASP MFA CS |
| SHA-1 for TOTP algorithm | RFC 6238 default; Google Authenticator and Authy do NOT support SHA-256/SHA-512 TOTP variants | RFC 6238, Authgear 2026 |
| `valid_window=1` (±30 s) | RFC 6238 §5.2 recommends at most one step window for network delay | RFC 6238, pyotp docs |
| Replay prevention via `last_used_at` | pyotp official checklist mandates tracking last authenticated timestamp | pyotp 2.9.0 PyPI page |
| Fernet for secret encryption | Already in `cryptography` (Day 1 dep); AES-128-CBC + HMAC-SHA256 = confidentiality + integrity | cryptography docs |
| `TOTP_ENCRYPTION_KEY` in env (not DB) | If DB is compromised and key is also in DB, encryption is useless | Key management best practice |
| SHA-256 hash for challenge tokens | Same principle as session tokens (Day 1) — raw token leak from DB unusable | OWASP Session Management CS |
| 5-minute challenge token TTL | Short enough to limit window of theft; long enough for slow networks | OWASP MFA CS: "enforce short TTL" |
| Single-use challenge token | OWASP: "ensure OTPs are single use, invalidate on successful verification" | OWASP MFA CS |
| 10 recovery codes, 96-bit entropy | Matches GitHub / Google Workspace / AWS Cognito standards | Industry standard |
| SHA-256 for recovery code storage | Raw codes must never be recoverable from DB (same as session tokens) | OWASP MFA CS |
| Require password to disable MFA | OWASP: "require reauthentication with an existing factor before allowing changes" | OWASP MFA CS |
| Require TOTP to regenerate recovery codes | Prevents attacker with stolen session from harvesting fresh codes | OWASP MFA CS |
| Challenge token in response body (not cookie) | Cookie can't be read by JS to extract and send in step-2 body; body is consumed programmatically | HTTP design |
| No JWT issued until both factors verified | Password alone is worthless if JWT is issued pre-TOTP — attacker intercepts password then uses JWT | Security principle |
| `mfa_verified` on `security_sessions` | Enables future step-up authentication for sensitive endpoints without re-querying `user_mfa` | Security Layer Plan Day 5 |
| `mfa_enabled` denorm on `user_profiles` | Avoids JOIN on `user_mfa` on every login — fast hot path check | Performance + correctness |

---

## 13. What Day 3 Builds On This

Day 3 adds **RBAC + Tenant Isolation**.
It depends on everything built in Days 1 and 2:

- `security_sessions.mfa_verified` — Day 3 tenant isolation middleware can enforce
  that admin actions require `mfa_verified=TRUE`
- `user_profiles.mfa_enabled` — RBAC rules can require MFA for `platform_admin` and
  `partner_admin` roles before granting elevated permissions
- `login_attempts` table (Day 1) — already tracks MFA failures (`failure_reason="mfa_failed"`)
  which Day 3 can expose in audit dashboards per tenant
- `user_mfa.last_used_at` — available for risk-based authentication decisions in Day 3+

Tables Day 3 will add:
- `roles` — role definitions with hierarchy
- `permissions` — granular action permissions per role
- Tenant middleware updates — inject and validate tenant context on every request

---

*End of Day 2 — MFA (TOTP)*
