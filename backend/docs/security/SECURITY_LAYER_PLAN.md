# Talk-lee Security Layer Plan

> **Project:** Talky.ai / Talk-lee  
> **Document:** Security Layer — Complete Plan & Implementation Checkpoints  
> **Status:** 🔲 In Progress  
> **Last Updated:** 2026

---

## Table of Contents

1. [Purpose](#1-purpose)
2. [System Position](#2-system-position)
3. [What Security Must Cover](#3-what-security-must-cover)
4. [Coverage Priorities](#4-coverage-priorities)
5. [Authentication Protocols](#5-authentication-protocols)
6. [Authorization Model](#6-authorization-model)
7. [Session Security](#7-session-security)
8. [API Security](#8-api-security)
9. [Security Checks Before Call Session Start](#9-security-checks-before-call-session-start)
10. [Security Data / Tables](#10-security-data--tables)
11. [Required Endpoints / Services](#11-required-endpoints--services)
12. [Audit Logging Rules](#12-audit-logging-rules)
13. [Implementation Order & Checkpoints](#13-implementation-order--checkpoints)
14. [Acceptance Criteria](#14-acceptance-criteria)
15. [Developer Note](#15-developer-note)

---

## 1. Purpose

This document defines the complete **Security Layer** for Talk-lee.

The security layer sits **above the session layer** and protects:

- Platform access
- Tenant isolation
- APIs
- Call permissions
- Administrative control

Its job is simple:

> **Decide who is allowed, what they can do, and when the system must block them.**

---

## 2. System Position

```
┌────────────────────────────────────────────────────────┐
│               Talk-lee Security Layer                  │
│  Identity · Permissions · Policy · Rate Limits         │
│  ← Enforced BEFORE session or action is allowed →      │
└────────────────────────────────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────┐
│               Session / Application Layer              │
└────────────────────────────────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────┐
│         Media Path — NOT touched by security           │
│   OpenSIPS · Asterisk · C++ Voice Gateway              │
│   (Signaling and audio flow continues uninterrupted)   │
└────────────────────────────────────────────────────────┘
```

> ⚠️ The security layer must **not interfere with media flow**.  
> OpenSIPS, Asterisk, and the C++ voice gateway continue handling signaling and audio.  
> Security enforces identity, permissions, policy, and rate limits **before** a call session or protected platform action is allowed.

---

## 3. What Security Must Cover

| Area | Simple Meaning | Why It Matters |
|------|---------------|----------------|
| **Authentication** | Prove the person is really who they say they are | Prevents account takeover |
| **MFA / TOTP** | Second proof after password | Stolen passwords alone are not enough |
| **Passkeys** | Phishing-resistant login | Strongest modern user login |
| **Authorization** | Decide what each role can do | Partner must not act like platform admin |
| **Tenant Isolation** | One company cannot see another company's data | Critical for SaaS and white-label trust |
| **Session Security** | Control login sessions safely | Stops stale or stolen sessions |
| **API Security** | Protect backend endpoints | Stops unauthorized access and abuse |
| **Rate Limiting** | Too many requests/calls get blocked | Protects system stability |
| **Audit Logs** | Record who changed what | Required for trust and debugging |
| **Secrets Management** | Store keys/tokens safely | Protects infrastructure |
| **Suspension Controls** | Block partner or tenant instantly | Lets admin stop misuse quickly |
| **Voice Abuse Protection** | Stop call floods and exploit patterns | Protects telephony infrastructure |

---

## 4. Coverage Priorities

```
CRITICAL (Must ship before any production tenant onboarding)
─────────────────────────────────────────────────────────────
  ■ Authentication (password + sessions)
  ■ RBAC + Tenant Isolation
  ■ Session-start authorization checks
  ■ API authentication on every endpoint
  ■ Suspension controls (partner + tenant)

HIGH (Required before partner go-live)
─────────────────────────────────────────────────────────────
  ■ MFA / TOTP
  ■ API rate limiting
  ■ Audit logs
  ■ Voice abuse protection

IMPORTANT (Required for production hardening)
─────────────────────────────────────────────────────────────
  ■ Passkeys / WebAuthn
  ■ Secret rotation / storage hardening
  ■ Signed webhooks
  ■ Recovery codes
```

---

## 5. Authentication Protocols

- Support **password login** with **Argon2id** or bcrypt password hashing.
- Add **TOTP MFA** for `platform_admin`, `partner_admin`, and internal staff as the default second factor.
- Add **passkeys (WebAuthn / FIDO2)** for phishing-resistant login, especially for platform admins.
- Generate **one-time recovery codes** for account recovery.
- Do **not** use SMS as the primary second factor.

### Password Hashing

| Algorithm | Parameters | Notes |
|-----------|-----------|-------|
| Argon2id | memory=64MB, iterations=3, parallelism=4 | Preferred |
| bcrypt | cost=12 | Fallback if Argon2id unavailable |

### Token Strategy

| Token Type | Storage | Lifetime |
|-----------|---------|---------|
| Access token | Memory / httpOnly cookie | 15 minutes |
| Refresh token | httpOnly cookie (secure, sameSite=Strict) | 7 days |
| TOTP code | Not stored (verified on use) | 30 seconds |
| Recovery code | Hashed in DB | Single use |

---

## 6. Authorization Model

### Roles

| Role | Scope | Description |
|------|-------|-------------|
| `platform_admin` | Full system | Talky.ai internal staff. Can manage all partners and tenants. |
| `partner_admin` | Partner scope | Manages tenants under their partner. Cannot see other partners. |
| `tenant_admin` | Tenant scope | Manages users within their tenant. Cannot see other tenants. |
| `user` | Tenant scope | Standard end user. Limited to their own data. |
| `readonly` | Tenant scope | Read-only access. Cannot create or modify. |

### Per-Request Validation Checklist

Every protected request must validate in order:

1. ☐ **Who is the user?** — Valid session / token
2. ☐ **Which tenant or partner do they belong to?** — Tenant context resolved
3. ☐ **What role do they have?** — Role extracted from session
4. ☐ **Is the partner or tenant active?** — Not suspended or expired
5. ☐ **Is the requested action allowed for this role and tenant context?** — RBAC check passes

### Role Permission Matrix (Summary)

| Action | platform_admin | partner_admin | tenant_admin | user | readonly |
|--------|:-:|:-:|:-:|:-:|:-:|
| Manage all tenants | ✅ | ❌ | ❌ | ❌ | ❌ |
| Manage own partner's tenants | ✅ | ✅ | ❌ | ❌ | ❌ |
| Manage tenant users | ✅ | ✅ | ✅ | ❌ | ❌ |
| Start a call | ✅ | ✅ | ✅ | ✅ | ❌ |
| View audit logs | ✅ | ✅ (own) | ✅ (own) | ❌ | ✅ (own) |
| Suspend partner | ✅ | ❌ | ❌ | ❌ | ❌ |
| Suspend tenant | ✅ | ✅ (own) | ❌ | ❌ | ❌ |
| Change limits | ✅ | ✅ (own) | ❌ | ❌ | ❌ |

---

## 7. Session Security

- **Secure, httpOnly, sameSite** cookies or equivalent secure token handling.
- **Rotate session identifiers** at:
  - Login
  - MFA completion
  - Privilege elevation
- **Idle timeout** — session expires after N minutes of inactivity (default: 30 min).
- **Absolute session lifetime** — session always expires after max TTL (default: 12 hours).
- **Logout-all-sessions** capability for high-risk cases (account compromise response).
- **Server-side session revocation** — sessions stored server-side so revocation is immediate.
- Session tokens must be **cryptographically random** (min 128-bit entropy).

### Session Lifecycle

```
User Login ──► Issue Session Token (rotated)
                      │
              ┌───────▼───────┐
              │  Active       │──────────────────┐
              │  Session      │                  │
              └───────┬───────┘          Idle > 30min
                      │                          │
              MFA Complete                       ▼
                      │                  Auto-Expire
              ┌───────▼───────┐
              │  Rotate Token │
              └───────┬───────┘
                      │
              Privilege Elevation
                      │
              ┌───────▼───────┐
              │  Rotate Token │
              └───────────────┘
```

---

## 8. API Security

- **Authenticate every protected endpoint** — no anonymous access to data routes.
- **Validate tenant scope** on every request — inject and check tenant context at middleware level.
- **Rate-limit** by: user, tenant, partner, and IP.
- **Validate request payloads strictly** — reject unknown fields, enforce type and length constraints.
- **Use signed webhooks** for external billing events (HMAC-SHA256 signature on payload).
- Make all **state-changing payment and security actions idempotent** where possible.
- Enforce **HTTPS only** — reject plain HTTP in production.
- Set security headers on all responses:

| Header | Value |
|--------|-------|
| `Strict-Transport-Security` | `max-age=63072000; includeSubDomains` |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Content-Security-Policy` | Restrictive policy per page |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |

---

## 9. Security Checks Before Call Session Start

Before a session is created, the backend **must** check all of the following. If any check fails, the session is rejected with an appropriate error code.

| # | Check | Failure Response |
|---|-------|-----------------|
| 1 | Partner is active | `403 PARTNER_SUSPENDED` |
| 2 | Tenant is active | `403 TENANT_SUSPENDED` |
| 3 | Voice feature is enabled for this tenant | `403 FEATURE_DISABLED` |
| 4 | Tenant concurrency limit is not exceeded | `429 TENANT_CONCURRENCY_LIMIT` |
| 5 | Partner concurrency limit is not exceeded | `429 PARTNER_CONCURRENCY_LIMIT` |
| 6 | Per-minute call creation rate is not exceeded | `429 RATE_LIMIT_EXCEEDED` |
| 7 | No active abuse block or security hold exists | `403 SECURITY_HOLD` |

All failures must be **logged to `security_events`** with timestamp, tenant ID, partner ID, IP, and reason.

---

## 10. Security Data / Tables

Minimum backend tables or models to add or verify:

| Table | Purpose |
|-------|---------|
| `users` | User identity, credentials, status |
| `roles` | Role definitions and hierarchy |
| `permissions` | Granular action permissions per role |
| `sessions` | Active sessions with expiry and rotation tracking |
| `user_mfa` | TOTP secrets, status, last verified timestamp |
| `user_passkeys` | WebAuthn credential storage |
| `recovery_codes` | Hashed one-time recovery codes |
| `audit_log` | Immutable record of all security-relevant actions |
| `tenant_limits` | Concurrency, rate, and feature limits per tenant |
| `partner_limits` | Concurrency, rate, and billing limits per partner |
| `security_events` | Abuse attempts, failed checks, holds, blocks |

### Key Schema Notes

- `audit_log` must be **append-only** — no UPDATE or DELETE permissions on this table.
- `security_events` must index on `(tenant_id, created_at)` and `(partner_id, created_at)` for fast lookups.
- `user_mfa.totp_secret` must be **encrypted at rest**.
- `user_passkeys` must store credential ID, public key, sign count, and AAGUID per WebAuthn spec.

---

## 11. Required Endpoints / Services

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/auth/login` | Password login, returns session token |
| `POST` | `/auth/logout` | Invalidate current session |
| `POST` | `/auth/refresh` | Refresh access token using refresh token |
| `POST` | `/auth/mfa/enable` | Set up TOTP for authenticated user |
| `POST` | `/auth/mfa/verify` | Verify TOTP code during login |
| `POST` | `/auth/passkeys/register` | Begin WebAuthn registration ceremony |
| `POST` | `/auth/passkeys/verify` | Complete WebAuthn authentication ceremony |
| `POST` | `/security/suspend/partner` | Suspend a partner (platform_admin only) |
| `POST` | `/security/suspend/tenant` | Suspend a tenant (platform_admin, partner_admin) |
| `GET`  | `/security/audit` | Query audit log (role-scoped results) |

---

## 12. Audit Logging Rules

**Always log these actions** (no exceptions):

| Action | Log Fields |
|--------|-----------|
| Login success | user_id, ip, timestamp, user_agent |
| Login failure | attempted_email, ip, timestamp, reason |
| MFA enabled | user_id, timestamp, actor_id |
| MFA disabled | user_id, timestamp, actor_id |
| Passkey added | user_id, credential_id, timestamp |
| Passkey removed | user_id, credential_id, timestamp, actor_id |
| Partner suspension | partner_id, actor_id, timestamp, reason |
| Partner resume | partner_id, actor_id, timestamp |
| Tenant suspension | tenant_id, actor_id, timestamp, reason |
| Tenant resume | tenant_id, actor_id, timestamp |
| Limit changes | target_id, field, old_value, new_value, actor_id, timestamp |
| Permission changes | target_role, changed_by, timestamp |
| Billing-impacting actions | action, amount, actor_id, tenant_id, timestamp |

### Audit Log Rules

- Logs are **immutable** — no row may be edited or deleted.
- Every log entry must include: `id`, `action`, `actor_id`, `target_type`, `target_id`, `tenant_id`, `partner_id`, `ip_address`, `user_agent`, `created_at`, `metadata (JSON)`.
- Logs must be **queryable** by: actor, target, action type, date range, tenant, partner.

---

## 13. Implementation Order & Checkpoints

Work through each phase in order. Do not start the next phase until all checkpoints in the current phase are marked complete.

---

### Phase 1 — Authentication (Password + Sessions)

> Goal: Users can securely log in and out. Sessions are safe.

- [ ] `users` table created with hashed password field (Argon2id)
- [ ] `sessions` table created with token, expiry, user_id, rotation tracking
- [ ] `POST /auth/login` implemented and tested
- [ ] `POST /auth/logout` implemented and tested
- [ ] `POST /auth/refresh` implemented and tested
- [ ] Session rotation on login confirmed working
- [ ] httpOnly + secure + sameSite cookie attributes verified
- [ ] Idle timeout enforced (default 30 min)
- [ ] Absolute session lifetime enforced (default 12 hours)
- [ ] Password hashing with Argon2id verified in unit tests

**Phase 1 Complete:** ☐

---

### Phase 2 — MFA / Passkeys

> Goal: Admin and partner accounts require a second factor. Passkeys available for platform admins.

- [ ] `user_mfa` table created (TOTP secret encrypted at rest)
- [ ] `user_passkeys` table created (WebAuthn credential storage)
- [ ] `recovery_codes` table created (hashed single-use codes)
- [ ] `POST /auth/mfa/enable` implemented (TOTP setup flow)
- [ ] `POST /auth/mfa/verify` implemented (TOTP code check on login)
- [ ] `POST /auth/passkeys/register` implemented (WebAuthn registration ceremony)
- [ ] `POST /auth/passkeys/verify` implemented (WebAuthn authentication ceremony)
- [ ] Recovery code generation and verification working
- [ ] MFA enforced by default for `platform_admin` and `partner_admin` roles
- [ ] Session rotated after successful MFA completion
- [ ] SMS NOT used as primary MFA method confirmed

**Phase 2 Complete:** ☐

---

### Phase 3 — RBAC + Tenant Isolation

> Goal: Every request is scoped to the correct tenant. Roles enforce what each user can do.

- [ ] `roles` table created with all 5 defined roles
- [ ] `permissions` table created with granular actions
- [ ] Tenant middleware injects and validates tenant context on every request
- [ ] Per-request validation checklist (steps 1–5 from Section 6) implemented
- [ ] `platform_admin` can access all tenants confirmed
- [ ] `partner_admin` cannot access another partner's tenants confirmed
- [ ] `tenant_admin` cannot access another tenant's data confirmed
- [ ] `user` cannot access another user's data confirmed
- [ ] Cross-tenant data isolation tested with automated tests
- [ ] RBAC permission check helper/decorator created and used on all protected routes

**Phase 3 Complete:** ☐

---

### Phase 4 — API Rate Limiting

> Goal: Abusive callers and tenants are blocked before they harm the system.

- [ ] Rate limiter middleware integrated (per user, tenant, partner, IP)
- [ ] Call creation rate limit implemented (per-minute per tenant)
- [ ] Login attempt rate limit implemented (per IP + per account)
- [ ] API endpoint rate limits configured and tested
- [ ] `429` responses include `Retry-After` header
- [ ] Rate limit configuration externalized (not hardcoded)
- [ ] Rate limit events logged to `security_events`

**Phase 4 Complete:** ☐

---

### Phase 5 — Session-Start Authorization Checks

> Goal: No call session can start unless all 7 pre-session checks pass.

- [ ] Partner active check implemented
- [ ] Tenant active check implemented
- [ ] Voice feature enabled check implemented
- [ ] Tenant concurrency limit check implemented
- [ ] Partner concurrency limit check implemented
- [ ] Per-minute call creation rate check implemented
- [ ] Abuse block / security hold check implemented
- [ ] All 7 checks wired into call session creation path
- [ ] Each failed check returns correct error code (403 / 429)
- [ ] All failures logged to `security_events`
- [ ] Unit tests cover each failure path individually

**Phase 5 Complete:** ☐

---

### Phase 6 — Suspension Propagation

> Goal: Suspending a partner or tenant immediately blocks all their active and future calls.

- [ ] `POST /security/suspend/partner` implemented (platform_admin only)
- [ ] `POST /security/suspend/tenant` implemented (platform_admin, partner_admin)
- [ ] Suspension status cached / propagated to call session check within < 5 seconds
- [ ] Suspended partner: all active sessions invalidated
- [ ] Suspended tenant: all active sessions invalidated
- [ ] Resume endpoint implemented for both partner and tenant
- [ ] Suspension and resume events logged to `audit_log`
- [ ] Suspension blocks new login attempts (not just calls)

**Phase 6 Complete:** ☐

---

### Phase 7 — Audit Logs

> Goal: Every security-relevant action is permanently recorded and queryable.

- [ ] `audit_log` table created (append-only, no DELETE/UPDATE permissions)
- [ ] `security_events` table created with proper indexes
- [ ] All actions from Section 12 are being logged
- [ ] `GET /security/audit` endpoint implemented with role-scoped filtering
- [ ] Audit log query supports filters: actor, target, action type, date range, tenant, partner
- [ ] Audit log entries include all required fields (id, action, actor_id, target_type, target_id, tenant_id, partner_id, ip_address, user_agent, created_at, metadata)
- [ ] Confirmed no log entry can be deleted via application layer
- [ ] Audit log tested: login success entry verified
- [ ] Audit log tested: suspension entry verified
- [ ] Audit log tested: MFA change entry verified

**Phase 7 Complete:** ☐

---

### Phase 8 — Secrets Management / Storage Hardening

> Goal: All secrets, keys, and sensitive data are stored and rotated safely.

- [ ] TOTP secrets encrypted at rest in database
- [ ] Recovery codes hashed before storage (never stored plaintext)
- [ ] All API keys and provider secrets loaded from environment variables (not hardcoded)
- [ ] `.env` files excluded from version control confirmed
- [ ] Webhook HMAC-SHA256 signing implemented for billing events
- [ ] Secrets rotation procedure documented
- [ ] Database credentials rotatable without app downtime
- [ ] No sensitive data appearing in application logs
- [ ] Security headers set on all HTTP responses (HSTS, X-Frame-Options, CSP, etc.)

**Phase 8 Complete:** ☐

---

## 14. Acceptance Criteria

The security layer is considered **production-ready** when all of the following are true:

- [ ] Admin, partner admin, and tenant users can log in securely with password
- [ ] MFA (TOTP) flow is working for `platform_admin` and `partner_admin`
- [ ] Passkey (WebAuthn) flow is working for `platform_admin`
- [ ] Recovery codes work for locked-out accounts
- [ ] One tenant **cannot** access another tenant's data (tested and confirmed)
- [ ] Suspended partner **cannot** start new calls (tested and confirmed)
- [ ] Suspended tenant **cannot** start new calls (tested and confirmed)
- [ ] Over-limit call attempts are **rejected before session creation** (not during)
- [ ] Audit logs clearly show who did what and when for all tracked actions
- [ ] No plaintext secrets exist in the codebase or logs
- [ ] All 8 implementation phases marked complete above

---

## 15. Developer Note

> This security layer must remain **logically separate from the media path**.

Voice continues to flow through:
- **OpenSIPS** — SIP proxy and routing
- **Asterisk** — PBX call control
- **C++ Voice Gateway** — real-time audio processing

Security must **block bad access before the session starts**, not inside the RTP path.

Any security check that would add latency to audio frames is architecturally wrong and must be refactored to operate at the session admission layer.

---

*End of Talk-lee Security Layer Plan*