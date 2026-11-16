/**
 * Permission to call on the contacts page: the CSV upload card, the "Check the
 * file first" mapper import, and adding a single contact. Nothing is sent until
 * the confirmation block is complete; then the request carries it.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { dashboardApi } from "@/lib/dashboard-api";
import { extendedApi, type BulkImportResponse } from "@/lib/extended-api";
import { leadDetailsApi } from "@/lib/lead-details-api";
import { ApiClientError } from "@/lib/http-client";
import { EXPECTED_PERMISSION, fillContactPermission } from "@/test-utils/contact-permission";
import { UK_E164, UK_NATIONAL, chooseUnitedKingdom, csvFile } from "@/test-utils/phone-fixtures";

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

const okResponse: BulkImportResponse = { total_rows: 1, imported: 1, failed: 0, duplicates_skipped: 0, errors: [] };
const FILE_TEXT = `Phone,First Name,Last Name,Email\n${UK_NATIONAL},Ann,Lee,ann@example.com`;

function refusal(detail: Record<string, unknown>) {
    return new ApiClientError({ status: 422, code: String(detail.code), message: String(detail.message), details: detail, url: "u", method: "POST" });
}

function mockHome() {
    mock.method(dashboardApi, "listCampaigns", async () => ({ campaigns: [{ id: "camp-1", name: "Spring push", direction: "outbound" }] }));
    mock.method(dashboardApi, "listContacts", async () => ({ items: [], total: 1 }));
}

async function openUploadCardWithFile() {
    render(<Page />);
    fireEvent.click(await screen.findByRole("button", { name: /Add more contacts/ }, { timeout: SLOW }));
    await screen.findByText("Add more contacts", { selector: "h3" }, { timeout: SLOW });
    fireEvent.change(document.getElementById("contacts-csv-file") as HTMLInputElement, { target: { files: [csvFile(FILE_TEXT)] } });
    await screen.findAllByTestId("contacts-preview-row", undefined, { timeout: SLOW });
    await chooseUnitedKingdom("contacts-import-country");
    await waitFor(() => assert.ok(screen.getByRole("button", { name: /Upload 1 contact$/ })), { timeout: SLOW });
}

test("upload card: empty block sends nothing and shows the errors; complete block sends the file with the four values", async () => {
    mockHome();
    const calls: Array<unknown[]> = [];
    mock.method(extendedApi, "uploadCSV", async (...args: unknown[]) => { calls.push(args); return okResponse; });
    await openUploadCardWithFile();

    fireEvent.click(screen.getByRole("button", { name: /Upload 1 contact$/ }));
    assert.equal(calls.length, 0);
    assert.ok(screen.getByText("Choose why you can call them."));
    assert.ok(screen.getByText("Tick this box to confirm."));

    fillContactPermission();
    fireEvent.click(screen.getByRole("button", { name: /Upload 1 contact$/ }));
    await waitFor(() => assert.equal(calls.length, 1), { timeout: SLOW });
    assert.equal(calls[0]![0], "camp-1");
    assert.ok(calls[0]![1] instanceof File);
    assert.deepEqual(calls[0]![2], EXPECTED_PERMISSION);
    assert.equal(calls[0]![3], true);
});

test("upload card: a 422 for the date shows beside the date field and no generic upload error", async () => {
    mockHome();
    mock.method(extendedApi, "uploadCSV", async () => {
        throw refusal({ code: "contact_permission_invalid", field: "permission_date", message: "The date cannot be in the future." });
    });
    await openUploadCardWithFile();
    fillContactPermission();
    fireEvent.click(screen.getByRole("button", { name: /Upload 1 contact$/ }));
    const date = screen.getByLabelText("Date permission was given");
    await waitFor(() => assert.equal(date.getAttribute("aria-invalid"), "true"), { timeout: SLOW });
    const described = (date.getAttribute("aria-describedby") ?? "").split(" ").map((id) => document.getElementById(id)?.textContent);
    assert.ok(described.includes("The date cannot be in the future."));
    assert.equal(screen.queryByText("Upload failed"), null);
});

test("mapper Import: nothing is sent until the same block is complete, then the upload carries it", async () => {
    mockHome();
    mock.method(leadDetailsApi, "previewImport", async () => ({
        headers: { Phone: "phone_number", "First Name": "first_name", "Last Name": "last_name", Email: "email" },
        unmapped: [], total_rows: 1, valid_rows: 1, issues: [], duplicates_in_file: [], sample: [],
    }));
    const calls: Array<unknown[]> = [];
    mock.method(extendedApi, "uploadCSV", async (...args: unknown[]) => { calls.push(args); return okResponse; });
    render(<Page />);
    fireEvent.click(await screen.findByRole("button", { name: /Add more contacts/ }, { timeout: SLOW }));
    await screen.findByText("Check the file first", undefined, { timeout: SLOW });
    const mapperCard = screen.getByText("Check the file first").closest(".content-card")!;
    fireEvent.change(mapperCard.querySelector('input[type="file"]') as HTMLInputElement, { target: { files: [csvFile(FILE_TEXT, "mapper.csv")] } });
    const importButton = await screen.findByRole("button", { name: /Import 1 contact$/ }, { timeout: SLOW });
    await chooseUnitedKingdom("contacts-mapper-country");
    fireEvent.click(importButton);
    await waitFor(() => assert.ok(screen.getByText("Tick this box to confirm.")), { timeout: SLOW });
    assert.equal(calls.length, 0);
    fillContactPermission();
    fireEvent.click(importButton);
    await waitFor(() => assert.equal(calls.length, 1), { timeout: SLOW });
    assert.deepEqual(calls[0]![2], EXPECTED_PERMISSION);
});

test("add contact: empty block sends nothing; a complete block sends the contact and the permission object", async () => {
    mockHome();
    const add = mock.method(dashboardApi, "addContact", async () => ({ message: "ok", contact: { id: "c-1", phone_number: UK_E164 } }));
    render(<Page />);
    fireEvent.click(await screen.findByRole("button", { name: /Add contact/ }, { timeout: SLOW }));
    const phone = await screen.findByLabelText(/Phone Number/, undefined, { timeout: SLOW });
    await chooseUnitedKingdom("contact-country");
    fireEvent.change(phone, { target: { value: UK_NATIONAL } });
    await waitFor(() => assert.equal(document.getElementById("contact-phone-status")?.textContent, `Saves as ${UK_E164}`), { timeout: SLOW });
    const save = screen.getByRole("button", { name: "Add" });
    fireEvent.click(save);
    assert.equal(add.mock.callCount(), 0);
    assert.ok(screen.getByText("Tick this box to confirm."));
    fillContactPermission();
    fireEvent.click(save);
    await waitFor(() => assert.equal(add.mock.callCount(), 1), { timeout: SLOW });
    const [campaignId, payload, permission] = add.mock.calls[0].arguments as unknown as [string, { phone_number: string }, unknown];
    assert.equal(campaignId, "camp-1");
    assert.equal(payload.phone_number, UK_E164);
    assert.deepEqual(permission, EXPECTED_PERMISSION);
});

test("add contact: a 422 contact_permission_required shows the backend message in the block", async () => {
    mockHome();
    mock.method(dashboardApi, "addContact", async () => {
        throw refusal({ code: "contact_permission_required", message: "Confirm you have permission to call these contacts before adding them." });
    });
    render(<Page />);
    fireEvent.click(await screen.findByRole("button", { name: /Add contact/ }, { timeout: SLOW }));
    const phone = await screen.findByLabelText(/Phone Number/, undefined, { timeout: SLOW });
    await chooseUnitedKingdom("contact-country");
    fireEvent.change(phone, { target: { value: UK_NATIONAL } });
    await waitFor(() => assert.equal(document.getElementById("contact-phone-status")?.textContent, `Saves as ${UK_E164}`), { timeout: SLOW });
    fillContactPermission();
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await screen.findByText("Confirm you have permission to call these contacts before adding them.", undefined, { timeout: SLOW });
});
