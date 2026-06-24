# Users & Roles — Talky.ai

> How user access is modeled, what each role can do, and how roles are assigned via the
> admin portal. Grounded in the actual implementation (`backend/app/core/security/rbac.py`
> and the `/rbac/*` + `/admin/*` APIs). Sections marked **⚠️ Gap** describe access that is
> **not yet enforced** and should be closed before relying on these roles for isolation.

_Last reviewed: 2026-06-23._

---

## 1. TL;DR — we have **5 roles**

| # | Role (stored value) | Level | Scope | Plain-English meaning | Your term |
|---|---|---|---|---|---|
| 1 | `platform_admin` | **100** | Global (all tenants) | Full system super-user | **Superadmin** |
| 2 | `partner_admin` | 80 | Multiple tenants (reseller) | Partner/reseller admin | Partner |
| 3 | `tenant_admin` | 60 | One tenant | Full admin of their own org | Account owner / Admin |
| 4 | `user` | 40 | One tenant | Standard user (the signup default) | Member |
| 5 | `readonly` | 20 | One tenant | View-only | **Support** (intended) |

- **Higher level ⊇ lower** (`platform_admin > partner_admin > tenant_admin > user > readonly`).
- **Default role** on a record with no role = `user`. **Unknown/garbage role values fail _closed_ to `readonly`** (safe default).
- **Legacy aliases** still accepted when reading: `admin`/`owner` → `tenant_admin`, `super_admin` → `platform_admin`. (These are *not* valid to store — the DB only allows the 5 canonical values.)
- ❗ There is **no dedicated `support` role today.** The closest is `readonly` — but see **⚠️ Gap G4**, because `readonly` can currently still write. If you want a true support tier, we should either enforce `readonly` properly or add a `support` role.

---

## 2. The two-level model (important)

A user has **two** kinds of role:

1. **Platform-global role** — one value on `user_profiles.role`. **This is what every access check at request time actually reads** (`CurrentUser.role`). `platform_admin` here = global super-user across all tenants.
2. **Per-tenant role** — `tenant_users.role_id`. A user can be a member of multiple tenants with a *different* role in each. This drives the granular permission tables.

> Practical implication: today the request-time guards (`require_admin`, `require_platform_admin`, etc.) key off the **single global** `user_profiles.role`. The richer per-tenant role exists in the schema but is not what gates most endpoints. Keep this in mind when assigning access.

---

## 3. What each role can do (capability matrix)

✅ = allowed · ❌ = blocked · ⚠️ = allowed today but **shouldn't be** (see Gaps)

| Capability | platform_admin | partner_admin | tenant_admin | user | readonly |
|---|:--:|:--:|:--:|:--:|:--:|
| Assign **permissions to roles** (`/rbac/roles/{id}/permissions`) | ✅ | ❌ | ❌ | ❌ | ❌ |
| Platform ops: **audit logs, secrets, suspensions, security events, emergency access** | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Add / change / remove users & their role** in a tenant (`/rbac/tenant-users`) | ✅ (any tenant) | ✅ (own) | ✅ (own) | ❌ | ❌ |
| `/admin/*` console: tenants, calls, connectors, usage, health, API keys, rate/call limits, webhooks | ✅ | ✅ | ✅ | ❌ | ❌ |
| Configure **telephony providers & AI credentials** (Twilio/Vonage/AI keys) | ✅ | ✅ | ✅ | ❌ | ❌ |
| **Campaigns / contacts / DNC / calls / billing / analytics / recordings** create-update-delete | ✅ | ✅ | ✅ | ✅ | ⚠️ |
| **Place / originate calls** | ✅ | ✅ | ✅ | ✅ | ⚠️ |
| View own permissions & tenants | ✅ | ✅ | ✅ | ✅ | ✅ |

**Notes on the matrix**
- The platform-ops row (audit/secrets/suspensions/…) is effectively **platform_admin-only** because the permissions those endpoints require are not granted to any other role (see **Gap G5**).
- `partner_admin` currently has **the exact same permission set as `tenant_admin`** in code — the "cross-tenant reseller" capability is described but **not implemented** (see **Gap G6**). Treat `partner_admin` as "tenant_admin that also passes platform-ish admin gates" until that's built.

---

## 4. How roles are assigned (the "admin portal")

