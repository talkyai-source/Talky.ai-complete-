"use client";

import { useEffect, useLayoutEffect, useMemo, useSyncExternalStore } from "react";
import type { NotificationId, NotificationType, NotificationsSettings } from "@/lib/notifications";
import { EMPTY_NOTIFICATIONS_STATE, notificationsStore } from "@/lib/notifications";
import { useAuth } from "@/lib/auth-context";

const subscribe = (listener: () => void) => notificationsStore.subscribe(listener);
const getSnapshot = () => notificationsStore.getSnapshot();
const getServerSnapshot = () => EMPTY_NOTIFICATIONS_STATE;
function useNotificationsSnapshot() {
    const snapshot = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
    useEffect(() => { notificationsStore.hydrateIfNeeded(); }, []);
    return snapshot;
}

/** One bridge from verified authentication to the otherwise auth-independent store. */
export function NotificationsIdentityProvider() {
    const { user, status, refreshUser, notificationSyncAvailable } = useAuth();
    useLayoutEffect(() => {
        // A foreground profile refresh can show a loading shell while the
        // existing verified identity is unchanged. Explicit session changes
        // are fenced synchronously by AuthProvider before any asynchronous work.
        notificationsStore.setIdentity(notificationSyncAvailable && (status === "authenticated" || status === "loading") && user?.tenant_id && user.id
            ? { tenantId: user.tenant_id, userId: user.id } : null);
    }, [user, status, notificationSyncAvailable]);
    // Optimistic login responses lack tenant identity. Verify before enabling
    // notifications; a verified account with no tenant stays unscoped.
    useEffect(() => {
        if (status === "authenticated" && user && !user.tenant_id) void refreshUser({ silent: true });
        // Only once per login identity, not every null-tenant /me response.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [status, user?.id]);
    useEffect(() => () => { notificationsStore.setIdentity(null); }, []);
    return null;
}

export function useNotificationsState() {
    const state = useNotificationsSnapshot();
    const unreadCount = useMemo(() => state.notifications.filter(item => !item.readAt).length, [state.notifications]);
    return { ...state, unreadCount };
}
export function useNotificationsActions() {
    const { scopeKey, generation } = useNotificationsSnapshot();
    return useMemo(() => {
        const origin = notificationsStore.capture();
        return {
            create: origin.create,
            capture: () => origin.isCurrent() ? notificationsStore.capture() : origin,
            dismissToast: (id: NotificationId) => { if (origin.isCurrent()) notificationsStore.dismissToast(id); },
            markRead: (id: NotificationId) => { if (origin.isCurrent()) notificationsStore.markRead(id); },
            markAllRead: () => { if (origin.isCurrent()) notificationsStore.markAllRead(); },
            clearAll: () => { if (origin.isCurrent()) notificationsStore.clearAll(); },
            exportHistoryJson: () => origin.isCurrent() ? notificationsStore.exportHistoryJson() : JSON.stringify({ notifications: [], scopeKey: null }),
            setSettings: (patch: Partial<NotificationsSettings>) => { if (origin.isCurrent()) notificationsStore.setSettings(patch); },
            setCategory: (type: NotificationType, patch: Partial<NotificationsSettings["category"][NotificationType]>) => { if (origin.isCurrent()) notificationsStore.setCategory(type, patch); },
            setPrivacy: (patch: Partial<NotificationsSettings["privacy"]>) => { if (origin.isCurrent()) notificationsStore.setPrivacy(patch); },
        };
        // Capture identity, not each toast/read/settings update.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [scopeKey, generation]);
}
