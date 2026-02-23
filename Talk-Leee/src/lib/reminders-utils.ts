"use client";

import type { CalendarEvent, Reminder, ReminderChannel, ReminderStatus } from "@/lib/models";

export type ReminderSortKey = "scheduledAt" | "status" | "meeting" | "contact" | "retryCount";
export type SortDir = "asc" | "desc";

export type ReminderFilters = {
    query: string;
    statuses: Set<ReminderStatus>;
    channels: Set<ReminderChannel>;
};

function timeMs(iso: string | undefined) {
    if (!iso) return Number.NaN;
    const ms = Date.parse(iso);
    return Number.isFinite(ms) ? ms : Number.NaN;
}

export function reminderStatusLabel(s: ReminderStatus) {
    if (s === "scheduled") return "Scheduled";
    if (s === "sent") return "Sent";
    if (s === "failed") return "Failed";
    return "Canceled";
}

export function reminderStatusBadgeClass(s: ReminderStatus) {
    if (s === "sent") return "bg-emerald-500/10 text-emerald-700 border border-emerald-500/20";
    if (s === "failed") return "bg-red-500/10 text-red-600 border border-red-500/20";
    if (s === "canceled") return "bg-gray-500/10 text-gray-700 border border-gray-500/20";
    return "bg-blue-500/10 text-blue-700 border border-blue-500/20";
}

export function formatIsoDateTime(iso: string | undefined) {
    if (!iso) return "—";
    const ms = timeMs(iso);
    if (!Number.isFinite(ms)) return "—";
    return new Date(ms).toLocaleString();
}

export function sanitizeFailureReason(raw: string | undefined) {
    const base = String(raw ?? "").replace(/\u0000/g, "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    const stripped = base.replace(/[^\S\n]+/g, " ").trim();
    const safeAngle = stripped.replace(/</g, "‹").replace(/>/g, "›");
    const max = 800;
    if (safeAngle.length <= max) return safeAngle;
    return `${safeAngle.slice(0, max)}…`;
}

export function retryGuidance(r: Reminder) {
    if (r.status !== "failed") return "";
    const retryCount = typeof r.retryCount === "number" ? r.retryCount : 0;
    const maxRetries = typeof r.maxRetries === "number" ? r.maxRetries : undefined;
    const next = r.nextRetryAt ? formatIsoDateTime(r.nextRetryAt) : "";
    if (maxRetries !== undefined && retryCount >= maxRetries) return "No retries remaining.";
    if (next) return `Next retry: ${next}`;
    if (retryCount > 0) return "Retry pending. Check back shortly.";
    return "Retry pending.";
}

function match(haystack: string, query: string) {
    const q = query.trim().toLowerCase();
    if (!q) return true;
    return haystack.toLowerCase().includes(q);
}

export function applyReminderJoins(reminders: Reminder[], calendarEvents: CalendarEvent[]) {
    const byMeeting = new Map<string, CalendarEvent>();
    const byLead = new Map<string, CalendarEvent>();
    for (const ev of calendarEvents) {
        byMeeting.set(ev.id, ev);
        if (ev.leadId) byLead.set(ev.leadId, ev);
    }

    return reminders.map((r) => {
        const ev = (r.meetingId && byMeeting.get(r.meetingId)) || (r.contactId && byLead.get(r.contactId)) || undefined;
        if (!ev) return r;
        return {
            ...r,
            meetingId: r.meetingId ?? ev.id,
            meetingTitle: r.meetingTitle ?? ev.title,
            contactId: r.contactId ?? ev.leadId,
            contactName: r.contactName ?? ev.leadName,
        } satisfies Reminder;
    });
}

export function filterReminders(reminders: Reminder[], filters: ReminderFilters) {
    const q = filters.query.trim();
    return reminders.filter((r) => {
        if (filters.statuses.size > 0 && !filters.statuses.has(r.status)) return false;
        if (filters.channels.size > 0 && !filters.channels.has(r.channel)) return false;
        if (!q) return true;
        const hay = [r.meetingTitle, r.contactName, r.content, r.toEmail, r.toPhone].filter(Boolean).join(" • ");
        return match(hay, q);
    });
}

export function sortReminders(reminders: Reminder[], key: ReminderSortKey, dir: SortDir) {
    const sign = dir === "asc" ? 1 : -1;
    const items = [...reminders];
    items.sort((a, b) => {
        if (key === "scheduledAt") {
            const aa = timeMs(a.scheduledAt);
            const bb = timeMs(b.scheduledAt);
            if (!Number.isFinite(aa) && !Number.isFinite(bb)) return 0;
            if (!Number.isFinite(aa)) return 1;
            if (!Number.isFinite(bb)) return -1;
            return (aa - bb) * sign;
        }
        if (key === "retryCount") {
            const aa = typeof a.retryCount === "number" ? a.retryCount : -1;
            const bb = typeof b.retryCount === "number" ? b.retryCount : -1;
            return (aa - bb) * sign;
        }
        if (key === "meeting") {
            return (String(a.meetingTitle ?? "")).localeCompare(String(b.meetingTitle ?? "")) * sign;
        }
        if (key === "contact") {
            return (String(a.contactName ?? "")).localeCompare(String(b.contactName ?? "")) * sign;
        }
        return (a.status.localeCompare(b.status) || timeMs(a.scheduledAt) - timeMs(b.scheduledAt)) * sign;
    });
    return items;
}

export type ReminderGroup = {
    key: string;
    meetingTitle: string;
    contactLabel: string;
    meetingId?: string;
    contactId?: string;
    items: Reminder[];
};

export function groupReminders(reminders: Reminder[]) {
    const groups = new Map<string, ReminderGroup>();
    for (const r of reminders) {
        const meetingKey = r.meetingId ? `meeting:${r.meetingId}` : "";
        const contactKey = r.contactId ? `contact:${r.contactId}` : "";
        const key = meetingKey || contactKey || "general";
        const prev = groups.get(key);
        const meetingTitle = r.meetingTitle ?? (r.meetingId ? "Meeting" : "General");
        const contactLabel = r.contactName ?? (r.toEmail || r.toPhone || "—");
        if (!prev) {
            groups.set(key, {
                key,
                meetingId: r.meetingId,
                contactId: r.contactId,
                meetingTitle,
                contactLabel,
                items: [r],
            });
        } else {
            prev.items.push(r);
        }
    }

    const out = Array.from(groups.values());
    out.sort((a, b) => a.meetingTitle.localeCompare(b.meetingTitle));
    for (const g of out) g.items.sort((a, b) => timeMs(a.scheduledAt) - timeMs(b.scheduledAt));
    return out;
}

