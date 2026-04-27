import crypto from "node:crypto";
import { captureMessage } from "@/lib/monitoring";
import { getSql, isDatabaseConfigured } from "@/server/db";
import { canAccessTenant, ensureTenantAccounts, findTenantById, getAuthzContextForUser, getTenantAccounts, hasPermissionInTenant, type AuthzContext, type PartnerStatus, type TenantStatus } from "@/server/rbac";

export const voiceFeatures = ["voice", "premium", "transfer"] as const;

export type VoiceFeature = (typeof voiceFeatures)[number];
export type GuardOutcome = "ALLOW" | "REJECT";
export type AbuseEntityKind = "tenant" | "partner" | "user" | "ip";
export type CallReservationStatus = "reserved" | "active" | "released" | "ended";

export type CallGuardRejectCode =
    | "invalid_tenant_id"
    | "invalid_partner_id"
    | "tenant_not_found"
    | "partner_not_found"
    | "tenant_partner_mismatch"
    | "tenant_inactive"
    | "partner_inactive"
    | "forbidden"
    | "feature_not_allowed"
    | "temporary_block"
    | "tenant_rate_limited"
    | "partner_rate_limited"
    | "user_rate_limited"
    | "ip_rate_limited"
    | "rapid_attempts_detected"
    | "unusual_spike_detected"
    | "tenant_concurrency_exceeded"
    | "partner_concurrency_exceeded";

export type CallGuardAllowResult = {
    outcome: "ALLOW";
    tenantId: string;
    partnerId: string;
    reservationId: string | null;
    activeCalls: { tenant: number; partner: number };
    overage: { tenant: boolean; partner: boolean };
    allowedFeatures: VoiceFeature[];
    requestedFeatures: VoiceFeature[];
    usageAccountId: string | null;
    billingAccountId: string | null;
};

export type CallGuardRejectResult = {
    outcome: "REJECT";
    tenantId: string | null;
    partnerId: string | null;
    code: CallGuardRejectCode;
    reason: string;
    retryAfterSeconds: number | null;
    blockExpiresAt: string | null;
};

export type CallGuardResult = CallGuardAllowResult | CallGuardRejectResult;

export type CallGuardInput = {
    tenantId: string;
    partnerId?: string | null;
    userId?: string | null;
    ipAddress?: string | null;
    callId?: string | null;
    providerCallId?: string | null;
    requestedFeatures?: VoiceFeature[];
    reserveConcurrency?: boolean;
    allowOverage?: boolean;
    now?: Date;
};

export type ConfirmGuardedCallStartInput = {
    reservationId: string;
    callId?: string | null;
    providerCallId?: string | null;
    startedAt?: Date;
};

export type ReleaseGuardedCallReservationInput = {
    reservationId: string;
    releasedAt?: Date;
    reason?: string | null;
};

export type EndGuardedCallInput = {
    reservationId?: string | null;
    callId?: string | null;
    providerCallId?: string | null;
    endedAt?: Date;
    reason?: string | null;
};

export type ReservationLifecycleResult = {
    ok: boolean;
    tenantId: string | null;
    partnerId: string | null;
    status: CallReservationStatus | null;
    activeCalls: { tenant: number; partner: number } | null;
};

export type StartGuardedVoiceCallInput = CallGuardInput & {
    startedAt?: Date;
};

export type StartGuardedVoiceCallAllowResult = CallGuardAllowResult & {
    callId: string;
    providerCallId: string | null;
    status: "active";
    startedAt: string;
};

export type StartGuardedVoiceCallResult = CallGuardRejectResult | StartGuardedVoiceCallAllowResult;

type GuardLimits = {
    maxConcurrentCalls: number;
    callsPerMinute: number;
    allowedFeatures: VoiceFeature[];
    updatedAt: string;
};

type ResolvedScope = {
    tenant: { id: string; partnerId: string; status: TenantStatus };
    partner: { id: string; status: PartnerStatus; allowTransfer: boolean };
    tenantLimits: GuardLimits;
    partnerLimits: GuardLimits;
};

type BlockState = {
    entityKind: AbuseEntityKind;
    entityId: string;
    reasonCode: CallGuardRejectCode;
    blockedUntil: Date;
};

type RateLimitResult = {
    allowed: boolean;
    count: number;
    retryAfterSeconds: number;
};

type ConcurrencyReservation = {
    allowed: boolean;
    reservationId: string | null;
    activeCalls: { tenant: number; partner: number };
    overage: { tenant: boolean; partner: boolean };
    rejectionCode: "tenant_concurrency_exceeded" | "partner_concurrency_exceeded" | null;
};

export type VoiceSecurityBackend = {
    resolveScope(input: { tenantId: string; partnerId?: string | null }): Promise<ResolvedScope | null>;
    validateCaller(input: { userId: string; tenantId: string; partnerId: string }): Promise<boolean>;
    ensureBillingContext(input: { tenantId: string; partnerId: string }): Promise<{ usageAccountId: string | null; billingAccountId: string | null }>;
    getBlock(input: { entityKind: AbuseEntityKind; entityId: string; now: Date }): Promise<BlockState | null>;
    setBlock(input: { entityKind: AbuseEntityKind; entityId: string; reasonCode: CallGuardRejectCode; blockedUntil: Date }): Promise<void>;
    consumeRateLimit(input: { bucket: string; limit: number; windowSeconds: number; now: Date }): Promise<RateLimitResult>;
    reserveConcurrency(input: {
        tenantId: string;
        partnerId: string;
        userId?: string | null;
        ipAddress?: string | null;
        callId?: string | null;
        providerCallId?: string | null;
        requestedFeatures: VoiceFeature[];
        allowOverage: boolean;
        tenantLimit: number;
        partnerLimit: number;
        now: Date;
    }): Promise<ConcurrencyReservation>;
    confirmCallStart(input: ConfirmGuardedCallStartInput): Promise<ReservationLifecycleResult>;
    releaseReservation(input: ReleaseGuardedCallReservationInput): Promise<ReservationLifecycleResult>;
    endCall(input: EndGuardedCallInput): Promise<ReservationLifecycleResult>;
};

export type VoiceSecurityService = {
    call_guard(input: CallGuardInput): Promise<CallGuardResult>;
    confirmGuardedCallStart(input: ConfirmGuardedCallStartInput): Promise<ReservationLifecycleResult>;
    releaseGuardedCallReservation(input: ReleaseGuardedCallReservationInput): Promise<ReservationLifecycleResult>;
    endGuardedCall(input: EndGuardedCallInput): Promise<ReservationLifecycleResult>;
};

let voiceSecuritySchemaReady: Promise<void> | undefined;
let defaultVoiceSecurityBackend: VoiceSecurityBackend | undefined;
let defaultVoiceSecurityService: VoiceSecurityService | undefined;

function parsePositiveInt(raw: unknown, fallback: number) {
    const n = Number(raw);
    if (!Number.isFinite(n)) return fallback;
    if (n < 0) return fallback;
    return Math.floor(n);
}

function nowIso(input: Date) {
    return input.toISOString();
}

function normalizePartnerId(raw: string) {
    return raw.trim().toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-").replace(/^-+|-+$/g, "");
}

function normalizeTenantId(raw: string) {
    return raw.trim();
}

function normalizeFeature(raw: string) {
    const v = raw.trim().toLowerCase();
    return voiceFeatures.includes(v as VoiceFeature) ? (v as VoiceFeature) : null;
}

function uniqueFeatures(input?: VoiceFeature[]) {
    const out: VoiceFeature[] = [];
    const seen = new Set<VoiceFeature>();
    for (const raw of input ?? ["voice"]) {
        const feature = normalizeFeature(raw);
        if (!feature || seen.has(feature)) continue;
        seen.add(feature);
        out.push(feature);
    }
    if (!seen.has("voice")) out.unshift("voice");
    return out;
}

function isSafeIdentifier(input: string) {
    const s = input.trim();
    if (!s) return false;
    if (s.length > 120) return false;
    return /^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,118}[a-zA-Z0-9]$/.test(s);
}

function sanitizeIpAddress(input: string | null | undefined) {
    const raw = typeof input === "string" ? input.trim() : "";
    if (!raw) return null;
    return raw.slice(0, 120);
}

