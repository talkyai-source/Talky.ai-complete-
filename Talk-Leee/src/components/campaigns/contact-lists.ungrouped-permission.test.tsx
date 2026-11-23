/**
 * "Confirm permission" on the Ungrouped card: only when the server has a
 * summary; one post that carries expected_count; toast, refetch, 409 handling.
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ContactLists } from "@/components/campaigns/contact-lists";
import { dashboardApi, type ContactList, type ContactPermissionSummary } from "@/lib/dashboard-api";
import { ApiClientError } from "@/lib/http-client";
import { api } from "@/lib/api";
import { notificationsStore } from "@/lib/notifications";
import { EXPECTED_PERMISSION, fillContactPermission } from "@/test-utils/contact-permission";

const originals = {
    list: dashboardApi.listContactLists,
    summary: dashboardApi.getContactPermissionSummary,
    confirm: dashboardApi.confirmUngroupedPermission,
    record: dashboardApi.recordContactListPermission,
    request: api.request,
};
const clients: QueryClient[] = [];

function mountLists(readOnly?: boolean) {
    api.request = async <T,>() => ({ ready: true, reason: null }) as T;
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><ContactLists campaignId="campaign-a" readOnly={readOnly} /></QueryClientProvider>);
}

afterEach(() => {
    cleanup();
    clients.splice(0).forEach((client) => client.clear());
    api.request = originals.request;
    dashboardApi.listContactLists = originals.list;
    dashboardApi.getContactPermissionSummary = originals.summary;
    dashboardApi.confirmUngroupedPermission = originals.confirm;
    dashboardApi.recordContactListPermission = originals.record;
    notificationsStore.setIdentity(null);
});

const none = { confirmed: false, basis: null, source: null, permission_date: null, confirmed_at: null } as const;
// Only the newer server sends `queued_for_calling` on a list; that is how the page knows it may ask for the summary.
const ungroupedOld: ContactList = { id: "ungrouped", name: "Ungrouped", is_active: true, contact_count: 7, created_at: null, permission: none };
const ungrouped: ContactList = { ...ungroupedOld, queued_for_calling: null, message: null };
const summaryOn = (over: Partial<ContactPermissionSummary> = {}): ContactPermissionSummary => ({
    dial_gate: "on", statement_version: "2026-11-v1", waiting_without_permission: 7,
    ungrouped_without_permission: 7, queues_on_confirm: false, ...over,
});
const OLD_HINT = "Leads not tied to an uploaded list. Import a CSV to group them.";
const TOKEN = "0123456789abcdef0123456789abcdef";
const toasts = () => notificationsStore.getSnapshot().notifications.map((n) => `${n.title}|${n.message ?? ""}`);

async function openDialog() {
    fireEvent.click(await screen.findByRole("button", { name: "Confirm permission" }));
    return await screen.findByRole("dialog");
}

test("older server: it is never asked for the summary; the old hint and no button", async () => {
    let asked = 0;
    dashboardApi.listContactLists = async () => [ungroupedOld];
    dashboardApi.getContactPermissionSummary = async () => { asked++; return null; };
    mountLists();
    await screen.findByText(OLD_HINT);
    assert.equal(screen.queryByRole("button", { name: "Confirm permission" }) === null, true);
    assert.equal(screen.queryByText("Contacts added without a list.") === null, true);
    assert.equal(asked, 0, "an older server is never asked for the summary");
});

test("new server: the button and the new hint", async () => {
    dashboardApi.listContactLists = async () => [ungrouped];
    dashboardApi.getContactPermissionSummary = async () => summaryOn();
    mountLists();
    await screen.findByText("Contacts added without a list.");
    assert.ok(screen.getByRole("button", { name: "Confirm permission" }));
    assert.equal(screen.queryByText(OLD_HINT) === null, true);
});

test("read-only role: no button even on the new server", async () => {
    dashboardApi.listContactLists = async () => [ungrouped];
    dashboardApi.getContactPermissionSummary = async () => summaryOn();
    mountLists(true);
    await screen.findByText("Contacts added without a list.");
    assert.equal(screen.queryByRole("button", { name: "Confirm permission" }) === null, true);
});

test("nothing is posted until the block is complete; then the post carries expected_count, a toast shows and the lists are read again", async () => {
    notificationsStore.setIdentity({ tenantId: "tenant-a", userId: "user-a" });
    let reads = 0;
    dashboardApi.listContactLists = async () => { reads++; return reads === 1 ? [ungrouped] : []; };
    dashboardApi.getContactPermissionSummary = async () => summaryOn({ ungrouped_token: TOKEN });
    const posted: unknown[][] = [];
    dashboardApi.confirmUngroupedPermission = async (id, permission, expected, token) => {
        posted.push([id, permission, expected, token]);
        return { list: { ...ungrouped, id: "new" }, contacts_confirmed: 7, queued_for_calling: null, message: "Permission recorded for 7 contacts." };
    };
    mountLists();
    const dialog = await openDialog();
    assert.ok(within(dialog).getByText("“Ungrouped”: 7 contacts"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Save confirmation" }));
    assert.equal(posted.length, 0);
    fillContactPermission();
    fireEvent.click(within(dialog).getByRole("button", { name: "Save confirmation" }));
    await waitFor(() => assert.equal(posted.length, 1));
    // The fingerprint that came with the page goes back with the confirmation.
    assert.deepEqual(posted[0], ["campaign-a", EXPECTED_PERMISSION, 7, TOKEN]);
    await waitFor(() => assert.equal(reads, 2));
    await waitFor(() => assert.equal(screen.queryByRole("dialog") === null, true));
    assert.equal(toasts().includes("Permission saved|Permission recorded for 7 contacts."), true);
});

test("a count change (409): the server's message in the dialog, then the lists are read again", async () => {
    let reads = 0;
    dashboardApi.listContactLists = async () => { reads++; return [reads === 1 ? ungrouped : { ...ungrouped, contact_count: 9 }]; };
    dashboardApi.getContactPermissionSummary = async () => summaryOn();
    const message = "The number of contacts without a list changed from 7 to 9. Check them and confirm again.";
    dashboardApi.confirmUngroupedPermission = async () => {
        throw new ApiClientError({ status: 409, code: "contact_count_changed", message, details: { detail: { code: "contact_count_changed", message } }, url: "u", method: "POST" });
    };
    mountLists();
    const dialog = await openDialog();
    fillContactPermission();
    fireEvent.click(within(dialog).getByRole("button", { name: "Save confirmation" }));
    await within(dialog).findByText(message);
    await waitFor(() => assert.equal(reads, 2));
    await screen.findByText("9 contacts");
});

test("the dialog says that saving queues the contacts only when the server says so", async () => {
    dashboardApi.listContactLists = async () => [ungrouped];
    dashboardApi.getContactPermissionSummary = async () => summaryOn({ queues_on_confirm: true });
    mountLists();
    const dialog = await openDialog();
    assert.ok(within(dialog).getByText("This campaign is running: saving queues these contacts for calling."));
});

test("no running line when the server does not say so; a single contact reads in the singular", async () => {
    dashboardApi.listContactLists = async () => [{ ...ungrouped, contact_count: 1 }];
    dashboardApi.getContactPermissionSummary = async () => summaryOn();
    mountLists();
    const dialog = await openDialog();
    assert.ok(within(dialog).getByText("“Ungrouped”: 1 contact"));
    assert.equal(within(dialog).queryByText(/saving queues/) === null, true);
});

test("an existing list's save shows the server's message when it sends one", async () => {
    notificationsStore.setIdentity({ tenantId: "tenant-a", userId: "user-a" });
    const list: ContactList = { id: "list-1", name: "Old import", is_active: false, contact_count: 3, created_at: "2026-09-03T00:00:00Z", permission: none };
    dashboardApi.listContactLists = async () => [list];
    dashboardApi.getContactPermissionSummary = async () => null;
    let saved = 0;
    dashboardApi.recordContactListPermission = async () => {
        saved++;
        return { ...list, permission: { ...none, confirmed: true }, queued_for_calling: 3, message: "Permission recorded. 3 contacts were queued for calling." };
    };
    mountLists();
    const dialog = await openDialog();
    fillContactPermission();
    fireEvent.click(within(dialog).getByRole("button", { name: "Save confirmation" }));
    await waitFor(() => assert.equal(saved, 1));
    await waitFor(() => assert.equal(screen.queryByRole("dialog") === null, true));
    assert.equal(toasts().includes("Permission saved|Permission recorded. 3 contacts were queued for calling."), true);
});

test("the fingerprint posted is the one loaded with the list on screen, not a later one", async () => {
    notificationsStore.setIdentity({ tenantId: "tenant-a", userId: "user-a" });
    dashboardApi.listContactLists = async () => [ungrouped];
    let summaryReads = 0;
    dashboardApi.getContactPermissionSummary = async () => {
        summaryReads++;
        return summaryOn({ ungrouped_token: summaryReads === 1 ? TOKEN : "f".repeat(32) });
    };
    const tokens: unknown[] = [];
    dashboardApi.confirmUngroupedPermission = async (_id, _permission, _expected, token) => {
        tokens.push(token);
        return { list: { ...ungrouped, id: "new" }, contacts_confirmed: 7, queued_for_calling: null, message: "Permission recorded for 7 contacts." };
    };
    mountLists();
    const dialog = await openDialog();
    fillContactPermission();
    fireEvent.click(within(dialog).getByRole("button", { name: "Save confirmation" }));
    await waitFor(() => assert.equal(tokens.length, 1));
    assert.equal(tokens[0], TOKEN);
    // The summary was read once before the click (with the list); the next read comes after the save.
    assert.equal(summaryReads >= 1, true);
});

test("an older server sends no fingerprint and is sent none", async () => {
    notificationsStore.setIdentity({ tenantId: "tenant-a", userId: "user-a" });
    dashboardApi.listContactLists = async () => [ungrouped];
    dashboardApi.getContactPermissionSummary = async () => summaryOn();
    const tokens: unknown[] = [];
    dashboardApi.confirmUngroupedPermission = async (_id, _permission, _expected, token) => {
        tokens.push(token);
        return { list: { ...ungrouped, id: "new" }, contacts_confirmed: 7, queued_for_calling: null, message: "Permission recorded for 7 contacts." };
    };
    mountLists();
    const dialog = await openDialog();
    fillContactPermission();
    fireEvent.click(within(dialog).getByRole("button", { name: "Save confirmation" }));
    await waitFor(() => assert.equal(tokens.length, 1));
    assert.equal(tokens[0], undefined);
});

test("the contacts changed although their number did not (409): the server's message, then the lists are read again", async () => {
    let reads = 0;
    dashboardApi.listContactLists = async () => { reads++; return [ungrouped]; };
    dashboardApi.getContactPermissionSummary = async () => summaryOn({ ungrouped_token: TOKEN });
    const message = "The contacts without a list changed since you loaded this page. Reload the page, check them and confirm again.";
    dashboardApi.confirmUngroupedPermission = async () => {
        throw new ApiClientError({ status: 409, code: "contacts_changed", message, details: { detail: { code: "contacts_changed", message } }, url: "u", method: "POST" });
    };
    mountLists();
    const dialog = await openDialog();
    fillContactPermission();
    fireEvent.click(within(dialog).getByRole("button", { name: "Save confirmation" }));
    await within(dialog).findByText(message);
    await waitFor(() => assert.equal(reads, 2));
});
