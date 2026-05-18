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
import { apiBaseUrl } from "@/lib/env";
import { getBrowserAuthToken } from "@/lib/auth-token";

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

async function api<T>(path: string, init?: RequestInit): Promise<T> {
    const base = apiBaseUrl();
    const token = getBrowserAuthToken();
    const res = await fetch(`${base}${path}`, {
        ...init,
        credentials: "include",
        headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
            ...init?.headers,
        },
    });
    if (!res.ok) {
        let detail = `Request failed (${res.status})`;
        try {
            const j = await res.json();
            detail = j?.error?.message || j?.detail || detail;
        } catch {
            // ignore
        }
        throw new Error(detail);
    }
    return (await res.json()) as T;
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
        queryFn: () => api<{ trunks: SipTrunkRow[] }>("/telephony/sip/trunks"),
        select: (d) => d.trunks ?? [],
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