function defaultTenantConcurrencyLimit() {
    return parsePositiveInt(process.env.VOICE_DEFAULT_TENANT_MAX_CONCURRENT_CALLS, 10);
}

function defaultPartnerConcurrencyLimit() {
    return parsePositiveInt(process.env.VOICE_DEFAULT_PARTNER_MAX_CONCURRENT_CALLS, 100);
}

function defaultTenantCallsPerMinute() {
    return parsePositiveInt(process.env.VOICE_DEFAULT_TENANT_CALLS_PER_MINUTE, 30);
}

function defaultPartnerCallsPerMinute() {
    return parsePositiveInt(process.env.VOICE_DEFAULT_PARTNER_CALLS_PER_MINUTE, 300);
}

function defaultUserCallsPerMinute() {
    return parsePositiveInt(process.env.VOICE_DEFAULT_USER_CALLS_PER_MINUTE, 20);
}

function defaultIpCallsPerMinute() {
    return parsePositiveInt(process.env.VOICE_DEFAULT_IP_CALLS_PER_MINUTE, 40);
}

function abuseFailureWindowSeconds() {
    return parsePositiveInt(process.env.VOICE_ABUSE_FAILURE_WINDOW_SECONDS, 600);
}

function abuseFailureThreshold() {
    return parsePositiveInt(process.env.VOICE_ABUSE_FAILURE_THRESHOLD, 6);
}

function abuseBlockSeconds() {
    return parsePositiveInt(process.env.VOICE_ABUSE_BLOCK_SECONDS, 600);
}

function rapidAttemptWindowSeconds() {
    return parsePositiveInt(process.env.VOICE_ABUSE_RAPID_WINDOW_SECONDS, 10);
}

function rapidAttemptThreshold(limitPerMinute: number) {
    const derived = Math.max(3, Math.min(10, Math.ceil(Math.max(limitPerMinute, 1) / 6)));
    return parsePositiveInt(process.env.VOICE_ABUSE_RAPID_ATTEMPTS_THRESHOLD, derived);
}

function spikeWindowSeconds() {
    return parsePositiveInt(process.env.VOICE_ABUSE_SPIKE_WINDOW_SECONDS, 300);
}

function spikeThreshold(limitPerMinute: number) {
    const multiplier = parsePositiveInt(process.env.VOICE_ABUSE_SPIKE_MULTIPLIER, 3);
    return Math.max(limitPerMinute, Math.max(1, multiplier) * Math.max(limitPerMinute, 1));
}

function defaultAllowedFeatures() {
    return uniqueFeatures((process.env.VOICE_DEFAULT_ALLOWED_FEATURES ?? "voice").split(",").map((v) => v.trim() as VoiceFeature));
}

function parseAllowedFeatures(raw: unknown) {
    if (Array.isArray(raw)) {
        return uniqueFeatures(raw.filter((value): value is VoiceFeature => typeof value === "string") as VoiceFeature[]);
    }
    if (typeof raw === "string") {
        return uniqueFeatures(raw.split(",").map((value) => value.trim() as VoiceFeature));
    }
    if (raw && typeof raw === "object") {
        const enabled = Object.entries(raw as Record<string, unknown>)
            .filter(([, value]) => value === true)
            .map(([key]) => key as VoiceFeature);
        return uniqueFeatures(enabled);
    }
    return defaultAllowedFeatures();
}

function featuresContain(features: VoiceFeature[], feature: VoiceFeature) {
    return features.includes(feature);
}

function buildRateBucket(kind: string, entityId: string, suffix: string) {
    return `voice:${kind}:${entityId}:${suffix}`;
}

function buildBlockKey(entityKind: AbuseEntityKind, entityId: string) {
    return `${entityKind}:${entityId}`;
}

function rejectResult(input: {
    tenantId: string | null;
    partnerId: string | null;
    code: CallGuardRejectCode;
    reason: string;
    retryAfterSeconds?: number | null;
    block?: BlockState | null;
}) {
    return {
        outcome: "REJECT" as const,
        tenantId: input.tenantId,
        partnerId: input.partnerId,
        code: input.code,
        reason: input.reason,
        retryAfterSeconds: input.retryAfterSeconds ?? null,
        blockExpiresAt: input.block ? nowIso(input.block.blockedUntil) : null,
    };
}

function safeCapture(event: string, context: Record<string, unknown>) {
    try {
        captureMessage(event, context);
    } catch {
    }
}

async function recordFailure(input: {
    backend: VoiceSecurityBackend;
    now: Date;
    tenantId?: string | null;
    partnerId?: string | null;
    userId?: string | null;
    ipAddress?: string | null;
    code: CallGuardRejectCode;
}) {
    const failureThreshold = abuseFailureThreshold();
    if (failureThreshold <= 0) return null;
    const windowSeconds = abuseFailureWindowSeconds();
    const blockSeconds = abuseBlockSeconds();
    const candidates: Array<{ entityKind: AbuseEntityKind; entityId: string }> = [];
    if (input.userId) candidates.push({ entityKind: "user", entityId: input.userId });
    if (input.ipAddress) candidates.push({ entityKind: "ip", entityId: input.ipAddress });
    if (input.tenantId) candidates.push({ entityKind: "tenant", entityId: input.tenantId });
    if (input.partnerId) candidates.push({ entityKind: "partner", entityId: input.partnerId });

    let lastBlock: BlockState | null = null;
    for (const candidate of candidates) {
        const result = await input.backend.consumeRateLimit({
            bucket: buildRateBucket(candidate.entityKind, candidate.entityId, "failed"),
            limit: failureThreshold,
            windowSeconds,
            now: input.now,
        });
        if (!result.allowed) {
            const blockedUntil = new Date(input.now.getTime() + blockSeconds * 1000);
            await input.backend.setBlock({
                entityKind: candidate.entityKind,
                entityId: candidate.entityId,
                reasonCode: "temporary_block",
                blockedUntil,
            });
            lastBlock = {
                entityKind: candidate.entityKind,
                entityId: candidate.entityId,
                reasonCode: "temporary_block",
                blockedUntil,
            };
            safeCapture("voice_call_abuse_blocked", {
                entity_kind: candidate.entityKind,
                entity_id: candidate.entityId,
                reason_code: input.code,
                blocked_until: blockedUntil.toISOString(),
            });
        }
    }
    return lastBlock;
}

async function checkExistingBlocks(input: {
    backend: VoiceSecurityBackend;
    now: Date;
    tenantId: string;
    partnerId: string;
    userId?: string | null;
    ipAddress?: string | null;
}) {
    const checks: Array<{ entityKind: AbuseEntityKind; entityId: string }> = [
        { entityKind: "tenant", entityId: input.tenantId },
        { entityKind: "partner", entityId: input.partnerId },
    ];
    if (input.userId) checks.push({ entityKind: "user", entityId: input.userId });
    if (input.ipAddress) checks.push({ entityKind: "ip", entityId: input.ipAddress });

    for (const check of checks) {
        const block = await input.backend.getBlock({ entityKind: check.entityKind, entityId: check.entityId, now: input.now });
        if (block) return block;
    }
    return null;
}

