/**
 * FX4c Task A1 — both Contacts error states for a failed contacts read show
 * the failed request's reference ("Request reference: …").
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { dashboardApi } from "@/lib/dashboard-api";
import { ApiClientError } from "@/lib/http-client";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
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
const CONTACT = { id: "c-1", campaign_id: "camp-1", phone_number: "+447400123456", status: "pending" };
const shown = (text: string | RegExp) => screen.queryByText(text) !== null;
const refused = (requestId: string) => new ApiClientError({
    status: 503, code: "service_unavailable", message: "Contacts could not be loaded right now.", requestId,
    url: "http://example.test/contacts", method: "GET",
});

test("a failed first contacts read shows its request reference", async () => {
    mock.method(dashboardApi, "listCampaigns", async () => CAMPAIGNS);
    mock.method(dashboardApi, "listContacts", async () => { throw refused("req-fx4c-contacts-1"); });
    render(<Page />);
    await waitFor(() => assert.equal(shown("Contacts could not be loaded right now."), true), { timeout: SLOW });
    assert.equal(shown("Request reference: req-fx4c-contacts-1"), true);
});

test("a failed search inside a campaign that has contacts shows its request reference", async () => {
    mock.method(dashboardApi, "listCampaigns", async () => CAMPAIGNS);
    let fail = false;
    mock.method(dashboardApi, "listContacts", async () => {
        if (fail) throw refused("req-fx4c-contacts-2");
        return { items: [CONTACT], total: 1 };
    });
    render(<Page />);
    await waitFor(() => assert.equal(screen.queryAllByText("+447400123456").length > 0, true), { timeout: SLOW });
    assert.equal(shown(/Request reference/), false, "no reference while nothing failed");

    fail = true;
    fireEvent.change(screen.getByPlaceholderText("Search by phone number…"), { target: { value: "0740" } });
    await waitFor(() => assert.equal(shown("Contacts could not be loaded right now."), true), { timeout: SLOW });
    assert.equal(shown("Request reference: req-fx4c-contacts-2"), true);
    assert.equal(screen.queryByPlaceholderText("Search by phone number…") !== null, true, "the error is inside the contacts view");
});
