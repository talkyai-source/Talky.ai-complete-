import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { createElement } from "react";
import { act, cleanup, waitFor } from "@testing-library/react";
import type { QueryClient } from "@tanstack/react-query";

import { LowMinutesAlerts } from "@/components/notifications/low-minutes-alerts";
import { api } from "@/lib/api";
import { NotificationsStore, notificationSeenKey, notificationsStore } from "@/lib/notifications";
import type { TopupBalance } from "@/lib/topup-api";
import { ensureDom } from "@/test-utils/dom";
import { renderWithQueryClient } from "@/test-utils/render";

ensureDom();
const A = { userId: "user-a", tenantId: "tenant-a" };
const apiRecord = api as unknown as { request: unknown };
const originalRequest = apiRecord.request;

function balance(remaining: number, allocated = 100, unlimited = false): TopupBalance {
    return { allocated, used_minutes: Math.max(0, allocated - remaining), remaining_minutes: remaining, unlimited,
        exhausted: remaining <= 0, purchased_minutes: 0, topup_minutes_left: 0 };
}
let requests = 0;
function stubBalance(value: TopupBalance) { apiRecord.request = async () => { requests += 1; return value; }; }
function items() { return notificationsStore.getSnapshot().notifications; }
function titles() { return items().map((item) => item.title); }
function unread() { return items().filter((item) => !item.readAt).length; }
function seenKey() { return notificationSeenKey(notificationsStore.getSnapshot().scopeKey!, "lowminutes"); }
const clients: QueryClient[] = [];
function mount() { const result = renderWithQueryClient(createElement(LowMinutesAlerts)); clients.push(result.qc); return result; }
/** The poll has answered `count` times, every query is idle and effects have flushed. */
async function waitRequested(count: number) {
    await waitFor(() => assert.equal(requests >= count, true));
    await waitFor(() => assert.equal(clients.every((qc) => qc.getQueryCache().getAll().every((query) => query.state.fetchStatus === "idle")), true));
    await act(async () => {});
}

beforeEach(() => {
    requests = 0;
    clients.splice(0);
    window.localStorage.clear();
    notificationsStore.setIdentity(null);
    notificationsStore.setIdentity(A);
});
afterEach(() => {
    cleanup();
    notificationsStore.setIdentity(null);
    apiRecord.request = originalRequest;
    window.localStorage.clear();
});

test("becoming low creates one Low minutes notification with the Billing label and figures", async () => {
    stubBalance(balance(12));
    mount();
    await waitFor(() => assert.deepEqual(titles(), ["Low minutes"]));
    const item = items()[0];
    assert.equal(item.type, "warning");
    assert.equal(item.message, "Minutes remaining: 12 / 100");
    assert.equal(notificationsStore.getSnapshot().toasts.some((toast) => toast.id === item.id), true);
    assert.deepEqual(JSON.parse(window.localStorage.getItem(seenKey())!), ["low"]);
});

test("no second notification while minutes stay low, including after a reload and in a second tab", async () => {
    stubBalance(balance(12));
    const first = mount();
    await waitFor(() => assert.deepEqual(titles(), ["Low minutes"]));
    stubBalance(balance(9));
    await act(async () => { await first.qc.invalidateQueries({ queryKey: ["low-minutes-alerts"] }); });
    await waitRequested(2);
    assert.deepEqual(titles(), ["Low minutes"]);
    // Reload: a fresh observer over the same saved account state.
    first.unmount();
    mount();
    await waitRequested(3);
    assert.deepEqual(titles(), ["Low minutes"]);
    // Second tab: another store over the same browser storage sees the marker.
    const otherTab = new NotificationsStore();
    otherTab.setIdentity(A);
    assert.equal(otherTab.readSeen("lowminutes")?.has("low"), true);
    otherTab.dispose();
});

test("minutes that stop being low and become low again notify once more", async () => {
    stubBalance(balance(12));
    const { qc } = mount();
    await waitFor(() => assert.deepEqual(titles(), ["Low minutes"]));
    stubBalance(balance(80));
    await act(async () => { await qc.invalidateQueries({ queryKey: ["low-minutes-alerts"] }); });
    await waitFor(() => assert.equal(window.localStorage.getItem(seenKey()), "[]"));
    assert.deepEqual(titles(), ["Low minutes"]);
    stubBalance(balance(10));
    await act(async () => { await qc.invalidateQueries({ queryKey: ["low-minutes-alerts"] }); });
    await waitFor(() => assert.deepEqual(titles(), ["Low minutes", "Low minutes"]));
    assert.equal(items()[0].message, "Minutes remaining: 10 / 100");
});

test("an unlimited plan is never low", async () => {
    stubBalance(balance(0, 0, true));
    mount();
    await waitRequested(1);
    assert.deepEqual(titles(), []);
    assert.equal(window.localStorage.getItem(seenKey()), null);
});

test("a balance above the low rule creates nothing", async () => {
    stubBalance(balance(15));
    mount();
    await waitRequested(1);
    assert.deepEqual(titles(), []);
});

test("the warning category setting is respected: disabled means history only, no popup", async () => {
    notificationsStore.setCategory("warning", { enabled: false });
    stubBalance(balance(12));
    mount();
    await waitFor(() => assert.deepEqual(titles(), ["Low minutes"]));
    assert.deepEqual(notificationsStore.getSnapshot().toasts, []);
});

test("the badge clears after reading a Low minutes notification, on reload and in a second tab", async () => {
    stubBalance(balance(12));
    mount();
    await waitFor(() => assert.deepEqual(titles(), ["Low minutes"]));
    assert.equal(unread(), 1);
    notificationsStore.markRead(items()[0].id);
    assert.equal(unread(), 0);
    const reloaded = new NotificationsStore();
    reloaded.setIdentity(A);
    assert.equal(reloaded.getSnapshot().notifications.filter((item) => !item.readAt).length, 0);
    reloaded.dispose();
    // Mark all from this tab after a second unread item appears.
    stubBalance(balance(80));
    const { qc } = mount();
    await waitFor(() => assert.equal(window.localStorage.getItem(seenKey()), "[]"));
    stubBalance(balance(5));
    await act(async () => { await qc.invalidateQueries({ queryKey: ["low-minutes-alerts"] }); });
    await waitFor(() => assert.equal(unread(), 1));
    notificationsStore.markAllRead();
    assert.equal(unread(), 0);
    const secondTab = new NotificationsStore();
    secondTab.setIdentity(A);
    assert.equal(secondTab.getSnapshot().notifications.filter((item) => !item.readAt).length, 0);
    secondTab.dispose();
});
