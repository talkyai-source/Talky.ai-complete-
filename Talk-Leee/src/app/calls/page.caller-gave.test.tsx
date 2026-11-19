/**
 * Call History: every call shows what the caller GAVE (email and number) in a
 * dedicated "Caller gave" strip, "Not provided" where none was given.
 * Same boundary stubs as page.fx3b-text.test.tsx.
 */
import Module from "node:module";
import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { cleanup, waitFor } from "@testing-library/react";

import { dashboardApi, type Call } from "@/lib/dashboard-api";
import { renderWithQueryClient } from "@/test-utils/render";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/calls/call-issues-banner")) return { CallIssuesBanner: nothing };
    if (id.includes("components/calls/dial-again-button")) return { DialAgainButton: nothing };
    if (id.includes("lib/queries/inbound-queries")) return { useEffectivePermissions: () => ({ isSuccess: true, isError: false, data: { permissions: [] } }) };
    if (id.endsWith("lib/auth-context")) return { useAuth: () => ({ user: { id: "user-a", tenant_id: "tenant-a" } }) };
    return originalLoad.call(this, id, ...args);
};
let Page: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Page = require("./page").default;
} finally {
    loader._load = originalLoad;
}

const SLOW = 15_000;
const base = {
    campaign_id: "camp-1", campaign_name: "Main campaign", lead_id: "", status: "completed", duration_seconds: 30,
    created_at: "2026-01-01T00:00:00Z", direction: "outbound",
};
const given = { ...base, id: "call-1", phone_number: "+447400000001", to_number: "+447400000001", captured_email: "gave@example.com", captured_phone: "+923085397539" } as Call;
const none = { ...base, id: "call-2", phone_number: "+447400000002", to_number: "+447400000002", captured_email: null, captured_phone: null } as Call;
type Listing = Awaited<ReturnType<typeof dashboardApi.listCallsCursor>>;

const originalRequestAnimationFrame = window.requestAnimationFrame;
const originalCancelAnimationFrame = window.cancelAnimationFrame;
const originalPause = window.HTMLMediaElement.prototype.pause;
const originalScrollIntoView = window.Element.prototype.scrollIntoView;
beforeEach(() => {
    window.history.replaceState(null, "", "/calls");
    window.requestAnimationFrame = (callback) => window.setTimeout(() => callback(performance.now()), 0);
    window.cancelAnimationFrame = (handle) => window.clearTimeout(handle);
    window.HTMLMediaElement.prototype.pause = () => {};
    window.Element.prototype.scrollIntoView = () => {};
});
afterEach(() => {
    cleanup();
    mock.restoreAll();
    window.requestAnimationFrame = originalRequestAnimationFrame;
    window.cancelAnimationFrame = originalCancelAnimationFrame;
    window.HTMLMediaElement.prototype.pause = originalPause;
    window.Element.prototype.scrollIntoView = originalScrollIntoView;
    window.history.replaceState(null, "", "/");
});

const rows = () => Array.from(document.querySelectorAll<HTMLElement>('[data-call-grid="row"]'));

async function mount() {
    mock.method(dashboardApi, "listCallsCursor", async () => ({ calls: [given, none], nextCursor: null } as Listing));
    renderWithQueryClient(<Page />);
    await waitFor(() => assert.equal(rows().length === 2, true), { timeout: SLOW });
}

test("a call with an email and a number shows both in its row's Caller gave strip", async () => {
    await mount();
    const strip = rows()[0].querySelector<HTMLElement>('[data-caller-gave="row"]');
    assert.equal(strip !== null, true);
    assert.equal(strip!.parentElement === rows()[0], true);
    assert.equal((strip!.textContent ?? "").includes("gave@example.com"), true, strip!.textContent ?? "");
    assert.equal((strip!.textContent ?? "").includes("+92 308 5397539"), true, strip!.textContent ?? "");
    assert.equal((strip!.textContent ?? "").includes("Not provided"), false);
});

test("a call where nothing was given says Not provided twice in its row", async () => {
    await mount();
    const strip = rows()[1].querySelector<HTMLElement>('[data-caller-gave="row"]');
    assert.equal(strip !== null, true);
    assert.equal(((strip!.textContent ?? "").match(/Not provided/g) ?? []).length, 2, strip!.textContent ?? "");
});

test("the stacked card carries the same strip and the header is unchanged", async () => {
    await mount();
    assert.equal(document.querySelectorAll('[data-caller-gave="card"]').length, 2);
    const header = document.querySelector('[data-call-grid="header"]');
    assert.equal(header !== null, true);
    assert.equal(header!.children.length, 8);
});
