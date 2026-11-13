/**
 * FX2a Task 5.2 — Audit Logs: the shared loading state; a failed read shows
 * the shared, announced error state with "Retry"; the "…needs the audit:read
 * permission" sentence appears only on a 403; a malformed answer never prints
 * its validation dump.
 */
import React from "react";
import Module from "node:module";
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "@/lib/auth-context";
import { api, MeResponseSchema } from "@/lib/api";
import { notificationsStore } from "@/lib/notifications";
import { clearFreshLoginGrace, invalidateRequestIdentity } from "@/lib/http-client";
import { ACTOR_ID, TENANT_ID, auditRow } from "@/test-utils/admin-events-fixtures";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return {
        DashboardLayout: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
    };
    return originalLoad.call(this, id, ...args);
};
let AuditLogsPage: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- scoped shell stub above
    AuditLogsPage = require("./page").default;
} finally { loader._load = originalLoad; }

const SLOW = 15_000;
const PERMISSION = /reading the audit trail needs the audit:read permission/;
const TITLE = "The audit log could not be loaded, so none of it is shown.";
const originalFetch = globalThis.fetch, originalGetMe = api.getMe;
const profile = { id: ACTOR_ID, email: "a@example.test", role: "platform_admin", tenant_id: TENANT_ID, minutes_remaining: 0, minutes_state: "known" };
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });
let qc: QueryClient, answer: () => Response | Promise<Response>, reads: number;
beforeEach(() => {
    window.localStorage.clear(); window.sessionStorage.clear(); invalidateRequestIdentity(); clearFreshLoginGrace();
    reads = 0; answer = () => json([auditRow()]);
    api.getMe = async () => MeResponseSchema.parse(profile);
    globalThis.fetch = (async () => { reads += 1; return answer(); }) as typeof fetch;
    qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000, gcTime: Infinity } } });
});
afterEach(() => {
    cleanup(); qc.clear(); globalThis.fetch = originalFetch; api.getMe = originalGetMe;
    notificationsStore.setIdentity(null); window.localStorage.clear(); clearFreshLoginGrace(); invalidateRequestIdentity();
});
const draw = () => render(<QueryClientProvider client={qc}><AuthProvider><AuditLogsPage /></AuthProvider></QueryClientProvider>);
const errorText = () => document.querySelector('[role="alert"]')?.textContent ?? "";

test("loading is announced through the shared loading state", async () => {
    let release!: (value: Response) => void;
    answer = () => new Promise<Response>((resolve) => { release = resolve; });
    draw();
    await waitFor(() => assert.equal(screen.queryByRole("status") !== null, true), { timeout: SLOW });
    assert.equal(within(screen.getByRole("status")).queryByText("Loading audit events…") !== null, true);
    await waitFor(() => assert.equal(typeof release, "function"), { timeout: SLOW });
    await act(async () => { release(json([auditRow()])); });
    await screen.findByText(auditRow().action, undefined, { timeout: SLOW });
    assert.equal(screen.queryByRole("status") === null, true);
});

test("an empty audit log shows the shared empty state with the existing sentence", async () => {
    answer = () => json([]);
    draw();
    await screen.findByRole("heading", { name: "No audit events were returned for the current scope." }, { timeout: SLOW });
    assert.equal(document.querySelector('[role="alert"]') === null, true, "an empty list is not an error");
});

test("a category with no events in the loaded page shows the shared empty state with the existing sentence", async () => {
    draw();
    await screen.findByText(auditRow().action, undefined, { timeout: SLOW });
    fireEvent.click(screen.getByRole("button", { name: "authentication" }));
    await screen.findByRole("heading", { name: "No audit events in the loaded page match the selected category." }, { timeout: SLOW });
});

test("a 403 shows the announced error state with the permission sentence and Retry", async () => {
    answer = () => json({ detail: "Forbidden" }, 403);
    draw();
    await screen.findByText(TITLE, undefined, { timeout: SLOW });
    assert.match(errorText(), /Forbidden/);
    assert.match(errorText(), PERMISSION);
    assert.equal(screen.queryByRole("button", { name: "Retry" }) !== null, true);
    assert.equal(screen.queryByRole("button", { name: "Try again" }) === null, true);
});

test("a 500 shows the server's message without the permission sentence, and Retry reads the log again", async () => {
    answer = () => json({ detail: "Audit store unavailable" }, 500);
    draw();
    await screen.findByText(TITLE, undefined, { timeout: SLOW });
    assert.match(errorText(), /Audit store unavailable/);
    assert.doesNotMatch(document.body.textContent ?? "", PERMISSION);
    assert.equal(screen.queryByText("No audit events were returned for the current scope.") === null, true);

    answer = () => json([auditRow()]);
    const before = reads;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText(auditRow().action, undefined, { timeout: SLOW });
    assert.equal(reads > before, true, "Retry sent the request again");
    assert.equal(screen.queryByText(TITLE) === null, true);
});

test("a malformed answer shows the error state's default message, never the validation dump or the permission sentence", async () => {
    answer = () => json([auditRow(1, { event_id: "bad-id" })]);
    draw();
    await screen.findByText(TITLE, undefined, { timeout: SLOW });
    assert.equal(errorText(), "Please retry.");
    assert.doesNotMatch(document.body.textContent ?? "", /invalid_|"path"|"code"|Invalid input/i);
    assert.doesNotMatch(document.body.textContent ?? "", PERMISSION);
});
