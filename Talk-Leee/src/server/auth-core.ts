import { hash as argon2Hash, verify as argon2Verify } from "@node-rs/argon2";
import crypto from "node:crypto";
import { createHmac, timingSafeEqual } from "node:crypto";
import { captureMessage } from "@/lib/monitoring";
import { getSql, isDatabaseConfigured } from "@/server/db";
import { ensureDefaultPartnerAndTenantForUser, ensureTenantAccounts, getAuthzContextForUser, getTenantAccounts, type RoleName } from "@/server/rbac";

export type AuthUserStatus = "active" | "suspended" | "disabled";
export type AuthRole =
    | RoleName
    | "admin"
    | "white_label_admin";

export type AuthMe = {
    id: string;
    email: string;
    name?: string;
    business_name?: string;
    role: AuthRole;
    minutes_remaining?: number;
    partner_id?: string;
    tenant_id?: string;
};

type DbUserRow = {
    id: string;
    email: string;
    username: string | null;
    password_hash: string;
    status: AuthUserStatus;
    role: AuthRole;
    name: string | null;
    business_name: string | null;
};

type DbSessionRow = {
    session_id: string;
    user_id: string;
    created_at: Date;
    expires_at: Date;
    last_activity_at: Date;
    ip_address: string | null;
    user_agent: string | null;
    revoked: boolean;
    revoked_at: Date | null;
    rotated_from: string | null;
    replaced_by: string | null;
    scope_role: string | null;
    scope_partner_id: string | null;
    scope_tenant_id: string | null;
    scope_usage_account_id: string | null;
    scope_billing_account_id: string | null;
};

type DbUserMfaRow = {
    user_id: string;
    secret_encrypted: string;
    is_enabled: boolean;
};

let authSchemaReady: Promise<void> | undefined;

function nowMs() {
    return Date.now();
}

function parsePositiveInt(input: unknown, fallback: number) {
    const n = typeof input === "string" ? Number(input) : typeof input === "number" ? input : NaN;
    if (!Number.isFinite(n) || n <= 0) return fallback;
    return Math.floor(n);
}

function parseNonNegativeInt(input: unknown, fallback: number) {
    const n = typeof input === "string" ? Number(input) : typeof input === "number" ? input : NaN;
    if (!Number.isFinite(n) || n < 0) return fallback;
    return Math.floor(n);
}

function cookieName() {
    return "talklee_auth_token";
}

function readCookie(header: string, name: string) {
    const parts = header.split(";").map((p) => p.trim());
    for (const part of parts) {
        if (!part) continue;
        const eq = part.indexOf("=");
        if (eq <= 0) continue;
        const k = part.slice(0, eq).trim();
        if (k !== name) continue;
        const v = part.slice(eq + 1).trim();
        try {
            return decodeURIComponent(v);
        } catch {
            return v;
        }
    }
    return null;
}

export function authTokenFromRequest(request: Request) {
    const auth = request.headers.get("authorization") ?? "";
    const m = auth.match(/^Bearer\s+(.+)$/i);
    if (m) return (m[1] ?? "").trim();
    const cookie = request.headers.get("cookie") ?? "";
    const token = readCookie(cookie, cookieName());
    return (token ?? "").trim();
}

export function clientIpFromRequest(request: Request) {
    const forwarded = request.headers.get("x-forwarded-for");
    if (forwarded) {
        const first = forwarded.split(",")[0]?.trim();
        if (first) return first;
    }
    const realIp = request.headers.get("x-real-ip")?.trim();
    if (realIp) return realIp;
    const cf = request.headers.get("cf-connecting-ip")?.trim();
    if (cf) return cf;
    return "";
}

export function userAgentFromRequest(request: Request) {
    return (request.headers.get("user-agent") ?? "").trim();
}

export function normalizeEmail(input: string) {
    return input.trim().toLowerCase();
}

export function normalizeUsername(input: string) {
    const u = input.trim().toLowerCase();
    if (!u) return "";
    return u.replace(/[^a-z0-9._-]/g, "");
}

export function validatePasswordStrength(password: string) {
    const p = password ?? "";
    const failures: string[] = [];
    if (p.length < 12) failures.push("Password must be at least 12 characters");
    if (p.length > 128) failures.push("Password must be at most 128 characters");
    if (!/[a-z]/.test(p)) failures.push("Password must include a lowercase letter");
    if (!/[A-Z]/.test(p)) failures.push("Password must include an uppercase letter");
    if (!/[0-9]/.test(p)) failures.push("Password must include a number");
    if (!/[^A-Za-z0-9]/.test(p)) failures.push("Password must include a symbol");
    if (/\s/.test(p)) failures.push("Password must not include whitespace");
    return { ok: failures.length === 0, failures };
}

export async function hashPassword(password: string) {
    return argon2Hash(password, {
        memoryCost: parsePositiveInt(process.env.AUTH_ARGON2_MEMORY_KIB, 19_456),
        timeCost: parsePositiveInt(process.env.AUTH_ARGON2_TIME_COST, 3),
        parallelism: parsePositiveInt(process.env.AUTH_ARGON2_PARALLELISM, 1),
        outputLen: parsePositiveInt(process.env.AUTH_ARGON2_OUTPUT_LEN, 32),
    });
}

export async function verifyPassword(passwordHash: string, password: string) {
    return argon2Verify(passwordHash, password);
}

let dummyHashPromise: Promise<string> | undefined;

async function dummyHash() {
    if (!dummyHashPromise) {
        dummyHashPromise = hashPassword(`invalid-${crypto.randomUUID()}-${crypto.randomUUID()}`);
    }
    return dummyHashPromise;
}

export function randomSessionId() {
    return crypto.randomBytes(32).toString("base64url");
}

function sessionTtlSeconds() {
    return parsePositiveInt(process.env.AUTH_SESSION_TTL_SECONDS, 60 * 60 * 24 * 7);
}

function sessionIdleTimeoutSeconds() {
    return parsePositiveInt(process.env.AUTH_SESSION_IDLE_TIMEOUT_SECONDS, 30 * 60);
}

function sessionRotationSeconds() {
    return parseNonNegativeInt(process.env.AUTH_SESSION_ROTATE_SECONDS, 6 * 60 * 60);
}

function sessionBindingMode(): "strict" | "soft" {
    const v = String(process.env.AUTH_SESSION_BINDING_MODE ?? "").trim().toLowerCase();
    return v === "soft" ? "soft" : "strict";
}

