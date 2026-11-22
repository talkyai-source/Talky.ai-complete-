/**
 * How many contacts of a campaign have no permission to call on record, and the
 * words the campaign page uses for it. The server route exists only on newer
 * servers: a missing summary (null) means "show nothing", never "zero".
 */
import { useEffect, useState } from "react";

import { dashboardApi, type ContactPermissionSummary } from "@/lib/dashboard-api";

export type { ContactPermissionSummary };

export type SummaryNotice = { title: string; body: string };

export function summaryNotice(summary: ContactPermissionSummary | null | undefined): SummaryNotice | null {
    if (!summary || !(summary.waiting_without_permission > 0)) return null;
    const n = summary.waiting_without_permission;
    if (summary.dial_gate === "on") {
        return {
            title: n === 1 ? "1 contact will not be called" : `${n.toLocaleString("en-US")} contacts will not be called`,
            body: "No permission to call is recorded for them. Confirm permission on their contact list below.",
        };
    }
    return {
        title: n === 1
            ? "1 contact has no permission to call on record"
            : `${n.toLocaleString("en-US")} contacts have no permission to call on record`,
        body: "Confirm permission on their contact list below.",
    };
}

/** Reads the summary on mount and whenever `refreshKey` changes; null until it answers and on any error. */
export function useContactPermissionSummary(campaignId: string, refreshKey: string): ContactPermissionSummary | null {
    const [summary, setSummary] = useState<ContactPermissionSummary | null>(null);
    useEffect(() => {
        let cancelled = false;
        void dashboardApi.getContactPermissionSummary(campaignId).then((next) => {
            if (!cancelled) setSummary(next);
        }, () => {
            if (!cancelled) setSummary(null);
        });
        return () => { cancelled = true; };
    }, [campaignId, refreshKey]);
    return summary;
}
