export type EmailAuditStatus = "pending" | "success" | "failed";

export type EmailAuditId = string;

export interface EmailAuditEntry {
    id: EmailAuditId;
    createdAt: number;
    to: string[];
    templateId: string;
    subject?: string;
    status: EmailAuditStatus;
    messageId?: string;
    providerStatus?: string;
    errorMessage?: string;
}

export interface EmailAuditState {
    items: EmailAuditEntry[];
    hydrated: boolean;
    scopeKey: string | null;
    generation: number;
}

export interface EmailAuditIdentity { tenantId: string; userId: string }
const LEGACY_STORAGE_EMAIL_AUDIT = "talklee.email.audit.v1";
function emptyState(generation = 0): EmailAuditState {
    return { items: [], hydrated: false, scopeKey: null, generation };
}
export const EMPTY_EMAIL_AUDIT_STATE = emptyState();
export function emailAuditStorageKey(identity: EmailAuditIdentity) {
    return `talklee.email.audit.v2:${encodeURIComponent(JSON.stringify([identity.tenantId, identity.userId]))}`;
}

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
    return `email_${Math.random().toString(16).slice(2)}_${Date.now().toString(16)}`;
}

function normalizeEntry(e: EmailAuditEntry): EmailAuditEntry {
    return {
        ...e,
        createdAt: Number.isFinite(e.createdAt) ? e.createdAt : nowMs(),
        to: Array.isArray(e.to) ? e.to.filter((x) => typeof x === "string") : [],
        status: e.status ?? "pending",
    };
}

type Listener = () => void;

class EmailAuditStore {
    private listeners = new Set<Listener>();
    private state: EmailAuditState = emptyState();
    private identity: EmailAuditIdentity | null = null;
    private suspendedState: EmailAuditState | null = null;

    subscribe(listener: Listener) {
        this.listeners.add(listener);
        return () => this.listeners.delete(listener);
    }

    getSnapshot(): EmailAuditState {
        return this.state;
    }

    private emit() {
        for (const l of this.listeners) l();
    }

    hydrateIfNeeded() {
        // v1 has no provable owner. Never import it into the current account.
        try { if (typeof window !== "undefined") window.localStorage.removeItem(LEGACY_STORAGE_EMAIL_AUDIT); } catch { /* Storage may be unavailable. */ }
    }

    setIdentity(next: EmailAuditIdentity | null) {
        const valid = next && typeof next.tenantId === "string" && next.tenantId.trim()
            && typeof next.userId === "string" && next.userId.trim() ? { ...next } : null;
        const key = valid ? emailAuditStorageKey(valid) : null;
        const previousKey = this.identity ? emailAuditStorageKey(this.identity) : null;
        this.hydrateIfNeeded();
        if (key === previousKey && !this.suspendedState) return;
        const restored = key === previousKey ? this.suspendedState : null;
        this.identity = valid;
        this.suspendedState = null;
        this.state = { ...emptyState(this.state.generation + 1), scopeKey: key,
            hydrated: Boolean(valid), items: restored?.items ?? [] };
        if (key && typeof window !== "undefined") {
            try {
                const saved = safeParseJson<EmailAuditEntry[]>(window.localStorage.getItem(key));
                this.state = { ...this.state, items: Array.isArray(saved) ? saved
                    .filter(entry => entry && typeof entry === "object" && typeof entry.id === "string" && typeof entry.templateId === "string")
                    .map(normalizeEntry) : [] };
            } catch { /* A verified same-identity refresh can retain memory if storage is unavailable. */ }
        }
        this.emit();
    }

    suspendIdentity() {
        if (!this.identity || this.suspendedState) return;
        this.suspendedState = this.state;
        this.state = emptyState(this.state.generation + 1);
        this.emit();
    }

    clearForLogout() {
        if (this.identity && typeof window !== "undefined") {
            try { window.localStorage.removeItem(emailAuditStorageKey(this.identity)); } catch { /* Still retire memory below. */ }
        }
        this.setIdentity(null);
    }

    capture() {
        const { scopeKey, generation } = this.state;
        const isCurrent = () => !!scopeKey && this.state.scopeKey === scopeKey && this.state.generation === generation;
        return {
            isCurrent,
            createAttempt: (input: { to: string[]; templateId: string; subject?: string }) => {
                if (!isCurrent()) throw new Error("Email history identity changed. Sign in again.");
                return this.createAttempt(input);
            },
            markSuccess: (id: EmailAuditId, patch: { messageId?: string; providerStatus?: string }) => {
                if (isCurrent()) this.markSuccess(id, patch);
            },
            markFailed: (id: EmailAuditId, patch: { errorMessage?: string; providerStatus?: string }) => {
                if (isCurrent()) this.markFailed(id, patch);
            },
            clearAll: () => { if (isCurrent()) this.clearAll(); },
            exportHistoryJson: () => isCurrent() ? this.exportHistoryJson() : JSON.stringify({ exportedAt: new Date().toISOString(), items: [] }),
        };
    }

    private persist() {
        if (typeof window === "undefined") return;
        if (!this.state.scopeKey) return;
        try {
            window.localStorage.setItem(this.state.scopeKey, JSON.stringify(this.state.items));
        } catch {
        }
    }

    createAttempt(input: { to: string[]; templateId: string; subject?: string }): EmailAuditId {
        this.hydrateIfNeeded();
        if (!this.state.scopeKey) throw new Error("A verified account identity is required for email history.");
        const id = randomId();
        const entry: EmailAuditEntry = {
            id,
            createdAt: nowMs(),
            to: input.to,
            templateId: input.templateId,
            subject: input.subject,
            status: "pending",
        };
        this.state = { ...this.state, items: [entry, ...this.state.items] };
        this.persist();
        this.emit();
        return id;
    }

    markSuccess(id: EmailAuditId, patch: { messageId?: string; providerStatus?: string }) {
        if (!this.state.scopeKey) return;
        this.state = {
            ...this.state,
            items: this.state.items.map((e) =>
                e.id === id ? { ...e, status: "success", messageId: patch.messageId, providerStatus: patch.providerStatus } : e
            ),
        };
        this.persist();
        this.emit();
    }

    markFailed(id: EmailAuditId, patch: { errorMessage?: string; providerStatus?: string }) {
        if (!this.state.scopeKey) return;
        this.state = {
            ...this.state,
            items: this.state.items.map((e) =>
                e.id === id ? { ...e, status: "failed", errorMessage: patch.errorMessage, providerStatus: patch.providerStatus } : e
            ),
        };
        this.persist();
        this.emit();
    }

    clearAll() {
        const key = this.state.scopeKey;
        if (!key) return;
        this.state = { ...this.state, items: [] };
        if (typeof window !== "undefined" && this.state.hydrated) {
            try {
                window.localStorage.removeItem(key);
            } catch {
            }
        }
        this.emit();
    }

    exportHistoryJson() {
        const payload = { exportedAt: new Date().toISOString(), items: this.state.items };
        return JSON.stringify(payload, null, 2);
    }

    resetForTests() {
        this.listeners.clear();
        this.identity = null;
        this.suspendedState = null;
        this.state = emptyState(this.state.generation + 1);
    }
}

export const emailAuditStore = new EmailAuditStore();

export function resetEmailAuditStoreForTests() {
    emailAuditStore.resetForTests();
}