function isSecureRequest(request: Request) {
    const forwarded = request.headers.get("x-forwarded-proto");
    if (forwarded) return forwarded.split(",")[0]!.trim().toLowerCase() === "https";
    return request.url.startsWith("https:");
}

type AuthSessionScope = { role: AuthRole; partner_id?: string; tenant_id?: string };

function computeScopeFromAuthz(authz: Awaited<ReturnType<typeof getAuthzContextForUser>>): AuthSessionScope {
    if (authz.platformRole === "platform_admin") {
        return { role: "platform_admin" };
    }

    const partnerAdmins = Array.from(new Set(authz.partnerRoles.filter((r) => r.role === "partner_admin").map((r) => r.partnerId)));
    if (partnerAdmins.length > 0) {
        return { role: "partner_admin", ...(partnerAdmins.length === 1 ? { partner_id: partnerAdmins[0] } : {}) };
    }

    if (authz.tenantRoles.length > 0) {
        const rank: Record<RoleName, number> = { platform_admin: 100, partner_admin: 90, tenant_admin: 50, user: 10, readonly: 1 };
        const active = authz.tenantRoles.filter((t) => t.tenantStatus === "active");
        const candidates = active.length > 0 ? active : authz.tenantRoles;
        const top = candidates.slice().sort((a, b) => (rank[b.role] ?? 0) - (rank[a.role] ?? 0))[0];
        if (top) return { role: top.role, partner_id: top.partnerId, tenant_id: top.tenantId };
    }

    return { role: "user" };
}

export type AuthSessionEventType =
    | "session_created"
    | "session_rotated"
    | "session_revoked"
    | "logout_all_sessions"
    | "session_scope_updated";

export type AuthSessionEvent = {
    type: AuthSessionEventType;
    user_id: string;
    session_id?: string;
    rotated_from?: string;
    reason?: string;
    ip_address?: string | null;
    user_agent?: string | null;
    partner_id?: string | null;
    tenant_id?: string | null;
    usage_account_id?: string | null;
    billing_account_id?: string | null;
};

function emitAuthSessionEvent(event: AuthSessionEvent) {
    try {
        captureMessage(`auth.${event.type}`, event);
    } catch {
    }
}

function sessionHandleSecret() {
    const raw = String(process.env.AUTH_SESSION_HANDLE_SECRET ?? "").trim();
    if (raw) return raw;
    if (process.env.NODE_ENV === "production") {
        throw new Error("AUTH_SESSION_HANDLE_SECRET is required in production");
    }
    return "dev-insecure-session-handle-secret";
}

export function sessionHandleForSessionId(sessionId: string) {
    const h = createHmac("sha256", sessionHandleSecret()).update(sessionId, "utf8").digest("base64url");
    return h;
}

function safeEqual(a: string, b: string) {
    const aa = Buffer.from(String(a ?? ""), "utf8");
    const bb = Buffer.from(String(b ?? ""), "utf8");
    if (aa.length !== bb.length) return false;
    try {
        return timingSafeEqual(aa, bb);
    } catch {
        return false;
    }
}

