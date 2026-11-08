import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import { createElement } from "react";
import { act, cleanup, waitFor } from "@testing-library/react";

import { QualifiedLeadAlerts, qualifiedLeadSeenKey } from "@/components/notifications/qualified-lead-alerts";
import { backendApi } from "@/lib/backend-api";
import { NotificationsStore, notificationsStore } from "@/lib/notifications";
import { ensureDom } from "@/test-utils/dom";
import { renderWithQueryClient } from "@/test-utils/render";

ensureDom();
const originalEventsList = backendApi.events.list;
type RawEvent = Awaited<ReturnType<typeof backendApi.events.list>>["items"][number];
const A = { userId: "user-a", tenantId: "tenant-a" };
const B = { userId: "user-b", tenantId: "tenant-b" };

function rawLead(id: string, ageMs = 0): RawEvent {
    return { id, category: "alert", title: `Qualified lead: ${id}`, description: "Wants a callback",
        severity: "info", related_campaign_id: null, related_call_id: null, actor_user_id: null,
        metadata: { kind: "qualified_lead", phone_number: "+15550001111" },
        created_at: new Date(Date.now() - ageMs).toISOString() };
}
function result(items: RawEvent[], owner?: typeof A) {
    const [tenantId, userId] = JSON.parse(notificationsStore.getSnapshot().scopeKey ?? '["",""]') as [string, string];
    return { items, next_cursor: null, tenant_id: owner?.tenantId ?? tenantId, user_id: owner?.userId ?? userId };
}
function seenKey() { return qualifiedLeadSeenKey(notificationsStore.getSnapshot().scopeKey!); }
function titles() { return notificationsStore.getSnapshot().notifications.map((item) => item.title); }
function stubEvents(items: RawEvent[]) { backendApi.events.list = async () => result(items); }
async function waitSeeded() {
    await waitFor(() => assert.notEqual(window.localStorage.getItem(seenKey()), null));
}

beforeEach(() => {
    window.localStorage.clear();
    notificationsStore.setIdentity(null);
    notificationsStore.setIdentity(A);
});
afterEach(() => {
    cleanup();
    notificationsStore.setIdentity(null);
    backendApi.events.list = originalEventsList;
    window.localStorage.clear();
});

test("scoped saved lead IDs suppress repeats while fresh leads create in-app alerts", async () => {
    window.localStorage.setItem(seenKey(), JSON.stringify(["evt-seen"]));
    stubEvents([rawLead("evt-seen"), rawLead("evt-fresh")]);
    renderWithQueryClient(createElement(QualifiedLeadAlerts));
    await waitFor(() => assert.deepEqual(titles(), ["Qualified lead: evt-fresh"]));
    assert.deepEqual(JSON.parse(window.localStorage.getItem(seenKey())!).sort(), ["evt-fresh", "evt-seen"]);
});

test("a first load notifies every lead present, without adopting legacy or another identity's seen IDs", async () => {
    window.localStorage.setItem("talklee.qlead.seen.v1", JSON.stringify(["evt-legacy"]));
    const keyA = seenKey();
    window.localStorage.setItem(keyA, JSON.stringify(["evt-a"]));
    notificationsStore.setIdentity(B);
    stubEvents([rawLead("evt-a"), rawLead("evt-new")]);
    renderWithQueryClient(createElement(QualifiedLeadAlerts));
    await waitSeeded();
    // N3: no first-load skip. B has shown neither lead; A's seen IDs are A's.
    await waitFor(() => assert.deepEqual(titles(), ["Qualified lead: evt-new", "Qualified lead: evt-a"]));
    assert.notEqual(seenKey(), keyA);
    assert.deepEqual(JSON.parse(window.localStorage.getItem(seenKey())!).sort(), ["evt-a", "evt-new"]);
    assert.equal(window.localStorage.getItem("talklee.qlead.seen.v1"), null);
});

test("a lead older than 60 minutes is notified like a fresh one", async () => {
    window.localStorage.setItem(seenKey(), JSON.stringify(["evt-seen"]));
    stubEvents([rawLead("evt-old", 3 * 60 * 60 * 1000), rawLead("evt-new")]);
    renderWithQueryClient(createElement(QualifiedLeadAlerts));
    await waitFor(() => assert.deepEqual(titles(), ["Qualified lead: evt-new", "Qualified lead: evt-old"]));
});

test("no lead is notified twice: same poll again, reload, second tab", async () => {
    stubEvents([rawLead("evt-1")]);
    const first = renderWithQueryClient(createElement(QualifiedLeadAlerts));
    await waitFor(() => assert.deepEqual(titles(), ["Qualified lead: evt-1"]));
    await act(async () => { await first.qc.invalidateQueries({ queryKey: ["events"] }); });
    assert.deepEqual(titles(), ["Qualified lead: evt-1"]);
    first.unmount();
    const second = renderWithQueryClient(createElement(QualifiedLeadAlerts));
    await waitFor(() => assert.notEqual(second.qc.getQueryCache().getAll()[0]?.state.status, "pending"));
    assert.deepEqual(titles(), ["Qualified lead: evt-1"]);
    const otherTab = new NotificationsStore();
    otherTab.setIdentity(A);
    assert.equal(otherTab.readSeen("qlead")?.has("evt-1"), true);
    otherTab.dispose();
});

