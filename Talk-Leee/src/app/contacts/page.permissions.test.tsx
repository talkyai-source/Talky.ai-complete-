import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { dashboardApi } from "@/lib/dashboard-api";

// QA F1-21 (QA-UI-041): the Contacts page offered a read-only user Add more
// contacts, the CSV import, Add contact and per-contact Edit/Delete, all of
// which need campaigns:update. It now shows contacts and lists read-only.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
let permissionsQuery: { isSuccess: boolean; data?: { permissions: string[] } } = { isSuccess: false };
let contactListsReadOnly: boolean | undefined;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/campaigns/contact-lists")) return {
        ContactLists: ({ readOnly }: { readOnly?: boolean }) => { contactListsReadOnly = readOnly; return null; },
    };
    if (id.includes("components/calls/lead-details-panel")) return { ContactCapturedDetails: nothing };
    if (id.includes("lib/queries/inbound-queries")) return { useEffectivePermissions: () => permissionsQuery };
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
const READ_ONLY = ["campaigns:read", "calls:read"];
const MANAGER = ["campaigns:create", "campaigns:read", "campaigns:update", "campaigns:delete"];
afterEach(() => { cleanup(); mock.restoreAll(); permissionsQuery = { isSuccess: false }; contactListsReadOnly = undefined; });

const contact = {
    id: "lead-1", phone_number: "+447400123456", first_name: "Ann", last_name: "Lee", email: null,
    status: "pending", call_attempts: 0, last_call_result: null, created_at: "2026-11-01T00:00:00Z",
};
function load(permissions: string[], options: { contacts?: boolean; campaigns?: boolean } = {}) {
    permissionsQuery = { isSuccess: true, data: { permissions } };
    const campaigns = options.campaigns === false ? [] : [{ id: "camp-1", name: "Spring push", direction: "outbound" }];
    mock.method(dashboardApi, "listCampaigns", async () => ({ campaigns }));
    mock.method(dashboardApi, "listContacts", async () => options.contacts === false
        ? { items: [], total: 0 } : { items: [contact], total: 1 });
    render(<Page />);
}
const absent = (role: "button" | "link", name: RegExp) => screen.queryAllByRole(role, { name }).length === 0;

test("a read-only role sees contacts and lists without adding, importing, editing or deleting", async () => {
    load(READ_ONLY);
    await screen.findAllByText("+447400123456", undefined, { timeout: SLOW });
    for (const name of [/Add more contacts/, /^Edit \+44/, /^Delete \+44/]) assert.equal(absent("button", name), true, `${name} offered`);
    await waitFor(() => assert.equal(contactListsReadOnly, true));
});

test("a read-only role on a campaign without contacts can switch campaigns but not add any", async () => {
    load(READ_ONLY, { contacts: false });
    await screen.findByText(/Your role can view contacts but not add them/, undefined, { timeout: SLOW });
    assert.ok(screen.getByRole("combobox", { name: /campaign/i }), "campaign switch missing");
    for (const name of [/^Add contact$/, /Upload/, /Download sample CSV/]) assert.equal(absent("button", name), true, `${name} offered`);
});

test("a read-only role without campaigns is not sent to create one", async () => {
    load(READ_ONLY, { campaigns: false });
    await screen.findByText(/Ask an account admin to create one/, undefined, { timeout: SLOW });
    assert.equal(absent("link", /Create a campaign/), true, "create link offered");
});

test("a role that may change campaigns keeps adding, editing and deleting", async () => {
    load(MANAGER);
    await screen.findAllByText("+447400123456", undefined, { timeout: SLOW });
    assert.ok(screen.getByRole("button", { name: /Add more contacts/ }));
    assert.ok(screen.getAllByRole("button", { name: /^Edit \+44/ }).length > 0);
    assert.ok(screen.getAllByRole("button", { name: /^Delete \+44/ }).length > 0);
    await waitFor(() => assert.equal(contactListsReadOnly, false));
});

// QA F1-15: the Do-Not-Call list is reachable from Contacts for every role
// (reading it is open to the tenant; changing it is admin-only there).
test("Contacts links to the Do-Not-Call list", async () => {
    load(READ_ONLY);
    const link = await screen.findByRole("link", { name: "Do-Not-Call list" }, { timeout: SLOW });
    assert.equal(link.getAttribute("href"), "/contacts/do-not-call");
});
