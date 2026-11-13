/** FX1b Task 3 — Call History filters cover every call and survive a refresh. */
import Module from "node:module";
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import React from "react";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

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

const TOTAL = 120;
const FAR_DID = "+442079460999";
const FAR_PHONE = "+447400999111";
const SEARCH_LABEL = "Search calls by phone number or campaign";

/** 120 calls, newest first. Rows 0-49 are what one page used to hold. Calls
 *  100-119 are failed; call 110 alone is inbound on FAR_DID / campaign "Far line". */
function makeCalls(): Call[] {
    return Array.from({ length: TOTAL }, (_, index) => {
        const far = index === 110;
        return {
            id: `call-${index}`,
            campaign_id: "camp-1",
            campaign_name: far ? "Far line" : "Main campaign",
            lead_id: "",
            phone_number: far ? FAR_PHONE : `+4474000${String(index).padStart(5, "0")}`,
            status: index >= 100 ? "failed" : "completed",
            duration_seconds: 30,
            created_at: "2026-01-01T00:00:00Z",
            direction: far ? "inbound" : "outbound",
            // The wire sends to_number on outbound calls too (the destination).
            // Call 5 is an OUTBOUND call that dialled the far number.
            to_number: far || index === 5 ? FAR_DID : `+4474000${String(index).padStart(5, "0")}`,
            inbound_campaign_id: far ? "inb-far" : null,
        } as Call;
    });
}

const originalListCalls = dashboardApi.listCallsCursor;
const originalRequestAnimationFrame = window.requestAnimationFrame;
const originalCancelAnimationFrame = window.cancelAnimationFrame;
const originalPause = window.HTMLMediaElement.prototype.pause;
const originalScrollIntoView = window.Element.prototype.scrollIntoView;
let requests: Array<{ page: number; pageSize: number }> = [];
let all: Call[] = [];

beforeEach(() => {
    requests = [];
    all = makeCalls();
    // The server's cursor mode: the cursor names the page that follows, and the last page has none.
    dashboardApi.listCallsCursor = async (cursor: string | null, pageSize = 20) => {
        const page = cursor === null ? 1 : Number(cursor);
        requests.push({ page, pageSize });
        return { calls: all.slice((page - 1) * pageSize, page * pageSize), nextCursor: page * pageSize < all.length ? String(page + 1) : null };
    };
    window.history.replaceState(null, "", "/calls");
    // JSDOM gaps the rows touch on mount and unmount.
    window.requestAnimationFrame = (callback) => window.setTimeout(() => callback(performance.now()), 0);
    window.cancelAnimationFrame = (handle) => window.clearTimeout(handle);
    window.HTMLMediaElement.prototype.pause = () => {};
    window.Element.prototype.scrollIntoView = () => {};
});

afterEach(() => {
    cleanup();
    dashboardApi.listCallsCursor = originalListCalls;
    window.requestAnimationFrame = originalRequestAnimationFrame;
    window.cancelAnimationFrame = originalCancelAnimationFrame;
    window.HTMLMediaElement.prototype.pause = originalPause;
    window.Element.prototype.scrollIntoView = originalScrollIntoView;
    window.history.replaceState(null, "", "/");
});

async function renderLoaded() {
    renderWithQueryClient(<Page />);
    await waitFor(() => {
        assert.equal(screen.queryByText("Showing 1 to 50 of 120 calls") !== null, true);
    }, { timeout: 5_000 });
}

function rowIds() {
    return Array.from(document.querySelectorAll("[data-call-row]")).map((row) => row.getAttribute("data-call-row"));
}

test("every page of calls is read through the all-pages helper", async () => {
    await renderLoaded();
    assert.deepEqual(requests, [{ page: 1, pageSize: 50 }, { page: 2, pageSize: 50 }, { page: 3, pageSize: 50 }]);
    assert.equal(rowIds().length, 50);
});

test("search finds a call beyond the first 50 rows", async () => {
    const user = userEvent.setup({ document: globalThis.document });
    await renderLoaded();
    await user.click(screen.getByLabelText(SEARCH_LABEL));
    await user.paste(FAR_PHONE);
    await waitFor(() => assert.deepEqual(rowIds(), ["call-110"]));
    // One matching row: the footer no longer claims 50 of 120.
    assert.equal(screen.queryByText(/of 120 calls/) !== null, false);
});

test("Issues covers all calls and the footer counts the rows actually shown", async () => {
    const user = userEvent.setup({ document: globalThis.document });
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Issues" }));
    await waitFor(() => assert.equal(rowIds().length, 20));
    assert.equal(rowIds().includes("call-119"), true);
    assert.equal(screen.queryByText(/Showing .* calls/) !== null, false);

    await user.click(screen.getByRole("button", { name: "All" }));
    await user.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => assert.equal(screen.queryByText("Showing 51 to 100 of 120 calls") !== null, true));
    assert.equal(rowIds()[0], "call-50");
});

test("the number filter lists and applies a number that is only on a later row", async () => {
    const user = userEvent.setup({ document: globalThis.document });
    await renderLoaded();
    await user.click(screen.getByRole("combobox", { name: "Filter calls by phone number" }));
    // FX1c Task 2: inbound numbers only. 119 outbound destinations are not options,
    // and the outbound call that dialled the same number is not a match.
    assert.deepEqual(within(screen.getByRole("listbox")).getAllByRole("option").map((option) => option.textContent), ["All phone numbers", FAR_DID]);
    await user.click(within(screen.getByRole("listbox")).getByRole("option", { name: FAR_DID }));
    await waitFor(() => assert.deepEqual(rowIds(), ["call-110"]));
});

