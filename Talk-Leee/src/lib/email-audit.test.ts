import { test } from "node:test";
import assert from "node:assert/strict";
import { emailAuditStore, resetEmailAuditStoreForTests } from "@/lib/email-audit";

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
        __getStore: () => store,
    };
}

test("createAttempt records pending entry and persists to storage", () => {
    const mockWindow = resetStorage();
    const prevWindow = getGlobalWindow();
    setGlobalWindow(mockWindow);
    resetEmailAuditStoreForTests();

    emailAuditStore.hydrateIfNeeded();
    const id = emailAuditStore.createAttempt({ to: ["a@x.com"], templateId: "tpl-1", subject: "Hello" });
    const snap = emailAuditStore.getSnapshot();

    assert.equal(typeof id, "string");
    assert.equal(snap.hydrated, true);
    assert.equal(snap.items.length, 1);
    assert.equal(snap.items[0]!.id, id);
    assert.equal(snap.items[0]!.status, "pending");
    assert.deepEqual(snap.items[0]!.to, ["a@x.com"]);
    assert.equal(snap.items[0]!.templateId, "tpl-1");
    assert.equal(snap.items[0]!.subject, "Hello");

    const saved = mockWindow.localStorage.getItem("talklee.email.audit.v1");
    assert.ok(saved);
    const parsed = JSON.parse(saved!) as unknown;
    assert.ok(Array.isArray(parsed));

    setGlobalWindow(prevWindow);
});

test("markSuccess and markFailed update the audit entry", () => {
    const mockWindow = resetStorage();
    const prevWindow = getGlobalWindow();
    setGlobalWindow(mockWindow);
    resetEmailAuditStoreForTests();

    emailAuditStore.hydrateIfNeeded();
    const id = emailAuditStore.createAttempt({ to: ["a@x.com"], templateId: "tpl-1" });
    emailAuditStore.markSuccess(id, { messageId: "msg-1", providerStatus: "queued" });
    let snap = emailAuditStore.getSnapshot();
    assert.equal(snap.items[0]!.status, "success");
    assert.equal(snap.items[0]!.messageId, "msg-1");
    assert.equal(snap.items[0]!.providerStatus, "queued");

    emailAuditStore.markFailed(id, { errorMessage: "bounce", providerStatus: "failed" });
    snap = emailAuditStore.getSnapshot();
    assert.equal(snap.items[0]!.status, "failed");
    assert.equal(snap.items[0]!.errorMessage, "bounce");
    assert.equal(snap.items[0]!.providerStatus, "failed");

    setGlobalWindow(prevWindow);
});

test("exportHistoryJson includes exportedAt and items", () => {
    const mockWindow = resetStorage();
    const prevWindow = getGlobalWindow();
    setGlobalWindow(mockWindow);
    resetEmailAuditStoreForTests();

    emailAuditStore.hydrateIfNeeded();
    emailAuditStore.createAttempt({ to: ["a@x.com"], templateId: "tpl-1" });

    const json = emailAuditStore.exportHistoryJson();
    const parsed = JSON.parse(json) as { exportedAt: string; items: unknown[] };
    assert.equal(typeof parsed.exportedAt, "string");
    assert.ok(Array.isArray(parsed.items));
    assert.equal(parsed.items.length, 1);

    setGlobalWindow(prevWindow);
});

test("clearAll removes items and clears storage", () => {
    const mockWindow = resetStorage();
    const prevWindow = getGlobalWindow();
    setGlobalWindow(mockWindow);
    resetEmailAuditStoreForTests();

    emailAuditStore.hydrateIfNeeded();
    emailAuditStore.createAttempt({ to: ["a@x.com"], templateId: "tpl-1" });
    assert.ok(mockWindow.localStorage.getItem("talklee.email.audit.v1"));

    emailAuditStore.clearAll();
    const snap = emailAuditStore.getSnapshot();
    assert.equal(snap.items.length, 0);
    assert.equal(mockWindow.localStorage.getItem("talklee.email.audit.v1"), null);

    setGlobalWindow(prevWindow);
});

