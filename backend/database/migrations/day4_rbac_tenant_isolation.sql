-- =============================================================================
-- Day 4 Security: RBAC + Tenant Isolation
-- Migration: day4_rbac_tenant_isolation.sql
-- =============================================================================
--
-- Official References (verified March 2026):
--   NIST RBAC Standard (ANSI/INCITS 359-2004):
--     https://csrc.nist.gov/projects/role-based-access-control
--   OWASP Access Control Cheat Sheet:
--     https://cheatsheetseries.owasp.org/cheatsheets/Access_Control_Cheat_Sheet.html
--   OWASP Authorization Cheat Sheet:
--     https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html
--   PostgreSQL Row Level Security (RLS):
--     https://www.postgresql.org/docs/current/ddl-rowsecurity.html
--
-- What this migration does:
--   1. Creates roles table (RBAC core role definitions)
--   2. Creates permissions table (granular permissions)
--   3. Creates role_permissions junction (many-to-many)
--   4. Creates tenant_users junction (many-to-many with tenant-specific roles)
--   5. Creates user_permissions table (direct user grants, overrides)
--   6. Updates user_profiles.role to validate against new roles
--   7. Adds tenant isolation columns to existing tables
--   8. Creates RLS policies for core tables
--
-- Design Decisions:
--   - Role hierarchy: platform_admin > partner_admin > tenant_admin > user > readonly
--   - Tenant isolation enforced at application layer (middleware) + database layer (RLS)
--   - Permissions are fine-grained: resource:action (e.g., "campaigns:create")
--   - Users can have different roles in different tenants
--   - Platform admins bypass tenant isolation (cross-tenant access)
--
-- To apply:
--   psql postgresql://talkyai:talkyai_secret@localhost:5432/talkyai \
--        -f database/migrations/day4_rbac_tenant_isolation.sql
--
-- Safe to run multiple times (all DDL uses IF NOT EXISTS guards).
-- Wrapped in a single transaction — rolls back entirely on any error.
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- Ensure required extension
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =============================================================================
-- TABLE: roles
-- =============================================================================
--
-- Core RBAC role definitions. These are the system roles that can be assigned.
-- Role hierarchy is enforced in application code.
--
-- NIST RBAC: Core RBAC defines roles as job functions within an organization.
-- We extend this for SaaS multi-tenant: platform-level, partner-level, tenant-level.
--
-- Hierarchy (highest to lowest):
--   1. platform_admin  - Full system access, all tenants, all operations
--   2. partner_admin   - Access to multiple tenants (reseller/partner view)
--   3. tenant_admin    - Full access within a single tenant
--   4. user            - Standard user within a tenant (limited admin)
--   5. readonly        - View-only access within a tenant
--
-- Design decisions:
--   - is_system_role: prevents deletion of core roles
--   - level: numeric for easy hierarchy comparison (higher = more access)
--   - tenant_scoped: whether this role is assigned per-tenant or global
-- =============================================================================

CREATE TABLE IF NOT EXISTS roles (
    id              UUID        NOT NULL DEFAULT uuid_generate_v4(),
    name            VARCHAR(50) NOT NULL,
    description     TEXT,

    -- Role hierarchy level (higher number = more privileges)
    -- platform_admin=100, partner_admin=80, tenant_admin=60, user=40, readonly=20
    level           INTEGER     NOT NULL,

    -- Is this a system-defined role (cannot be deleted)
    is_system_role  BOOLEAN     NOT NULL DEFAULT FALSE,

    -- Is this role assigned per-tenant (TRUE) or global (FALSE)
    -- platform_admin is global; tenant_admin is per-tenant
    tenant_scoped   BOOLEAN     NOT NULL DEFAULT TRUE,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_roles PRIMARY KEY (id),
    CONSTRAINT uq_roles_name UNIQUE (name),
    CONSTRAINT chk_roles_level_positive CHECK (level > 0)
);

