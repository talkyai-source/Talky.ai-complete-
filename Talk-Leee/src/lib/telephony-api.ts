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

export interface SipTrunkTestResult {
    ok: boolean;
    latency_ms?: number;
    transport?: "udp" | "tcp" | "tls";
    target?: string;
    error?: string | null;
    detail?: string | null;
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
    created_at: string;
}

export interface SipTrunkTestResponse extends SipTrunkTestResult {
    tested_at: string;
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
    return await sharedApi.request<T>({ path, method, body, headers: init?.headers });
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
        queryFn: () => api<ProvidersListResponse>("/telephony/providers"),
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

export function useActivateSipTrunk() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: (id: string) =>
            api<SipTrunkRow>(`/telephony/sip/trunks/${id}/activate`, { method: "POST" }),
        onSuccess: () => {
            void qc.invalidateQueries({ queryKey: telephonyKeys.sipTrunks });
        },
    });
}

export function useDeactivateSipTrunk() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: (id: string) =>
            api<SipTrunkRow>(`/telephony/sip/trunks/${id}/deactivate`, { method: "POST" }),
        onSuccess: () => {
            void qc.invalidateQueries({ queryKey: telephonyKeys.sipTrunks });
        },
    });
}

export function useTestSipTrunk() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: (id: string) =>
            api<SipTrunkTestResponse>(`/telephony/sip/trunks/${id}/test`, { method: "POST" }),
        onSuccess: () => {
            void qc.invalidateQueries({ queryKey: telephonyKeys.sipTrunks });
        },
    });
}

export function useDeleteSipTrunk() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: (id: string) =>
            api<{ ok: boolean }>(`/telephony/sip/trunks/${id}`, { method: "DELETE" }),
        onSuccess: () => {
            void qc.invalidateQueries({ queryKey: telephonyKeys.sipTrunks });
        },
    });
}
