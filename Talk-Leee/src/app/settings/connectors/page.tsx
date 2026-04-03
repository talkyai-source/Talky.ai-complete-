"use client";

import { useEffect, useMemo, useRef, type ComponentType } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { Boxes, CalendarDays, HardDrive, Mail, UsersRound } from "lucide-react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { ConnectorCard } from "@/components/connectors/connector-card";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { queryKeys, useConnectorProviders, useConnectors } from "@/lib/api-hooks";
import { backendApi } from "@/lib/backend-api";
import { isApiClientError } from "@/lib/http-client";
import type { Connector } from "@/lib/models";
import { notificationsStore } from "@/lib/notifications";
import { cn } from "@/lib/utils";
import { normalizeConnectorStatus, pickPreferredConnector, summarizeConnectorStatuses } from "@/lib/connectors-utils";

function formatError(err: unknown) {
    if (isApiClientError(err)) return err.message;
    return err instanceof Error ? err.message : "Request failed";
}

type ProviderType = "calendar" | "email" | "crm" | "drive";

function providerVisual(input: { type: string; provider: string }): { accent: string; icon: ComponentType<{ className?: string }> } {
    if (input.provider === "google_calendar") {
        return { accent: "border-sky-500/20 bg-gradient-to-br from-sky-500/10 to-indigo-500/5", icon: CalendarDays };
    }
    if (input.provider === "outlook_calendar") {
        return { accent: "border-blue-500/20 bg-gradient-to-br from-blue-500/10 to-cyan-500/5", icon: CalendarDays };
    }
    if (input.provider === "gmail" || input.type === "email") {
        return { accent: "border-emerald-500/20 bg-gradient-to-br from-emerald-500/10 to-cyan-500/5", icon: Mail };
    }
    if (input.provider === "hubspot" || input.type === "crm") {
        return { accent: "border-fuchsia-500/20 bg-gradient-to-br from-fuchsia-500/10 to-orange-500/5", icon: UsersRound };
    }
    if (input.provider === "google_drive" || input.type === "drive") {
        return { accent: "border-amber-500/20 bg-gradient-to-br from-amber-500/10 to-orange-500/5", icon: HardDrive };
    }
    return { accent: "border-slate-500/20 bg-gradient-to-br from-slate-500/10 to-zinc-500/5", icon: Boxes };
}

