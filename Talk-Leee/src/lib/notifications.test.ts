import { test } from "node:test";
import assert from "node:assert/strict";
import { notificationsStore } from "@/lib/notifications";

function getGlobalWindow() {
    return (globalThis as unknown as { window?: unknown }).window;
}

function setGlobalWindow(next: unknown) {
    (globalThis as unknown as { window?: unknown }).window = next;
}

function resetStorage() {
    const store = new Map<string, string>();
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
    };
}

test("create adds notification and toast by default", () => {
    const mockWindow = resetStorage();
    const prevWindow = getGlobalWindow();
    setGlobalWindow(mockWindow);

    notificationsStore.hydrateIfNeeded();
    const id = notificationsStore.create({ type: "success", title: "Saved", message: "Done" });
    const snap = notificationsStore.getSnapshot();

    assert.equal(typeof id, "string");
    assert.equal(snap.notifications.length, 1);
    assert.equal(snap.toasts.length, 1);
    assert.equal(snap.notifications[0].type, "success");
    assert.equal(snap.notifications[0].readAt, undefined);

    setGlobalWindow(prevWindow);
});

test("markRead and markAllRead set readAt", () => {
    const mockWindow = resetStorage();
    const prevWindow = getGlobalWindow();
    setGlobalWindow(mockWindow);

    notificationsStore.hydrateIfNeeded();
    const a = notificationsStore.create({ type: "info", title: "A" });
    const b = notificationsStore.create({ type: "warning", title: "B" });

    notificationsStore.markRead(a);
    let snap = notificationsStore.getSnapshot();
    const aItem = snap.notifications.find((n) => n.id === a);
    const bItem = snap.notifications.find((n) => n.id === b);
    assert.ok(aItem?.readAt);
    assert.equal(bItem?.readAt, undefined);

    notificationsStore.markAllRead();
    snap = notificationsStore.getSnapshot();
    assert.ok(snap.notifications.every((n) => n.readAt));

    setGlobalWindow(prevWindow);
});

test("dismissToast removes toast only", () => {
    const mockWindow = resetStorage();
    const prevWindow = getGlobalWindow();
    setGlobalWindow(mockWindow);

    notificationsStore.hydrateIfNeeded();
    const id = notificationsStore.create({ type: "error", title: "Oops" });
    let snap = notificationsStore.getSnapshot();
    assert.equal(snap.toasts.some((t) => t.id === id), true);
    assert.equal(snap.notifications.some((n) => n.id === id), true);

    notificationsStore.dismissToast(id);
    snap = notificationsStore.getSnapshot();
    assert.equal(snap.toasts.some((t) => t.id === id), false);
    assert.equal(snap.notifications.some((n) => n.id === id), true);

    setGlobalWindow(prevWindow);
});

test("clearAll removes history and toasts", () => {
    const mockWindow = resetStorage();
    const prevWindow = getGlobalWindow();
    setGlobalWindow(mockWindow);

    notificationsStore.hydrateIfNeeded();
    notificationsStore.create({ type: "success", title: "One" });
    notificationsStore.create({ type: "success", title: "Two" });
    notificationsStore.clearAll();
    const snap = notificationsStore.getSnapshot();
    assert.equal(snap.notifications.length, 0);
    assert.equal(snap.toasts.length, 0);

    setGlobalWindow(prevWindow);
});
