import { NextResponse } from "next/server";
import nodemailer from "nodemailer";
import { z } from "zod";
import { buildResponsiveHtmlDocument } from "@/lib/email-utils";
import { getWhiteLabelBranding } from "@/lib/white-label/branding";
import { captureMessage } from "@/lib/monitoring";
import {
    beginIdempotency,
    completeIdempotency,
    enforceMultiLevelRateLimit,
    sanitizeHtmlEmailInput,
    sanitizeUnknown,
    sanitizeTextInput,
    sha256Hex,
    verifyStripeWebhookSignature,
} from "@/server/api-security";
import {
    authMeFromRequest,
    authTokenFromRequest,
    buildSessionCookie,
    clearSessionCookie,
    clientIpFromRequest,
    consumeAuthRateLimit,
    createSessionForUser,
    listUserSessions,
    logoutAllSessionsForUser,
    logoutSession,
    registerUser,
    revokeUserSessionByHandle,
    sessionHandleForSessionId,
    userAgentFromRequest,
    verifyPasswordLoginAttempt,
} from "@/server/auth-core";
import { getSql, isDatabaseConfigured } from "@/server/db";
import { disableTotpMfa, startTotpEnrollment, verifyMfaForLogin, verifyTotpEnrollment } from "@/server/mfa";
import { getPasskeyAuthenticationOptions, getPasskeyRegistrationOptions, verifyPasskeyAuthentication, verifyPasskeyRegistration } from "@/server/passkeys";
import {
    assignPartnerAdmin,
    assignTenantUserRole,
    getAuthzContextForUser,
    hasPermission,
    hasPermissionInPartner,
    hasPermissionInTenant,
    listPartners,
    listPartnerTenants,
    requireTenantAccess,
    upsertPartner,
    upsertTenant,
    type RoleName,
} from "@/server/rbac";
import { call_guard, startGuardedVoiceCallSession } from "@/server/voice-security";

type RouteContext = { params: Promise<{ path?: string[] }> };

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function json(data: unknown, init?: { status?: number; headers?: Record<string, string> }) {
    return NextResponse.json(data, {
        status: init?.status ?? 200,
        headers: { "cache-control": "no-store", ...(init?.headers ?? {}) },
    });
}

function noContent(init?: { status?: number; headers?: Record<string, string> }) {
    return new NextResponse(null, {
        status: init?.status ?? 204,
        headers: { "cache-control": "no-store", ...(init?.headers ?? {}) },
    });
}

function nowIso() {
    return new Date().toISOString();
}

function statusForCallGuardCode(code: string) {
    if (code === "invalid_tenant_id" || code === "invalid_partner_id") return 400;
    if (code === "tenant_not_found" || code === "partner_not_found") return 404;
    if (
        code === "tenant_rate_limited" ||
        code === "partner_rate_limited" ||
        code === "user_rate_limited" ||
        code === "ip_rate_limited" ||
        code === "temporary_block" ||
        code === "rapid_attempts_detected" ||
        code === "unusual_spike_detected"
    ) return 429;
    if (code === "tenant_concurrency_exceeded" || code === "partner_concurrency_exceeded") return 409;
    return 403;
}

async function readJsonBody(request: Request) {
    const maxBytesRaw = Number(process.env.API_MAX_JSON_BODY_BYTES ?? 1_048_576);
    const maxBytes = Number.isFinite(maxBytesRaw) && maxBytesRaw > 0 ? Math.floor(maxBytesRaw) : 1_048_576;
    try {
        const buf = new Uint8Array(await request.arrayBuffer());
        if (buf.byteLength > maxBytes) return undefined;
        const text = new TextDecoder().decode(buf);
        if (!text.trim()) return {};
        const parsed = JSON.parse(text) as unknown;
        return sanitizeUnknown(parsed);
    } catch {
        return undefined;
    }
}

function isHttps(request: Request) {
    const forwarded = request.headers.get("x-forwarded-proto");
    if (forwarded) return forwarded.split(",")[0]!.trim().toLowerCase() === "https";
    return request.url.startsWith("https:");
}

function maskSessionId(sessionId: string) {
    const s = String(sessionId ?? "");
    if (s.length <= 12) return s.replace(/.(?=.{4})/g, "*");
    return `${s.slice(0, 6)}…${s.slice(-4)}`;
}

function devSessionItemFromRequest(input: { request: Request; sessionId: string }) {
    const now = new Date().toISOString();
    return {
        session_id: maskSessionId(input.sessionId),
        session_handle: sessionHandleForSessionId(input.sessionId),
        ip_address: clientIpFromRequest(input.request) || null,
        user_agent: userAgentFromRequest(input.request) || null,
        created_at: now,
        last_activity_at: now,
        current: true,
    };
}

type CachedAuth = Awaited<ReturnType<typeof authMeFromRequest>>;

async function requireAuthContext(request: Request, options?: { cachedAuth?: CachedAuth | null; rotate?: boolean }) {
    const auth = options?.cachedAuth ?? (await authMeFromRequest(request, { rotate: options?.rotate }));
    if (!auth) return { ok: false as const, res: json({ detail: "Unauthorized" }, { status: 401 }) };
    const ctx = await getAuthzContextForUser(auth.me.id);
    return { ok: true as const, me: auth.me, ctx, sessionId: auth.sessionId };
}

type RequestAuth = Awaited<ReturnType<typeof requireAuthContext>> & { ok: true };

type TenantScope = {
    tenantIdFromPath: string;
    partnerIdFromPath?: string;
};

function extractTenantScope(path: string): TenantScope | null {
    const wl = path.match(/^\/white-label\/partners\/([^/]+)\/tenants\/([^/]+)(?:\/|$)/);
    if (wl) {
        const partnerIdFromPath = normalizePartnerId(sanitizeTextInput(decodeURIComponent(wl[1] ?? ""), { maxLen: 120 }));
        const tenantIdFromPath = sanitizeTextInput(decodeURIComponent(wl[2] ?? ""), { maxLen: 120 });
        if (partnerIdFromPath && tenantIdFromPath) return { tenantIdFromPath, partnerIdFromPath };
        return null;
    }
    const m = path.match(/^\/tenants\/([^/]+)(?:\/|$)/);
    if (m) {
        const tenantIdFromPath = sanitizeTextInput(decodeURIComponent(m[1] ?? ""), { maxLen: 120 });
        if (tenantIdFromPath) return { tenantIdFromPath };
        return null;
    }
    return null;
}

function extractPartnerFromPath(path: string) {
    const m = path.match(/^\/partners\/([^/]+)(?:\/|$)/);
    if (!m) return null;
    const partnerId = normalizePartnerId(sanitizeTextInput(decodeURIComponent(m[1] ?? ""), { maxLen: 120 }));
    return partnerId || null;
}

async function requireRoleOr403(input: { auth: RequestAuth; roles: RoleName[]; scope?: { partnerId?: string; tenantId?: string } }) {
    const { me, ctx } = input.auth;
    if (ctx.platformRole === "platform_admin" || me.role === "platform_admin") return null;

    const want = new Set(input.roles);
    if (want.has(me.role as RoleName)) {
        if (input.scope?.partnerId && typeof me.partner_id === "string" && me.partner_id.trim()) {
            if (me.partner_id.trim().toLowerCase() !== input.scope.partnerId.trim().toLowerCase()) {
                captureMessage("role_scope_denied", { user_id: ctx.userId, role: me.role, partner_id: input.scope.partnerId });
                return json({ detail: "Forbidden" }, { status: 403 });
            }
        }
        if (input.scope?.tenantId && typeof me.tenant_id === "string" && me.tenant_id.trim()) {
            if (me.tenant_id.trim() !== input.scope.tenantId.trim()) {
                captureMessage("role_scope_denied", { user_id: ctx.userId, role: me.role, tenant_id: input.scope.tenantId });
                return json({ detail: "Forbidden" }, { status: 403 });
            }
        }
        return null;
    }

    if (input.scope?.tenantId) {
        const tenantId = input.scope.tenantId;
        const match = ctx.tenantRoles.find((t) => t.tenantId === tenantId && want.has(t.role));
        if (match) return null;
    }

    if (input.scope?.partnerId) {
        const partnerId = input.scope.partnerId;
        const match = ctx.partnerRoles.find((p) => p.partnerId === partnerId && want.has(p.role));
        if (match) return null;
    }

    captureMessage("role_denied", { user_id: ctx.userId, roles: input.roles.join(",") });
    return json({ detail: "Forbidden" }, { status: 403 });
}

async function requirePermissionOr403(input: { auth: RequestAuth; permission: string; scope?: { partnerId?: string; tenantId?: string } }) {
    const { ctx } = input.auth;
    const permission = input.permission;
    const tenantId = input.scope?.tenantId;
    const partnerId = input.scope?.partnerId;

    const allowed = tenantId
        ? await hasPermissionInTenant({ ctx, tenantId, permission })
        : partnerId
          ? await hasPermissionInPartner({ ctx, partnerId, permission })
          : hasPermission({ ctx, permission });

    if (allowed) return null;
    captureMessage("permission_denied", { user_id: ctx.userId, permission, partner_id: partnerId, tenant_id: tenantId });
    return json({ detail: "Forbidden" }, { status: 403 });
}

async function enforceTenantIsolationIfScoped(input: { request: Request; path: string; cachedAuth?: CachedAuth | null }) {
    const scope = extractTenantScope(input.path);
    if (!scope) return { ok: true as const, auth: null, tenant: null, scope: null };

    const auth = await requireAuthContext(input.request, { cachedAuth: input.cachedAuth });
    if (!auth.ok) return { ok: false as const, res: auth.res };

    if (!isDatabaseConfigured()) {
        const partnerIdFromPath = scope.partnerIdFromPath ?? null;
        if (auth.me.role === "platform_admin") return { ok: true as const, auth, tenant: null, scope };
        if (auth.me.role === "partner_admin" && partnerIdFromPath && auth.me.partner_id?.toLowerCase() === partnerIdFromPath.toLowerCase()) {
            return { ok: true as const, auth, tenant: null, scope };
        }
        return { ok: false as const, res: json({ detail: "Forbidden" }, { status: 403 }) };
    }

    const access = await requireTenantAccess({ ctx: auth.ctx, tenantId: scope.tenantIdFromPath });
    if (!access.ok) {
        const status = access.status;
        if (status === 400) return { ok: false as const, res: json({ detail: "Invalid tenant_id" }, { status: 400 }) };
        if (status === 404) return { ok: false as const, res: json({ detail: "Tenant not found" }, { status: 404 }) };
        if (status === 403 && access.code === "tenant_suspended") return { ok: false as const, res: json({ detail: "Tenant suspended" }, { status: 403 }) };
        return { ok: false as const, res: json({ detail: "Forbidden" }, { status: 403 }) };
    }

    if (scope.partnerIdFromPath && access.tenant.partner_id !== scope.partnerIdFromPath) {
        return { ok: false as const, res: json({ detail: "Tenant not found" }, { status: 404 }) };
    }

    return { ok: true as const, auth, tenant: access.tenant, scope };
}

