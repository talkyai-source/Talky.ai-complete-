"use client";

import { useEffect } from "react";
import { useAccountAlertQuery } from "@/lib/account-alert-query";
import { humanizeOutcome, isFailedCallOutcome } from "@/lib/call-outcome-label";
import { dashboardApi } from "@/lib/dashboard-api";
import { notificationsStore } from "@/lib/notifications";
import { useNotificationsActions, useNotificationsState } from "@/lib/notifications-client";

const SEEN_SOURCE = "failedcall";
// GET /calls/ is newest first (calls.py ORDER BY c.created_at DESC) with at
// most 100 rows per page (page_size le=100).
const PAGE_SIZE = 100;

// Remount the observer when verified identity changes, like QualifiedLeadAlerts.
export function FailedCallAlerts() {
    const { scopeKey, generation, hydrated } = useNotificationsState();
    return scopeKey && hydrated
        ? <ScopedFailedCallAlerts key={`${scopeKey}:${generation}`} scopeKey={scopeKey} generation={generation} />
        : null;
}

function ScopedFailedCallAlerts({ scopeKey, generation }: { scopeKey: string; generation: number }) {
    const { data } = useAccountAlertQuery("failed-call-alerts", scopeKey, generation,
        // Cursor mode, first page only (QA F6-03): the newest calls are all this
        // needs, and the server then skips the total count and the OFFSET scan.
        (signal) => dashboardApi.listCallsCursor(null, PAGE_SIZE, {}, signal));
    const { create, capture } = useNotificationsActions();

    useEffect(() => {
        if (!data) return;
        const origin = capture();
        if (!origin.isCurrent() || origin.scopeKey !== scopeKey) return;
        const failed = data.calls.filter((call) => isFailedCallOutcome(call.outcome));
        const seen = notificationsStore.readSeen(SEEN_SOURCE);
        if (seen === null) {
            // Very first run for this account: remember the failed calls that
            // already exist without notifying, so old history is not replayed.
            notificationsStore.markSeen(SEEN_SOURCE, failed.map((call) => call.id));
            return;
        }
        // Oldest first, so the newest failed call ends up at the top of the list.
        const fresh = failed.filter((call) => !seen.has(call.id)).reverse();
        notificationsStore.markSeen(SEEN_SOURCE, fresh.map((call) => call.id));
        for (const call of fresh) {
            // Campaign name, the call list's outcome label (call-outcome-label.ts)
            // and the number as the call list shows it (calls/page.tsx:168),
            // joined the way the lead alert joins its parts.
            const message = [call.campaign_name, humanizeOutcome(call.outcome), call.phone_number].filter(Boolean).join(" — ");
            create({
                type: "error",
                title: "Failed call",
                message,
                data: { kind: "failed_call", call_id: call.id, campaign_id: call.campaign_id, outcome: call.outcome ?? "" },
            });
        }
    }, [data, create, capture, scopeKey]);

    return null;
}
