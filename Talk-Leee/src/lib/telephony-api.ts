"use client";

/**
 * Hooks for the Settings → Telephony tab.
 *
 * Two pieces:
 *   - Cloud provider credentials (Twilio, Vonage) via /telephony/providers
 *   - Local PBX / SIP trunks via existing /telephony/sip/trunks
 *
 * All mutations invalidate the relevant list queries on success so the
 * UI reflects server state immediately.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api as sharedApi } from "@/lib/api";
import { ApiClientError, isApiClientError } from "@/lib/http-client";
import { z } from "zod";

// ----- Types ----------------------------------------------------------------

export type TelephonyProvider = "twilio" | "vonage";
export type ActiveProvider = TelephonyProvider | "sip" | "none";

export interface TestResult {
    ok: boolean;
    latency_ms?: number;
    error?: string | null;
    status_code?: number | null;
    account_status?: string | null;
    friendly_name?: string | null;
    check_scope?: "provider_account" | "sdk_initialization" | null;
}

export interface ProviderAvailability {
    activation_allowed: boolean;
    qualification_only: boolean;
    reason_code?: string | null;
    reason: string;
}

export interface ProviderRow {
    provider: TelephonyProvider;
    label?: string | null;
    from_number?: string | null;
    status: "active" | "inactive" | "failed";
    last_tested_at?: string | null;
    last_test_result?: TestResult | null;
    has_credentials: boolean;
}

export interface ProvidersListResponse {
    active: ActiveProvider;
    providers: ProviderRow[];
    availability?: Partial<Record<TelephonyProvider, ProviderAvailability>>;
}

const providerAvailabilitySchema = z.object({
    activation_allowed: z.boolean(), qualification_only: z.boolean(),
    reason_code: z.string().nullable().optional(), reason: z.string(),
});
const providerCheckSchema = z.object({
    ok: z.boolean(), latency_ms: z.number().finite().optional(), error: z.string().nullable().optional(),
    status_code: z.number().nullable().optional(), account_status: z.string().nullable().optional(),
    friendly_name: z.string().nullable().optional(),
    check_scope: z.enum(["provider_account", "sdk_initialization"]).nullable().optional(),
});
const providersListSchema = z.object({
    active: z.enum(["twilio", "vonage", "sip", "none"]),
    providers: z.array(z.object({
        provider: z.enum(["twilio", "vonage"]), status: z.enum(["active", "inactive", "failed"]),
        label: z.string().nullable().optional(), from_number: z.string().nullable().optional(),
        last_tested_at: z.string().nullable().optional(), last_test_result: providerCheckSchema.nullable().optional(),
        has_credentials: z.boolean(),
    })),
    // Older API responses remain readable, but cannot authorize activation.
    availability: z.object({ twilio: providerAvailabilitySchema.optional(), vonage: providerAvailabilitySchema.optional() }).optional(),
});

export function parseProvidersResponse(value: unknown): ProvidersListResponse {
    return providersListSchema.parse(value);
}

export interface TwilioCredentials {
    account_sid: string;
    auth_token: string;
}

export interface VonageCredentials {
    api_key: string;
    api_secret: string;
    app_id?: string;
    private_key?: string;
}

export type ProviderCredentials = TwilioCredentials | VonageCredentials;

/**
 * One check inside a line test. `key` is "address", "reachability", "credentials"
 * or "registration" today; `ok: null` means skipped or inconclusive.
 */
export interface SipTestStep {
    key: string;
    ok: boolean | null;
    label: string;
    detail: string;
    sip_code?: number | string | null;
}

export type SipTestStatus =
    | "connected"
    | "credentials_rejected"
    | "unreachable"
    | "not_registered"
    | "reachable";

export interface SipTrunkTestResult {
    ok: boolean;
    latency_ms?: number;
    transport?: "udp" | "tcp" | "tls";
    target?: string;
    error?: string | null;
    detail?: string | null;
    sip_code?: string | number | null;
    timeout_code?: number | null;
    inconclusive?: boolean;
    /** Absent on results saved before the step-by-step test existed. */
    steps?: SipTestStep[];
    /** One plain sentence about the outcome; absent on older saved results. */
    verdict?: string | null;
    status?: SipTestStatus | null;
}

