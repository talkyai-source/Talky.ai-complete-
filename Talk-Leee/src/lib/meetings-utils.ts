"use client";

import type { CalendarEvent } from "@/lib/models";

function timeMs(iso: string | undefined) {
    if (!iso) return Number.NaN;
    const ms = Date.parse(iso);
    return Number.isFinite(ms) ? ms : Number.NaN;
}

export type MeetingSortKey = "startTime" | "title" | "lead" | "status";
export type SortDir = "asc" | "desc";

export function splitAndSortMeetings(items: CalendarEvent[], nowMs: number = Date.now()) {
    const upcoming: CalendarEvent[] = [];
    const past: CalendarEvent[] = [];

    for (const m of items) {
        const start = timeMs(m.startTime);
        if (!Number.isFinite(start)) {
            upcoming.push(m);
            continue;
        }
        if (start >= nowMs) upcoming.push(m);
        else past.push(m);
    }

    upcoming.sort((a, b) => timeMs(a.startTime) - timeMs(b.startTime));
    past.sort((a, b) => timeMs(b.startTime) - timeMs(a.startTime));

    return { upcoming, past };
}

export function sortMeetings(items: CalendarEvent[], key: MeetingSortKey, dir: SortDir = "asc") {
    const d = dir === "asc" ? 1 : -1;
    const collator = new Intl.Collator(undefined, { sensitivity: "base", numeric: true });
    const copy = items.slice();

    const leadValue = (m: CalendarEvent) => meetingLeadLabel(m);
    const statusValue = (m: CalendarEvent) => meetingStatusLabel(m);

    copy.sort((a, b) => {
        if (key === "startTime") {
            const am = timeMs(a.startTime);
            const bm = timeMs(b.startTime);
            const aOk = Number.isFinite(am);
            const bOk = Number.isFinite(bm);
            if (aOk && bOk) {
                if (am !== bm) return (am - bm) * d;
            } else if (aOk !== bOk) {
                return (aOk ? -1 : 1) * d;
            }
        } else if (key === "title") {
            const v = collator.compare(a.title, b.title);
            if (v !== 0) return v * d;
        } else if (key === "lead") {
            const v = collator.compare(leadValue(a), leadValue(b));
            if (v !== 0) return v * d;
        } else if (key === "status") {
            const v = collator.compare(statusValue(a), statusValue(b));
            if (v !== 0) return v * d;
        }

        const am = timeMs(a.startTime);
        const bm = timeMs(b.startTime);
        const aOk = Number.isFinite(am);
        const bOk = Number.isFinite(bm);
        if (aOk && bOk && am !== bm) return (am - bm) * d;
        if (aOk !== bOk) return (aOk ? -1 : 1) * d;
        return collator.compare(a.id, b.id) * d;
    });

    return copy;
}

export function meetingLeadLabel(m: CalendarEvent) {
    if (m.leadName) return m.leadName;
    const p0 = m.participants?.[0];
    if (p0?.name) return p0.name;
    if (p0?.email) return p0.email;
    return "—";
}

export function meetingParticipantSummary(m: CalendarEvent) {
    const parts = (m.participants ?? [])
        .map((p) => p.name ?? p.email ?? "")
        .map((v) => v.trim())
        .filter(Boolean);
    if (parts.length === 0) return "";
    const first = parts.slice(0, 2).join(", ");
    const extra = parts.length > 2 ? ` +${parts.length - 2}` : "";
    return `${first}${extra}`;
}

export function meetingStatusLabel(m: CalendarEvent) {
    const s = (m.status ?? "scheduled").toLowerCase();
    if (s === "canceled" || s === "cancelled") return "Cancelled";
    if (s === "completed") return "Completed";
    if (s === "scheduled") return "Scheduled";
    if (s === "confirmed") return "Confirmed";
    return m.status ?? "Scheduled";
}

export function meetingStatusBadgeClass(m: CalendarEvent) {
    const s = (m.status ?? "scheduled").toLowerCase();
    if (s === "canceled" || s === "cancelled") return "bg-red-500/10 text-red-600 border border-red-500/20";
    if (s === "completed") return "bg-gray-500/10 text-gray-700 border border-gray-500/20";
    if (s === "confirmed") return "bg-emerald-500/10 text-emerald-700 border border-emerald-500/20";
    return "bg-blue-500/10 text-blue-700 border border-blue-500/20";
}

export function formatMeetingDateTime(iso: string | undefined) {
    if (!iso) return "—";
    const ms = timeMs(iso);
    if (!Number.isFinite(ms)) return "—";
    return new Date(ms).toLocaleString();
}

export function sanitizeMeetingNotesHtml(html: string) {
    const trimmed = html.trim();
    if (trimmed.length === 0) return "";
    if (typeof document === "undefined") return trimmed;

    const allowed = new Set(["A", "B", "BR", "DIV", "EM", "I", "LI", "OL", "P", "SPAN", "STRONG", "U", "UL"]);
    const root = document.createElement("div");
    root.innerHTML = trimmed;

    const queue: Element[] = Array.from(root.querySelectorAll("*"));
    for (const el of queue) {
        const tag = el.tagName.toUpperCase();
        if (tag === "SCRIPT" || tag === "STYLE") {
            el.remove();
            continue;
        }

        const attrs = Array.from(el.attributes);
        for (const a of attrs) {
            const name = a.name.toLowerCase();
            const value = a.value;
            if (name.startsWith("on")) {
                el.removeAttribute(a.name);
                continue;
            }
            if (name === "href" || name === "src") {
                const v = value.trim().toLowerCase();
                if (v.startsWith("javascript:") || v.startsWith("data:")) {
                    el.removeAttribute(a.name);
                    continue;
                }
            }
            if (name === "style") el.removeAttribute(a.name);
        }

        if (!allowed.has(tag)) {
            const text = document.createTextNode(el.textContent ?? "");
            el.replaceWith(text);
        } else if (tag === "A") {
            const href = el.getAttribute("href");
            if (href) {
                el.setAttribute("rel", "noopener noreferrer");
                el.setAttribute("target", "_blank");
            } else {
                const text = document.createTextNode(el.textContent ?? "");
                el.replaceWith(text);
            }
        }
    }

    return root.innerHTML;
}
