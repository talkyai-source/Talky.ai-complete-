"use client";

import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Loader2, ShieldAlert } from "lucide-react";
import { api, type MeResponse } from "@/lib/api";
import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";

type SuspensionScope = "partner" | "tenant" | null;

type SuspensionState = {
    partnerId: string | null;
    tenantId: string | null;
    partnerStatus: "active" | "suspended";
    tenantStatus: "active" | "suspended";
    suspended: boolean;
    scope: SuspensionScope;
    reason: string | null;
    suspendedAt: string | null;
};

type ScopedSuspensionOverride = {
    targetType: "partner" | "tenant";
    targetId: string;
    status: "active" | "suspended";
    reason?: string | null;
};

type SuspensionContextValue = {
    state: SuspensionState;
    loading: boolean;
    refresh: () => Promise<void>;
    applyScopedUpdate: (input: ScopedSuspensionOverride) => void;
};

const defaultState: SuspensionState = {
    partnerId: null,
    tenantId: null,
    partnerStatus: "active",
    tenantStatus: "active",
    suspended: false,
    scope: null,
    reason: null,
    suspendedAt: null,
};

const SuspensionStateContext = createContext<SuspensionContextValue | undefined>(undefined);

function deriveSuspensionState(me: Partial<MeResponse> | null | undefined): SuspensionState {
    if (!me) return defaultState;
    const partnerStatus = me.partner_status === "suspended" ? "suspended" : "active";
    const tenantStatus = me.tenant_status === "suspended" ? "suspended" : "active";
    // The schema-level `suspended_scope` is a free-form string from the
    // backend — narrow it to the runtime SuspensionScope union here.
    const fallbackScope: SuspensionScope =
        me.suspended_scope === "tenant" || me.suspended_scope === "partner"
            ? me.suspended_scope
            : null;
    const scope: SuspensionScope =
        tenantStatus === "suspended"
            ? "tenant"
            : partnerStatus === "suspended"
              ? "partner"
              : fallbackScope;

    return {
        partnerId: me.partner_id?.trim() ? me.partner_id : null,
        tenantId: me.tenant_id?.trim() ? me.tenant_id : null,
        partnerStatus,
        tenantStatus,
        suspended: partnerStatus === "suspended" || tenantStatus === "suspended",
        scope,
        reason: me.suspension_reason?.trim() ? me.suspension_reason : null,
        suspendedAt: me.suspended_at?.trim() ? me.suspended_at : null,
    };
}

function mergeSuspensionState(serverState: SuspensionState, override: ScopedSuspensionOverride | null): SuspensionState {
    if (!override) return serverState;

    if (override.targetType === "partner" && serverState.partnerId === override.targetId) {
        const partnerStatus = override.status;
        const tenantStatus = serverState.tenantStatus;
        return {
            ...serverState,
            partnerStatus,
            suspended: partnerStatus === "suspended" || tenantStatus === "suspended",
            scope: tenantStatus === "suspended" ? "tenant" : partnerStatus === "suspended" ? "partner" : null,
            reason: override.reason ?? serverState.reason,
        };
    }

    if (override.targetType === "tenant" && serverState.tenantId === override.targetId) {
        const tenantStatus = override.status;
        const partnerStatus = serverState.partnerStatus;
        return {
            ...serverState,
            tenantStatus,
            suspended: partnerStatus === "suspended" || tenantStatus === "suspended",
            scope: tenantStatus === "suspended" ? "tenant" : partnerStatus === "suspended" ? "partner" : null,
            reason: override.reason ?? serverState.reason,
        };
    }

    return serverState;
}

