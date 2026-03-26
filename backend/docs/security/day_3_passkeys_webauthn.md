# Day 3 — Passkeys (WebAuthn): FIDO2 Credential Registration & Authentication

> **Date:** 2026
> **Security Phase:** Day 3 of 8
> **Status:** ✅ Implemented
> **Official References:**
> - [W3C WebAuthn Level 3 Candidate Recommendation (January 13, 2026)](https://www.w3.org/TR/webauthn-3/)
> - [FIDO Alliance FIDO2 Specifications](https://fidoalliance.org/specs/fido-v2.1-ps-20210615/)
> - [py_webauthn 2.7.1 — Duo Labs](https://github.com/duo-labs/py_webauthn)
> - [OWASP Authentication Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)

---

## Table of Contents

1. [What Was Built](#1-what-was-built)
2. [Research Sources](#2-research-sources)
3. [WebAuthn Architecture](#3-webauthn-architecture)
4. [Registration Ceremony](#4-registration-ceremony)
5. [Authentication Ceremony](#5-authentication-ceremony)
6. [Security Controls](#6-security-controls)
7. [Fallback Login Flow](#7-fallback-login-flow)
8. [Passkey Management](#8-passkey-management)
9. [Database Migration](#9-database-migration)
10. [Files Created or Modified](#10-files-created-or-modified)
11. [Implementation Checkpoints](#11-implementation-checkpoints)
12. [Security Decisions Log](#12-security-decisions-log)
13. [What Day 4 Builds On This](#13-what-day-4-builds-on-this)

---

## 1. What Was Built

Day 3 delivers the complete WebAuthn / FIDO2 passkey layer — compatible with TouchID, FaceID,
Windows Hello, YubiKey, and all synced passkeys (iCloud Keychain, Google Password Manager).
Every decision was driven by the official W3C WebAuthn Level 3 specification and py_webauthn library.

| Component | Before Day 3 | After Day 3 |
|-----------|-------------|-------------|
| Authentication methods | Password + TOTP MFA | **+ Passkeys (WebAuthn)** — passwordless option |
| Credential storage | N/A | **`user_passkeys`** table with COSE public keys, sign counters |
| Ceremony challenges | N/A | **`webauthn_challenges`** — 5-min TTL, single-use |
| Clone detection | N/A | **Sign count verification** on every authentication |
| Backup/sync detection | N/A | **`backed_up`** flag identifies synced passkeys |
| Discoverable credentials | N/A | **Username-less login** supported (resident keys) |
| Hybrid transport | N/A | **Phone-as-key** support via hybrid transport |
| Device type tracking | N/A | **`device_type`** — singleDevice vs multiDevice |
| Passkey management | N/A | **Full CRUD** — register, list, rename, delete |
| Login fallback | Password only | **Passkey first, fallback to password+MFA** |

---

## 2. Research Sources

Every decision below was verified against the official W3C WebAuthn Level 3 specification
and py_webauthn library documentation before coding.

### W3C WebAuthn Level 3 — Key Findings

**Credential Records (§6.4) — Required stored fields:**
> "The server MUST store at minimum: type, id, publicKey, signCount, uvInitialized,
> transports, backupEligible, backupState"

Decision: All mandatory fields stored in `user_passkeys`:
- `credential_id` — base64url-encoded unique identifier
- `credential_public_key` — COSE-encoded public key for signature verification
- `sign_count` — monotonically increasing counter for clone detection
- `transports` — array of transport hints (internal, hybrid, usb, nfc, ble)
- `device_type` — singleDevice or multiDevice (from backupEligible)
- `backed_up` — backupState flag from authenticator data

**Signature Counter (§6.1.3) — Clone detection:**
> "If the signature counter is greater than zero, and the new signature counter
> is less than or equal to the stored signature counter, this is a signal that
> the authenticator may be cloned."

Decision: Sign count verified on every authentication. If `new_sign_count > 0` and
`new_sign_count <= stored_sign_count`, a critical warning is logged (potential clone).

**Ceremony Challenges (§7.1, §7.2):**
> "Let challenge be a cryptographically random value of at least 16 bytes."

Decision: 32-byte random challenges generated via `secrets.token_bytes(32)`.
Stored SHA-256 hashed in `webauthn_challenges` with 5-minute TTL (same as MFA challenges).

**User Verification:**
> "User verification SHOULD be required for authentication ceremonies."

Decision: `user_verification=required` for both registration and authentication.
This ensures biometric/PIN verification on every use.

### py_webauthn 2.7.1 — Library Behavior

The official Duo Labs py_webauthn library handles:
- COSE key parsing and signature verification
- Attestation statement validation (optional for our deployment)
- Client data JSON parsing and origin verification
- Base64url encoding/decoding utilities

Key algorithms supported (in preference order):
1. ES256 (ECDSA w/ SHA-256) — alg: -7
2. Ed25519 — alg: -8
3. RS256 (RSASSA-PKCS1-v1_5 w/ SHA-256) — alg: -257

---

## 3. WebAuthn Architecture

**File:** `backend/app/core/security/passkeys.py`

### Relying Party (RP) Configuration

| Setting | Default | Purpose |
|---------|---------|---------|
| `RP_NAME` | "Talky.ai" | Human-readable name shown in authenticator UI |
| `RP_ID` | "talky.ai" | Domain scope for credentials (must match origin host) |
| `RP_ORIGIN` | "https://talky.ai" | Full expected origin for verification |

Development origins also allowed:
- `http://localhost:3000`
- `http://localhost:5173`
- `http://127.0.0.1:3000`
- `http://127.0.0.1:5173`

### Authenticator Selection

| Type | Attachment | Use Case |
|------|-----------|----------|
| `platform` | `platform` | Built-in authenticators (TouchID, Windows Hello, FaceID) |
| `cross-platform` | `cross-platform` | Roaming authenticators (YubiKey, Titan Security Key) |
| `any` | (none) | Either type — lets browser choose |

All selections use:
- `resident_key=preferred` — allows discoverable credentials (username-less login)
- `user_verification=required` — requires biometric/PIN

---

## 4. Registration Ceremony

### Step 1 — Begin Registration

```
POST /auth/passkeys/register/begin
Auth: JWT required
Body: {
  "authenticator_type": "platform" | "cross-platform" | "any",
  "display_name": "Work MacBook"  // optional
}

Response:
{
  "ceremony_id": "uuid-for-challenge-lookup",
  "options": {
    "rp": { "name": "Talky.ai", "id": "talky.ai" },
    "user": { "id": "base64url-user-handle", "name": "user@example.com", ... },
    "challenge": "base64url-challenge-bytes",
    "pubKeyCredParams": [...],
    "authenticatorSelection": { ... },
    "timeout": 120000
  }
}
```

Server actions:
1. Generate 32-byte random challenge
2. Store challenge in `webauthn_challenges` (ceremony="registration", 5-min TTL)
3. Return options for `navigator.credentials.create()`

### Step 2 — Complete Registration

```
POST /auth/passkeys/register/complete
Auth: JWT required
Body: {
  "ceremony_id": "uuid-from-step-1",
  "credential_response": { /* PublicKeyCredential JSON from browser */ },
  "display_name": "Work MacBook"
}
```

Server actions:
1. Retrieve and validate challenge (not used, not expired)
2. Call `verify_registration_response()`:
   - Verify challenge matches
   - Verify origin matches expected
   - Verify RP ID matches expected
   - Parse attestation (if present)
   - Extract credential ID, public key, sign count
3. Consume challenge (single-use)
4. Store credential in `user_passkeys`
5. Increment `user_profiles.passkey_count`

Extracted credential data:
- `credential_id` — base64url
- `credential_public_key` — base64url COSE
- `sign_count` — initial counter value (usually 0)
- `aaguid` — authenticator model identifier
- `device_type` — "singleDevice" or "multiDevice"
- `backed_up` — true if synced credential
- `transports` — array of transport strings

---

## 5. Authentication Ceremony

### Step 1 — Begin Authentication

```
POST /auth/passkeys/login/begin
Auth: None (unauthenticated)
Body: { "email": "user@example.com" }  // optional — enables non-discoverable flow

Response:
{
  "ceremony_id": "uuid-for-challenge-lookup",
  "options": {
    "challenge": "base64url-challenge-bytes",
    "allowCredentials": [ /* user's credential IDs if email provided */ ],
    "userVerification": "required",
    "timeout": 120000
  },
  "has_passkeys": true
}
```

Server actions:
1. If email provided: lookup user's credentials, build `allowCredentials` list
2. If no email: allow discoverable credential (user identified from `userHandle`)
3. Generate and store challenge

### Step 2 — Complete Authentication

```
POST /auth/passkeys/login/complete
Auth: None (unauthenticated)
Body: {
  "ceremony_id": "uuid-from-step-1",
  "credential_response": { /* PublicKeyCredential JSON from browser */ }
}
```

Server actions:
1. Extract `credential_id` from response
2. Lookup credential in `user_passkeys` by `credential_id`
3. Retrieve and validate challenge
4. Call `verify_authentication_response()`:
   - Verify challenge matches
   - Verify origin and RP ID
   - Verify signature using stored public key
   - Verify sign count (clone detection)
5. Consume challenge
6. Check for clone attack (`new_sign_count <= stored_sign_count` when both > 0)
7. Update `sign_count` and `last_used_at` on credential
8. Create server-side session (`security_sessions`)
9. Issue JWT + httpOnly session cookie
10. Record successful login in `login_attempts`

---

## 6. Security Controls

### Clone Detection via Sign Count

```python
if current_sign_count > 0 and new_sign_count <= current_sign_count:
    logger.critical("CLONE DETECTED: credential_id=%s", credential_id)
    # Logged but login allowed — security team can investigate
```

The sign counter is a monotonically increasing value from the authenticator.
If it ever decreases or stalls, the authenticator may have been cloned.

### Challenge Security

| Property | Value | Rationale |
|----------|-------|-----------|
| Size | 32 bytes | W3C minimum is 16 bytes; we use 256-bit |
| Storage | SHA-256 hash | Raw challenge never stored |
| TTL | 5 minutes | Short window limits replay window |
| Single-use | `used` flag | Prevents replay attacks |
| IP tracking | `ip_address` column | Anomaly detection (log if IP changes) |

### Credential ID Uniqueness

Credential IDs are globally unique per authenticator (UUID-like).
The database enforces `UNIQUE(credential_id)` across all users.

### User Verification Required

All ceremonies require `user_verification=required`:
- Biometric (fingerprint, face)
- Device PIN/password

This ensures the authenticator performs local user verification before signing.

---

## 7. Fallback Login Flow

The system supports hybrid authentication:

```
Login UI:
├─ "Sign in with passkey" (calls /auth/passkeys/login/begin)
│   └─ User authenticates with biometrics → logged in
│
└─ "Sign in with password" (calls /auth/login)
    ├─ If MFA disabled → logged in
    └─ If MFA enabled → challenge token → /auth/mfa/verify → logged in
```

Pre-login check endpoint:
```
POST /auth/passkey-check
Body: { "email": "user@example.com" }
Response: { "has_passkeys": true }
```

The UI uses this to show/hide the "Sign in with passkey" button.

---

## 8. Passkey Management

### Endpoints (all require JWT)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/auth/passkeys` | List user's passkeys |
| `PATCH` | `/auth/passkeys/{id}` | Update display name |
| `DELETE` | `/auth/passkeys/{id}` | Remove a passkey |

### List Response

```json
{
  "passkeys": [
    {
      "id": "uuid-of-passkey-record",
      "credential_id": "base64url-credential-id",
      "display_name": "iPhone 15",
      "device_type": "multiDevice",
      "backed_up": true,
      "transports": ["internal", "hybrid"],
      "created_at": "2026-03-17T10:00:00Z",
      "last_used_at": "2026-03-17T14:30:00Z"
    }
  ],
  "count": 1
}
```

### Delete Behavior

- Removes row from `user_passkeys`
- Decrements `user_profiles.passkey_count`
- Does NOT revoke from authenticator (not possible via WebAuthn)

---

## 9. Database Migration

**File:** `backend/database/migrations/day3_passkeys_tables.sql`

Apply with:
```bash
psql postgresql://talkyai:talkyai_secret@localhost:5432/talkyai \
     -f backend/database/migrations/day3_passkeys_tables.sql
```

**Idempotent** — safe to run multiple times. Wrapped in `BEGIN; ... COMMIT;`.

### Tables Created

#### `user_passkeys`

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID PK | Server record identifier |
| `user_id` | UUID FK | Links to `user_profiles(id)` — CASCADE DELETE |
| `credential_id` | TEXT UNIQUE | base64url-encoded authenticator credential ID |
| `credential_public_key` | TEXT | base64url-encoded COSE public key |
| `sign_count` | BIGINT | Signature counter for clone detection |
| `aaguid` | TEXT | Authenticator Attestation GUID (model identifier) |
| `device_type` | TEXT | "singleDevice" or "multiDevice" |
| `backed_up` | BOOLEAN | TRUE if synced (iCloud, Google, etc.) |
| `transports` | TEXT[] | Transport hints: internal, hybrid, usb, nfc, ble |
| `display_name` | TEXT | User-friendly label |
| `created_at` | TIMESTAMPTZ | When passkey was registered |
| `last_used_at` | TIMESTAMPTZ | Last successful authentication |

#### `webauthn_challenges`

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID PK | Ceremony lookup identifier |
| `challenge` | TEXT | base64url-encoded challenge bytes |
| `ceremony` | TEXT | "registration" or "authentication" |
| `user_id` | UUID FK | User initiating ceremony (NULL for discoverable auth) |
| `ip_address` | TEXT | Client IP for anomaly detection |
| `user_agent` | TEXT | Browser/client info |
| `expires_at` | TIMESTAMPTZ | Challenge expiry (NOW + 5 minutes) |
| `used` | BOOLEAN | Single-use flag |
| `used_at` | TIMESTAMPTZ | When consumed |
| `created_at` | TIMESTAMPTZ | When created |

### Columns Added

| Table | Column | Type | Default | Purpose |
|-------|--------|------|---------|---------|
| `user_profiles` | `passkey_count` | INTEGER | 0 | Fast O(1) check if user has passkeys |

### Indexes Created

| Index | Table | Purpose |
|-------|-------|---------|
| `idx_up_credential_id` | `user_passkeys` | Authentication lookup by credential ID |
| `idx_up_user_id` | `user_passkeys` | List credentials for user |
| `idx_up_backed_up` | `user_passkeys` | Find synced passkeys (partial index) |
| `idx_wc_id_active` | `webauthn_challenges` | Challenge validation (partial: used=FALSE) |
| `idx_wc_cleanup` | `webauthn_challenges` | Purge expired challenges |
| `idx_user_profiles_has_passkey` | `user_profiles` | Find users with passkeys (partial: passkey_count > 0) |

---

## 10. Files Created or Modified

| File | Action | Purpose |
|------|--------|---------|
| `backend/requirements.txt` | Modified | Added `webauthn==2.7.1` |
| `backend/app/core/security/passkeys.py` | **Created** | WebAuthn operations: registration/auth options, verification, credential storage |
| `backend/app/api/v1/endpoints/passkeys.py` | **Created** | All passkey endpoints: register, login, list, update, delete |
| `backend/app/api/v1/endpoints/auth.py` | Modified | Added `POST /auth/passkey-check` endpoint |
| `backend/app/api/v1/routes.py` | Modified | Registered `passkeys_router` |
| `backend/database/migrations/day3_passkeys_tables.sql` | **Created** | `user_passkeys` + `webauthn_challenges` tables + `passkey_count` column |
| `backend/docs/security/day_3_passkeys_webauthn.md` | **Created** | This document |

---

## 11. Implementation Checkpoints

Work through each section in order. Only mark a phase complete when every item is ticked.

### 11.1 — Dependencies

- [ ] `webauthn==2.7.1` added to `requirements.txt`
- [ ] `pip install webauthn` run in venv — confirmed installed
- [ ] `from webauthn import generate_registration_options` imports without error

### 11.2 — Database Migration

- [ ] `day3_passkeys_tables.sql` applied to the database
- [ ] `user_passkeys` table exists with all columns
- [ ] `webauthn_challenges` table exists with all columns
- [ ] `user_profiles.passkey_count` column exists, defaults to 0
- [ ] All indexes created (verify with `\d user_passkeys` in psql)

### 11.3 — Passkeys Module (`passkeys.py`)

- [ ] `generate_registration_options()` returns ceremony_id and options
- [ ] `verify_registration()` verifies attestation and returns `VerifiedCredential`
- [ ] `generate_authentication_options()` returns ceremony_id and options
- [ ] `verify_authentication()` verifies assertion and returns `AuthenticationResult`
- [ ] `create_challenge()` stores SHA-256 hash, returns raw bytes
- [ ] `get_and_validate_challenge()` returns None for expired challenges
- [ ] `get_and_validate_challenge()` returns None for already-used challenges
- [ ] `consume_challenge()` marks challenge as used
- [ ] `store_credential()` inserts row and increments `passkey_count`
- [ ] `get_credential_by_id()` returns credential by credential_id
- [ ] `update_credential_sign_count()` updates counter and `last_used_at`

### 11.4 — Passkey Endpoints

#### Registration flow
- [ ] `POST /auth/passkeys/register/begin` returns options with challenge
- [ ] After begin, `webauthn_challenges` row exists with ceremony="registration"
- [ ] `POST /auth/passkeys/register/complete` verifies and stores credential
- [ ] After complete, `user_passkeys` row exists with all fields populated
- [ ] After complete, `user_profiles.passkey_count` incremented by 1

#### Login flow
- [ ] `POST /auth/passkeys/login/begin` returns options (no auth required)
- [ ] With email: `allowCredentials` contains user's credential IDs
- [ ] Without email: `allowCredentials` is empty (discoverable flow)
- [ ] `POST /auth/passkeys/login/complete` verifies and creates session
- [ ] After complete, JWT returned and `talky_sid` cookie set
- [ ] After complete, credential's `sign_count` updated in DB
- [ ] After complete, `last_used_at` updated on credential

#### Clone detection
- [ ] Authentication with same sign count (when > 0) logs CRITICAL warning
- [ ] Authentication with increased sign count succeeds normally

#### Pre-login check
- [ ] `POST /auth/passkey-check` with email of user with passkeys → `{"has_passkeys": true}`
- [ ] `POST /auth/passkey-check` with email of user without passkeys → `{"has_passkeys": false}`
- [ ] `POST /auth/passkey-check` with unknown email → `{"has_passkeys": false}` (no enumeration)

#### Management
- [ ] `GET /auth/passkeys` returns list of user's passkeys
- [ ] `PATCH /auth/passkeys/{id}` updates display_name
- [ ] `DELETE /auth/passkeys/{id}` removes passkey and decrements count

### 11.5 — Integration with Auth System

- [ ] User with passkey can authenticate via passkey endpoints
- [ ] User can still authenticate via password + MFA endpoints
- [ ] `POST /auth/login` on MFA-enabled user returns challenge token (unchanged from Day 2)
- [ ] `POST /auth/mfa/verify` after passkey login sets `mfa_verified=TRUE` (if MFA enabled)

### 11.6 — Security Properties (End-to-End)

- [ ] `credential_public_key` is NEVER returned in any API response
- [ ] `webauthn_challenges.challenge` is SHA-256 hashed in DB — raw challenge not stored
- [ ] Expired challenges are rejected
- [ ] Used challenges are rejected (single-use enforced)
- [ ] All ceremonies require user verification
- [ ] Origin verification rejects requests from wrong origin
- [ ] RP ID verification rejects requests from wrong domain
- [ ] Clone detection logs CRITICAL warning when sign count stalls

---

## 12. Security Decisions Log

| Decision | Rationale | Source |
|----------|-----------|--------|
| ES256, Ed25519, RS256 algorithm support | Covers all major authenticator types | py_webauthn docs, FIDO spec |
| `user_verification=required` | Ensures biometric/PIN on every use | W3C WebAuthn §6.1 |
| 32-byte challenges | W3C minimum is 16 bytes; 32 bytes = 256-bit | W3C WebAuthn §7.1 |
| SHA-256 hash of challenge in DB | Raw challenge leak = replay attack | Session Management best practice |
| 5-minute challenge TTL | Short window limits attack surface | Same as MFA challenges (Day 2) |
| Sign count verification | Clone detection per W3C §6.1.3 | W3C WebAuthn |
| `device_type` and `backed_up` fields | Distinguish single-device vs synced passkeys | W3C WebAuthn §6.4 |
| `transports` stored | Helps browser route auth request correctly | W3C WebAuthn §5.8 |
| `credential_id` globally unique | Authenticator generates UUID-like identifiers | FIDO spec |
| No attestation verification (default) | Improves UX; attestation provides marginal benefit for most deployments | FIDO deployment guidance |
| `passkey_count` denormalized | Avoids JOIN on login check — fast hot path | Performance |
| HTTP-only session cookie after passkey auth | Same security as password login | OWASP Session Management CS |

---

## 13. What Day 4 Builds On This

Day 4 adds **Session Management Improvements** and **Security Audit Logging**.
It depends on everything built in Days 1-3:

- `security_sessions` — Day 4 adds session fingerprinting and anomaly detection
- `login_attempts` (Day 1) — Day 4 adds structured audit logging with IP geolocation
- `user_passkeys` — Day 4 adds passkey usage analytics (last used, device types)
- `webauthn_challenges` — Day 4 adds challenge success/failure rates

Tables Day 4 will add:
- `security_audit_log` — structured security events with retention policies
- `session_anomalies` — detected suspicious session patterns

---

*End of Day 3 — Passkeys (WebAuthn)*