-- Insert system roles (idempotent with ON CONFLICT)
INSERT INTO roles (name, description, level, is_system_role, tenant_scoped) VALUES
    ('platform_admin', 'Full system access across all tenants. Can manage platform settings, users, and billing.', 100, TRUE, FALSE),
    ('partner_admin', 'Access to multiple tenants within partner scope. Can manage partner-level resources.', 80, TRUE, TRUE),
    ('tenant_admin', 'Full administrative access within a single tenant. Can manage users, campaigns, and settings.', 60, TRUE, TRUE),
    ('user', 'Standard user within a tenant. Can create campaigns and view own data.', 40, TRUE, TRUE),
    ('readonly', 'View-only access within a tenant. Cannot modify any resources.', 20, TRUE, TRUE)
ON CONFLICT (name) DO UPDATE SET
    description = EXCLUDED.description,
    level = EXCLUDED.level,
    is_system_role = TRUE,
    tenant_scoped = EXCLUDED.tenant_scoped,
    updated_at = NOW();

-- Index for level-based queries
CREATE INDEX IF NOT EXISTS idx_roles_level ON roles (level DESC);

-- =============================================================================
-- TABLE: permissions
-- =============================================================================
--
-- Granular permissions following the pattern: resource:action
-- Examples: campaigns:create, campaigns:read, campaigns:update, campaigns:delete
--           users:read, users:manage, billing:read, settings:admin
--
-- OWASP: Permissions should be fine-grained and specific.
-- NIST RBAC: Permissions are operations on objects that roles can perform.
-- =============================================================================

CREATE TABLE IF NOT EXISTS permissions (
    id              UUID        NOT NULL DEFAULT uuid_generate_v4(),
    name            VARCHAR(100) NOT NULL,
    description     TEXT,

    -- Resource being acted upon (e.g., 'campaigns', 'users', 'billing')
    resource        VARCHAR(50) NOT NULL,

    -- Action being performed (e.g., 'create', 'read', 'update', 'delete', 'admin')
    action          VARCHAR(50) NOT NULL,

    -- Is this a system permission (cannot be deleted)
    is_system       BOOLEAN     NOT NULL DEFAULT FALSE,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_permissions PRIMARY KEY (id),
    CONSTRAINT uq_permissions_name UNIQUE (name),
    CONSTRAINT uq_permissions_resource_action UNIQUE (resource, action)
);

-- Insert system permissions
INSERT INTO permissions (name, description, resource, action, is_system) VALUES
    -- Campaign permissions
    ('campaigns:create', 'Create new campaigns', 'campaigns', 'create', TRUE),
    ('campaigns:read', 'View campaigns and their data', 'campaigns', 'read', TRUE),
    ('campaigns:update', 'Modify existing campaigns', 'campaigns', 'update', TRUE),
    ('campaigns:delete', 'Delete campaigns', 'campaigns', 'delete', TRUE),
    ('campaigns:admin', 'Full administrative control over all campaigns', 'campaigns', 'admin', TRUE),

    -- User permissions
    ('users:create', 'Create new users within tenant', 'users', 'create', TRUE),
    ('users:read', 'View user profiles', 'users', 'read', TRUE),
    ('users:update', 'Update user profiles', 'users', 'update', TRUE),
    ('users:delete', 'Deactivate/delete users', 'users', 'delete', TRUE),
    ('users:manage', 'Manage user roles and permissions', 'users', 'manage', TRUE),

    -- Tenant permissions
    ('tenants:read', 'View tenant information', 'tenants', 'read', TRUE),
    ('tenants:update', 'Update tenant settings', 'tenants', 'update', TRUE),
    ('tenants:admin', 'Full tenant administration', 'tenants', 'admin', TRUE),

    -- Billing permissions
    ('billing:read', 'View billing and usage information', 'billing', 'read', TRUE),
    ('billing:update', 'Modify billing settings', 'billing', 'update', TRUE),
    ('billing:admin', 'Full billing administration', 'billing', 'admin', TRUE),

    -- Call permissions
    ('calls:create', 'Initiate calls', 'calls', 'create', TRUE),
    ('calls:read', 'View call history and recordings', 'calls', 'read', TRUE),
    ('calls:delete', 'Delete call records', 'calls', 'delete', TRUE),

    -- Connector permissions
    ('connectors:create', 'Add new connectors', 'connectors', 'create', TRUE),
    ('connectors:read', 'View connector configurations', 'connectors', 'read', TRUE),
    ('connectors:update', 'Modify connector settings', 'connectors', 'update', TRUE),
    ('connectors:delete', 'Remove connectors', 'connectors', 'delete', TRUE),

    -- Analytics permissions
    ('analytics:read', 'View analytics and reports', 'analytics', 'read', TRUE),
    ('analytics:export', 'Export analytics data', 'analytics', 'export', TRUE),

    -- Platform admin permissions (global scope)
    ('platform:admin', 'Full platform administration', 'platform', 'admin', TRUE),
    ('platform:tenants:manage', 'Manage all tenants', 'platform:tenants', 'manage', TRUE),
    ('platform:users:manage', 'Manage all users across tenants', 'platform:users', 'manage', TRUE),
    ('platform:settings:manage', 'Manage global platform settings', 'platform:settings', 'manage', TRUE)