test("the inbound-campaign filter lists and applies a campaign that is only on a later row", async () => {
    const user = userEvent.setup({ document: globalThis.document });
    await renderLoaded();
    await user.click(screen.getByRole("combobox", { name: "Filter calls by inbound campaign" }));
    await user.click(within(screen.getByRole("listbox")).getByRole("option", { name: "Far line" }));
    await waitFor(() => assert.deepEqual(rowIds(), ["call-110"]));
});

test("the page and every filter are written to the address", async () => {
    const user = userEvent.setup({ document: globalThis.document });
    await renderLoaded();
    await user.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => assert.equal(new URLSearchParams(window.location.search).get("page"), "2"));

    await user.click(screen.getByRole("button", { name: "Issues" }));
    await user.click(screen.getByRole("button", { name: "outbound" }));
    await user.click(screen.getByLabelText(SEARCH_LABEL));
    await user.paste("+44740");
    await waitFor(() => {
        const params = new URLSearchParams(window.location.search);
        assert.equal(params.get("filter"), "issues");
        assert.equal(params.get("direction"), "outbound");
        assert.equal(params.get("search"), "+44740");
        assert.equal(params.get("page"), null);
    });

    await user.click(screen.getByRole("button", { name: "all" }));
    await user.click(screen.getByRole("button", { name: "All" }));
    await user.clear(screen.getByLabelText(SEARCH_LABEL));
    await user.click(screen.getByRole("combobox", { name: "Filter calls by phone number" }));
    await user.click(within(screen.getByRole("listbox")).getByRole("option", { name: FAR_DID }));
    await user.click(screen.getByRole("combobox", { name: "Filter calls by inbound campaign" }));
    await user.click(within(screen.getByRole("listbox")).getByRole("option", { name: "Far line" }));
    await waitFor(() => {
        const params = new URLSearchParams(window.location.search);
        assert.equal(params.get("did"), FAR_DID);
        assert.equal(params.get("inbound_campaign_id"), "inb-far");
        assert.equal(params.get("filter"), null);
        assert.equal(params.get("search"), null);
    });
});

test("a reload or a shared link shows the same page and filters", async () => {
    window.history.replaceState(null, "", "/calls?page=2");
    renderWithQueryClient(<Page />);
    await waitFor(() => {
        assert.equal(screen.queryByText("Showing 51 to 100 of 120 calls") !== null, true);
    }, { timeout: 5_000 });
    assert.equal(rowIds()[0], "call-50");
    cleanup();

    const shared = new URLSearchParams({ filter: "issues", direction: "inbound", search: FAR_PHONE, did: FAR_DID, inbound_campaign_id: "inb-far" });
    window.history.replaceState(null, "", `/calls?${shared.toString()}`);
    renderWithQueryClient(<Page />);
    await waitFor(() => assert.deepEqual(rowIds(), ["call-110"]), { timeout: 5_000 });
    assert.equal((screen.getByLabelText(SEARCH_LABEL) as HTMLInputElement).value, FAR_PHONE);
    assert.equal(screen.getByRole("button", { name: "Issues" }).getAttribute("aria-pressed"), "true");
    assert.equal(screen.getByRole("button", { name: "inbound" }).getAttribute("aria-pressed"), "true");
    assert.equal(screen.getByRole("combobox", { name: "Filter calls by phone number" }).textContent?.includes(FAR_DID), true);
    assert.equal(screen.getByRole("combobox", { name: "Filter calls by inbound campaign" }).textContent?.includes("Far line"), true);
    // The address still carries the view after the page has settled.
    assert.equal(new URLSearchParams(window.location.search).get("did"), FAR_DID);
});

// ── FX1c Task 1 ─────────────────────────────────────────────────────────────

test("while a call is live the refresh reads only the newest page and merges it", async () => {
    all[0] = { ...all[0], status: "in_progress" };
    await renderLoaded();
    const afterLoad = requests.length;
    assert.deepEqual(requests.slice(0, 3), [{ page: 1, pageSize: 50 }, { page: 2, pageSize: 50 }, { page: 3, pageSize: 50 }]);

    // A new call arrives and a loaded one changes on the server.
    all = [{ ...all[0], id: "call-new", phone_number: "+447400555000", status: "completed" }, ...all];
    all[2] = { ...all[2], phone_number: "+447400555111" };
    await waitFor(() => assert.equal(rowIds()[0], "call-new"), { timeout: 8_000 });
    assert.equal(screen.queryByText("Showing 1 to 50 of 121 calls") !== null, true);
    assert.equal(rowIds()[2], "call-1");
    assert.equal(screen.queryAllByText("+447400555111").length > 0, true);

    const live = requests.slice(afterLoad);
    assert.equal(live.length >= 1, true);
    assert.equal(live.every((request) => request.page === 1 && request.pageSize === 50), true, JSON.stringify(live));
});

test("with no live call nothing is read again", async () => {
    await renderLoaded();
    const afterLoad = requests.length;
    await new Promise((resolve) => setTimeout(resolve, 3_500));
    assert.equal(requests.length, afterLoad);
});

test("a filter change reads every page again", async () => {
    const user = userEvent.setup({ document: globalThis.document });
    await renderLoaded();
    const afterLoad = requests.length;
    await user.click(screen.getByRole("button", { name: "Issues" }));
    await waitFor(() => assert.deepEqual(requests.slice(afterLoad), [{ page: 1, pageSize: 50 }, { page: 2, pageSize: 50 }, { page: 3, pageSize: 50 }]));
});
