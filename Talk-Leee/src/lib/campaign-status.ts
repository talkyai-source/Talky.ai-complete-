/**
 * The one campaign status label (production-readiness 3.3, SS4), used by the
 * campaign list badge, its details modal, status filter and exports, the
 * campaign detail page and the dashboard.
 *
 * The backend writes draft, running, paused and stopped, and reads completed
 * (backend/app/api/v1/endpoints/campaigns.py). Any other value it sends is
 * shown under its own name: a status is never renamed to one the backend did
 * not send.
 */
const KNOWN_LABELS: Record<string, string> = {
    draft: "Draft",
    running: "Running",
    paused: "Paused",
    completed: "Completed",
    stopped: "Stopped",
};

/** The labels the status filter always offers, in display order. */
export const CAMPAIGN_STATUS_FILTER_LABELS: readonly string[] = ["Draft", "Running", "Paused", "Completed", "Stopped"];

export function campaignStatusLabel(status: string | null | undefined): string {
    const raw = String(status ?? "").trim();
    const known = KNOWN_LABELS[raw.toLowerCase()];
    if (known) return known;
    return raw ? raw.charAt(0).toUpperCase() + raw.slice(1) : "";
}

/** Shown from a Start/Resume confirm until its response. Not a backend status. */
export const CAMPAIGN_PREPARING_LABEL = "Preparing";
/** Shown while a Stop request runs. Not a backend status. */
export const CAMPAIGN_STOPPING_LABEL = "Stopping";

/** The status a campaign reads as right now: an action in flight, else the backend's value. */
export function campaignDisplayStatus(
    status: string | null | undefined,
    inFlight?: { starting?: boolean; stopping?: boolean },
): string {
    if (inFlight?.stopping) return CAMPAIGN_STOPPING_LABEL;
    if (inFlight?.starting) return CAMPAIGN_PREPARING_LABEL;
    return campaignStatusLabel(status);
}

/**
 * Status-filter choices saved before this helper existed used the old list
 * labels ("Active" for running, "Failed" for stopped). Translate them so a
 * saved filter keeps matching the same campaigns.
 */
export function migrateStatusFilterLabels(labels: unknown): string[] {
    if (!Array.isArray(labels)) return [];
    const next = labels
        .filter((label): label is string => typeof label === "string")
        .map((label) => (label === "Active" ? "Running" : label === "Failed" ? "Stopped" : label));
    return Array.from(new Set(next));
}

/** Filter options: the five known labels, then any other status the backend sent. */
export function campaignStatusFilterOptions(statuses: Array<string | null | undefined>): string[] {
    const options = [...CAMPAIGN_STATUS_FILTER_LABELS];
    for (const status of statuses) {
        const label = campaignStatusLabel(status);
        if (label && !options.includes(label)) options.push(label);
    }
    return options;
}
