import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { emailAuditStore, resetEmailAuditStoreForTests } from "./email-audit";

const A = { tenantId: "tenant-one", userId: "person-a" };
const B = { tenantId: "tenant-one", userId: "person-b" };
const OTHER_TENANT = { tenantId: "tenant-two", userId: "person-a" };
const recipient = "private-recipient@example.invalid";
const attempt = () => emailAuditStore.createAttempt({ to: [recipient], templateId: "private-template", subject: "Private subject" });

beforeEach(() => { window.localStorage.clear(); resetEmailAuditStoreForTests(); });
afterEach(() => { resetEmailAuditStoreForTests(); window.localStorage.clear(); });

test("unowned legacy history is not inherited by the next verified user", () => {
    window.localStorage.setItem("talklee.email.audit.v1", JSON.stringify([
        { id: "old", to: [recipient], templateId: "legacy", createdAt: Date.now(), status: "success" },
    ]));
    emailAuditStore.hydrateIfNeeded();
    assert.deepEqual(emailAuditStore.getSnapshot().items, [], "Legacy history has no provable account owner");
    emailAuditStore.setIdentity(A);
    assert.deepEqual(emailAuditStore.getSnapshot().items, []);
    assert.equal(window.localStorage.getItem("talklee.email.audit.v1"), null);
});

for (const other of [B, OTHER_TENANT]) test(`email history is isolated from ${other.tenantId}/${other.userId}`, () => {
    emailAuditStore.setIdentity(A);
    const id = attempt();
    emailAuditStore.markSuccess(id, { messageId: "message-a" });
    emailAuditStore.setIdentity(other);
    assert.deepEqual(emailAuditStore.getSnapshot().items, []);
    assert.ok(!emailAuditStore.exportHistoryJson().includes(recipient));
    emailAuditStore.setIdentity(A);
    assert.equal(emailAuditStore.getSnapshot().items[0]?.messageId, "message-a");
});

test("logout retires memory and removes only the signed-out user's saved history", () => {
    emailAuditStore.setIdentity(B);
    const b = attempt();
    emailAuditStore.setIdentity(A);
    attempt();
    emailAuditStore.clearAll();
    emailAuditStore.setIdentity(null);
    assert.deepEqual(emailAuditStore.getSnapshot().items, []);
    resetEmailAuditStoreForTests();
    emailAuditStore.setIdentity(A);
    assert.deepEqual(emailAuditStore.getSnapshot().items, []);
    emailAuditStore.setIdentity(B);
    assert.equal(emailAuditStore.getSnapshot().items[0]?.id, b);
});

test("unverified identity cannot create or export another user's email attempts", () => {
    emailAuditStore.setIdentity(A);
    attempt();
    emailAuditStore.setIdentity(null);
    assert.throws(attempt, /sign.in|identity|account/i);
    assert.ok(!emailAuditStore.exportHistoryJson().includes(recipient));
});
