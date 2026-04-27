import crypto from "node:crypto";
import { captureMessage } from "@/lib/monitoring";
import { clientIpFromRequest } from "@/server/auth-core";
import { getSql, isDatabaseConfigured } from "@/server/db";

type RateLimitTier = "default" | "sensitive" | "webhook";

let apiSecuritySchemaReady: Promise<void> | undefined;

export async function ensureApiSecuritySchema() {
    if (apiSecuritySchemaReady) return apiSecuritySchemaReady;
    apiSecuritySchemaReady = (async () => {
        if (!isDatabaseConfigured()) return;
        const sql = getSql();

        await sql.unsafe(`
            create table if not exists api_rate_limits (
                bucket text not null,
                window_start timestamptz not null,
                count integer not null,
                updated_at timestamptz not null default now(),
                primary key (bucket, window_start)
            )
        `);
        await sql.unsafe(`create index if not exists api_rate_limits_updated_at_idx on api_rate_limits (updated_at)`);

        await sql.unsafe(`
            create table if not exists idempotency_keys (
                scope_key text primary key,
                scope text not null,
                idempotency_key text not null,
                request_hash text not null,
                status text not null check (status in ('in_progress','completed')),
                method text,
                path text,
                user_id text,
                tenant_id text,
                ip_address text,
                response_status integer,
                response_headers jsonb,
                response_body jsonb,
                created_at timestamptz not null default now(),
                updated_at timestamptz not null default now()
            )
        `);
        await sql.unsafe(`create index if not exists idempotency_keys_created_at_idx on idempotency_keys (created_at)`);
        await sql.unsafe(`create unique index if not exists idempotency_keys_key_unique on idempotency_keys (idempotency_key, scope)`);
    })();
    return apiSecuritySchemaReady;
}

function parsePositiveInt(raw: unknown, fallback: number) {
    const n = Number(raw);
    if (!Number.isFinite(n)) return fallback;
    if (n <= 0) return fallback;
    return Math.floor(n);
}

function rateLimitConfig(tier: RateLimitTier) {
    if (tier === "sensitive") {
        return {
            perIpPerMinute: parsePositiveInt(process.env.API_RL_SENSITIVE_PER_IP_PER_MINUTE, 20),
            perUserPerMinute: parsePositiveInt(process.env.API_RL_SENSITIVE_PER_USER_PER_MINUTE, 30),
            perTenantPerMinute: parsePositiveInt(process.env.API_RL_SENSITIVE_PER_TENANT_PER_MINUTE, 200),
        };
    }
    if (tier === "webhook") {
        return {
            perIpPerMinute: parsePositiveInt(process.env.API_RL_WEBHOOK_PER_IP_PER_MINUTE, 60),
            perUserPerMinute: parsePositiveInt(process.env.API_RL_WEBHOOK_PER_USER_PER_MINUTE, 0),
            perTenantPerMinute: parsePositiveInt(process.env.API_RL_WEBHOOK_PER_TENANT_PER_MINUTE, 600),
        };
    }
    return {
        perIpPerMinute: parsePositiveInt(process.env.API_RL_DEFAULT_PER_IP_PER_MINUTE, 300),
        perUserPerMinute: parsePositiveInt(process.env.API_RL_DEFAULT_PER_USER_PER_MINUTE, 600),
        perTenantPerMinute: parsePositiveInt(process.env.API_RL_DEFAULT_PER_TENANT_PER_MINUTE, 2000),
    };
}

async function consumeApiRateLimit(input: { bucket: string; limit: number; windowSeconds: number }) {
    const sql = getSql();
    const now = new Date();
    const windowMs = input.windowSeconds * 1000;
    const start = new Date(Math.floor(now.getTime() / windowMs) * windowMs);

    const rows = await sql<{ count: number }[]>`
        insert into api_rate_limits (bucket, window_start, count, updated_at)
        values (${input.bucket}, ${start}, 1, now())
        on conflict (bucket, window_start)
        do update set count = api_rate_limits.count + 1, updated_at = now()
        returning count
    `;
    const count = rows[0]?.count ?? 1;
    const remainingMs = windowMs - (now.getTime() - start.getTime());
    const retryAfterSeconds = Math.max(0, Math.ceil(remainingMs / 1000));
    return { allowed: count <= input.limit, count, retryAfterSeconds };
}

