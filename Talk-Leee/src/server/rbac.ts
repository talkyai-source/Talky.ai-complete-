import crypto from "node:crypto";
import { getSql, isDatabaseConfigured } from "@/server/db";
import { captureMessage } from "@/lib/monitoring";

export type RoleName = "platform_admin" | "partner_admin" | "tenant_admin" | "user" | "readonly";

export type PermissionName =
    | "manage_partners"
    | "manage_tenants"
    | "manage_users"
    | "view_billing"
    | "start_call"
    | "view_calls"
    | "manage_agent_settings";

export type PartnerStatus = "active" | "suspended" | "inactive" | "disabled";

export type TenantStatus = "active" | "suspended" | "inactive" | "disabled";

export type PartnerRow = {
    partner_id: string;
    display_name: string;
    status: PartnerStatus;
    allow_transfer: boolean;
    created_at: Date;
    updated_at: Date;
};

export type TenantRow = {
    id: string;
    partner_id: string;
    name: string;
    status: TenantStatus;
    created_at: Date;
    updated_at: Date;
};

export type UsageAccountRow = {
    id: string;
    partner_id: string;
    tenant_id: string;
    created_at: Date;
};

export type BillingAccountRow = {
    id: string;
    partner_id: string;
    created_at: Date;
};

export type AuthzContext = {
    userId: string;
    platformRole: RoleName | null;
    partnerRoles: Array<{ partnerId: string; role: RoleName }>;
    tenantRoles: Array<{ tenantId: string; partnerId: string; role: RoleName; tenantStatus: TenantStatus }>;
    permissions: Set<string>;
};

let rbacSchemaReady: Promise<void> | undefined;
let rbacSeedReady: Promise<void> | undefined;

function normalizePartnerId(raw: string) {
    return raw.trim().toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-").replace(/^-+|-+$/g, "");
}

function normalizeTenantId(raw: string) {
    return raw.trim();
}

function isUuid(input: string) {
    return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(input.trim());
}

function isSafeId(input: string) {
    const s = input.trim();
    if (!s) return false;
    if (s.length > 120) return false;
    if (isUuid(s)) return true;
    return /^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,118}[a-zA-Z0-9]$/.test(s);
}

async function roleIdByName(roleName: RoleName) {
    const sql = getSql();
    const rows = await sql<{ id: string }[]>`
        select id
        from roles
        where name = ${roleName}
        limit 1
    `;
    const id = rows[0]?.id;
    if (!id) throw new Error(`RBAC role missing: ${roleName}`);
    return id;
}

