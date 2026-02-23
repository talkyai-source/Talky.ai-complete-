"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";
import type { EmailAuditId, EmailAuditState } from "@/lib/email-audit";
import { emailAuditStore } from "@/lib/email-audit";

function useEmailAuditSnapshot(): EmailAuditState {
    const snapshot = useSyncExternalStore(
        (listener) => emailAuditStore.subscribe(listener),
        () => emailAuditStore.getSnapshot(),
        () => emailAuditStore.getSnapshot()
    );

    useEffect(() => {
        emailAuditStore.hydrateIfNeeded();
    }, []);

    return snapshot;
}

export function useEmailAuditState() {
    return useEmailAuditSnapshot();
}

export function useEmailAuditActions() {
    const createAttempt = useCallback((input: { to: string[]; templateId: string; subject?: string }) => emailAuditStore.createAttempt(input), []);
    const markSuccess = useCallback((id: EmailAuditId, patch: { messageId?: string; providerStatus?: string }) => emailAuditStore.markSuccess(id, patch), []);
    const markFailed = useCallback((id: EmailAuditId, patch: { errorMessage?: string; providerStatus?: string }) => emailAuditStore.markFailed(id, patch), []);
    const clearAll = useCallback(() => emailAuditStore.clearAll(), []);
    const exportHistoryJson = useCallback(() => emailAuditStore.exportHistoryJson(), []);
    return { createAttempt, markSuccess, markFailed, clearAll, exportHistoryJson };
}

