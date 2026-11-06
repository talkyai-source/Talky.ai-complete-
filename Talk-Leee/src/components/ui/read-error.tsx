"use client";

import { useSyncExternalStore } from "react";

function subscribe(callback: () => void) {
    window.addEventListener("online", callback);
    window.addEventListener("offline", callback);
    return () => {
        window.removeEventListener("online", callback);
        window.removeEventListener("offline", callback);
    };
}

/** Read recovery only: connectivity changes never replay a request or write. */
export function ReadError({ resource, requestId, onRetry, hasData = false, busy = false }: {
    resource: string;
    requestId?: string;
    onRetry?: () => void;
    hasData?: boolean;
    busy?: boolean;
}) {
    const offline = useSyncExternalStore(subscribe, () => navigator.onLine === false, () => false);
    return (
        <div role="alert" aria-live="polite" className="rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm">
            <p className="font-semibold">Could not load {resource}.</p>
            <p className="mt-1">{offline ? "You appear to be offline. Reconnect and try again." : "Please try again."}</p>
            {hasData ? <p className="mt-1">Showing the last loaded data. It may not reflect the selected filters.</p> : null}
            {requestId ? <p className="mt-1 break-all text-xs text-muted-foreground">Request reference: {requestId}</p> : null}
            {onRetry ? <button type="button" onClick={onRetry} disabled={offline || busy}
                className="mt-3 rounded-md border border-border px-3 py-2 font-semibold disabled:opacity-50">
                {busy ? "Loading…" : "Try again"}
            </button> : null}
        </div>
    );
}
