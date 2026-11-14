/**
 * FX3b Task 2 — Call History texts: the search label and its placeholder say
 * the same thing, the number filter and a missing called number say "phone
 * number", and the row's conversation is named "Transcript" everywhere.
 * Same boundary stubs as page.states.test.tsx.
 */
import Module from "node:module";
import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";

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
const outbound = {
    id: "call-1", campaign_id: "camp-1", campaign_name: "Main campaign", lead_id: "", phone_number: "+447400000001",
    status: "completed", duration_seconds: 30, created_at: "2026-01-01T00:00:00Z", direction: "outbound", to_number: "+447400000001",
} as Call;
// An inbound call whose called number the server did not send.
const inbound = {
    id: "call-2", campaign_id: "camp-1", campaign_name: "Main campaign", lead_id: "", phone_number: "+447400000002", from_number: "+447400000002",
    status: "completed", duration_seconds: 30, created_at: "2026-01-01T00:00:00Z", direction: "inbound",
} as Call;
type Listing = Awaited<ReturnType<typeof dashboardApi.listCallsCursor>>;
type Transcript = Awaited<ReturnType<typeof dashboardApi.getCallTranscript>>;
const pageText = () => document.body.textContent ?? "";

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

async function mount() {
    mock.method(dashboardApi, "listCallsCursor", async () => ({ calls: [outbound, inbound], nextCursor: null } as Listing));
    mock.method(dashboardApi, "getCallTranscript", async () => ({
        turns: [{ role: "assistant", content: "Hello there" }, { role: "user", content: "Hi" }],
    } as unknown as Transcript));
    renderWithQueryClient(<Page />);
    await waitFor(() => assert.equal(document.querySelector("[data-call-row]") !== null, true), { timeout: SLOW });
}

test("FX3b 2.3: the search label and its placeholder name the same things", async () => {
    await mount();
    const search = screen.getByLabelText("Search calls by phone number or campaign");
    assert.equal(search.getAttribute("placeholder"), "Search by phone number or campaign…");
    assert.equal(screen.queryByLabelText("Search calls by phone number, DID, or campaign") !== null, false);
    assert.equal(screen.queryByPlaceholderText("Search by phone number…") !== null, false);
});

test("FX3b 2.4: the number filter and a missing called number say 'phone number', not DID", async () => {
    await mount();
    const filter = screen.getByRole("combobox", { name: "Filter calls by phone number" });
    assert.equal(filter.textContent?.includes("All phone numbers"), true, filter.textContent ?? "");
    assert.equal(screen.queryByRole("combobox", { name: "Filter calls by DID" }) !== null, false);
    assert.equal(screen.queryAllByText("to assigned phone number").length > 0, true);
    assert.equal(/\bDIDs?\b/.test(pageText()), false);
});

test("FX3b 2.5, 2.6: the row's conversation is named 'Transcript' and its speakers 'Agent' and 'Caller'", async () => {
    await mount();
    assert.equal(screen.queryAllByRole("button", { name: "Show transcript" }).length > 0, true);
    assert.equal(screen.queryAllByRole("button", { name: "Show AI script" }).length, 0);
    assert.equal(screen.queryAllByRole("button", { name: "Transcript" }).length > 0, true);
    const header = document.querySelector('[data-call-grid="header"]');
    assert.equal(header !== null, true);
    assert.equal(header!.textContent?.includes("Transcript / Form"), true, header!.textContent ?? "");
    fireEvent.click(screen.getAllByRole("button", { name: "Show transcript" })[0]);
    await waitFor(() => assert.equal(screen.queryAllByText("Hello there").length > 0, true), { timeout: SLOW });
    assert.equal(screen.queryAllByRole("button", { name: "Hide transcript" }).length > 0, true);
    assert.equal(screen.queryAllByText("Agent").length > 0 && screen.queryAllByText("Caller").length > 0, true);
    assert.equal(/AI script/i.test(pageText()), false);
});