async function enforceBurstAbuseProtection(input: {
    backend: VoiceSecurityBackend;
    now: Date;
    tenantId: string;
    partnerId: string;
    userId?: string | null;
    ipAddress?: string | null;
    tenantLimitPerMinute: number;
    partnerLimitPerMinute: number;
}) {
    const blockSeconds = abuseBlockSeconds();
    const rapidWindow = rapidAttemptWindowSeconds();
    const tenantRapid = await input.backend.consumeRateLimit({
        bucket: buildRateBucket("tenant", input.tenantId, "rapid"),
        limit: rapidAttemptThreshold(input.tenantLimitPerMinute),
        windowSeconds: rapidWindow,
        now: input.now,
    });
    if (!tenantRapid.allowed) {
        const blockedUntil = new Date(input.now.getTime() + blockSeconds * 1000);
        await input.backend.setBlock({
            entityKind: "tenant",
            entityId: input.tenantId,
            reasonCode: "rapid_attempts_detected",
            blockedUntil,
        });
        return rejectResult({
            tenantId: input.tenantId,
            partnerId: input.partnerId,
            code: "rapid_attempts_detected",
            reason: "Tenant rapid repeated call attempts detected.",
            retryAfterSeconds: tenantRapid.retryAfterSeconds,
            block: {
                entityKind: "tenant",
                entityId: input.tenantId,
                reasonCode: "rapid_attempts_detected",
                blockedUntil,
            },
        });
    }

    const partnerRapid = await input.backend.consumeRateLimit({
        bucket: buildRateBucket("partner", input.partnerId, "rapid"),
        limit: rapidAttemptThreshold(input.partnerLimitPerMinute),
        windowSeconds: rapidWindow,
        now: input.now,
    });
    if (!partnerRapid.allowed) {
        const blockedUntil = new Date(input.now.getTime() + blockSeconds * 1000);
        await input.backend.setBlock({
            entityKind: "partner",
            entityId: input.partnerId,
            reasonCode: "rapid_attempts_detected",
            blockedUntil,
        });
        return rejectResult({
            tenantId: input.tenantId,
            partnerId: input.partnerId,
            code: "rapid_attempts_detected",
            reason: "Partner rapid repeated call attempts detected.",
            retryAfterSeconds: partnerRapid.retryAfterSeconds,
            block: {
                entityKind: "partner",
                entityId: input.partnerId,
                reasonCode: "rapid_attempts_detected",
                blockedUntil,
            },
        });
    }

    if (input.userId) {
        const userRapid = await input.backend.consumeRateLimit({
            bucket: buildRateBucket("user", input.userId, "rapid"),
            limit: rapidAttemptThreshold(defaultUserCallsPerMinute()),
            windowSeconds: rapidWindow,
            now: input.now,
        });
        if (!userRapid.allowed) {
            const blockedUntil = new Date(input.now.getTime() + blockSeconds * 1000);
            await input.backend.setBlock({
                entityKind: "user",
                entityId: input.userId,
                reasonCode: "rapid_attempts_detected",
                blockedUntil,
            });
            return rejectResult({
                tenantId: input.tenantId,
                partnerId: input.partnerId,
                code: "rapid_attempts_detected",
                reason: "User rapid repeated call attempts detected.",
                retryAfterSeconds: userRapid.retryAfterSeconds,
                block: {
                    entityKind: "user",
                    entityId: input.userId,
                    reasonCode: "rapid_attempts_detected",
                    blockedUntil,
                },
            });
        }
    }

    if (input.ipAddress) {
        const ipRapid = await input.backend.consumeRateLimit({
            bucket: buildRateBucket("ip", input.ipAddress, "rapid"),
            limit: rapidAttemptThreshold(defaultIpCallsPerMinute()),
            windowSeconds: rapidWindow,
            now: input.now,
        });
        if (!ipRapid.allowed) {
            const blockedUntil = new Date(input.now.getTime() + blockSeconds * 1000);
            await input.backend.setBlock({
                entityKind: "ip",
                entityId: input.ipAddress,
                reasonCode: "rapid_attempts_detected",
                blockedUntil,
            });
            return rejectResult({
                tenantId: input.tenantId,
                partnerId: input.partnerId,
                code: "rapid_attempts_detected",
                reason: "IP rapid repeated call attempts detected.",
                retryAfterSeconds: ipRapid.retryAfterSeconds,
                block: {
                    entityKind: "ip",
                    entityId: input.ipAddress,
                    reasonCode: "rapid_attempts_detected",
                    blockedUntil,
                },
            });
        }
    }

    const spikeWindow = spikeWindowSeconds();
    const tenantSpike = await input.backend.consumeRateLimit({
        bucket: buildRateBucket("tenant", input.tenantId, "spike"),
        limit: spikeThreshold(input.tenantLimitPerMinute),
        windowSeconds: spikeWindow,
        now: input.now,
    });
    if (!tenantSpike.allowed) {
        const blockedUntil = new Date(input.now.getTime() + blockSeconds * 1000);
        await input.backend.setBlock({
            entityKind: "tenant",
            entityId: input.tenantId,
            reasonCode: "unusual_spike_detected",
            blockedUntil,
        });
        return rejectResult({
            tenantId: input.tenantId,
            partnerId: input.partnerId,
            code: "unusual_spike_detected",
            reason: "Tenant unusual spike in call volume detected.",
            retryAfterSeconds: tenantSpike.retryAfterSeconds,
            block: {
                entityKind: "tenant",
                entityId: input.tenantId,
                reasonCode: "unusual_spike_detected",
                blockedUntil,
            },
        });
    }

    return null;
}

function ensureAllowedFeatures(input: {
    requestedFeatures: VoiceFeature[];
    tenantFeatures: VoiceFeature[];
    partnerFeatures: VoiceFeature[];
    allowTransfer: boolean;
}) {
    for (const feature of input.requestedFeatures) {
        if (!featuresContain(input.tenantFeatures, feature)) return feature;
        if (!featuresContain(input.partnerFeatures, feature)) return feature;
        if (feature === "transfer" && !input.allowTransfer) return feature;
    }
    return null;
}

async function createRejectedResult(input: {
    backend: VoiceSecurityBackend;
    now: Date;
    tenantId?: string | null;
    partnerId?: string | null;
    userId?: string | null;
    ipAddress?: string | null;
    code: CallGuardRejectCode;
    reason: string;
    retryAfterSeconds?: number | null;
    block?: BlockState | null;
}) {
    await recordFailure({
        backend: input.backend,
        now: input.now,
        tenantId: input.tenantId,
        partnerId: input.partnerId,
        userId: input.userId,
        ipAddress: input.ipAddress,
        code: input.code,
    });
    safeCapture("voice_call_guard_rejected", {
        code: input.code,
        tenant_id: input.tenantId ?? null,
        partner_id: input.partnerId ?? null,
        user_id: input.userId ?? null,
        ip_address: input.ipAddress ?? null,
    });
    return rejectResult({
        tenantId: input.tenantId ?? null,
        partnerId: input.partnerId ?? null,
        code: input.code,
        reason: input.reason,
        retryAfterSeconds: input.retryAfterSeconds,
        block: input.block,
    });
}

export async function ensureVoiceSecuritySchema() {
    if (voiceSecuritySchemaReady) return voiceSecuritySchemaReady;
    voiceSecuritySchemaReady = (async () => {
        if (!isDatabaseConfigured()) return;
        const sql = getSql();
        await sql.unsafe(`
            create table if not exists tenant_limits (
                tenant_id text primary key references tenants(id) on delete cascade,
                max_concurrent_calls integer not null check (max_concurrent_calls >= 0),
                calls_per_minute integer not null check (calls_per_minute >= 0),
                allowed_features jsonb not null default '["voice"]'::jsonb,
                updated_at timestamptz not null default now()
            )
        `);
        await sql.unsafe(`create index if not exists tenant_limits_updated_at_idx on tenant_limits (updated_at)`);
        await sql.unsafe(`
            create table if not exists partner_limits (
                partner_id text primary key references partners(partner_id) on delete cascade,
                max_concurrent_calls integer not null check (max_concurrent_calls >= 0),
                calls_per_minute integer not null check (calls_per_minute >= 0),
                allowed_features jsonb not null default '["voice"]'::jsonb,
                updated_at timestamptz not null default now()
            )
        `);
        await sql.unsafe(`create index if not exists partner_limits_updated_at_idx on partner_limits (updated_at)`);
        await sql.unsafe(`
            create table if not exists call_rate_limits (
                bucket text not null,
                window_start timestamptz not null,
                count integer not null,
                updated_at timestamptz not null default now(),
                primary key (bucket, window_start)
            )
        `);
        await sql.unsafe(`create index if not exists call_rate_limits_updated_at_idx on call_rate_limits (updated_at)`);
        await sql.unsafe(`
            create table if not exists call_guard_blocks (
                entity_kind text not null check (entity_kind in ('tenant','partner','user','ip')),
                entity_id text not null,
                reason_code text not null,
                blocked_until timestamptz not null,
                created_at timestamptz not null default now(),
                updated_at timestamptz not null default now(),
                primary key (entity_kind, entity_id)
            )
        `);
        await sql.unsafe(`create index if not exists call_guard_blocks_blocked_until_idx on call_guard_blocks (blocked_until)`);
        await sql.unsafe(`
            create table if not exists call_active_counters (
                scope_kind text not null check (scope_kind in ('tenant','partner')),
                scope_id text not null,
                active_calls integer not null default 0 check (active_calls >= 0),
                updated_at timestamptz not null default now(),
                primary key (scope_kind, scope_id)
            )
        `);
        await sql.unsafe(`
            create table if not exists call_active_sessions (
                reservation_id uuid primary key,
                call_id text,
                provider_call_id text,
                tenant_id text not null references tenants(id) on delete restrict,
                partner_id text not null references partners(partner_id) on delete restrict,
                user_id text,
                ip_address text,
                status text not null check (status in ('reserved','active','released','ended')),
                requested_features jsonb not null default '["voice"]'::jsonb,
                overage_flags jsonb not null default '{"tenant":false,"partner":false}'::jsonb,
                release_reason text,
                created_at timestamptz not null default now(),
                started_at timestamptz,
                ended_at timestamptz,
                updated_at timestamptz not null default now()
            )
        `);
        await sql.unsafe(`create index if not exists call_active_sessions_tenant_id_status_idx on call_active_sessions (tenant_id, status)`);
        await sql.unsafe(`create index if not exists call_active_sessions_partner_id_status_idx on call_active_sessions (partner_id, status)`);
        await sql.unsafe(`create unique index if not exists call_active_sessions_call_id_unique on call_active_sessions (call_id) where call_id is not null`);
        await sql.unsafe(`create unique index if not exists call_active_sessions_provider_call_id_unique on call_active_sessions (provider_call_id) where provider_call_id is not null`);
    })();
    return voiceSecuritySchemaReady;
}

