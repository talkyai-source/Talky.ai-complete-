import { z } from "zod";
import { setBrowserAuthToken } from "@/lib/auth-token";
import { createHttpClient, ApiClientError, resetSessionExpiredLatch } from "@/lib/http-client";
import { apiBaseUrl } from "@/lib/env";

/* ------------------------------------------------------------------ */
/*  Response Schemas                                                   */
/* ------------------------------------------------------------------ */

export const LoginResponseSchema = z
    .object({
        access_token: z.string(),
        token_type: z.string().optional(),
        user_id: z.string(),
        email: z.string().email(),
        role: z.string(),
        business_name: z.string().optional().nullable(),
        minutes_remaining: z.number().optional(),
        message: z.string().optional(),
        mfa_required: z.boolean().optional(),
        mfa_challenge_token: z.string().optional().nullable(),
    })
    .passthrough()
    .transform((v) => ({
        access_token: v.access_token,
        token_type: v.token_type ?? "bearer",
        user_id: v.user_id,
        email: v.email,
        role: v.role,
        business_name: v.business_name ?? undefined,
        minutes_remaining: v.minutes_remaining ?? 0,
        message: v.message ?? "",
        mfa_required: v.mfa_required ?? false,
        mfa_challenge_token: v.mfa_challenge_token ?? undefined,
    }));

export type LoginResponse = z.infer<typeof LoginResponseSchema>;

// POST /auth/register no longer issues a session — the response shape
// changed to {user_id, email, verification_required, verification_email_sent, message}.
// Users must verify their email and then sign in via /auth/login.
//
// Kept the LoginResponse-shaped fallback so a future re-enable of
// session-on-register doesn't immediately crash the parser.
export const RegisterResponseSchema = z
    .object({
        user_id: z.string(),
        email: z.string().email(),
        business_name: z.string().optional().nullable(),
        verification_required: z.boolean().optional(),
        verification_email_sent: z.boolean().optional(),
        message: z.string().optional(),
        // Legacy fields — null in the new response, present if a future
        // backend reverts to session-on-register.
        access_token: z.string().optional().nullable(),
        token_type: z.string().optional(),
        role: z.string().optional(),
        minutes_remaining: z.number().optional(),
    })
    .passthrough()
    .transform((v) => ({
        user_id: v.user_id,
        email: v.email,
        business_name: v.business_name ?? undefined,
        verification_required: v.verification_required ?? true,
        verification_email_sent: v.verification_email_sent ?? false,
        message: v.message ?? "",
        access_token: v.access_token ?? null,
        role: v.role ?? null,
        minutes_remaining: v.minutes_remaining ?? 0,
    }));
export type RegisterResponse = z.infer<typeof RegisterResponseSchema>;

export const SignupStartResponseSchema = z
    .object({
        message: z.string(),
        expires_in_minutes: z.number(),
        email: z.string().email(),
    })
    .passthrough();
export type SignupStartResponse = z.infer<typeof SignupStartResponseSchema>;

export const SignupVerifyCodeResponseSchema = z
    .object({
        message: z.string(),
        email: z.string().email(),
    })
    .passthrough();
export type SignupVerifyCodeResponse = z.infer<typeof SignupVerifyCodeResponseSchema>;

export const SignupCompleteResponseSchema = LoginResponseSchema;
export type SignupCompleteResponse = LoginResponse;

export const VerifyOtpResponseSchema = z
    .object({
        access_token: z.string(),
        refresh_token: z.string(),
        user_id: z.string(),
        email: z.string().email(),
        message: z.string().optional(),
    })
    .passthrough();
export type VerifyOtpResponse = z.infer<typeof VerifyOtpResponseSchema>;

export const MeResponseSchema = z
    .object({
        id: z.string(),
        email: z.string().email(),
        name: z.string().optional().nullable(),
        business_name: z.string().optional().nullable(),
        role: z.string(),
        minutes_remaining: z.number(),
        // Admin / suspension fields populated when the user has elevated
        // permissions or the tenant/partner is in a non-active state.
        // All optional — missing fields just mean "not suspended" /
        // "no admin scope". Used by SuspensionStateProvider.
        partner_id: z.string().optional().nullable(),
        tenant_id: z.string().optional().nullable(),
        partner_status: z.string().optional().nullable(),
        tenant_status: z.string().optional().nullable(),
        suspended_scope: z.string().optional().nullable(),
        suspension_reason: z.string().optional().nullable(),
        suspended_at: z.string().optional().nullable(),
    })
    .passthrough()
    .transform((v) => ({
        ...v,
        name: v.name ?? undefined,
        business_name: v.business_name ?? undefined,
        partner_id: v.partner_id ?? undefined,
        tenant_id: v.tenant_id ?? undefined,
        partner_status: v.partner_status ?? undefined,
        tenant_status: v.tenant_status ?? undefined,
        suspended_scope: v.suspended_scope ?? undefined,
        suspension_reason: v.suspension_reason ?? undefined,
        suspended_at: v.suspended_at ?? undefined,
    }));

