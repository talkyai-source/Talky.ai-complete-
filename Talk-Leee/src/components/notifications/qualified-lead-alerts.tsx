"use client";

import { useEffect, useRef } from "react";
import { useAlerts } from "@/lib/alerts-api";
import { useNotificationsActions, useNotificationsState } from "@/lib/notifications-client";

const LEGACY_SEEN_KEY = "talklee.qlead.seen.v1";
const SEEN_CAP = 500;
const FRESH_WINDOW_MS = 60 * 60 * 1000;

export function qualifiedLeadSeenKey(scopeKey: string) {
    // A new namespace prevents legacy stream IDs from being treated as proof
    // that these durable server notices were already observed.
    return `talklee.campaign-alerts.seen.v1:${scopeKey}`;
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

// This is a browser presentation of durable server notices, not a producer.
// Remount on identity changes; never consume the separate legacy event stream
// or initiate mail delivery. Keep the existing public component name/mount.
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
    const { data } = useAlerts({ severity: new Set(), type: new Set(), status: new Set() });
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
        const notices = data.filter((event) =>
            ["qualified_lead", "campaign_failures", "low_minutes"].includes(String(event.metadata?.kind))
            && !seenRef.current.has(event.id));
        for (const event of notices) seenRef.current.add(event.id);
        seenRef.current = new Set([...seenRef.current].slice(-SEEN_CAP));
        seededRef.current = true;
        try { window.localStorage.setItem(storageKey, JSON.stringify([...seenRef.current])); } catch { /* Keep deduplication in memory. */ }
        if (isSeed) return;

        const cutoff = Date.now() - FRESH_WINDOW_MS;
        for (const event of notices) {
            const created = Date.parse(event.createdAt);
            if (!Number.isFinite(created) || created < cutoff || event.status === "Resolved") continue;
            const kind = String(event.metadata?.kind);
            create({
                type: kind === "qualified_lead" ? "success" : "warning",
                title: event.title,
                message: event.description || "Review this notice in the campaign alert timeline.",
                priority: "high",
                // Persist only the server identity and notice kind. Do not
                // copy legacy stream phone numbers or raw follow-up text.
                data: { kind, server_event_id: event.id },
            });
        }
    }, [data, create, storageKey]);

    return null;
}