function buildDefaultLimits(input: { scope: "tenant" | "partner"; updatedAt?: string }) {
    if (input.scope === "tenant") {
        return {
            maxConcurrentCalls: defaultTenantConcurrencyLimit(),
            callsPerMinute: defaultTenantCallsPerMinute(),
            allowedFeatures: defaultAllowedFeatures(),
            updatedAt: input.updatedAt ?? new Date(0).toISOString(),
        };
    }
    return {
        maxConcurrentCalls: defaultPartnerConcurrencyLimit(),
        callsPerMinute: defaultPartnerCallsPerMinute(),
        allowedFeatures: defaultAllowedFeatures(),
        updatedAt: input.updatedAt ?? new Date(0).toISOString(),
    };
}

function createPostgresVoiceSecurityBackend(): VoiceSecurityBackend {
    return {
        async resolveScope(input) {
            if (!isDatabaseConfigured()) return null;
            await ensureVoiceSecuritySchema();
            const sql = getSql();
            const tenantId = normalizeTenantId(input.tenantId);
            if (!tenantId) return null;
            const rows = await sql<
                Array<{
                    tenant_id: string;
                    tenant_partner_id: string;
                    tenant_status: TenantStatus;
                    partner_id: string;
                    partner_status: PartnerStatus;
                    allow_transfer: boolean;
                    tenant_max_concurrent_calls: number | null;
                    tenant_calls_per_minute: number | null;
                    tenant_allowed_features: unknown;
                    tenant_limits_updated_at: Date | null;
                    partner_max_concurrent_calls: number | null;
                    partner_calls_per_minute: number | null;
                    partner_allowed_features: unknown;
                    partner_limits_updated_at: Date | null;
                }>
            >`
                select
                    t.id as tenant_id,
                    t.partner_id as tenant_partner_id,
                    t.status as tenant_status,
                    p.partner_id,
                    p.status as partner_status,
                    p.allow_transfer,
                    tl.max_concurrent_calls as tenant_max_concurrent_calls,
                    tl.calls_per_minute as tenant_calls_per_minute,
                    tl.allowed_features as tenant_allowed_features,
                    tl.updated_at as tenant_limits_updated_at,
                    pl.max_concurrent_calls as partner_max_concurrent_calls,
                    pl.calls_per_minute as partner_calls_per_minute,
                    pl.allowed_features as partner_allowed_features,
                    pl.updated_at as partner_limits_updated_at
                from tenants t
                join partners p on p.partner_id = t.partner_id
                left join tenant_limits tl on tl.tenant_id = t.id
                left join partner_limits pl on pl.partner_id = p.partner_id
                where t.id = ${tenantId}
                limit 1
            `;
            const row = rows[0];
            if (!row) return null;
            return {
                tenant: {
                    id: row.tenant_id,
                    partnerId: row.tenant_partner_id,
                    status: row.tenant_status,
                },
                partner: {
                    id: row.partner_id,
                    status: row.partner_status,
                    allowTransfer: row.allow_transfer,
                },
                tenantLimits: {
                    maxConcurrentCalls: row.tenant_max_concurrent_calls ?? defaultTenantConcurrencyLimit(),
                    callsPerMinute: row.tenant_calls_per_minute ?? defaultTenantCallsPerMinute(),
                    allowedFeatures: parseAllowedFeatures(row.tenant_allowed_features),
                    updatedAt: row.tenant_limits_updated_at?.toISOString() ?? new Date(0).toISOString(),
                },
                partnerLimits: {
                    maxConcurrentCalls: row.partner_max_concurrent_calls ?? defaultPartnerConcurrencyLimit(),
                    callsPerMinute: row.partner_calls_per_minute ?? defaultPartnerCallsPerMinute(),
                    allowedFeatures: parseAllowedFeatures(row.partner_allowed_features),
                    updatedAt: row.partner_limits_updated_at?.toISOString() ?? new Date(0).toISOString(),
                },
            };
        },
        async validateCaller(input) {
            const authz = await getAuthzContextForUser(input.userId);
            if (!authz) return false;
            if (!canAccessTenant({ ctx: authz, tenantId: input.tenantId, partnerId: input.partnerId })) return false;
            return hasPermissionInTenant({ ctx: authz, tenantId: input.tenantId, permission: "start_call" });
        },
        async ensureBillingContext(input) {
            await ensureTenantAccounts({ tenantId: input.tenantId, partnerId: input.partnerId }).catch(() => undefined);
            const accounts = await getTenantAccounts({ tenantId: input.tenantId }).catch(() => ({ ok: false as const }));
            if (!accounts.ok) {
                return { usageAccountId: null, billingAccountId: null };
            }
            return { usageAccountId: accounts.usageAccountId, billingAccountId: accounts.billingAccountId };
        },
        async getBlock(input) {
            if (!isDatabaseConfigured()) return null;
            await ensureVoiceSecuritySchema();
            const sql = getSql();
            const rows = await sql<Array<{ reason_code: CallGuardRejectCode; blocked_until: Date }>>`
                select reason_code, blocked_until
                from call_guard_blocks
                where entity_kind = ${input.entityKind}
                  and entity_id = ${input.entityId}
                  and blocked_until > ${input.now}
                limit 1
            `;
            const row = rows[0];
            if (!row) return null;
            return {
                entityKind: input.entityKind,
                entityId: input.entityId,
                reasonCode: row.reason_code,
                blockedUntil: row.blocked_until,
            };
        },
        async setBlock(input) {
            if (!isDatabaseConfigured()) return;
            await ensureVoiceSecuritySchema();
            const sql = getSql();
            await sql`
                insert into call_guard_blocks (entity_kind, entity_id, reason_code, blocked_until, updated_at)
                values (${input.entityKind}, ${input.entityId}, ${input.reasonCode}, ${input.blockedUntil}, now())
                on conflict (entity_kind, entity_id)
                do update set reason_code = excluded.reason_code, blocked_until = excluded.blocked_until, updated_at = now()
            `;
        },
        async consumeRateLimit(input) {
            if (!isDatabaseConfigured()) return { allowed: true, count: 0, retryAfterSeconds: 0 };
            await ensureVoiceSecuritySchema();
            const sql = getSql();
            const now = input.now;
            const windowMs = input.windowSeconds * 1000;
            const start = new Date(Math.floor(now.getTime() / windowMs) * windowMs);
            const rows = await sql<Array<{ count: number }>>`
                insert into call_rate_limits (bucket, window_start, count, updated_at)
                values (${input.bucket}, ${start}, 1, now())
                on conflict (bucket, window_start)
                do update set count = call_rate_limits.count + 1, updated_at = now()
                returning count
            `;
            const count = rows[0]?.count ?? 1;
            const remainingMs = windowMs - (now.getTime() - start.getTime());
            return {
                allowed: count <= input.limit,
                count,
                retryAfterSeconds: Math.max(0, Math.ceil(remainingMs / 1000)),
            };
        },
        async reserveConcurrency(input) {
            if (!isDatabaseConfigured()) {
                return {
                    allowed: true,
                    reservationId: crypto.randomUUID(),
                    activeCalls: { tenant: 0, partner: 0 },
                    overage: { tenant: false, partner: false },
                    rejectionCode: null,
                };
            }
            await ensureVoiceSecuritySchema();
            const sql = getSql();
            const reservationId = crypto.randomUUID();
            return sql.begin(async (tx) => {
                const q = tx as unknown as ReturnType<typeof getSql>;
                await q`
                    insert into call_active_counters (scope_kind, scope_id, active_calls, updated_at)
                    values ('tenant', ${input.tenantId}, 0, now())
                    on conflict (scope_kind, scope_id) do nothing
                `;
                await q`
                    insert into call_active_counters (scope_kind, scope_id, active_calls, updated_at)
                    values ('partner', ${input.partnerId}, 0, now())
                    on conflict (scope_kind, scope_id) do nothing
                `;
                const counters = await q<Array<{ scope_kind: "tenant" | "partner"; active_calls: number }>>`
                    select scope_kind, active_calls
                    from call_active_counters
                    where (scope_kind = 'partner' and scope_id = ${input.partnerId})
                       or (scope_kind = 'tenant' and scope_id = ${input.tenantId})
                    order by scope_kind asc
                    for update
                `;
                const tenantActive = counters.find((row) => row.scope_kind === "tenant")?.active_calls ?? 0;
                const partnerActive = counters.find((row) => row.scope_kind === "partner")?.active_calls ?? 0;
                const tenantOverage = tenantActive >= input.tenantLimit;
                const partnerOverage = partnerActive >= input.partnerLimit;
                if ((tenantOverage || partnerOverage) && !input.allowOverage) {
                    return {
                        allowed: false,
                        reservationId: null,
                        activeCalls: { tenant: tenantActive, partner: partnerActive },
                        overage: { tenant: tenantOverage, partner: partnerOverage },
                        rejectionCode: tenantOverage ? "tenant_concurrency_exceeded" : "partner_concurrency_exceeded",
                    };
                }
                await q`
                    insert into call_active_sessions (
                        reservation_id,
                        call_id,
                        provider_call_id,
                        tenant_id,
                        partner_id,
                        user_id,
                        ip_address,
                        status,
                        requested_features,
                        overage_flags,
                        created_at,
                        updated_at
                    )
                    values (
                        ${reservationId}::uuid,
                        ${input.callId || null},
                        ${input.providerCallId || null},
                        ${input.tenantId},
                        ${input.partnerId},
                        ${input.userId || null},
                        ${input.ipAddress || null},
                        'reserved',
                        ${input.requestedFeatures as unknown as never},
                        ${{ tenant: tenantOverage, partner: partnerOverage } as never},
                        ${input.now},
                        ${input.now}
                    )
                `;
                await q`
                    update call_active_counters
                    set active_calls = active_calls + 1, updated_at = ${input.now}
                    where scope_kind = 'tenant' and scope_id = ${input.tenantId}
                `;
                await q`
                    update call_active_counters
                    set active_calls = active_calls + 1, updated_at = ${input.now}
                    where scope_kind = 'partner' and scope_id = ${input.partnerId}
                `;
                return {
                    allowed: true,
                    reservationId,
                    activeCalls: { tenant: tenantActive + 1, partner: partnerActive + 1 },
                    overage: { tenant: tenantOverage, partner: partnerOverage },
                    rejectionCode: null,
                };
            });
        },
        async confirmCallStart(input) {
            if (!isDatabaseConfigured()) {
                return { ok: true, tenantId: null, partnerId: null, status: "active", activeCalls: null };
            }
            await ensureVoiceSecuritySchema();
            const sql = getSql();
            return sql.begin(async (tx) => {
                const q = tx as unknown as ReturnType<typeof getSql>;
                const rows = await q<
                    Array<{ tenant_id: string; partner_id: string; status: CallReservationStatus }>
                >`
                    select tenant_id, partner_id, status
                    from call_active_sessions
                    where reservation_id = ${input.reservationId}::uuid
                    limit 1
                    for update
                `;
                const row = rows[0];
                if (!row) return { ok: false, tenantId: null, partnerId: null, status: null, activeCalls: null };
                if (row.status === "released" || row.status === "ended") {
                    return { ok: false, tenantId: row.tenant_id, partnerId: row.partner_id, status: row.status, activeCalls: null };
                }
                await q`
                    update call_active_sessions
                    set
                        status = 'active',
                        call_id = coalesce(${input.callId || null}, call_id),
                        provider_call_id = coalesce(${input.providerCallId || null}, provider_call_id),
                        started_at = coalesce(started_at, ${input.startedAt ?? new Date()}),
                        updated_at = ${input.startedAt ?? new Date()}
                    where reservation_id = ${input.reservationId}::uuid
                `;
                const counters = await q<Array<{ scope_kind: "tenant" | "partner"; active_calls: number }>>`
                    select scope_kind, active_calls
                    from call_active_counters
                    where (scope_kind = 'partner' and scope_id = ${row.partner_id})
                       or (scope_kind = 'tenant' and scope_id = ${row.tenant_id})
                `;
                return {
                    ok: true,
                    tenantId: row.tenant_id,
                    partnerId: row.partner_id,
                    status: "active" as const,
                    activeCalls: {
                        tenant: counters.find((item) => item.scope_kind === "tenant")?.active_calls ?? 0,
                        partner: counters.find((item) => item.scope_kind === "partner")?.active_calls ?? 0,
                    },
                };
            });
        },
        async releaseReservation(input) {
            return releaseOrEndCall("released", input);
        },
        async endCall(input) {
            return releaseOrEndCall("ended", input);
        },
    };

    async function releaseOrEndCall(
        targetStatus: "released" | "ended",
        input: ReleaseGuardedCallReservationInput | EndGuardedCallInput
    ): Promise<ReservationLifecycleResult> {
        if (!isDatabaseConfigured()) {
            return { ok: true, tenantId: null, partnerId: null, status: targetStatus, activeCalls: null };
        }
        await ensureVoiceSecuritySchema();
        const sql = getSql();
        const reservationId = "reservationId" in input ? input.reservationId ?? null : input.reservationId ?? null;
        const callId = "callId" in input ? input.callId ?? null : null;
        const providerCallId = "providerCallId" in input ? input.providerCallId ?? null : null;
        const endedAt = "releasedAt" in input ? input.releasedAt ?? new Date() : (input as EndGuardedCallInput).endedAt ?? new Date();
        const reason = input.reason ?? null;
        return sql.begin(async (tx) => {
            const q = tx as unknown as ReturnType<typeof getSql>;
            const rows = reservationId
                ? await q<Array<{ reservation_id: string; tenant_id: string; partner_id: string; status: CallReservationStatus }>>`
                    select reservation_id::text as reservation_id, tenant_id, partner_id, status
                    from call_active_sessions
                    where reservation_id = ${reservationId}::uuid
                    limit 1
                    for update
                `
                : callId
                    ? await q<Array<{ reservation_id: string; tenant_id: string; partner_id: string; status: CallReservationStatus }>>`
                        select reservation_id::text as reservation_id, tenant_id, partner_id, status
                        from call_active_sessions
                        where call_id = ${callId}
                        limit 1
                        for update
                    `
                    : providerCallId
                        ? await q<Array<{ reservation_id: string; tenant_id: string; partner_id: string; status: CallReservationStatus }>>`
                            select reservation_id::text as reservation_id, tenant_id, partner_id, status
                            from call_active_sessions
                            where provider_call_id = ${providerCallId}
                            limit 1
                            for update
                        `
                        : [];
            const row = rows[0];
            if (!row) return { ok: false, tenantId: null, partnerId: null, status: null, activeCalls: null };
            if (row.status === "released" || row.status === "ended") {
                const counters = await q<Array<{ scope_kind: "tenant" | "partner"; active_calls: number }>>`
                    select scope_kind, active_calls
                    from call_active_counters
                    where (scope_kind = 'partner' and scope_id = ${row.partner_id})
                       or (scope_kind = 'tenant' and scope_id = ${row.tenant_id})
                `;
                return {
                    ok: true,
                    tenantId: row.tenant_id,
                    partnerId: row.partner_id,
                    status: row.status,
                    activeCalls: {
                        tenant: counters.find((item) => item.scope_kind === "tenant")?.active_calls ?? 0,
                        partner: counters.find((item) => item.scope_kind === "partner")?.active_calls ?? 0,
                    },
                };
            }
            await q`
                update call_active_sessions
                set status = ${targetStatus}, ended_at = ${endedAt}, release_reason = ${reason}, updated_at = ${endedAt}
                where reservation_id = ${row.reservation_id}::uuid
            `;
            await q`
                update call_active_counters
                set active_calls = greatest(active_calls - 1, 0), updated_at = ${endedAt}
                where scope_kind = 'tenant' and scope_id = ${row.tenant_id}
            `;
            await q`
                update call_active_counters
                set active_calls = greatest(active_calls - 1, 0), updated_at = ${endedAt}
                where scope_kind = 'partner' and scope_id = ${row.partner_id}
            `;
            const counters = await q<Array<{ scope_kind: "tenant" | "partner"; active_calls: number }>>`
                select scope_kind, active_calls
                from call_active_counters
                where (scope_kind = 'partner' and scope_id = ${row.partner_id})
                   or (scope_kind = 'tenant' and scope_id = ${row.tenant_id})
            `;
            return {
                ok: true,
                tenantId: row.tenant_id,
                partnerId: row.partner_id,
                status: targetStatus,
                activeCalls: {
                    tenant: counters.find((item) => item.scope_kind === "tenant")?.active_calls ?? 0,
                    partner: counters.find((item) => item.scope_kind === "partner")?.active_calls ?? 0,
                },
            };
        });
    }
}

