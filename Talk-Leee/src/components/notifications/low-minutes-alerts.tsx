"use client";

import { useEffect } from "react";
import { api } from "@/lib/api";
import { useAccountAlertQuery } from "@/lib/account-alert-query";
import { notificationsStore } from "@/lib/notifications";
import { useNotificationsActions, useNotificationsState } from "@/lib/notifications-client";
import { isLowBalance, topupBalanceSchema } from "@/lib/topup-api";

const SEEN_SOURCE = "lowminutes";
const LOW_MARKER = "low";

// Remount the observer when verified identity changes, like QualifiedLeadAlerts.
export function LowMinutesAlerts() {
    const { scopeKey, generation, hydrated } = useNotificationsState();
    return scopeKey && hydrated
        ? <ScopedLowMinutesAlerts key={`${scopeKey}:${generation}`} scopeKey={scopeKey} generation={generation} />
        : null;
}

function ScopedLowMinutesAlerts({ scopeKey, generation }: { scopeKey: string; generation: number }) {
    // The same balance isLowBalance reads on Billing (topup-api.ts:55-61).
    const { data: balance } = useAccountAlertQuery("low-minutes-alerts", scopeKey, generation,
        async (signal) => topupBalanceSchema.parse(await api.request({ path: "/billing/topups/balance", signal })));
    const { create, capture } = useNotificationsActions();

    useEffect(() => {
        if (!balance) return;
        const origin = capture();
        if (!origin.isCurrent() || origin.scopeKey !== scopeKey) return;
        const low = isLowBalance(balance);
        const notified = notificationsStore.readSeen(SEEN_SOURCE)?.has(LOW_MARKER) ?? false;
        if (low && !notified) {
            // Remembered per account before creating, so a reload or another
            // tab does not repeat it while minutes stay low.
            notificationsStore.markSeen(SEEN_SOURCE, [LOW_MARKER]);
            create({
                type: "warning",
                title: "Low minutes",
                // "Minutes remaining" and the two figures as Billing shows them (topup-card.tsx:194-206).
                message: `Minutes remaining: ${balance.remaining_minutes.toLocaleString()} / ${balance.allocated.toLocaleString()}`,
                data: { kind: "low_minutes", remaining_minutes: balance.remaining_minutes, allocated: balance.allocated },
            });
        } else if (!low && notified) {
            // Minutes stopped being low: the next low reading may notify again.
            notificationsStore.unmarkSeen(SEEN_SOURCE, [LOW_MARKER]);
        }
    }, [balance, create, capture, scopeKey]);

    return null;
}