export async function ensureAuthSchema() {
    if (authSchemaReady) return authSchemaReady;
    authSchemaReady = (async () => {
        const sql = getSql();
        await sql.unsafe(`
            create table if not exists users (
                id uuid primary key,
                email text not null,
                username text,
                password_hash text not null,
                status text not null check (status in ('active','suspended','disabled')),
                role text not null default 'user',
                name text,
                business_name text,
                created_at timestamptz not null default now(),
                updated_at timestamptz not null default now()
            )
        `);
        await sql.unsafe(`create unique index if not exists users_email_unique on users (email)`);
        await sql.unsafe(`create unique index if not exists users_username_unique on users (username) where username is not null`);
        await sql.unsafe(`create index if not exists users_status_idx on users (status)`);

        await sql.unsafe(`
            create table if not exists sessions (
                session_id text primary key,
                user_id uuid not null references users(id) on delete cascade,
                created_at timestamptz not null default now(),
                expires_at timestamptz not null,
                last_activity_at timestamptz not null default now(),
                ip_address text,
                user_agent text,
                revoked boolean not null default false,
                revoked_at timestamptz,
                rotated_from text references sessions(session_id) on delete set null,
                replaced_by text references sessions(session_id) on delete set null,
                scope_role text,
                scope_partner_id text,
                scope_tenant_id text,
                scope_usage_account_id uuid,
                scope_billing_account_id uuid
            )
        `);
        await sql.unsafe(`alter table sessions add column if not exists last_activity_at timestamptz`);
        await sql.unsafe(`alter table sessions add column if not exists revoked boolean`);
        await sql.unsafe(`alter table sessions add column if not exists rotated_from text`);
        await sql.unsafe(`alter table sessions add column if not exists replaced_by text`);
        await sql.unsafe(`alter table sessions add column if not exists scope_role text`);
        await sql.unsafe(`alter table sessions add column if not exists scope_partner_id text`);
        await sql.unsafe(`alter table sessions add column if not exists scope_tenant_id text`);
        await sql.unsafe(`alter table sessions add column if not exists scope_usage_account_id uuid`);
        await sql.unsafe(`alter table sessions add column if not exists scope_billing_account_id uuid`);
        await sql.unsafe(`update sessions set last_activity_at = coalesce(last_activity_at, created_at, now()) where last_activity_at is null`);
        await sql.unsafe(`update sessions set revoked = false where revoked is null`);
        await sql.unsafe(`alter table sessions alter column last_activity_at set default now()`);
        await sql.unsafe(`alter table sessions alter column last_activity_at set not null`);
        await sql.unsafe(`alter table sessions alter column revoked set default false`);
        await sql.unsafe(`alter table sessions alter column revoked set not null`);
        await sql.unsafe(`
            do $$
            begin
                alter table sessions add constraint sessions_rotated_from_fk
                    foreign key (rotated_from) references sessions(session_id) on delete set null;
            exception when duplicate_object then
                null;
            end $$;
        `);
        await sql.unsafe(`
            do $$
            begin
                alter table sessions add constraint sessions_replaced_by_fk
                    foreign key (replaced_by) references sessions(session_id) on delete set null;
            exception when duplicate_object then
                null;
            end $$;
        `);
        await sql.unsafe(`create index if not exists sessions_user_id_idx on sessions (user_id)`);
        await sql.unsafe(`create index if not exists sessions_expires_at_idx on sessions (expires_at)`);
        await sql.unsafe(`create index if not exists sessions_revoked_at_idx on sessions (revoked_at)`);
        await sql.unsafe(`create index if not exists sessions_last_activity_at_idx on sessions (last_activity_at)`);
        await sql.unsafe(`create index if not exists sessions_user_id_revoked_expires_at_idx on sessions (user_id, revoked, expires_at)`);
        await sql.unsafe(`create index if not exists sessions_scope_partner_id_idx on sessions (scope_partner_id)`);
        await sql.unsafe(`create index if not exists sessions_scope_tenant_id_idx on sessions (scope_tenant_id)`);
        await sql.unsafe(`create index if not exists sessions_scope_usage_account_id_idx on sessions (scope_usage_account_id)`);
        await sql.unsafe(`create index if not exists sessions_scope_billing_account_id_idx on sessions (scope_billing_account_id)`);

        await sql.unsafe(`
            create table if not exists auth_user_security (
                user_id uuid primary key references users(id) on delete cascade,
                failed_attempt_count int not null default 0,
                last_failed_attempt timestamptz,
                locked_until timestamptz,
                updated_at timestamptz not null default now()
            )
        `);

        await sql.unsafe(`
            create table if not exists auth_rate_limits (
                bucket text not null,
                window_start timestamptz not null,
                count int not null default 0,
                updated_at timestamptz not null default now(),
                primary key (bucket, window_start)
            )
        `);
        await sql.unsafe(`create index if not exists auth_rate_limits_window_idx on auth_rate_limits (window_start)`);

        await sql.unsafe(`
            create table if not exists user_mfa (
                user_id uuid primary key references users(id) on delete cascade,
                secret_encrypted text not null,
                is_enabled boolean not null default false,
                created_at timestamptz not null default now(),
                updated_at timestamptz not null default now()
            )
        `);
        await sql.unsafe(`create index if not exists user_mfa_enabled_idx on user_mfa (is_enabled)`);

        await sql.unsafe(`
            create table if not exists recovery_codes (
                id uuid primary key,
                user_id uuid not null references users(id) on delete cascade,
                code_hash text not null,
                used boolean not null default false,
                created_at timestamptz not null default now()
            )
        `);
        await sql.unsafe(`create index if not exists recovery_codes_user_id_idx on recovery_codes (user_id)`);
        await sql.unsafe(`create index if not exists recovery_codes_used_idx on recovery_codes (used)`);

        await sql.unsafe(`
            create table if not exists user_passkeys (
                id uuid primary key,
                user_id uuid not null references users(id) on delete cascade,
                credential_id text not null,
                public_key bytea not null,
                sign_count bigint not null default 0,
                device_name text,
                created_at timestamptz not null default now(),
                updated_at timestamptz not null default now()
            )
        `);
        await sql.unsafe(`create unique index if not exists user_passkeys_credential_id_unique on user_passkeys (credential_id)`);
        await sql.unsafe(`create index if not exists user_passkeys_user_id_idx on user_passkeys (user_id)`);

        await sql.unsafe(`
            create table if not exists auth_webauthn_challenges (
                challenge text primary key,
                kind text not null check (kind in ('registration','authentication')),
                user_id uuid references users(id) on delete cascade,
                identifier text,
                ip_address text,
                user_agent text,
                expires_at timestamptz not null,
                created_at timestamptz not null default now()
            )
        `);
        await sql.unsafe(`create index if not exists auth_webauthn_challenges_expires_at_idx on auth_webauthn_challenges (expires_at)`);
    })();
    return authSchemaReady;
}

export async function consumeAuthRateLimit(input: { bucket: string; limit: number; windowSeconds: number }) {
    const sql = getSql();
    const now = new Date();
    const windowMs = input.windowSeconds * 1000;
    const start = new Date(Math.floor(now.getTime() / windowMs) * windowMs);

    const rows = await sql<{ count: number }[]>`
        insert into auth_rate_limits (bucket, window_start, count, updated_at)
        values (${input.bucket}, ${start}, 1, now())
        on conflict (bucket, window_start)
        do update set count = auth_rate_limits.count + 1, updated_at = now()
        returning count
    `;
    const count = rows[0]?.count ?? 1;
    const remainingMs = windowMs - (now.getTime() - start.getTime());
    const retryAfterSeconds = Math.max(0, Math.ceil(remainingMs / 1000));
    return { allowed: count <= input.limit, count, retryAfterSeconds };
}

async function findUserByIdentifier(identifier: string): Promise<DbUserRow | null> {
    const sql = getSql();
    const trimmed = identifier.trim();
    if (!trimmed) return null;
    if (trimmed.includes("@")) {
        const email = normalizeEmail(trimmed);
        const rows = await sql<DbUserRow[]>`
            select id, email, username, password_hash, status, role, name, business_name
            from users
            where email = ${email}
            limit 1
        `;
        return rows[0] ?? null;
    }
    const username = normalizeUsername(trimmed);
    if (!username) return null;
    const rows = await sql<DbUserRow[]>`
        select id, email, username, password_hash, status, role, name, business_name
        from users
        where username = ${username}
        limit 1
    `;
    return rows[0] ?? null;
}

async function getUserSecurity(userId: string) {
    const sql = getSql();
    const rows = await sql<{ failed_attempt_count: number; locked_until: Date | null }[]>`
        select failed_attempt_count, locked_until
        from auth_user_security
        where user_id = ${userId}::uuid
        limit 1
    `;
    return rows[0] ?? { failed_attempt_count: 0, locked_until: null };
}

async function recordFailedLoginAttempt(userId: string) {
    const sql = getSql();
    const lockThreshold = parsePositiveInt(process.env.AUTH_LOCK_THRESHOLD, 10);
    const lockSeconds = parsePositiveInt(process.env.AUTH_LOCK_SECONDS, 15 * 60);

    const rows = await sql<{ failed_attempt_count: number; locked_until: Date | null }[]>`
        insert into auth_user_security (user_id, failed_attempt_count, last_failed_attempt, locked_until, updated_at)
        values (${userId}::uuid, 1, now(), null, now())
        on conflict (user_id)
        do update set
            failed_attempt_count = auth_user_security.failed_attempt_count + 1,
            last_failed_attempt = now(),
            updated_at = now()
        returning failed_attempt_count, locked_until
    `;

    const count = rows[0]?.failed_attempt_count ?? 1;
    if (count < lockThreshold) return;

    await sql`
        update auth_user_security
        set locked_until = now() + make_interval(secs => ${lockSeconds}), updated_at = now()
        where user_id = ${userId}::uuid
    `;
}

