import { afterEach, beforeEach, mock, test } from "node:test";
import assert from "node:assert/strict";
import { NotificationsStore, notificationStorageKeys, notificationScopeKey, type NotificationIdentity } from "@/lib/notifications";

const alice = { tenantId: "tenant-a", userId: "alice" };
const bob = { tenantId: "tenant-b", userId: "bob" };
const instances: NotificationsStore[] = [];
function store(identity: NotificationIdentity | null = alice) {
    const result = new NotificationsStore(); instances.push(result); result.setIdentity(identity); return result;
}
function savedItem(id: string, createdAt = Date.now()) {
    return { id, type: "info", priority: "normal", title: `Notice ${id}`, createdAt };
}
function storageEvent(key: string) {
    window.dispatchEvent(new window.StorageEvent("storage", { key, storageArea: window.localStorage }));
}
beforeEach(() => { window.localStorage.clear(); });
afterEach(() => {
    mock.restoreAll();
    for (const instance of instances.splice(0)) instance.dispose();
    window.localStorage.clear();
});

test("unverified identity cannot hydrate or publish an owned notification", () => {
    const instance = store(null);
    assert.equal(instance.capture().signal.aborted, true);
    assert.equal(instance.create({ type: "success", title: "Anonymous" }), null);
    assert.equal(instance.capture().create({ type: "success", title: "Anonymous" }), null);
    assert.deepEqual(instance.getSnapshot().notifications, []);
    assert.equal(window.localStorage.length, 0);
});

test("same-owner create, dismiss, read, reload and clear retain their existing behavior", () => {
    const instance = store();
    const first = instance.create({ type: "success", title: "Saved" });
    const second = instance.create({ type: "warning", title: "Review" });
    assert.ok(first && second);
    instance.dismissToast(first);
    assert.equal(instance.getSnapshot().toasts.some(item => item.id === first), false);
    instance.markRead(first);
    assert.ok(instance.getSnapshot().notifications.find(item => item.id === first)?.readAt);
    assert.equal(instance.getSnapshot().notifications.find(item => item.id === second)?.readAt, undefined);
    instance.markAllRead();
    const reloaded = store();
    assert.equal(reloaded.getSnapshot().notifications.length, 2);
    assert.ok(reloaded.getSnapshot().notifications.every(item => item.readAt));
    reloaded.clearAll();
    assert.deepEqual(reloaded.getSnapshot().notifications, []);
});

for (const other of [bob, { tenantId: alice.tenantId, userId: "another-user" }, { tenantId: "another-tenant", userId: alice.userId }]) {
    test(`history, settings and exports isolate ${other.tenantId}/${other.userId}`, () => {
        const instance = store();
        instance.create({ type: "success", title: "Alice private lead" });
        instance.setSettings({ soundsEnabled: false });
        const pending = instance.capture();
        instance.setIdentity(other);
        assert.equal(pending.signal.aborted, true);
        assert.deepEqual(instance.getSnapshot().notifications, []);
        assert.equal(instance.getSnapshot().settings.soundsEnabled, true);
        instance.create({ type: "info", title: "Other account" });
        const exported = JSON.parse(instance.exportHistoryJson());
        assert.equal(exported.scopeKey, notificationScopeKey(other));
        assert.deepEqual(exported.notifications.map((item: { title: string }) => item.title), ["Other account"]);
        instance.setIdentity(alice);
        assert.equal(instance.getSnapshot().notifications[0]?.title, "Alice private lead");
        assert.equal(instance.getSnapshot().settings.soundsEnabled, false);
        assert.equal(pending.create({ type: "error", title: "Late response" }), null);
    });
}

test("logout and verification revoke old callbacks, while a same-identity refresh preserves them", () => {
    const instance = store();
    const initial = instance.capture();
    instance.setIdentity({ ...alice });
    assert.equal(initial.isCurrent(), true);
    initial.create({ type: "success", title: "Before verification" });
    instance.suspendIdentity();
    assert.equal(initial.signal.aborted, true);
    assert.deepEqual(instance.getSnapshot().notifications, []);
    instance.setIdentity(alice);
    assert.equal(instance.getSnapshot().notifications[0]?.title, "Before verification");
    const current = instance.capture();
    instance.setIdentity(null);
    assert.deepEqual(instance.getSnapshot().toasts, []);
    assert.equal(JSON.parse(instance.exportHistoryJson()).scopeKey, null);
    instance.setIdentity(alice);
    assert.equal(current.create({ type: "error", title: "Late old session" }), null);
    assert.equal(initial.create({ type: "error", title: "Late verification" }), null);
});