export interface SipTrunkRow {
    id: string;
    tenant_id: string;
    trunk_name: string;
    sip_domain: string;
    port: number;
    transport: "udp" | "tcp" | "tls";
    direction: "inbound" | "outbound" | "both";
    is_active: boolean;
    auth_username?: string | null;
    auth_configured: boolean;
    metadata: Record<string, unknown>;
    last_tested_at?: string | null;
    last_test_result?: SipTrunkTestResult | null;
    live_registration_status?: string | null;
    live_status_detail?: string | null;
    live_status_checked_at?: string | null;
    runtime_ready: boolean;
    /** Missing on older responses; never infer inbound admission from outbound health. */
    inbound_runtime_ready?: boolean;
    runtime_status_code: string;
    runtime_status_detail: string;
    created_at: string;
}

export interface SipTrunkTestResponse extends SipTrunkTestResult {
    tested_at: string;
}

/** What DELETE /telephony/sip/trunks/{id} reports. Counts are absent on a replayed or older response. */
export interface SipTrunkDeleteResult {
    id: string;
    deleted: boolean;
    /** "archived" when call history or inbound bindings are kept; "removed" when nothing referenced the line. */
    mode?: "removed" | "archived";
    archived_inbound_numbers?: number;
    unpinned_campaigns?: number;
    /** Inbound campaigns paused because the deleted line was the one that answered their number. */
    paused_inbound_campaigns?: number;
}

// ----- Reading the server's own explanation of a failure ---------------------
//
// The SIP endpoints report failures as RFC 9457 `application/problem+json`
// ({ title, detail }). The shared HTTP client only parses bodies whose
// content-type contains "application/json", so for these responses it reports
// the generic message "Request failed" and keeps the raw JSON text as `details`.
// That hid WHY a delete, save or test was refused. This puts the server's own
// `detail` (or the field messages of a 422) back into the error message.

const FIELD_LABELS: Record<string, string> = {
    sip_domain: "provider address",
    trunk_name: "line name",
    auth_username: "username",
    auth_password: "password",
    outbound_proxy: "outbound proxy",
    auth_realm: "auth realm",
    caller_id: "number to show",
    register_interval: "register interval",
    dtmf_mode: "DTMF mode",
};

function readProblem(details: unknown): string | null {
    let value = details;
    if (typeof value === "string") {
        try {
            value = JSON.parse(value) as unknown;
        } catch {
            return null;
        }
    }
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    const { detail, title } = value as { detail?: unknown; title?: unknown };
    for (const candidate of [detail, title]) {
        if (typeof candidate === "string" && candidate.trim()) return candidate.trim();
    }
    return null;
}

function readFieldProblems(details: unknown): string | null {
    if (!Array.isArray(details)) return null;
    const messages: string[] = [];
    for (const item of details) {
        if (!item || typeof item !== "object") continue;
        const { msg, loc } = item as { msg?: unknown; loc?: unknown };
        if (typeof msg !== "string" || !msg.trim()) continue;
        let text = msg.replace(/^Value error,\s*/i, "").trim();
        for (const [name, label] of Object.entries(FIELD_LABELS)) text = text.replace(new RegExp(`\\b${name}\\b`, "g"), label);
        const field = Array.isArray(loc) ? FIELD_LABELS[String(loc[loc.length - 1])] : undefined;
        if (field && !text.toLowerCase().includes(field)) text = `${field}: ${text}`;
        text = text.charAt(0).toUpperCase() + text.slice(1);
        if (!messages.includes(text)) messages.push(text);
    }
    return messages.length ? messages.slice(0, 3).join(" ") : null;
}

/**
 * Returns `err` with the server's explanation as its message when the shared
 * client could only report "Request failed"; any other value is returned as is.
 */
export function surfaceProblemDetail(err: unknown): unknown {
    if (!isApiClientError(err)) return err;
    const message = readProblem(err.details) ?? readFieldProblems(err.details);
    if (!message || message === err.message) return err;
    return new ApiClientError({
        status: err.status,
        code: err.code,
        message,
        details: err.details,
        retryAfterMs: err.retryAfterMs,
        requestId: err.requestId,
        url: err.url,
        method: err.method,
    });
}

// ----- Fetch helper ---------------------------------------------------------
//
// Phase 5 universal-auth-state: delegates to the shared `api` client so
// requests participate in refresh-on-401, single-flight refresh dedup,
// fresh-login grace, and the unified session-expired redirect latch.
// Public contract preserved: this helper throws on non-2xx with the
// backend's detail message (or "Request failed (N)" fallback) — the
// shared client's ApiClientError carries those fields.

type ApiInit = {
    method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
    body?: string;
    headers?: Record<string, string>;
};

