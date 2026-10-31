import React from "react";
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AdminOperationsConsole } from "@/components/admin/admin-operations-console";
import { SuspensionStateProvider } from "@/components/admin/suspension-state-provider";
import { AuthProvider, useAuth } from "@/lib/auth-context";
import { api, MeResponseSchema } from "@/lib/api";
import { notificationsStore } from "@/lib/notifications";
import { clearFreshLoginGrace, invalidateRequestIdentity } from "@/lib/http-client";
import { ACTOR_ID, EVENT_TIME, SECURITY_STATUSES, SEVERITIES, SUBJECT_ID, TENANT_ID, auditRow, securityRow } from "@/test-utils/admin-events-fixtures";

const AUDIT = "/api/v1/admin/audit/logs", SECURITY = "/api/v1/admin/security-events/events";
const originalFetch = globalThis.fetch, originalGetMe = api.getMe;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
let calls: URL[], audit: unknown, security: unknown, readStatus: number, qc: QueryClient;
let override: ((url: URL) => Promise<Response> | Response | undefined) | undefined;
let replacementRole: "platform_admin" | "partner_admin";
const requestIdentity = { id: ACTOR_ID, email: "operator@example.test", role: "platform_admin", tenant_id: TENANT_ID,
    minutes_remaining: 0, minutes_state: "known" };
beforeEach(() => {
    window.localStorage.clear(); window.sessionStorage.clear(); invalidateRequestIdentity(); clearFreshLoginGrace();
    calls = []; audit = []; security = []; readStatus = 200; override = undefined; replacementRole = "partner_admin";
    api.getMe = async () => MeResponseSchema.parse(requestIdentity);
    globalThis.fetch = (async (input, init) => {
        const url = new URL(String(input), "http://localhost"); calls.push(url);
        if ((init?.method ?? "GET") !== "GET") throw new Error("Unexpected business mutation");
        const custom = override?.(url); if (custom) return custom;
        if (url.pathname === AUDIT) return json(readStatus === 200 ? audit : { detail: "Audit access unavailable" }, readStatus);
        if (url.pathname === SECURITY) return json(readStatus === 200 ? security : { detail: "Security access unavailable" }, readStatus);
        // Wrong-route errors are real HTTP errors within this synthetic transport;
        // never return the fictional envelope accepted by the old client.
        return json({ detail: "Not Found" }, 404);
    }) as typeof fetch;
    qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000, gcTime: Infinity },
        mutations: { retry: false, gcTime: Infinity } } });
});
afterEach(() => {
    cleanup(); qc.clear(); globalThis.fetch = originalFetch; api.getMe = originalGetMe;
    notificationsStore.setIdentity(null); window.localStorage.clear(); clearFreshLoginGrace(); invalidateRequestIdentity();
});
function IdentityControl() {
    const auth = useAuth();
    return <><button onClick={() => auth.applyLoginResult({ user_id: SUBJECT_ID, email: "replacement@example.test",
        role: replacementRole, tenant_id: TENANT_ID })}>Replace fixture identity</button>
        <button onClick={() => void auth.refreshUser()}>Recheck fixture identity</button></>;
}
function draw() {
    render(<QueryClientProvider client={qc}><AuthProvider><SuspensionStateProvider>
        <IdentityControl /><AdminOperationsConsole />
    </SuspensionStateProvider></AuthProvider></QueryClientProvider>);
}
const chooseSecurity = async () => fireEvent.click(await screen.findByRole("tab", { name: "Security Events" }));
const bodyText = () => document.body.textContent ?? "";
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { promise, resolve }; }

test("real console renders a canonical saved audit action and actor UUID without inventing System", async () => {
    audit = [auditRow()]; draw();
    await screen.findByText(auditRow().action);
    assert.match(bodyText(), new RegExp(ACTOR_ID));
    assert.equal(screen.queryByText("System", { exact: true }), null);
    assert.equal(calls.some(call => call.pathname === AUDIT), true);
});

test("security rows expose actual title, affected user, all five severities and statuses", async () => {
    security = SEVERITIES.map((severity, index) => securityRow(index + 1, { severity, status: SECURITY_STATUSES[index] }));
    draw(); await chooseSecurity(); await screen.findByText("Owned security alert 1");
    assert.match(bodyText(), /Affected user/); assert.match(bodyText(), new RegExp(SUBJECT_ID));
    for (const severity of SEVERITIES) assert.equal(Boolean(screen.queryByText(severity, { exact: true })), true);
    for (const status of SECURITY_STATUSES) assert.match(bodyText(), new RegExp(status.replaceAll("_", " ")));
    assert.equal(screen.queryByText("Actor", { exact: true }), null);
});