export type MeResponse = z.infer<typeof MeResponseSchema>;

export const ChangePasswordResponseSchema = z
    .object({
        detail: z.string(),
    })
    .passthrough();

export type ChangePasswordResponse = z.infer<typeof ChangePasswordResponseSchema>;

/* ------------------------------------------------------------------ */
/*  API Client                                                         */
/* ------------------------------------------------------------------ */

/**
 * AH-Phase-B: the singleton HttpClient instance shared by every
 * browser-side API wrapper (api.ts, backend-api.ts, dashboard-api.ts,
 * ai-options-api.ts, extended-api.ts, …). Single-flight refresh dedup,
 * the fresh-login grace window, and the session-expired latch are all
 * PER-INSTANCE state — without sharing, simultaneous 401s from two
 * wrappers each fire /auth/refresh in parallel and the second
 * response can clobber the first's token write. AuthContext's
 * setTokenProvider() callback (Phase 2 of the universal-auth-state
 * plan) attaches to this one instance, so token rotation is
 * automatically picked up everywhere.
 *
 * Lazy-initialised on first read so apiBaseUrl() is resolved AFTER
 * any env-var bootstrap has run. The server-side Next.js route at
 * app/api/voices/route.ts intentionally uses its own short-lived
 * client per request (different lifecycle: Vercel function, no
 * AuthContext) — it's the only legitimate exception.
 */
let _sharedHttpClient: ReturnType<typeof createHttpClient> | undefined;

export function sharedHttpClient() {
    if (!_sharedHttpClient) {
        _sharedHttpClient = createHttpClient({ baseUrl: apiBaseUrl() });
    }
    return _sharedHttpClient;
}

/* ---------- Campaign knowledge (vectorless RAG) types ---------- */

export type KnowledgeMode = "none" | "inline" | "map_retrieve" | "retrieve";

export interface KnowledgeNode {
    id: string;
    parent_id: string | null;
    depth: number;
    path: string;
    position: number;
    heading: string;
    summary?: string | null;
    voice_answer?: string | null;
    keywords?: string[] | null;
    example_questions?: string[] | null;
    priority: number;
    hit_count: number;
    enabled: boolean;
    children: KnowledgeNode[];
}

export interface KnowledgeSource {
    id: string;
    filename?: string | null;
    token_count: number;
    version: number;
    status: "processing" | "ready" | "failed";
    error?: string | null;
    created_at: string;
}

export interface CampaignKnowledge {
    campaign_id: string;
    knowledge_mode: KnowledgeMode | string | null;
    sources: KnowledgeSource[];
    tree: KnowledgeNode[];
}

export interface KnowledgeIngestResult {
    source_id: string;
    node_count: number;
    token_count: number;
    mode: KnowledgeMode | string;
}

class ApiClient {
    private client() {
        return sharedHttpClient();
    }