export async function enforceMultiLevelRateLimit(input: {
    request: Request;
    tier: RateLimitTier;
    path: string;
    method: string;
    userId?: string | null;
    tenantId?: string | null;
    layers?: { ip?: boolean; user?: boolean; tenant?: boolean };
}) {
    if (!isDatabaseConfigured()) return { ok: true as const, headers: {} as Record<string, string> };
    await ensureApiSecuritySchema();

    const cfg = rateLimitConfig(input.tier);
    const ip = clientIpFromRequest(input.request) || "unknown";

    const buckets: Array<{ kind: "ip" | "user" | "tenant"; bucket: string; limit: number }> = [];
    const layers = input.layers ?? {};
    const wantIp = layers.ip !== false;
    const wantUser = layers.user !== false;
    const wantTenant = layers.tenant !== false;

    if (wantIp && cfg.perIpPerMinute > 0) buckets.push({ kind: "ip", bucket: `api:${input.tier}:ip:${ip}`, limit: cfg.perIpPerMinute });
    if (wantUser && cfg.perUserPerMinute > 0 && input.userId) buckets.push({ kind: "user", bucket: `api:${input.tier}:user:${input.userId}`, limit: cfg.perUserPerMinute });
    if (wantTenant && cfg.perTenantPerMinute > 0) {
        const tenantKey = typeof input.tenantId === "string" && input.tenantId.trim().length > 0 ? input.tenantId.trim() : "unknown";
        buckets.push({ kind: "tenant", bucket: `api:${input.tier}:tenant:${tenantKey}`, limit: cfg.perTenantPerMinute });
    }

    const results = await Promise.all(buckets.map((b) => consumeApiRateLimit({ bucket: b.bucket, limit: b.limit, windowSeconds: 60 })));
    const denied = results.some((r) => !r.allowed);
    if (denied) {
        const retryAfterSeconds = Math.max(...results.map((r) => r.retryAfterSeconds));
        try {
            captureMessage("rate_limit_denied", {
                tier: input.tier,
                path: input.path,
                method: input.method,
                user_id: input.userId || null,
                tenant_id: input.tenantId || null,
                ip_address: ip,
            });
        } catch {
        }
        return {
            ok: false as const,
            retryAfterSeconds,
            headers: {
                "retry-after": String(retryAfterSeconds),
            },
        };
    }

    const headers: Record<string, string> = {};
    const ipIndex = buckets.findIndex((b) => b.kind === "ip");
    if (ipIndex >= 0) {
        const limit = buckets[ipIndex]!.limit;
        const count = results[ipIndex]!.count;
        headers["x-ratelimit-limit-ip"] = String(limit);
        headers["x-ratelimit-remaining-ip"] = String(Math.max(0, limit - count));
    }
    const userIndex = buckets.findIndex((b) => b.kind === "user");
    if (userIndex >= 0) {
        const limit = buckets[userIndex]!.limit;
        const count = results[userIndex]!.count;
        headers["x-ratelimit-limit-user"] = String(limit);
        headers["x-ratelimit-remaining-user"] = String(Math.max(0, limit - count));
    }
    const tenantIndex = buckets.findIndex((b) => b.kind === "tenant");
    if (tenantIndex >= 0) {
        const limit = buckets[tenantIndex]!.limit;
        const count = results[tenantIndex]!.count;
        headers["x-ratelimit-limit-tenant"] = String(limit);
        headers["x-ratelimit-remaining-tenant"] = String(Math.max(0, limit - count));
    }
    return { ok: true as const, headers };
}

export function sanitizeUnknown(input: unknown): unknown {
    if (typeof input === "string") {
        const normalized = input.normalize("NFKC");
        const withoutNull = normalized.replace(/\u0000/g, "");
        return withoutNull.replace(/[\u0001-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "");
    }
    if (Array.isArray(input)) return input.map(sanitizeUnknown);
    if (!input || typeof input !== "object") return input;

    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(input as Record<string, unknown>)) {
        if (k === "__proto__" || k === "constructor" || k === "prototype") continue;
        out[k] = sanitizeUnknown(v);
    }
    return out;
}

export function sanitizeTextInput(text: string, options?: { maxLen?: number }) {
    const maxLen = options?.maxLen ?? 500;
    const normalized = sanitizeUnknown(text);
    const s = typeof normalized === "string" ? normalized : String(text ?? "");
    const trimmed = s.trim().slice(0, maxLen);
    return trimmed.replace(/</g, "‹").replace(/>/g, "›");
}

