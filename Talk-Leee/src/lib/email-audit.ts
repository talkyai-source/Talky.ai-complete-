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
}

const STORAGE_EMAIL_AUDIT = "talklee.email.audit.v1";

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
    private state: EmailAuditState = { items: [], hydrated: false };

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
        if (this.state.hydrated) return;
        if (typeof window === "undefined") return;
        const saved = safeParseJson<EmailAuditEntry[]>(window.localStorage.getItem(STORAGE_EMAIL_AUDIT));
        this.state = {
            items: saved ? saved.map(normalizeEntry) : [],
            hydrated: true,
        };
        this.persist();
        this.emit();
    }

    private persist() {
        if (typeof window === "undefined") return;
        if (!this.state.hydrated) return;
        try {
            window.localStorage.setItem(STORAGE_EMAIL_AUDIT, JSON.stringify(this.state.items));
        } catch {
        }
    }

    createAttempt(input: { to: string[]; templateId: string; subject?: string }): EmailAuditId {
        this.hydrateIfNeeded();
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
        this.state = { ...this.state, items: [] };
        if (typeof window !== "undefined" && this.state.hydrated) {
            try {
                window.localStorage.removeItem(STORAGE_EMAIL_AUDIT);
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
        this.state = { items: [], hydrated: false };
    }
}

export const emailAuditStore = new EmailAuditStore();

export function resetEmailAuditStoreForTests() {
    emailAuditStore.resetForTests();
}
