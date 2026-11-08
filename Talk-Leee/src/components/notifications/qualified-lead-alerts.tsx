"use client";

import { useEffect } from "react";
import { useEventStream } from "@/lib/event-stream-api";
import { notificationSeenKey, notificationsStore } from "@/lib/notifications";
import { useNotificationsActions, useNotificationsState } from "@/lib/notifications-client";

const LEGACY_SEEN_KEY = "talklee.qlead.seen.v1";
const SEEN_SOURCE = "qlead";

export function qualifiedLeadSeenKey(scopeKey: string) {
    return notificationSeenKey(scopeKey, SEEN_SOURCE);
}

// Remount the observer when verified identity changes. Its query cache and its
// saved deduplication IDs use that same identity; no legacy IDs are adopted.
export function QualifiedLeadAlerts() {
    const { scopeKey, generation, hydrated } = useNotificationsState();
    useEffect(() => {
        try { window.localStorage.removeItem(LEGACY_SEEN_KEY); } catch { /* Storage can be unavailable. */ }
    }, []);
    return scopeKey && hydrated
        ? <ScopedLeadAlerts key={`${scopeKey}:${generation}`} scopeKey={scopeKey} />
        : null;
}

function ScopedLeadAlerts({ scopeKey }: { scopeKey: string }) {
    const { data } = useEventStream("Alerts");
    const { create, capture } = useNotificationsActions();

    useEffect(() => {
        if (data === undefined) return;
        const origin = capture();
        if (!origin.isCurrent() || origin.scopeKey !== scopeKey) return;
        // Read before each observation so sequential observations in another
        // tab are respected even before its storage event is delivered. Every
        // lead event not yet shown for this account is shown, including those
        // that arrived while the app was closed or the tab was hidden.
        const seen = notificationsStore.readSeen(SEEN_SOURCE) ?? new Set<string>();
        const leads = data.filter((event) =>
            event.metadata?.kind === "qualified_lead" && !seen.has(event.id));
        notificationsStore.markSeen(SEEN_SOURCE, leads.map((event) => event.id));

        for (const event of leads) {
            const phone = event.metadata?.phone_number;
            const note = event.description || event.metadata?.follow_up_note;
            const message = [phone ? `Phone: ${phone}` : "", note ? String(note) : ""].filter(Boolean).join(" — ");
            create({
                type: "success",
                title: event.title,
                message: message || "New qualified lead — open the lead details for more information.",
                priority: "high",
                data: { kind: "qualified_lead", ...(event.metadata ?? {}) },
            });
        }
    }, [data, create, capture, scopeKey]);

    return null;
}