test("inactive security tab does not make a speculative security read", async () => {
    draw(); await waitFor(() => assert.equal(calls.length > 0, true));
    assert.equal(calls.some(call => /security-events/.test(call.pathname)), false);
});

for (const status of [403, 503]) test(`audit ${status} is visible and retryable without a false empty history`, async () => {
    readStatus = status; draw(); await screen.findByText("Audit logs unavailable");
    assert.equal(screen.queryByText("No audit activity found"), null);
    readStatus = 200; audit = [auditRow()]; fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText(auditRow().action); assert.equal(screen.queryByText("Audit logs unavailable"), null);
});

test("a malformed successful audit response is shown as unavailable, not empty or a fabricated epoch", async () => {
    audit = [auditRow(1, { event_time: undefined })];
    override = () => json(audit); // Isolate a real200 decoder failure from wrong-route404.
    draw();
    await screen.findByText("Audit logs unavailable");
    assert.equal(screen.queryByText("No audit activity found"), null);
    assert.doesNotMatch(bodyText(), /1970/);
});

test("empty later audit page still has Previous and exact lookahead does not show the extra row", async () => {
    audit = Array.from({ length: 21 }, (_, index) => auditRow(index + 1));
    override = url => url.pathname === AUDIT && url.searchParams.get("before_id") === auditRow(20).event_id ? json([]) : undefined;
    draw(); await screen.findByText(auditRow().action);
    assert.equal(screen.queryByText(auditRow(21).action), null);
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByText("No audit activity found");
    assert.equal((screen.getByRole("button", { name: "Previous" }) as HTMLButtonElement).disabled, false);
    assert.equal((screen.getByRole("button", { name: "Next" }) as HTMLButtonElement).disabled, true);
    fireEvent.click(screen.getByRole("button", { name: "Previous" })); await screen.findByText(auditRow().action);
});

// These controls seed a normalized, already-cached row to isolate ownership
// from the independently tested broken transport/decoder. It represents a row
// previously authorized for A, not a successful read by the replacement actor.
async function seedEventCache(kind: "auditLogs" | "securityEvents" = "auditLogs") {
    await waitFor(() => {
        const query = qc.getQueryCache().findAll({ queryKey: [kind] }).find(item => item.getObserversCount() > 0);
        assert.equal(Boolean(query && query.state.status === "success" && query.state.fetchStatus === "idle"), true);
    });
    const query = qc.getQueryCache().findAll({ queryKey: [kind] }).find(item => item.getObserversCount() > 0)!;
    const item = kind === "auditLogs" ? { id: auditRow().event_id, timestamp: EVENT_TIME,
        actionType: "A private cached audit action", eventType: "user_updated", eventCategory: "USER_MANAGEMENT", severity: "INFO",
        actor: { id: ACTOR_ID, type: "user" }, target: { type: "user", id: SUBJECT_ID }, metadata: {} }
        : { id: securityRow().event_id, timestamp: EVENT_TIME, title: "A private cached security signal",
            eventType: "A private cached security signal", severity: "HIGH", status: "open", subjectId: SUBJECT_ID, metadata: {} };
    await act(async () => { qc.setQueryData(query.queryKey, { items: [item], hasNext: false }); });
    await waitFor(() => assert.match(bodyText(), kind === "auditLogs" ? /A private cached audit action/ : /A private cached security signal/));
}

for (const kind of ["auditLogs", "securityEvents"] as const) for (const role of ["platform_admin", "partner_admin"] as const) test(`same QueryClient retires A ${kind} on replacement ${role} before its read`, async () => {
    draw(); if (kind === "securityEvents") await chooseSecurity(); await seedEventCache(kind); replacementRole = role;
    const pending = deferred<Response>(); override = () => pending.promise;
    try {
        fireEvent.click(screen.getByRole("button", { name: "Replace fixture identity" }));
        assert.doesNotMatch(bodyText(), /A private cached (audit action|security signal)/);
        await act(async () => { pending.resolve(json({ detail: "Replacement read denied" }, 403)); });
        assert.doesNotMatch(bodyText(), /A private cached (audit action|security signal)/);
        await screen.findByText(kind === "auditLogs" ? "Audit logs unavailable" : "Security events unavailable");
    } finally { await act(async () => { pending.resolve(json({ detail: "Replacement read denied" }, 403)); }); }
});

