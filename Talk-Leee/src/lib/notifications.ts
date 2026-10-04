import { z } from "zod";

export type NotificationType = "success" | "warning" | "error" | "info";
export type NotificationPriority = "low" | "normal" | "high";
/** Legacy webhook values are read only to disable them, never to send. */
export type NotificationRouting = "inApp" | "webhook" | "both" | "none";
export type ThemePreference = "light" | "dark" | "system";
export type NotificationId = string;
export interface AppNotification { id: string; type: NotificationType; priority: NotificationPriority; title: string; message?: string; createdAt: number; readAt?: number; data?: Record<string, unknown> }
export interface NotificationCategoryPreferences { enabled: boolean; priority: NotificationPriority; routing: NotificationRouting }
export interface NotificationsPrivacySettings { storeHistory: boolean; consentThirdParty: boolean }
export interface NotificationsIntegrationsSettings { webhook: { enabled: boolean; url: string } }
export interface NotificationsAccountSettings { profile: { name: string; email: string }; auth: { twoFactorEnabled: boolean }; linking: { google: boolean; github: boolean } }
export interface NotificationsSettings {
    toastDurationMs: number; soundsEnabled: boolean; theme: ThemePreference;
    category: Record<NotificationType, NotificationCategoryPreferences>;
    historyRetentionDays: number; privacy: NotificationsPrivacySettings;
    integrations: NotificationsIntegrationsSettings; account: NotificationsAccountSettings;
}
export interface NotificationIdentity { tenantId: string; userId: string }
export interface NotificationsState {
    notifications: AppNotification[]; toasts: AppNotification[]; settings: NotificationsSettings;
    hydrated: boolean; scopeKey: string | null; generation: number;
    persistence: "none" | "local" | "memory";
}
export type CreateNotificationInput = { type: NotificationType; title: string; message?: string; priority?: NotificationPriority; data?: Record<string, unknown> };
export interface CapturedNotifications {
    create: (input: CreateNotificationInput) => NotificationId | null;
    signal: AbortSignal; scopeKey: string | null; generation: number; isCurrent: () => boolean;
}
const LEGACY_KEYS = ["talklee.notifications.v1", "talklee.notifications.settings.v1"];
const HISTORY_CAP = 500;
const TYPES: NotificationType[] = ["success", "warning", "error", "info"];
const priority = z.enum(["low", "normal", "high"]);
const category = z.object({ enabled: z.boolean(), priority, routing: z.enum(["inApp", "webhook", "both", "none"]) }).partial();
const settingsSchema = z.object({
    toastDurationMs: z.number().int().min(1000).max(30000), soundsEnabled: z.boolean(), theme: z.enum(["light", "dark", "system"]),
    historyRetentionDays: z.number().int().min(1).max(365),
    category: z.object({ success: category, warning: category, error: category, info: category }).partial(),
    privacy: z.object({ storeHistory: z.boolean() }).partial(),
}).partial();
const notificationSchema = z.object({
    id: z.string().min(1).max(200), type: z.enum(["success", "warning", "error", "info"]), priority,
    title: z.string().min(1).max(1000), message: z.string().max(20000).optional(),
    createdAt: z.number().finite().nonnegative(), readAt: z.number().finite().nonnegative().optional(),
    data: z.record(z.unknown()).optional(),
});
export function defaultNotificationsSettings(): NotificationsSettings {
    return {
        toastDurationMs: 5000, soundsEnabled: true, theme: "system", historyRetentionDays: 30,
        category: { success: { enabled: true, priority: "normal", routing: "inApp" }, warning: { enabled: true, priority: "normal", routing: "inApp" }, error: { enabled: true, priority: "high", routing: "inApp" }, info: { enabled: true, priority: "low", routing: "inApp" } },
        privacy: { storeHistory: true, consentThirdParty: false }, integrations: { webhook: { enabled: false, url: "" } },
        account: { profile: { name: "", email: "" }, auth: { twoFactorEnabled: false }, linking: { google: false, github: false } },
    };
}
function normalizeSettings(value: unknown): NotificationsSettings {
    const base = defaultNotificationsSettings();
    const parsed = settingsSchema.safeParse(value);
    if (!parsed.success) return base;
    const raw = parsed.data;
    for (const type of TYPES) {
        const pref = { ...base.category[type], ...raw.category?.[type] };
        // Preserve useful local alerts, but an external-only preference does
        // not silently become permission to display an in-app alert.
        pref.routing = pref.routing === "both" ? "inApp" : pref.routing === "webhook" ? "none" : pref.routing;
        base.category[type] = pref;
    }
    return { ...base, ...raw, category: base.category, privacy: { ...base.privacy, ...raw.privacy },
        integrations: { webhook: { enabled: false, url: "" } } };
}
function parseJson(value: string | null): unknown { try { return value ? JSON.parse(value) : undefined; } catch { return undefined; } }
function prune(items: AppNotification[], days: number): AppNotification[] {
    const cutoff = Date.now() - days * 86400000;
    return items.filter(item => item.createdAt >= cutoff).slice(0, HISTORY_CAP);
}
function emptyState(generation = 0): NotificationsState {
    return { notifications: [], toasts: [], settings: defaultNotificationsSettings(), hydrated: false, scopeKey: null, generation, persistence: "none" };
}
export const EMPTY_NOTIFICATIONS_STATE = emptyState();
export function notificationScopeKey(identity: NotificationIdentity): string {
    return JSON.stringify([identity.tenantId, identity.userId]);
}
export function notificationStorageKeys(identity: NotificationIdentity) {
    const key = encodeURIComponent(notificationScopeKey(identity));
    return { history: `talklee.notifications.v2:${key}`, settings: `talklee.notifications.settings.v2:${key}`, clear: `talklee.notifications.clear.v1:${key}` };
}