test("legacy unowned records are removed and malformed scoped records never become valid history", () => {
    window.localStorage.setItem("talklee.notifications.v1", JSON.stringify([savedItem("unowned")]));
    window.localStorage.setItem("talklee.notifications.settings.v1", JSON.stringify({ integrations: { webhook: { enabled: true, url: "https://invalid.example/private" } } }));
    const keys = notificationStorageKeys(alice);
    window.localStorage.setItem(keys.history, JSON.stringify([savedItem("valid"), null, { ...savedItem("bad"), type: "unknown" }, { ...savedItem("time"), createdAt: "yesterday" }]));
    window.localStorage.setItem(keys.settings, "{invalid JSON");
    const instance = store();
    assert.deepEqual(instance.getSnapshot().notifications.map(item => item.id), ["valid"]);
    assert.equal(window.localStorage.getItem("talklee.notifications.v1"), null);
    assert.equal(window.localStorage.getItem("talklee.notifications.settings.v1"), null);
    assert.equal(instance.getSnapshot().settings.integrations.webhook.url, "");
    assert.equal(instance.getSnapshot().settings.soundsEnabled, true);
});

test("hydration rejects non-array history and enforces retention and bounded history", () => {
    const keys = notificationStorageKeys(alice);
    window.localStorage.setItem(keys.history, JSON.stringify({ invalid: "shape" }));
    assert.deepEqual(store().getSnapshot().notifications, []);
    window.localStorage.setItem(keys.history, JSON.stringify([savedItem("expired", Date.now() - 31 * 86400000), ...Array.from({ length: 503 }, (_, index) => savedItem(`fresh-${index}`))]));
    const instance = store();
    assert.equal(instance.getSnapshot().notifications.length, 500);
    assert.equal(instance.getSnapshot().notifications.some(item => item.id === "expired"), false);
});

for (const reason of ["SecurityError", "QuotaExceededError"]) {
    test(`${reason} degrades to memory without carrying history into another account`, () => {
        if (reason === "SecurityError") mock.method(window.Storage.prototype, "getItem", () => { throw new window.DOMException("Blocked", reason); });
        mock.method(window.Storage.prototype, "setItem", () => { throw new window.DOMException("Unavailable", reason); });
        const instance = store();
        instance.create({ type: "info", title: "Memory only" });
        assert.equal(instance.getSnapshot().persistence, "memory");
        assert.equal(instance.getSnapshot().notifications[0]?.title, "Memory only");
        instance.setIdentity(bob);
        assert.deepEqual(instance.getSnapshot().notifications, []);
    });
}

test("same-owner tabs cannot resurrect cleared history before a delayed storage event", () => {
    const first = store(); first.create({ type: "info", title: "Old" });
    const second = store();
    first.clearAll();
    second.create({ type: "success", title: "New" });
    assert.deepEqual(second.getSnapshot().notifications.map(item => item.title), ["New"]);
    storageEvent(notificationStorageKeys(alice).history);
    assert.deepEqual(first.getSnapshot().notifications.map(item => item.title), ["New"]);
    second.clearAll(); storageEvent(notificationStorageKeys(alice).history);
    assert.deepEqual(first.getSnapshot().notifications, []);
    assert.deepEqual(first.getSnapshot().toasts, []);
});

test("privacy opt-out and storage events remain scoped and prevent old-tab persistence", () => {
    const first = store(); first.create({ type: "info", title: "Alice old" });
    const stale = store();
    const other = store(bob); other.create({ type: "info", title: "Bob retained" });
    first.setPrivacy({ storeHistory: false });
    stale.markAllRead();
    assert.equal(window.localStorage.getItem(notificationStorageKeys(alice).history), null);
    assert.equal(stale.getSnapshot().settings.privacy.storeHistory, false);
    storageEvent(notificationStorageKeys(alice).settings);
    assert.deepEqual(stale.getSnapshot().notifications, []);
    assert.deepEqual(other.getSnapshot().notifications.map(item => item.title), ["Bob retained"]);
    assert.equal(JSON.parse(window.localStorage.getItem(notificationStorageKeys(bob).history) ?? "[]")[0]?.title, "Bob retained");
});

