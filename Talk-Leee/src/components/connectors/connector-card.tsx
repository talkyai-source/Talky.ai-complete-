"use client";

import React from "react";
import type { ComponentType } from "react";
import { useCallback, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Loader2, RotateCcw, Unplug } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { StatusPill, type StatusPillState, type StatusPillTheme } from "@/components/ui/status-pill";
import { useAuthorizeConnector, useDisconnectConnector, queryKeys } from "@/lib/api-hooks";
import { formatLastSync, type ConnectorProviderType } from "@/lib/connectors-utils";
import { isApiClientError } from "@/lib/http-client";
import type { ConnectorConnectionStatus } from "@/lib/models";
import { cn } from "@/lib/utils";

function openOAuthWindow(url: string) {
    const w = 640;
    const h = 760;
    const left = Math.max(0, Math.round((window.screen.width - w) / 2));
    const top = Math.max(0, Math.round((window.screen.height - h) / 2));
    const features = `popup=yes,width=${w},height=${h},left=${left},top=${top}`;
    const win = window.open(url, "_blank", features);
    if (!win) {
        window.location.assign(url);
        return;
    }
    try {
        win.focus();
    } catch {
    }
}

function formatError(err: unknown) {
    if (isApiClientError(err)) return err.message;
    return err instanceof Error ? err.message : "Request failed";
}

