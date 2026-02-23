"use client";

import Link from "next/link";
import { useMemo } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useTheme } from "@/components/providers/theme-provider";
import { useConnectorStatuses } from "@/lib/api-hooks";
import { useAuth } from "@/lib/auth-context";
import type { ConnectorProviderStatus } from "@/lib/models";

export type RequiredConnectorType = "calendar" | "email" | "crm" | "drive";

type ConnectorConnectionStatus = "connected" | "disconnected" | "expired" | "error";

type ConnectorIssue = {
    type: RequiredConnectorType;
    status: "missing" | ConnectorConnectionStatus;
    message: string;
};

function isConnectorConnectionStatus(status: string): status is ConnectorConnectionStatus {
    return status === "connected" || status === "disconnected" || status === "expired" || status === "error";
}

function connectorLabel(type: RequiredConnectorType) {
    if (type === "calendar") return "Calendar";
    if (type === "email") return "Email";
    if (type === "crm") return "CRM";
    return "Drive";
}

function connectorBlockedMessage(type: RequiredConnectorType, status: ConnectorIssue["status"]) {
    const base = `${connectorLabel(type)} connector`;
    if (status === "connected") return `${base} is connected.`;
    if (status === "disconnected") return `${base} is disconnected. Connect it to continue.`;
    if (status === "expired") return `${base} credentials have expired. Reconnect to refresh credentials.`;
    if (status === "error") return `${base} is in an error state. Reconnect to continue.`;
    return `${base} is not configured. Connect it to continue.`;
}

function issuesForRequirements(input: { required: RequiredConnectorType[]; statuses: ConnectorProviderStatus[] }): ConnectorIssue[] {
    const byType = new Map<string, ConnectorProviderStatus>();
    for (const s of input.statuses) byType.set(s.type, s);
    return input.required
        .map((t): ConnectorIssue => {
            const found = byType.get(t);
            if (!found) return { type: t, status: "missing", message: connectorBlockedMessage(t, "missing") };
            const status = found.status;
            if (!isConnectorConnectionStatus(status)) return { type: t, status: "missing", message: connectorBlockedMessage(t, "missing") };
            return { type: t, status, message: connectorBlockedMessage(t, status) };
        })
        .filter((x) => x.status !== "connected");
}