for (const kind of ["auditLogs", "securityEvents"] as const) test(`changing ${kind} filter never presents previous rows as matching pending query`, async () => {
    draw(); if (kind === "securityEvents") await chooseSecurity(); await seedEventCache(kind);
    const pending = deferred<Response>(); override = () => pending.promise;
    try {
        fireEvent.change(screen.getByPlaceholderText("Name or email"), { target: { value: "different@example.test" } });
        assert.doesNotMatch(bodyText(), /A private cached (audit action|security signal)/);
        await act(async () => { pending.resolve(json([])); });
        await screen.findByText(kind === "auditLogs" ? "No audit activity found" : "No security events found");
    } finally { await act(async () => { pending.resolve(json([])); }); }
});

test("exactly twenty audit rows do not imply a nonexistent next page", async () => {
    audit = Array.from({ length: 20 }, (_, index) => auditRow(index + 1)); draw();
    await screen.findByText(auditRow(20).action);
    assert.equal((screen.getByRole("button", { name: "Next" }) as HTMLButtonElement).disabled, true);
});

test("later-page read failure retains Previous and retries the exact failed page", async () => {
    audit = Array.from({ length: 21 }, (_, index) => auditRow(index + 1)); let fail = true;
    override = url => url.pathname === AUDIT && url.searchParams.get("before_id") === auditRow(20).event_id
        ? fail ? json({ detail: "Owned page unavailable" }, 503) : json([auditRow(22)]) : undefined;
    draw(); await screen.findByText(auditRow().action); fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByText("Audit logs unavailable");
    assert.equal((screen.getByRole("button", { name: "Previous" }) as HTMLButtonElement).disabled, false);
    fail = false; fireEvent.click(screen.getByRole("button", { name: "Retry" })); await screen.findByText(auditRow(22).action);
    assert.equal(calls.at(-1)!.searchParams.get("before_id"), auditRow(20).event_id);
});

test("audit date controls send UTC inclusive microseconds and role operations use event_group", async () => {
    draw(); await screen.findByRole("tab", { name: "Audit Logs" });
    fireEvent.change(screen.getByLabelText("Audit from date (UTC)"), { target: { value: "2026-10-21" } });
    fireEvent.change(screen.getByLabelText("Audit to date (UTC)"), { target: { value: "2026-10-21" } });
    fireEvent.click(screen.getByRole("combobox", { name: "Audit event type" }));
    fireEvent.click(screen.getByRole("option", { name: "Role operations" }));
    await waitFor(() => assert.equal(calls.some(url => url.pathname === AUDIT && url.searchParams.get("event_group") === "role_change"
        && url.searchParams.get("start_date") === "2026-10-21T00:00:00Z" && url.searchParams.get("end_date") === "2026-10-21T23:59:59.999999Z"), true));
});

test("security filter exposes INFO and escalated with canonical values", async () => {
    draw(); await chooseSecurity(); fireEvent.click(screen.getByRole("combobox", { name: "Security severity" }));
    fireEvent.click(screen.getByRole("option", { name: "INFO" }));
    fireEvent.click(screen.getByRole("combobox", { name: "Security status" }));
    fireEvent.click(screen.getByRole("option", { name: "Escalated" }));
    await waitFor(() => assert.equal(calls.some(url => url.pathname === SECURITY
        && url.searchParams.get("severity") === "INFO" && url.searchParams.get("status") === "escalated"), true));
});

test("seek Next uses last visible boundary, Previous restores it and filter resets to first page", async () => {
    audit = Array.from({ length: 21 }, (_, index) => auditRow(index + 1));
    override = url => url.pathname === AUDIT && url.searchParams.has("before_id") ? json([auditRow(22)]) : undefined;
    draw(); await screen.findByText(auditRow().action); fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByText(auditRow(22).action);
    const next = calls.find(url => url.searchParams.has("before_id"))!;
    assert.equal(next.searchParams.get("before_time"), EVENT_TIME);
    assert.equal(next.searchParams.get("before_id"), auditRow(20).event_id);
    assert.equal(next.searchParams.has("offset"), false);
    fireEvent.click(screen.getByRole("button", { name: "Previous" })); await screen.findByText(auditRow().action);
    fireEvent.click(screen.getByRole("button", { name: "Next" })); await screen.findByText(auditRow(22).action);
    fireEvent.change(screen.getByPlaceholderText("Name or email"), { target: { value: "new@example.test" } });
    await waitFor(() => assert.equal(calls.some(url => url.searchParams.get("user_query") === "new@example.test"), true));
    const changed = calls.find(url => url.searchParams.get("user_query") === "new@example.test")!;
    assert.equal(changed.searchParams.has("before_id"), false); assert.match(bodyText(), /Page 1/);
});