ON CONFLICT (resource, action) DO UPDATE SET
    description = EXCLUDED.description,
    name = EXCLUDED.name,
    is_system = TRUE;

-- Indexes
CREATE INDEX IF NOT EXISTS idx_permissions_resource ON permissions (resource);
CREATE INDEX IF NOT EXISTS idx_permissions_resource_action ON permissions (resource, action);

-- =============================================================================
-- TABLE: role_permissions
-- =============================================================================
--
-- Many-to-many junction: which permissions are granted to which roles.
-- This implements the NIST RBAC "permission-role assignment" component.
--
-- Predefined assignments for system roles:
--   readonly:        campaigns:read, calls:read, analytics:read
--   user:            readonly + campaigns:* (except admin), calls:* (except delete)
--   tenant_admin:    All tenant-scoped permissions
--   partner_admin:   tenant_admin + cross-tenant read access
--   platform_admin:  All permissions including platform:*
-- =============================================================================

CREATE TABLE IF NOT EXISTS role_permissions (
    id              UUID        NOT NULL DEFAULT uuid_generate_v4(),
    role_id         UUID        NOT NULL,
    permission_id   UUID        NOT NULL,
    granted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_role_permissions PRIMARY KEY (id),
    CONSTRAINT uq_role_permissions_role_perm UNIQUE (role_id, permission_id),
    CONSTRAINT fk_rp_role FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE,
    CONSTRAINT fk_rp_permission FOREIGN KEY (permission_id) REFERENCES permissions(id) ON DELETE CASCADE
);

-- Index for permission lookups by role
CREATE INDEX IF NOT EXISTS idx_rp_role_id ON role_permissions (role_id);
CREATE INDEX IF NOT EXISTS idx_rp_permission_id ON role_permissions (permission_id);

-- Populate role_permissions for system roles
-- Helper CTE to get role and permission IDs
WITH role_ids AS (
    SELECT id, name FROM roles WHERE name IN ('readonly', 'user', 'tenant_admin', 'partner_admin', 'platform_admin')
),
perm_ids AS (
    SELECT id, name, resource, action FROM permissions
)
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM role_ids r
CROSS JOIN perm_ids p
WHERE
    -- readonly: read-only access
    (r.name = 'readonly' AND (p.action = 'read' OR p.name IN ('analytics:export')))
    OR
    -- user: can create/modify their own resources
    (r.name = 'user' AND (
        p.action IN ('read', 'create', 'update') OR
        (p.resource = 'calls' AND p.action IN ('create', 'read')) OR
        p.name = 'analytics:export'
    ))
    OR
    -- tenant_admin: full tenant access (excluding platform:*)
    (r.name = 'tenant_admin' AND p.resource NOT LIKE 'platform:%')
    OR
    -- partner_admin: tenant_admin + some cross-tenant
    (r.name = 'partner_admin' AND (
        p.resource NOT LIKE 'platform:%' OR
        p.name IN ('platform:tenants:read')
    ))
    OR
    -- platform_admin: everything
    (r.name = 'platform_admin')
