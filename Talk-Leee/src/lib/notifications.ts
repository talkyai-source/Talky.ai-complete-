export type NotificationType = "success" | "warning" | "error" | "info";

export type NotificationPriority = "low" | "normal" | "high";

export type NotificationRouting = "inApp" | "webhook" | "both" | "none";

export type ThemePreference = "light" | "dark" | "system";

export type NotificationId = string;

export interface AppNotification {
    id: NotificationId;
    type: NotificationType;
    priority: NotificationPriority;
    title: string;
    message?: string;
    createdAt: number;
    readAt?: number;
    data?: Record<string, unknown>;
}

export interface NotificationCategoryPreferences {
    enabled: boolean;
    priority: NotificationPriority;
    routing: NotificationRouting;
}

export interface NotificationsPrivacySettings {
    storeHistory: boolean;
    consentThirdParty: boolean;
}

export interface NotificationsIntegrationsSettings {
    webhook: {
        enabled: boolean;
        url: string;
    };
}

export interface NotificationsAccountSettings {
    profile: {
        name: string;
        email: string;
    };
    auth: {
        twoFactorEnabled: boolean;
    };
    linking: {
        google: boolean;
        github: boolean;
    };
}

export interface NotificationsSettings {
    toastDurationMs: number;
    soundsEnabled: boolean;
    theme: ThemePreference;
    category: Record<NotificationType, NotificationCategoryPreferences>;
    historyRetentionDays: number;
    privacy: NotificationsPrivacySettings;
    integrations: NotificationsIntegrationsSettings;
    account: NotificationsAccountSettings;
}

export interface NotificationsState {
    notifications: AppNotification[];
    toasts: AppNotification[];
    settings: NotificationsSettings;
    hydrated: boolean;
}

export type CreateNotificationInput = {
    type: NotificationType;
    title: string;
    message?: string;
    priority?: NotificationPriority;
    data?: Record<string, unknown>;
};

const STORAGE_NOTIFICATIONS = "talklee.notifications.v1";
const STORAGE_SETTINGS = "talklee.notifications.settings.v1";

function safeParseJson<T>(text: string | null): T | undefined {
    if (!text) return undefined;
    try {
        return JSON.parse(text) as T;
    } catch {
        return undefined;
    }
}

function nowMs() {
    return Date.now();
}

function randomId() {
    if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
    return `ntf_${Math.random().toString(16).slice(2)}_${Date.now().toString(16)}`;
}

export function defaultNotificationsSettings(): NotificationsSettings {
    return {
        toastDurationMs: 5000,
        soundsEnabled: true,
        theme: "system",
        category: {
            success: { enabled: true, priority: "normal", routing: "inApp" },
            warning: { enabled: true, priority: "normal", routing: "inApp" },
            error: { enabled: true, priority: "high", routing: "inApp" },
            info: { enabled: true, priority: "low", routing: "inApp" },
        },
        historyRetentionDays: 30,
        privacy: {
            storeHistory: true,
            consentThirdParty: false,
        },
        integrations: {
            webhook: {
                enabled: false,
                url: "",
            },
        },
        account: {
            profile: { name: "Demo User", email: "demo@talk-lee.ai" },
            auth: { twoFactorEnabled: false },
            linking: { google: false, github: false },
        },
    };
}

function pruneNotifications(items: AppNotification[], retentionDays: number, now = nowMs()) {
    if (!Number.isFinite(retentionDays) || retentionDays <= 0) return items;
    const cutoff = now - retentionDays * 24 * 60 * 60 * 1000;
    return items.filter((n) => n.createdAt >= cutoff);
}

function normalizeNotification(n: AppNotification): AppNotification {
    return {
        ...n,
        createdAt: Number.isFinite(n.createdAt) ? n.createdAt : nowMs(),
    };
}

type Listener = () => void;

class NotificationsStore {
    private listeners = new Set<Listener>();
    private state: NotificationsState = {
        notifications: [],
        toasts: [],
        settings: defaultNotificationsSettings(),
        hydrated: false,
    };

    subscribe(listener: Listener) {
        this.listeners.add(listener);
        return () => this.listeners.delete(listener);
    }

    getSnapshot(): NotificationsState {
        return this.state;
    }

    private emit() {
        for (const l of this.listeners) l();
    }

    hydrateIfNeeded() {
        if (this.state.hydrated) return;
        if (typeof window === "undefined") return;

        const savedSettings = safeParseJson<NotificationsSettings>(window.localStorage.getItem(STORAGE_SETTINGS));
        const savedNotifications = safeParseJson<AppNotification[]>(window.localStorage.getItem(STORAGE_NOTIFICATIONS));

        const settings = savedSettings ? { ...defaultNotificationsSettings(), ...savedSettings } : this.state.settings;
        const notifications = savedNotifications
            ? pruneNotifications(savedNotifications.map(normalizeNotification), settings.historyRetentionDays)
            : this.state.notifications;

        this.state = {
            ...this.state,
            settings,
            notifications,
            hydrated: true,
        };
        this.persist();
        this.emit();
    }

