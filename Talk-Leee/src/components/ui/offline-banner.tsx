"use client";

import { useSyncExternalStore } from "react";
import { WifiOff } from "lucide-react";

function subscribe(callback: () => void) {
    window.addEventListener("online", callback);
    window.addEventListener("offline", callback);
    return () => {
        window.removeEventListener("online", callback);
        window.removeEventListener("offline", callback);
    };
}

/**
 * QA F1-27 (QA-UI-093): while the browser is offline every signed-in page
 * says so, instead of the first sign being a failed save. Nothing is queued
 * or replayed on reconnect.
 */
export function OfflineBanner() {
    const offline = useSyncExternalStore(subscribe, () => navigator.onLine === false, () => false);
    if (!offline) return null;
    return (
        <div role="status" aria-live="polite" className="print:hidden flex items-center gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm font-medium text-amber-800 dark:text-amber-300">
            <WifiOff className="h-4 w-4 shrink-0" aria-hidden />
            You&apos;re offline. Changes can&apos;t be saved until you reconnect.
        </div>
    );
}