async function clearFailedLoginState(userId: string) {
    const sql = getSql();
    await sql`
        insert into auth_user_security (user_id, failed_attempt_count, last_failed_attempt, locked_until, updated_at)
        values (${userId}::uuid, 0, null, null, now())
        on conflict (user_id)
        do update set failed_attempt_count = 0, last_failed_attempt = null, locked_until = null, updated_at = now()
    `;
}

function devMeForToken(token: string): AuthMe | null {
    const t = token.trim();
    if (!t) return null;
    if (t === "wl-admin-token") return { id: "usr_platform_admin", email: "platform-admin@example.com", role: "platform_admin" };
    const partner = t.match(/^partner-([a-z0-9-]+)-token$/i);
    if (partner) {
        const partnerId = (partner[1] ?? "").trim().toLowerCase();
        if (partnerId) return { id: `usr_partner_${partnerId}`, email: `partner-${partnerId}@example.com`, role: "partner_admin", partner_id: partnerId };
    }
    if (t === "e2e-token") return { id: "usr_e2e", email: "e2e@example.com", role: "user" };
    if (t === "dev-token") return { id: "usr_dev", email: "dev@example.com", role: "user" };
    return null;
}

export type AuthMeResult = {
    me: AuthMe;
    sessionId: string;
    setCookie?: string;
};

export async function rotateSessionNow(input: {
    sessionId: string;
    expiresAt: Date;
    userId: string;
    request: Request;
    ipAddress: string;
    userAgent: string;
    reason: string;
    scope: {
        role: string;
        partnerId: string | null;
        tenantId: string | null;
        usageAccountId: string | null;
        billingAccountId: string | null;
    };
}) {
    if (!isDatabaseConfigured()) return null;
    await ensureAuthSchema();
    const sql = getSql();

    const newSessionId = randomSessionId();
    await sql.begin(async (tx) => {
        const q = tx as unknown as ReturnType<typeof getSql>;
        await q`
            insert into sessions (
                session_id,
                user_id,
                expires_at,
                last_activity_at,
                ip_address,
                user_agent,
                revoked,
                revoked_at,
                rotated_from,
                replaced_by,
                scope_role,
                scope_partner_id,
                scope_tenant_id,
                scope_usage_account_id,
                scope_billing_account_id
            )
            values (
                ${newSessionId},
                ${input.userId}::uuid,
                ${input.expiresAt},
                now(),
                ${input.ipAddress || null},
                ${input.userAgent || null},
                false,
                null,
                ${input.sessionId},
                null,
                ${input.scope.role || null},
                ${input.scope.partnerId},
                ${input.scope.tenantId},
                ${input.scope.usageAccountId}::uuid,
                ${input.scope.billingAccountId}::uuid
            )
        `;
        await q`
            update sessions
            set revoked = true, revoked_at = now(), replaced_by = ${newSessionId}
            where session_id = ${input.sessionId} and revoked = false and revoked_at is null
        `;
    });

    emitAuthSessionEvent({
        type: "session_rotated",
        user_id: input.userId,
        session_id: newSessionId,
        rotated_from: input.sessionId,
        reason: input.reason,
        ip_address: input.ipAddress || null,
        user_agent: input.userAgent || null,
        partner_id: input.scope.partnerId,
        tenant_id: input.scope.tenantId,
        usage_account_id: input.scope.usageAccountId,
        billing_account_id: input.scope.billingAccountId,
    });

    const setCookie = buildSessionCookie({ sessionId: newSessionId, expiresAt: input.expiresAt, secure: isSecureRequest(input.request) });
    return { sessionId: newSessionId, setCookie };
}

