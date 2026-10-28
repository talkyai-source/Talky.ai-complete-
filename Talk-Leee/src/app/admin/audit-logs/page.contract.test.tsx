import React from "react";
import Module from "node:module";
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider, useAuth } from "@/lib/auth-context";
import { api, MeResponseSchema } from "@/lib/api";
import { notificationsStore } from "@/lib/notifications";
import { clearFreshLoginGrace, invalidateRequestIdentity } from "@/lib/http-client";
import { ACTOR_ID, SUBJECT_ID, TENANT_ID, auditRow } from "@/test-utils/admin-events-fixtures";

// Only the unrelated dashboard shell/navigation is replaced. This is the real
// page, AuthProvider, query hook, client and canonical decoder over fake HTTP.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return {
        DashboardLayout: ({ children, description }: { children: React.ReactNode; description: string }) => <main><p>{description}</p>{children}</main>,
    };
    return originalLoad.call(this, id, ...args);
};
let AuditLogsPage: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- scoped shell stub above
    AuditLogsPage = require("./page").default;
} finally { loader._load = originalLoad; }

const originalFetch = globalThis.fetch, originalGetMe = api.getMe;
const profile = { id: ACTOR_ID, email: "a@example.test", role: "platform_admin", tenant_id: TENANT_ID,
    minutes_remaining: 0, minutes_state: "known" };
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });
let qc: QueryClient, rows: unknown, calls: URL[], override: (() => Response | Promise<Response>) | undefined;
beforeEach(() => {
    window.localStorage.clear(); window.sessionStorage.clear(); invalidateRequestIdentity(); clearFreshLoginGrace();
    rows = [auditRow()]; calls = []; override = undefined;
    api.getMe = async () => MeResponseSchema.parse(profile);
    globalThis.fetch = (async input => { const url = new URL(String(input), "http://localhost"); calls.push(url);
        assert.equal(url.pathname, "/api/v1/admin/audit/logs"); return override?.() ?? json(rows);
    }) as typeof fetch;
    qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000, gcTime: Infinity } } });
});
afterEach(() => {
    cleanup(); qc.clear(); globalThis.fetch = originalFetch; api.getMe = originalGetMe;
    notificationsStore.setIdentity(null); window.localStorage.clear(); clearFreshLoginGrace(); invalidateRequestIdentity();
});
function Controls() {
    const auth = useAuth();
    return <><button onClick={() => auth.applyLoginResult({ user_id: SUBJECT_ID, email: "b@example.test", role: "partner_admin", tenant_id: TENANT_ID })}>Switch page identity</button>
        <button onClick={() => void auth.refreshUser()}>Check page identity</button></>;
}
const draw = () => render(<QueryClientProvider client={qc}><AuthProvider><Controls /><AuditLogsPage /></AuthProvider></QueryClientProvider>);
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { promise, resolve }; }

test("standalone audit keeps category filtering page-local and caps raw lookahead at201", async () => {
    rows = [auditRow(), auditRow(2, { event_category: "AUTHENTICATION", event_type: "login_success" })];
    draw(); await screen.findByText(auditRow().action); assert.equal(calls[0]!.searchParams.get("limit"), "201");
    const before = calls.length; fireEvent.click(screen.getByRole("button", { name: "authentication" }));
    assert.equal(Boolean(screen.queryByText(auditRow().action)), false); assert.equal(Boolean(screen.getByText(auditRow(2).action)), true);
    assert.equal(calls.length, before); assert.match(document.body.textContent ?? "", /1 event shown of 2 most recent/);
    assert.doesNotMatch(document.body.textContent ?? "", /for your tenant/);
    assert.match(document.body.textContent ?? "", /Up to 200 recent events; categories filter this loaded page/);
});

test("standalone audit rejects malformed200 and retains a working explicit retry", async () => {
    rows = [auditRow(1, { event_id: "bad-id" })]; draw();
    await screen.findByText("The audit log could not be loaded, so none of it is shown.");
    assert.equal(screen.queryByText("No audit events were returned for the current scope."), null);
    rows = [auditRow()]; fireEvent.click(screen.getByRole("button", { name: "Try again" })); await screen.findByText(auditRow().action);
});

test("standalone audit same-client replacement cannot reuse A rows before B403", async () => {
    draw(); await screen.findByText(auditRow().action); const pending = deferred<Response>(); override = () => pending.promise;
    try {
        fireEvent.click(screen.getByRole("button", { name: "Switch page identity" }));
        assert.equal(Boolean(screen.queryByText(auditRow().action)), false);
        await act(async () => { pending.resolve(json({ detail: "Forbidden" }, 403)); });
        await screen.findByText("The audit log could not be loaded, so none of it is shown.");
    } finally { await act(async () => { pending.resolve(json({ detail: "Forbidden" }, 403)); }); }
});

test("checking identity masks standalone cached audit while current profile is pending", async () => {
    draw(); await screen.findByText(auditRow().action);
    const pending = deferred<ReturnType<typeof MeResponseSchema.parse>>(); api.getMe = () => pending.promise;
    try {
        fireEvent.click(screen.getByRole("button", { name: "Check page identity" }));
        assert.equal(Boolean(screen.queryByText(auditRow().action)), false);
        assert.equal(Boolean(screen.queryByText("Loading audit events…")), true);
        assert.doesNotMatch(document.body.textContent ?? "", /0 events shown of 0 most recent/);
        assert.equal(Boolean(screen.queryByText("No audit events were returned for the current scope.")), false);
    }
    finally { await act(async () => { pending.resolve(MeResponseSchema.parse(profile)); }); }
    await waitFor(() => assert.equal(Boolean(screen.queryByText(auditRow().action)), true));
});