export async function ensureRbacSchema() {
    if (rbacSchemaReady) return rbacSchemaReady;
    rbacSchemaReady = (async () => {
        if (!isDatabaseConfigured()) return;
        const sql = getSql();

        await sql.unsafe(`
            create table if not exists roles (
                id uuid primary key,
                name text not null unique,
                created_at timestamptz not null default now()
            )
        `);

        await sql.unsafe(`
            create table if not exists permissions (
                id uuid primary key,
                name text not null unique,
                created_at timestamptz not null default now()
            )
        `);

        await sql.unsafe(`
            create table if not exists role_permissions (
                role_id uuid not null references roles(id) on delete cascade,
                permission_id uuid not null references permissions(id) on delete cascade,
                created_at timestamptz not null default now(),
                primary key (role_id, permission_id)
            )
        `);
        await sql.unsafe(`create index if not exists role_permissions_role_id_idx on role_permissions (role_id)`);
        await sql.unsafe(`create index if not exists role_permissions_permission_id_idx on role_permissions (permission_id)`);

        await sql.unsafe(`
            create table if not exists partners (
                partner_id text primary key,
                display_name text not null,
                status text not null default 'active',
                allow_transfer boolean not null default true,
                created_at timestamptz not null default now(),
                updated_at timestamptz not null default now()
            )
        `);
        await sql.unsafe(`alter table partners add column if not exists status text`);
        await sql.unsafe(`update partners set status = 'active' where status is null or status not in ('active','suspended','inactive','disabled')`);
        await sql.unsafe(`alter table partners alter column status set default 'active'`);
        await sql.unsafe(`alter table partners alter column status set not null`);
        await sql.unsafe(`alter table partners drop constraint if exists partners_status_check`);
        await sql.unsafe(`alter table partners add constraint partners_status_check check (status in ('active','suspended','inactive','disabled'))`);
        await sql.unsafe(`create index if not exists partners_display_name_idx on partners (display_name)`);
        await sql.unsafe(`create index if not exists partners_status_idx on partners (status)`);

        await sql.unsafe(`
            create table if not exists tenants (
                id text primary key,
                partner_id text not null references partners(partner_id) on delete restrict,
                name text not null,
                status text not null check (status in ('active','suspended','inactive','disabled')) default 'active',
                created_at timestamptz not null default now(),
                updated_at timestamptz not null default now()
            )
        `);
        await sql.unsafe(`update tenants set status = 'active' where status is null or status not in ('active','suspended','inactive','disabled')`);
        await sql.unsafe(`alter table tenants alter column status set default 'active'`);
        await sql.unsafe(`alter table tenants alter column status set not null`);
        await sql.unsafe(`alter table tenants drop constraint if exists tenants_status_check`);
        await sql.unsafe(`alter table tenants add constraint tenants_status_check check (status in ('active','suspended','inactive','disabled'))`);
        await sql.unsafe(`create index if not exists tenants_partner_id_idx on tenants (partner_id)`);
        await sql.unsafe(`create index if not exists tenants_status_idx on tenants (status)`);

        await sql.unsafe(`
            create table if not exists tenant_users (
                id uuid primary key,
                user_id uuid not null references users(id) on delete cascade,
                tenant_id text not null references tenants(id) on delete cascade,
                role_id uuid not null references roles(id) on delete restrict,
                created_at timestamptz not null default now(),
                unique (user_id, tenant_id)
            )
        `);
        await sql.unsafe(`create index if not exists tenant_users_user_id_idx on tenant_users (user_id)`);
        await sql.unsafe(`create index if not exists tenant_users_tenant_id_idx on tenant_users (tenant_id)`);
        await sql.unsafe(`create index if not exists tenant_users_role_id_idx on tenant_users (role_id)`);

        await sql.unsafe(`
            create table if not exists partner_users (
                id uuid primary key,
                user_id uuid not null references users(id) on delete cascade,
                partner_id text not null references partners(partner_id) on delete cascade,
                role_id uuid not null references roles(id) on delete restrict,
                created_at timestamptz not null default now(),
                unique (user_id, partner_id)
            )
        `);
        await sql.unsafe(`create index if not exists partner_users_user_id_idx on partner_users (user_id)`);
        await sql.unsafe(`create index if not exists partner_users_partner_id_idx on partner_users (partner_id)`);
        await sql.unsafe(`create index if not exists partner_users_role_id_idx on partner_users (role_id)`);

        await sql.unsafe(`
            create table if not exists platform_users (
                user_id uuid primary key references users(id) on delete cascade,
                role_id uuid not null references roles(id) on delete restrict,
                created_at timestamptz not null default now()
            )
        `);
        await sql.unsafe(`create index if not exists platform_users_role_id_idx on platform_users (role_id)`);

        await sql.unsafe(`
            create table if not exists billing_accounts (
                id uuid primary key,
                partner_id text not null references partners(partner_id) on delete cascade,
                created_at timestamptz not null default now()
            )
        `);
        await sql.unsafe(`create unique index if not exists billing_accounts_partner_id_unique on billing_accounts (partner_id)`);

        await sql.unsafe(`
            create table if not exists usage_accounts (
                id uuid primary key,
                partner_id text not null references partners(partner_id) on delete cascade,
                tenant_id text not null references tenants(id) on delete cascade,
                created_at timestamptz not null default now()
            )
        `);
        await sql.unsafe(`create unique index if not exists usage_accounts_tenant_id_unique on usage_accounts (tenant_id)`);
        await sql.unsafe(`create index if not exists usage_accounts_partner_id_idx on usage_accounts (partner_id)`);

        await sql.unsafe(`
            create table if not exists tenant_accounts (
                tenant_id text primary key references tenants(id) on delete cascade,
                usage_account_id uuid not null references usage_accounts(id) on delete restrict,
                billing_account_id uuid not null references billing_accounts(id) on delete restrict,
                created_at timestamptz not null default now()
            )
        `);
        await sql.unsafe(`create index if not exists tenant_accounts_usage_account_id_idx on tenant_accounts (usage_account_id)`);
        await sql.unsafe(`create index if not exists tenant_accounts_billing_account_id_idx on tenant_accounts (billing_account_id)`);
    })();
    return rbacSchemaReady;
}