export function RouteGuard({
    title,
    description,
    requiredConnectors,
    children,
}: {
    title: string;
    description?: string;
    requiredConnectors?: RequiredConnectorType[];
    children: React.ReactNode;
}) {
    const { user, loading } = useAuth();
    const { theme } = useTheme();
    const isDark = theme === "dark";
    const pathname = usePathname();
    const searchParams = useSearchParams();

    const required = useMemo(() => requiredConnectors ?? [], [requiredConnectors]);
    const shouldCheckConnectors = Boolean(user) && required.length > 0;
    const statusesQ = useConnectorStatuses({ enabled: shouldCheckConnectors });

    const next = useMemo(() => {
        const base = pathname ?? "/";
        const query = searchParams.toString();
        return query.length > 0 ? `${base}?${query}` : base;
    }, [pathname, searchParams]);

    const requiredParam = useMemo(() => required.join(","), [required]);

    const connectorIssues = useMemo(() => {
        if (!shouldCheckConnectors) return [];
        return issuesForRequirements({ required, statuses: statusesQ.data?.items ?? [] });
    }, [required, shouldCheckConnectors, statusesQ.data?.items]);

    const shouldBlockOnConnectors = shouldCheckConnectors && (statusesQ.isLoading || statusesQ.isError || connectorIssues.length > 0);

    if (loading) {
        return (
            <div className="mx-auto w-full max-w-5xl px-4 py-10">
                <Card>
                    <CardHeader>
                        <CardTitle>{title}</CardTitle>
                        {description ? <CardDescription>{description}</CardDescription> : null}
                    </CardHeader>
                    <CardContent className="space-y-3">
                        <div className="h-4 w-1/2 animate-pulse rounded bg-foreground/10" />
                        <div className="h-4 w-2/3 animate-pulse rounded bg-foreground/10" />
                        <div className="h-10 w-32 animate-pulse rounded bg-foreground/10" />
                    </CardContent>
                </Card>
            </div>
        );
    }

    if (!user) {
        return (
            <div className="mx-auto w-full max-w-5xl px-4 py-10">
                <Card>
                    <CardHeader>
                        <CardTitle>Sign in required</CardTitle>
                        <CardDescription>You need to be authenticated to access this page.</CardDescription>
                    </CardHeader>
                    <CardContent className="flex flex-wrap items-center justify-between gap-3">
                        <div className="text-sm text-muted-foreground">Continue to login to proceed.</div>
                        <Button asChild>
                            <Link href={`/auth/login?next=${encodeURIComponent(next)}`}>Go to login</Link>
                        </Button>
                    </CardContent>
                </Card>
            </div>
        );
    }

    if (shouldBlockOnConnectors) {
        const href = `/settings/connectors?required=${encodeURIComponent(requiredParam)}&next=${encodeURIComponent(next)}`;

        if (isDark) {
            return (
                <div className="mx-auto w-full max-w-5xl px-4 py-10">
                    <div className="content-card">
                        <h2 className="mb-1 text-sm font-semibold text-foreground">Connector setup required</h2>
                        <p className="text-sm text-muted-foreground">Connect required providers to unlock this feature.</p>

                        <div className="mt-4 space-y-4">
                            {statusesQ.isLoading ? (
                                <div className="group rounded-2xl border border-border bg-muted/60 p-4 shadow-sm transition-[transform,background-color,border-color,box-shadow] duration-150 ease-out hover:-translate-y-0.5 hover:bg-background hover:shadow-md">
                                    <div className="space-y-3">
                                        <div className="h-4 w-2/3 animate-pulse rounded bg-foreground/10" />
                                        <div className="h-4 w-1/2 animate-pulse rounded bg-foreground/10" />
                                        <div className="h-4 w-3/5 animate-pulse rounded bg-foreground/10" />
                                    </div>
                                </div>
                            ) : statusesQ.isError ? (
                                <div className="group rounded-2xl border border-border bg-muted/60 p-4 shadow-sm transition-[transform,background-color,border-color,box-shadow] duration-150 ease-out hover:-translate-y-0.5 hover:bg-background hover:shadow-md">
                                    <div className="text-sm font-medium text-foreground">Failed to load connector status.</div>
                                    <div className="mt-3 flex flex-wrap gap-2">
                                        <Button
                                            type="button"
                                            variant="outline"
                                            onClick={() => void statusesQ.refetch()}
                                            className="border-teal-500/60 bg-teal-600 text-white hover:scale-[1.02] hover:bg-teal-700 hover:text-white active:scale-[0.99]"
                                        >
                                            Retry
                                        </Button>
                                        <Button
                                            asChild
                                            className="border-teal-500/60 bg-teal-600 text-white hover:scale-[1.02] hover:bg-teal-700 hover:text-white active:scale-[0.99]"
                                        >
                                            <Link href={href}>Open Connectors</Link>
                                        </Button>
                                    </div>
                                </div>
                            ) : connectorIssues.length > 0 ? (
                                <div className="space-y-3">
                                    <div className="text-sm text-muted-foreground">
                                        This page requires: {required.map((t) => connectorLabel(t)).join(", ")}.
                                    </div>
                                    <div className="space-y-2">
                                        {connectorIssues.map((x) => (
                                            <div
                                                key={x.type}
                                                className="group rounded-2xl border border-amber-500/20 bg-amber-500/10 p-4 text-sm text-amber-100 shadow-sm transition-[transform,box-shadow] duration-150 ease-out hover:-translate-y-0.5 hover:shadow-md"
                                            >
                                                <div className="font-semibold">{connectorLabel(x.type)}</div>
                                                <div className="mt-1 text-amber-100/80">{x.message}</div>
                                            </div>
                                        ))}
                                    </div>
                                    <div className="flex flex-wrap gap-2">
                                        <Button asChild className="hover:scale-[1.02] active:scale-[0.99]">
                                            <Link href={href}>Fix connectors</Link>
                                        </Button>
                                        <Button
                                            type="button"
                                            variant="outline"
                                            onClick={() => void statusesQ.refetch()}
                                            className="hover:scale-[1.02] active:scale-[0.99]"
                                        >
                                            Refresh status
                                        </Button>
                                    </div>
                                </div>
                            ) : (
                                <div className="text-sm text-muted-foreground">Checking connectors…</div>
                            )}
                        </div>
                    </div>
                </div>
            );
        }

        return (
            <div className="mx-auto w-full max-w-5xl px-4 py-10">
                <Card className={statusesQ.isError ? "border-red-500/30 bg-red-500/10" : undefined}>
                    <CardHeader>
                        <CardTitle>Connector setup required</CardTitle>
                        <CardDescription>Connect required providers to unlock this feature.</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        {statusesQ.isLoading ? (
                            <div className="space-y-3">
                                <div className="h-4 w-2/3 animate-pulse rounded bg-foreground/10" />
                                <div className="h-4 w-1/2 animate-pulse rounded bg-foreground/10" />
                                <div className="h-4 w-3/5 animate-pulse rounded bg-foreground/10" />
                            </div>
                        ) : statusesQ.isError ? (
                            <div className="rounded-xl border border-border bg-background p-4 text-sm text-foreground shadow-sm transition-[transform,box-shadow] duration-150 ease-out hover:-translate-y-0.5 hover:scale-[1.01] hover:shadow-md">
                                <div className="font-semibold text-destructive">Failed to load connector status.</div>
                                <div className="mt-3 flex flex-wrap gap-2">
                                    <Button
                                        type="button"
                                        variant="outline"
                                        onClick={() => void statusesQ.refetch()}
                                        className="border-teal-500/60 bg-teal-600 text-white hover:scale-[1.02] hover:bg-teal-700 hover:text-white active:scale-[0.99]"
                                    >
                                        Retry
                                    </Button>
                                    <Button
                                        asChild
                                        className="border-teal-500/60 bg-teal-600 text-white hover:scale-[1.02] hover:bg-teal-700 hover:text-white active:scale-[0.99]"
                                    >
                                        <Link href={href}>Open Connectors</Link>
                                    </Button>
                                </div>
                            </div>
                        ) : connectorIssues.length > 0 ? (
                            <div className="space-y-3">
                                <div className="text-sm text-muted-foreground">
                                    This page requires: {required.map((t) => connectorLabel(t)).join(", ")}.
                                </div>
                                <div className="space-y-2">
                                    {connectorIssues.map((x) => (
                                        <div key={x.type} className="rounded-xl border border-amber-500/20 bg-amber-500/10 p-3 text-sm text-amber-100">
                                            <div className="font-semibold">{connectorLabel(x.type)}</div>
                                            <div className="mt-1 text-amber-100/80">{x.message}</div>
                                        </div>
                                    ))}
                                </div>
                                <div className="flex flex-wrap gap-2">
                                    <Button asChild>
                                        <Link href={href}>Fix connectors</Link>
                                    </Button>
                                    <Button type="button" variant="outline" onClick={() => void statusesQ.refetch()}>
                                        Refresh status
                                    </Button>
                                </div>
                            </div>
                        ) : (
                            <div className="text-sm text-muted-foreground">Checking connectors…</div>
                        )}
                    </CardContent>
                </Card>
            </div>
        );
    }

    return <>{children}</>;
}
