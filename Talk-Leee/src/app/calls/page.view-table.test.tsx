import React from "react";
import Module from "node:module";
import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { sharedHttpClient } from "@/lib/api";
import type { Call } from "@/lib/dashboard-api";

const call: Call = {
    id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", campaign_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd", campaign_name: "Read-only check",
    lead_id: "", phone_number: "+15550001111", status: "completed", created_at: "2026-11-06T10:00:00.000Z",
};
const callsData = { calls: [call], total: 1 };
const refetch = async () => undefined;
const idle = { data: undefined, isLoading: false, isError: false };
const user = { id: "user-a", tenant_id: "tenant-a" };
let permissionResult = { isSuccess: true, data: { permissions: ["calls:read"] } };
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <main>{children}</main> };
    if (id.endsWith("components/calls/dial-again-button")) return { DialAgainButton: () => null };
    if (id.endsWith("components/calls/call-issues-banner")) return { CallIssuesBanner: () => null };
    if (id.endsWith("lib/auth-context")) return { useAuth: () => ({ user, isLoading: false }) };
    if (id.endsWith("lib/queries/inbound-queries")) return { useEffectivePermissions: () => permissionResult };
    if (id.endsWith("lib/api-hooks")) {
        return {
            useCalls: () => ({ data: callsData, isLoading: false, isError: false, refetch }),
            useCallSummary: () => idle,
            useCallTranscript: () => idle,
        };
    }
    return originalLoad.call(this, id, ...args);
};
let CallsPage: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- scoped unrelated dependency stubs above
    CallsPage = require("./page").default;
} finally {
    loader._load = originalLoad;
}
if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};


beforeEach(() => {
    localStorage.clear();
    mock.method(globalThis, "fetch", async () => { throw new Error("Unexpected external request"); });
    mock.method(sharedHttpClient(), "request", async () => ({ workflow: null }));
});
afterEach(() => { cleanup(); mock.restoreAll(); localStorage.clear(); });

const tableShown = () => document.querySelector("[data-call-history-table]") !== null;
const listShown = () => document.querySelector('[data-call-grid="row"]') !== null;

test("?view=table shows the table, List shows the list again, Table brings it back, and the address follows", async () => {
    permissionResult = { isSuccess: true, data: { permissions: ["calls:read", "calls:create"] } };
    window.history.replaceState({}, "", "/calls?view=table");
    render(<CallsPage />);
    await waitFor(() => assert.equal(tableShown(), true));
    assert.equal(listShown(), false);
    assert.equal(screen.getByRole("button", { name: "Table" }).getAttribute("aria-pressed"), "true");
    fireEvent.click(screen.getByRole("button", { name: "List" }));
    await waitFor(() => assert.equal(listShown(), true));
    assert.equal(tableShown(), false);
    await waitFor(() => assert.equal(window.location.search.includes("view=table"), false));
    fireEvent.click(screen.getByRole("button", { name: "Table" }));
    await waitFor(() => assert.equal(tableShown(), true));
    await waitFor(() => assert.equal(window.location.search.includes("view=table"), true));
});

test("the default view is the list and the Download button waits until every call is loaded", async () => {
    window.history.replaceState({}, "", "/calls");
    render(<CallsPage />);
    await waitFor(() => assert.equal(listShown(), true));
    assert.equal(tableShown(), false);
    assert.equal((screen.getByRole("button", { name: "Loading every call…" }) as HTMLButtonElement).disabled, true);
});