export function SuspensionStateProvider({ children }: { children: React.ReactNode }) {
    const { user } = useAuth();
    const [override, setOverride] = useState<ScopedSuspensionOverride | null>(null);

    const statusQuery = useQuery({
        queryKey: ["accountSuspensionState", user?.id ?? "guest"],
        queryFn: () => api.getMe(),
        enabled: Boolean(user),
        refetchInterval: () => {
            if (typeof document === "undefined") return 30_000;
            if (document.visibilityState === "hidden") return false;
            return 30_000;
        },
        refetchOnReconnect: true,
        refetchOnWindowFocus: true,
        refetchOnMount: "always",
        staleTime: 0,
        retry: 1,
    });

    const serverState = useMemo(() => deriveSuspensionState(statusQuery.data ?? user), [statusQuery.data, user]);
    const state = useMemo(() => mergeSuspensionState(serverState, override), [override, serverState]);

    useEffect(() => {
        if (!user) {
            setOverride(null);
        }
    }, [user]);

    useEffect(() => {
        if (!override) return;
        if (mergeSuspensionState(serverState, override).suspended === serverState.suspended) {
            if (override.targetType === "partner" && serverState.partnerId === override.targetId && serverState.partnerStatus === override.status) {
                setOverride(null);
                return;
            }
            if (override.targetType === "tenant" && serverState.tenantId === override.targetId && serverState.tenantStatus === override.status) {
                setOverride(null);
            }
        }
    }, [override, serverState]);

    useEffect(() => {
        if (typeof BroadcastChannel === "undefined") return;
        const channel = new BroadcastChannel("account-suspension");
        channel.onmessage = () => {
            void statusQuery.refetch();
        };
        return () => channel.close();
    }, [statusQuery]);

    const value = useMemo<SuspensionContextValue>(
        () => ({
            state,
            loading: Boolean(user) && (statusQuery.isLoading || statusQuery.isFetching),
            refresh: async () => {
                await statusQuery.refetch();
            },
            applyScopedUpdate: (input) => {
                setOverride(input);
                try {
                    const channel = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("account-suspension") : null;
                    channel?.postMessage(input);
                    channel?.close();
                } catch {
                }
            },
        }),
        [state, statusQuery, user]
    );

    return <SuspensionStateContext.Provider value={value}>{children}</SuspensionStateContext.Provider>;
}

export function useSuspensionState() {
    const context = useContext(SuspensionStateContext);
    if (!context) {
        throw new Error("useSuspensionState must be used within a SuspensionStateProvider");
    }
    return context;
}

export function SuspensionBanner({ className }: { className?: string }) {
    const { state, loading } = useSuspensionState();

    if (!state.suspended && !loading) return null;

    return (
        <div
            className={cn(
                "rounded-2xl border px-4 py-3 shadow-sm",
                state.suspended ? "border-red-500/30 bg-red-500/10 text-red-50" : "border-amber-500/30 bg-amber-500/10 text-foreground",
                className
            )}
            role="status"
            aria-live="polite"
        >
            <div className="flex items-start gap-3">
                <div
                    className={cn(
                        "mt-0.5 flex h-9 w-9 items-center justify-center rounded-full border shrink-0",
                        state.suspended ? "border-red-500/30 bg-red-500/20 text-red-100" : "border-amber-500/30 bg-amber-500/20 text-amber-100"
                    )}
                >
                    {loading && !state.suspended ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : state.suspended ? <ShieldAlert className="h-4 w-4" aria-hidden /> : <AlertTriangle className="h-4 w-4" aria-hidden />}
                </div>
                <div className="min-w-0">
                    <div className="text-sm font-semibold text-foreground">{state.suspended ? "Account suspended" : "Refreshing account access"}</div>
                    <div className="mt-1 text-sm text-muted-foreground">
                        {state.suspended
                            ? `This ${state.scope === "partner" ? "partner" : "tenant"} account is suspended. Interactive actions are disabled until access is restored.`
                            : "Checking the latest account status from the backend before allowing protected actions."}
                    </div>
                    {state.reason ? <div className="mt-2 text-xs text-muted-foreground">Reason: {state.reason}</div> : null}
                </div>
            </div>
        </div>
    );
}
