"use client";

import React from "react";
import type { ComponentType } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Loader2, RotateCcw, Unplug } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { StatusPill, type StatusPillState, type StatusPillTheme } from "@/components/ui/status-pill";
import { failedReadMessage } from "@/components/states/page-states";
import { useAuthorizeConnector, useDisconnectConnector, queryKeys } from "@/lib/api-hooks";
import {
    CONNECTOR_CHECKING_TEXT,
    CONNECTOR_EXPIRED_TEXT,
    CONNECTOR_POPUP_CLOSED_TEXT,
    formatLastSync,
    watchOAuthPopup,
    type ConnectorOAuthReturn,
    type ConnectorProviderType,
} from "@/lib/connectors-utils";
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
        return null;
    }
    try {
        win.focus();
    } catch {
    }
    return win;
}

function formatError(err: unknown) {
    if (isApiClientError(err)) return failedReadMessage(err, err.message);
    return err instanceof Error ? err.message : "Request failed";
}

export function ConnectorCard({
    type,
    name,
    description,
    icon: Icon,
    accentClassName,
    status,
    checking = false,
    oauthReturn,
    lastSync,
    provider,
    errorMessage,
    oauthCallbackPath = "/connectors/callback",
    authorizeConnector,
    disconnectConnector,
    onOAuthWindowOpened,
    statusPillTheme,
    available = true,
    unavailableReason,
    className,
}: {
    type: ConnectorProviderType;
    name: string;
    description: string;
    icon: ComponentType<{ className?: string }>;
    accentClassName?: string;
    /** The backend's answer for this card. null: GET /connectors/status gave none. */
    status: ConnectorConnectionStatus | null;
    /** GET /connectors/status has not answered yet. */
    checking?: boolean;
    /** Latest return from an OAuth window, relayed by the Connectors page. */
    oauthReturn?: ConnectorOAuthReturn | null;
    lastSync?: string | null;
    provider?: string | null;
    errorMessage?: string | null;
    oauthCallbackPath?: string;
    authorizeConnector?: (input: { type: string; redirect_uri: string }) => Promise<{ authorization_url: string }>;
    disconnectConnector?: (input: { type: string }) => Promise<void>;
    /** Called when this card has opened an OAuth window. */
    onOAuthWindowOpened?: (type: ConnectorProviderType) => void;
    statusPillTheme?: StatusPillTheme;
    available?: boolean;
    unavailableReason?: string;
    className?: string;
}) {
    const qc = useQueryClient();
    const authorize = useAuthorizeConnector();
    const disconnect = useDisconnectConnector();

    const [inlineError, setInlineError] = useState<string | undefined>(undefined);
    const [pendingAction, setPendingAction] = useState<"connect" | "reconnect" | "disconnect" | null>(null);
    const [confirmOpen, setConfirmOpen] = useState(false);
    // A sentence about the last connection attempt. `whileStatus` ties it to the
    // backend status it was said under: "Nothing was changed" is dropped as soon
    // as the backend reports a different status.
    const [flowNotice, setFlowNotice] = useState<{ text: string; whileStatus?: ConnectorConnectionStatus | null } | null>(null);
    const [awaitingReturn, setAwaitingReturn] = useState(false);
    const [seenReturnId, setSeenReturnId] = useState<number | null>(oauthReturn?.id ?? null);
    const stopPopupWatch = useRef<(() => void) | null>(null);

    // A new return message. Only the card it names reacts (the page names the
    // card that opened the window when the backend's return does not).
    if (oauthReturn && oauthReturn.id !== seenReturnId) {
        setSeenReturnId(oauthReturn.id);
        const mine = oauthReturn.providerType === type;
        if (mine) {
            setAwaitingReturn(false);
            setFlowNotice(oauthReturn.ok ? null : { text: oauthReturn.message });
        }
    }

    useEffect(() => {
        if (awaitingReturn) return;
        stopPopupWatch.current?.();
        stopPopupWatch.current = null;
    }, [awaitingReturn]);

    useEffect(() => {
        return () => {
            stopPopupWatch.current?.();
            stopPopupWatch.current = null;
        };
    }, []);

    const statusPillState: StatusPillState = status ?? "disconnected";
    // Without a backend answer the card claims nothing: no pill and no actions.
    const statusKnown = status !== null;

    const detailsTone = useMemo(() => {
        if (status === "error") return "text-red-600";
        if (status === "expired") return "text-amber-700";
        return "text-muted-foreground";
    }, [status]);

    const detailsText = useMemo(() => {
        if (!available) return unavailableReason?.trim() || "This connector is not enabled by the server.";
        if (status === null) return checking ? CONNECTOR_CHECKING_TEXT : "—";
        if (inlineError) return inlineError;
        if (flowNotice && (flowNotice.whileStatus === undefined || flowNotice.whileStatus === status)) return flowNotice.text;
        if (status === "expired") return errorMessage?.trim() || CONNECTOR_EXPIRED_TEXT;
        if (status === "error") return errorMessage?.trim() || "Connection needs attention.";
        if (status === "connected") return "Connected.";
        return "Disconnected.";
    }, [available, checking, errorMessage, flowNotice, inlineError, status, unavailableReason]);

    const authorizeFn = authorizeConnector ?? authorize.mutateAsync;
    const disconnectFn = disconnectConnector ?? disconnect.mutateAsync;

    const isBusy = pendingAction !== null;

    const canConnect = available && status === "disconnected";
    const canReconnect = available && (status === "expired" || status === "error");
    const canDisconnect = available && (status === "connected" || status === "expired" || status === "error");

    const connectOrReconnect = useCallback(async () => {
        setInlineError(undefined);
        setFlowNotice(null);
        setPendingAction(status === "disconnected" ? "connect" : "reconnect");
        try {
            const redirect = new URL(`${window.location.origin}${oauthCallbackPath}`);
            if (!oauthCallbackPath.includes("[type]") && !oauthCallbackPath.includes(`/${type}/`)) {
                redirect.searchParams.set("type", type);
            }
            const res = await authorizeFn({ type, redirect_uri: redirect.toString() });
            const win = openOAuthWindow(res.authorization_url);
            stopPopupWatch.current?.();
            stopPopupWatch.current = null;
            if (win) {
                onOAuthWindowOpened?.(type);
                setAwaitingReturn(true);
                stopPopupWatch.current = watchOAuthPopup(win, () => {
                    stopPopupWatch.current = null;
                    setAwaitingReturn(false);
                    setFlowNotice({ text: CONNECTOR_POPUP_CLOSED_TEXT, whileStatus: status });
                    void qc.invalidateQueries({ queryKey: queryKeys.connectorStatuses() });
                });
            }
        } catch (err) {
            setInlineError(formatError(err));
        } finally {
            setPendingAction(null);
        }
    }, [authorizeFn, oauthCallbackPath, onOAuthWindowOpened, qc, status, type]);

    const requestDisconnect = useCallback(() => {
        setInlineError(undefined);
        setFlowNotice(null);
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
                        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-white/60 text-gray-900 border border-gray-200/60">
                            <Icon className="h-5 w-5" aria-hidden />
                        </div>
                        <div className="min-w-0">
                            <div className="text-sm font-semibold text-foreground">{name}</div>
                            <div className="mt-1 text-xs text-muted-foreground">{description}</div>
                        </div>
                    </div>
                </div>

                {statusKnown || !available ? (
                    <div className="flex items-center gap-2">
                        <StatusPill
                            state={statusPillState}
                            theme={statusPillTheme}
                            label={available ? undefined : "Unavailable"}
                            tooltip={available ? undefined : unavailableReason || "This connector is not enabled by the server."}
                        />
                    </div>
                ) : null}
            </div>

            <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div className="rounded-xl border border-border bg-background/70 p-3">
                    <div className="text-xs font-semibold text-foreground">Last refreshed</div>
                    <div className="mt-1 text-xs text-muted-foreground">{lastSyncLabel}</div>
                </div>
                <div className="rounded-xl border border-border bg-background/70 p-3">
                    <div className="text-xs font-semibold text-foreground">Details</div>
                    <div className={cn("mt-1 break-words text-xs", detailsTone)}>{detailsText}</div>
                </div>
            </div>

            <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
                <div className="text-xs text-muted-foreground">{provider ? `Provider: ${name}` : null}</div>

                <div className="flex flex-wrap items-center gap-2">
                    {!available ? (
                        <span className="text-xs font-medium text-muted-foreground">Server capability required</span>
                    ) : null}
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
                            {pendingAction === "connect" ? "Starting…" : "Connect"}
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
                            {pendingAction === "reconnect" ? "Starting…" : "Reconnect"}
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
