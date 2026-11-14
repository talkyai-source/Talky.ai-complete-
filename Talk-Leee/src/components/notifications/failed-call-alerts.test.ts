import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { createElement } from "react";
import { act, cleanup, waitFor } from "@testing-library/react";
import type { QueryClient } from "@tanstack/react-query";

import { FailedCallAlerts } from "@/components/notifications/failed-call-alerts";
import { dashboardApi, type Call } from "@/lib/dashboard-api";
import { NotificationsStore, notificationSeenKey, notificationsStore } from "@/lib/notifications";
import { ensureDom } from "@/test-utils/dom";
import { renderWithQueryClient } from "@/test-utils/render";

ensureDom();
const A = { userId: "user-a", tenantId: "tenant-a" };
const originalListCalls = dashboardApi.listCalls;
const originalListCallsCursor = dashboardApi.listCallsCursor;

function call(id: string, outcome: string | undefined, campaign_name = "Spring promo"): Call {
    return { id, campaign_id: "camp-1", campaign_name, lead_id: "", phone_number: "+15550001111", status: "completed",
        outcome, created_at: new Date().toISOString() };
}
let requests = 0;
function stubCalls(calls: Call[]) {
    dashboardApi.listCallsCursor = async () => { requests += 1; return { calls, nextCursor: null }; };
}
function items() { return notificationsStore.getSnapshot().notifications; }
function titles() { return items().map((item) => item.title); }
function unread() { return items().filter((item) => !item.readAt).length; }
function seenKey() { return notificationSeenKey(notificationsStore.getSnapshot().scopeKey!, "failedcall"); }
function seen(): string[] { return JSON.parse(window.localStorage.getItem(seenKey()) ?? "null"); }
const clients: QueryClient[] = [];
function mount() { const result = renderWithQueryClient(createElement(FailedCallAlerts)); clients.push(result.qc); return result; }
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
    dashboardApi.listCalls = originalListCalls;
    dashboardApi.listCallsCursor = originalListCallsCursor;
    window.localStorage.clear();
});

test("the very first run remembers existing failed calls without notifying", async () => {
    stubCalls([call("c-old-1", "no_answer"), call("c-old-2", "busy"), call("c-ok", "answered")]);
    mount();
    await waitFor(() => assert.deepEqual(seen()?.sort(), ["c-old-1", "c-old-2"]));
    assert.deepEqual(titles(), []);
});

test("one Failed call notification per failed call, none for a call that did not fail", async () => {
    window.localStorage.setItem(seenKey(), "[]");
    stubCalls([call("c-2", "busy"), call("c-1", "no_answer"), call("c-ok", "answered"), call("c-live", undefined)]);
    mount();
    await waitFor(() => assert.deepEqual(titles(), ["Failed call", "Failed call"]));
    // Newest (first in the API answer) ends up at the top of the list.
    assert.deepEqual(items().map((item) => item.message), ["Spring promo — Busy — +15550001111", "Spring promo — No Answer — +15550001111"]);
    assert.equal(items()[0].type, "error");
    assert.deepEqual(items().map((item) => item.data?.call_id), ["c-2", "c-1"]);
    assert.deepEqual(seen().sort(), ["c-1", "c-2"]);
});

test("a failed call is not notified twice: same poll again, reload, second tab", async () => {
    window.localStorage.setItem(seenKey(), "[]");
    stubCalls([call("c-1", "rejected")]);
    const first = mount();
    await waitFor(() => assert.deepEqual(titles(), ["Failed call"]));
    await act(async () => { await first.qc.invalidateQueries({ queryKey: ["failed-call-alerts"] }); });
    await waitRequested(2);
    assert.deepEqual(titles(), ["Failed call"]);
    first.unmount();
    mount();
    await waitRequested(3);
    assert.deepEqual(titles(), ["Failed call"]);
    const otherTab = new NotificationsStore();
    otherTab.setIdentity(A);
    assert.equal(otherTab.readSeen("failedcall")?.has("c-1"), true);
    otherTab.dispose();
});

test("another tab's remembered call id prevents a repeated alert", async () => {
    window.localStorage.setItem(seenKey(), "[]");
    stubCalls([]);
    const { qc } = mount();
    await waitRequested(1);
    window.localStorage.setItem(seenKey(), JSON.stringify(["seen-in-another-tab"]));
    stubCalls([call("seen-in-another-tab", "failed")]);
    await act(async () => { await qc.invalidateQueries({ queryKey: ["failed-call-alerts"] }); });
    await waitRequested(2);
    assert.deepEqual(titles(), []);
});

test("a call that failed while the app was closed is notified on the next load", async () => {
    stubCalls([call("c-before", "no_answer")]);
    const first = mount();
    await waitFor(() => assert.deepEqual(seen(), ["c-before"]));
    first.unmount();
    // App closed; a call fails meanwhile.
    stubCalls([call("c-while-closed", "unreachable"), call("c-before", "no_answer")]);
    mount();
    await waitFor(() => assert.deepEqual(titles(), ["Failed call"]));
    assert.equal(items()[0].message, "Spring promo — Unreachable — +15550001111");
    assert.deepEqual(seen().sort(), ["c-before", "c-while-closed"]);
});

test("a call without a campaign name keeps the outcome label and the number", async () => {
    window.localStorage.setItem(seenKey(), "[]");
    stubCalls([call("c-1", "voicemail", "")]);
    mount();
    await waitFor(() => assert.deepEqual(titles(), ["Failed call"]));
    assert.equal(items()[0].message, "Voicemail — +15550001111");
});

test("the error category setting is respected: disabled means history only, no popup", async () => {
    notificationsStore.setCategory("error", { enabled: false });
    window.localStorage.setItem(seenKey(), "[]");
    stubCalls([call("c-1", "busy")]);
    mount();
    await waitFor(() => assert.deepEqual(titles(), ["Failed call"]));
    assert.deepEqual(notificationsStore.getSnapshot().toasts, []);
});

test("no verified identity means no call polling", async () => {
    notificationsStore.setIdentity(null);
    stubCalls([call("c-1", "busy")]);
    await act(async () => { mount(); });
    assert.equal(requests, 0);
    assert.deepEqual(titles(), []);
});

test("the badge clears after reading a Failed call notification, on reload and in a second tab", async () => {
    window.localStorage.setItem(seenKey(), "[]");
    stubCalls([call("c-2", "busy"), call("c-1", "no_answer")]);
    mount();
    await waitFor(() => assert.equal(unread(), 2));
    notificationsStore.markRead(items()[0].id);
    assert.equal(unread(), 1);
    notificationsStore.markAllRead();
    assert.equal(unread(), 0);
    const reloaded = new NotificationsStore();
    reloaded.setIdentity(A);
    assert.equal(reloaded.getSnapshot().notifications.filter((item) => !item.readAt).length, 0);
    reloaded.dispose();
});

test("the poll asks for the first cursor page (pagination=cursor) and never the offset list with a total", async () => {
    const cursorArgs: unknown[][] = [];
    let offsetRequests = 0;
    dashboardApi.listCalls = async () => { offsetRequests += 1; return { calls: [], total: 0 }; };
    dashboardApi.listCallsCursor = async (...args) => { requests += 1; cursorArgs.push(args); return { calls: [], nextCursor: null }; };
    mount();
    await waitRequested(1);
    assert.equal(offsetRequests, 0);
    assert.equal(cursorArgs[0][0], null);
    assert.equal(cursorArgs[0][1], 100);
    assert.deepEqual(cursorArgs[0][2], {});
});
