"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { parseConnectorsCallback } from "@/lib/connectors-utils";

function randomEventId() {
    if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
    return `evt_${Math.random().toString(16).slice(2)}_${Date.now().toString(16)}`;
}

function ConnectorsCallbackInner() {
    const searchParams = useSearchParams();
    const [canClose, setCanClose] = useState(true);

    const parsed = useMemo(() => parseConnectorsCallback(new URLSearchParams(searchParams.toString())), [searchParams]);

    useEffect(() => {
        try {
            if (sessionStorage.getItem("connectors.callback.sent") === "1") return;
            sessionStorage.setItem("connectors.callback.sent", "1");
        } catch {
        }

        let stableEventId = randomEventId();
        try {
            const existing = sessionStorage.getItem("connectors.callback.eventId");
            if (existing) stableEventId = existing;
            else sessionStorage.setItem("connectors.callback.eventId", stableEventId);
        } catch {
        }

        const payload = {
            type: "connectors:updated",
            eventId: stableEventId,
            ok: parsed.ok,
            providerType: parsed.providerType,
            message: parsed.message,
        };

        let deliveredToOpener = false;
        try {
            if (window.opener && !window.opener.closed) {
                window.opener.postMessage(payload, "*");
                deliveredToOpener = true;
            }
        } catch {
        }

        try {
            if (!deliveredToOpener && typeof BroadcastChannel !== "undefined") {
                const bc = new BroadcastChannel("connectors");
                bc.postMessage(payload);
                bc.close();
            }
        } catch {
        }

        try {
            localStorage.setItem("connectors.refresh", String(Date.now()));
        } catch {
        }
        try {
            if (!deliveredToOpener) localStorage.setItem("connectors.refresh.event", JSON.stringify({ ...payload, ts: Date.now() }));
        } catch {
        }

        const t = window.setTimeout(() => {
            try {
                window.close();
            } catch {
                setCanClose(false);
            }
        }, 900);
        return () => window.clearTimeout(t);
    }, [parsed.message, parsed.ok, parsed.providerType]);

    return (
        <div className="mx-auto flex min-h-[60vh] w-full max-w-xl items-center justify-center px-4">
            <Card className="w-full">
                <CardHeader>
                    <CardTitle>{parsed.ok ? "Connection complete" : "Connection failed"}</CardTitle>
                    <CardDescription>{parsed.ok ? "You can return to Connectors." : "Fix the issue and try again."}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className={parsed.ok ? "text-sm text-emerald-700" : "text-sm text-red-600"}>{parsed.message}</div>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                        <Button asChild>
                            <Link href="/settings/connectors">Back to Connectors</Link>
                        </Button>
                        {canClose ? (
                            <Button variant="ghost" onClick={() => window.close()}>
                                Close window
                            </Button>
                        ) : null}
                    </div>
                </CardContent>
            </Card>
        </div>
    );
}

export default function ConnectorsCallbackPage() {
    return (
        <Suspense
            fallback={
                <div className="mx-auto flex min-h-[60vh] w-full max-w-xl items-center justify-center px-4">
                    <Card className="w-full">
                        <CardHeader>
                            <CardTitle>Finishing up…</CardTitle>
                            <CardDescription>Reading authorization response.</CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="h-4 w-2/3 animate-pulse rounded bg-foreground/10" />
                            <div className="flex flex-wrap items-center justify-between gap-2">
                                <Button asChild>
                                    <Link href="/settings/connectors">Back to Connectors</Link>
                                </Button>
                            </div>
                        </CardContent>
                    </Card>
                </div>
            }
        >
            <ConnectorsCallbackInner />
        </Suspense>
    );
}