test("storage recovery respects another tab's clear and privacy opt-out before writing", () => {
    const recovering = store(); recovering.create({ type: "info", title: "Before quota failure" });
    const otherTab = store();
    const failedWrite = mock.method(window.Storage.prototype, "setItem", () => { throw new window.DOMException("Full", "QuotaExceededError"); });
    recovering.create({ type: "info", title: "Unsaved old-tab notice" });
    assert.equal(recovering.getSnapshot().persistence, "memory");
    failedWrite.mock.restore();
    otherTab.clearAll();
    otherTab.setPrivacy({ storeHistory: false });
    // No storage event yet: disk is authoritative for privacy after recovery.
    recovering.create({ type: "success", title: "Current notice" });
    assert.equal(window.localStorage.getItem(notificationStorageKeys(alice).history), null);
    assert.equal(recovering.getSnapshot().settings.privacy.storeHistory, false);
    assert.equal(recovering.getSnapshot().notifications.some(item => item.title === "Before quota failure" || item.title === "Unsaved old-tab notice"), false);
});

test("browser webhook settings neither send HTTP nor retain an external destination", () => {
    const fetch = mock.method(globalThis, "fetch", async () => { throw new Error("Unexpected external request"); });
    window.localStorage.setItem(notificationStorageKeys(alice).settings, JSON.stringify({
        category: { success: { enabled: true, routing: "both" }, error: { enabled: true, routing: "webhook" } },
        privacy: { storeHistory: true, consentThirdParty: true },
        integrations: { webhook: { enabled: true, url: "https://invalid.example/destination" } },
    }));
    const instance = store();
    instance.create({ type: "success", title: "Local only" });
    instance.create({ type: "error", title: "External stays silent" });
    assert.equal(fetch.mock.callCount(), 0);
    assert.equal(instance.getSnapshot().settings.integrations.webhook.enabled, false);
    assert.equal(instance.getSnapshot().settings.privacy.consentThirdParty, false);
    assert.equal(instance.getSnapshot().toasts.some(item => item.title === "External stays silent"), false);
    assert.equal(instance.exportHistoryJson().includes("invalid.example"), false);
});

test("clear reaches another tab when privacy is off and history/settings writes are browser no-ops", () => {
    const first = store();
    first.create({ type: "info", title: "Live private one" });
    first.create({ type: "success", title: "Live private two" });
    const stale = store();
    const otherAccount = store(bob); otherAccount.create({ type: "info", title: "Bob remains" });
    first.setPrivacy({ storeHistory: false });
    assert.equal(first.getSnapshot().notifications.length, 2);
    assert.equal(window.localStorage.getItem(notificationStorageKeys(alice).history), null);
    // Real Storage applies writes, while this transport model follows browser
    // semantics: identical setItem / removing an absent key emit no event.
    const changed: { key: string; oldValue: string | null; newValue: string | null }[] = [];
    const originalSet = window.Storage.prototype.setItem;
    const originalRemove = window.Storage.prototype.removeItem;
    const set = mock.method(window.Storage.prototype, "setItem", function (this: Storage, key: string, value: string) {
        const oldValue = this.getItem(key); originalSet.call(this, key, value);
        const newValue = this.getItem(key); if (newValue !== oldValue) changed.push({ key, oldValue, newValue });
    });
    const remove = mock.method(window.Storage.prototype, "removeItem", function (this: Storage, key: string) {
        const oldValue = this.getItem(key); originalRemove.call(this, key);
        if (oldValue !== null) changed.push({ key, oldValue, newValue: null });
    });
    stale.clearAll();
    set.mock.restore(); remove.mock.restore();
    assert.equal(stale.getSnapshot().settings.privacy.storeHistory, false);
    assert.equal(changed.some(event => event.key === notificationStorageKeys(alice).history || event.key === notificationStorageKeys(alice).settings), false);
    for (const event of changed) window.dispatchEvent(new window.StorageEvent("storage", { ...event, storageArea: window.localStorage }));
    assert.deepEqual(first.getSnapshot().notifications, []);
    assert.deepEqual(first.getSnapshot().toasts, []);
    assert.deepEqual(otherAccount.getSnapshot().notifications.map(item => item.title), ["Bob remains"]);
    assert.equal(window.localStorage.getItem(notificationStorageKeys(alice).history), null);
    assert.equal(changed.some(event => event.newValue?.includes("Live private")), false);
});
