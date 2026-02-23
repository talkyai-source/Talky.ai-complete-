import type { Campaign } from "@/lib/dashboard-api";

export type CampaignStatus = "Active" | "Paused" | "Completed" | "Draft" | "Failed";
export type SortDir = "asc" | "desc";
export type CampaignSortKey =
    | "name"
    | "status"
    | "progress"
    | "successRate"
    | "leads"
    | "completed"
    | "failed"
    | "createdAt";

export type CampaignSortSpec = { key: CampaignSortKey; dir: SortDir };

export type CampaignFilters = {
    statuses: CampaignStatus[];
    successMin: number;
    successMax: number;
    query: string;
};

function clamp(value: number, min: number, max: number) {
    return Math.min(max, Math.max(min, value));
}

export function normalizeCampaignStatus(status: string): CampaignStatus {
    const s = (status || "").toLowerCase();
    if (s === "running" || s === "active") return "Active";
    if (s === "paused") return "Paused";
    if (s === "completed") return "Completed";
    if (s === "draft") return "Draft";
    if (s === "failed" || s === "stopped") return "Failed";
    return "Draft";
}

export function campaignProgressPct(campaign: Campaign): number {
    const total = Math.max(0, campaign.total_leads || 0);
    const completed = Math.max(0, campaign.calls_completed || 0);
    if (total <= 0) return 0;
    return clamp((completed / total) * 100, 0, 100);
}

export function campaignSuccessRatePct(campaign: Campaign): number {
    const completed = Math.max(0, campaign.calls_completed || 0);
    const failed = Math.max(0, campaign.calls_failed || 0);
    const denom = completed + failed;
    if (denom <= 0) return 0;
    return clamp((completed / denom) * 100, 0, 100);
}

export function progressColorClass(progressPct: number) {
    const v = clamp(progressPct, 0, 100);
    if (v <= 40) return "bg-red-500";
    if (v <= 70) return "bg-orange-500";
    return "bg-emerald-500";
}

export function statusBadgeClass(status: CampaignStatus) {
    switch (status) {
        case "Active":
            return "bg-transparent border border-black/70 text-emerald-800 dark:border-white/70 dark:text-emerald-300";
        case "Paused":
            return "bg-transparent border border-black/70 text-amber-800 dark:border-white/70 dark:text-amber-300";
        case "Completed":
            return "bg-transparent border border-black/70 text-blue-800 dark:border-white/70 dark:text-blue-300";
        case "Draft":
            return "bg-transparent border border-black/70 text-gray-800 dark:border-white/70 dark:text-gray-200";
        case "Failed":
            return "bg-transparent border border-black/70 text-red-800 dark:border-white/70 dark:text-red-300";
        default:
            return "bg-transparent border border-black/70 text-gray-800 dark:border-white/70 dark:text-gray-200";
    }
}

function compareNumber(a: number, b: number) {
    if (a < b) return -1;
    if (a > b) return 1;
    return 0;
}

function compareString(a: string, b: string) {
    return a.localeCompare(b, undefined, { sensitivity: "base" });
}

function getSortValue(campaign: Campaign, key: CampaignSortKey) {
    switch (key) {
        case "name":
            return campaign.name || "";
        case "status":
            return normalizeCampaignStatus(campaign.status);
        case "progress":
            return campaignProgressPct(campaign);
        case "successRate":
            return campaignSuccessRatePct(campaign);
        case "leads":
            return Math.max(0, campaign.total_leads || 0);
        case "completed":
            return Math.max(0, campaign.calls_completed || 0);
        case "failed":
            return Math.max(0, campaign.calls_failed || 0);
        case "createdAt":
            return new Date(campaign.created_at).getTime();
        default:
            return campaign.name || "";
    }
}

export function applyCampaignSort(items: Campaign[], sort: CampaignSortSpec[]) {
    if (!sort || sort.length === 0) return items;
    const decorated = items.map((item, idx) => ({ item, idx }));
    decorated.sort((a, b) => {
        for (const spec of sort) {
            const av = getSortValue(a.item, spec.key);
            const bv = getSortValue(b.item, spec.key);
            let cmp = 0;
            if (typeof av === "number" && typeof bv === "number") cmp = compareNumber(av, bv);
            else cmp = compareString(String(av), String(bv));
            if (cmp !== 0) return spec.dir === "asc" ? cmp : -cmp;
        }
        return a.idx - b.idx;
    });
    return decorated.map((d) => d.item);
}

export function applyCampaignFilters(items: Campaign[], filters: CampaignFilters) {
    const statuses = new Set(filters.statuses);
    const q = (filters.query || "").trim().toLowerCase();
    const min = clamp(filters.successMin ?? 0, 0, 100);
    const max = clamp(filters.successMax ?? 100, 0, 100);
    const lo = Math.min(min, max);
    const hi = Math.max(min, max);

    return items.filter((c) => {
        const st = normalizeCampaignStatus(c.status);
        if (statuses.size > 0 && !statuses.has(st)) return false;
        const sr = campaignSuccessRatePct(c);
        if (sr < lo || sr > hi) return false;
        if (q.length > 0) {
            const name = (c.name || "").toLowerCase();
            if (!name.includes(q)) return false;
        }
        return true;
    });
}

export function formatPct(value: number) {
    return `${clamp(value, 0, 100).toFixed(1)}%`;
}

export type RowsPerPage = 10 | 25 | 50 | 100 | "All";