export async function ensureTenantAccounts(input: { tenantId: string; partnerId: string }) {
    if (!isDatabaseConfigured()) return { ok: false as const, status: 503 as const };
    await ensureRbacSeed();
    const sql = getSql();

    const partnerId = normalizePartnerId(input.partnerId);
    const tenantId = normalizeTenantId(input.tenantId);
    if (!partnerId || !tenantId) return { ok: false as const, status: 400 as const };

    const billingRows = await sql<{ id: string }[]>`
        insert into billing_accounts (id, partner_id, created_at)
        values (${crypto.randomUUID()}::uuid, ${partnerId}, now())
        on conflict (partner_id)
        do update set partner_id = excluded.partner_id
        returning id::text as id
    `;
    const billingAccountId = billingRows[0]?.id ?? null;
    if (!billingAccountId) return { ok: false as const, status: 500 as const };

    const usageRows = await sql<{ id: string }[]>`
        insert into usage_accounts (id, partner_id, tenant_id, created_at)
        values (${crypto.randomUUID()}::uuid, ${partnerId}, ${tenantId}, now())
        on conflict (tenant_id)
        do update set partner_id = excluded.partner_id
        returning id::text as id
    `;
    const usageAccountId = usageRows[0]?.id ?? null;
    if (!usageAccountId) return { ok: false as const, status: 500 as const };

    await sql`
        insert into tenant_accounts (tenant_id, usage_account_id, billing_account_id, created_at)
        values (${tenantId}, ${usageAccountId}::uuid, ${billingAccountId}::uuid, now())
        on conflict (tenant_id)
        do update set usage_account_id = excluded.usage_account_id, billing_account_id = excluded.billing_account_id
    `;

    return { ok: true as const, usageAccountId, billingAccountId };
}

export async function getTenantAccounts(input: { tenantId: string }) {
    if (!isDatabaseConfigured()) return { ok: false as const, status: 503 as const };
    await ensureRbacSeed();
    const sql = getSql();
    const tenantId = normalizeTenantId(input.tenantId);
    if (!tenantId) return { ok: false as const, status: 400 as const };
    const rows = await sql<{ usage_account_id: string; billing_account_id: string; partner_id: string }[]>`
        select ta.usage_account_id::text, ta.billing_account_id::text, t.partner_id
        from tenant_accounts ta
        join tenants t on t.id = ta.tenant_id
        where ta.tenant_id = ${tenantId}
        limit 1
    `;
    const row = rows[0];
    if (!row) return { ok: false as const, status: 404 as const };
    return { ok: true as const, usageAccountId: row.usage_account_id, billingAccountId: row.billing_account_id, partnerId: row.partner_id };
}

export async function ensureRbacSeed() {
    if (rbacSeedReady) return rbacSeedReady;
    rbacSeedReady = (async () => {
        if (!isDatabaseConfigured()) return;
        await ensureRbacSchema();
        const sql = getSql();

        const roles: RoleName[] = ["platform_admin", "partner_admin", "tenant_admin", "user", "readonly"];
        for (const name of roles) {
            await sql`
                insert into roles (id, name, created_at)
                values (${crypto.randomUUID()}::uuid, ${name}, now())
                on conflict (name) do nothing
            `;
        }

        const permissions: PermissionName[] = [
            "manage_partners",
            "manage_tenants",
            "manage_users",
            "view_billing",
            "start_call",
            "view_calls",
            "manage_agent_settings",
        ];
        for (const name of permissions) {
            await sql`
                insert into permissions (id, name, created_at)
                values (${crypto.randomUUID()}::uuid, ${name}, now())
                on conflict (name) do nothing
            `;
        }

        const rolePerms: Array<{ role: RoleName; perms: PermissionName[] }> = [
            {
                role: "platform_admin",
                perms: ["manage_partners", "manage_tenants", "manage_users", "view_billing", "start_call", "view_calls", "manage_agent_settings"],
            },
            { role: "partner_admin", perms: ["manage_tenants", "manage_users", "view_billing", "start_call", "view_calls", "manage_agent_settings"] },
            { role: "tenant_admin", perms: ["manage_users", "start_call", "view_calls", "manage_agent_settings"] },
            { role: "user", perms: ["start_call", "view_calls"] },
            { role: "readonly", perms: ["view_calls"] },
        ];

        for (const rp of rolePerms) {
            const roleId = await roleIdByName(rp.role);
            await sql`
                insert into role_permissions (role_id, permission_id, created_at)
                select ${roleId}::uuid, p.id, now()
                from permissions p
                where p.name = any (${sql.array(rp.perms)})
                on conflict (role_id, permission_id) do nothing
            `;
        }
    })();
    return rbacSeedReady;
}