export default function ConnectorsPage() {
    const qc = useQueryClient();
    const router = useRouter();
    const searchParams = useSearchParams();
    const providersQ = useConnectorProviders();
    const connectorsQ = useConnectors();
    const seenEvents = useRef(new Set<string>());
    const lastRefreshEventRaw = useRef<string | null>(null);

    useEffect(() => {
        const handleUpdated = (data: unknown) => {
            if (!data || typeof data !== "object") return;
            const obj = data as Record<string, unknown>;
            if (obj.type !== "connectors:updated") return;

            const eventId = typeof obj.eventId === "string" ? obj.eventId : undefined;
            if (eventId) {
                if (seenEvents.current.has(eventId)) return;
                seenEvents.current.add(eventId);
            }

            const ok = Boolean(obj.ok);
            const message = typeof obj.message === "string" ? obj.message : undefined;
            notificationsStore.create({
                type: ok ? "success" : "error",
                title: ok ? "Connector connected" : "Connector connection failed",
                message: message ?? (ok ? "Connection completed successfully." : "Authorization failed. Please try again."),
            });

            void qc.invalidateQueries({ queryKey: queryKeys.connectors() });
            void qc.invalidateQueries({ queryKey: queryKeys.connectorStatuses() });
        };

        const onMessage = (event: MessageEvent) => {
            if (event.origin !== window.location.origin) return;
            handleUpdated(event.data as unknown);
        };
        window.addEventListener("message", onMessage);

        const bc = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("connectors") : null;
        const onBc = (event: MessageEvent) => {
            handleUpdated(event.data as unknown);
        };
        bc?.addEventListener("message", onBc);

        const onStorage = (e: StorageEvent) => {
            if (e.key === "connectors.refresh.event") {
                if (typeof e.newValue === "string" && e.newValue.trim().length > 0) {
                    try {
                        handleUpdated(JSON.parse(e.newValue) as unknown);
                    } catch {
                    }
                }
            }
            if (e.key !== "connectors.refresh" && e.key !== "connectors.refresh.event") return;
            void qc.invalidateQueries({ queryKey: queryKeys.connectors() });
            void qc.invalidateQueries({ queryKey: queryKeys.connectorStatuses() });
        };
        window.addEventListener("storage", onStorage);

        const pollId = window.setInterval(() => {
            let raw: string | null = null;
            try {
                raw = window.localStorage.getItem("connectors.refresh.event");
            } catch {
                return;
            }
            if (!raw || raw === lastRefreshEventRaw.current) return;
            lastRefreshEventRaw.current = raw;
            try {
                handleUpdated(JSON.parse(raw) as unknown);
            } catch {
            }
            void qc.invalidateQueries({ queryKey: queryKeys.connectors() });
            void qc.invalidateQueries({ queryKey: queryKeys.connectorStatuses() });
        }, 500);

        return () => {
            window.removeEventListener("message", onMessage);
            bc?.removeEventListener("message", onBc);
            bc?.close();
            window.removeEventListener("storage", onStorage);
            window.clearInterval(pollId);
        };
    }, [qc]);

    const providers = useMemo(
        () =>
            [...(providersQ.data?.items ?? [])].sort(
                (a, b) => a.type.localeCompare(b.type) || a.name.localeCompare(b.name) || a.provider.localeCompare(b.provider)
            ),
        [providersQ.data?.items]
    );

    const connectorsByProvider = useMemo(() => {
        const grouped = new Map<string, Connector[]>();
        for (const connector of connectorsQ.data?.items ?? []) {
            if (!connector.provider) continue;
            const existing = grouped.get(connector.provider) ?? [];
            existing.push(connector);
            grouped.set(connector.provider, existing);
        }

        const result = new Map<string, Connector>();
        for (const [provider, items] of grouped.entries()) {
            const best = pickPreferredConnector(items);
            if (best) result.set(provider, best);
        }
        return result;
    }, [connectorsQ.data?.items]);

    const byType = useMemo(() => {
        const map = new Map<ProviderType, ReturnType<typeof summarizeConnectorStatuses>[number]>();
        for (const item of summarizeConnectorStatuses(connectorsQ.data?.items ?? [])) {
            if (item.type === "calendar" || item.type === "email" || item.type === "crm" || item.type === "drive") {
                map.set(item.type, item);
            }
        }
        return map;
    }, [connectorsQ.data?.items]);

    const required = useMemo<ProviderType[]>(() => {
        const raw = searchParams.get("required") ?? "";
        const list = raw
            .split(",")
            .map((x) => x.trim())
            .filter(Boolean);
        const allowed: ProviderType[] = ["calendar", "email", "crm", "drive"];
        return list.filter((x): x is ProviderType => (allowed as string[]).includes(x));
    }, [searchParams]);

    const next = useMemo(() => searchParams.get("next") ?? "", [searchParams]);

    const requirementsMet = useMemo(() => {
        if (required.length === 0) return false;
        return required.every((type) => byType.get(type)?.status === "connected");
    }, [byType, required]);

    const pageError = providersQ.isError ? providersQ.error : connectorsQ.isError ? connectorsQ.error : null;

    return (
        <DashboardLayout title="Connectors" description="Configure integrations and manage connector accounts.">
            <div className="mx-auto w-full max-w-5xl space-y-6">
                {required.length > 0 ? (
                    <div className={cn("rounded-2xl border p-4", requirementsMet ? "border-emerald-500/30 bg-emerald-500/10" : "border-amber-500/30 bg-amber-500/10")}>
                        <div className="flex flex-wrap items-center justify-between gap-3">
                            <div className="min-w-0">
                                <div className={cn("text-sm font-semibold", requirementsMet ? "text-emerald-100" : "text-amber-100")}>
                                    {requirementsMet ? "Connectors ready" : "Connectors required"}
                                </div>
                                <div className={cn("mt-1 text-sm", requirementsMet ? "text-emerald-100/80" : "text-amber-100/80")}>
                                    Required for this feature: {required.join(", ")}.
                                </div>
                            </div>
                            {next ? (
                                <div className="flex gap-2">
                                    <Button type="button" variant="outline" onClick={() => router.push(next)} disabled={!requirementsMet}>
                                        Continue
                                    </Button>
                                </div>
                            ) : null}
                        </div>
                    </div>
                ) : null}
                <Card>
                    <CardHeader>
                        <CardTitle>Providers</CardTitle>
                        <CardDescription>Every available connector is managed here. Connect accounts, review status, and disconnect providers from one place.</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        {pageError ? (
                            <div className="rounded-2xl border border-red-500/30 bg-background/70 p-4 text-sm text-red-500">{formatError(pageError)}</div>
                        ) : null}

                        {connectorsQ.isLoading && providers.length > 0 ? (
                            <div className="rounded-2xl border border-border/60 bg-background/70 p-4 text-sm text-muted-foreground">Refreshing connector states…</div>
                        ) : null}

                        {!providersQ.isLoading && providers.length === 0 ? (
                            <div className="rounded-2xl border border-border/60 bg-background/70 p-4 text-sm text-muted-foreground">No connector providers are available.</div>
                        ) : null}

                        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                            {providers.map((provider) => {
                                const connector = connectorsByProvider.get(provider.provider);
                                const visual = providerVisual(provider);
                                const status = connector ? normalizeConnectorStatus(connector.status) : "disconnected";

                                return (
                                    <ConnectorCard
                                        key={provider.provider}
                                        cardKey={provider.provider}
                                        type={provider.type}
                                        name={provider.name}
                                        description={provider.description}
                                        icon={visual.icon}
                                        accentClassName={visual.accent}
                                        status={status}
                                        lastSync={null}
                                        provider={connector?.accountEmail ?? null}
                                        errorMessage={null}
                                        authorizeConnector={() =>
                                            backendApi.connectors.authorizeProvider({
                                                type: provider.type,
                                                provider: provider.provider,
                                                name: provider.name,
                                            })
                                        }
                                        disconnectConnector={
                                            connector ? () => backendApi.connectors.disconnectById({ connectorId: connector.id }) : undefined
                                        }
                                    />
                                );
                            })}
                        </div>
                    </CardContent>
                </Card>
            </div>
        </DashboardLayout>
    );
}
