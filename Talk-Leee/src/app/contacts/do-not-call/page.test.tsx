import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { dncApi, type DncEntry } from "@/lib/dnc-api";
import { ApiClientError } from "@/lib/http-client";
import { SELECT_COUNTRY_ERROR } from "@/lib/phone-number";
import { UK_E164, UK_NATIONAL, chooseUnitedKingdom } from "@/test-utils/phone-fixtures";

// QA F1-15 (QA-UI-151): the Do-Not-Call list (list, add, bulk import, remove)
// existed only as an API. Contacts now has a Do-Not-Call page.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
let role = "tenant_admin";
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("hooks/useAuth")) return { useAuth: () => ({ user: { id: "u", role, account_timezone: "Europe/London" } }) };
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
const clients: QueryClient[] = [];
afterEach(() => { cleanup(); mock.restoreAll(); clients.splice(0).forEach((client) => client.clear()); role = "tenant_admin"; });

const OPT_OUT: DncEntry = { id: "e1", normalized_number: UK_E164, source: "caller_opt_out", reason: null, created_at: "2026-11-01T10:00:00Z", expires_at: null };
const MANUAL: DncEntry = { id: "e2", normalized_number: "+12025550123", source: "manual_admin", reason: "Asked by email", created_at: "2026-11-02T10:00:00Z", expires_at: "2027-01-01T00:00:00Z" };

function mount(entries: () => Promise<DncEntry[]>) {
    const list = mock.method(dncApi, "list", entries);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><Page /></QueryClientProvider>);
    return list;
}

test("the list shows each number, why it is there, and from when to when; search narrows by digits", async () => {
    mount(async () => [OPT_OUT, MANUAL]);
    await screen.findByText(UK_E164, undefined, { timeout: SLOW });
    assert.ok(screen.getByText("Caller asked to stop"));
    assert.ok(screen.getByText("Added by your team"));
    assert.ok(screen.getByText("Asked by email"));
    assert.ok(screen.getByText("No end date"));
    fireEvent.change(screen.getByLabelText("Search by number"), { target: { value: "7400 123" } });
    assert.ok(screen.getByText(UK_E164));
    assert.equal(screen.queryByText("+12025550123"), null);
});

test("an admin adds a number typed without a country code once the country is chosen", async () => {
    const list = mount(async () => []);
    const add = mock.method(dncApi, "add", async () => OPT_OUT);
    await screen.findByText("No numbers on your Do-Not-Call list yet.", undefined, { timeout: SLOW });
    fireEvent.change(screen.getByLabelText("Phone number"), { target: { value: UK_NATIONAL } });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Add to list/ })); });
    assert.ok(screen.getByText(SELECT_COUNTRY_ERROR));
    assert.equal(add.mock.callCount(), 0, "nothing is sent without a country");
    await chooseUnitedKingdom("dnc-country");
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Add to list/ })); });
    await waitFor(() => assert.equal(add.mock.callCount(), 1));
    assert.deepEqual(add.mock.calls[0].arguments[0], { e164: UK_E164, defaultCountry: "GB", reason: "" });
    await screen.findByText(`${UK_E164} is on the Do-Not-Call list.`);
    await waitFor(() => assert.ok(list.mock.callCount() >= 2, "the list is read again"));
});

test("removing asks first, names the number, and warns when the caller asked to stop", async () => {
    mount(async () => [OPT_OUT]);
    const remove = mock.method(dncApi, "remove", async () => undefined);
    fireEvent.click(await screen.findByRole("button", { name: `Remove ${UK_E164}` }, { timeout: SLOW }));
    let dialog = within(await screen.findByRole("dialog"));
    assert.ok(dialog.getByText(`Campaigns will be able to call ${UK_E164} again.`));
    assert.ok(dialog.getByText(/This caller asked not to be called/));
    fireEvent.click(dialog.getByRole("button", { name: "Cancel" }));
    assert.equal(remove.mock.callCount(), 0);
    fireEvent.click(screen.getByRole("button", { name: `Remove ${UK_E164}` }));
    dialog = within(await screen.findByRole("dialog"));
    await act(async () => { fireEvent.click(dialog.getByRole("button", { name: "Remove" })); });
    await waitFor(() => assert.equal(remove.mock.callCount(), 1));
    assert.deepEqual(remove.mock.calls[0].arguments, ["e1"]);
});

test("a role the server would refuse sees the list without add, import or remove", async () => {
    role = "readonly";
    mount(async () => [OPT_OUT]);
    await screen.findByText(UK_E164, undefined, { timeout: SLOW });
    for (const name of [/Add to list/, /Import numbers/, /^Remove/]) {
        assert.equal(screen.queryAllByRole("button", { name }).length, 0, `${name} offered`);
    }
});

test("a failed read is a load error with a retry, never an empty list", async () => {
    mount(async () => { throw new ApiClientError({ status: 500, code: "internal_error", message: "boom", requestId: "req-dnc", url: "http://example.test/dnc/", method: "GET" }); });
    await screen.findByText("Could not load the Do-Not-Call list.", undefined, { timeout: SLOW });
    assert.ok(screen.getByText(/Request reference: req-dnc/));
    assert.equal(screen.queryByText("No numbers on your Do-Not-Call list yet."), null);
});

test("an import reports what was added, already listed and not valid", async () => {
    mount(async () => []);
    const bulk = mock.method(dncApi, "bulkImport", async () => ({ accepted_count: 2, skipped_count: 1, invalid_count: 1, accepted: ["+1", "+2"], invalid: ["123"] }));
    await screen.findByText("No numbers on your Do-Not-Call list yet.", undefined, { timeout: SLOW });
    fireEvent.change(screen.getByLabelText("Numbers, one per line"), { target: { value: "+447400123456\n+12025550123\n\n123\n" } });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Import numbers/ })); });
    await waitFor(() => assert.equal(bulk.mock.callCount(), 1));
    assert.deepEqual(bulk.mock.calls[0].arguments[0], { numbers: ["+447400123456", "+12025550123", "123"], defaultCountry: "" });
    await screen.findByText("Added 2, already listed 1, not valid 1.");
    assert.ok(screen.getByText("Not valid: 123"));
});