type MemoryLimitRecord = GuardLimits;

type MemoryPartnerRecord = {
    id: string;
    status: PartnerStatus;
    allowTransfer: boolean;
};

type MemoryTenantRecord = {
    id: string;
    partnerId: string;
    status: TenantStatus;
};

type MemoryReservationRecord = {
    reservationId: string;
    callId: string | null;
    providerCallId: string | null;
    tenantId: string;
    partnerId: string;
    userId: string | null;
    ipAddress: string | null;
    status: CallReservationStatus;
    requestedFeatures: VoiceFeature[];
};

type MemoryBucketRecord = {
    count: number;
};

export type InMemoryVoiceSecurityBackend = VoiceSecurityBackend & {
    seedPartner(input: { partnerId: string; status?: PartnerStatus; allowTransfer?: boolean; limits?: Partial<GuardLimits> }): void;
    seedTenant(input: { tenantId: string; partnerId: string; status?: TenantStatus; limits?: Partial<GuardLimits> }): void;
    setAuthz(input: { userId: string; ctx: AuthzContext }): void;
    getActiveCalls(input: { tenantId: string; partnerId: string }): { tenant: number; partner: number };
};

export function createInMemoryVoiceSecurityBackend(): InMemoryVoiceSecurityBackend {
    const partners = new Map<string, MemoryPartnerRecord>();
    const tenants = new Map<string, MemoryTenantRecord>();
    const partnerLimits = new Map<string, MemoryLimitRecord>();
    const tenantLimits = new Map<string, MemoryLimitRecord>();
    const authzByUser = new Map<string, AuthzContext>();
    const blocks = new Map<string, BlockState>();
    const buckets = new Map<string, MemoryBucketRecord>();
    const reservations = new Map<string, MemoryReservationRecord>();
    const billingByTenant = new Map<string, { usageAccountId: string; billingAccountId: string }>();
    const counterByScope = new Map<string, number>();

    function scopeCount(kind: "tenant" | "partner", scopeId: string) {
        return counterByScope.get(`${kind}:${scopeId}`) ?? 0;
    }

    function setScopeCount(kind: "tenant" | "partner", scopeId: string, value: number) {
        counterByScope.set(`${kind}:${scopeId}`, Math.max(0, value));
    }

    function mergeLimits(base: GuardLimits, patch?: Partial<GuardLimits>) {
        return {
            maxConcurrentCalls: patch?.maxConcurrentCalls ?? base.maxConcurrentCalls,
            callsPerMinute: patch?.callsPerMinute ?? base.callsPerMinute,
            allowedFeatures: uniqueFeatures(patch?.allowedFeatures ?? base.allowedFeatures),
            updatedAt: patch?.updatedAt ?? base.updatedAt,
        };
    }

    return {
        seedPartner(input) {
            const partnerId = normalizePartnerId(input.partnerId);
            partners.set(partnerId, {
                id: partnerId,
                status: input.status ?? "active",
                allowTransfer: input.allowTransfer ?? true,
            });
            partnerLimits.set(partnerId, mergeLimits(buildDefaultLimits({ scope: "partner", updatedAt: new Date().toISOString() }), input.limits));
        },
        seedTenant(input) {
            const tenantId = normalizeTenantId(input.tenantId);
            const partnerId = normalizePartnerId(input.partnerId);
            tenants.set(tenantId, {
                id: tenantId,
                partnerId,
                status: input.status ?? "active",
            });
            tenantLimits.set(tenantId, mergeLimits(buildDefaultLimits({ scope: "tenant", updatedAt: new Date().toISOString() }), input.limits));
        },
        setAuthz(input) {
            authzByUser.set(input.userId, input.ctx);
        },
        getActiveCalls(input) {
            return { tenant: scopeCount("tenant", input.tenantId), partner: scopeCount("partner", input.partnerId) };
        },
        async resolveScope(input) {
            const tenantId = normalizeTenantId(input.tenantId);
            const tenant = tenants.get(tenantId);
            if (!tenant) return null;
            const partner = partners.get(tenant.partnerId);
            if (!partner) return null;
            return {
                tenant: { id: tenant.id, partnerId: tenant.partnerId, status: tenant.status },
                partner: { id: partner.id, status: partner.status, allowTransfer: partner.allowTransfer },
                tenantLimits: tenantLimits.get(tenant.id) ?? buildDefaultLimits({ scope: "tenant", updatedAt: new Date().toISOString() }),
                partnerLimits: partnerLimits.get(partner.id) ?? buildDefaultLimits({ scope: "partner", updatedAt: new Date().toISOString() }),
            };
        },
        async validateCaller(input) {
            const ctx = authzByUser.get(input.userId);
            if (!ctx) return false;
            if (!canAccessTenant({ ctx, tenantId: input.tenantId, partnerId: input.partnerId })) return false;
            return ctx.platformRole === "platform_admin" || ctx.permissions.has("start_call");
        },
        async ensureBillingContext(input) {
            let found = billingByTenant.get(input.tenantId);
            if (!found) {
                found = { usageAccountId: crypto.randomUUID(), billingAccountId: crypto.randomUUID() };
                billingByTenant.set(input.tenantId, found);
            }
            return found;
        },
        async getBlock(input) {
            const key = buildBlockKey(input.entityKind, input.entityId);
            const found = blocks.get(key);
            if (!found) return null;
            if (found.blockedUntil.getTime() <= input.now.getTime()) {
                blocks.delete(key);
                return null;
            }
            return found;
        },
        async setBlock(input) {
            blocks.set(buildBlockKey(input.entityKind, input.entityId), {
                entityKind: input.entityKind,
                entityId: input.entityId,
                reasonCode: input.reasonCode,
                blockedUntil: input.blockedUntil,
            });
        },
        async consumeRateLimit(input) {
            const windowMs = input.windowSeconds * 1000;
            const windowStart = Math.floor(input.now.getTime() / windowMs) * windowMs;
            const key = `${input.bucket}:${windowStart}`;
            const next = (buckets.get(key)?.count ?? 0) + 1;
            buckets.set(key, { count: next });
            const retryAfterSeconds = Math.max(0, Math.ceil((windowStart + windowMs - input.now.getTime()) / 1000));
            return { allowed: next <= input.limit, count: next, retryAfterSeconds };
        },
        async reserveConcurrency(input) {
            const tenantCount = scopeCount("tenant", input.tenantId);
            const partnerCount = scopeCount("partner", input.partnerId);
            const tenantOverage = tenantCount >= input.tenantLimit;
            const partnerOverage = partnerCount >= input.partnerLimit;
            if ((tenantOverage || partnerOverage) && !input.allowOverage) {
                return {
                    allowed: false,
                    reservationId: null,
                    activeCalls: { tenant: tenantCount, partner: partnerCount },
                    overage: { tenant: tenantOverage, partner: partnerOverage },
                    rejectionCode: tenantOverage ? "tenant_concurrency_exceeded" : "partner_concurrency_exceeded",
                };
            }
            const reservationId = crypto.randomUUID();
            reservations.set(reservationId, {
                reservationId,
                callId: input.callId ?? null,
                providerCallId: input.providerCallId ?? null,
                tenantId: input.tenantId,
                partnerId: input.partnerId,
                userId: input.userId ?? null,
                ipAddress: input.ipAddress ?? null,
                status: "reserved",
                requestedFeatures: input.requestedFeatures,
            });
            setScopeCount("tenant", input.tenantId, tenantCount + 1);
            setScopeCount("partner", input.partnerId, partnerCount + 1);
            return {
                allowed: true,
                reservationId,
                activeCalls: { tenant: tenantCount + 1, partner: partnerCount + 1 },
                overage: { tenant: tenantOverage, partner: partnerOverage },
                rejectionCode: null,
            };
        },
        async confirmCallStart(input) {
            const record = reservations.get(input.reservationId);
            if (!record) return { ok: false, tenantId: null, partnerId: null, status: null, activeCalls: null };
            if (record.status === "released" || record.status === "ended") {
                return {
                    ok: false,
                    tenantId: record.tenantId,
                    partnerId: record.partnerId,
                    status: record.status,
                    activeCalls: this.getActiveCalls({ tenantId: record.tenantId, partnerId: record.partnerId }),
                };
            }
            record.status = "active";
            if (input.callId) record.callId = input.callId;
            if (input.providerCallId) record.providerCallId = input.providerCallId;
            return {
                ok: true,
                tenantId: record.tenantId,
                partnerId: record.partnerId,
                status: record.status,
                activeCalls: this.getActiveCalls({ tenantId: record.tenantId, partnerId: record.partnerId }),
            };
        },
        async releaseReservation(input) {
            const record = reservations.get(input.reservationId);
            if (!record) return { ok: false, tenantId: null, partnerId: null, status: null, activeCalls: null };
            if (record.status !== "released" && record.status !== "ended") {
                record.status = "released";
                setScopeCount("tenant", record.tenantId, scopeCount("tenant", record.tenantId) - 1);
                setScopeCount("partner", record.partnerId, scopeCount("partner", record.partnerId) - 1);
            }
            return {
                ok: true,
                tenantId: record.tenantId,
                partnerId: record.partnerId,
                status: record.status,
                activeCalls: this.getActiveCalls({ tenantId: record.tenantId, partnerId: record.partnerId }),
            };
        },
        async endCall(input) {
            const record = input.reservationId
                ? reservations.get(input.reservationId)
                : Array.from(reservations.values()).find((item) => item.callId === (input.callId ?? null) || item.providerCallId === (input.providerCallId ?? null));
            if (!record) return { ok: false, tenantId: null, partnerId: null, status: null, activeCalls: null };
            if (record.status !== "released" && record.status !== "ended") {
                record.status = "ended";
                setScopeCount("tenant", record.tenantId, scopeCount("tenant", record.tenantId) - 1);
                setScopeCount("partner", record.partnerId, scopeCount("partner", record.partnerId) - 1);
            }
            return {
                ok: true,
                tenantId: record.tenantId,
                partnerId: record.partnerId,
                status: record.status,
                activeCalls: this.getActiveCalls({ tenantId: record.tenantId, partnerId: record.partnerId }),
            };
        },
    };
}

