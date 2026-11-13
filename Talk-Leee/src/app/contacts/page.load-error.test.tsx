import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { dashboardApi } from "@/lib/dashboard-api";
import { ApiClientError } from "@/lib/http-client";

// QA F1-27 (QA-UI-091): with the API failing, Contacts said "Create a
// campaign first" to a tenant with 29 campaigns: the failed campaigns read
// left an empty list, which looks exactly like having none. A failed read
// now says it failed, with a request reference and an explicit retry.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/campaigns/contact-lists")) return { ContactLists: nothing };
    if (id.includes("components/calls/lead-details-panel")) return { ContactCapturedDetails: nothing };
    if (id.includes("lib/queries/inbound-queries")) return { useEffectivePermissions: () => ({ isSuccess: false }) };
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

test("a failed campaigns read is a load error with a retry, never 'Create a campaign first'", async () => {
    let fail = true;
    const reads = mock.method(dashboardApi, "listCampaigns", async () => {
        if (fail) throw new ApiClientError({ status: 500, code: "internal_error", message: "Failed to list campaigns", requestId: "req-contacts-1", url: "http://example.test/campaigns", method: "GET" });
        return { campaigns: [{ id: "camp-1", name: "Spring push", direction: "outbound" }] };
    });
    mock.method(dashboardApi, "listContacts", async () => ({ items: [], total: 0 }));
    render(<Page />);

    await screen.findByText("Could not load your campaigns.", undefined, { timeout: SLOW });
    assert.ok(screen.getByText(/Request reference: req-contacts-1/));
    assert.equal(screen.queryByText("Create a campaign first"), null);
    assert.equal(screen.queryByText(/Failed to list campaigns/), null, "the raw server message is not shown");

    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.equal(reads.mock.callCount(), 2));
    await waitFor(() => assert.equal(screen.queryByText("Could not load your campaigns."), null), { timeout: SLOW });
    assert.equal(screen.queryByText("Create a campaign first"), null);
});

test("a tenant that truly has no campaigns is still told to create one", async () => {
    mock.method(dashboardApi, "listCampaigns", async () => ({ campaigns: [] }));
    render(<Page />);
    await screen.findByText("Create a campaign first", undefined, { timeout: SLOW });
    assert.equal(screen.queryByText("Could not load your campaigns."), null);
});
