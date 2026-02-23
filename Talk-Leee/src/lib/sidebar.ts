export interface SidebarState {
    collapsed: boolean;
    mobileOpen: boolean;
    hydrated: boolean;
}

export type SidebarPersistedState = Pick<SidebarState, "collapsed" | "mobileOpen">;

const STORAGE_KEY = "talklee.sidebar.state.v1";

type Listener = () => void;

function safeParseJson<T>(text: string | null): T | undefined {
    if (!text) return undefined;
    try {
        return JSON.parse(text) as T;
    } catch {
        return undefined;
    }
}

class SidebarStore {
    private listeners = new Set<Listener>();
    private storageListenerInstalled = false;
    private state: SidebarState = { collapsed: false, mobileOpen: false, hydrated: false };

    subscribe(listener: Listener) {
        this.listeners.add(listener);
        return () => this.listeners.delete(listener);
    }

    getSnapshot(): SidebarState {
        return this.state;
    }

    private emit() {
        for (const l of this.listeners) l();
    }

    hydrateIfNeeded() {
        if (this.state.hydrated) return;
        if (typeof window === "undefined") return;

        const saved = safeParseJson<SidebarPersistedState>(window.localStorage.getItem(STORAGE_KEY));
        if (saved) {
            this.state = {
                collapsed: Boolean(saved.collapsed),
                mobileOpen: Boolean(saved.mobileOpen),
                hydrated: true,
            };
        } else {
            this.state = { ...this.state, hydrated: true };
        }

        if (!this.storageListenerInstalled) {
            this.storageListenerInstalled = true;
            window.addEventListener("storage", (e) => {
                if (e.key !== STORAGE_KEY) return;
                const next = safeParseJson<SidebarPersistedState>(e.newValue);
                if (!next) return;
                const collapsed = Boolean(next.collapsed);
                const mobileOpen = Boolean(next.mobileOpen);
                if (collapsed === this.state.collapsed && mobileOpen === this.state.mobileOpen) return;
                this.state = { ...this.state, collapsed, mobileOpen };
                this.emit();
            });
        }

        this.persist();
        this.emit();
    }

    private persist() {
        if (typeof window === "undefined") return;
        if (!this.state.hydrated) return;
        try {
            const payload: SidebarPersistedState = {
                collapsed: this.state.collapsed,
                mobileOpen: this.state.mobileOpen,
            };
            window.localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
        } catch {
        }
    }

    setCollapsed(next: boolean) {
        if (next === this.state.collapsed) return;
        this.state = { ...this.state, collapsed: next };
        this.persist();
        this.emit();
    }

    setMobileOpen(next: boolean) {
        if (next === this.state.mobileOpen) return;
        this.state = { ...this.state, mobileOpen: next };
        this.persist();
        this.emit();
    }

    toggleCollapsed() {
        this.setCollapsed(!this.state.collapsed);
    }

    toggleMobile() {
        this.setMobileOpen(!this.state.mobileOpen);
    }

    closeMobile() {
        this.setMobileOpen(false);
    }

    resetForTests() {
        this.listeners.clear();
        this.storageListenerInstalled = false;
        this.state = { collapsed: false, mobileOpen: false, hydrated: false };
    }
}

export const sidebarStore = new SidebarStore();

export function resetSidebarStoreForTests() {
    sidebarStore.resetForTests();
}