### Backend API (the source of truth)

| Action | Endpoint | Who can do it |
|---|---|---|
| List members of a tenant | `GET /api/v1/rbac/tenant-users` | any tenant member |
| **Add** a user to a tenant with a role | `POST /api/v1/rbac/tenant-users` | `tenant_admin`+ |
| **Change** a user's role / status | `PATCH /api/v1/rbac/tenant-users/{id}` | `tenant_admin`+ |
| **Remove** a user from a tenant | `DELETE /api/v1/rbac/tenant-users/{id}` | `tenant_admin`+ |
| View a user's effective permissions | `GET /api/v1/rbac/users/{id}/permissions` | `tenant_admin`+ |
| Grant/revoke a **permission to a role** | `POST`/`DELETE /api/v1/rbac/roles/{id}/permissions` | **`platform_admin` only** |
| Browse the role/permission catalog | `GET /api/v1/rbac/roles`, `/permissions` | any logged-in user |

- **Same-tenant scoping is enforced** on add/change/remove (a tenant_admin can only touch users in their own tenant; platform_admin can touch any).
- **Initial role on self-signup = `tenant_admin` of a brand-new, isolated tenant.** A signup can NOT mint a `platform_admin` or join an existing tenant — every signup becomes the owner of its own org. To add people to an existing org you use the `tenant_admin`-gated add-user flow above.
- Creating `platform_admin` users is **not** a public flow — it must be done deliberately (DB or a platform_admin via the role API).

### Frontend portals (current state)
- **`Admin/frontend/`** — platform admin panel (Users, Tenants, Calls, System Health/Config, Usage, Incidents…). The Users page **lists** users and shows a role badge but is **view-only — there is no role-edit / invite screen wired up yet.** So role assignment today is **API/DB-driven**, not point-and-click. ⚠️ It also has a `VITE_ADMIN_DEV_MODE` flag that disables client-side auth — never ship it truthy.
- **`Talk-Leee/` `/admin/*`** — tenant-facing security consoles (billing, API keys, audit logs, abuse detection, rate limiting, secrets, webhooks). Not a user-role editor.

> **Recommendation:** if the goal is "distribute roles via the admin portal," we need to build the role-assignment UI on `Admin/frontend` UsersPage (the backend API already exists). Small, well-scoped frontend task.

---

## 5. Mapping to your product requirements

You described three tiers — **superadmin**, **support**, and regular users. Here's how to set that up with what exists, and what to add:

| You want | Use this role | Status |
|---|---|---|
| **Superadmin** — full control, all orgs | `platform_admin` | ✅ Ready. Assign sparingly (it's a global super-user). |
| **Org admin** — runs one customer org | `tenant_admin` | ✅ Ready (this is the signup default). |
| **Regular member** — uses the product, no admin | `user` | ⚠️ Works, but `user` currently has near-full CRUD (Gap G4). |
| **Support** — can *view* to help customers, no changes | `readonly` (intended) | ⚠️ **Not safe yet** — `readonly` can still write (Gap G4). Needs enforcement or a new `support` role. |

**To genuinely restrict access** the way you want ("we can't allow all the users to access"), the role names alone aren't enough today — the gaps in §6 must be closed, because right now `user` and `readonly` can do almost everything a `tenant_admin` can on the core resources.

---

## 6. ⚠️ Gaps that currently undermine access control

These are real, code-verified gaps. They matter directly to your goal of restricting access. Severity is the security impact.

| ID | Severity | Gap | Effect | Where |
|---|---|---|---|---|
| **G1** | **High** | **Privilege escalation in role assignment** — no "can't grant a role higher than your own" check. The role field accepts all 5 values. | A **`tenant_admin` can promote a user (or themselves via a member) to `partner_admin`/`platform_admin`.** | `rbac/tenant_users.py` (POST 104-210, PATCH 213-338); `rbac/schemas.py:54,59` |
| **G2** | **High** | **Unauthenticated admin endpoints** — `/api/v1/admin/abuse/*` (list/resolve abuse events, edit detection rules) have **no auth dependency at all**; no global middleware covers them. | Anyone on the internet can read cross-tenant abuse data and edit detection rules. | `abuse_monitoring.py` (no `get_current_user`/`require_admin`) |
| **G3** | Medium | **`/admin/*` "platform" console is only `require_admin`**, not `require_platform_admin`. | Any **`tenant_admin` can reach cross-tenant admin surfaces** (tenants/calls/connectors/usage lists); isolation depends entirely on RLS being correct on every query, not on a role gate. | `admin/__init__.py` (no router guard); `admin/tenants.py`, `admin/calls.py`, … |
| **G4** | Medium | **Core tenant resources are authenticated-but-not-role-gated** — campaigns, calls, billing, contacts, DNC, connectors, analytics, telephony-concurrency, etc. use only "is logged in". | **`readonly` and `user` can create/update/delete and place calls** — so "view-only" and "support" tiers don't actually restrict anything. | most of `endpoints/*.py` (`Depends(get_current_user)` only) |
| **G5** | Low | **Two permission systems disagree** + the security-feature permissions (`audit:*`, `secrets:*`, `emergency:*`, `users:suspend`…) are **never seeded**, so those features are platform_admin-only by accident. Fragile: a future seeder could silently widen access. | Inconsistent/undocumented enforcement of platform-ops features. | `rbac.py:594-595` vs `dependencies.py:555`; seed gap |
| **G6** | Low | **`partner_admin` is not really implemented** — identical permission set to `tenant_admin`, no cross-tenant reseller logic. | The "partner" tier promises more than it delivers. | `rbac.py:232-259` |
| **G7** | Low | **Frontend `VITE_ADMIN_DEV_MODE`** disables all client-side auth checks when truthy. | Admin SPA shell exposed if shipped truthy (backend still guards APIs). | `Admin/frontend/src/components/AdminRouteGuard.tsx:9,24-26` |

> **Bottom line:** the *role model* is solid (clear 5-tier hierarchy, safe defaults, platform-admin bypass done consistently). The *enforcement* is incomplete: **G1, G3, and G4 are the ones that break "restrict who can access."** Closing G4 (gate writes behind role/permission) is what actually makes `readonly`/support and `user` meaningfully limited.

---

## 7. Recommendations (priority order)

1. **Close G4 — gate core resources by role/permission.** Decide per resource what `readonly` (view only) and `user` (use, no config) may do, and enforce it (the `require_permission`/`Permission` machinery already exists — wire it onto campaigns/calls/billing/connectors/etc.). _This is the single change that makes your tiers real._
2. **Fix G1 — add an escalation ceiling.** Reject assigning a role with a higher level than the caller's own (and require `platform_admin` to grant `platform_admin`/`partner_admin`).
3. **Fix G2 — authenticate `abuse_monitoring.py`** (add `require_admin` / `require_platform_admin`).
4. **Decide the "support" tier:** either (a) make `readonly` truly read-only (falls out of #1) and use it for support, or (b) add a dedicated `support` role between `readonly` and `user`.
5. **G3:** put `require_platform_admin` on the genuinely cross-tenant `/admin/*` surfaces; keep `require_admin` only on tenant-scoped admin actions.
6. **Build the role-assignment UI** on `Admin/frontend` UsersPage (backend API is ready) so roles are managed in the portal, not by hand.
7. **Seed the security-feature permissions explicitly (G5)** and converge on one permission source of truth.

---

## 8. Reference — the 32 permissions

Format `resource:action`. Held implicitly via role (`ROLE_DEFAULT_PERMISSIONS`, `rbac.py:179-292`).

- **campaigns:** create, read, update, delete, admin
- **users:** create, read, update, delete, manage
- **tenants:** read, update, admin
- **billing:** read, update, admin
- **calls:** create, read, delete
- **connectors:** create, read, update, delete
- **analytics:** read, export
- **platform (global):** admin, tenants:manage, users:manage, settings:manage

Default grants by role (today): `readonly` = 6 read-ish perms · `user` = 14 (incl. campaign/connector delete) · `tenant_admin` = all 25 tenant-scoped · `partner_admin` = same 25 as tenant_admin · `platform_admin` = all 32.

---

_Source files: `backend/app/core/security/rbac.py` (roles, permissions, hierarchy, normalize), `backend/app/api/v1/dependencies.py` (request-time guards), `backend/app/api/v1/endpoints/rbac/*` (assignment API), `backend/app/api/v1/endpoints/admin/*` (admin console), `backend/database/schema/baseline_2026-06-02.sql` (DB constraints)._