export function ConnectorCard({
    type,
    name,
    description,
    icon: Icon,
    accentClassName,
    status,
    lastSync,
    provider,
    errorMessage,
    oauthCallbackPath = "/connectors/callback",
    authorizeConnector,
    disconnectConnector,
    statusPillTheme,
    className,
}: {
    type: ConnectorProviderType;
    name: string;
    description: string;
    icon: ComponentType<{ className?: string }>;
    accentClassName?: string;
    status: ConnectorConnectionStatus;
    lastSync?: string | null;
    provider?: string | null;
    errorMessage?: string | null;
    oauthCallbackPath?: string;
    authorizeConnector?: (input: { type: string; redirect_uri: string }) => Promise<{ authorization_url: string }>;
    disconnectConnector?: (input: { type: string }) => Promise<void>;
    statusPillTheme?: StatusPillTheme;
    className?: string;
}) {
    const qc = useQueryClient();
    const authorize = useAuthorizeConnector();
    const disconnect = useDisconnectConnector();

    const [inlineError, setInlineError] = useState<string | undefined>(undefined);
    const [pendingAction, setPendingAction] = useState<"connect" | "reconnect" | "disconnect" | null>(null);
    const [confirmOpen, setConfirmOpen] = useState(false);

    const statusPillState: StatusPillState = status;

    const detailsTone = useMemo(() => {
        if (status === "error") return "text-red-600";
        if (status === "expired") return "text-amber-700";
        return "text-muted-foreground";
    }, [status]);

    const detailsText = useMemo(() => {
        if (inlineError) return inlineError;
        if (status === "error" || status === "expired") return errorMessage?.trim() || "Connection needs attention.";
        if (status === "connected") return "Syncing enabled.";
        return "Not connected.";
    }, [errorMessage, inlineError, status]);

    const authorizeFn = authorizeConnector ?? authorize.mutateAsync;
    const disconnectFn = disconnectConnector ?? disconnect.mutateAsync;

    const isBusy = pendingAction !== null;

    const canConnect = status === "disconnected";
    const canReconnect = status === "expired" || status === "error";
    const canDisconnect = status === "connected" || status === "expired" || status === "error";

    const connectOrReconnect = useCallback(async () => {
        setInlineError(undefined);
        setPendingAction(status === "disconnected" ? "connect" : "reconnect");
        try {
            const redirect = new URL(`${window.location.origin}${oauthCallbackPath}`);
            if (!oauthCallbackPath.includes("[type]") && !oauthCallbackPath.includes(`/${type}/`)) {
                redirect.searchParams.set("type", type);
            }
            const res = await authorizeFn({ type, redirect_uri: redirect.toString() });
            openOAuthWindow(res.authorization_url);
        } catch (err) {
            setInlineError(formatError(err));
        } finally {
            setPendingAction(null);
        }
    }, [authorizeFn, oauthCallbackPath, status, type]);

    const requestDisconnect = useCallback(() => {
        setInlineError(undefined);
        setConfirmOpen(true);
    }, []);

    const confirmDisconnect = useCallback(async () => {
        setInlineError(undefined);
        setPendingAction("disconnect");
        try {
            await disconnectFn({ type });
            await qc.invalidateQueries({ queryKey: queryKeys.connectorStatuses() });
        } catch (err) {
            setInlineError(formatError(err));
            throw err;
        } finally {
            setPendingAction(null);
        }
    }, [disconnectFn, qc, type]);

    const lastSyncLabel = useMemo(() => formatLastSync(lastSync), [lastSync]);

    return (
        <div
            className={cn(
                "rounded-2xl border p-4 backdrop-blur-sm shadow-sm transition-[transform,background-color,border-color,box-shadow] duration-150 ease-out hover:-translate-y-0.5 hover:bg-background/40 hover:shadow-md",
                accentClassName,
                className
            )}
            data-testid={`connector-card-${type}`}
        >
            <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                    <div className="flex items-center gap-3">
                        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-white/60 text-gray-900 border border-gray-200/60">
                            <Icon className="h-5 w-5" aria-hidden />
                        </div>
                        <div className="min-w-0">
                            <div className="text-sm font-semibold text-foreground">{name}</div>
                            <div className="mt-1 text-xs text-muted-foreground">{description}</div>
                        </div>
                    </div>
                </div>

                <div className="flex items-center gap-2">
                    <StatusPill state={statusPillState} theme={statusPillTheme} />
                </div>
            </div>

            <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div className="rounded-xl border border-border bg-background/70 p-3">
                    <div className="text-xs font-semibold text-foreground">Last sync</div>
                    <div className="mt-1 text-xs text-muted-foreground">{lastSyncLabel}</div>
                </div>
                <div className="rounded-xl border border-border bg-background/70 p-3">
                    <div className="text-xs font-semibold text-foreground">Details</div>
                    <div className={cn("mt-1 text-xs", detailsTone)}>{detailsText}</div>
                </div>
            </div>

            <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
                <div className="text-xs text-muted-foreground">{provider ? `Provider: ${provider}` : null}</div>

                <div className="flex flex-wrap items-center gap-2">
                    {canConnect ? (
                        <Button
                            type="button"
                            variant="outline"
                            disabled={isBusy}
                            onClick={() => void connectOrReconnect()}
                            data-testid={`connector-${type}-connect`}
                        >
                            {pendingAction === "connect" ? (
                                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                            ) : (
                                <ExternalLink className="h-4 w-4" aria-hidden />
                            )}
                            {pendingAction === "connect" ? "Starting..." : "Connect"}
                        </Button>
                    ) : null}

                    {canReconnect ? (
                        <Button
                            type="button"
                            variant="secondary"
                            disabled={isBusy}
                            onClick={() => void connectOrReconnect()}
                            data-testid={`connector-${type}-reconnect`}
                        >
                            {pendingAction === "reconnect" ? (
                                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                            ) : (
                                <RotateCcw className="h-4 w-4" aria-hidden />
                            )}
                            {pendingAction === "reconnect" ? "Starting..." : "Reconnect"}
                        </Button>
                    ) : null}

                    {canDisconnect ? (
                        <Button
                            type="button"
                            variant="destructive"
                            disabled={isBusy}
                            onClick={requestDisconnect}
                            data-testid={`connector-${type}-disconnect`}
                        >
                            {pendingAction === "disconnect" ? (
                                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                            ) : (
                                <Unplug className="h-4 w-4" aria-hidden />
                            )}
                            {pendingAction === "disconnect" ? "Disconnecting..." : "Disconnect"}
                        </Button>
                    ) : null}
                </div>
            </div>

            <ConfirmDialog
                open={confirmOpen}
                onOpenChange={setConfirmOpen}
                intent="disconnect"
                warningText={`Disconnect ${name}? This will stop syncing data from the connected account.`}
                onConfirm={confirmDisconnect}
            />
        </div>
    );
}