export async function authMeFromRequest(request: Request, options?: { rotate?: boolean }): Promise<AuthMeResult | null> {
    const token = authTokenFromRequest(request);
    if (!token) return null;

    const devEnabled = process.env.NODE_ENV !== "production" && process.env.TALKLEE_DEV_AUTH_TOKENS !== "0";
    if (devEnabled) {
        const dev = devMeForToken(token);
        if (dev) return { me: dev, sessionId: token };
    }

    if (!isDatabaseConfigured()) return null;
    await ensureAuthSchema();

    const sql = getSql();
    const ip = clientIpFromRequest(request);
    const ua = userAgentFromRequest(request);
    const rotate = options?.rotate !== false;

    const authRow = await sql.begin(async (tx) => {
        const q = tx as unknown as ReturnType<typeof getSql>;
        const rows = await q<Array<DbUserRow & DbSessionRow>>`
            select
                s.session_id,
                s.user_id,
                s.created_at,
                s.expires_at,
                s.last_activity_at,
                s.ip_address,
                s.user_agent,
                s.revoked,
                s.revoked_at,
                s.rotated_from,
                s.replaced_by,
                s.scope_role,
                s.scope_partner_id,
                s.scope_tenant_id,
                s.scope_usage_account_id::text as scope_usage_account_id,
                s.scope_billing_account_id::text as scope_billing_account_id,
                u.id,
                u.email,
                u.username,
                u.password_hash,
                u.status,
                u.role,
                u.name,
                u.business_name
            from sessions s
            join users u on u.id = s.user_id
            where s.session_id = ${token}
            limit 1
            for update
        `;

        const row = rows[0];
        if (!row) return { ok: false as const };

        if (row.revoked || row.revoked_at) return { ok: false as const };
        if (row.expires_at.getTime() <= nowMs()) {
            await q`
                update sessions
                set revoked = true, revoked_at = now()
                where session_id = ${token} and revoked = false and revoked_at is null
            `;
            emitAuthSessionEvent({
                type: "session_revoked",
                user_id: row.user_id,
                session_id: row.session_id,
                reason: "expired",
                ip_address: ip || null,
                user_agent: ua || null,
                partner_id: row.scope_partner_id,
                tenant_id: row.scope_tenant_id,
                usage_account_id: row.scope_usage_account_id,
                billing_account_id: row.scope_billing_account_id,
            });
            return { ok: false as const };
        }
        if (row.status !== "active") return { ok: false as const };

        const idleSeconds = sessionIdleTimeoutSeconds();
        const lastActivityMs = row.last_activity_at?.getTime?.() ? row.last_activity_at.getTime() : row.created_at.getTime();
        if (idleSeconds > 0 && lastActivityMs + idleSeconds * 1000 <= nowMs()) {
            await q`
                update sessions
                set revoked = true, revoked_at = now()
                where session_id = ${token} and revoked = false and revoked_at is null
            `;
            emitAuthSessionEvent({
                type: "session_revoked",
                user_id: row.user_id,
                session_id: row.session_id,
                reason: "idle_timeout",
                ip_address: ip || null,
                user_agent: ua || null,
                partner_id: row.scope_partner_id,
                tenant_id: row.scope_tenant_id,
                usage_account_id: row.scope_usage_account_id,
                billing_account_id: row.scope_billing_account_id,
            });
            return { ok: false as const };
        }

        const storedIp = (row.ip_address ?? "").trim();
        const storedUa = (row.user_agent ?? "").trim();
        const ipMismatch = storedIp && ip ? storedIp !== ip : false;
        const uaMismatch = storedUa && ua ? storedUa !== ua : false;
        if (ipMismatch || uaMismatch) {
            if (sessionBindingMode() === "strict") {
                await q`
                    update sessions
                    set revoked = true, revoked_at = now()
                    where session_id = ${token} and revoked = false and revoked_at is null
                `;
                emitAuthSessionEvent({
                    type: "session_revoked",
                    user_id: row.user_id,
                    session_id: row.session_id,
                    reason: "session_binding_mismatch",
                    ip_address: ip || null,
                    user_agent: ua || null,
                    partner_id: row.scope_partner_id,
                    tenant_id: row.scope_tenant_id,
                    usage_account_id: row.scope_usage_account_id,
                    billing_account_id: row.scope_billing_account_id,
                });
                return { ok: false as const };
            }
            try {
                captureMessage("session_binding_mismatch", {
                    session_id: row.session_id,
                    user_id: row.user_id,
                    stored_ip: storedIp || null,
                    stored_ua: storedUa || null,
                    request_ip: ip || null,
                    request_ua: ua || null,
                });
            } catch {
            }
        }

        const rotateSeconds = sessionRotationSeconds();
        if (rotate && rotateSeconds > 0 && row.created_at.getTime() + rotateSeconds * 1000 <= nowMs()) {
            const newSessionId = randomSessionId();
            await q`
                insert into sessions (
                    session_id,
                    user_id,
                    expires_at,
                    last_activity_at,
                    ip_address,
                    user_agent,
                    revoked,
                    revoked_at,
                    rotated_from,
                    replaced_by,
                    scope_role,
                    scope_partner_id,
                    scope_tenant_id,
                    scope_usage_account_id,
                    scope_billing_account_id
                )
                values (
                    ${newSessionId},
                    ${row.user_id}::uuid,
                    ${row.expires_at},
                    now(),
                    ${ip || storedIp || null},
                    ${ua || storedUa || null},
                    false,
                    null,
                    ${row.session_id},
                    null,
                    ${row.scope_role},
                    ${row.scope_partner_id},
                    ${row.scope_tenant_id},
                    ${row.scope_usage_account_id}::uuid,
                    ${row.scope_billing_account_id}::uuid
                )
            `;
            await q`
                update sessions
                set revoked = true, revoked_at = now(), replaced_by = ${newSessionId}
                where session_id = ${row.session_id} and revoked = false and revoked_at is null
            `;
            emitAuthSessionEvent({
                type: "session_rotated",
                user_id: row.user_id,
                session_id: newSessionId,
                rotated_from: row.session_id,
                reason: "interval",
                ip_address: (ip || storedIp) || null,
                user_agent: (ua || storedUa) || null,
                partner_id: row.scope_partner_id,
                tenant_id: row.scope_tenant_id,
                usage_account_id: row.scope_usage_account_id,
                billing_account_id: row.scope_billing_account_id,
            });
            const setCookie = buildSessionCookie({ sessionId: newSessionId, expiresAt: row.expires_at, secure: isSecureRequest(request) });
            return { ok: true as const, row, sessionId: newSessionId, setCookie };
        }

        await q`
            update sessions
            set last_activity_at = now(),
                ip_address = case when ip_address is null or btrim(ip_address) = '' then ${ip || null} else ip_address end,
                user_agent = case when user_agent is null or btrim(user_agent) = '' then ${ua || null} else user_agent end
            where session_id = ${token} and revoked = false and revoked_at is null
        `;
        return { ok: true as const, row, sessionId: row.session_id, setCookie: undefined as string | undefined };
    });

    if (!authRow.ok) return null;

    const row = authRow.row;

    await ensureDefaultPartnerAndTenantForUser({ userId: row.id, email: row.email, businessName: row.business_name });
    const authz = await getAuthzContextForUser(row.id);
    const computed = computeScopeFromAuthz(authz);
    const storedRole = (row.scope_role ?? "").trim();
    const storedPartnerId = (row.scope_partner_id ?? "").trim();
    const storedTenantId = (row.scope_tenant_id ?? "").trim();
    const storedUsageAccountId = (row.scope_usage_account_id ?? "").trim();
    const storedBillingAccountId = (row.scope_billing_account_id ?? "").trim();

    let expectedUsageAccountId: string | null = null;
    let expectedBillingAccountId: string | null = null;

    if (computed.tenant_id && computed.partner_id) {
        await ensureTenantAccounts({ tenantId: computed.tenant_id, partnerId: computed.partner_id }).catch(() => undefined);
        const accounts = await getTenantAccounts({ tenantId: computed.tenant_id }).catch(() => ({ ok: false as const, status: 503 as const }));
        if (!accounts.ok) {
            await logoutSession(authRow.sessionId);
            emitAuthSessionEvent({
                type: "session_revoked",
                user_id: row.id,
                session_id: authRow.sessionId,
                reason: "tenant_accounts_unavailable",
                ip_address: ip || null,
                user_agent: ua || null,
                partner_id: computed.partner_id ?? null,
                tenant_id: computed.tenant_id ?? null,
            });
            return null;
        }
        expectedUsageAccountId = accounts.usageAccountId;
        expectedBillingAccountId = accounts.billingAccountId;
    } else if (computed.partner_id) {
        const billingRows = await sql<{ id: string }[]>`
            insert into billing_accounts (id, partner_id, created_at)
            values (${crypto.randomUUID()}::uuid, ${computed.partner_id}, now())
            on conflict (partner_id)
            do update set partner_id = excluded.partner_id
            returning id::text as id
        `;
        expectedBillingAccountId = billingRows[0]?.id ?? null;
    }

    const scopeMissing = !storedRole && !storedPartnerId && !storedTenantId;
    const scopeChanged =
        (storedRole && storedRole !== computed.role) ||
        (storedPartnerId && storedPartnerId !== (computed.partner_id ?? "")) ||
        (storedTenantId && storedTenantId !== (computed.tenant_id ?? ""));

    const accountsMissing =
        (!storedUsageAccountId && expectedUsageAccountId) || (!storedBillingAccountId && expectedBillingAccountId);
    const accountsChanged =
        (storedUsageAccountId && expectedUsageAccountId && storedUsageAccountId !== expectedUsageAccountId) ||
        (storedBillingAccountId && expectedBillingAccountId && storedBillingAccountId !== expectedBillingAccountId);

    if (!scopeMissing) {
        if (storedTenantId) {
            const ok =
                authz.platformRole === "platform_admin" ||
                authz.tenantRoles.some((t) => t.tenantId === storedTenantId && (!storedPartnerId || t.partnerId === storedPartnerId));
            if (!ok) {
                await logoutSession(authRow.sessionId);
                emitAuthSessionEvent({
                    type: "session_revoked",
                    user_id: row.id,
                    session_id: authRow.sessionId,
                    reason: "scope_no_longer_allowed",
                    ip_address: ip || null,
                    user_agent: ua || null,
                    partner_id: storedPartnerId || null,
                    tenant_id: storedTenantId || null,
                });
                return null;
            }
        } else if (storedPartnerId) {
            const ok = authz.platformRole === "platform_admin" || authz.partnerRoles.some((p) => p.partnerId === storedPartnerId && p.role === "partner_admin");
            if (!ok) {
                await logoutSession(authRow.sessionId);
                emitAuthSessionEvent({
                    type: "session_revoked",
                    user_id: row.id,
                    session_id: authRow.sessionId,
                    reason: "scope_no_longer_allowed",
                    ip_address: ip || null,
                    user_agent: ua || null,
                    partner_id: storedPartnerId || null,
                    tenant_id: null,
                });
                return null;
            }
        }
    }

    let sessionId = authRow.sessionId;
    let setCookie = authRow.setCookie;

    if ((scopeChanged || accountsChanged) && !setCookie) {
        const rotated = await rotateSessionNow({
            sessionId: authRow.sessionId,
            expiresAt: row.expires_at,
            userId: row.id,
            request,
            ipAddress: ip,
            userAgent: ua,
            reason: scopeChanged ? "role_or_scope_changed" : "usage_or_billing_changed",
            scope: {
                role: computed.role,
                partnerId: computed.partner_id ?? null,
                tenantId: computed.tenant_id ?? null,
                usageAccountId: expectedUsageAccountId,
                billingAccountId: expectedBillingAccountId,
            },
        });
        if (rotated) {
            sessionId = rotated.sessionId;
            setCookie = rotated.setCookie;
        }
    }

    if (scopeMissing || (scopeChanged && !!setCookie) || accountsMissing || (accountsChanged && !!setCookie)) {
        if (isDatabaseConfigured()) {
            try {
                await sql`
                    update sessions
                    set scope_role = ${computed.role},
                        scope_partner_id = ${computed.partner_id ?? null},
                        scope_tenant_id = ${computed.tenant_id ?? null},
                        scope_usage_account_id = ${expectedUsageAccountId}::uuid,
                        scope_billing_account_id = ${expectedBillingAccountId}::uuid
                    where session_id = ${sessionId}
                `;
            } catch {
            }
        }
        emitAuthSessionEvent({
            type: "session_scope_updated",
            user_id: row.id,
            session_id: sessionId,
            reason: scopeMissing ? "initial_set" : "updated_after_change",
            ip_address: ip || null,
            user_agent: ua || null,
            partner_id: computed.partner_id ?? null,
            tenant_id: computed.tenant_id ?? null,
            usage_account_id: expectedUsageAccountId,
            billing_account_id: expectedBillingAccountId,
        });
    }

    return {
        me: {
            id: row.id,
            email: row.email,
            name: row.name ?? undefined,
            business_name: row.business_name ?? undefined,
            role: computed.role,
            minutes_remaining: 0,
            partner_id: computed.partner_id,
            tenant_id: computed.tenant_id,
        },
        sessionId,
        setCookie: setCookie ?? undefined,
    };
}