export function createVoiceSecurityService(backend: VoiceSecurityBackend): VoiceSecurityService {
    return {
        async call_guard(input) {
            const now = input.now ?? new Date();
            const tenantId = normalizeTenantId(input.tenantId);
            const partnerIdInput = input.partnerId ? normalizePartnerId(input.partnerId) : null;
            const userId = input.userId?.trim() || null;
            const ipAddress = sanitizeIpAddress(input.ipAddress);
            const requestedFeatures = uniqueFeatures(input.requestedFeatures);
            const reserveConcurrency = input.reserveConcurrency !== false;
            const allowOverage = Boolean(input.allowOverage);

            if (!tenantId || !isSafeIdentifier(tenantId)) {
                return createRejectedResult({
                    backend,
                    now,
                    userId,
                    ipAddress,
                    code: "invalid_tenant_id",
                    reason: "Invalid tenant identifier.",
                });
            }
            if (partnerIdInput && !isSafeIdentifier(partnerIdInput)) {
                return createRejectedResult({
                    backend,
                    now,
                    tenantId,
                    userId,
                    ipAddress,
                    code: "invalid_partner_id",
                    reason: "Invalid partner identifier.",
                });
            }

            const resolved = await backend.resolveScope({ tenantId, partnerId: partnerIdInput });
            if (!resolved) {
                const tenant = await findTenantById(tenantId).catch(() => null);
                if (tenant && partnerIdInput) {
                    const partner = await backend.resolveScope({ tenantId: tenant.id, partnerId: tenant.partner_id });
                    if (partner && partner.partner.id !== partnerIdInput) {
                        return createRejectedResult({
                            backend,
                            now,
                            tenantId,
                            partnerId: partnerIdInput,
                            userId,
                            ipAddress,
                            code: "tenant_partner_mismatch",
                            reason: "Tenant does not belong to the requested partner.",
                        });
                    }
                }
                return createRejectedResult({
                    backend,
                    now,
                    tenantId,
                    partnerId: partnerIdInput,
                    userId,
                    ipAddress,
                    code: "tenant_not_found",
                    reason: "Tenant not found.",
                });
            }

            const partnerId = resolved.partner.id;
            if (partnerIdInput && partnerIdInput !== partnerId) {
                return createRejectedResult({
                    backend,
                    now,
                    tenantId,
                    partnerId: partnerIdInput,
                    userId,
                    ipAddress,
                    code: "tenant_partner_mismatch",
                    reason: "Tenant does not belong to the requested partner.",
                });
            }

            const block = await checkExistingBlocks({
                backend,
                now,
                tenantId,
                partnerId,
                userId,
                ipAddress,
            });
            if (block) {
                return createRejectedResult({
                    backend,
                    now,
                    tenantId,
                    partnerId,
                    userId,
                    ipAddress,
                    code: "temporary_block",
                    reason: `${block.entityKind} is temporarily blocked for call creation.`,
                    retryAfterSeconds: Math.max(0, Math.ceil((block.blockedUntil.getTime() - now.getTime()) / 1000)),
                    block,
                });
            }

            if (resolved.tenant.status !== "active") {
                return createRejectedResult({
                    backend,
                    now,
                    tenantId,
                    partnerId,
                    userId,
                    ipAddress,
                    code: "tenant_inactive",
                    reason: `Tenant status ${resolved.tenant.status} cannot initiate calls.`,
                });
            }
            if (resolved.partner.status !== "active") {
                return createRejectedResult({
                    backend,
                    now,
                    tenantId,
                    partnerId,
                    userId,
                    ipAddress,
                    code: "partner_inactive",
                    reason: `Partner status ${resolved.partner.status} cannot initiate calls.`,
                });
            }

            if (userId) {
                const callerOk = await backend.validateCaller({ userId, tenantId, partnerId });
                if (!callerOk) {
                    return createRejectedResult({
                        backend,
                        now,
                        tenantId,
                        partnerId,
                        userId,
                        ipAddress,
                        code: "forbidden",
                        reason: "Caller is not authorized to start calls for this tenant scope.",
                    });
                }
            }

            const forbiddenFeature = ensureAllowedFeatures({
                requestedFeatures,
                tenantFeatures: resolved.tenantLimits.allowedFeatures,
                partnerFeatures: resolved.partnerLimits.allowedFeatures,
                allowTransfer: resolved.partner.allowTransfer,
            });
            if (forbiddenFeature) {
                return createRejectedResult({
                    backend,
                    now,
                    tenantId,
                    partnerId,
                    userId,
                    ipAddress,
                    code: "feature_not_allowed",
                    reason: `Requested feature ${forbiddenFeature} is not allowed for this scope.`,
                });
            }

            const burstViolation = await enforceBurstAbuseProtection({
                backend,
                now,
                tenantId,
                partnerId,
                userId,
                ipAddress,
                tenantLimitPerMinute: resolved.tenantLimits.callsPerMinute,
                partnerLimitPerMinute: resolved.partnerLimits.callsPerMinute,
            });
            if (burstViolation) {
                return createRejectedResult({
                    backend,
                    now,
                    tenantId,
                    partnerId,
                    userId,
                    ipAddress,
                    code: burstViolation.code,
                    reason: burstViolation.reason,
                    retryAfterSeconds: burstViolation.retryAfterSeconds,
                    block: burstViolation.blockExpiresAt
                        ? {
                            entityKind: "tenant",
                            entityId: tenantId,
                            reasonCode: burstViolation.code,
                            blockedUntil: new Date(burstViolation.blockExpiresAt),
                        }
                        : null,
                });
            }

            const tenantRate = await backend.consumeRateLimit({
                bucket: buildRateBucket("tenant", tenantId, "minute"),
                limit: resolved.tenantLimits.callsPerMinute,
                windowSeconds: 60,
                now,
            });
            if (!tenantRate.allowed) {
                return createRejectedResult({
                    backend,
                    now,
                    tenantId,
                    partnerId,
                    userId,
                    ipAddress,
                    code: "tenant_rate_limited",
                    reason: "Tenant call initiation rate exceeded.",
                    retryAfterSeconds: tenantRate.retryAfterSeconds,
                });
            }

            const partnerRate = await backend.consumeRateLimit({
                bucket: buildRateBucket("partner", partnerId, "minute"),
                limit: resolved.partnerLimits.callsPerMinute,
                windowSeconds: 60,
                now,
            });
            if (!partnerRate.allowed) {
                return createRejectedResult({
                    backend,
                    now,
                    tenantId,
                    partnerId,
                    userId,
                    ipAddress,
                    code: "partner_rate_limited",
                    reason: "Partner call initiation rate exceeded.",
                    retryAfterSeconds: partnerRate.retryAfterSeconds,
                });
            }

            if (userId) {
                const userRate = await backend.consumeRateLimit({
                    bucket: buildRateBucket("user", userId, "minute"),
                    limit: defaultUserCallsPerMinute(),
                    windowSeconds: 60,
                    now,
                });
                if (!userRate.allowed) {
                    return createRejectedResult({
                        backend,
                        now,
                        tenantId,
                        partnerId,
                        userId,
                        ipAddress,
                        code: "user_rate_limited",
                        reason: "User call initiation rate exceeded.",
                        retryAfterSeconds: userRate.retryAfterSeconds,
                    });
                }
            }

            if (ipAddress) {
                const ipRate = await backend.consumeRateLimit({
                    bucket: buildRateBucket("ip", ipAddress, "minute"),
                    limit: defaultIpCallsPerMinute(),
                    windowSeconds: 60,
                    now,
                });
                if (!ipRate.allowed) {
                    return createRejectedResult({
                        backend,
                        now,
                        tenantId,
                        partnerId,
                        userId,
                        ipAddress,
                        code: "ip_rate_limited",
                        reason: "IP call initiation rate exceeded.",
                        retryAfterSeconds: ipRate.retryAfterSeconds,
                    });
                }
            }

            let reservationId: string | null = null;
            let activeCalls = { tenant: 0, partner: 0 };
            let overage = { tenant: false, partner: false };

            if (reserveConcurrency) {
                const reservation = await backend.reserveConcurrency({
                    tenantId,
                    partnerId,
                    userId,
                    ipAddress,
                    callId: input.callId ?? null,
                    providerCallId: input.providerCallId ?? null,
                    requestedFeatures,
                    allowOverage,
                    tenantLimit: resolved.tenantLimits.maxConcurrentCalls,
                    partnerLimit: resolved.partnerLimits.maxConcurrentCalls,
                    now,
                });
                if (!reservation.allowed) {
                    return createRejectedResult({
                        backend,
                        now,
                        tenantId,
                        partnerId,
                        userId,
                        ipAddress,
                        code: reservation.rejectionCode ?? "tenant_concurrency_exceeded",
                        reason: reservation.rejectionCode === "partner_concurrency_exceeded"
                            ? "Partner concurrent call limit exceeded."
                            : "Tenant concurrent call limit exceeded.",
                    });
                }
                if (!reservation.reservationId) {
                    return createRejectedResult({
                        backend,
                        now,
                        tenantId,
                        partnerId,
                        userId,
                        ipAddress,
                        code: "tenant_concurrency_exceeded",
                        reason: "Unable to reserve call capacity.",
                    });
                }
                reservationId = reservation.reservationId;
                activeCalls = reservation.activeCalls;
                overage = reservation.overage;
            }

            const billing = await backend.ensureBillingContext({ tenantId, partnerId });
            safeCapture("voice_call_guard_allowed", {
                tenant_id: tenantId,
                partner_id: partnerId,
                user_id: userId,
                overage_tenant: overage.tenant,
                overage_partner: overage.partner,
            });

            return {
                outcome: "ALLOW",
                tenantId,
                partnerId,
                reservationId,
                activeCalls,
                overage,
                allowedFeatures: uniqueFeatures(
                    resolved.tenantLimits.allowedFeatures.filter((feature) => resolved.partnerLimits.allowedFeatures.includes(feature))
                ),
                requestedFeatures,
                usageAccountId: billing.usageAccountId,
                billingAccountId: billing.billingAccountId,
            };
        },
        async confirmGuardedCallStart(input) {
            return backend.confirmCallStart(input);
        },
        async releaseGuardedCallReservation(input) {
            return backend.releaseReservation(input);
        },
        async endGuardedCall(input) {
            return backend.endCall(input);
        },
    };
}

