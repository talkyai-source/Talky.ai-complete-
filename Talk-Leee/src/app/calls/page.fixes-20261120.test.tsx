import React from "react";
import Module from "node:module";
import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { sharedHttpClient } from "@/lib/api";
import type { HttpRequestOptions } from "@/lib/http-client";
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

let puts: HttpRequestOptions[] = [];
beforeEach(() => {
    puts = [];
    localStorage.clear();
    window.history.replaceState({}, "", "/calls");
    mock.method(globalThis, "fetch", async () => { throw new Error("Unexpected external request"); });
    mock.method(sharedHttpClient(), "request", async (options: HttpRequestOptions) => {
        if (options.method === "PUT") puts.push(options);
        return { workflow: null };
    });
});
afterEach(() => { cleanup(); mock.restoreAll(); localStorage.clear(); });

const notes = () => screen.getAllByRole("textbox", { name: `Notes for ${call.phone_number}` }) as HTMLInputElement[];
const isDisabled = (el: Element) => el.matches(":disabled");

test("a read-only member's lead type, notes, form and review controls are disabled and nothing is saved", async () => {
    permissionResult = { isSuccess: true, data: { permissions: ["calls:read"] } };
    render(<CallsPage />);
    await waitFor(() => assert.equal(notes().length > 0, true));
    assert.equal(notes().every(isDisabled), true);
    assert.equal(screen.getAllByRole("combobox", { name: /lead type for/i }).every(isDisabled), true);
    assert.equal(screen.getAllByRole("button", { name: /open form for/i }).every(isDisabled), true);
    assert.equal(screen.getAllByRole("button", { name: /review call/i }).every(isDisabled), true);
    fireEvent.change(notes()[0], { target: { value: "sneaky" } });
    await new Promise((resolve) => setTimeout(resolve, 50));
    assert.equal(puts.length, 0);
});

test("with calls:create the controls work as before", async () => {
    permissionResult = { isSuccess: true, data: { permissions: ["calls:read", "calls:create"] } };
    render(<CallsPage />);
    await waitFor(() => assert.equal(notes().length > 0, true));
    assert.equal(notes().some(isDisabled), false);
    assert.equal(screen.getAllByRole("combobox", { name: /lead type for/i }).some(isDisabled), false);
    fireEvent.change(notes()[0], { target: { value: "ok" } });
    await waitFor(() => assert.equal(puts.length, 1));
});