export function sanitizeHtmlEmailInput(html: string) {
    const normalized = sanitizeUnknown(html);
    const s = typeof normalized === "string" ? normalized : String(html ?? "");
    const trimmed = s.trim();
    if (!trimmed) return "";
    const withoutScripts = trimmed
        .replace(/<\s*script\b[^>]*>[\s\S]*?<\s*\/\s*script\s*>/gi, "")
        .replace(/<\s*style\b[^>]*>[\s\S]*?<\s*\/\s*style\s*>/gi, "");
    const withoutHandlers = withoutScripts.replace(/\son\w+\s*=\s*(['"]).*?\1/gi, "");
    const withoutJsUrls = withoutHandlers.replace(/\s(href|src)\s*=\s*(['"])\s*javascript:[\s\S]*?\2/gi, "");
    return withoutJsUrls;
}

export function sha256Hex(data: Uint8Array | string) {
    return crypto.createHash("sha256").update(data).digest("hex");
}

export function timingSafeEqualHex(aHex: string, bHex: string) {
    const a = Buffer.from(aHex, "hex");
    const b = Buffer.from(bHex, "hex");
    if (a.length !== b.length) return false;
    return crypto.timingSafeEqual(a, b);
}

export function parseStripeSignatureHeader(header: string) {
    const parts = header
        .split(",")
        .map((p) => p.trim())
        .filter(Boolean);
    let timestamp: number | null = null;
    const v1: string[] = [];
    for (const part of parts) {
        const [k, v] = part.split("=").map((s) => s.trim());
        if (!k || !v) continue;
        if (k === "t") {
            const n = Number(v);
            if (Number.isFinite(n) && n > 0) timestamp = Math.floor(n);
        } else if (k === "v1") {
            if (/^[0-9a-f]{64}$/i.test(v)) v1.push(v.toLowerCase());
        }
    }
    if (!timestamp || v1.length === 0) return null;
    return { timestamp, signatures: v1 };
}

export function verifyStripeWebhookSignature(input: { rawBody: Uint8Array; header: string; secret: string; toleranceSeconds: number }) {
    const parsed = parseStripeSignatureHeader(input.header);
    if (!parsed) return { ok: false as const, code: "missing_or_invalid_header" as const };

    const now = Math.floor(Date.now() / 1000);
    const age = Math.abs(now - parsed.timestamp);
    if (age > input.toleranceSeconds) return { ok: false as const, code: "timestamp_out_of_range" as const };

    const signedPayload = Buffer.concat([Buffer.from(String(parsed.timestamp) + ".", "utf8"), Buffer.from(input.rawBody)]);
    const expected = crypto.createHmac("sha256", input.secret).update(signedPayload).digest("hex");
    const ok = parsed.signatures.some((sig) => timingSafeEqualHex(sig, expected));
    return ok ? { ok: true as const, timestamp: parsed.timestamp } : { ok: false as const, code: "signature_mismatch" as const };
}

type IdempotencyRow = {
    scope_key: string;
    scope: string;
    idempotency_key: string;
    request_hash: string;
    status: "in_progress" | "completed";
    response_status: number | null;
    response_headers: Record<string, unknown> | null;
    response_body: unknown | null;
};

export async function beginIdempotency(input: {
    scope: string;
    idempotencyKey: string;
    requestHash: string;
    method: string;
    path: string;
    userId?: string | null;
    tenantId?: string | null;
    ipAddress?: string | null;
}) {
    if (!isDatabaseConfigured()) return { ok: true as const, state: "new" as const, row: null as IdempotencyRow | null };
    await ensureApiSecuritySchema();
    const sql = getSql();

    const scopeKey = `${input.scope}:${input.idempotencyKey}`;
    const inserted = await sql<IdempotencyRow[]>`
        insert into idempotency_keys (
            scope_key,
            scope,
            idempotency_key,
            request_hash,
            status,
            method,
            path,
            user_id,
            tenant_id,
            ip_address,
            updated_at
        )
        values (
            ${scopeKey},
            ${input.scope},
            ${input.idempotencyKey},
            ${input.requestHash},
            'in_progress',
            ${input.method},
            ${input.path},
            ${input.userId || null},
            ${input.tenantId || null},
            ${input.ipAddress || null},
            now()
        )
        on conflict (scope_key) do nothing
        returning scope_key, scope, idempotency_key, request_hash, status, response_status, response_headers, response_body
    `;
    if (inserted[0]) return { ok: true as const, state: "new" as const, row: inserted[0] };

    const existing = await sql<IdempotencyRow[]>`
        select scope_key, scope, idempotency_key, request_hash, status, response_status, response_headers, response_body
        from idempotency_keys
        where scope_key = ${scopeKey}
        limit 1
    `;
    const row = existing[0];
    if (!row) return { ok: false as const, status: 503 as const, code: "unavailable" as const };
    if (row.request_hash !== input.requestHash) return { ok: false as const, status: 409 as const, code: "hash_mismatch" as const };
    if (row.status === "completed" && row.response_status) {
        return { ok: true as const, state: "replay" as const, row };
    }
    return { ok: false as const, status: 409 as const, code: "in_progress" as const };
}

export async function completeIdempotency(input: {
    scope: string;
    idempotencyKey: string;
    requestHash: string;
    responseStatus: number;
    responseHeaders?: Record<string, string>;
    responseBody: unknown;
}) {
    if (!isDatabaseConfigured()) return;
    await ensureApiSecuritySchema();
    const sql = getSql();
    const scopeKey = `${input.scope}:${input.idempotencyKey}`;
    await sql`
        update idempotency_keys
        set
            status = 'completed',
            response_status = ${input.responseStatus},
            response_headers = ${(input.responseHeaders ?? {}) as never},
            response_body = ${input.responseBody as never},
            updated_at = now()
        where scope_key = ${scopeKey} and request_hash = ${input.requestHash}
    `;
}