export async function ensureDefaultPartnerAndTenantForUser(input: { userId: string; email: string; businessName?: string | null }) {
    if (!isDatabaseConfigured()) return;
    await ensureRbacSeed();
    const sql = getSql();

    const tenantRows = await sql<{ tenant_id: string }[]>`
        select tenant_id
        from tenant_users
        where user_id = ${input.userId}::uuid
        limit 1
    `;
    if (tenantRows.length > 0) return;

    const partnerId = "default";
    await sql`
        insert into partners (partner_id, display_name, status, allow_transfer, created_at, updated_at)
        values (${partnerId}, 'Default', 'active', true, now(), now())
        on conflict (partner_id)
        do update set status = 'active', updated_at = now()
    `;

    const tenantBase = input.businessName?.trim() ? input.businessName.trim() : input.email.split("@")[0] ?? "tenant";
    const tenantId = `t_${crypto.randomUUID()}`;
    const tenantName = tenantBase.length > 2 ? tenantBase.slice(0, 80) : "Tenant";

    await sql`
        insert into tenants (id, partner_id, name, status, created_at, updated_at)
        values (${tenantId}, ${partnerId}, ${tenantName}, 'active', now(), now())
        on conflict (id) do nothing
    `;

    const tenantAdminRoleId = await roleIdByName("tenant_admin");
    await sql`
        insert into tenant_users (id, user_id, tenant_id, role_id, created_at)
        values (${crypto.randomUUID()}::uuid, ${input.userId}::uuid, ${tenantId}, ${tenantAdminRoleId}::uuid, now())
        on conflict (user_id, tenant_id) do nothing
    `;

    await ensureTenantAccounts({ tenantId, partnerId }).catch(() => undefined);
}

export async function findPartnerById(partnerIdRaw: string): Promise<PartnerRow | null> {
    if (!isDatabaseConfigured()) return null;
    await ensureRbacSeed();
    const sql = getSql();
    const partnerId = normalizePartnerId(partnerIdRaw);
    if (!partnerId) return null;
    const rows = await sql<PartnerRow[]>`
        select partner_id, display_name, status, allow_transfer, created_at, updated_at
        from partners
        where partner_id = ${partnerId}
        limit 1
    `;
    return rows[0] ?? null;
}

export async function findTenantById(tenantIdRaw: string): Promise<TenantRow | null> {
    if (!isDatabaseConfigured()) return null;
    await ensureRbacSeed();
    const sql = getSql();
    const tenantId = normalizeTenantId(tenantIdRaw);
    if (!tenantId) return null;
    const rows = await sql<TenantRow[]>`
        select id, partner_id, name, status, created_at, updated_at
        from tenants
        where id = ${tenantId}
        limit 1
    `;
    return rows[0] ?? null;
}

