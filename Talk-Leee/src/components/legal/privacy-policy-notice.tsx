"use client";

import Link from "next/link";
import { useEffect, useState, useSyncExternalStore } from "react";
import { Button } from "@/components/ui/button";
import { useSignedInUser } from "@/lib/auth-context";
import { notificationsStore } from "@/lib/notifications";
import { useNotificationsState } from "@/lib/notifications-client";
import { PRIVACY_POLICY_DATES, PRIVACY_POLICY_VERSION, formatPolicyDate, privacyNoticeInWindow } from "@/lib/privacy-policy-dates";

/** The "seen" marker source (lib/notifications.ts notificationSeenKey): one id per policy version. */
export const PRIVACY_NOTICE_SEEN_SOURCE = "privacy-policy-notice";

// For a person the seen markers cannot hold (no account scope, or browser storage
// is closed): remembered until the page is reloaded, keyed by person and version.
const dismissedInMemory = new Set<string>();

// The clock, re-read once a minute so an open page crosses a date boundary by itself.
// 0 on the server and during hydration: the notice is decided in the browser only.
const CLOCK_TICK_MS = 60_000;
const subscribeToClock = (onChange: () => void) => {
    const id = setInterval(onChange, CLOCK_TICK_MS);
    return () => clearInterval(id);
};
const readClock = () => Math.floor(Date.now() / CLOCK_TICK_MS) * CLOCK_TICK_MS;
const readServerClock = () => 0;

// How long to wait for a person's account scope before showing the notice without it.
const SCOPE_WAIT_MS = 3_000;

/**
 * Privacy Policy change notice: shown at the top of every signed-in page from
 * the notice start date until the policy's effective date
 * (lib/privacy-policy-dates.ts), to every role. Dismiss hides it for that
 * person for that policy version.
 */
export function PrivacyPolicyNotice({ now, scopeWaitMs = SCOPE_WAIT_MS }: {
    /** Tests only: the time to decide by. */
    now?: number;
    /** Tests only: how long to wait for the account scope. */
    scopeWaitMs?: number;
}) {
    const user = useSignedInUser();
    const { scopeKey } = useNotificationsState();
    const tenantId = user?.tenant_id ?? null;
    // True once the wait for this person's account scope is over without one arriving.
    const [waitedFor, setWaitedFor] = useState<string | null>(null);
    useEffect(() => {
        if (!tenantId || scopeKey) return;
        const id = setTimeout(() => setWaitedFor(tenantId), scopeWaitMs);
        return () => clearTimeout(id);
    }, [tenantId, scopeKey, scopeWaitMs]);
    const clock = useSyncExternalStore(subscribeToClock, readClock, readServerClock);
    // Bumped by Dismiss so this render reads the marker it just wrote.
    const [, setDismissals] = useState(0);

    if (!user) return null;
    if (!privacyNoticeInWindow(now ?? clock)) return null;

    // The seen markers belong to an account scope (tenant + person), which arrives a moment after
    // sign-in. Someone in a tenant waits for it: showing first would flash the notice at a person who
    // dismissed it. If it never arrives (browser storage is closed), the notice shows after the wait.
    if (tenantId && !scopeKey && waitedFor !== tenantId) return null;
    const memoryKey = `${user.id}:${PRIVACY_POLICY_VERSION}`;
    const dismissed = scopeKey
        ? Boolean(notificationsStore.readSeen(PRIVACY_NOTICE_SEEN_SOURCE)?.has(PRIVACY_POLICY_VERSION)) || dismissedInMemory.has(memoryKey)
        : dismissedInMemory.has(memoryKey);
    if (dismissed) return null;

    function dismiss() {
        notificationsStore.markSeen(PRIVACY_NOTICE_SEEN_SOURCE, [PRIVACY_POLICY_VERSION]);
        dismissedInMemory.add(memoryKey);
        setDismissals((count) => count + 1);
    }

    return (
        // The offline banner's style (components/ui/offline-banner.tsx:24), without an icon.
        <div
            role="note"
            data-privacy-policy-notice
            className="print:hidden flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm font-medium text-amber-800 dark:text-amber-300"
        >
            <p className="min-w-0 flex-1 basis-56">We&apos;re updating our Privacy Policy on {formatPolicyDate(PRIVACY_POLICY_DATES.effectiveDate)}.</p>
            <Link
                href="/privacy"
                className="rounded-sm underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            >
                Read the Privacy Policy
            </Link>
            <Button type="button" variant="outline" size="sm" onClick={dismiss}>
                Dismiss
            </Button>
        </div>
    );
}
