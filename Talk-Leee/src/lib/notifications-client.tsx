"use client";

import { useCallback, useEffect, useMemo, useSyncExternalStore } from "react";
import type { CreateNotificationInput, NotificationId, NotificationType, NotificationsSettings, NotificationsState } from "@/lib/notifications";
import { notificationsStore } from "@/lib/notifications";

function useNotificationsSnapshot(): NotificationsState {
    const snapshot = useSyncExternalStore(
        (listener) => notificationsStore.subscribe(listener),
        () => notificationsStore.getSnapshot(),
        () => notificationsStore.getSnapshot()
    );

    useEffect(() => {
        notificationsStore.hydrateIfNeeded();
    }, []);

    return snapshot;
}

export function useNotificationsState() {
    const state = useNotificationsSnapshot();
    const unreadCount = useMemo(() => state.notifications.filter((n) => !n.readAt).length, [state.notifications]);
    return { ...state, unreadCount };
}

export function useNotificationsActions() {
    const create = useCallback((input: CreateNotificationInput) => notificationsStore.create(input), []);
    const dismissToast = useCallback((id: NotificationId) => notificationsStore.dismissToast(id), []);
    const markRead = useCallback((id: NotificationId) => notificationsStore.markRead(id), []);
    const markAllRead = useCallback(() => notificationsStore.markAllRead(), []);
    const clearAll = useCallback(() => notificationsStore.clearAll(), []);
    const exportHistoryJson = useCallback(() => notificationsStore.exportHistoryJson(), []);
    const setSettings = useCallback((patch: Partial<NotificationsSettings>) => notificationsStore.setSettings(patch), []);
    const setCategory = useCallback(
        (type: NotificationType, patch: Partial<NotificationsSettings["category"][NotificationType]>) =>
            notificationsStore.setCategory(type, patch),
        []
    );
    const setPrivacy = useCallback((patch: Partial<NotificationsSettings["privacy"]>) => notificationsStore.setPrivacy(patch), []);

    return {
        create,
        dismissToast,
        markRead,
        markAllRead,
        clearAll,
        exportHistoryJson,
        setSettings,
        setCategory,
        setPrivacy,
    };
}