type PartnerRecord = {
    partner_id: string;
    display_name: string;
    allow_transfer: boolean;
    created_at: string;
    admin_email: string;
    admin_token: string;
};

const partnersStore = new Map<string, PartnerRecord>();
const inflightByPartner = new Map<string, number>();

function normalizePartnerId(raw: string) {
    return raw.trim().toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-").replace(/^-+|-+$/g, "");
}

function ensureSeedPartners() {
    if (partnersStore.size > 0) return;
    const created_at = nowIso();
    for (const p of [
        { partner_id: "acme", display_name: "Acme", allow_transfer: true },
        { partner_id: "zen", display_name: "Zen", allow_transfer: false },
    ]) {
        partnersStore.set(p.partner_id, {
            partner_id: p.partner_id,
            display_name: p.display_name,
            allow_transfer: p.allow_transfer,
            created_at,
            admin_email: `partner-${p.partner_id}@example.com`,
            admin_token: `partner-${p.partner_id}-token`,
        });
    }
}

function partnerConcurrencyLimit(partnerId: string) {
    const key = normalizePartnerId(partnerId);
    if (key === "acme") return 10;
    if (key === "zen") return 8;
    return 5;
}

function brandingLogoUrl(branding: NonNullable<ReturnType<typeof getWhiteLabelBranding>>) {
    const base = branding.logo.src;
    const joiner = base.includes("?") ? "&" : "?";
    return `${base}${joiner}wl=${encodeURIComponent(branding.partnerId)}&v=${encodeURIComponent(branding.version)}`;
}

type EmailTemplate = { id: string; name: string; html: string; locked?: boolean; thumbnailUrl?: string; updatedAt?: string };

function emailTemplates(input?: { branding?: NonNullable<ReturnType<typeof getWhiteLabelBranding>> | null }): EmailTemplate[] {
    const branding = input?.branding ?? null;
    const header = branding
        ? `<div style="padding: 0 0 12px; margin: 0 0 16px; border-bottom: 1px solid ${branding.colors.secondary}; display: flex; align-items: center; gap: 10px;">
                <img src="${brandingLogoUrl(branding)}" alt="${branding.logo.alt}" width="${branding.logo.width}" height="${branding.logo.height}" style="display:block;" />
                <div style="font-size: 14px; font-weight: 600; color: ${branding.colors.primary};">${branding.displayName}</div>
           </div>`
        : "";
    return [
        {
            id: "tpl-basic",
            name: "Basic",
            html: buildResponsiveHtmlDocument(
                `<div style="font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; color: #111827;">
                    ${header}
                    <h1 style="margin: 0 0 12px; font-size: 20px; line-height: 28px;">Hello</h1>
                    <p style="margin: 0 0 12px; font-size: 14px; line-height: 22px;">This is a test email from Talk-Lee.</p>
                    <p style="margin: 0; font-size: 12px; line-height: 18px; color: #6b7280;">If you did not expect this message, you can ignore it.</p>
                </div>`
            ),
            locked: false,
            updatedAt: nowIso(),
        },
        {
            id: "tpl-reminder",
            name: "Reminder",
            html: buildResponsiveHtmlDocument(
                `<div style="font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; color: #111827;">
                    ${header}
                    <h1 style="margin: 0 0 12px; font-size: 20px; line-height: 28px;">Reminder</h1>
                    <p style="margin: 0 0 12px; font-size: 14px; line-height: 22px;">You have an upcoming item.</p>
                    <div style="margin: 16px 0; padding: 12px 14px; border: 1px solid #e5e7eb; border-radius: 12px; background: #f9fafb;">
                        <div style="font-size: 12px; color: #6b7280; margin-bottom: 6px;">Details</div>
                        <div style="font-size: 14px; color: #111827;">Scheduled reminder</div>
                    </div>
                </div>`
            ),
            locked: false,
            updatedAt: nowIso(),
        },
    ];
}

function requireEnv(name: string) {
    const v = process.env[name];
    if (!v || !String(v).trim()) throw new Error(`Missing environment variable: ${name}`);
    return String(v).trim();
}

function optionalEnv(name: string) {
    const v = process.env[name];
    if (!v || !String(v).trim()) return undefined;
    return String(v).trim();
}

function parseBoolEnv(name: string, fallback: boolean) {
    const raw = optionalEnv(name);
    if (raw === undefined) return fallback;
    if (raw === "1" || /^true$/i.test(raw)) return true;
    if (raw === "0" || /^false$/i.test(raw)) return false;
    throw new Error(`Invalid boolean for ${name}: ${raw}`);
}

let cachedTransport: nodemailer.Transporter | undefined;
let cachedVerify: Promise<void> | undefined;

function createEmailTransport() {
    if (cachedTransport) return cachedTransport;
    const transportMode = optionalEnv("SMTP_TRANSPORT")?.toLowerCase();
    const smtpUrl = optionalEnv("SMTP_URL");
    const smtpHost = optionalEnv("SMTP_HOST");
    if (transportMode === "stream" || (!transportMode && process.env.NODE_ENV !== "production" && !smtpUrl && !smtpHost)) {
        cachedTransport = nodemailer.createTransport({
            streamTransport: true,
            newline: "unix",
            buffer: true,
        });
        cachedVerify = Promise.resolve();
        return cachedTransport;
    }
    if (smtpUrl) {
        cachedTransport = nodemailer.createTransport(smtpUrl);
        return cachedTransport;
    }

    const host = requireEnv("SMTP_HOST");
    const portRaw = optionalEnv("SMTP_PORT");
    const port = portRaw ? Number(portRaw) : 587;
    if (!Number.isFinite(port) || port <= 0) throw new Error(`Invalid SMTP_PORT: ${portRaw ?? ""}`);

    const secure = parseBoolEnv("SMTP_SECURE", port === 465);

    const user = optionalEnv("SMTP_USER");
    const pass = optionalEnv("SMTP_PASS");
    if (user && !pass) throw new Error("Missing SMTP_PASS");
    if (!user && pass) throw new Error("Missing SMTP_USER");

    const rejectUnauthorized = parseBoolEnv("SMTP_TLS_REJECT_UNAUTHORIZED", true);

    cachedTransport = nodemailer.createTransport({
        host,
        port,
        secure,
        auth: user ? { user, pass: pass! } : undefined,
        tls: { rejectUnauthorized },
    });
    return cachedTransport;
}

function getConfiguredFrom() {
    const from = optionalEnv("EMAIL_FROM");
    if (!from) {
        const transportMode = optionalEnv("SMTP_TRANSPORT")?.toLowerCase();
        if (process.env.NODE_ENV !== "production" || transportMode === "stream") return "Talk-Lee <noreply@example.com>";
        throw new Error("Missing environment variable: EMAIL_FROM");
    }
    if (!/@/.test(from)) throw new Error("EMAIL_FROM must include an email address (e.g. \"Talk-Lee <noreply@yourdomain.com>\")");
    return from;
}

function inferSubject(input: { subject?: string; templateName?: string }) {
    const s = input.subject?.trim();
    if (s) return s;
    if (input.templateName) return input.templateName;
    return "Talk-Lee";
}