export async function registerUser(input: { email: string; password: string; username?: string; name?: string; businessName?: string }) {
    await ensureAuthSchema();
    const sql = getSql();

    const email = normalizeEmail(input.email);
    const username = input.username ? normalizeUsername(input.username) : "";
    const passCheck = validatePasswordStrength(input.password);
    if (!passCheck.ok) {
        return { ok: false as const, code: "weak_password" as const, failures: passCheck.failures };
    }

    const id = crypto.randomUUID();
    const passwordHash = await hashPassword(input.password);
    try {
        const rows = await sql<DbUserRow[]>`
            insert into users (id, email, username, password_hash, status, role, name, business_name, created_at, updated_at)
            values (
                ${id}::uuid,
                ${email},
                ${username.length ? username : null},
                ${passwordHash},
                'active',
                'user',
                ${input.name?.trim() ? input.name.trim() : null},
                ${input.businessName?.trim() ? input.businessName.trim() : null},
                now(),
                now()
            )
            returning id, email, username, password_hash, status, role, name, business_name
        `;
        const u = rows[0]!;
        await ensureDefaultPartnerAndTenantForUser({ userId: u.id, email: u.email, businessName: u.business_name });
        return { ok: true as const, user: { id: u.id, email: u.email, username: u.username, status: u.status, role: u.role } };
    } catch (err) {
        const message = err instanceof Error ? err.message : "";
        if (/users_email_unique/i.test(message) || /unique/i.test(message)) {
            return { ok: false as const, code: "conflict" as const };
        }
        throw err;
    }
}