test("a lead that arrived while the tab was hidden is notified when it becomes visible", async () => {
    stubEvents([]);
    renderWithQueryClient(createElement(QualifiedLeadAlerts));
    await waitSeeded();
    const visibility = { value: "hidden" as DocumentVisibilityState };
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => visibility.value });
    try {
        await act(async () => { window.dispatchEvent(new window.Event("visibilitychange")); });
        stubEvents([rawLead("evt-while-hidden", 10 * 60 * 1000)]);
        // The hidden period outlasts the 5 s stale window of the event poll.
        mock.timers.enable({ apis: ["Date"], now: Date.now() + 6_000 });
        visibility.value = "visible";
        await act(async () => { window.dispatchEvent(new window.Event("visibilitychange")); });
        await waitFor(() => assert.deepEqual(titles(), ["Qualified lead: evt-while-hidden"]));
    } finally {
        mock.timers.reset();
        Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
    }
});

test("an initially empty stream still alerts on the next newly qualified lead", async () => {
    stubEvents([]);
    const { qc } = renderWithQueryClient(createElement(QualifiedLeadAlerts));
    await waitSeeded();
    stubEvents([rawLead("first-new")]);
    await act(async () => { await qc.invalidateQueries({ queryKey: ["events"] }); });
    await waitFor(() => assert.deepEqual(titles(), ["Qualified lead: first-new"]));
});

test("late A event response cannot populate B's query or notifications after an identity switch", async () => {
    let resolveA!: (value: ReturnType<typeof result>) => void;
    let startedA = false;
    backendApi.events.list = async () => {
        startedA = true;
        return new Promise((resolve) => { resolveA = resolve; });
    };
    const { qc } = renderWithQueryClient(createElement(QualifiedLeadAlerts));
    await waitFor(() => assert.equal(startedA, true));
    stubEvents([]);
    await act(async () => { notificationsStore.setIdentity(B); });
    await waitSeeded();
    await act(async () => { resolveA(result([rawLead("A-private")], A)); });
    assert.deepEqual(titles(), []);
    const current = notificationsStore.getSnapshot();
    const ownQueries = qc.getQueriesData({ queryKey: ["events", current.scopeKey, current.generation] });
    assert.ok(ownQueries.length > 0);
    assert.ok(ownQueries.every(([, rows]) => JSON.stringify(rows).includes("A-private") === false));
    stubEvents([rawLead("B-new")]);
    await act(async () => { await qc.invalidateQueries({ queryKey: ["events", current.scopeKey] }); });
    await waitFor(() => assert.deepEqual(titles(), ["Qualified lead: B-new"]));
});

test("another tab's scoped seen update prevents a repeated alert", async () => {
    stubEvents([]);
    const { qc } = renderWithQueryClient(createElement(QualifiedLeadAlerts));
    await waitSeeded();
    window.localStorage.setItem(seenKey(), JSON.stringify(["seen-in-another-tab"]));
    stubEvents([rawLead("seen-in-another-tab")]);
    await act(async () => { await qc.invalidateQueries({ queryKey: ["events"] }); });
    assert.deepEqual(titles(), []);
});

test("no verified identity means no lead polling or alert hydration", async () => {
    notificationsStore.setIdentity(null);
    let requested = false;
    backendApi.events.list = async () => { requested = true; return result([rawLead("private")]); };
    await act(async () => { renderWithQueryClient(createElement(QualifiedLeadAlerts)); });
    assert.equal(requested, false);
    assert.deepEqual(titles(), []);
});

for (const [name, owner] of [
    ["missing owner", null],
    ["same user in another tenant", { userId: A.userId, tenantId: B.tenantId }],
    ["another user in the same tenant", { userId: B.userId, tenantId: A.tenantId }],
] as const) {
    test(`a response with ${name} cannot enter A's cache before the account marker arrives`, async () => {
        window.localStorage.setItem(seenKey(), "[]");
        backendApi.events.list = async () => owner
            ? result([rawLead("wrong-owner-private")], owner)
            : ({ items: [rawLead("wrong-owner-private")], next_cursor: null } as unknown as ReturnType<typeof result>);
        const { qc } = renderWithQueryClient(createElement(QualifiedLeadAlerts));
        // A deliberately remains the active browser identity throughout. The
        // server cookie may already belong to B before the storage marker arrives.
        await waitFor(() => assert.notEqual(qc.getQueryCache().getAll()[0]?.state.status, "pending"));
        const query = qc.getQueryCache().getAll()[0];
        assert.equal(query.state.status, "error");
        assert.equal(query.state.data, undefined);
        assert.deepEqual(titles(), []);
        assert.equal(window.localStorage.getItem(seenKey()), "[]");
    });
}