function htmlToText(html: string) {
    return html
        .replace(/<style[\s\S]*?<\/style>/gi, " ")
        .replace(/<script[\s\S]*?<\/script>/gi, " ")
        .replace(/<br\s*\/?>/gi, "\n")
        .replace(/<\/p\s*>/gi, "\n\n")
        .replace(/<\/div\s*>/gi, "\n")
        .replace(/<[^>]+>/g, " ")
        .replace(/&nbsp;/gi, " ")
        .replace(/&amp;/gi, "&")
        .replace(/&lt;/gi, "<")
        .replace(/&gt;/gi, ">")
        .replace(/&quot;/gi, '"')
        .replace(/&#39;/gi, "'")
        .replace(/[ \t]+\n/g, "\n")
        .replace(/\n{3,}/g, "\n\n")
        .trim();
}

async function sendEmail(input: { to: string[]; subject?: string; html: string }) {
    const from = getConfiguredFrom();
    const replyTo = optionalEnv("EMAIL_REPLY_TO");
    const listUnsubscribe = optionalEnv("EMAIL_LIST_UNSUBSCRIBE");

    const transport = createEmailTransport();
    if (!cachedVerify) {
        cachedVerify = transport.verify().then(() => undefined);
        cachedVerify.catch(() => {
            cachedVerify = undefined;
            cachedTransport = undefined;
        });
    }
    await cachedVerify;

    const html = buildResponsiveHtmlDocument(input.html);
    const info = await transport.sendMail({
        from,
        to: input.to.join(", "),
        subject: inferSubject({ subject: input.subject }),
        html,
        text: htmlToText(html),
        ...(replyTo ? { replyTo } : {}),
        headers: {
            ...(listUnsubscribe ? { "List-Unsubscribe": listUnsubscribe } : {}),
        },
    });

    const accepted = Array.isArray(info.accepted) ? info.accepted.length : 0;
    const rejected = Array.isArray(info.rejected) ? info.rejected.length : 0;
    const status = accepted > 0 && rejected === 0 ? "sent" : accepted > 0 ? "partial" : "failed";

    return { messageId: info.messageId, status, accepted: info.accepted, rejected: info.rejected, response: info.response };
}

type AgentSettings = {
    systemPrompt: string;
    greetingMessage: string;
    transferEnabled: boolean;
    updatedAt: string;
};

const AgentSettingsInputSchema = z
    .object({
        systemPrompt: z.string(),
        greetingMessage: z.string(),
        transferEnabled: z.boolean(),
    })
    .strict();

const agentSettingsByTenant = new Map<string, AgentSettings>();

const RegisterInputSchema = z
    .object({
        email: z.string().email(),
        password: z.string().min(1),
        username: z.string().min(3).max(64).optional(),
        name: z.string().min(1).max(120).optional(),
        business_name: z.string().min(1).max(160).optional(),
    })
    .strict();

const LoginInputSchema = z
    .object({
        email: z.string().email().optional(),
        identifier: z.string().min(1).optional(),
        password: z.string().min(1),
        totp_code: z.string().min(1).optional(),
        recovery_code: z.string().min(1).optional(),
    })
    .strict();

const MfaEnrollVerifySchema = z
    .object({
        code: z.string().min(1),
    })
    .strict();

const MfaDisableSchema = z
    .object({
        password: z.string().min(1).optional(),
        totp_code: z.string().min(1).optional(),
    })
    .strict();

const PasskeyRegistrationOptionsSchema = z.object({}).strict();

const PasskeyRegistrationVerifySchema = z
    .object({
        attestation: z.unknown(),
        device_name: z.string().min(1).max(120).optional(),
    })
    .strict();

const PasskeyLoginOptionsSchema = z
    .object({
        identifier: z.string().min(1),
    })
    .strict();

const PasskeyLoginVerifySchema = z
    .object({
        assertion: z.unknown(),
        totp_code: z.string().min(1).optional(),
        recovery_code: z.string().min(1).optional(),
    })
    .strict();

const PartnerCreateSchema = z
    .object({
        partner_id: z.string().min(1).max(120),
        display_name: z.string().min(1).max(160),
        allow_transfer: z.boolean().optional(),
        admin_email: z.string().email(),
    })
    .strict();

const PartnerUpsertSchema = z
    .object({
        partner_id: z.string().min(1).max(120),
        display_name: z.string().min(1).max(160),
        allow_transfer: z.boolean().optional(),
    })
    .strict();

const TenantUpsertSchema = z
    .object({
        id: z.string().min(1).max(120).optional(),
        name: z.string().min(1).max(200),
        status: z.enum(["active", "suspended"]).optional(),
    })
    .strict();

const TenantAssignRoleSchema = z
    .object({
        user_id: z.string().min(1).max(120),
        role: z.enum(["tenant_admin", "user", "readonly"]).optional(),
    })
    .strict();

const EmailSendSchema = z
    .object({
        to: z.array(z.string().min(1)).optional(),
        recipients: z.array(z.string().min(1)).optional(),
        template_id: z.string().min(1).optional(),
        templateId: z.string().min(1).optional(),
        subject: z.string().min(1).max(240).optional(),
        html: z.string().min(1).max(200_000).optional(),
    })
    .strict();

const DevConnectorCreateSchema = z
    .object({
        name: z.string().min(1).max(160).optional(),
        type: z.string().min(1).max(120).optional(),
        config: z.record(z.unknown()).optional(),
    })
    .strict();

const DevCalendarEventCreateSchema = z
    .object({
        lead_id: z.string().min(1).max(120).optional(),
        lead_name: z.string().min(1).max(200).optional(),
        title: z.string().min(1).max(240).optional(),
        start_time: z.string().min(1).max(64).optional(),
        end_time: z.string().min(1).max(64).optional(),
        notes: z.string().max(10_000).optional(),
    })
    .strict();

const DevCalendarEventPatchSchema = z
    .object({
        title: z.string().min(1).max(240).optional(),
        start_time: z.string().min(1).max(64).optional(),
        end_time: z.string().min(1).max(64).optional(),
    })
    .strict();

const DevReminderCreateSchema = z
    .object({
        content: z.string().max(10_000).optional(),
        channel: z.enum(["email", "sms"]).optional(),
        scheduled_at: z.string().min(1).max(64).optional(),
        meeting_id: z.string().min(1).max(120).optional(),
        meeting_title: z.string().min(1).max(240).optional(),
        contact_id: z.string().min(1).max(120).optional(),
        contact_name: z.string().min(1).max(200).optional(),
        to_email: z.string().email().optional(),
        to_phone: z.string().min(1).max(40).optional(),
    })
    .strict();

const DevReminderPatchSchema = z
    .object({
        content: z.string().max(10_000).optional(),
        status: z.string().min(1).max(64).optional(),
        channel: z.string().min(1).max(64).optional(),
        scheduled_at: z.string().min(1).max(64).optional(),
    })
    .strict();

const VoiceFeatureSchema = z.enum(["voice", "premium", "transfer"]);

const VoiceCallGuardSchema = z
    .object({
        tenant_id: z.string().min(1).max(120).optional(),
        partner_id: z.string().min(1).max(120).optional(),
        requested_features: z.array(VoiceFeatureSchema).max(10).optional(),
        call_id: z.string().min(1).max(120).optional(),
        provider_call_id: z.string().min(1).max(120).optional(),
        allow_overage: z.boolean().optional(),
    })
    .strict();

const VoiceCallStartSchema = z
    .object({
        tenant_id: z.string().min(1).max(120).optional(),
        partner_id: z.string().min(1).max(120).optional(),
        requested_features: z.array(VoiceFeatureSchema).max(10).optional(),
        call_id: z.string().min(1).max(120).optional(),
        provider_call_id: z.string().min(1).max(120).optional(),
        allow_overage: z.boolean().optional(),
    })
    .strict();

function defaultAgentSettings(input: { partnerId: string; tenantId: string }): AgentSettings {
    const now = nowIso();
    const partnerKey = input.partnerId.trim().toLowerCase();
    const tenantKey = input.tenantId.trim().toLowerCase();

    if (partnerKey === "zen" || tenantKey.includes("salon")) {
        return {
            systemPrompt: "You are a friendly salon receptionist. Greet callers politely and assist with booking appointments.",
            greetingMessage: "Hello! Thank you for calling Zen Salon. How may I assist you today?",
            transferEnabled: false,
            updatedAt: now,
        };
    }

    return {
        systemPrompt: "You are a helpful voice assistant. Be concise, polite, and goal-oriented.",
        greetingMessage: "Hello! Thanks for calling. How may I help you today?",
        transferEnabled: false,
        updatedAt: now,
    };
}

function isSessionMutationRequest(method: string, path: string) {
    if (method === "POST" && path === "/auth/login") return true;
    if (method === "POST" && path === "/auth/logout") return true;
    if (method === "POST" && path === "/auth/logout_all") return true;
    if (method === "POST" && path === "/auth/passkeys/login/verify") return true;
    return false;
}

function isPublicApiPath(method: string, path: string) {
    if (method === "GET" && path === "/health") return true;
    if (method === "POST" && path === "/auth/register") return true;
    if (method === "POST" && path === "/auth/login") return true;
    if (method === "POST" && path === "/auth/passkeys/login/options") return true;
    if (method === "POST" && path === "/auth/passkeys/login/verify") return true;
    if (method === "POST" && path === "/billing/webhooks/stripe") return true;
    return false;
}

function rateLimitTierForPath(method: string, path: string) {
    if (method === "POST" && path === "/billing/webhooks/stripe") return "webhook" as const;
    if (path.startsWith("/auth/")) return "sensitive" as const;
    return "default" as const;
}

function shouldUseIdempotency(method: string, path: string) {
    if (method !== "POST" && method !== "PATCH" && method !== "DELETE") return false;
    if (path.startsWith("/auth/")) return false;
    if (path === "/billing/webhooks/stripe") return false;
    if (path === "/assistant/plan") return false;
    return true;
}

async function handle(request: Request, segments: string[]) {
    const method = request.method.toUpperCase();
    const path = `/${segments.join("/")}`;
    const token = authTokenFromRequest(request);
    const cachedAuth = token && !isSessionMutationRequest(method, path) ? await authMeFromRequest(request) : null;
    const useIdempotency = shouldUseIdempotency(method, path);
    if (useIdempotency) {
        let rawBytes = new Uint8Array();
        try {
            rawBytes = new Uint8Array(await request.clone().arrayBuffer());
        } catch {
            rawBytes = new Uint8Array();
        }
        const bodyHash = sha256Hex(rawBytes);
        const headerKey = request.headers.get("idempotency-key") ?? request.headers.get("Idempotency-Key");
        const userId = cachedAuth?.me?.id ?? null;
        const tenantFromPath = extractTenantScope(path)?.tenantIdFromPath ?? null;
        const tenantId = tenantFromPath ?? (typeof cachedAuth?.me?.tenant_id === "string" ? cachedAuth.me.tenant_id : "unknown");
        const computedKey = headerKey && headerKey.trim().length > 0 ? sanitizeTextInput(headerKey, { maxLen: 200 }) : `auto:${sha256Hex(`${method}:${path}:${userId ?? "anon"}:${tenantId}:${bodyHash}`)}`;
        const scope = tenantId !== "unknown" ? `tenant:${tenantId}` : userId ? `user:${userId}` : "global";

        const ipAddress = clientIpFromRequest(request) || null;
        const idem = await beginIdempotency({
            scope,
            idempotencyKey: computedKey,
            requestHash: bodyHash,
            method,
            path,
            userId,
            tenantId: tenantId === "unknown" ? null : tenantId,
            ipAddress,
        });
        if (!idem.ok) return json({ detail: "Conflict" }, { status: idem.status });
        if (idem.state === "replay" && idem.row) {
            const replayRes = (idem.row.response_status ?? 200) === 204
                ? noContent({ status: 204, headers: { "x-idempotent-replay": "1" } })
                : json(idem.row.response_body, { status: idem.row.response_status ?? 200, headers: { "x-idempotent-replay": "1" } });
            if (cachedAuth?.setCookie) replayRes.headers.append("set-cookie", cachedAuth.setCookie);
            return replayRes;
        }

        const res = await handleInner(request, segments, { cachedAuth });
        if (cachedAuth?.setCookie) res.headers.append("set-cookie", cachedAuth.setCookie);

        let responseBody: unknown = null;
        if (res.status !== 204) {
            try {
                responseBody = await res.clone().json();
            } catch {
                responseBody = null;
            }
        }
        await completeIdempotency({
            scope,
            idempotencyKey: computedKey,
            requestHash: bodyHash,
            responseStatus: res.status,
            responseHeaders: { "content-type": res.headers.get("content-type") ?? "application/json" },
            responseBody,
        });
        res.headers.set("x-idempotency-key", computedKey);
        return res;
    }

    const res = await handleInner(request, segments, { cachedAuth });
    if (cachedAuth?.setCookie) res.headers.append("set-cookie", cachedAuth.setCookie);
    return res;
}

async function handleInner(request: Request, segments: string[], state: { cachedAuth: CachedAuth | null }) {
    const method = request.method.toUpperCase();
    const path = `/${segments.join("/")}`;

    const publicPath = isPublicApiPath(method, path);
    const token = authTokenFromRequest(request);
    const shouldPreloadAuth = Boolean(token) && !isSessionMutationRequest(method, path);
    const auth = state.cachedAuth ?? (shouldPreloadAuth ? await authMeFromRequest(request) : null);
    if (!publicPath && !auth) return json({ detail: "Unauthorized" }, { status: 401 });

    const tenantEnforcement = await enforceTenantIsolationIfScoped({ request, path, cachedAuth: auth });
    if (!tenantEnforcement.ok) return tenantEnforcement.res;

    const tenantId = tenantEnforcement.tenant?.id ?? (typeof auth?.me.tenant_id === "string" ? auth.me.tenant_id : "unknown");
    const userId = auth?.me?.id ?? null;
    const tier = rateLimitTierForPath(method, path);
    const rate = await enforceMultiLevelRateLimit({
        request,
        tier,
        path,
        method,
        userId,
        tenantId,
        layers: tier === "webhook" ? { ip: true, user: false, tenant: false } : undefined,
    });
    if (!rate.ok) return json({ detail: "Too many requests" }, { status: 429, headers: rate.headers });

    const platformScoped = path === "/platform" || path.startsWith("/platform/");
    const partnerIdFromPath = extractPartnerFromPath(path);
    if (platformScoped || partnerIdFromPath) {
        const auth = await requireAuthContext(request, { cachedAuth: state.cachedAuth });
        if (!auth.ok) return auth.res;

        if (platformScoped) {
            const roleDenied = await requireRoleOr403({ auth, roles: ["platform_admin"] });
            if (roleDenied) return roleDenied;
        }

        if (partnerIdFromPath) {
            const roleDenied = await requireRoleOr403({ auth, roles: ["partner_admin"], scope: { partnerId: partnerIdFromPath } });
            if (roleDenied) return roleDenied;
        }
    }

    if (method === "GET" && path === "/health") {
        return json({ status: "ok" });
    }

    if (method === "POST" && path === "/billing/webhooks/stripe") {
        const secret = String(process.env.STRIPE_WEBHOOK_SECRET ?? "").trim();
        if (!secret) return json({ detail: "Service unavailable" }, { status: 503 });

        const sig = request.headers.get("stripe-signature") ?? "";
        const maxBytesRaw = Number(process.env.API_MAX_WEBHOOK_BODY_BYTES ?? 262_144);
        const maxBytes = Number.isFinite(maxBytesRaw) && maxBytesRaw > 0 ? Math.floor(maxBytesRaw) : 262_144;
        let raw: Uint8Array;
        try {
            raw = new Uint8Array(await request.arrayBuffer());
        } catch {
            return json({ detail: "Invalid request" }, { status: 400 });
        }
        if (raw.byteLength > maxBytes) return json({ detail: "Invalid request" }, { status: 400 });

        const verified = verifyStripeWebhookSignature({
            rawBody: raw,
            header: sig,
            secret,
            toleranceSeconds: Number(process.env.STRIPE_WEBHOOK_TOLERANCE_SECONDS ?? 300),
        });
        if (!verified.ok) {
            try {
                captureMessage("webhook_signature_invalid", { provider: "stripe", code: verified.code });
            } catch {
            }
            return json({ detail: "Unauthorized" }, { status: 401 });
        }

        let event: unknown;
        try {
            const text = new TextDecoder().decode(raw);
            event = sanitizeUnknown(JSON.parse(text));
        } catch {
            return json({ detail: "Invalid request" }, { status: 400 });
        }

        const StripeEventSchema = z
            .object({
                id: z.string().min(1).max(255),
                type: z.string().min(1).max(255),
                created: z.number().int().nonnegative().optional(),
                data: z
                    .object({
                        object: z.record(z.unknown()).optional(),
                    })
                    .optional(),
            })
            .strip();
        const parsed = StripeEventSchema.safeParse(event);
        if (!parsed.success) return json({ detail: "Invalid request" }, { status: 400 });

        const metadata =
            parsed.data.data?.object && typeof parsed.data.data.object === "object"
                ? ((parsed.data.data.object as Record<string, unknown>).metadata as unknown)
                : undefined;
        const tenantId =
            metadata && typeof metadata === "object" && metadata
                ? (metadata as Record<string, unknown>).tenant_id
                : undefined;
        const tenantScope = typeof tenantId === "string" && tenantId.trim().length > 0 ? `tenant:${tenantId.trim()}` : "tenant:unknown";
        const webhookTenantRate = await enforceMultiLevelRateLimit({
            request,
            tier: "webhook",
            path,
            method,
            userId: null,
            tenantId: typeof tenantId === "string" ? tenantId : null,
            layers: { ip: false, user: false, tenant: true },
        });
        if (!webhookTenantRate.ok) return json({ detail: "Too many requests" }, { status: 429, headers: webhookTenantRate.headers });

        const requestHash = sha256Hex(raw);
        const ipAddress = clientIpFromRequest(request) || null;
        const idem = await beginIdempotency({
            scope: tenantScope,
            idempotencyKey: `stripe_event:${parsed.data.id}`,
            requestHash,
            method,
            path,
            userId: null,
            tenantId: typeof tenantId === "string" ? tenantId : null,
            ipAddress,
        });
        if (!idem.ok) return json({ detail: "Conflict" }, { status: idem.status });
        if (idem.state === "replay" && idem.row) {
            return json(idem.row.response_body, {
                status: idem.row.response_status ?? 200,
                headers: { "x-idempotent-replay": "1" },
            });
        }

        const responseBody = { received: true };
        await completeIdempotency({
            scope: tenantScope,
            idempotencyKey: `stripe_event:${parsed.data.id}`,
            requestHash,
            responseStatus: 200,
            responseHeaders: { "content-type": "application/json" },
            responseBody,
        });
        return json(responseBody);
    }

    if (method === "GET" && (path === "/auth/me" || path === "/me")) {
        const auth = state.cachedAuth ?? (await authMeFromRequest(request));
        if (!auth) return json({ detail: "Unauthorized" }, { status: 401 });
        return json(auth.me);
    }

    if (method === "POST" && path === "/auth/register") {
        const body = await readJsonBody(request);
        const parsed = RegisterInputSchema.safeParse(body);
        if (!parsed.success) return json({ detail: "Invalid request body" }, { status: 400 });
        try {
            const res = await registerUser({
                email: parsed.data.email,
                password: parsed.data.password,
                username: parsed.data.username,
                name: parsed.data.name,
                businessName: parsed.data.business_name,
            });
            if (!res.ok && res.code === "weak_password") {
                return json({ detail: "Password does not meet requirements", failures: res.failures }, { status: 400 });
            }
            if (!res.ok && res.code === "conflict") {
                return json({ detail: "Email is already in use" }, { status: 409 });
            }
            if (!res.ok) return json({ detail: "Registration failed" }, { status: 400 });
            return json(res.user, { status: 201 });
        } catch {
            return json({ detail: "Registration failed" }, { status: 500 });
        }
    }

    if (method === "POST" && path === "/auth/login") {
        const body = await readJsonBody(request);
        const parsed = LoginInputSchema.safeParse(body);
        if (!parsed.success) return json({ detail: "Invalid request body" }, { status: 400 });

        const identifier = (parsed.data.email ?? parsed.data.identifier ?? "").trim();
        if (!identifier) return json({ detail: "Invalid credentials" }, { status: 401 });

        const ipAddress = clientIpFromRequest(request);
        const userAgent = userAgentFromRequest(request);

        const result = await verifyPasswordLoginAttempt({
            identifier,
            password: parsed.data.password,
            ipAddress,
            userAgent,
        });

        if (!result.ok && result.code === "rate_limited") {
            return json(
                { detail: "Too many login attempts" },
                { status: 429, headers: { "retry-after": String(result.retryAfterSeconds) } }
            );
        }
        if (!result.ok && result.code === "db_unavailable") {
            return json({ detail: "Service unavailable" }, { status: 503 });
        }
        if (!result.ok) {
            return json({ detail: "Invalid credentials" }, { status: 401 });
        }

        const mfa = await verifyMfaForLogin({
            userId: result.user.id,
            ipAddress,
            totpCode: parsed.data.totp_code,
            recoveryCode: parsed.data.recovery_code,
        });
        if (!mfa.ok && mfa.code === "rate_limited") {
            return json(
                { detail: "Too many verification attempts" },
                { status: 429, headers: { "retry-after": String(mfa.retryAfterSeconds) } }
            );
        }
        if (!mfa.ok && mfa.code === "mfa_required") {
            return json({ detail: "MFA required", mfa_required: true }, { status: 401 });
        }
        if (!mfa.ok && (mfa.code === "db_unavailable" || mfa.code === "service_unavailable")) {
            return json({ detail: "Service unavailable" }, { status: 503 });
        }
        if (!mfa.ok) {
            return json({ detail: "Invalid credentials" }, { status: 401 });
        }

        const existingToken = authTokenFromRequest(request);
        try {
            await logoutSession(existingToken);
        } catch {
        }

        const session = await createSessionForUser({
            userId: result.user.id,
            ipAddress,
            userAgent,
            mfaVerified: mfa.required,
        });
        if (!session.ok && session.code === "mfa_required") {
            return json({ detail: "MFA required", mfa_required: true }, { status: 401 });
        }
        if (!session.ok && session.code === "rate_limited") {
            return json(
                { detail: "Too many sessions created" },
                { status: 429, headers: { "retry-after": String(session.retryAfterSeconds) } }
            );
        }
        if (!session.ok && session.code === "db_unavailable") {
            return json({ detail: "Service unavailable" }, { status: 503 });
        }
        if (!session.ok) {
            return json({ detail: "Login failed" }, { status: 500 });
        }

        const cookie = buildSessionCookie({ sessionId: session.session.sessionId, expiresAt: session.session.expiresAt, secure: isHttps(request) });
        return json(
            { id: result.user.id, email: result.user.email, role: result.user.role, message: "ok" },
            { headers: { "set-cookie": cookie } }
        );
    }

    if (method === "POST" && path === "/auth/mfa/enroll/start") {
        const auth = state.cachedAuth ?? (await authMeFromRequest(request));
        if (!auth) return json({ detail: "Unauthorized" }, { status: 401 });
        const me = auth.me;

        const res = await startTotpEnrollment({ userId: me.id, email: me.email });
        if (!res.ok && res.code === "already_enabled") return json({ detail: "MFA already enabled" }, { status: 409 });
        if (!res.ok && (res.code === "db_unavailable" || res.code === "service_unavailable")) return json({ detail: "Service unavailable" }, { status: 503 });
        if (!res.ok) return json({ detail: "MFA enrollment failed" }, { status: 400 });
        return json({ otpauth_uri: res.otpauthUri, secret_base32: res.secretBase32 });
    }

    if (method === "POST" && path === "/auth/mfa/enroll/verify") {
        const auth = state.cachedAuth ?? (await authMeFromRequest(request));
        if (!auth) return json({ detail: "Unauthorized" }, { status: 401 });
        const me = auth.me;

        const body = await readJsonBody(request);
        const parsed = MfaEnrollVerifySchema.safeParse(body);
        if (!parsed.success) return json({ detail: "Invalid request body" }, { status: 400 });

        const ipAddress = clientIpFromRequest(request);
        const res = await verifyTotpEnrollment({ userId: me.id, ipAddress, code: parsed.data.code });
        if (!res.ok && res.code === "rate_limited") {
            return json(
                { detail: "Too many verification attempts" },
                { status: 429, headers: { "retry-after": String(res.retryAfterSeconds) } }
            );
        }
        if (!res.ok && (res.code === "db_unavailable" || res.code === "service_unavailable")) return json({ detail: "Service unavailable" }, { status: 503 });
        if (!res.ok) return json({ detail: "Invalid credentials" }, { status: 401 });
        if (res.alreadyEnabled) return json({ message: "ok" });
        return json({ recovery_codes: res.recoveryCodes });
    }

    if (method === "POST" && path === "/auth/mfa/disable") {
        const auth = state.cachedAuth ?? (await authMeFromRequest(request, { rotate: false }));
        if (!auth) return json({ detail: "Unauthorized" }, { status: 401 });
        const me = auth.me;

        const body = await readJsonBody(request);
        const parsed = MfaDisableSchema.safeParse(body);
        if (!parsed.success) return json({ detail: "Invalid request body" }, { status: 400 });

        const ipAddress = clientIpFromRequest(request);
        const res = await disableTotpMfa({
            userId: me.id,
            ipAddress,
            password: parsed.data.password,
            totpCode: parsed.data.totp_code,
        });
        if (!res.ok && res.code === "rate_limited") {
            return json(
                { detail: "Too many verification attempts" },
                { status: 429, headers: { "retry-after": String(res.retryAfterSeconds) } }
            );
        }
        if (!res.ok && (res.code === "db_unavailable" || res.code === "service_unavailable")) return json({ detail: "Service unavailable" }, { status: 503 });
        if (!res.ok && res.code === "verification_required") return json({ detail: "Password or TOTP code required" }, { status: 400 });
        if (!res.ok) return json({ detail: "Invalid credentials" }, { status: 401 });
        return json({ message: "ok" });
    }

    if (method === "POST" && path === "/auth/logout") {
        const token = authTokenFromRequest(request);
        try {
            await logoutSession(token);
        } catch {
        }
        const cookie = clearSessionCookie({ secure: isHttps(request) });
        return noContent({ headers: { "set-cookie": cookie } });
    }

    if (method === "POST" && path === "/auth/logout_all") {
        const auth = await requireAuthContext(request, { rotate: false });
        if (!auth.ok) return auth.res;

        if (!isDatabaseConfigured()) {
            const cookie = clearSessionCookie({ secure: isHttps(request) });
            return noContent({ headers: { "set-cookie": cookie } });
        }

        const ipAddress = clientIpFromRequest(request);
        const userLimit = Number(process.env.AUTH_LOGOUT_ALL_LIMIT_PER_USER_PER_MINUTE ?? 5);
        const ipLimit = Number(process.env.AUTH_LOGOUT_ALL_LIMIT_PER_IP_PER_MINUTE ?? 20);
        const perUser = await consumeAuthRateLimit({
            bucket: `logout_all:user:${auth.me.id}`,
            limit: Number.isFinite(userLimit) && userLimit > 0 ? Math.floor(userLimit) : 5,
            windowSeconds: 60,
        });
        const perIp = await consumeAuthRateLimit({
            bucket: `logout_all:ip:${ipAddress || "unknown"}`,
            limit: Number.isFinite(ipLimit) && ipLimit > 0 ? Math.floor(ipLimit) : 20,
            windowSeconds: 60,
        });
        if (!perUser.allowed || !perIp.allowed) {
            const retryAfterSeconds = Math.max(perUser.retryAfterSeconds, perIp.retryAfterSeconds);
            return json(
                { detail: "Too many requests" },
                { status: 429, headers: { "retry-after": String(retryAfterSeconds) } }
            );
        }

        await logoutAllSessionsForUser({ userId: auth.me.id });
        const cookie = clearSessionCookie({ secure: isHttps(request) });
        return noContent({ headers: { "set-cookie": cookie } });
    }

    if (method === "GET" && path === "/auth/sessions") {
        const auth = await requireAuthContext(request, { cachedAuth: state.cachedAuth });
        if (!auth.ok) return auth.res;

        if (!isDatabaseConfigured()) {
            return json({ items: [devSessionItemFromRequest({ request, sessionId: auth.sessionId })] });
        }

        const res = await listUserSessions({ userId: auth.me.id });
        if (!res.ok && res.code === "db_unavailable") return json({ detail: "Service unavailable" }, { status: 503 });
        if (!res.ok) return json({ detail: "Service unavailable" }, { status: 503 });

        const now = Date.now();
        const idleSecondsRaw = Number(process.env.AUTH_SESSION_IDLE_TIMEOUT_SECONDS ?? 30 * 60);
        const idleSeconds = Number.isFinite(idleSecondsRaw) && idleSecondsRaw > 0 ? Math.floor(idleSecondsRaw) : 30 * 60;
        const idleCutoff = now - idleSeconds * 1000;

        const active = res.sessions
            .filter((s) => !s.revoked && !s.revoked_at)
            .filter((s) => s.expires_at.getTime() > now)
            .filter((s) => (s.last_activity_at?.getTime?.() ?? s.created_at.getTime()) > idleCutoff);

        return json({
            items: active.map((s) => ({
                session_id: maskSessionId(s.session_id),
                session_handle: sessionHandleForSessionId(s.session_id),
                ip_address: s.ip_address ?? null,
                user_agent: s.user_agent ?? null,
                created_at: s.created_at.toISOString(),
                last_activity_at: s.last_activity_at.toISOString(),
                current: s.session_id === auth.sessionId,
            })),
        });
    }

    if (method === "POST" && path === "/auth/sessions/revoke") {
        const auth = await requireAuthContext(request, { rotate: false });
        if (!auth.ok) return auth.res;

        const SessionRevokeSchema = z.object({ session_handle: z.string().min(1).max(255) }).strict();
        const body = await readJsonBody(request);
        const parsed = SessionRevokeSchema.safeParse(body);
        if (!parsed.success) return json({ detail: "Invalid request body" }, { status: 400 });
        const sessionHandle = parsed.data.session_handle.trim();

        if (!isDatabaseConfigured()) {
            if (sessionHandle !== sessionHandleForSessionId(auth.sessionId)) return json({ detail: "Not found" }, { status: 404 });
            const cookie = clearSessionCookie({ secure: isHttps(request) });
            return noContent({ headers: { "set-cookie": cookie } });
        }

        const out = await revokeUserSessionByHandle({ userId: auth.me.id, sessionHandle, reason: "manual_revocation" });
        if (!out.ok && out.code === "db_unavailable") return json({ detail: "Service unavailable" }, { status: 503 });
        if (!out.ok && out.code === "invalid_request") return json({ detail: "Invalid request" }, { status: 400 });
        if (!out.ok && out.code === "not_found") return json({ detail: "Not found" }, { status: 404 });
        if (!out.ok) return json({ detail: "Service unavailable" }, { status: 503 });

        if (out.sessionId === auth.sessionId) {
            const cookie = clearSessionCookie({ secure: isHttps(request) });
            return noContent({ headers: { "set-cookie": cookie } });
        }
        return noContent();
    }

    if (method === "POST" && path === "/voice/calls/guard") {
        const auth = await requireAuthContext(request, { cachedAuth: state.cachedAuth });
        if (!auth.ok) return auth.res;

        const body = await readJsonBody(request);
        const parsed = VoiceCallGuardSchema.safeParse(body);
        if (!parsed.success) return json({ detail: "Invalid request body" }, { status: 400 });

        const tenantId = parsed.data.tenant_id?.trim() || auth.me.tenant_id?.trim() || "";
        const partnerId = parsed.data.partner_id?.trim() || auth.me.partner_id?.trim() || null;
        if (!tenantId) return json({ detail: "Missing tenant scope" }, { status: 400 });

        const result = await call_guard({
            tenantId,
            partnerId,
            userId: auth.me.id,
            ipAddress: clientIpFromRequest(request) || null,
            callId: parsed.data.call_id?.trim() || null,
            providerCallId: parsed.data.provider_call_id?.trim() || null,
            requestedFeatures: parsed.data.requested_features,
            allowOverage: Boolean(parsed.data.allow_overage),
            reserveConcurrency: true,
        });

        if (result.outcome === "REJECT") {
            return json(
                {
                    outcome: result.outcome,
                    tenant_id: result.tenantId,
                    partner_id: result.partnerId,
                    code: result.code,
                    reason: result.reason,
                    retry_after_seconds: result.retryAfterSeconds,
                    block_expires_at: result.blockExpiresAt,
                },
                { status: statusForCallGuardCode(result.code) }
            );
        }

        return json(
            {
                outcome: result.outcome,
                tenant_id: result.tenantId,
                partner_id: result.partnerId,
                reservation_id: result.reservationId,
                active_calls: result.activeCalls,
                overage: result.overage,
                allowed_features: result.allowedFeatures,
                requested_features: result.requestedFeatures,
                usage_account_id: result.usageAccountId,
                billing_account_id: result.billingAccountId,
            },
            { status: 200 }
        );
    }

    if (method === "POST" && path === "/voice/calls/start") {
        const auth = await requireAuthContext(request, { cachedAuth: state.cachedAuth });
        if (!auth.ok) return auth.res;

        const body = await readJsonBody(request);
        const parsed = VoiceCallStartSchema.safeParse(body);
        if (!parsed.success) return json({ detail: "Invalid request body" }, { status: 400 });

        const tenantId = parsed.data.tenant_id?.trim() || auth.me.tenant_id?.trim() || "";
        const partnerId = parsed.data.partner_id?.trim() || auth.me.partner_id?.trim() || null;
        if (!tenantId) return json({ detail: "Missing tenant scope" }, { status: 400 });

        const result = await startGuardedVoiceCallSession({
            tenantId,
            partnerId,
            userId: auth.me.id,
            ipAddress: clientIpFromRequest(request) || null,
            callId: parsed.data.call_id?.trim() || null,
            providerCallId: parsed.data.provider_call_id?.trim() || null,
            requestedFeatures: parsed.data.requested_features,
            allowOverage: Boolean(parsed.data.allow_overage),
        });

        if (result.outcome === "REJECT") {
            return json(
                {
                    outcome: result.outcome,
                    tenant_id: result.tenantId,
                    partner_id: result.partnerId,
                    code: result.code,
                    reason: result.reason,
                    retry_after_seconds: result.retryAfterSeconds,
                    block_expires_at: result.blockExpiresAt,
                },
                { status: statusForCallGuardCode(result.code) }
            );
        }

        return json(
            {
                outcome: result.outcome,
                tenant_id: result.tenantId,
                partner_id: result.partnerId,
                reservation_id: result.reservationId,
                call_id: result.callId,
                provider_call_id: result.providerCallId,
                status: result.status,
                started_at: result.startedAt,
                active_calls: result.activeCalls,
                overage: result.overage,
                allowed_features: result.allowedFeatures,
                requested_features: result.requestedFeatures,
                usage_account_id: result.usageAccountId,
                billing_account_id: result.billingAccountId,
            },
            { status: 201 }
        );
    }

    if (method === "POST" && path === "/auth/passkeys/registration/options") {
        const auth = state.cachedAuth ?? (await authMeFromRequest(request));
        if (!auth) return json({ detail: "Unauthorized" }, { status: 401 });
        const me = auth.me;

        const body = await readJsonBody(request);
        const parsed = PasskeyRegistrationOptionsSchema.safeParse(body);
        if (!parsed.success) return json({ detail: "Invalid request body" }, { status: 400 });

        const ipAddress = clientIpFromRequest(request);
        const userAgent = userAgentFromRequest(request);
        const res = await getPasskeyRegistrationOptions({
            request,
            userId: me.id,
            email: me.email,
            displayName: me.name ?? me.email,
            ipAddress,
            userAgent,
        });
        if (!res.ok && res.code === "rate_limited") {
            return json(
                { detail: "Too many registration attempts" },
                { status: 429, headers: { "retry-after": String(res.retryAfterSeconds) } }
            );
        }
        if (!res.ok && res.code === "db_unavailable") return json({ detail: "Service unavailable" }, { status: 503 });
        if (!res.ok) return json({ detail: "Passkey registration unavailable" }, { status: 503 });
        return json(res.options);
    }

    if (method === "POST" && path === "/auth/passkeys/registration/verify") {
        const auth = state.cachedAuth ?? (await authMeFromRequest(request));
        if (!auth) return json({ detail: "Unauthorized" }, { status: 401 });
        const me = auth.me;

        const body = await readJsonBody(request);
        const parsed = PasskeyRegistrationVerifySchema.safeParse(body);
        if (!parsed.success) return json({ detail: "Invalid request body" }, { status: 400 });

        const ipAddress = clientIpFromRequest(request);
        const userAgent = userAgentFromRequest(request);
        const res = await verifyPasskeyRegistration({
            request,
            userId: me.id,
            deviceName: parsed.data.device_name,
            ipAddress,
            userAgent,
            attestation: parsed.data.attestation as never,
        });
        if (!res.ok && res.code === "rate_limited") {
            return json(
                { detail: "Too many registration attempts" },
                { status: 429, headers: { "retry-after": String(res.retryAfterSeconds) } }
            );
        }
        if (!res.ok && res.code === "duplicate_credential") return json({ detail: "Passkey already registered" }, { status: 409 });
        if (!res.ok && res.code === "db_unavailable") return json({ detail: "Service unavailable" }, { status: 503 });
        if (!res.ok) return json({ detail: "Invalid passkey attestation" }, { status: 400 });
        return json({ message: "ok" }, { status: 201 });
    }

    if (method === "POST" && path === "/auth/passkeys/login/options") {
        const body = await readJsonBody(request);
        const parsed = PasskeyLoginOptionsSchema.safeParse(body);
        if (!parsed.success) return json({ detail: "Invalid request body" }, { status: 400 });

        const ipAddress = clientIpFromRequest(request);
        const userAgent = userAgentFromRequest(request);
        const res = await getPasskeyAuthenticationOptions({
            request,
            identifier: parsed.data.identifier,
            ipAddress,
            userAgent,
        });
        if (!res.ok && res.code === "rate_limited") {
            return json({ detail: "Too many login attempts" }, { status: 429, headers: { "retry-after": String(res.retryAfterSeconds) } });
        }
        if (!res.ok && res.code === "db_unavailable") return json({ detail: "Service unavailable" }, { status: 503 });
        if (!res.ok) return json({ detail: "Passkey login unavailable" }, { status: 503 });
        return json(res.options);
    }

    if (method === "POST" && path === "/auth/passkeys/login/verify") {
        const body = await readJsonBody(request);
        const parsed = PasskeyLoginVerifySchema.safeParse(body);
        if (!parsed.success) return json({ detail: "Invalid request body" }, { status: 400 });

        const ipAddress = clientIpFromRequest(request);
        const userAgent = userAgentFromRequest(request);
        const res = await verifyPasskeyAuthentication({
            request,
            assertion: parsed.data.assertion as never,
            ipAddress,
            userAgent,
            totpCode: parsed.data.totp_code,
            recoveryCode: parsed.data.recovery_code,
        });

        if (!res.ok && res.code === "rate_limited") {
            return json({ detail: "Too many login attempts" }, { status: 429, headers: { "retry-after": String(res.retryAfterSeconds) } });
        }
        if (!res.ok && res.code === "mfa_required") {
            return json({ detail: "MFA required", mfa_required: true }, { status: 401 });
        }
        if (!res.ok && res.code === "db_unavailable") {
            return json({ detail: "Service unavailable" }, { status: 503 });
        }
        if (!res.ok && res.code === "service_unavailable") {
            return json({ detail: "Service unavailable" }, { status: 503 });
        }
        if (!res.ok) {
            return json({ detail: "Invalid credentials" }, { status: 401 });
        }

        const existingToken = authTokenFromRequest(request);
        try {
            await logoutSession(existingToken);
        } catch {
        }

        const session = await createSessionForUser({
            userId: res.user.id,
            ipAddress,
            userAgent,
            mfaVerified: res.mfaVerified,
        });
        if (!session.ok && session.code === "mfa_required") {
            return json({ detail: "MFA required", mfa_required: true }, { status: 401 });
        }
        if (!session.ok && session.code === "rate_limited") {
            return json(
                { detail: "Too many sessions created" },
                { status: 429, headers: { "retry-after": String(session.retryAfterSeconds) } }
            );
        }
        if (!session.ok && session.code === "db_unavailable") {
            return json({ detail: "Service unavailable" }, { status: 503 });
        }
        if (!session.ok) {
            return json({ detail: "Login failed" }, { status: 500 });
        }

        const cookie = buildSessionCookie({ sessionId: session.session.sessionId, expiresAt: session.session.expiresAt, secure: isHttps(request) });
        return json(
            { id: res.user.id, email: res.user.email, role: res.user.role, message: "ok" },
            { headers: { "set-cookie": cookie } }
        );
    }

    if (path === "/white-label/partners") {
        const auth = await requireAuthContext(request, { cachedAuth: state.cachedAuth });
        if (!auth.ok) return auth.res;
        const { ctx } = auth;

        const roleDenied = await requireRoleOr403({ auth, roles: ["platform_admin"] });
        if (roleDenied) return roleDenied;

        if (isDatabaseConfigured()) {
            const forbidden = await requirePermissionOr403({ auth, permission: "manage_partners" });
            if (forbidden) return forbidden;
        }

        if (method === "GET") {
            if (!isDatabaseConfigured()) {
                ensureSeedPartners();
                return json({ items: Array.from(partnersStore.values()).sort((a, b) => a.partner_id.localeCompare(b.partner_id)) });
            }
            const out = await listPartners({ ctx });
            if (!out.ok) return json({ detail: "Forbidden" }, { status: out.status });
            return json({
                items: out.partners.map((p) => ({
                    partner_id: p.partner_id,
                    display_name: p.display_name,
                    allow_transfer: p.allow_transfer,
                    created_at: p.created_at.toISOString(),
                    updated_at: p.updated_at.toISOString(),
                })),
            });
        }

        if (method === "POST") {
            const body = await readJsonBody(request);
            const parsed = PartnerCreateSchema.safeParse(body);
            if (!parsed.success) return json({ detail: "Invalid request body" }, { status: 400 });
            const partnerId = normalizePartnerId(parsed.data.partner_id);
            const displayName = parsed.data.display_name.trim();
            const adminEmail = parsed.data.admin_email.trim().toLowerCase();
            const allowTransfer = parsed.data.allow_transfer ?? true;
            if (!partnerId) return json({ detail: "Invalid request body" }, { status: 400 });

            if (!isDatabaseConfigured()) {
                ensureSeedPartners();
                if (partnersStore.has(partnerId)) return json({ detail: "Partner already exists" }, { status: 409 });
                const rec: PartnerRecord = {
                    partner_id: partnerId,
                    display_name: displayName,
                    allow_transfer: allowTransfer,
                    created_at: nowIso(),
                    admin_email: adminEmail,
                    admin_token: `partner-${partnerId}-token`,
                };
                partnersStore.set(partnerId, rec);
                return json(rec, { status: 201 });
            }

            const created = await upsertPartner({ ctx, partnerId, displayName, allowTransfer });
            if (!created.ok) return json({ detail: "Forbidden" }, { status: created.status });

            const sql = getSql();
            const email = adminEmail.trim().toLowerCase();
            const userRows = await sql<{ id: string }[]>`
                select id
                from users
                where email = ${email}
                limit 1
            `;
            const userId = userRows[0]?.id ?? null;
            if (userId) {
                await assignPartnerAdmin({ platformCtx: ctx, userId, partnerId }).catch(() => undefined);
            }

            return json(
                {
                    partner_id: created.partner.partner_id,
                    display_name: created.partner.display_name,
                    allow_transfer: created.partner.allow_transfer,
                    created_at: created.partner.created_at.toISOString(),
                    updated_at: created.partner.updated_at.toISOString(),
                },
                { status: 201 }
            );
        }
    }

    {
        const m = path.match(/^\/white-label\/partners\/([^/]+)\/tenants\/([^/]+)\/agent-settings$/);
        if (m) {
            const partnerIdFromPath = normalizePartnerId(decodeURIComponent(m[1] ?? ""));
            const tenantIdFromPath = decodeURIComponent(m[2] ?? "");
            const branding = getWhiteLabelBranding(partnerIdFromPath);

            const auth = tenantEnforcement.auth ?? (await requireAuthContext(request, { cachedAuth: state.cachedAuth }));
            if (!auth || !auth.ok) return auth ? auth.res : json({ detail: "Unauthorized" }, { status: 401 });

            if (!isDatabaseConfigured()) {
                ensureSeedPartners();
                const allowTransfer = partnersStore.get(partnerIdFromPath)?.allow_transfer ?? branding?.features.callTransfer ?? true;
                const key = `${partnerIdFromPath}:${tenantIdFromPath}`;
                const existing = agentSettingsByTenant.get(key) ?? defaultAgentSettings({ partnerId: partnerIdFromPath, tenantId: tenantIdFromPath });
                const config = allowTransfer ? existing : { ...existing, transferEnabled: false };

                if (method === "GET") {
                    return json({
                        partner: { id: partnerIdFromPath, allowTransfer },
                        tenant: { id: tenantIdFromPath },
                        agentSettings: { transfer_enabled: allowTransfer },
                        config: { systemPrompt: config.systemPrompt, greetingMessage: config.greetingMessage, transferEnabled: config.transferEnabled },
                        updatedAt: config.updatedAt,
                    });
                }

                if (method === "PATCH") {
                    const roleDenied = await requireRoleOr403({ auth, roles: ["partner_admin"], scope: { partnerId: partnerIdFromPath } });
                    if (roleDenied) return roleDenied;
                    const body = await readJsonBody(request);
                    const parsed = AgentSettingsInputSchema.safeParse(body);
                    if (!parsed.success) return json({ detail: "Invalid request body" }, { status: 400 });

                    const nextPrompt = parsed.data.systemPrompt.trim();
                    const nextGreeting = parsed.data.greetingMessage.trim();
                    if (nextPrompt.length === 0) return json({ detail: "System prompt cannot be empty" }, { status: 400 });
                    if (nextGreeting.length === 0) return json({ detail: "Greeting message cannot be empty" }, { status: 400 });

                    const nextTransfer = Boolean(parsed.data.transferEnabled);
                    if (nextTransfer && !allowTransfer) return json({ detail: "This feature is disabled by partner policy" }, { status: 400 });

                    const updated: AgentSettings = {
                        systemPrompt: nextPrompt,
                        greetingMessage: nextGreeting,
                        transferEnabled: allowTransfer ? nextTransfer : false,
                        updatedAt: nowIso(),
                    };
                    agentSettingsByTenant.set(key, updated);

                    return json({
                        partner: { id: partnerIdFromPath, allowTransfer },
                        tenant: { id: tenantIdFromPath },
                        agentSettings: { transfer_enabled: allowTransfer },
                        config: { systemPrompt: updated.systemPrompt, greetingMessage: updated.greetingMessage, transferEnabled: updated.transferEnabled },
                        updatedAt: updated.updatedAt,
                    });
                }

                return json({ error: "Method not allowed" }, { status: 405 });
            }

            const tenant = tenantEnforcement.tenant;
            if (!tenant) return json({ detail: "Tenant not found" }, { status: 404 });

            let allowTransfer = branding?.features.callTransfer ?? true;
            try {
                const sql = getSql();
                const partnerRows = await sql<{ allow_transfer: boolean }[]>`
                    select allow_transfer
                    from partners
                    where partner_id = ${tenant.partner_id}
                    limit 1
                `;
                if (typeof partnerRows[0]?.allow_transfer === "boolean") allowTransfer = partnerRows[0].allow_transfer;
            } catch {
            }

            const key = `${tenant.partner_id}:${tenant.id}`;
            const existing = agentSettingsByTenant.get(key) ?? defaultAgentSettings({ partnerId: tenant.partner_id, tenantId: tenant.id });
            const config = allowTransfer ? existing : { ...existing, transferEnabled: false };

            if (method === "GET") {
                return json({
                    partner: { id: tenant.partner_id, allowTransfer },
                    tenant: { id: tenant.id },
                    agentSettings: { transfer_enabled: allowTransfer },
                    config: { systemPrompt: config.systemPrompt, greetingMessage: config.greetingMessage, transferEnabled: config.transferEnabled },
                    updatedAt: config.updatedAt,
                });
            }

            if (method === "PATCH") {
                const roleDenied = await requireRoleOr403({ auth, roles: ["tenant_admin", "partner_admin"], scope: { tenantId: tenant.id, partnerId: tenant.partner_id } });
                if (roleDenied) return roleDenied;
                const forbidden = await requirePermissionOr403({ auth, permission: "manage_agent_settings", scope: { tenantId: tenant.id } });
                if (forbidden) return forbidden;
                const body = await readJsonBody(request);
                const parsed = AgentSettingsInputSchema.safeParse(body);
                if (!parsed.success) return json({ detail: "Invalid request body" }, { status: 400 });

                const nextPrompt = parsed.data.systemPrompt.trim();
                const nextGreeting = parsed.data.greetingMessage.trim();
                if (nextPrompt.length === 0) return json({ detail: "System prompt cannot be empty" }, { status: 400 });
                if (nextGreeting.length === 0) return json({ detail: "Greeting message cannot be empty" }, { status: 400 });

                const nextTransfer = Boolean(parsed.data.transferEnabled);
                if (nextTransfer && !allowTransfer) return json({ detail: "This feature is disabled by partner policy" }, { status: 400 });

                const updated: AgentSettings = {
                    systemPrompt: nextPrompt,
                    greetingMessage: nextGreeting,
                    transferEnabled: allowTransfer ? nextTransfer : false,
                    updatedAt: nowIso(),
                };
                agentSettingsByTenant.set(key, updated);

                return json({
                    partner: { id: tenant.partner_id, allowTransfer },
                    tenant: { id: tenant.id },
                    agentSettings: { transfer_enabled: allowTransfer },
                    config: { systemPrompt: updated.systemPrompt, greetingMessage: updated.greetingMessage, transferEnabled: updated.transferEnabled },
                    updatedAt: updated.updatedAt,
                });
            }

            return json({ error: "Method not allowed" }, { status: 405 });
        }
    }

    if (path === "/platform/partners") {
        const auth = await requireAuthContext(request, { cachedAuth: state.cachedAuth });
        if (!auth.ok) return auth.res;
        const { ctx } = auth;

        const roleDenied = await requireRoleOr403({ auth, roles: ["platform_admin"] });
        if (roleDenied) return roleDenied;

        const forbidden = await requirePermissionOr403({ auth, permission: "manage_partners" });
        if (forbidden) return forbidden;

        if (method === "GET") {
            const out = await listPartners({ ctx });
            if (!out.ok) return json({ detail: "Forbidden" }, { status: out.status });
            return json({
                items: out.partners.map((p) => ({
                    partner_id: p.partner_id,
                    display_name: p.display_name,
                    allow_transfer: p.allow_transfer,
                    created_at: p.created_at.toISOString(),
                    updated_at: p.updated_at.toISOString(),
                })),
            });
        }

        if (method === "POST") {
            const body = await readJsonBody(request);
            const parsed = PartnerUpsertSchema.safeParse(body);
            if (!parsed.success) return json({ detail: "Invalid request body" }, { status: 400 });
            const partnerId = normalizePartnerId(parsed.data.partner_id);
            const displayName = parsed.data.display_name.trim();
            const allowTransfer = parsed.data.allow_transfer ?? true;
            const created = await upsertPartner({ ctx, partnerId, displayName, allowTransfer });
            if (!created.ok) return json({ detail: "Invalid request" }, { status: created.status });
            return json(
                {
                    partner_id: created.partner.partner_id,
                    display_name: created.partner.display_name,
                    allow_transfer: created.partner.allow_transfer,
                    created_at: created.partner.created_at.toISOString(),
                    updated_at: created.partner.updated_at.toISOString(),
                },
                { status: 201 }
            );
        }

        return json({ error: "Method not allowed" }, { status: 405 });
    }

    {
        const m = path.match(/^\/partners\/([^/]+)\/tenants$/);
        if (m) {
            const partnerId = normalizePartnerId(decodeURIComponent(m[1] ?? ""));
            const auth = await requireAuthContext(request, { cachedAuth: state.cachedAuth });
            if (!auth.ok) return auth.res;
            const { ctx } = auth;

            const roleDenied = await requireRoleOr403({ auth, roles: ["partner_admin"], scope: { partnerId } });
            if (roleDenied) return roleDenied;

            const forbidden = await requirePermissionOr403({ auth, permission: "manage_tenants", scope: { partnerId } });
            if (forbidden) return forbidden;

            if (method === "GET") {
                const out = await listPartnerTenants({ ctx, partnerId });
                if (!out.ok) return json({ detail: out.status === 403 ? "Forbidden" : "Invalid request" }, { status: out.status });
                return json({
                    items: out.tenants.map((t) => ({
                        id: t.id,
                        partner_id: t.partner_id,
                        name: t.name,
                        status: t.status,
                        created_at: t.created_at.toISOString(),
                        updated_at: t.updated_at.toISOString(),
                    })),
                });
            }

            if (method === "POST") {
                const body = await readJsonBody(request);
                const parsed = TenantUpsertSchema.safeParse(body);
                if (!parsed.success) return json({ detail: "Invalid request body" }, { status: 400 });
                const name = parsed.data.name.trim();
                const tenantId = parsed.data.id?.trim() || undefined;
                const status = parsed.data.status;
                const out = await upsertTenant({ ctx, partnerId, tenantId, name, status });
                if (!out.ok) return json({ detail: out.status === 403 ? "Forbidden" : "Invalid request" }, { status: out.status });
                return json(
                    {
                        id: out.tenant.id,
                        partner_id: out.tenant.partner_id,
                        name: out.tenant.name,
                        status: out.tenant.status,
                        created_at: out.tenant.created_at.toISOString(),
                        updated_at: out.tenant.updated_at.toISOString(),
                    },
                    { status: 201 }
                );
            }

            return json({ error: "Method not allowed" }, { status: 405 });
        }
    }

    {
        const m = path.match(/^\/tenants\/([^/]+)\/users$/);
        if (m) {
            const tenantId = decodeURIComponent(m[1] ?? "");
            const auth = tenantEnforcement.auth ?? (await requireAuthContext(request, { cachedAuth: state.cachedAuth }));
            if (!auth || !auth.ok) return auth ? auth.res : json({ detail: "Unauthorized" }, { status: 401 });
            const { ctx } = auth;

            const partnerId = tenantEnforcement.tenant?.partner_id;
            const roleDenied = await requireRoleOr403({ auth, roles: ["tenant_admin", "partner_admin"], scope: { tenantId, partnerId } });
            if (roleDenied) return roleDenied;

            const forbidden = await requirePermissionOr403({ auth, permission: "manage_users", scope: { tenantId } });
            if (forbidden) return forbidden;

            if (method === "POST") {
                const body = await readJsonBody(request);
                const parsed = TenantAssignRoleSchema.safeParse(body);
                if (!parsed.success) return json({ detail: "Invalid request body" }, { status: 400 });
                const userId = parsed.data.user_id.trim();
                const role = (parsed.data.role ?? "user") as RoleName;
                const out = await assignTenantUserRole({ ctx, tenantId, userId, role });
                if (!out.ok) return json({ detail: out.status === 403 ? "Forbidden" : "Invalid request" }, { status: out.status });
                return noContent({ status: 204 });
            }

            return json({ error: "Method not allowed" }, { status: 405 });
        }
    }

    if (method === "GET" && path === "/email/templates") {
        const url = new URL(request.url);
        const EmailTemplatesQuerySchema = z.object({ partner: z.string().max(120).optional() }).strict();
        const qp = EmailTemplatesQuerySchema.safeParse({
            partner: url.searchParams.get("partner") ?? url.searchParams.get("partnerId") ?? undefined,
        });
        if (!qp.success) return json({ detail: "Invalid request" }, { status: 400 });
        const partnerId = qp.data.partner ? normalizePartnerId(sanitizeTextInput(qp.data.partner, { maxLen: 120 })) : "";
        const branding = partnerId.length > 0 ? getWhiteLabelBranding(partnerId) : null;
        return json({ items: emailTemplates({ branding }) });
    }

    if (method === "POST" && path === "/email/send") {
        const body = await readJsonBody(request);
        const parsed = EmailSendSchema.safeParse(body);
        if (!parsed.success) return json({ detail: "Invalid request body" }, { status: 400 });

        const to = (parsed.data.to ?? parsed.data.recipients ?? []).map((v) => v.trim()).filter((v) => v.length > 0);
        const templateId = parsed.data.templateId ?? parsed.data.template_id ?? undefined;
        const subject = parsed.data.subject;
        const htmlOverride = typeof parsed.data.html === "string" ? sanitizeHtmlEmailInput(parsed.data.html) : undefined;

        if (to.length === 0) return json({ detail: "Invalid request body" }, { status: 400 });
        if (!templateId && !htmlOverride) return json({ detail: "Invalid request body" }, { status: 400 });

        const templates = emailTemplates();
        const template = templateId ? templates.find((t) => t.id === templateId) : undefined;

        const html = htmlOverride ?? template?.html;
        if (!html) return json({ detail: "Invalid request body" }, { status: 400 });

        try {
            const out = await sendEmail({ to, subject: subject ?? template?.name, html });
            return json(out);
        } catch (err) {
            const message = err instanceof Error ? err.message : "Email send failed";
            return json({ detail: message }, { status: 500 });
        }
    }

    if (process.env.NODE_ENV === "production") {
        return json({ error: "Not found" }, { status: 404 });
    }

    if (method === "GET" && path === "/connectors") {
        return json({ items: [] });
    }

    if (method === "POST" && path === "/connectors") {
        const body = await readJsonBody(request);
        const parsed = DevConnectorCreateSchema.safeParse(body);
        if (!parsed.success) return json({ detail: "Invalid request body" }, { status: 400 });
        return json({
            id: `connector-${Math.random().toString(16).slice(2)}`,
            name: parsed.data.name ?? "Connector",
            type: parsed.data.type ?? "unknown",
            config: parsed.data.config ?? {},
            createdAt: nowIso(),
        });
    }

    if (method === "GET" && path === "/connectors/status") {
        return json({
            items: [
                { type: "calendar", status: "disconnected", last_sync: null, error_message: null, provider: null },
                { type: "email", status: "disconnected", last_sync: null, error_message: null, provider: null },
                { type: "crm", status: "disconnected", last_sync: null, error_message: null, provider: null },
                { type: "drive", status: "disconnected", last_sync: null, error_message: null, provider: null },
            ],
        });
    }

    {
        const m = path.match(/^\/connectors\/([^/]+)\/authorize$/);
        if (method === "GET" && m) {
            const type = decodeURIComponent(m[1] ?? "");
            const url = new URL(request.url);
            const redirectUri = url.searchParams.get("redirect_uri") || `${url.origin}/connectors/callback?type=${encodeURIComponent(type)}`;
            const redirect = new URL(redirectUri);
            redirect.searchParams.set("type", type);
            if (!redirect.searchParams.has("status")) redirect.searchParams.set("status", "success");
            return json({ authorization_url: redirect.toString() });
        }
    }

    {
        const m = path.match(/^\/connectors\/([^/]+)\/disconnect$/);
        if (method === "POST" && m) {
            return json({ ok: true });
        }
    }

    if (method === "GET" && path === "/connector-accounts") {
        return json({ items: [] });
    }

    if (method === "GET" && path === "/meetings") {
        return json({ items: [] });
    }

    if (method === "GET" && path === "/calendar/events") {
        return json({ items: [], total: 0, page: 1, page_size: 50 });
    }

    if (method === "POST" && path === "/calendar/events") {
        const body = await readJsonBody(request);
        const parsed = DevCalendarEventCreateSchema.safeParse(body);
        if (!parsed.success) return json({ detail: "Invalid request body" }, { status: 400 });
        return json({
            id: `event-${Math.random().toString(16).slice(2)}`,
            title: parsed.data.title ?? "Meeting",
            startTime: parsed.data.start_time ?? nowIso(),
            endTime: parsed.data.end_time ?? undefined,
            status: "scheduled",
            leadId: parsed.data.lead_id ?? undefined,
            leadName: parsed.data.lead_name ?? undefined,
            notes: parsed.data.notes ?? undefined,
            participants: [],
        });
    }

    {
        const m = path.match(/^\/calendar\/events\/([^/]+)$/);
        if (method === "PATCH" && m) {
            const body = await readJsonBody(request);
            const parsed = DevCalendarEventPatchSchema.safeParse(body);
            if (!parsed.success) return json({ detail: "Invalid request body" }, { status: 400 });
            const title = parsed.data.title ?? "Meeting";
            const startTime = parsed.data.start_time ?? nowIso();
            const endTime = parsed.data.end_time ?? undefined;
            return json({ id: decodeURIComponent(m[1] ?? ""), title, startTime, endTime, status: "scheduled", participants: [] });
        }
        if (method === "DELETE" && m) {
            return json({ ok: true });
        }
    }

    if (method === "GET" && path === "/reminders") {
        return json({ items: [] });
    }

    if (method === "POST" && path === "/reminders") {
        const body = await readJsonBody(request);
        const parsed = DevReminderCreateSchema.safeParse(body);
        if (!parsed.success) return json({ detail: "Invalid request body" }, { status: 400 });
        return json({
            id: `reminder-${Math.random().toString(16).slice(2)}`,
            content: parsed.data.content ?? "",
            status: "scheduled",
            channel: parsed.data.channel ?? "email",
            scheduledAt: parsed.data.scheduled_at ?? nowIso(),
            meetingId: parsed.data.meeting_id ?? undefined,
            meetingTitle: parsed.data.meeting_title ?? undefined,
            contactId: parsed.data.contact_id ?? undefined,
            contactName: parsed.data.contact_name ?? undefined,
            toEmail: parsed.data.to_email ?? undefined,
            toPhone: parsed.data.to_phone ?? undefined,
        });
    }

    {
        const m = path.match(/^\/reminders\/([^/]+)$/);
        if (method === "PATCH" && m) {
            const body = await readJsonBody(request);
            const parsed = DevReminderPatchSchema.safeParse(body);
            if (!parsed.success) return json({ detail: "Invalid request body" }, { status: 400 });
            return json({
                id: decodeURIComponent(m[1] ?? ""),
                content: parsed.data.content ?? "",
                status: parsed.data.status ?? "scheduled",
                channel: parsed.data.channel ?? "email",
                scheduledAt: parsed.data.scheduled_at ?? nowIso(),
            });
        }
    }

    {
        const m = path.match(/^\/reminders\/([^/]+)\/cancel$/);
        if (method === "POST" && m) {
            return json({ id: decodeURIComponent(m[1] ?? ""), content: "", status: "canceled", channel: "email", scheduledAt: nowIso() });
        }
    }

    if (method === "GET" && path === "/assistant/actions") {
        return json({ items: [] });
    }

    if (method === "GET" && path === "/assistant/runs") {
        return json({ items: [], total: 0, page: 1, page_size: 50 });
    }

    {
        const m = path.match(/^\/assistant\/runs\/([^/]+)\/retry$/);
        if (method === "POST" && m) {
            return json({ id: decodeURIComponent(m[1] ?? ""), actionType: "retry", source: "ui", status: "pending", createdAt: nowIso() });
        }
    }

    if (method === "POST" && path === "/assistant/plan") {
        return json({ summary: "Development mode: plan generated.", steps: [] });
    }

    if (method === "POST" && path === "/assistant/execute") {
        const auth = state.cachedAuth ?? (await authMeFromRequest(request));
        if (!auth) return json({ detail: "Unauthorized" }, { status: 401 });
        const me = auth.me;

        const partnerId = typeof me.partner_id === "string" && me.partner_id.trim().length > 0 ? me.partner_id.trim().toLowerCase() : "default";
        const limit = partnerConcurrencyLimit(partnerId);
        const inflight = inflightByPartner.get(partnerId) ?? 0;
        if (inflight >= limit) {
            return json(
                { detail: `Concurrency limit reached (${limit}).`, code: "concurrency_limit_reached", partner_id: partnerId, limit, inflight },
                { status: 429, headers: { "retry-after": "3" } }
            );
        }
        inflightByPartner.set(partnerId, inflight + 1);
        setTimeout(() => {
            const cur = inflightByPartner.get(partnerId) ?? 0;
            if (cur <= 1) inflightByPartner.delete(partnerId);
            else inflightByPartner.set(partnerId, cur - 1);
        }, 900);

        return json({
            id: `run-${Math.random().toString(16).slice(2)}`,
            actionType: "execute",
            source: "ui",
            status: "pending",
            createdAt: nowIso(),
            partner_id: partnerId,
            limit,
            inflight: inflight + 1,
        });
    }

    return json({ error: "Not found" }, { status: 404 });
}

export async function GET(request: Request, ctx: RouteContext) {
    const { path } = await ctx.params;
    return handle(request, path ?? []);
}

export async function POST(request: Request, ctx: RouteContext) {
    const { path } = await ctx.params;
    return handle(request, path ?? []);
}

export async function PATCH(request: Request, ctx: RouteContext) {
    const { path } = await ctx.params;
    return handle(request, path ?? []);
}

export async function DELETE(request: Request, ctx: RouteContext) {
    const { path } = await ctx.params;
    return handle(request, path ?? []);
}
