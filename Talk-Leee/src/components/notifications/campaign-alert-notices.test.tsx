import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { createElement } from "react";
import { act, cleanup, waitFor } from "@testing-library/react";

import { QualifiedLeadAlerts, qualifiedLeadSeenKey } from "@/components/notifications/qualified-lead-alerts";
import { useAlerts } from "@/lib/alerts-api";
import { backendApi } from "@/lib/backend-api";
import { notificationsStore } from "@/lib/notifications";
import { ensureDom } from "@/test-utils/dom";
import { renderWithQueryClient } from "@/test-utils/render";

ensureDom();
const A = { tenantId: "tenant-a", userId: "user-a" };
const B = { tenantId: "tenant-b", userId: "user-b" };
type Alert = Awaited<ReturnType<typeof backendApi.alerts.list>>["items"][number];
const originalAlerts = backendApi.alerts.list;
const originalEvents = backendApi.events.list;
const originalFetch = globalThis.fetch;
let alertRequests = 0;
let eventRequests = 0;
let networkRequests = 0;
let rows: Alert[] = [];

function notice(id: string, kind = "qualified_lead", age = 0): Alert {
    const createdAt = new Date(Date.now() - age).toISOString();
    return { id, title: `Server ${kind}: ${id}`, description: `Safe server notice ${id}`,
        severity: kind === "qualified_lead" ? "Info" : "Warning",
        type: kind === "low_minutes" ? "System" : "Campaign", status: "Active",
        createdAt, updatedAt: createdAt, acknowledged: false, relatedCampaignIds: [],
        metadata: { kind }, resolutionNotes: null };
}
function result(items: Alert[], owner?: typeof A) {
    const [tenantId, userId] = JSON.parse(notificationsStore.getSnapshot().scopeKey ?? '["",""]') as [string, string];
    return { items, next_cursor: null, tenant_id: owner?.tenantId ?? tenantId, user_id: owner?.userId ?? userId };
}
function key() { return qualifiedLeadSeenKey(notificationsStore.getSnapshot().scopeKey!); }
function notifications() { return notificationsStore.getSnapshot().notifications; }
function AlertsRead() {
    const query = useAlerts({ severity: new Set(), type: new Set(), status: new Set() });
    return createElement("output", { "data-testid": "server-alerts" }, JSON.stringify(query.data ?? []));
}
async function seeded() {
    await waitFor(() => assert.notEqual(window.localStorage.getItem(key()), null));
}

beforeEach(() => {
    window.localStorage.clear();
    notificationsStore.setIdentity(null);
    notificationsStore.setIdentity(A);
    alertRequests = eventRequests = networkRequests = 0;
    rows = [];
    backendApi.alerts.list = async () => { alertRequests += 1; return result(rows); };
    backendApi.events.list = async () => {
        eventRequests += 1;
        return { items: [], next_cursor: null, tenant_id: A.tenantId, user_id: A.userId };
    };
    globalThis.fetch = async () => { networkRequests += 1; throw new Error("No network/email in H35 fixtures"); };
});
afterEach(() => {
    cleanup();
    notificationsStore.setIdentity(null);
    backendApi.alerts.list = originalAlerts;
    backendApi.events.list = originalEvents;
    globalThis.fetch = originalFetch;
    window.localStorage.clear();
    assert.equal(networkRequests, 0, "Browser notices must not initiate email/webhook delivery");
});

test("the observer displays all three durable notice kinds and never polls the legacy event source", async () => {
    window.localStorage.setItem(key(), "[]");
    rows = [notice("qualified"), notice("failures", "campaign_failures"), notice("minutes", "low_minutes")];
    renderWithQueryClient(createElement(QualifiedLeadAlerts));
    await waitFor(() => assert.equal(notifications().length, 3));
    assert.equal(eventRequests, 0);
    assert.ok(alertRequests > 0);
    assert.deepEqual(new Set(notifications().map(item => item.data?.server_event_id)), new Set(rows.map(row => row.id)));
    assert.deepEqual(new Set(notifications().map(item => item.type)), new Set(["success", "warning"]));
    assert.ok(notifications().every(item => item.message?.startsWith("Safe server notice")));
});

test("poll repeats and a remount reuse durable IDs without creating another local notice", async () => {
    const first = renderWithQueryClient(createElement(QualifiedLeadAlerts));
    await seeded();
    rows = [notice("stable-server-id", "low_minutes")];
    await act(async () => { await first.qc.invalidateQueries({ queryKey: ["alerts"] }); });
    await waitFor(() => assert.equal(notifications().length, 1));
    const id = notifications()[0].id;
    await act(async () => { await first.qc.invalidateQueries({ queryKey: ["alerts"] }); });
    first.unmount();
    renderWithQueryClient(createElement(QualifiedLeadAlerts));
    await waitFor(() => assert.ok(alertRequests >= 3));
    assert.deepEqual(notifications().map(item => item.id), [id]);
    assert.deepEqual(JSON.parse(window.localStorage.getItem(key())!), ["stable-server-id"]);
});

test("first observation seeds existing server events silently, and an old unseen event does not toast", async () => {
    rows = [notice("existing", "campaign_failures")];
    const world = renderWithQueryClient(createElement(QualifiedLeadAlerts));
    await seeded();
    assert.deepEqual(notifications(), []);
    assert.deepEqual(JSON.parse(window.localStorage.getItem(key())!), ["existing"]);
    rows = [notice("older", "low_minutes", 3 * 60 * 60 * 1000), notice("newer")];
    await act(async () => { await world.qc.invalidateQueries({ queryKey: ["alerts"] }); });
    await waitFor(() => assert.deepEqual(notifications().map(item => item.data?.server_event_id), ["newer"]));
});

