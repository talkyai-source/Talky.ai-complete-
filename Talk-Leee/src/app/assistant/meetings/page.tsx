"use client";

import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { RouteGuard } from "@/components/guards/route-guard";
import { useMeetings } from "@/lib/api-hooks";
import { isApiClientError } from "@/lib/http-client";

function formatError(err: unknown) {
    if (isApiClientError(err)) return err.message;
    return err instanceof Error ? err.message : "Request failed";
}

export default function AssistantMeetingsPage() {
    const q = useMeetings();
    const items = q.data?.items ?? [];

    return (
        <DashboardLayout title="Meetings" description="Assistant meetings overview.">
            <RouteGuard title="Meetings" description="Assistant meetings overview." requiredConnectors={["calendar"]}>
                <div className="mx-auto w-full max-w-5xl space-y-6">
                    {q.isLoading ? (
                        <div className="flex items-center justify-center h-64">
                            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-foreground/60" />
                        </div>
                    ) : q.isError ? (
                        <div className="rounded-2xl border border-red-500/30 bg-background/70 p-6 text-sm text-red-500">
                            {formatError(q.error)}
                        </div>
                    ) : items.length === 0 ? (
                        <div className="rounded-2xl border border-border bg-background/70 p-6 text-sm text-muted-foreground">
                            No meetings found.
                        </div>
                    ) : (
                        <div className="space-y-2">
                            {items.map((m) => (
                                <div key={m.id} className="rounded-2xl border border-border bg-background/70 p-4">
                                    <div className="flex items-start justify-between gap-3">
                                        <div className="min-w-0">
                                            <div className="text-sm font-semibold text-foreground truncate">{m.title}</div>
                                            <div className="mt-1 text-xs text-muted-foreground">{m.status}</div>
                                            <div className="mt-2 text-xs text-muted-foreground truncate">
                                                Participants: {m.participants.join(", ")}
                                            </div>
                                        </div>
                                        <div className="text-xs text-muted-foreground text-right">
                                            <div>{new Date(m.startTime).toLocaleString()}</div>
                                            <div className="opacity-70">{new Date(m.endTime).toLocaleString()}</div>
                                        </div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            </RouteGuard>
        </DashboardLayout>
    );
}