async function loadUserAuthzContext(userId: string): Promise<AuthzContext> {
    await ensureRbacSeed();
    const sql = getSql();

    const platformRoleRows = await sql<{ role: RoleName }[]>`
        select r.name as role
        from platform_users pu
        join roles r on r.id = pu.role_id
        where pu.user_id = ${userId}::uuid
        limit 1
    `;
    const platformRole = (platformRoleRows[0]?.role ?? null) as RoleName | null;

    const partnerRoleRows = await sql<{ partner_id: string; role: RoleName }[]>`
        select pu.partner_id, r.name as role
        from partner_users pu
        join roles r on r.id = pu.role_id
        where pu.user_id = ${userId}::uuid
    `;

    const tenantRoleRows = await sql<{ tenant_id: string; partner_id: string; role: RoleName; tenant_status: TenantStatus }[]>`
        select tu.tenant_id, t.partner_id, r.name as role, t.status as tenant_status
        from tenant_users tu
        join tenants t on t.id = tu.tenant_id
        join roles r on r.id = tu.role_id
        where tu.user_id = ${userId}::uuid
    `;

    const roleIdsRows = await sql<{ role_id: string }[]>`
        select distinct role_id
        from (
            select role_id from platform_users where user_id = ${userId}::uuid
            union all
            select role_id from partner_users where user_id = ${userId}::uuid
            union all
            select role_id from tenant_users where user_id = ${userId}::uuid
        ) x
    `;

    const roleIds = roleIdsRows.map((r) => r.role_id);
    const permissions = new Set<string>();
    if (roleIds.length > 0) {
        const permRows = await sql<{ name: string }[]>`
            select distinct p.name
            from role_permissions rp
            join permissions p on p.id = rp.permission_id
            where rp.role_id = any (${sql.array(roleIds)}::uuid[])
        `;
        for (const p of permRows) permissions.add(p.name);
    }

    return {
        userId,
        platformRole,
        partnerRoles: partnerRoleRows.map((r) => ({ partnerId: r.partner_id, role: r.role })),
        tenantRoles: tenantRoleRows.map((r) => ({
            tenantId: r.tenant_id,
            partnerId: r.partner_id,
            role: r.role,
            tenantStatus: r.tenant_status,
        })),
        permissions,
    };
}

export async function getAuthzContextForUser(userId: string): Promise<AuthzContext> {
    if (!isDatabaseConfigured()) {
        return { userId, platformRole: null, partnerRoles: [], tenantRoles: [], permissions: new Set() };
    }
    return loadUserAuthzContext(userId);
}

export function canAccessPartner(input: { ctx: AuthzContext; partnerId: string }) {
    const partnerIdNorm = normalizePartnerId(input.partnerId);
    if (!partnerIdNorm) return false;
    if (input.ctx.platformRole === "platform_admin") return true;
    return input.ctx.partnerRoles.some((r) => r.partnerId === partnerIdNorm && r.role === "partner_admin");
}

export function canAccessTenant(input: { ctx: AuthzContext; tenantId: string; partnerId: string }) {
    const partnerIdNorm = normalizePartnerId(input.partnerId);
    const tenantIdNorm = normalizeTenantId(input.tenantId);
    if (!partnerIdNorm || !tenantIdNorm) return false;
    if (input.ctx.platformRole === "platform_admin") return true;
    if (input.ctx.partnerRoles.some((r) => r.partnerId === partnerIdNorm && r.role === "partner_admin")) return true;
    return input.ctx.tenantRoles.some((r) => r.tenantId === tenantIdNorm && r.partnerId === partnerIdNorm);
}

export function hasPermission(input: { ctx: AuthzContext; permission: string }) {
    if (input.ctx.platformRole === "platform_admin") return true;
    return input.ctx.permissions.has(input.permission);
}

export async function hasPermissionInPartner(input: { ctx: AuthzContext; partnerId: string; permission: string }) {
    const partnerId = normalizePartnerId(input.partnerId);
    if (!partnerId) return false;
    if (input.ctx.platformRole === "platform_admin") return true;
    if (!isDatabaseConfigured()) return false;
    await ensureRbacSeed();
    const sql = getSql();
    const rows = await sql<{ ok: number }[]>`
        select 1 as ok
        from partner_users pu
        join role_permissions rp on rp.role_id = pu.role_id
        join permissions p on p.id = rp.permission_id
        where pu.user_id = ${input.ctx.userId}::uuid
          and pu.partner_id = ${partnerId}
          and p.name = ${input.permission}
        limit 1
    `;
    if (rows.length > 0) return true;
    const platformRows = await sql<{ ok: number }[]>`
        select 1 as ok
        from platform_users pu
        join role_permissions rp on rp.role_id = pu.role_id
        join permissions p on p.id = rp.permission_id
        where pu.user_id = ${input.ctx.userId}::uuid
          and p.name = ${input.permission}
        limit 1
    `;
    return platformRows.length > 0;
}

