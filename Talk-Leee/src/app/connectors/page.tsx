"use client";

import { useEffect, useMemo, useRef, type ComponentType } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { ConnectorCard } from "@/components/connectors/connector-card";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useConnectorStatuses, queryKeys } from "@/lib/api-hooks";
import { isApiClientError } from "@/lib/http-client";
import type { ConnectorProviderStatus } from "@/lib/models";
import { notificationsStore } from "@/lib/notifications";
import { cn } from "@/lib/utils";
import { CalendarDays, Mail, UsersRound, HardDrive } from "lucide-react";

function formatError(err: unknown) {
    if (isApiClientError(err)) return err.message;
    return err instanceof Error ? err.message : "Request failed";
}

type ProviderType = "calendar" | "email" | "crm" | "drive";

type ProviderCard = {
    type: ProviderType;
    name: string;
    description: string;
    accent: string;
    icon: ComponentType<{ className?: string }>;
};

const PROVIDERS: ProviderCard[] = [
    {
        type: "calendar",
        name: "Google Calendar",
        description: "Sync events for scheduling, reminders, and meeting context.",
        accent: "border-sky-500/20 bg-gradient-to-br from-sky-500/10 to-indigo-500/5",
        icon: CalendarDays,
    },
    {
        type: "email",
        name: "Gmail",
        description: "Connect inboxes for activity capture and follow-ups.",
        accent: "border-emerald-500/20 bg-gradient-to-br from-emerald-500/10 to-cyan-500/5",
        icon: Mail,
    },
    {
        type: "crm",
        name: "HubSpot",
        description: "Sync contacts and engagement history into your CRM.",
        accent: "border-fuchsia-500/20 bg-gradient-to-br from-fuchsia-500/10 to-purple-500/5",
        icon: UsersRound,
    },
    {
        type: "drive",
        name: "Google Drive",
        description: "Connect file storage for documents and recordings.",
        accent: "border-amber-500/20 bg-gradient-to-br from-amber-500/10 to-orange-500/5",
        icon: HardDrive,
    },
];

export default function ConnectorsPage() {
    const qc = useQueryClient();
    const router = useRouter();
    const searchParams = useSearchParams();
    const q = useConnectorStatuses();
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

            void qc.invalidateQueries({ queryKey: queryKeys.connectorStatuses() });
        };

        const onMessage = (event: MessageEvent) => {
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

    const byType = useMemo(() => {
        const map = new Map<ProviderType, ConnectorProviderStatus>();
        for (const item of q.data?.items ?? []) {
            if (item.type === "calendar" || item.type === "email" || item.type === "crm" || item.type === "drive") {
                map.set(item.type, item);
            }
        }
        return map;
    }, [q.data?.items]);

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
        const items = q.data?.items ?? [];
        return required.every((t) => items.find((x) => x.type === t)?.status === "connected");
    }, [q.data?.items, required]);

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
                        <CardDescription>Connect your accounts to sync data and automate workflows.</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        {q.isError ? (
                            <div className="rounded-2xl border border-red-500/30 bg-background/70 p-4 text-sm text-red-500">{formatError(q.error)}</div>
                        ) : null}

                        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                            {PROVIDERS.map((p) => {
                                const data = byType.get(p.type);
                                const Icon = p.icon;
                                const status = data?.status ?? "disconnected";

                                return (
                                    <ConnectorCard
                                        key={p.type}
                                        type={p.type}
                                        name={p.name}
                                        description={p.description}
                                        icon={Icon}
                                        accentClassName={p.accent}
                                        status={status}
                                        lastSync={data?.last_sync}
                                        provider={data?.provider}
                                        errorMessage={data?.error_message}
                                        oauthCallbackPath={`/connectors/${p.type}/callback`}
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