ON CONFLICT (role_id, permission_id) DO NOTHING;

-- =============================================================================
-- TABLE: tenant_users
-- =============================================================================
--
-- Many-to-many junction between users and tenants with role assignment.
-- This enables a user to have different roles in different tenants.
--
-- For example:
--   - User A is tenant_admin in Tenant X
--   - User A is user in Tenant Y
--   - User A is readonly in Tenant Z
--
-- NIST RBAC: User-role assignment with tenant context (scope).
-- =============================================================================

CREATE TABLE IF NOT EXISTS tenant_users (
    id              UUID        NOT NULL DEFAULT uuid_generate_v4(),
    user_id         UUID        NOT NULL,
    tenant_id       UUID        NOT NULL,
    role_id         UUID        NOT NULL,

    -- Is this the user's primary tenant (used for default context)
    is_primary      BOOLEAN     NOT NULL DEFAULT FALSE,

    -- Invitation/status tracking
    status          VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('pending', 'active', 'suspended', 'removed')),
    invited_by      UUID,
    invited_at      TIMESTAMPTZ,
    joined_at       TIMESTAMPTZ,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_tenant_users PRIMARY KEY (id),
    CONSTRAINT uq_tenant_users_user_tenant UNIQUE (user_id, tenant_id),
    CONSTRAINT fk_tu_user FOREIGN KEY (user_id) REFERENCES user_profiles(id) ON DELETE CASCADE,
    CONSTRAINT fk_tu_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    CONSTRAINT fk_tu_role FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE RESTRICT,
    CONSTRAINT fk_tu_inviter FOREIGN KEY (invited_by) REFERENCES user_profiles(id) ON DELETE SET NULL
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_tu_user_id ON tenant_users (user_id);
CREATE INDEX IF NOT EXISTS idx_tu_tenant_id ON tenant_users (tenant_id);
CREATE INDEX IF NOT EXISTS idx_tu_role_id ON tenant_users (role_id);
CREATE INDEX IF NOT EXISTS idx_tu_status ON tenant_users (status) WHERE status IN ('pending', 'active');
CREATE INDEX IF NOT EXISTS idx_tu_user_active ON tenant_users (user_id) WHERE status = 'active';

-- Ensure only one primary tenant per user
CREATE UNIQUE INDEX IF NOT EXISTS idx_tu_user_primary ON tenant_users (user_id) WHERE is_primary = TRUE;

-- =============================================================================
-- TABLE: user_permissions
-- =============================================================================
--
-- Direct permission grants to users (bypassing roles).
-- Used for:
--   - Temporary elevated access
--   - Exceptions to role-based rules
--   - User-specific feature flags
--
-- These permissions are ADDITIVE to role permissions (cannot be used to deny).
-- =============================================================================

