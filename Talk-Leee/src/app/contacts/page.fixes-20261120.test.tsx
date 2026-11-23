import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { dashboardApi } from "@/lib/dashboard-api";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    // Their side reads effective permissions (QA F1-21); unknown offers every action.
    if (id.includes("lib/queries/inbound-queries")) return { useEffectivePermissions: () => ({ isSuccess: false, data: undefined }) };
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/campaigns/contact-lists")) return { ContactLists: nothing };
    if (id.includes("components/calls/lead-details-panel")) return { ContactCapturedDetails: nothing };
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
afterEach(() => { cleanup(); mock.restoreAll(); });

const CAMPAIGNS = { campaigns: [{ id: "camp-1", name: "Spring push", direction: "outbound" }] };
const TOTAL = 120;
const rowsFor = (page: number, size: number) => {
    const start = (page - 1) * size;
    const count = Math.max(0, Math.min(size, TOTAL - start));
    return Array.from({ length: count }, (_, i) => ({ id: `c-${start + i}`, campaign_id: "camp-1", phone_number: `+4474001${String(start + i).padStart(5, "0")}`, status: "pending" }));
};
function mockPages() {
    mock.method(dashboardApi, "listCampaigns", async () => CAMPAIGNS);
    return mock.method(dashboardApi, "listContacts", async (_id: string, page: number, size: number) => ({ items: rowsFor(page, size), total: TOTAL, page, page_size: size }));
}
const next = () => screen.findByRole("button", { name: "Next" }, { timeout: SLOW });
const isDisabled = (el: HTMLElement) => (el as HTMLButtonElement).disabled;
const shown = (text: string) => screen.queryByText(text) !== null;
const pagesRequested = (read: ReturnType<typeof mockPages>) => read.mock.calls.map((c) => c.arguments[1]);

test("the Contacts page shows 'Showing 1 to 50 of 120 contacts' and pages with Next and Previous", async () => {
    const read = mockPages();
    render(<Page />);
    await waitFor(() => assert.equal(shown("Showing 1 to 50 of 120 contacts"), true), { timeout: SLOW });
    assert.equal(isDisabled(await next()), false);
    assert.equal(isDisabled(screen.getByRole("button", { name: "Previous" })), true, "page 1 has no Previous");

    fireEvent.click(await next());
    await waitFor(() => assert.equal(shown("Showing 51 to 100 of 120 contacts"), true), { timeout: SLOW });
    assert.equal(pagesRequested(read).includes(2), true, "Next asked the server for page 2");

    fireEvent.click(await next());
    await waitFor(() => assert.equal(shown("Showing 101 to 120 of 120 contacts"), true), { timeout: SLOW });
    assert.equal(isDisabled(await next()), true, "the last page has no Next");

    fireEvent.click(screen.getByRole("button", { name: "Previous" }));
    await waitFor(() => assert.equal(shown("Showing 51 to 100 of 120 contacts"), true), { timeout: SLOW });
});

test("changing the search goes back to page 1", async () => {
    const read = mockPages();
    render(<Page />);
    fireEvent.click(await next());
    await waitFor(() => assert.equal(shown("Showing 51 to 100 of 120 contacts"), true), { timeout: SLOW });

    fireEvent.change(screen.getByLabelText("Search contacts by phone number"), { target: { value: "7400" } });
    await waitFor(() => {
        assert.equal(shown("Showing 1 to 50 of 120 contacts"), true);
        assert.ok(screen.queryAllByText("+447400100000").length > 0, "page 1's rows are on screen");
        assert.equal(screen.queryAllByText("+447400100050").length, 0, "page 2's rows are gone");
    }, { timeout: SLOW });
    const last = read.mock.calls.at(-1)!.arguments;
    assert.equal(last[1], 1, "the search asked for page 1");
    assert.equal((last[3] as { search: string }).search, "7400");
});