export async function hasPermissionInTenant(input: { ctx: AuthzContext; tenantId: string; permission: string }) {
    const tenantId = normalizeTenantId(input.tenantId);
    if (!tenantId) return false;
    if (input.ctx.platformRole === "platform_admin") return true;
    if (!isDatabaseConfigured()) return false;
    await ensureRbacSeed();
    const tenant = await findTenantById(tenantId);
    if (!tenant) return false;
    const sql = getSql();
    const rows = await sql<{ ok: number }[]>`
        select 1 as ok
        from (
            select 1
            from tenant_users tu
            join role_permissions rp on rp.role_id = tu.role_id
            join permissions p on p.id = rp.permission_id
            where tu.user_id = ${input.ctx.userId}::uuid
              and tu.tenant_id = ${tenant.id}
              and p.name = ${input.permission}

            union all

            select 1
            from partner_users pu
            join role_permissions rp on rp.role_id = pu.role_id
            join permissions p on p.id = rp.permission_id
            where pu.user_id = ${input.ctx.userId}::uuid
              and pu.partner_id = ${tenant.partner_id}
              and p.name = ${input.permission}

            union all

            select 1
            from platform_users pu
            join role_permissions rp on rp.role_id = pu.role_id
            join permissions p on p.id = rp.permission_id
            where pu.user_id = ${input.ctx.userId}::uuid
              and p.name = ${input.permission}
        ) x
        limit 1
    `;
    return rows.length > 0;
}

export async function requireTenantAccess(input: { ctx: AuthzContext; tenantId: string }) {
    const tenantId = normalizeTenantId(input.tenantId);
    if (!tenantId || !isSafeId(tenantId)) {
        return { ok: false as const, status: 400 as const, code: "invalid_tenant_id" as const };
    }

    const tenant = await findTenantById(tenantId);
    if (!tenant) return { ok: false as const, status: 404 as const, code: "tenant_not_found" as const };

    if (!canAccessTenant({ ctx: input.ctx, tenantId: tenant.id, partnerId: tenant.partner_id })) {
        captureMessage("tenant_access_denied", { user_id: input.ctx.userId, tenant_id: tenant.id, partner_id: tenant.partner_id });
        return { ok: false as const, status: 403 as const, code: "forbidden" as const };
    }

    if (tenant.status === "suspended" && input.ctx.platformRole !== "platform_admin") {
        const isPartnerAdmin = input.ctx.partnerRoles.some((r) => r.partnerId === tenant.partner_id && r.role === "partner_admin");
        if (!isPartnerAdmin) return { ok: false as const, status: 403 as const, code: "tenant_suspended" as const };
    }

    return { ok: true as const, tenant };
}

export async function assignPartnerAdmin(input: { platformCtx: AuthzContext; userId: string; partnerId: string }) {
    if (!hasPermission({ ctx: input.platformCtx, permission: "manage_partners" })) {
        return { ok: false as const, status: 403 as const };
    }
    if (!isDatabaseConfigured()) return { ok: false as const, status: 503 as const };
    await ensureRbacSeed();
    const sql = getSql();

    const partnerId = normalizePartnerId(input.partnerId);
    if (!partnerId) return { ok: false as const, status: 400 as const };
    const partner = await findPartnerById(partnerId);
    if (!partner) return { ok: false as const, status: 404 as const };

    const roleId = await roleIdByName("partner_admin");
    await sql`
        insert into partner_users (id, user_id, partner_id, role_id, created_at)
        values (${crypto.randomUUID()}::uuid, ${input.userId}::uuid, ${partner.partner_id}, ${roleId}::uuid, now())
        on conflict (user_id, partner_id)
        do update set role_id = excluded.role_id
    `;
    return { ok: true as const };
}

export async function upsertPartner(input: { ctx: AuthzContext; partnerId: string; displayName: string; allowTransfer: boolean }) {
    if (!hasPermission({ ctx: input.ctx, permission: "manage_partners" })) {
        return { ok: false as const, status: 403 as const };
    }
    if (!isDatabaseConfigured()) return { ok: false as const, status: 503 as const };
    await ensureRbacSeed();
    const sql = getSql();

    const partnerId = normalizePartnerId(input.partnerId);
    if (!partnerId) return { ok: false as const, status: 400 as const };
    const displayName = input.displayName.trim();
    if (!displayName) return { ok: false as const, status: 400 as const };

    const rows = await sql<PartnerRow[]>`
        insert into partners (partner_id, display_name, status, allow_transfer, created_at, updated_at)
        values (${partnerId}, ${displayName}, 'active', ${input.allowTransfer}, now(), now())
        on conflict (partner_id)
        do update set display_name = excluded.display_name, allow_transfer = excluded.allow_transfer, updated_at = now()
        returning partner_id, display_name, status, allow_transfer, created_at, updated_at
    `;
    return { ok: true as const, partner: rows[0]! };
}