    private parseOrThrow<T>(
        schema: { safeParse: (v: unknown) => { success: true; data: T } | { success: false } },
        data: unknown,
        meta: { url: string; method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE" },
    ) {
        const parsed = schema.safeParse(data);
        if (parsed.success) return parsed.data;
        throw new ApiClientError({
            code: "invalid_response",
            message: "Invalid response format",
            url: meta.url,
            method: meta.method,
            details: data,
        });
    }

    setToken(token: string) {
        setBrowserAuthToken(token);
        // A fresh token re-arms the http-client's session-expired
        // latch.  Without this, login → expire → login → expire only
        // bounces to /auth/login on the FIRST expiry of the process.
        resetSessionExpiredLatch();
    }

    clearToken() {
        setBrowserAuthToken(null);
    }

    /**
     * Generic escape hatch for raw requests that don't (yet) have a
     * dedicated method on this class. Phase 5 of the universal-auth-state
     * refactor uses this so the React-Query hook files (billing-api,
     * telephony-api) can delegate one-liner fetches through the shared
     * client without each duplicating auth-header injection,
     * refresh-on-401, single-flight refresh dedup, or the
     * session-expired latch.
     */
    request<T>(opts: import("@/lib/http-client").HttpRequestOptions): Promise<T> {
        return this.client().request(opts) as Promise<T>;
    }

    /* ---------- Auth ---------- */

    /**
     * Login.
     *
     * Two-mode signature to satisfy both flows:
     *  - `login(email, password)` — classic password auth (auth-context).
     *  - `login(email)`           — passwordless / OTP-trigger used by the
     *                                registration flow's "resend code" path.
     *
     * The backend decides which path it serves based on whether
     * `password` is in the body.
     */
    async login(email: string, password?: string): Promise<LoginResponse> {
        const path = "/auth/login";
        const method = "POST" as const;
        const body: Record<string, string> = { email };
        if (password !== undefined) {
            body.password = password;
        }
        const data = await this.client().request({
            path,
            method,
            body,
            timeoutMs: 12_000,
        });
        return this.parseOrThrow(LoginResponseSchema, data, { url: `${apiBaseUrl()}${path}`, method });
    }

    /**
     * Email OTP verification — used by the registration flow's
     * "Enter the 6-digit code we sent" step. Returns access/refresh
     * tokens on success.
     */
    async verifyOtp(email: string, token: string): Promise<VerifyOtpResponse> {
        const path = "/auth/verify-otp";
        const method = "POST" as const;
        const data = await this.client().request({
            path,
            method,
            body: { email, token },
            timeoutMs: 12_000,
        });
        return this.parseOrThrow(VerifyOtpResponseSchema, data, { url: `${apiBaseUrl()}${path}`, method });
    }

    /**
     * Two-step signup, step 1: send name/business/email; backend emails a 6-digit code.
     * No password, no plan_id — plan is hardcoded to "free" server-side.
     */
    async signupStart(name: string, businessName: string, email: string): Promise<SignupStartResponse> {
        const path = "/auth/signup/start";
        const method = "POST" as const;
        const data = await this.client().request({
            path,
            method,
            body: { name, business_name: businessName, email },
            timeoutMs: 12_000,
        });
        return this.parseOrThrow(SignupStartResponseSchema, data, { url: `${apiBaseUrl()}${path}`, method });
    }

    /**
     * Two-step signup, step 1.5: check the code without consuming it.
     * Used to gate the password screen behind a correct code.
     */
    async signupVerifyCode(email: string, code: string): Promise<SignupVerifyCodeResponse> {
        const path = "/auth/signup/verify-code";
        const method = "POST" as const;
        const data = await this.client().request({
            path,
            method,
            body: { email, code },
            timeoutMs: 12_000,
        });
        return this.parseOrThrow(SignupVerifyCodeResponseSchema, data, { url: `${apiBaseUrl()}${path}`, method });
    }

    /**
     * Two-step signup, step 2: send code + password + confirm_password.
     * Backend creates the account on plan_id="free", returns an auth token.
     */
    async signupComplete(
        email: string,
        code: string,
        password: string,
        confirmPassword: string,
    ): Promise<SignupCompleteResponse> {
        const path = "/auth/signup/complete";
        const method = "POST" as const;
        const data = await this.client().request({
            path,
            method,
            body: { email, code, password, confirm_password: confirmPassword },
            timeoutMs: 12_000,
        });
        return this.parseOrThrow(SignupCompleteResponseSchema, data, { url: `${apiBaseUrl()}${path}`, method });
    }

    async register(
        email: string,
        password: string,
        businessName: string,
        planId: string = "basic",
        name?: string,
    ): Promise<RegisterResponse> {
        const path = "/auth/register";
        const method = "POST" as const;
        const data = await this.client().request({
            path,
            method,
            body: {
                email,
                password,
                business_name: businessName,
                plan_id: planId,
                ...(name ? { name } : {}),
            },
            timeoutMs: 12_000,
        });
        return this.parseOrThrow(RegisterResponseSchema, data, { url: `${apiBaseUrl()}${path}`, method });
    }

    async getMe(): Promise<MeResponse> {
        const method = "GET" as const;
        try {
            const path = "/auth/me";
            const data = await this.client().request({ path, method, timeoutMs: 12_000 });
            return this.parseOrThrow(MeResponseSchema, data, { url: `${apiBaseUrl()}${path}`, method });
        } catch (err) {
            if (err instanceof ApiClientError && err.status === 404) {
                const path = "/me";
                const data = await this.client().request({ path, method, timeoutMs: 12_000 });
                return this.parseOrThrow(MeResponseSchema, data, { url: `${apiBaseUrl()}${path}`, method });
            }
            throw err;
        }
    }

    async updateMe(input: { name?: string; business_name?: string }): Promise<MeResponse> {
        const path = "/auth/me";
        const method = "PATCH" as const;
        const data = await this.client().request({
            path,
            method,
            body: input,
            timeoutMs: 12_000,
        });
        return this.parseOrThrow(MeResponseSchema, data, { url: `${apiBaseUrl()}${path}`, method });
    }

    async changePassword(oldPassword: string, newPassword: string): Promise<ChangePasswordResponse> {
        const path = "/auth/change-password";
        const method = "POST" as const;
        const data = await this.client().request({
            path,
            method,
            body: {
                old_password: oldPassword,
                new_password: newPassword,
            },
            timeoutMs: 12_000,
        });
        return this.parseOrThrow(ChangePasswordResponseSchema, data, { url: `${apiBaseUrl()}${path}`, method });
    }

    async logout(): Promise<void> {
        try {
            await this.client().request({ path: "/auth/logout", method: "POST", timeoutMs: 12_000 });
        } catch (err) {
            if (err instanceof ApiClientError && (err.status === 404 || err.status === 405)) {
                // Ignore
            } else {
                throw err;
            }
        } finally {
            this.clearToken();
        }
    }

    async health(): Promise<{ status: string }> {
        const path = "/health";
        return this.client().request({ path, method: "GET", timeoutMs: 2500 });
    }

    /* ---------- MFA (Multi-Factor Authentication) ---------- */

    async getMfaStatus(): Promise<{
        enabled: boolean;
        verified_at: string | null;
        recovery_codes_remaining: number;
    }> {
        const path = "/auth/mfa/status";
        const method = "GET" as const;
        const data = await this.client().request({ path, method, timeoutMs: 10_000 });
        return z.object({
            enabled: z.boolean(),
            verified_at: z.string().nullable(),
            recovery_codes_remaining: z.number(),
        }).parse(data);
    }

    async setupMfa(): Promise<{
        provisioning_uri: string;
        qr_code: string;
        issuer: string;
        account: string;
    }> {
        const path = "/auth/mfa/setup";
        const method = "POST" as const;
        const data = await this.client().request({ path, method, timeoutMs: 10_000 });
        return z.object({
            provisioning_uri: z.string(),
            qr_code: z.string(),
            issuer: z.string(),
            account: z.string(),
        }).parse(data);
    }

    async confirmMfa(code: string): Promise<{
        enabled: boolean;
        recovery_codes: string[];
        recovery_codes_count: number;
        message: string;
    }> {
        const path = "/auth/mfa/confirm";
        const method = "POST" as const;
        const data = await this.client().request({
            path,
            method,
            body: { code },
            timeoutMs: 10_000,
        });
        return z.object({
            enabled: z.boolean(),
            recovery_codes: z.array(z.string()),
            recovery_codes_count: z.number(),
            message: z.string(),
        }).parse(data);
    }

    async verifyMfaChallenge(
        challengeToken: string,
        code?: string,
        recoveryCode?: string
    ): Promise<LoginResponse> {
        const path = "/auth/mfa/verify";
        const method = "POST" as const;
        const data = await this.client().request({
            path,
            method,
            body: {
                challenge_token: challengeToken,
                ...(code ? { code } : {}),
                ...(recoveryCode ? { recovery_code: recoveryCode } : {}),
            },
            timeoutMs: 10_000,
        });
        return this.parseOrThrow(LoginResponseSchema, data, { url: `${apiBaseUrl()}${path}`, method });
    }

    async disableMfa(password: string): Promise<{ detail: string }> {
        const path = "/auth/mfa/disable";
        const method = "POST" as const;
        return this.client().request({
            path,
            method,
            body: { password },
            timeoutMs: 10_000,
        });
    }

    async regenerateRecoveryCodes(code: string): Promise<{
        recovery_codes: string[];
        recovery_codes_count: number;
        message: string;
    }> {
        const path = "/auth/mfa/recovery-codes/regenerate";
        const method = "POST" as const;
        const data = await this.client().request({
            path,
            method,
            body: { code },
            timeoutMs: 10_000,
        });
        return z.object({
            recovery_codes: z.array(z.string()),
            recovery_codes_count: z.number(),
            message: z.string(),
        }).parse(data);
    }

    /* ---------- Sessions ---------- */

    async getActiveSessions(): Promise<{ sessions: Array<{
        id: string;
        ip_address: string;
        user_agent: string | null;
        created_at: string;
        last_active_at: string;
        is_current: boolean;
        device_info: Record<string, unknown> | null;
    }>; total: number }> {
        const path = "/sessions/active";
        const method = "GET" as const;
        return this.client().request({ path, method, timeoutMs: 10_000 });
    }

    async revokeSession(sessionId: string): Promise<{ detail: string }> {
        const path = `/sessions/${sessionId}`;
        const method = "DELETE" as const;
        return this.client().request({ path, method, timeoutMs: 10_000 });
    }

    async getSessionSecurityStatus(): Promise<{
        session_valid: boolean;
        mfa_verified: boolean;
        ip_match: boolean;
        fingerprint_match: boolean;
    }> {
        const path = "/sessions/security-status";
        const method = "GET" as const;
        return this.client().request({ path, method, timeoutMs: 10_000 });
    }

    /* ---------- Live calls (Track B) ---------- */

    /**
     * Snapshot of currently-in-flight calls + recently-ended ones.
     * Designed to be polled every 1-2s by the live panel — the backend
     * intentionally keeps the shape lean so frequent polling is cheap.
     */
    async listLiveCalls(input?: { campaignId?: string; recentWindowSeconds?: number }): Promise<{
        items: Array<{
            id: string;
            talklee_call_id?: string | null;
            to_number: string;
            status: string;
            started_at?: string | null;
            answered_at?: string | null;
            ended_at?: string | null;
            duration_seconds?: number | null;
            outcome?: string | null;
            campaign_id?: string | null;
            campaign_name?: string | null;
            lead_id?: string | null;
            caller_id?: string | null;
        }>;
        server_time: string;
    }> {
        const path = "/calls/live";
        const query: Record<string, string | number> = {};
        if (input?.campaignId) query.campaign_id = input.campaignId;
        if (input?.recentWindowSeconds !== undefined) query.recent_window_seconds = input.recentWindowSeconds;
        const data = await this.client().request({
            path,
            method: "GET",
            query,
            timeoutMs: 8_000,
        });
        return data as {
            items: Array<{
                id: string;
                talklee_call_id?: string | null;
                to_number: string;
                status: string;
                started_at?: string | null;
                answered_at?: string | null;
                ended_at?: string | null;
                duration_seconds?: number | null;
                outcome?: string | null;
                campaign_id?: string | null;
                campaign_name?: string | null;
                lead_id?: string | null;
                caller_id?: string | null;
            }>;
            server_time: string;
        };
    }

    /* ---------- Campaign knowledge (vectorless RAG) ---------- */

    async getCampaignKnowledge(campaignId: string): Promise<CampaignKnowledge> {
        const data = await this.client().request({
            path: `/campaigns/${campaignId}/knowledge`,
            method: "GET",
            timeoutMs: 12_000,
        });
        return data as CampaignKnowledge;
    }

    async uploadCampaignKnowledge(
        campaignId: string,
        file: File,
    ): Promise<KnowledgeIngestResult> {
        const form = new FormData();
        form.append("file", file);
        const data = await this.client().request({
            path: `/campaigns/${campaignId}/knowledge`,
            method: "POST",
            body: form,
            // Ingest parses + LLM-enriches every node — a large doc can take
            // a while, so give it plenty of headroom.
            timeoutMs: 180_000,
        });
        return data as KnowledgeIngestResult;
    }

    async updateKnowledgeNode(
        campaignId: string,
        nodeId: string,
        payload: Partial<Pick<KnowledgeNode, "enabled" | "priority" | "summary" | "voice_answer">>,
    ): Promise<{ id: string; updated: string[] }> {
        const data = await this.client().request({
            path: `/campaigns/${campaignId}/knowledge/nodes/${nodeId}`,
            method: "PATCH",
            body: payload,
            timeoutMs: 10_000,
        });
        return data as { id: string; updated: string[] };
    }

    async deleteKnowledgeSource(
        campaignId: string,
        sourceId: string,
    ): Promise<{ deleted: string; knowledge_mode: string }> {
        const data = await this.client().request({
            path: `/campaigns/${campaignId}/knowledge/sources/${sourceId}`,
            method: "DELETE",
            timeoutMs: 10_000,
        });
        return data as { deleted: string; knowledge_mode: string };
    }

    /* ---------- Passkeys (WebAuthn) ---------- */

    async checkUserHasPasskeys(email: string): Promise<boolean> {
        const path = "/auth/passkey-check";
        const method = "POST" as const;
        try {
            const data = await this.client().request({
                path,
                method,
                body: { email },
                timeoutMs: 5_000,
            });
            return z.object({ has_passkeys: z.boolean() }).parse(data).has_passkeys;
        } catch {
            return false;
        }
    }

    async beginPasskeyLogin(email?: string): Promise<{
        ceremony_id: string;
        options: Record<string, unknown>;
        has_passkeys: boolean;
    }> {
        const path = "/auth/passkeys/login/begin";
        const method = "POST" as const;
        const data = await this.client().request({
            path,
            method,
            body: email ? { email } : {},
            timeoutMs: 10_000,
        });
        return z.object({
            ceremony_id: z.string(),
            options: z.record(z.unknown()),
            has_passkeys: z.boolean(),
        }).parse(data);
    }

    async completePasskeyLogin(
        ceremonyId: string,
        credentialResponse: Record<string, unknown>
    ): Promise<LoginResponse> {
        const path = "/auth/passkeys/login/complete";
        const method = "POST" as const;
        const data = await this.client().request({
            path,
            method,
            body: {
                ceremony_id: ceremonyId,
                credential_response: credentialResponse,
            },
            timeoutMs: 10_000,
        });
        return this.parseOrThrow(LoginResponseSchema, data, { url: `${apiBaseUrl()}${path}`, method });
    }

    async beginPasskeyRegistration(
        authenticatorType: "platform" | "cross-platform" | "any" = "any",
        displayName?: string
    ): Promise<{ ceremony_id: string; options: Record<string, unknown> }> {
        const path = "/auth/passkeys/register/begin";
        const method = "POST" as const;
        const data = await this.client().request({
            path,
            method,
            body: {
                authenticator_type: authenticatorType,
                display_name: displayName,
            },
            timeoutMs: 10_000,
        });
        return z.object({
            ceremony_id: z.string(),
            options: z.record(z.unknown()),
        }).parse(data);
    }

    async completePasskeyRegistration(
        ceremonyId: string,
        credentialResponse: Record<string, unknown>,
        displayName?: string
    ): Promise<{ passkey_id: string; message: string }> {
        const path = "/auth/passkeys/register/complete";
        const method = "POST" as const;
        const data = await this.client().request({
            path,
            method,
            body: {
                ceremony_id: ceremonyId,
                credential_response: credentialResponse,
                display_name: displayName,
            },
            timeoutMs: 10_000,
        });
        return z.object({
            passkey_id: z.string(),
            message: z.string(),
        }).parse(data);
    }

    async listPasskeys(): Promise<Array<{
        id: string;
        credential_id: string;
        display_name: string;
        device_type: string;
        backed_up: boolean;
        transports: string[];
        created_at: string;
        last_used_at?: string | null;
    }>> {
        const path = "/auth/passkeys";
        const method = "GET" as const;
        const data = await this.client().request({ path, method, timeoutMs: 10_000 });
        return z.object({
            passkeys: z.array(z.object({
                id: z.string(),
                credential_id: z.string(),
                display_name: z.string(),
                device_type: z.string(),
                backed_up: z.boolean(),
                transports: z.array(z.string()),
                created_at: z.string(),
                // Backend returns null (not omitted) when the passkey has never been used.
                last_used_at: z.string().nullable().optional(),
            })),
        }).parse(data).passkeys;
    }

    async updatePasskey(passkeyId: string, displayName: string): Promise<void> {
        const path = `/auth/passkeys/${passkeyId}`;
        const method = "PATCH" as const;
        await this.client().request({
            path,
            method,
            body: { display_name: displayName },
            timeoutMs: 10_000,
        });
    }

    async deletePasskey(passkeyId: string): Promise<void> {
        const path = `/auth/passkeys/${passkeyId}`;
        const method = "DELETE" as const;
        await this.client().request({ path, method, timeoutMs: 10_000 });
    }
}

export const api = new ApiClient();