async function api<T>(path: string, init?: ApiInit): Promise<T> {
    const method = (init?.method as "GET" | "POST" | "PUT" | "PATCH" | "DELETE" | undefined) ?? "GET";
    let body: unknown;
    if (init?.body !== undefined) {
        try {
            body = JSON.parse(init.body);
        } catch {
            body = init.body;
        }
    }
    try {
        return await sharedApi.request<T>({ path, method, body, headers: init?.headers });
    } catch (err) {
        throw surfaceProblemDetail(err);
    }
}

// ----- Query keys -----------------------------------------------------------

export const telephonyKeys = {
    providers: ["telephony", "providers"] as const,
    sipTrunks: ["telephony", "sip-trunks"] as const,
};

// ----- Provider hooks -------------------------------------------------------

export function useTelephonyProviders() {
    return useQuery({
        queryKey: telephonyKeys.providers,
        queryFn: async () => parseProvidersResponse(await api<unknown>("/telephony/providers")),
    });
}

export function useSaveTelephonyProvider() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: async (input: {
            provider: TelephonyProvider;
            credentials: ProviderCredentials;
            from_number?: string;
            label?: string;
        }) => {
            return api<ProviderRow>(
                `/telephony/providers/${input.provider}`,
                {
                    method: "PUT",
                    body: JSON.stringify({
                        credentials: input.credentials,
                        from_number: input.from_number,
                        label: input.label,
                    }),
                },
            );
        },
        onSuccess: () => {
            void qc.invalidateQueries({ queryKey: telephonyKeys.providers });
        },
    });
}

export function useDeleteTelephonyProvider() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: (provider: TelephonyProvider) =>
            api<{ ok: boolean }>(`/telephony/providers/${provider}`, { method: "DELETE" }),
        onSuccess: () => {
            void qc.invalidateQueries({ queryKey: telephonyKeys.providers });
        },
    });
}

export function useTestTelephonyProvider() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: (provider: TelephonyProvider) =>
            api<TestResult>(`/telephony/providers/${provider}/test`, { method: "POST" }),
        onSuccess: () => {
            void qc.invalidateQueries({ queryKey: telephonyKeys.providers });
        },
    });
}

export function useActivateTelephonyProvider() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: (provider: ActiveProvider) =>
            api<{ active: ActiveProvider }>("/telephony/providers/activate", {
                method: "POST",
                body: JSON.stringify({ provider }),
            }),
        onSuccess: () => {
            void qc.invalidateQueries({ queryKey: telephonyKeys.providers });
        },
    });
}

// ----- SIP trunk hooks (existing /telephony/sip/trunks endpoints) ----------

export function useSipTrunks() {
    return useQuery({
        queryKey: telephonyKeys.sipTrunks,
        // The backend returns a BARE array (response_model=list[SIPTrunkResponse]).
        // Tolerate both a bare array and a {trunks:[]} wrapper so the card renders
        // regardless — the .trunks-on-a-bare-array bug hid EVERY trunk before.
        queryFn: () => api<SipTrunkRow[] | { trunks: SipTrunkRow[] }>("/telephony/sip/trunks"),
        select: (d) => (Array.isArray(d) ? d : (d?.trunks ?? [])),
        refetchInterval: 10000,
    });
}

export interface SipTrunkInput {
    trunk_name: string;
    sip_domain: string;
    port: number;
    transport: "udp" | "tcp" | "tls";
    direction: "inbound" | "outbound" | "both";
    auth_username?: string;
    auth_password?: string;
    metadata?: Record<string, unknown>;
}

export function useCreateSipTrunk() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: (input: SipTrunkInput) => {
            const idempotencyKey =
                typeof crypto !== "undefined" && crypto.randomUUID
                    ? crypto.randomUUID()
                    : `${Date.now()}-${Math.random()}`;
            return api<SipTrunkRow>("/telephony/sip/trunks", {
                method: "POST",
                headers: { "Idempotency-Key": idempotencyKey },
                body: JSON.stringify(input),
            });
        },
        onSuccess: () => {
            void qc.invalidateQueries({ queryKey: telephonyKeys.sipTrunks });
        },
    });
}

export function useUpdateSipTrunk() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: (input: { id: string; patch: Partial<SipTrunkInput> & { clear_auth?: boolean } }) => {
            const idempotencyKey =
                typeof crypto !== "undefined" && crypto.randomUUID
                    ? crypto.randomUUID()
                    : `${Date.now()}-${Math.random()}`;
            return api<SipTrunkRow>(`/telephony/sip/trunks/${input.id}`, {
                method: "PATCH",
                headers: { "Idempotency-Key": idempotencyKey },
                body: JSON.stringify(input.patch),
            });
        },
        onSuccess: () => {
            void qc.invalidateQueries({ queryKey: telephonyKeys.sipTrunks });
        },
    });
}