test("a legacy qualified-lead stream event cannot manufacture a second server notice", async () => {
    window.localStorage.setItem(key(), "[]");
    backendApi.events.list = async () => {
        eventRequests += 1;
        return { items: [{ id: "legacy-event", category: "alert", title: "Legacy duplicate",
            description: "Legacy private follow-up", severity: "info", related_campaign_id: null,
            related_call_id: null, actor_user_id: null, metadata: { kind: "qualified_lead" },
            created_at: new Date().toISOString() }], next_cursor: null, tenant_id: A.tenantId, user_id: A.userId };
    };
    renderWithQueryClient(createElement(QualifiedLeadAlerts));
    await waitFor(() => assert.ok(alertRequests > 0));
    assert.equal(eventRequests, 0);
    assert.deepEqual(notifications(), []);
});

test("unrelated security alerts are visible in the timeline data but do not become campaign notices", async () => {
    window.localStorage.setItem(key(), "[]");
    rows = [notice("admin-security", "suspicious_login")];
    renderWithQueryClient(createElement(QualifiedLeadAlerts));
    await waitFor(() => assert.ok(alertRequests > 0));
    assert.deepEqual(notifications(), []);
});

test("a second tab's saved server ID suppresses a sequential duplicate", async () => {
    const world = renderWithQueryClient(createElement(QualifiedLeadAlerts));
    await seeded();
    window.localStorage.setItem(key(), JSON.stringify(["seen-other-tab"]));
    rows = [notice("seen-other-tab", "campaign_failures")];
    await act(async () => { await world.qc.invalidateQueries({ queryKey: ["alerts"] }); });
    assert.deepEqual(notifications(), []);
    assert.ok(alertRequests > 0);
});

test("no verified identity means no alert query, hydration or legacy fallback", async () => {
    notificationsStore.setIdentity(null);
    await act(async () => {
        renderWithQueryClient(createElement("div", null, createElement(QualifiedLeadAlerts), createElement(AlertsRead)));
    });
    assert.equal(alertRequests, 0);
    assert.equal(eventRequests, 0);
    assert.deepEqual(notifications(), []);
});

for (const identity of [B, { tenantId: A.tenantId, userId: "other-user" }, { tenantId: B.tenantId, userId: A.userId }]) {
    test(`the alert query removes A's cached rows immediately when identity changes to ${identity.userId}`, async () => {
        rows = [notice("A-private", "low_minutes")];
        const world = renderWithQueryClient(createElement(AlertsRead));
        await waitFor(() => assert.ok(world.getByTestId("server-alerts").textContent?.includes("A-private")));
        let finish!: (value: Awaited<ReturnType<typeof backendApi.alerts.list>>) => void;
        let started = false;
        backendApi.alerts.list = async () => { started = true; return new Promise(resolve => { finish = resolve; }); };
        await act(async () => { notificationsStore.setIdentity(identity); });
        assert.equal(world.getByTestId("server-alerts").textContent?.includes("A-private"), false);
        await waitFor(() => assert.equal(started, true));
        await act(async () => { finish(result([notice("B-safe")], identity)); });
        await waitFor(() => assert.ok(world.getByTestId("server-alerts").textContent?.includes("B-safe")));
        const current = notificationsStore.getSnapshot();
        const own = world.qc.getQueriesData({ queryKey: ["alerts", current.scopeKey, current.generation] });
        assert.ok(own.length > 0);
        assert.ok(own.every(([, data]) => !JSON.stringify(data).includes("A-private")));
    });
}

test("a delayed A response cannot enter B's alert cache or create B's notice", async () => {
    window.localStorage.setItem(key(), "[]");
    let finish!: (value: Awaited<ReturnType<typeof backendApi.alerts.list>>) => void;
    let started = false;
    backendApi.alerts.list = async () => { started = true; return new Promise(resolve => { finish = resolve; }); };
    const world = renderWithQueryClient(createElement(QualifiedLeadAlerts));
    await waitFor(() => assert.equal(started, true));
    backendApi.alerts.list = async () => result([]);
    await act(async () => { notificationsStore.setIdentity(B); });
    await seeded();
    await act(async () => { finish(result([notice("A-private-late")], A)); });
    assert.deepEqual(notifications(), []);
    const current = notificationsStore.getSnapshot();
    const own = world.qc.getQueriesData({ queryKey: ["alerts", current.scopeKey, current.generation] });
    assert.ok(own.length > 0);
    assert.ok(own.every(([, data]) => !JSON.stringify(data).includes("A-private-late")));
});

for (const [name, owner] of [
    ["missing owner", null],
    ["same user in another tenant", { tenantId: B.tenantId, userId: A.userId }],
    ["another user in the same tenant", { tenantId: A.tenantId, userId: B.userId }],
] as const) {
    test(`an alert response with ${name} cannot enter the verified actor's cache or notices`, async () => {
        window.localStorage.setItem(key(), "[]");
        backendApi.alerts.list = async () => owner
            ? result([notice("wrong-owner-private")], owner)
            : ({ items: [notice("wrong-owner-private")], next_cursor: null } as Awaited<ReturnType<typeof backendApi.alerts.list>>);
        const world = renderWithQueryClient(createElement("div", null, createElement(QualifiedLeadAlerts), createElement(AlertsRead)));
        await waitFor(() => assert.equal(world.qc.getQueryCache().getAll().find(query => query.queryKey[0] === "alerts")?.state.status, "error"));
        const query = world.qc.getQueryCache().getAll().find(query => query.queryKey[0] === "alerts")!;
        assert.equal(query.state.data, undefined);
        assert.deepEqual(notifications(), []);
        assert.equal(window.localStorage.getItem(key()), "[]");
        assert.equal(eventRequests, 0);
    });
}