export function paginate<T>(items: T[], page: number, rowsPerPage: RowsPerPage) {
    if (rowsPerPage === "All") {
        return { page: 1, pageCount: 1, slice: items, start: 1, end: items.length };
    }
    const size = rowsPerPage;
    const pageCount = Math.max(1, Math.ceil(items.length / size));
    const safePage = clamp(page, 1, pageCount);
    const startIdx = (safePage - 1) * size;
    const endIdx = Math.min(items.length, startIdx + size);
    const slice = items.slice(startIdx, endIdx);
    return { page: safePage, pageCount, slice, start: items.length === 0 ? 0 : startIdx + 1, end: endIdx };
}

export type EventCategory = "Campaign" | "System" | "Alerts" | "User Actions" | "Milestones";
export type EventQuickFilter = "All" | "Campaigns" | "System" | "Alerts" | "User Actions";

export type StreamEvent = {
    id: string;
    category: EventCategory;
    title: string;
    description: string;
    createdAt: string;
    relatedCampaignIds?: string[];
    metadata?: Record<string, string | number | boolean>;
};

export function eventCategoryIcon(category: EventCategory) {
    switch (category) {
        case "Campaign":
            return "📢\uFE0E";
        case "System":
            return "🖥\uFE0E";
        case "Alerts":
            return "⚠\uFE0E";
        case "User Actions":
            return "👤\uFE0E";
        case "Milestones":
            return "🏁\uFE0E";
        default:
            return "🖥\uFE0E";
    }
}

export type EventTimeGroup = "Today" | "Yesterday" | "Last 7 Days" | "Older";

export function groupEventTime(createdAt: string, now = new Date()) {
    const d = new Date(createdAt);
    const startOfToday = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
    const startOfYesterday = new Date(startOfToday.getTime() - 24 * 60 * 60 * 1000);
    const sevenDaysAgo = new Date(startOfToday.getTime() - 7 * 24 * 60 * 60 * 1000);

    if (d >= startOfToday) return "Today";
    if (d >= startOfYesterday) return "Yesterday";
    if (d >= sevenDaysAgo) return "Last 7 Days";
    return "Older";
}

export function filterEvents(events: StreamEvent[], quick: EventQuickFilter) {
    if (quick === "All") return events;
    if (quick === "Campaigns") return events.filter((e) => e.category === "Campaign" || e.category === "Milestones");
    if (quick === "System") return events.filter((e) => e.category === "System");
    if (quick === "Alerts") return events.filter((e) => e.category === "Alerts");
    if (quick === "User Actions") return events.filter((e) => e.category === "User Actions");
    return events;
}

export type AlertSeverity = "Critical" | "Warning" | "Info";
export type AlertType = "Network" | "API" | "Campaign" | "System";
export type AlertStatus = "Active" | "Resolved" | "Investigating";

export type AlertItem = {
    id: string;
    title: string;
    description: string;
    severity: AlertSeverity;
    type: AlertType;
    status: AlertStatus;
    createdAt: string;
    updatedAt: string;
    acknowledged: boolean;
    relatedCampaignIds?: string[];
    metadata?: Record<string, string | number | boolean>;
    snoozedUntil?: string;
};

export function severityBadgeClass(sev: AlertSeverity) {
    switch (sev) {
        case "Critical":
            return "bg-background text-red-800 border border-red-700/50 dark:text-red-300 dark:border-red-400/50";
        case "Warning":
            return "bg-background text-amber-800 border border-amber-700/50 dark:text-amber-300 dark:border-amber-400/50";
        case "Info":
            return "bg-background text-blue-800 border border-blue-700/50 dark:text-blue-300 dark:border-blue-400/50";
        default:
            return "bg-background text-slate-800 border border-slate-600/50 dark:text-slate-200 dark:border-slate-500/60";
    }
}

export function isAlertSnoozed(alert: AlertItem, now = new Date()) {
    if (!alert.snoozedUntil) return false;
    const until = new Date(alert.snoozedUntil).getTime();
    return Number.isFinite(until) && until > now.getTime();
}

export type CommandResultCategory = "Campaigns" | "Reports" | "Settings" | "Help Docs";
export type CommandPrefix = "/" | "@" | "#" | ">" | "";

export function parseCommandInput(input: string): { prefix: CommandPrefix; query: string } {
    const raw = input || "";
    const trimmed = raw.trimStart();
    const first = trimmed[0] || "";
    if (first === "/" || first === "@" || first === "#" || first === ">") {
        return { prefix: first as CommandPrefix, query: trimmed.slice(1).trim() };
    }
    return { prefix: "", query: trimmed.trim() };
}

export function campaignsToCsv(campaigns: Campaign[]) {
    const headers = [
        "id",
        "name",
        "status",
        "total_leads",
        "calls_completed",
        "calls_failed",
        "progress_pct",
        "success_rate_pct",
        "created_at",
    ];
    const lines = [headers.join(",")];
    for (const c of campaigns) {
        const row = [
            c.id,
            csvEscape(c.name),
            normalizeCampaignStatus(c.status),
            String(c.total_leads ?? 0),
            String(c.calls_completed ?? 0),
            String(c.calls_failed ?? 0),
            campaignProgressPct(c).toFixed(1),
            campaignSuccessRatePct(c).toFixed(1),
            c.created_at,
        ];
        lines.push(row.join(","));
    }
    return lines.join("\n");
}

function csvEscape(value: string) {
    const v = String(value ?? "");
    const needs = v.includes(",") || v.includes('"') || v.includes("\n") || v.includes("\r");
    if (!needs) return v;
    return `"${v.replaceAll('"', '""')}"`;
}
