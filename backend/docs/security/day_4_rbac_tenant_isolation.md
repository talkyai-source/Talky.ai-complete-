# Day 4 — RBAC + Tenant Isolation: Role-Based Access Control

> **Date:** 2026
> **Security Phase:** Day 4 of 8
> **Status:** ✅ Implemented
> **Official References:**
> - [NIST RBAC Standard (ANSI/INCITS 359-2004)](https://csrc.nist.gov/projects/role-based-access-control)
> - [OWASP Access Control Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Access_Control_Cheat_Sheet.html)
> - [OWASP Authorization Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html)
> - [PostgreSQL Row Level Security](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)

---

## Table of Contents

1. [What Was Built](#1-what-was-built)
2. [Research Sources](#2-research-sources)
3. [RBAC Architecture](#3-rbac-architecture)
4. [Role Hierarchy](#4-role-hierarchy)
5. [Permission System](#5-permission-system)
6. [Tenant Isolation](#6-tenant-isolation)
7. [Middleware Integration](#7-middleware-integration)
8. [API Endpoints](#8-api-endpoints)
9. [Database Migration](#9-database-migration)
10. [Files Created or Modified](#10-files-created-or-modified)
11. [Implementation Checkpoints](#11-implementation-checkpoints)
12. [Security Decisions Log](#12-security-decisions-log)
13. [What Day 5 Builds On This](#13-what-day-5-builds-on-this)

---

## 1. What Was Built

Day 4 delivers complete Role-Based Access Control (RBAC) and tenant isolation following NIST standards.
The system supports multi-tenancy with strict data isolation while allowing cross-tenant access for platform administrators.

| Component | Before Day 4 | After Day 4 |
|-----------|-------------|-------------|
| Role system | Simple string on user_profiles | **Full RBAC** with role hierarchy and permissions |
| Roles | "user", "admin", "owner" | **platform_admin, partner_admin, tenant_admin, user, readonly** |
| Permissions | Implicit (hardcoded checks) | **Explicit permission registry** with resource:action format |
| Tenant membership | Single tenant per user | **Many-to-many** - users can belong to multiple tenants |
| Tenant access | Check tenant_id match | **RBAC-validated** with platform admin bypass |
| Row-level security | None | **PostgreSQL RLS policies** on core tables |
| Middleware | Basic tenant extraction | **Full tenant isolation** with RBAC context |
| Cross-tenant access | Not supported | **Platform admins** can access any tenant |

---

## 2. Research Sources

### NIST RBAC Standard (ANSI/INCITS 359-2004)

**Core RBAC Components (all implemented):**
> "RBAC comprises the following components:> - Users: entities that can be assigned roles
> - Roles: job functions within an organization
> - Permissions: approvals to perform operations on objects
> - Sessions: mappings between users and activated roles"

**Role Hierarchy:**
> "Roles can have hierarchical relationships where senior roles inherit permissions from junior roles"

Decision: Implemented hierarchy where platform_admin > partner_admin > tenant_admin > user > readonly

### OWASP Access Control Cheat Sheet

**Principle of Least Privilege:**
> "Every user, program, or process should have only the bare minimum privileges necessary to perform its function"

Decision: Five role tiers with minimal default permissions. Direct permission grants for exceptions.

**Deny by Default:**
> "Access should be denied unless explicitly granted"

Decision: RLS policies default to deny. Application checks deny unless permission is explicitly found.

### OWASP Multi-Tenant Security

**Tenant Isolation:**
> "Strict isolation between tenant data must be enforced at all layers"

Decision: Two-layer defense:
1. Application layer: middleware validates tenant membership
2. Database layer: PostgreSQL RLS policies enforce isolation

---

## 3. RBAC Architecture

**Files:**
- `backend/app/core/security/rbac.py` — Core RBAC logic
- `backend/app/core/security/tenant_isolation.py` — Tenant isolation

### Role Hierarchy (NIST Standard)

```
┌─────────────────────────────────────────────────────────────┐
│                    PLATFORM_ADMIN (100)                     │
│  • Full system access across all tenants                    │
│  • Can manage platform settings, billing, all users         │
│  • Cross-tenant access with audit logging                   │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│                    PARTNER_ADMIN (80)                       │
│  • Multi-tenant access within partner scope                 │
│  • Can manage partner-level resources                       │
│  • View analytics across partner tenants                    │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│                    TENANT_ADMIN (60)                        │
│  • Full administrative access within single tenant          │
│  • Can manage users, campaigns, tenant settings             │
│  • Access to billing information                            │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│                       USER (40)                             │
│  • Standard user within a tenant                            │
│  • Can create/modify own campaigns                          │
│  • Can view own data and shared resources                   │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│                     READONLY (20)                           │
│  • View-only access within a tenant                         │
│  • Cannot modify any resources                              │
│  • Can view campaigns, calls, analytics                     │
└─────────────────────────────────────────────────────────────┘
```

### Permission Resolution Flow

```
User Request
    │
    ▼
┌────────────────────────────────────────┐
│  1. Extract tenant_id from request     │
│  2. Load user's role in that tenant    │
│  3. Check if platform admin (bypass)   │
└────────────┬───────────────────────────┘
             │
             ▼
┌────────────────────────────────────────┐
│  4. Query role_permissions table       │
│  5. Query user_permissions table       │
│  6. Combine into effective perms       │
└────────────┬───────────────────────────┘
             │
             ▼
┌────────────────────────────────────────┐
│  7. Check required permission          │
│  8. If granted: proceed                │
│  9. If denied: 403 Forbidden           │
└────────────────────────────────────────┘
```

---

## 4. Role Hierarchy

### UserRole Enum

```python
class UserRole(str, enum.Enum):
    PLATFORM_ADMIN = "platform_admin"   # level=100
    PARTNER_ADMIN = "partner_admin"     # level=80
    TENANT_ADMIN = "tenant_admin"       # level=60
    USER = "user"                       # level=40
    READONLY = "readonly"               # level=20
```

### Hierarchy Checking

```python
def can_access(self, required_role: UserRole) -> bool:
    """
    Check if this role can access resources requiring `required_role`.

    Example:
        platform_admin.can_access(tenant_admin) -> True
        user.can_access(tenant_admin) -> False
    """
    return self.level >= required_role.level
```

### Usage in Endpoints

```python
@router.post("/campaigns")
async def create_campaign(
    user: CurrentUser = Depends(require_role(UserRole.USER))
):
    # Only users with role >= USER can access
    ...

@router.delete("/tenants/{tenant_id}")
async def delete_tenant(
    user: CurrentUser = Depends(require_role(UserRole.TENANT_ADMIN))
):
    # Only tenant_admin, partner_admin, or platform_admin
    ...
```

---

## 5. Permission System

### Permission Format

Permissions follow the pattern: `resource:action`

| Resource | Actions | Example |
|----------|---------|---------|
| campaigns | create, read, update, delete, admin | `campaigns:create` |
| users | create, read, update, delete, manage | `users:manage` |
| tenants | read, update, admin | `tenants:admin` |
| billing | read, update, admin | `billing:read` |
| calls | create, read, delete | `calls:read` |
| connectors | create, read, update, delete | `connectors:update` |
| analytics | read, export | `analytics:export` |
| platform | admin, tenants:manage, users:manage | `platform:admin` |

### Admin Permission Inheritance

If a user has `{resource}:admin`, they automatically have all permissions for that resource:

```python
def check_permission(user_permissions, required: Permission) -> bool:
    # Direct check
    if required in user_permissions:
        return True

    # Admin permission grants all actions
    resource = required.split(":")[0]
    admin_perm = Permission(f"{resource}:admin")
    if admin_perm in user_permissions:
        return True

    # Platform admin grants everything
    if Permission.PLATFORM_ADMIN in user_permissions:
        return True

    return False
```

---

## 6. Tenant Isolation

### Isolation Layers

#### Layer 1: Application Middleware

```python
class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 1. Extract JWT and tenant_id
        # 2. Set context variables
        # 3. Platform admins can bypass (cross-tenant)
        # 4. Log cross-tenant access for audit
```

#### Layer 2: Dependency Validation

```python
async def require_tenant_member(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    # Validate user is member of requested tenant
    # Raises 403 if not a member
    # Allows platform admins to bypass
```

#### Layer 3: Database RLS Policies

```sql
-- Example: campaigns table RLS policy
CREATE POLICY campaigns_tenant_isolation ON campaigns
    USING (
        tenant_id = current_setting('app.current_tenant_id', true)::UUID
        OR current_setting('app.bypass_rls', true)::BOOLEAN = TRUE
    );
```

### Context Variables

```python
# Per-request context (using ContextVar)
_tenant_context: ContextVar[Optional[str]] = ContextVar("tenant_context")
_bypass_rls: ContextVar[bool] = ContextVar("bypass_rls", default=False)

# Set by middleware, used by database queries
def set_current_tenant_id(tenant_id: str):
    _tenant_context.set(tenant_id)

def get_bypass_rls() -> bool:
    return _bypass_rls.get()
```

### Cross-Tenant Access

Platform admins can access any tenant by specifying `X-Tenant-ID` header:

```
GET /api/v1/campaigns
X-Tenant-ID: {target_tenant_id}
Authorization: Bearer {platform_admin_token}

# Response: campaigns from target_tenant_id
```

Cross-tenant access is logged for audit:
```
INFO: Cross-tenant access: user={id} from={tenant_a} to={tenant_b} path=/campaigns
```

---

## 7. Middleware Integration

### TenantMiddleware (Updated for Day 4)

**File:** `backend/app/core/tenant_middleware.py`

```python
class TenantMiddleware(BaseHTTPMiddleware):
    """
    Day 4 Enhancements:
    - RBAC role extraction and validation
    - Platform admin bypass support
    - Cross-tenant access logging
    - RLS context variable management
    """

    async def dispatch(self, request: Request, call_next):
        # 1. Clear any existing context
        clear_tenant_context()

        # 2. Extract and validate JWT
        payload = decode_and_validate_token(token)

        # 3. Set context variables
        set_current_tenant_id(tenant_id)
        set_current_user_context(CurrentUser(...))

        # 4. Platform admin special handling
        if role == UserRole.PLATFORM_ADMIN:
            set_bypass_rls(True)
            if cross_tenant_request:
                log_cross_tenant_access(...)

        # 5. Process request
        response = await call_next(request)

        # 6. Clear context
        clear_tenant_context()
        return response
```

### Dependency Chain

```python
# Basic auth (extract user from JWT)
get_current_user
    │
    ▼
# Load permissions from database
load_user_permissions
    │
    ▼
# Validate tenant membership
require_tenant_member
    │
    ▼
# Check role hierarchy
require_role(UserRole.TENANT_ADMIN)
    │
    ▼
# Check specific permission
require_permission(Permission.CAMPAIGNS_DELETE)
```

---

## 8. API Endpoints

**File:** `backend/app/api/v1/endpoints/rbac.py`

### Role Management

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/rbac/roles` | Any | List all roles |
| `GET` | `/rbac/roles/{id}` | Any | Get role details |
| `GET` | `/rbac/roles/{id}/permissions` | Any | Get role permissions |
| `POST` | `/rbac/roles/{id}/permissions` | platform_admin | Assign permission |
| `DELETE` | `/rbac/roles/{id}/permissions` | platform_admin | Remove permission |

### Permission Management

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/rbac/permissions` | Any | List all permissions |

### User Management

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/rbac/users/me/permissions` | Any | Get my permissions |
| `GET` | `/rbac/users/me/tenants` | Any | Get my tenant memberships |
| `GET` | `/rbac/users/{id}/permissions` | tenant_admin | Get user permissions |

### Tenant Membership

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/rbac/tenant-users` | tenant_admin | List tenant members |
| `POST` | `/rbac/tenant-users` | tenant_admin | Add user to tenant |
| `PATCH` | `/rbac/tenant-users/{id}` | tenant_admin | Update member role |
| `DELETE` | `/rbac/tenant-users/{id}` | tenant_admin | Remove from tenant |

---

## 9. Database Migration

**File:** `backend/database/migrations/day4_rbac_tenant_isolation.sql`

### Tables Created

#### roles

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID PK | Role identifier |
| `name` | VARCHAR(50) | Role name (unique) |
| `description` | TEXT | Human-readable description |
| `level` | INTEGER | Hierarchy level (higher = more access) |
| `is_system_role` | BOOLEAN | System-defined (immutable) |
| `tenant_scoped` | BOOLEAN | Per-tenant vs global |

#### permissions

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID PK | Permission identifier |
| `name` | VARCHAR(100) | Permission name (resource:action) |
| `description` | TEXT | Description |
| `resource` | VARCHAR(50) | Resource being acted upon |
| `action` | VARCHAR(50) | Action being performed |
| `is_system` | BOOLEAN | System-defined |

#### role_permissions

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID PK | Junction ID |
| `role_id` | UUID FK → roles | Role |
| `permission_id` | UUID FK → permissions | Permission |

#### tenant_users (Junction)

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID PK | Membership ID |
| `user_id` | UUID FK → user_profiles | User |
| `tenant_id` | UUID FK → tenants | Tenant |
| `role_id` | UUID FK → roles | Role in this tenant |
| `is_primary` | BOOLEAN | Primary tenant for user |
| `status` | VARCHAR(20) | pending/active/suspended/removed |

#### user_permissions (Direct Grants)

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID PK | Grant ID |
| `user_id` | UUID FK → user_profiles | User |
| `permission_id` | UUID FK → permissions | Permission |
| `tenant_id` | UUID FK → tenants | Optional tenant scope |
| `expires_at` | TIMESTAMPTZ | Optional expiration |
| `granted_by` | UUID FK → user_profiles | Who granted |

### Row Level Security (RLS)

Enabled on tables with tenant_id:
- `campaigns`
- `leads`
- `calls`
- `conversations`
- `connectors`

RLS Policy Pattern:
```sql
CREATE POLICY {table}_tenant_isolation ON {table}
    USING (
        tenant_id = current_setting('app.current_tenant_id', true)::UUID
        OR current_setting('app.bypass_rls', true)::BOOLEAN = TRUE
    );
```

### Views Created

#### user_effective_permissions

Combines role permissions and direct user permissions:
```sql
SELECT user_id, permission_id, permission_name, resource, action,
       tenant_id, role_name, 'role' as grant_type
FROM tenant_users
JOIN role_permissions ON ...

UNION

SELECT user_id, permission_id, permission_name, resource, action,
       tenant_id, NULL as role_name, 'direct' as grant_type
FROM user_permissions
WHERE expires_at IS NULL OR expires_at > NOW();
```

#### user_tenant_roles

Lists all tenant memberships for users:
```sql
SELECT user_id, email, tenant_id, tenant_name, role_name, role_level,
       status, is_primary
FROM user_profiles
JOIN tenant_users ON ...
JOIN tenants ON ...
JOIN roles ON ...;
```

---

## 10. Files Created or Modified

| File | Action | Purpose |
|------|--------|---------|
| `backend/app/core/security/rbac.py` | **Created** | Core RBAC: roles, permissions, hierarchy, checking |
| `backend/app/core/security/tenant_isolation.py` | **Created** | Tenant isolation: middleware, context, RLS helpers |
| `backend/app/api/v1/endpoints/rbac.py` | **Created** | RBAC API endpoints for role/permission management |
| `backend/app/api/v1/dependencies.py` | Modified | Added require_role, require_permission, require_tenant_member |
| `backend/app/api/v1/routes.py` | Modified | Registered rbac_router |
| `backend/app/core/tenant_middleware.py` | Modified | Day 4: RBAC integration, cross-tenant logging, context vars |
| `backend/database/migrations/day4_rbac_tenant_isolation.sql` | **Created** | roles, permissions, role_permissions, tenant_users, user_permissions, RLS policies |
| `backend/docs/security/day_4_rbac_tenant_isolation.md` | **Created** | This document |

---

## 11. Implementation Checkpoints

### 11.1 — Database Migration

- [ ] `day4_rbac_tenant_isolation.sql` applied to database
- [ ] `roles` table exists with 5 system roles inserted
- [ ] `permissions` table exists with all system permissions
- [ ] `role_permissions` table has correct mappings
- [ ] `tenant_users` table exists
- [ ] `user_permissions` table exists
- [ ] RLS enabled on campaigns, leads, calls, conversations, connectors
- [ ] RLS policies created and active
- [ ] Views `user_effective_permissions` and `user_tenant_roles` created

### 11.2 — RBAC Module

- [ ] `UserRole` enum has all 5 roles with correct levels
- [ ] `normalize_role()` handles backward compatibility
- [ ] `can_access()` correctly implements hierarchy
- [ ] `Permission` enum has all resource:action permissions
- [ ] `get_user_permissions()` aggregates role + direct permissions
- [ ] `check_permission()` correctly checks admin inheritance
- [ ] `require_role()` dependency factory works
- [ ] `require_permission()` dependency factory works
- [ ] `RBACContext` context manager works

### 11.3 — Tenant Isolation

- [ ] Context variables properly store tenant_id per request
- [ ] `set_tenant_context_in_db()` sets PostgreSQL session vars
- [ ] `validate_tenant_access()` correctly validates membership
- [ ] `require_tenant_member()` dependency blocks non-members
- [ ] `require_tenant_member()` allows platform admin bypass
- [ ] Cross-tenant access is logged
- [ ] Context is cleared after each request

### 11.4 — Middleware Integration

- [ ] `TenantMiddleware` extracts role from JWT
- [ ] `TenantMiddleware` sets RBAC context variables
- [ ] `TenantMiddleware` detects cross-tenant access
- [ ] Cross-tenant requests are logged with user/from/to/path
- [ ] Platform admins get `bypass_rls=True`

### 11.5 — API Endpoints

- [ ] `GET /rbac/roles` returns all roles
- [ ] `GET /rbac/roles/{id}/permissions` returns role permissions
- [ ] `GET /rbac/permissions` returns all permissions
- [ ] `GET /rbac/users/me/permissions` returns current user perms
- [ ] `GET /rbac/users/me/tenants` returns user's tenants
- [ ] `GET /rbac/tenant-users` lists members (tenant_admin+)
- [ ] `POST /rbac/tenant-users` adds member (tenant_admin+)
- [ ] `PATCH /rbac/tenant-users/{id}` updates role (tenant_admin+)
- [ ] `DELETE /rbac/tenant-users/{id}` removes member (tenant_admin+)

### 11.6 — Security Properties

- [ ] User cannot access tenant they're not a member of
- [ ] Regular user cannot access platform admin endpoints
- [ ] Platform admin can access any tenant
- [ ] Cross-tenant access is logged
- [ ] RLS blocks queries without tenant context
- [ ] Role hierarchy is enforced (tenant_admin can do user things)
- [ ] Permission check fails closed (deny if uncertain)

---

## 12. Security Decisions Log

| Decision | Rationale | Source |
|----------|-----------|--------|
| Role hierarchy with levels | NIST RBAC standard pattern | NIST RBAC |
| Platform admin bypass | Needed for support/debugging; logged for audit | Multi-tenant best practice |
| Two-layer isolation (app + RLS) | Defense in depth | OWASP |
| Many-to-many tenant membership | Users can work across organizations (consultants, partners) | Business requirement |
| Direct user permissions | Handle exceptions without creating new roles | NIST RBAC discretionary |
| Permission format `resource:action` | Standard pattern, easy to understand | Industry standard |
| Admin permission inheritance | `{resource}:admin` grants all actions reduces permission bloat | Common pattern |
| Soft delete for tenant_users | Audit trail + ability to restore | Data integrity |
| ContextVar for request state | Thread-safe (async-safe) per-request storage | Python best practice |
| Cross-tenant access logging | SOC2/ISO27001 compliance requirement | Compliance |
| RLS bypass only for platform_admin | Minimize bypass privilege | Principle of least privilege |

---

## 13. What Day 5 Builds On This

Day 5 adds **Audit Logging** and **Security Event Monitoring**.
It depends on everything built in Days 1-4:

- `roles` / `permissions` — Day 5 logs permission changes in audit log
- `tenant_users` — Day 5 tracks membership changes
- `login_attempts` (Day 1) — Day 5 aggregates into security dashboard
- RBAC context — Day 5 includes user role in all audit events
- Tenant isolation — Day 5 monitors cross-tenant access patterns

Tables Day 5 will add:
- `security_audit_log` — Immutable audit trail of all security events
- `security_alerts` — Real-time security alerts (failed logins, privilege escalation)

---

*End of Day 4 — RBAC + Tenant Isolation*