for (const kind of ["audit", "security"] as const) test(`candidate cursor ${kind} filter round-trip cannot revive a retired page`, async () => {
    audit = Array.from({ length: 21 }, (_, index) => auditRow(index + 1));
    security = Array.from({ length: 21 }, (_, index) => securityRow(index + 1));
    const path = kind === "audit" ? AUDIT : SECURITY, row = kind === "audit" ? auditRow : securityRow;
    const marker = kind === "audit" ? auditRow().action : securityRow().title;
    const later = kind === "audit" ? auditRow(22).action : securityRow(22).title;
    override = url => url.pathname === path && url.searchParams.has("before_id") ? json([row(22)]) : undefined;
    draw(); if (kind === "security") await chooseSecurity(); await screen.findByText(marker);
    fireEvent.click(screen.getByRole("button", { name: "Next" })); await screen.findByText(later);
    const filter = screen.getByPlaceholderText("Name or email");
    fireEvent.change(filter, { target: { value: "temporary@example.test" } }); await screen.findByText(marker);
    fireEvent.change(filter, { target: { value: "" } });
    assert.match(bodyText(), /Page 1/); assert.doesNotMatch(bodyText(), /Page 2/);
    await screen.findByText(marker);
});

for (const kind of ["audit", "security"] as const) test(`checking identity masks cached ${kind} rows while canonical profile is pending`, async () => {
    audit = [auditRow()]; security = [securityRow()]; draw(); if (kind === "security") await chooseSecurity();
    const marker = kind === "audit" ? auditRow().action : securityRow().title;
    await screen.findByText(marker);
    const pending = deferred<ReturnType<typeof MeResponseSchema.parse>>(); api.getMe = () => pending.promise;
    try {
        fireEvent.click(screen.getByRole("button", { name: "Recheck fixture identity" }));
        assert.equal(Boolean(screen.queryByText(marker)), false);
        assert.equal(Boolean(screen.queryByText(kind === "audit" ? "Loading audit logs" : "Loading security events")), true);
        assert.equal(Boolean(screen.queryByText(kind === "audit" ? "No audit activity found" : "No security events found")), false);
        const refresh = screen.getByRole("button", { name: kind === "audit" ? "Refresh audit history" : "Refresh security history" }) as HTMLButtonElement;
        assert.equal(refresh.disabled, true);
        const before = calls.length; fireEvent.click(refresh);
        assert.equal(calls.length, before);
    } finally { await act(async () => { pending.resolve(MeResponseSchema.parse(requestIdentity)); }); }
});

for (const kind of ["audit", "security"] as const) test(`refresh ${kind} from later page reads fresh first page without reusing the retired boundary`, async () => {
    audit = Array.from({ length: 21 }, (_, index) => auditRow(index + 1));
    security = Array.from({ length: 21 }, (_, index) => securityRow(index + 1));
    const path = kind === "audit" ? AUDIT : SECURITY, row = kind === "audit" ? auditRow : securityRow;
    const marker = kind === "audit" ? auditRow().action : securityRow().title;
    const later = kind === "audit" ? auditRow(22).action : securityRow(22).title;
    const fresh = kind === "audit" ? auditRow(30).action : securityRow(30).title;
    override = url => url.pathname === path && url.searchParams.has("before_id") ? json([row(22)]) : undefined;
    draw(); if (kind === "security") await chooseSecurity(); await screen.findByText(marker);
    fireEvent.click(screen.getByRole("button", { name: "Next" })); await screen.findByText(later);
    const beforeRefresh = calls.length;
    override = url => url.pathname === path ? json([row(30)]) : undefined;
    fireEvent.click(screen.getByRole("button", { name: kind === "audit" ? "Refresh audit history" : "Refresh security history" }));
    await screen.findByText(fresh); assert.match(bodyText(), /Page 1/);
    const reads = calls.slice(beforeRefresh).filter(url => url.pathname === path);
    assert.equal(reads.length, 1); assert.equal(reads[0]!.searchParams.has("before_id"), false);
    assert.equal(reads[0]!.searchParams.has("before_time"), false);
    assert.equal(Boolean(screen.queryByText(later)), false);
});
