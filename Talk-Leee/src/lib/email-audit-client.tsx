"use client";

import { useEffect, useMemo, useSyncExternalStore } from "react";
import type { EmailAuditState } from "@/lib/email-audit";
import { emailAuditStore, EMPTY_EMAIL_AUDIT_STATE } from "@/lib/email-audit";

function useEmailAuditSnapshot(): EmailAuditState {
    const snapshot = useSyncExternalStore(
        (listener) => emailAuditStore.subscribe(listener),
        () => emailAuditStore.getSnapshot(),
        () => EMPTY_EMAIL_AUDIT_STATE
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
    const { scopeKey, generation } = useEmailAuditSnapshot();
    return useMemo(() => emailAuditStore.capture(),
        // Keep actions stable during history updates, but retire them on identity changes.
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [scopeKey, generation]);
}