// ----- Where outbound calls really go out from ------------------------------

export interface OutboundRoute {
    ready: boolean;
    reason_code: string;
    reason?: string | null;
    caller_id?: string | null;
    trunk_id?: string | null;
    trunk_name?: string | null;
}

/**
 * The line and number the dialer uses now, from the same preflight as campaign
 * launch. A trunk's stored caller ID is not always the number dialled (the
 * dialer can fall back to the account's calling-rules number). Keyed under
 * sipTrunks so every trunk or pool change refreshes it.
 */
export function useOutboundRoute() {
    return useQuery({
        queryKey: [...telephonyKeys.sipTrunks, "outbound-route"] as const,
        queryFn: () => api<OutboundRoute>("/telephony/sip/trunks/outbound-route"),
        refetchInterval: 10000,
    });
}

// ----- Shared trunk pool: allot a registered pool account to this tenant ----

export interface PoolTrunkItem {
    id: string;
    label: string;
    caller_id?: string | null;
    registration_status?: string | null;
    runtime_ready: boolean;
    runtime_status_detail: string;
}

export interface PoolAssignment {
    pool_trunk_id?: string | null;
    label?: string | null;
    caller_id?: string | null;
}

export function usePoolTrunks() {
    return useQuery({
        queryKey: ["telephony", "pool-trunks"] as const,
        queryFn: () => api<PoolTrunkItem[]>("/telephony/sip/trunks/pool"),
        refetchInterval: 15000,
    });
}

export function usePoolAssignment() {
    return useQuery({
        queryKey: ["telephony", "pool-assignment"] as const,
        queryFn: () => api<PoolAssignment>("/telephony/sip/trunks/pool-assignment"),
    });
}

export function useSetPoolAssignment() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: (poolTrunkId: string | null) =>
            api<PoolAssignment>("/telephony/sip/trunks/pool-assignment", {
                method: "PUT",
                body: JSON.stringify({ pool_trunk_id: poolTrunkId }),
            }),
        onSuccess: () => {
            void qc.invalidateQueries({ queryKey: ["telephony", "pool-assignment"] });
            void qc.invalidateQueries({ queryKey: telephonyKeys.sipTrunks });
        },
    });
}

function newIdempotencyKey(): string {
    return typeof crypto !== "undefined" && crypto.randomUUID
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random()}`;
}

export function useActivateSipTrunk() {
    const qc = useQueryClient();
    return useMutation({
        // The activate/deactivate endpoints REQUIRE an Idempotency-Key header
        // (400 "Idempotency Key Required" without it) — same as create/update.
        // Missing it here made every Active toggle silently 400 → dead buttons.
        mutationFn: (id: string) =>
            api<SipTrunkRow>(`/telephony/sip/trunks/${id}/activate`, {
                method: "POST",
                headers: { "Idempotency-Key": newIdempotencyKey() },
            }),
        onSuccess: () => {
            void qc.invalidateQueries({ queryKey: telephonyKeys.sipTrunks });
        },
    });
}

export function useDeactivateSipTrunk() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: (id: string) =>
            api<SipTrunkRow>(`/telephony/sip/trunks/${id}/deactivate`, {
                method: "POST",
                headers: { "Idempotency-Key": newIdempotencyKey() },
            }),
        onSuccess: () => {
            void qc.invalidateQueries({ queryKey: telephonyKeys.sipTrunks });
        },
    });
}

export function useTestSipTrunk() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: (id: string) =>
            api<SipTrunkTestResponse>(`/telephony/sip/trunks/${id}/test`, {
                method: "POST",
                headers: { "Idempotency-Key": newIdempotencyKey() },
            }),
        onSuccess: () => {
            void qc.invalidateQueries({ queryKey: telephonyKeys.sipTrunks });
        },
    });
}

export function useDeleteSipTrunk() {
    const qc = useQueryClient();
    return useMutation({
        // The server deactivates the line, unpins campaigns and disconnects
        // inbound numbers itself, so the caller deletes directly. Invalidating
        // the sipTrunks family also refreshes the outbound-route summary.
        mutationFn: (id: string) =>
            api<SipTrunkDeleteResult>(`/telephony/sip/trunks/${id}`, { method: "DELETE", headers: { "Idempotency-Key": newIdempotencyKey() } }),
        onSuccess: () => {
            void qc.invalidateQueries({ queryKey: telephonyKeys.sipTrunks });
        },
    });
}
