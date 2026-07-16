"use client";

/*
 * Global qualified-lead alerter.
 *
 * The backend now emits a `stream_events` row (category "alert", metadata
 * kind="qualified_lead") the moment a call in an active campaign qualifies a
 * lead (see call_summary/store.py). This mounts once in the dashboard layout,
 * reuses the SAME /api/v1/events poll the campaign Event Stream already runs
 * (no extra polling), and pops a toast — with the lead's name, number, and
 * follow-up — anywhere in the app.
 *
 * De-dupe: seen event ids persist in localStorage so a page refresh doesn't
 * re-toast. On the FIRST load we seed the current events as "seen" WITHOUT
 * toasting, so logging in doesn't flood the user with historical leads — only
 * genuinely new qualifications after that point raise a toast.
 */

import { useEffect, useRef } from "react";
import { useEventStream } from "@/lib/event-stream-api";
import { useNotificationsActions } from "@/lib/notifications-client";

const SEEN_KEY = "talklee.qlead.seen.v1";
const SEEN_CAP = 500;
// Only TOAST events fresher than this — a returning user must not get a burst
// of stale lead toasts (those still live in the Event Stream panel and via the
// assistant's get_qualified_leads). Toasting is for "it just happened".
const FRESH_WINDOW_MS = 60 * 60 * 1000;

export function QualifiedLeadAlerts() {
    const { data } = useEventStream("Alerts");
    const { create } = useNotificationsActions();
    const seenRef = useRef<Set<string>>(new Set());
    const seededRef = useRef(false);

    // Hydrate the seen-set once, before the first data effect runs.
    if (!seededRef.current && typeof window !== "undefined") {
        try {
            const raw = window.localStorage.getItem(SEEN_KEY);
            if (raw) {
                const ids = JSON.parse(raw) as string[];
                if (Array.isArray(ids)) ids.forEach((id) => seenRef.current.add(id));
            }
        } catch {
            /* ignore */
        }
    }

    useEffect(() => {
        if (!data || data.length === 0) return;

        const leads = data.filter(
            (e) => e.metadata?.kind === "qualified_lead" && !seenRef.current.has(e.id),
        );
        if (leads.length === 0) {
            seededRef.current = true;
            return;
        }

        // First observation with no persisted history → seed only, don't toast
        // the backlog.
        const isSeed =
            !seededRef.current &&
            (typeof window === "undefined" || !window.localStorage.getItem(SEEN_KEY));

        for (const e of leads) seenRef.current.add(e.id);
        seededRef.current = true;
        try {
            const kept = [...seenRef.current].slice(-SEEN_CAP);
            window.localStorage.setItem(SEEN_KEY, JSON.stringify(kept));
        } catch {
            /* ignore */
        }

        if (isSeed) return;

        // Even past the seed, only toast FRESH qualifications; older unseen ones
        // (e.g. from while the user was away) are absorbed silently.
        const cutoff = Date.now() - FRESH_WINDOW_MS;
        const fresh = leads.filter((e) => {
            const t = Date.parse(e.createdAt);
            return Number.isFinite(t) && t >= cutoff;
        });

        for (const e of fresh) {
            const phone = e.metadata?.phone_number;
            const note = e.description || e.metadata?.follow_up_note;
            const message = [phone ? `📞 ${phone}` : "", note ? String(note) : ""]
                .filter(Boolean)
                .join(" — ");
            create({
                type: "success",
                title: e.title, // "Qualified lead: Jane Doe · +1555…"
                message: message || "New qualified lead — tap the assistant for details.",
                priority: "high",
                data: { kind: "qualified_lead", ...(e.metadata ?? {}) },
            });
        }
    }, [data, create]);

    return null;
}