function getDefaultBackend() {
    if (!defaultVoiceSecurityBackend) {
        defaultVoiceSecurityBackend = isDatabaseConfigured() ? createPostgresVoiceSecurityBackend() : createInMemoryVoiceSecurityBackend();
    }
    return defaultVoiceSecurityBackend;
}

function getDefaultService() {
    if (!defaultVoiceSecurityService) {
        defaultVoiceSecurityService = createVoiceSecurityService(getDefaultBackend());
    }
    return defaultVoiceSecurityService;
}

export async function startGuardedVoiceCallSessionWithService(service: VoiceSecurityService, input: StartGuardedVoiceCallInput): Promise<StartGuardedVoiceCallResult> {
    const startedAt = input.startedAt ?? input.now ?? new Date();
    const guard = await service.call_guard({
        ...input,
        reserveConcurrency: true,
        now: input.now ?? startedAt,
    });

    if (guard.outcome === "REJECT") return guard;

    if (!guard.reservationId) {
        return rejectResult({
            tenantId: guard.tenantId,
            partnerId: guard.partnerId,
            code: "tenant_concurrency_exceeded",
            reason: "Call reservation was not created.",
        });
    }

    const callId = input.callId?.trim() || `call_${crypto.randomUUID()}`;
    const providerCallId = input.providerCallId?.trim() || null;
    const confirmed = await service.confirmGuardedCallStart({
        reservationId: guard.reservationId,
        callId,
        providerCallId,
        startedAt,
    });

    if (!confirmed.ok) {
        await service.releaseGuardedCallReservation({
            reservationId: guard.reservationId,
            releasedAt: startedAt,
            reason: "start_confirmation_failed",
        });
        return rejectResult({
            tenantId: guard.tenantId,
            partnerId: guard.partnerId,
            code: "tenant_concurrency_exceeded",
            reason: "Guarded call session could not be started.",
        });
    }

    return {
        ...guard,
        callId,
        providerCallId,
        status: "active",
        startedAt: nowIso(startedAt),
    };
}

export async function startGuardedVoiceCallSession(input: StartGuardedVoiceCallInput): Promise<StartGuardedVoiceCallResult> {
    return startGuardedVoiceCallSessionWithService(getDefaultService(), input);
}

export async function call_guard(input: CallGuardInput) {
    return getDefaultService().call_guard(input);
}

export async function confirmGuardedCallStart(input: ConfirmGuardedCallStartInput) {
    return getDefaultService().confirmGuardedCallStart(input);
}

export async function releaseGuardedCallReservation(input: ReleaseGuardedCallReservationInput) {
    return getDefaultService().releaseGuardedCallReservation(input);
}

export async function endGuardedCall(input: EndGuardedCallInput) {
    return getDefaultService().endGuardedCall(input);
}
