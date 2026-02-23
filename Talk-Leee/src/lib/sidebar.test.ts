import { test } from "node:test";
import assert from "node:assert/strict";
import { resetSidebarStoreForTests, sidebarStore } from "@/lib/sidebar";

function getGlobalWindow() {
    return (globalThis as unknown as { window?: unknown }).window;
}

function setGlobalWindow(next: unknown) {
    (globalThis as unknown as { window?: unknown }).window = next;
}

function createMockWindow() {
    const store = new Map<string, string>();
    type EventListener = (e: unknown) => void;
    type StorageEventLike = { key: string; newValue: string | null };

    const listeners = new Map<string, Set<EventListener>>();

    return {
        localStorage: {
            getItem: (k: string) => store.get(k) ?? null,
            setItem: (k: string, v: string) => {
                store.set(k, v);
            },
            removeItem: (k: string) => {
                store.delete(k);
            },
        },
        addEventListener: (type: string, cb: EventListener) => {
            const set = listeners.get(type) ?? new Set();
            set.add(cb);
            listeners.set(type, set);
        },
        removeEventListener: (type: string, cb: EventListener) => {
            const set = listeners.get(type);
            if (!set) return;
            set.delete(cb);
        },
        dispatchStorageEvent: (payload: StorageEventLike) => {
            const set = listeners.get("storage");
            if (!set) return;
            for (const cb of set) cb(payload);
        },
        __getRaw: () => store,
    };
}

test("hydrate reads collapsed state from localStorage", () => {
    resetSidebarStoreForTests();
    const mockWindow = createMockWindow();
    const prevWindow = getGlobalWindow();
    setGlobalWindow(mockWindow);

    mockWindow.localStorage.setItem("talklee.sidebar.state.v1", JSON.stringify({ collapsed: true, mobileOpen: false }));
    sidebarStore.hydrateIfNeeded();

    const snap = sidebarStore.getSnapshot();
    assert.equal(snap.hydrated, true);
    assert.equal(snap.collapsed, true);
    assert.equal(snap.mobileOpen, false);

    setGlobalWindow(prevWindow);
});

test("setCollapsed persists payload", () => {
    resetSidebarStoreForTests();
    const mockWindow = createMockWindow();
    const prevWindow = getGlobalWindow();
    setGlobalWindow(mockWindow);

    sidebarStore.hydrateIfNeeded();
    sidebarStore.setCollapsed(true);

    const raw = mockWindow.__getRaw().get("talklee.sidebar.state.v1");
    assert.ok(raw);
    const parsed = JSON.parse(raw);
    assert.deepEqual(parsed, { collapsed: true, mobileOpen: false });

    setGlobalWindow(prevWindow);
});

test("storage event updates snapshot in current tab", () => {
    resetSidebarStoreForTests();
    const mockWindow = createMockWindow();
    const prevWindow = getGlobalWindow();
    setGlobalWindow(mockWindow);

    sidebarStore.hydrateIfNeeded();
    assert.equal(sidebarStore.getSnapshot().collapsed, false);

    const nextValue = JSON.stringify({ collapsed: true, mobileOpen: true });
    mockWindow.localStorage.setItem("talklee.sidebar.state.v1", nextValue);
    mockWindow.dispatchStorageEvent({ key: "talklee.sidebar.state.v1", newValue: nextValue });

    const snap = sidebarStore.getSnapshot();
    assert.equal(snap.collapsed, true);
    assert.equal(snap.mobileOpen, true);

    setGlobalWindow(prevWindow);
});