export class NotificationsStore {
    private listeners = new Set<() => void>();
    private state = emptyState();
    private identity: NotificationIdentity | null = null;
    private controller = new AbortController();
    private suspended = false;
    private suspendedState: NotificationsState | null = null;
    private listeningWindow: Window | null = null;
    constructor() { this.controller.abort(); }
    subscribe(listener: () => void) { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; }
    getSnapshot() { return this.state; }
    private emit() { for (const listener of this.listeners) listener(); }
    private storage(): Storage | null { try { return typeof window === "undefined" ? null : window.localStorage; } catch { return null; } }
    private removeLegacy() { const storage = this.storage(); for (const key of LEGACY_KEYS) { try { storage?.removeItem(key); } catch { /* Never import unowned data. */ } } }
    private onStorage = (event: StorageEvent) => {
        if (!this.identity || this.suspended) return;
        const keys = notificationStorageKeys(this.identity);
        if (event.key === keys.clear) {
            if (!event.newValue) return;
            this.readScope();
            this.state = { ...this.state, notifications: [], toasts: [] };
            this.emit();
            return;
        }
        if (event.key !== null && event.key !== keys.history && event.key !== keys.settings) return;
        this.readScope();
        this.emit();
    };
    hydrateIfNeeded() {
        this.removeLegacy();
        if (typeof window !== "undefined" && this.listeningWindow !== window) {
            this.listeningWindow?.removeEventListener("storage", this.onStorage);
            window.addEventListener("storage", this.onStorage);
            this.listeningWindow = window;
        }
    }
    /** Hold display/work while a changed cross-tab token is verified by /me. */
    suspendIdentity() {
        if (!this.identity || this.suspended) return;
        this.suspended = true;
        this.suspendedState = this.state;
        this.controller.abort();
        this.controller = new AbortController();
        this.state = emptyState(this.state.generation + 1);
        this.emit();
    }
    setIdentity(next: NotificationIdentity | null) {
        const valid = next && next.tenantId.trim() && next.userId.trim() ? next : null;
        const changed = (valid ? notificationScopeKey(valid) : null) !== (this.identity ? notificationScopeKey(this.identity) : null);
        this.hydrateIfNeeded();
        if (!changed && !this.suspended) return;
        if (changed) {
            this.controller.abort();
            this.controller = new AbortController();
            if (!valid) this.controller.abort();
        }
        const generation = this.state.generation + (changed ? 1 : 0);
        const restored = !changed ? this.suspendedState : null;
        this.identity = valid ? { ...valid } : null;
        this.suspended = false;
        this.suspendedState = null;
        this.state = restored ? { ...restored, generation } : { ...emptyState(generation), scopeKey: valid ? notificationScopeKey(valid) : null, hydrated: Boolean(valid) };
        if (valid) { this.readScope(); this.persist(); }
        this.emit();
    }
    private readScope() {
        if (!this.identity || this.suspended) return;
        const storage = this.storage();
        if (!storage) { this.state = { ...this.state, persistence: "memory" }; return; }
        const keys = notificationStorageKeys(this.identity);
        try {
            const settings = normalizeSettings(parseJson(storage.getItem(keys.settings)));
            // A failed opt-out write must not silently re-enable history. Other
            // readable disk state wins on recovery; unsaved memory can be lost.
            if (this.state.persistence === "memory" && !this.state.settings.privacy.storeHistory) settings.privacy.storeHistory = false;
            const saved = parseJson(storage.getItem(keys.history));
            const items = Array.isArray(saved) ? saved.flatMap(value => { const parsed = notificationSchema.safeParse(value); return parsed.success ? [parsed.data] : []; }) : [];
            const notifications = settings.privacy.storeHistory ? prune(items, settings.historyRetentionDays) : [];
            const remainingIds = new Set(notifications.map(item => item.id));
            this.state = { ...this.state, settings, notifications, toasts: this.state.toasts.filter(item => remainingIds.has(item.id)), persistence: "local" };
        } catch { this.state = { ...this.state, persistence: "memory" }; }
    }
    private refreshBeforeMutation() {
        // Storage may have recovered, and another tab may have cleared history
        // or changed privacy before its storage event reaches this tab.
        this.readScope();
    }
    private persist() {
        if (!this.identity || this.suspended) return;
        const storage = this.storage();
        if (!storage) { this.state = { ...this.state, persistence: "memory" }; return; }
        const keys = notificationStorageKeys(this.identity);
        try {
            storage.setItem(keys.settings, JSON.stringify(this.state.settings));
            if (this.state.settings.privacy.storeHistory) storage.setItem(keys.history, JSON.stringify(this.state.notifications));
            else storage.removeItem(keys.history);
            this.state = { ...this.state, persistence: "local" };
        } catch { this.state = { ...this.state, persistence: "memory" }; }
    }
    capture(): CapturedNotifications {
        const scopeKey = this.state.scopeKey;
        const generation = this.state.generation;
        const active = !!scopeKey && !this.suspended;
        const inactive = new AbortController();
        inactive.abort();
        const signal = active ? this.controller.signal : inactive.signal;
        const isCurrent = () => active && !signal.aborted && !this.suspended && this.state.scopeKey === scopeKey && this.state.generation === generation;
        return { scopeKey, generation, signal, isCurrent, create: input => isCurrent() ? this.create(input) : null };
    }
    create(input: CreateNotificationInput): NotificationId | null {
        if (!this.state.scopeKey || this.suspended) return null;
        this.refreshBeforeMutation();
        const id = crypto.randomUUID();
        const prefs = this.state.settings.category[input.type];
        const parsed = notificationSchema.safeParse({ ...input, id, priority: input.priority ?? prefs?.priority, createdAt: Date.now() });
        if (!parsed.success || !prefs) return null;
        const item = parsed.data;
        this.state = { ...this.state, notifications: prune([item, ...this.state.notifications], this.state.settings.historyRetentionDays) };
        if (prefs.enabled && prefs.routing === "inApp") this.state = { ...this.state, toasts: [item, ...this.state.toasts.filter(toast => toast.type !== item.type)].slice(0, 5) };
        // No external browser delivery: there is no durable authorized contract.
        this.persist(); this.emit(); return id;
    }
    setSettings(patch: Partial<NotificationsSettings>) {
        if (!this.state.scopeKey || this.suspended) return;
        this.refreshBeforeMutation();
        const settings = normalizeSettings({ ...this.state.settings, ...patch });
        this.state = { ...this.state, settings, notifications: prune(this.state.notifications, settings.historyRetentionDays) };
        this.persist(); this.emit();
    }
    setCategory(type: NotificationType, patch: Partial<NotificationCategoryPreferences>) {
        this.refreshBeforeMutation();
        this.setSettings({ category: { ...this.state.settings.category, [type]: { ...this.state.settings.category[type], ...patch } } });
    }
    setPrivacy(patch: Partial<NotificationsPrivacySettings>) {
        this.refreshBeforeMutation();
        this.setSettings({ privacy: { ...this.state.settings.privacy, ...patch } });
    }
    dismissToast(id: string) {
        if (!this.state.scopeKey || this.suspended) return;
        this.state = { ...this.state, toasts: this.state.toasts.filter(item => item.id !== id) }; this.emit();
    }
    markRead(id: string) {
        if (!this.state.scopeKey || this.suspended) return;
        this.refreshBeforeMutation();
        this.state = { ...this.state, notifications: this.state.notifications.map(item => item.id === id && !item.readAt ? { ...item, readAt: Date.now() } : item) };
        this.persist(); this.emit();
    }
    markAllRead() {
        if (!this.state.scopeKey || this.suspended) return;
        this.refreshBeforeMutation();
        this.state = { ...this.state, notifications: this.state.notifications.map(item => item.readAt ? item : { ...item, readAt: Date.now() }) };
        this.persist(); this.emit();
    }
    clearAll() {
        if (!this.state.scopeKey || this.suspended) return;
        const clearKey = notificationStorageKeys(this.identity!).clear;
        this.refreshBeforeMutation();
        this.state = { ...this.state, notifications: [], toasts: [] }; this.persist(); this.emit();
        // With history disabled the key is already absent and settings may be
        // identical, so neither write produces a browser storage event. A fresh
        // secret-free marker also clears other tabs' live, memory-only alerts.
        try { this.storage()?.setItem(clearKey, crypto.randomUUID()); }
        catch { /* This tab cleared; cross-tab clearing requires writable storage. */ }
    }
    exportHistoryJson() {
        return JSON.stringify({ exportedAt: new Date().toISOString(), scopeKey: this.state.scopeKey, settings: this.state.settings, notifications: this.state.notifications }, null, 2);
    }
    dispose() { this.setIdentity(null); this.listeningWindow?.removeEventListener("storage", this.onStorage); this.listeningWindow = null; }
}
export const notificationsStore = new NotificationsStore();
