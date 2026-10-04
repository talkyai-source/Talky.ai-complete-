"use client";

import { useEffect, useRef } from "react";
import { useEventStream } from "@/lib/event-stream-api";
import { useNotificationsActions, useNotificationsState } from "@/lib/notifications-client";

const LEGACY_SEEN_KEY = "talklee.qlead.seen.v1";
const SEEN_CAP = 500;
const FRESH_WINDOW_MS = 60 * 60 * 1000;

export function qualifiedLeadSeenKey(scopeKey: string) {
    return `talklee.qlead.seen.v2:${scopeKey}`;
}

function readSeen(key: string): string[] | null {
    try {
        const raw = window.localStorage.getItem(key);
        if (raw === null) return null;
        const value: unknown = JSON.parse(raw);
        return Array.isArray(value) ? value.filter((id): id is string => typeof id === "string").slice(-SEEN_CAP) : null;
    } catch {
        return null;
    }
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
    const { create } = useNotificationsActions();
    const seenRef = useRef<Set<string>>(new Set());
    const seededRef = useRef(false);
    const storageKey = qualifiedLeadSeenKey(scopeKey);

    useEffect(() => {
        const onStorage = (event: StorageEvent) => {
            if (event.key !== storageKey) return;
            for (const id of readSeen(storageKey) ?? []) seenRef.current.add(id);
        };
        window.addEventListener("storage", onStorage);
        return () => window.removeEventListener("storage", onStorage);
    }, [storageKey]);

    useEffect(() => {
        if (data === undefined) return;
        // Read before each observation so sequential observations in another
        // tab are respected even before its storage event is delivered.
        const saved = readSeen(storageKey);
        for (const id of saved ?? []) seenRef.current.add(id);
        const isSeed = !seededRef.current && saved === null;
        const leads = data.filter((event) =>
            event.metadata?.kind === "qualified_lead" && !seenRef.current.has(event.id));
        for (const event of leads) seenRef.current.add(event.id);
        seenRef.current = new Set([...seenRef.current].slice(-SEEN_CAP));
        seededRef.current = true;
        try { window.localStorage.setItem(storageKey, JSON.stringify([...seenRef.current])); } catch { /* Keep deduplication in memory. */ }
        if (isSeed) return;

        const cutoff = Date.now() - FRESH_WINDOW_MS;
        for (const event of leads) {
            const created = Date.parse(event.createdAt);
            if (!Number.isFinite(created) || created < cutoff) continue;
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
    }, [data, create, storageKey]);

    return null;
}