    private persist() {
        if (typeof window === "undefined") return;
        if (!this.state.hydrated) return;

        try {
            window.localStorage.setItem(STORAGE_SETTINGS, JSON.stringify(this.state.settings));
        } catch {
        }

        if (!this.state.settings.privacy.storeHistory) return;

        try {
            window.localStorage.setItem(STORAGE_NOTIFICATIONS, JSON.stringify(this.state.notifications));
        } catch {
        }
    }

    setSettings(patch: Partial<NotificationsSettings>) {
        this.state = {
            ...this.state,
            settings: { ...this.state.settings, ...patch },
        };
        this.state = {
            ...this.state,
            notifications: pruneNotifications(this.state.notifications, this.state.settings.historyRetentionDays),
        };
        this.persist();
        this.emit();
    }

    setCategory(type: NotificationType, patch: Partial<NotificationCategoryPreferences>) {
        this.setSettings({
            category: {
                ...this.state.settings.category,
                [type]: { ...this.state.settings.category[type], ...patch },
            },
        });
    }

    setPrivacy(patch: Partial<NotificationsPrivacySettings>) {
        this.setSettings({
            privacy: { ...this.state.settings.privacy, ...patch },
        });
        if (typeof window !== "undefined" && this.state.hydrated && !this.state.settings.privacy.storeHistory) {
            try {
                window.localStorage.removeItem(STORAGE_NOTIFICATIONS);
            } catch {
            }
        }
    }

    create(input: CreateNotificationInput): NotificationId {
        const id = randomId();
        const type = input.type;
        const categoryDefaults = this.state.settings.category[type];
        const n: AppNotification = {
            id,
            type,
            title: input.title,
            message: input.message,
            priority: input.priority ?? categoryDefaults.priority,
            createdAt: nowMs(),
            data: input.data,
        };

        const nextNotifications = [n, ...this.state.notifications];
        const pruned = pruneNotifications(nextNotifications, this.state.settings.historyRetentionDays, n.createdAt);
        this.state = { ...this.state, notifications: pruned };

        const routing = categoryDefaults.routing;
        const enabled = categoryDefaults.enabled;
        const showInApp = routing === "inApp" || routing === "both";
        if (enabled && showInApp) {
            const nextToasts = [n, ...this.state.toasts.filter((t) => t.type !== n.type)].slice(0, 5);
            this.state = { ...this.state, toasts: nextToasts };
        }

        this.persist();
        this.emit();

        const sendWebhook =
            enabled &&
            (routing === "webhook" || routing === "both") &&
            this.state.settings.privacy.consentThirdParty &&
            this.state.settings.integrations.webhook.enabled &&
            Boolean(this.state.settings.integrations.webhook.url);

        if (sendWebhook && typeof window !== "undefined") {
            const url = this.state.settings.integrations.webhook.url;
            window
                .fetch(url, {
                    method: "POST",
                    headers: { "content-type": "application/json" },
                    body: JSON.stringify({
                        id: n.id,
                        type: n.type,
                        priority: n.priority,
                        title: n.title,
                        message: n.message,
                        createdAt: n.createdAt,
                        data: n.data ?? null,
                    }),
                })
                .catch(() => {});
        }

        return id;
    }

    dismissToast(id: NotificationId) {
        if (!this.state.toasts.some((t) => t.id === id)) return;
        this.state = { ...this.state, toasts: this.state.toasts.filter((t) => t.id !== id) };
        this.emit();
    }

    markRead(id: NotificationId) {
        let changed = false;
        const next = this.state.notifications.map((n) => {
            if (n.id !== id) return n;
            if (n.readAt) return n;
            changed = true;
            return { ...n, readAt: nowMs() };
        });
        if (!changed) return;
        this.state = { ...this.state, notifications: next };
        this.persist();
        this.emit();
    }

    markAllRead() {
        const now = nowMs();
        let changed = false;
        const next = this.state.notifications.map((n) => {
            if (n.readAt) return n;
            changed = true;
            return { ...n, readAt: now };
        });
        if (!changed) return;
        this.state = { ...this.state, notifications: next };
        this.persist();
        this.emit();
    }

    clearAll() {
        this.state = { ...this.state, notifications: [], toasts: [] };
        if (typeof window !== "undefined" && this.state.hydrated) {
            try {
                window.localStorage.removeItem(STORAGE_NOTIFICATIONS);
            } catch {
            }
        }
        this.emit();
    }

    exportHistoryJson() {
        const payload = {
            exportedAt: new Date().toISOString(),
            settings: this.state.settings,
            notifications: this.state.notifications,
        };
        return JSON.stringify(payload, null, 2);
    }
}

export const notificationsStore = new NotificationsStore();