export async function loginWithPassword(input: { identifier: string; password: string; ipAddress: string; userAgent: string }) {
    if (!isDatabaseConfigured()) {
        return { ok: false as const, code: "db_unavailable" as const };
    }
    await ensureAuthSchema();

    const ipBucketLimit = parsePositiveInt(process.env.AUTH_LOGIN_LIMIT_PER_IP_PER_MINUTE, 10);
    const userBucketLimit = parsePositiveInt(process.env.AUTH_LOGIN_LIMIT_PER_IDENTIFIER_PER_MINUTE, 10);

    const ipBucket = `login:ip:${input.ipAddress || "unknown"}`;
    const idBucket = `login:id:${input.identifier.trim().toLowerCase() || "unknown"}`;

    const [ipLimit, idLimit] = await Promise.all([
        consumeAuthRateLimit({ bucket: ipBucket, limit: ipBucketLimit, windowSeconds: 60 }),
        consumeAuthRateLimit({ bucket: idBucket, limit: userBucketLimit, windowSeconds: 60 }),
    ]);

    if (!ipLimit.allowed || !idLimit.allowed) {
        const retryAfterSeconds = Math.max(ipLimit.retryAfterSeconds, idLimit.retryAfterSeconds);
        return { ok: false as const, code: "rate_limited" as const, retryAfterSeconds };
    }

    const user = await findUserByIdentifier(input.identifier);
    const lockedUntil = user ? (await getUserSecurity(user.id)).locked_until : null;
    if (lockedUntil && lockedUntil.getTime() > nowMs()) {
        await verifyPassword(await dummyHash(), input.password).catch(() => false);
        return { ok: false as const, code: "invalid_credentials" as const };
    }

    const passwordHash = user?.password_hash ?? (await dummyHash());
    const valid = await verifyPassword(passwordHash, input.password).catch(() => false);

    if (!user || !valid || user.status !== "active") {
        if (user) await recordFailedLoginAttempt(user.id);
        return { ok: false as const, code: "invalid_credentials" as const };
    }

    await clearFailedLoginState(user.id);

    const session = await createSessionForUser({
        userId: user.id,
        ipAddress: input.ipAddress,
        userAgent: input.userAgent,
        mfaVerified: false,
    });
    if (!session.ok) return session;
    return { ok: true as const, session: session.session, user: { id: user.id, email: user.email, role: user.role } };
}

export async function verifyPasswordLoginAttempt(input: { identifier: string; password: string; ipAddress: string; userAgent: string }) {
    if (!isDatabaseConfigured()) {
        return { ok: false as const, code: "db_unavailable" as const };
    }
    await ensureAuthSchema();

    const ipBucketLimit = parsePositiveInt(process.env.AUTH_LOGIN_LIMIT_PER_IP_PER_MINUTE, 10);
    const userBucketLimit = parsePositiveInt(process.env.AUTH_LOGIN_LIMIT_PER_IDENTIFIER_PER_MINUTE, 10);

    const ipBucket = `login:ip:${input.ipAddress || "unknown"}`;
    const idBucket = `login:id:${input.identifier.trim().toLowerCase() || "unknown"}`;

    const [ipLimit, idLimit] = await Promise.all([
        consumeAuthRateLimit({ bucket: ipBucket, limit: ipBucketLimit, windowSeconds: 60 }),
        consumeAuthRateLimit({ bucket: idBucket, limit: userBucketLimit, windowSeconds: 60 }),
    ]);

    if (!ipLimit.allowed || !idLimit.allowed) {
        const retryAfterSeconds = Math.max(ipLimit.retryAfterSeconds, idLimit.retryAfterSeconds);
        return { ok: false as const, code: "rate_limited" as const, retryAfterSeconds };
    }

    const user = await findUserByIdentifier(input.identifier);
    const lockedUntil = user ? (await getUserSecurity(user.id)).locked_until : null;
    if (lockedUntil && lockedUntil.getTime() > nowMs()) {
        await verifyPassword(await dummyHash(), input.password).catch(() => false);
        return { ok: false as const, code: "invalid_credentials" as const };
    }

    const passwordHash = user?.password_hash ?? (await dummyHash());
    const valid = await verifyPassword(passwordHash, input.password).catch(() => false);

    if (!user || !valid || user.status !== "active") {
        if (user) await recordFailedLoginAttempt(user.id);
        return { ok: false as const, code: "invalid_credentials" as const };
    }

    await clearFailedLoginState(user.id);
    return { ok: true as const, user: { id: user.id, email: user.email, role: user.role } };
}

export async function getUserPasswordHashById(userId: string) {
    if (!isDatabaseConfigured()) return null;
    await ensureAuthSchema();
    const sql = getSql();
    const rows = await sql<{ password_hash: string }[]>`
        select password_hash
        from users
        where id = ${userId}::uuid
        limit 1
    `;
    return rows[0]?.password_hash ?? null;
}

