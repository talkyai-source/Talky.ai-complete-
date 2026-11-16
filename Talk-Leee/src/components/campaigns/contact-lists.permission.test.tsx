/**
 * Permission to call on the contact list cards: one line per list in three
 * states, and the Confirm permission dialog (200 and 409 paths).
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ContactLists } from "@/components/campaigns/contact-lists";
import { dashboardApi, type ContactList } from "@/lib/dashboard-api";
import { ApiClientError } from "@/lib/http-client";
import { api } from "@/lib/api";
import { notificationsStore } from "@/lib/notifications";
import { EXPECTED_PERMISSION, fillContactPermission } from "@/test-utils/contact-permission";

const originalList = dashboardApi.listContactLists;
const originalRecord = dashboardApi.recordContactListPermission;
const originalRequest = api.request;
const clients: QueryClient[] = [];

function mountLists(readOnly?: boolean) {
    api.request = async <T,>() => ({ ready: true, reason: null }) as T;
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><ContactLists campaignId="campaign-a" readOnly={readOnly} /></QueryClientProvider>);
}

const base: ContactList = {
    id: "list-1",
    name: "Priority leads",
    source: "csv",
    is_active: false,
    contact_count: 3,
    created_at: "2026-09-03T00:00:00Z",
};

const confirmedPermission = {
    confirmed: true,
    basis: "existing_customer",
    source: "Billing system export",
    permission_date: "2026-03-14",
    confirmed_at: "2026-11-10T09:30:00Z",
};

afterEach(() => {
    cleanup();
    clients.splice(0).forEach((client) => client.clear());
    api.request = originalRequest;
    dashboardApi.listContactLists = originalList;
    dashboardApi.recordContactListPermission = originalRecord;
    notificationsStore.setIdentity(null);
});

test("a confirmed list shows the date and the reason, and no Confirm permission button", async () => {
    dashboardApi.listContactLists = async () => [{ ...base, permission: confirmedPermission }];
    mountLists();
    await screen.findByText("Permission confirmed on 10 Nov 2026: They are existing customers");
    assert.equal(screen.queryByRole("button", { name: "Confirm permission" }), null);
});

test("an unconfirmed list says so and offers Confirm permission", async () => {
    dashboardApi.listContactLists = async () => [{
        ...base,
        permission: { confirmed: false, basis: null, source: null, permission_date: null, confirmed_at: null },
    }];
    mountLists();
    await screen.findByText("Permission to call not confirmed");
    assert.ok(screen.getByRole("button", { name: "Confirm permission" }));
});

test("a list with no permission field (older backend) shows nothing about permission", async () => {
    dashboardApi.listContactLists = async () => [base];
    mountLists();
    await screen.findByText("Priority leads");
    assert.equal(screen.queryByText(/Permission/), null);
    assert.equal(screen.queryByRole("button", { name: "Confirm permission" }), null);
});

test("a read-only role sees the unconfirmed line but no button", async () => {
    dashboardApi.listContactLists = async () => [{
        ...base,
        permission: { confirmed: false, basis: null, source: null, permission_date: null, confirmed_at: null },
    }];
    mountLists(true);
    await screen.findByText("Permission to call not confirmed");
    assert.equal(screen.queryByRole("button", { name: "Confirm permission" }), null);
});

const unconfirmed: ContactList = {
    ...base,
    permission: { confirmed: false, basis: null, source: null, permission_date: null, confirmed_at: null },
};

async function openDialog() {
    fireEvent.click(await screen.findByRole("button", { name: "Confirm permission" }));
    return await screen.findByRole("dialog");
}

test("the dialog does not post until the block is complete, then posts the confirmation and the line updates", async () => {
    dashboardApi.listContactLists = async () => [unconfirmed];
    const posted: Array<[string, unknown]> = [];
    dashboardApi.recordContactListPermission = async (id, permission) => {
        posted.push([id, permission]);
        return { ...unconfirmed, permission: { confirmed: true, basis: permission.basis, source: permission.source, permission_date: permission.permission_date, confirmed_at: "2026-11-16T10:00:00Z" } };
    };
    mountLists();
    const dialog = await openDialog();
    fireEvent.click(within(dialog).getByRole("button", { name: "Save confirmation" }));
    assert.equal(posted.length, 0);
    assert.ok(within(dialog).getByText("Tick this box to confirm."));
    fillContactPermission();
    fireEvent.click(within(dialog).getByRole("button", { name: "Save confirmation" }));
    await waitFor(() => assert.equal(posted.length, 1));
    assert.deepEqual(posted[0], ["list-1", EXPECTED_PERMISSION]);
    await screen.findByText("Permission confirmed on 16 Nov 2026: They agreed to be called");
    // Compare a boolean, never the element: a failed assertion on a DOM node makes
    // node:assert describe the whole node, which costs gigabytes inside a retry loop.
    await waitFor(() => assert.equal(screen.queryByRole("dialog") === null, true));
    assert.equal(screen.queryByText("Permission to call not confirmed"), null);
});

test("409 already recorded: shows the message in the dialog and refreshes the lists", async () => {
    let reads = 0;
    dashboardApi.listContactLists = async () => {
        reads++;
        return reads === 1 ? [unconfirmed] : [{ ...base, permission: confirmedPermission }];
    };
    dashboardApi.recordContactListPermission = async () => {
        throw new ApiClientError({
            status: 409,
            code: "contact_permission_already_recorded",
            message: "Already recorded.",
            details: { code: "contact_permission_already_recorded", message: "Already recorded." },
            url: "u",
            method: "POST",
        });
    };
    mountLists();
    const dialog = await openDialog();
    fillContactPermission();
    fireEvent.click(within(dialog).getByRole("button", { name: "Save confirmation" }));
    await screen.findByText("Permission is already recorded for this list.");
    await waitFor(() => assert.equal(reads, 2));
    await screen.findByText("Permission confirmed on 10 Nov 2026: They are existing customers");
});

test("a 422 for the date shows its message beside the date field in the dialog", async () => {
    dashboardApi.listContactLists = async () => [unconfirmed];
    dashboardApi.recordContactListPermission = async () => {
        throw new ApiClientError({
            status: 422,
            code: "contact_permission_invalid",
            message: "The date cannot be in the future.",
            details: { code: "contact_permission_invalid", field: "permission_date", message: "The date cannot be in the future." },
            url: "u",
            method: "POST",
        });
    };
    mountLists();
    const dialog = await openDialog();
    fillContactPermission();
    fireEvent.click(within(dialog).getByRole("button", { name: "Save confirmation" }));
    const date = within(dialog).getByLabelText("Date permission was given");
    await waitFor(() => assert.equal(date.getAttribute("aria-invalid"), "true"));
    const described = (date.getAttribute("aria-describedby") ?? "").split(" ").map((id) => document.getElementById(id)?.textContent);
    assert.ok(described.includes("The date cannot be in the future."));
});