export async function listPartners(input: { ctx: AuthzContext }) {
    if (!hasPermission({ ctx: input.ctx, permission: "manage_partners" })) {
        return { ok: false as const, status: 403 as const };
    }
    if (!isDatabaseConfigured()) return { ok: false as const, status: 503 as const };
    await ensureRbacSeed();
    const sql = getSql();
    const rows = await sql<PartnerRow[]>`
        select partner_id, display_name, status, allow_transfer, created_at, updated_at
        from partners
        order by partner_id asc
    `;
    return { ok: true as const, partners: rows };
}

export async function upsertTenant(input: {
    ctx: AuthzContext;
    partnerId: string;
    tenantId?: string;
    name: string;
    status?: TenantStatus;
}) {
    if (!isDatabaseConfigured()) return { ok: false as const, status: 503 as const };
    await ensureRbacSeed();
    const sql = getSql();

    const partnerId = normalizePartnerId(input.partnerId);
    if (!partnerId) return { ok: false as const, status: 400 as const };
    if (!(await hasPermissionInPartner({ ctx: input.ctx, partnerId, permission: "manage_tenants" }))) {
        return { ok: false as const, status: 403 as const };
    }

    const name = input.name.trim();
    if (!name) return { ok: false as const, status: 400 as const };

    const tenantId = input.tenantId ? normalizeTenantId(input.tenantId) : `t_${crypto.randomUUID()}`;
    if (!isSafeId(tenantId)) return { ok: false as const, status: 400 as const };

    const status: TenantStatus = input.status ?? "active";
    const rows = await sql<TenantRow[]>`
        insert into tenants (id, partner_id, name, status, created_at, updated_at)
        values (${tenantId}, ${partnerId}, ${name}, ${status}, now(), now())
        on conflict (id)
        do update set name = excluded.name, status = excluded.status, updated_at = now()
        returning id, partner_id, name, status, created_at, updated_at
    `;
    return { ok: true as const, tenant: rows[0]! };
}

export async function listPartnerTenants(input: { ctx: AuthzContext; partnerId: string }) {
    if (!isDatabaseConfigured()) return { ok: false as const, status: 503 as const };
    await ensureRbacSeed();
    const sql = getSql();

    const partnerId = normalizePartnerId(input.partnerId);
    if (!partnerId) return { ok: false as const, status: 400 as const };
    if (!(await hasPermissionInPartner({ ctx: input.ctx, partnerId, permission: "manage_tenants" }))) return { ok: false as const, status: 403 as const };

    const rows = await sql<TenantRow[]>`
        select id, partner_id, name, status, created_at, updated_at
        from tenants
        where partner_id = ${partnerId}
        order by created_at desc
    `;
    return { ok: true as const, tenants: rows };
}

export async function assignTenantUserRole(input: { ctx: AuthzContext; tenantId: string; userId: string; role: RoleName }) {
    if (!isDatabaseConfigured()) return { ok: false as const, status: 503 as const };
    await ensureRbacSeed();

    const access = await requireTenantAccess({ ctx: input.ctx, tenantId: input.tenantId });
    if (!access.ok) return access;
    const tenant = access.tenant;

    if (!(await hasPermissionInTenant({ ctx: input.ctx, tenantId: tenant.id, permission: "manage_users" }))) {
        return { ok: false as const, status: 403 as const };
    }

    const role: RoleName = input.role;
    if (role === "platform_admin" || role === "partner_admin") return { ok: false as const, status: 400 as const, code: "invalid_role" as const };

    const sql = getSql();
    const roleId = await roleIdByName(role);
    await sql`
        insert into tenant_users (id, user_id, tenant_id, role_id, created_at)
        values (${crypto.randomUUID()}::uuid, ${input.userId}::uuid, ${tenant.id}, ${roleId}::uuid, now())
        on conflict (user_id, tenant_id)
        do update set role_id = excluded.role_id
    `;

    return { ok: true as const, tenant };
}

