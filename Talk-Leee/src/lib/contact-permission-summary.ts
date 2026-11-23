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

/**
 * True when the contact lists answer came from a server that has the summary
 * route: only that server sends `queued_for_calling` on a contact list. An older
 * server is never asked for the summary, because the request would fail and
 * leave an error in the browser's console on every view of a campaign.
 */
export function serverHasPermissionSummary(lists: ReadonlyArray<object> | null | undefined): boolean {
    return Array.isArray(lists) && lists.some((list) => list !== null && typeof list === "object" && "queued_for_calling" in list);
}

/** Reads the summary when `enabled`, on mount and whenever `refreshKey` changes; null until it answers, on any error, and while not enabled. */
export function useContactPermissionSummary(campaignId: string, refreshKey: string, enabled: boolean): ContactPermissionSummary | null {
    const [summary, setSummary] = useState<ContactPermissionSummary | null>(null);
    useEffect(() => {
        let cancelled = false;
        if (!enabled) {
            // eslint-disable-next-line react-hooks/set-state-in-effect -- an older server has no summary: clear any earlier one
            setSummary(null);
            return;
        }
        void dashboardApi.getContactPermissionSummary(campaignId).then((next) => {
            if (!cancelled) setSummary(next);
        }, () => {
            if (!cancelled) setSummary(null);
        });
        return () => { cancelled = true; };
    }, [campaignId, refreshKey, enabled]);
    return summary;
}