export async function createSessionForUser(input: { userId: string; ipAddress: string; userAgent: string; mfaVerified: boolean }) {
    if (!isDatabaseConfigured()) {
        return { ok: false as const, code: "db_unavailable" as const };
    }
    await ensureAuthSchema();

    const sql = getSql();
    const createUserLimit = parsePositiveInt(process.env.AUTH_SESSION_CREATE_LIMIT_PER_USER_PER_MINUTE, 20);
    const createIpLimit = parsePositiveInt(process.env.AUTH_SESSION_CREATE_LIMIT_PER_IP_PER_MINUTE, 50);
    const createUserBucket = `session:create:user:${input.userId}`;
    const createIpBucket = `session:create:ip:${input.ipAddress || "unknown"}`;

    const [userLimit, ipLimit] = await Promise.all([
        consumeAuthRateLimit({ bucket: createUserBucket, limit: createUserLimit, windowSeconds: 60 }),
        consumeAuthRateLimit({ bucket: createIpBucket, limit: createIpLimit, windowSeconds: 60 }),
    ]);
    if (!userLimit.allowed || !ipLimit.allowed) {
        const retryAfterSeconds = Math.max(userLimit.retryAfterSeconds, ipLimit.retryAfterSeconds);
        return { ok: false as const, code: "rate_limited" as const, retryAfterSeconds };
    }

    const mfaRows = await sql<DbUserMfaRow[]>`
        select user_id, secret_encrypted, is_enabled
        from user_mfa
        where user_id = ${input.userId}::uuid
        limit 1
    `;
    const mfa = mfaRows[0] ?? null;
    if (mfa?.is_enabled && !input.mfaVerified) {
        return { ok: false as const, code: "mfa_required" as const };
    }

    const authz = await getAuthzContextForUser(input.userId);
    const computed = computeScopeFromAuthz(authz);
    let usageAccountId: string | null = null;
    let billingAccountId: string | null = null;
    if (computed.tenant_id && computed.partner_id) {
        await ensureTenantAccounts({ tenantId: computed.tenant_id, partnerId: computed.partner_id }).catch(() => undefined);
        const accounts = await getTenantAccounts({ tenantId: computed.tenant_id }).catch(() => ({ ok: false as const, status: 503 as const }));
        if (accounts.ok) {
            usageAccountId = accounts.usageAccountId;
            billingAccountId = accounts.billingAccountId;
        }
    } else if (computed.partner_id) {
        const billingRows = await sql<{ id: string }[]>`
            insert into billing_accounts (id, partner_id, created_at)
            values (${crypto.randomUUID()}::uuid, ${computed.partner_id}, now())
            on conflict (partner_id)
            do update set partner_id = excluded.partner_id
            returning id::text as id
        `;
        billingAccountId = billingRows[0]?.id ?? null;
    }

    const sessionId = randomSessionId();
    const expiresAt = new Date(nowMs() + sessionTtlSeconds() * 1000);
    await sql`
        insert into sessions (session_id, user_id, expires_at, last_activity_at, ip_address, user_agent, revoked, revoked_at, rotated_from, replaced_by)
        values (
            ${sessionId},
            ${input.userId}::uuid,
            ${expiresAt},
            now(),
            ${input.ipAddress || null},
            ${input.userAgent || null},
            false,
            null,
            null,
            null
        )
    `;
    try {
        await sql`
            update sessions
            set scope_role = ${computed.role},
                scope_partner_id = ${computed.partner_id ?? null},
                scope_tenant_id = ${computed.tenant_id ?? null},
                scope_usage_account_id = ${usageAccountId}::uuid,
                scope_billing_account_id = ${billingAccountId}::uuid
            where session_id = ${sessionId}
        `;
    } catch {
    }
    emitAuthSessionEvent({
        type: "session_created",
        user_id: input.userId,
        session_id: sessionId,
        reason: input.mfaVerified ? "mfa_verified" : "login",
        ip_address: input.ipAddress || null,
        user_agent: input.userAgent || null,
        partner_id: computed.partner_id ?? null,
        tenant_id: computed.tenant_id ?? null,
        usage_account_id: usageAccountId,
        billing_account_id: billingAccountId,
    });
    return { ok: true as const, session: { sessionId, expiresAt } };
}

export async function logoutSession(sessionId: string) {
    if (!sessionId.trim()) return;
    if (!isDatabaseConfigured()) return;
    await ensureAuthSchema();
    const sql = getSql();
    await sql`
        update sessions
        set revoked = true, revoked_at = now()
        where session_id = ${sessionId} and revoked = false and revoked_at is null
    `;
}

export async function logoutAllSessionsForUser(input: { userId: string }) {
    if (!isDatabaseConfigured()) return;
    await ensureAuthSchema();
    const sql = getSql();
    await sql`
        update sessions
        set revoked = true, revoked_at = now()
        where user_id = ${input.userId}::uuid and revoked = false and revoked_at is null
    `;
    emitAuthSessionEvent({ type: "logout_all_sessions", user_id: input.userId });
}

export async function listUserSessions(input: { userId: string }) {
    if (!isDatabaseConfigured()) return { ok: false as const, code: "db_unavailable" as const };
    await ensureAuthSchema();
    const sql = getSql();
    const rows = await sql<
        Array<{
            session_id: string;
            created_at: Date;
            expires_at: Date;
            last_activity_at: Date;
            ip_address: string | null;
            user_agent: string | null;
            revoked: boolean;
            revoked_at: Date | null;
            scope_role: string | null;
            scope_partner_id: string | null;
            scope_tenant_id: string | null;
            scope_usage_account_id: string | null;
            scope_billing_account_id: string | null;
        }>
    >`
        select
            session_id,
            created_at,
            expires_at,
            last_activity_at,
            ip_address,
            user_agent,
            revoked,
            revoked_at,
            scope_role,
            scope_partner_id,
            scope_tenant_id,
            scope_usage_account_id::text as scope_usage_account_id,
            scope_billing_account_id::text as scope_billing_account_id
        from sessions
        where user_id = ${input.userId}::uuid
        order by last_activity_at desc nulls last, created_at desc
        limit 50
    `;
    return { ok: true as const, sessions: rows };
}

export async function revokeUserSessionByHandle(input: { userId: string; sessionHandle: string; reason: string }) {
    if (!isDatabaseConfigured()) return { ok: false as const, code: "db_unavailable" as const };
    await ensureAuthSchema();
    const sql = getSql();
    const res = await listUserSessions({ userId: input.userId });
    if (!res.ok) return res;

    const wanted = String(input.sessionHandle ?? "").trim();
    if (!wanted) return { ok: false as const, code: "invalid_request" as const };

    const match = res.sessions.find((s) => safeEqual(sessionHandleForSessionId(s.session_id), wanted)) ?? null;
    if (!match) return { ok: false as const, code: "not_found" as const };

    await sql`
        update sessions
        set revoked = true, revoked_at = now()
        where session_id = ${match.session_id} and user_id = ${input.userId}::uuid and revoked = false and revoked_at is null
    `;

    emitAuthSessionEvent({
        type: "session_revoked",
        user_id: input.userId,
        session_id: match.session_id,
        reason: input.reason,
        ip_address: match.ip_address ?? null,
        user_agent: match.user_agent ?? null,
        partner_id: match.scope_partner_id,
        tenant_id: match.scope_tenant_id,
        usage_account_id: match.scope_usage_account_id,
        billing_account_id: match.scope_billing_account_id,
    });

    return { ok: true as const, sessionId: match.session_id };
}

export function buildSessionCookie(input: { sessionId: string; expiresAt: Date; secure: boolean }) {
    const maxAge = Math.max(0, Math.floor((input.expiresAt.getTime() - nowMs()) / 1000));
    const parts = [
        `${cookieName()}=${encodeURIComponent(input.sessionId)}`,
        "Path=/",
        `Max-Age=${maxAge}`,
        "HttpOnly",
        "SameSite=Lax",
    ];
    if (input.secure) parts.push("Secure");
    return parts.join("; ");
}

export function clearSessionCookie(input: { secure: boolean }) {
    const parts = [`${cookieName()}=`, "Path=/", "Max-Age=0", "HttpOnly", "SameSite=Lax"];
    if (input.secure) parts.push("Secure");
    return parts.join("; ");
}