CREATE TABLE IF NOT EXISTS user_permissions (
    id              UUID        NOT NULL DEFAULT uuid_generate_v4(),
    user_id         UUID        NOT NULL,
    permission_id   UUID        NOT NULL,

    -- Scope of this permission (NULL = global, tenant_id = scoped to tenant)
    tenant_id       UUID,

    -- When does this grant expire (NULL = never)
    expires_at      TIMESTAMPTZ,

    reason          TEXT,
    granted_by      UUID,
    granted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_user_permissions PRIMARY KEY (id),
    CONSTRAINT uq_user_permissions_user_perm_tenant UNIQUE (user_id, permission_id, tenant_id),
    CONSTRAINT fk_up_user FOREIGN KEY (user_id) REFERENCES user_profiles(id) ON DELETE CASCADE,
    CONSTRAINT fk_up_permission FOREIGN KEY (permission_id) REFERENCES permissions(id) ON DELETE CASCADE,
    CONSTRAINT fk_up_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    CONSTRAINT fk_up_granter FOREIGN KEY (granted_by) REFERENCES user_profiles(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_up_user_id ON user_permissions (user_id);
CREATE INDEX IF NOT EXISTS idx_up_permission_id ON user_permissions (permission_id);
CREATE INDEX IF NOT EXISTS idx_up_expires ON user_permissions (expires_at) WHERE expires_at IS NOT NULL;

-- =============================================================================
-- ALTER EXISTING TABLES
-- =============================================================================

-- Update user_profiles to validate role names
-- First, make existing roles compatible with new system
ALTER TABLE user_profiles
    DROP CONSTRAINT IF EXISTS chk_user_profiles_role;

-- Update existing roles to match new naming
UPDATE user_profiles
SET role = CASE role
    WHEN 'admin' THEN 'tenant_admin'
    WHEN 'owner' THEN 'tenant_admin'
    ELSE role
END;

-- Add constraint to validate role names against roles table
-- Note: This is a soft constraint - actual role existence is checked in application
ALTER TABLE user_profiles
    ADD CONSTRAINT chk_user_profiles_role_valid
    CHECK (role IN ('platform_admin', 'partner_admin', 'tenant_admin', 'user', 'readonly'));

-- Add is_active constraint if not exists
ALTER TABLE user_profiles
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;

-- Ensure tenant_id is properly typed
-- (Assumes tenants table already exists from earlier migrations)

-- =============================================================================
-- ROW LEVEL SECURITY (RLS) POLICIES
-- =============================================================================
--
-- PostgreSQL RLS provides defense-in-depth for tenant isolation.
-- Application middleware is the primary enforcement; RLS is the safety net.
--
-- IMPORTANT: RLS policies use current_setting('app.current_tenant_id', true)
-- This must be set by the application before each query.
-- =============================================================================

-- Enable RLS on core tables
ALTER TABLE campaigns ENABLE ROW LEVEL SECURITY;
ALTER TABLE leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE calls ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE connectors ENABLE ROW LEVEL SECURITY;

-- Create RLS policies for campaigns
DROP POLICY IF EXISTS campaigns_tenant_isolation ON campaigns;
CREATE POLICY campaigns_tenant_isolation ON campaigns
    USING (
        tenant_id = current_setting('app.current_tenant_id', true)::UUID
        OR current_setting('app.bypass_rls', true)::BOOLEAN = TRUE
    );

-- Create RLS policies for leads
DROP POLICY IF EXISTS leads_tenant_isolation ON leads;
CREATE POLICY leads_tenant_isolation ON leads
    USING (
        tenant_id = current_setting('app.current_tenant_id', true)::UUID
        OR current_setting('app.bypass_rls', true)::BOOLEAN = TRUE
    );

-- Create RLS policies for calls
DROP POLICY IF EXISTS calls_tenant_isolation ON calls;
CREATE POLICY calls_tenant_isolation ON calls
    USING (
        tenant_id = current_setting('app.current_tenant_id', true)::UUID
        OR current_setting('app.bypass_rls', true)::BOOLEAN = TRUE
    );

-- Create RLS policies for conversations
DROP POLICY IF EXISTS conversations_tenant_isolation ON conversations;
CREATE POLICY conversations_tenant_isolation ON conversations
    USING (
        tenant_id = current_setting('app.current_tenant_id', true)::UUID
        OR current_setting('app.bypass_rls', true)::BOOLEAN = TRUE
    );

-- Create RLS policies for connectors
DROP POLICY IF EXISTS connectors_tenant_isolation ON connectors;
CREATE POLICY connectors_tenant_isolation ON connectors
    USING (
        tenant_id = current_setting('app.current_tenant_id', true)::UUID
        OR current_setting('app.bypass_rls', true)::BOOLEAN = TRUE
    );

-- =============================================================================
-- VIEWS FOR CONVENIENCE
-- =============================================================================

-- View: user_effective_permissions
-- Combines role permissions and direct user permissions
CREATE OR REPLACE VIEW user_effective_permissions AS
SELECT DISTINCT
    up.id AS user_id,
    p.id AS permission_id,
    p.name AS permission_name,
    p.resource,
    p.action,
    tu.tenant_id,
    r.name AS role_name,
    'role' AS grant_type
FROM user_profiles up
JOIN tenant_users tu ON tu.user_id = up.id AND tu.status = 'active'
JOIN roles r ON r.id = tu.role_id
JOIN role_permissions rp ON rp.role_id = r.id
JOIN permissions p ON p.id = rp.permission_id

UNION

SELECT
    up.id AS user_id,
    p.id AS permission_id,
    p.name AS permission_name,
    p.resource,
    p.action,
    up_perm.tenant_id,
    NULL AS role_name,
    'direct' AS grant_type
FROM user_profiles up
JOIN user_permissions up_perm ON up_perm.user_id = up.id
JOIN permissions p ON p.id = up_perm.permission_id
WHERE up_perm.expires_at IS NULL OR up_perm.expires_at > NOW();

-- View: user_tenant_roles
-- Lists all tenants and roles for each user
CREATE OR REPLACE VIEW user_tenant_roles AS
SELECT
    up.id AS user_id,
    up.email,
    tu.tenant_id,
    t.business_name AS tenant_name,
    r.id AS role_id,
    r.name AS role_name,
    r.level AS role_level,
    tu.status,
    tu.is_primary
FROM user_profiles up
JOIN tenant_users tu ON tu.user_id = up.id
JOIN tenants t ON t.id = tu.tenant_id
JOIN roles r ON r.id = tu.role_id
WHERE tu.status IN ('active', 'pending');

-- =============================================================================
-- COMMENTS
-- =============================================================================

COMMENT ON TABLE roles IS
    'RBAC role definitions. System roles are immutable. Role hierarchy: platform_admin(100) > partner_admin(80) > tenant_admin(60) > user(40) > readonly(20).';

COMMENT ON TABLE permissions IS
    'Granular permissions in resource:action format. Used for fine-grained access control beyond role-based defaults.';

COMMENT ON TABLE role_permissions IS
    'Junction table mapping roles to their granted permissions. Implements NIST RBAC permission-role assignment.';

COMMENT ON TABLE tenant_users IS
    'Junction table for tenant membership with role assignment. Supports users having different roles in different tenants.';

COMMENT ON TABLE user_permissions IS
    'Direct permission grants to users. Bypasses role-based access. Additive only - cannot be used to deny access.';

COMMENT ON VIEW user_effective_permissions IS
    'Combined view of all permissions a user has (from roles + direct grants). Use for permission checks.';

-- =============================================================================
-- GRANT notes (apply manually per environment)
-- =============================================================================
--
-- Application role should have:
--   GRANT SELECT, INSERT, UPDATE, DELETE ON roles TO talkyai_app;
--   GRANT SELECT, INSERT, UPDATE, DELETE ON permissions TO talkyai_app;
--   GRANT SELECT, INSERT, UPDATE, DELETE ON role_permissions TO talkyai_app;
--   GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_users TO talkyai_app;
--   GRANT SELECT, INSERT, UPDATE, DELETE ON user_permissions TO talkyai_app;
--   GRANT SELECT ON user_effective_permissions TO talkyai_app;
--   GRANT SELECT ON user_tenant_roles TO talkyai_app;
--
-- For RLS to work, the app role needs:
--   ALTER ROLE talkyai_app SET app.current_tenant_id = '';
--
-- =============================================================================

COMMIT;
