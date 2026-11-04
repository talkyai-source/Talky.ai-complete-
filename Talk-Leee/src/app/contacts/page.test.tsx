/**
 * 2.4 Contacts and phone number validation — contacts page entry points:
 *   EP1 the CSV upload card, EP2 the "Check the file first" mapper's Import
 *   button, EP5 the Add/Edit contact form. AC-PH for each: with United
 *   Kingdom selected 07400 123456 is sent as +447400123456, 12345 is rejected
 *   with a row-level / inline error and nothing is sent for it.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { dashboardApi } from "@/lib/dashboard-api";
import { extendedApi, type BulkImportResponse } from "@/lib/extended-api";
import { leadDetailsApi } from "@/lib/lead-details-api";
import { CONTACT_UPLOAD_COLUMNS } from "@/lib/contact-upload-csv";
import { INVALID_PHONE_ERROR, PHONE_HELP_TEXT, SELECT_COUNTRY_ERROR } from "@/lib/phone-number";
import { BAD_NUMBER, UK_E164, UK_NATIONAL, chooseUnitedKingdom, csvFile } from "@/test-utils/phone-fixtures";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
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

const okResponse: BulkImportResponse = { total_rows: 1, imported: 1, failed: 0, duplicates_skipped: 0, errors: [] };

// Line 1 preamble, line 2 blank, line 3 header, line 4 the UK row, line 5 the bad row.
const FILE_TEXT = [
    "Export from CRM",
    "",
    "Phone,First Name,Last Name,Email",
    `${UK_NATIONAL},Ann,Lee,ann@example.com`,
    `${BAD_NUMBER},Bad,Row,`,
].join("\n");

function mockHome(upload: (file: File) => Promise<BulkImportResponse>) {
    mock.method(dashboardApi, "listCampaigns", async () => ({ campaigns: [{ id: "camp-1", name: "Spring push", direction: "outbound" }] }));
    mock.method(dashboardApi, "listContacts", async () => ({ items: [], total: 1 }));
    return mock.method(extendedApi, "uploadCSV", async (_campaignId: string, file: File) => upload(file));
}

async function openUploadPanel() {
    render(<Page />);
    const addMore = await screen.findByRole("button", { name: /Add more contacts/ }, { timeout: SLOW });
    fireEvent.click(addMore);
    await screen.findByText("Upload CSV", undefined, { timeout: SLOW }).catch(() => null);
    await screen.findByText("Add more contacts", { selector: "h3" }, { timeout: SLOW });
}

function uploadCardFileInput(): HTMLInputElement {
    return document.getElementById("contacts-csv-file") as HTMLInputElement;
}

test("EP1 upload card: UK selected sends 07400 123456 as +447400123456, 12345 is listed by its original row and never sent", async () => {
    const sent: string[] = [];
    mockHome(async (file) => { sent.push(await file.text()); return okResponse; });
    await openUploadPanel();

    fireEvent.change(uploadCardFileInput(), { target: { files: [csvFile(FILE_TEXT)] } });
    const rows = await screen.findAllByTestId("contacts-preview-row", undefined, { timeout: SLOW });
    assert.equal(rows.length, 2);
    // No country yet: a national number is an error, never a guess.
    assert.ok(within(rows[0]).getByText(SELECT_COUNTRY_ERROR));
    assert.equal(within(rows[0]).getAllByRole("cell")[0].textContent, "4", "row number is the ORIGINAL file line");
    assert.equal(within(rows[1]).getAllByRole("cell")[0].textContent, "5");

    await chooseUnitedKingdom("contacts-import-country");
    await waitFor(() => assert.ok(screen.getByText(UK_E164)), { timeout: SLOW });
    const table = screen.getByText("Saves as").closest("table")!;
    const [ukRow, badRow] = within(table).getAllByTestId("contacts-preview-row");
    assert.equal(within(ukRow).getAllByRole("cell")[5].textContent, UK_E164, "Saves as column shows the E.164");
    assert.equal(within(badRow).getAllByRole("cell")[5].textContent, INVALID_PHONE_ERROR, "Saves as column shows the row's error");

    fireEvent.click(screen.getByRole("button", { name: /Upload 1 contacts/ }));
    await waitFor(() => assert.equal(sent.length, 1), { timeout: SLOW });
    const lines = sent[0].split("\n");
    assert.equal(lines[0], CONTACT_UPLOAD_COLUMNS.join(","));
    assert.equal(lines.length, 2, "only the valid row is sent");
    assert.equal(lines[1].split(",")[0], UK_E164);
    assert.ok(!lines.some((l) => l.startsWith(BAD_NUMBER)));

    // The result card lists the rejected row by its ORIGINAL row number with its own error.
    const results = await screen.findByTestId("contacts-import-results", undefined, { timeout: SLOW });
    const errorRows = within(results).getAllByTestId("contacts-import-error-row");
    assert.equal(errorRows.length, 1);
    const cells = within(errorRows[0]).getAllByRole("cell").map((c) => c.textContent);
    assert.deepEqual(cells, ["5", BAD_NUMBER, INVALID_PHONE_ERROR]);
});

test("EP1 result card maps the backend's clean-file row numbers back to the original file rows", async () => {
    mockHome(async () => ({ total_rows: 1, imported: 0, failed: 1, duplicates_skipped: 0, errors: [{ row: 2, error: "duplicate", phone: UK_E164 }] }));
    await openUploadPanel();
    fireEvent.change(uploadCardFileInput(), { target: { files: [csvFile(FILE_TEXT)] } });
    await screen.findAllByTestId("contacts-preview-row", undefined, { timeout: SLOW });
    await chooseUnitedKingdom("contacts-import-country");
    await waitFor(() => assert.ok(screen.getByRole("button", { name: /Upload 1 contacts/ })), { timeout: SLOW });
    fireEvent.click(screen.getByRole("button", { name: /Upload 1 contacts/ }));
    const results = await screen.findByTestId("contacts-import-results", undefined, { timeout: SLOW });
    const rows = within(results).getAllByTestId("contacts-import-error-row").map((r) => within(r).getAllByRole("cell").map((c) => c.textContent));
    // Backend row 2 (first data line of the clean CSV) is line 4 of the user's file.
    assert.deepEqual(rows, [["5", BAD_NUMBER, INVALID_PHONE_ERROR], ["4", UK_E164, "duplicate"]]);
});

test("EP1 shows the exact help text and no longer promises automatic normalisation", async () => {
    mockHome(async () => okResponse);
    await openUploadPanel();
    assert.ok(screen.getAllByText(PHONE_HELP_TEXT).length >= 1);
    assert.equal(document.body.textContent?.includes("added automatically"), false);
    assert.equal(document.body.textContent?.includes("normalized"), false);
});

test("EP2 mapper Import button sends the file through the SAME upload path and CSV as EP1", async () => {
    const sent: string[] = [];
    mockHome(async (file) => { sent.push(await file.text()); return okResponse; });
    mock.method(leadDetailsApi, "previewImport", async () => ({
        headers: { Phone: "phone_number", "First Name": "first_name", "Last Name": "last_name", Email: "email" },
        unmapped: [], total_rows: 2, valid_rows: 2, issues: [], duplicates_in_file: [], sample: [],
    }));
    await openUploadPanel();

    // EP1 first, for the reference CSV.
    fireEvent.change(uploadCardFileInput(), { target: { files: [csvFile(FILE_TEXT)] } });
    await screen.findAllByTestId("contacts-preview-row", undefined, { timeout: SLOW });
    await chooseUnitedKingdom("contacts-import-country");
    await waitFor(() => assert.ok(screen.getByRole("button", { name: /Upload 1 contacts/ })), { timeout: SLOW });
    fireEvent.click(screen.getByRole("button", { name: /Upload 1 contacts/ }));
    await waitFor(() => assert.equal(sent.length, 1), { timeout: SLOW });

    // The panel closed after the import; reopen it for the mapper (EP2).
    fireEvent.click(screen.getByRole("button", { name: /Add more contacts/ }));
    await screen.findByText("Check the file first", undefined, { timeout: SLOW });
    const mapperCard = screen.getByText("Check the file first").closest(".content-card")!;
    const mapperInput = mapperCard.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(mapperInput, { target: { files: [csvFile(FILE_TEXT, "mapper.csv")] } });
    const importButton = await screen.findByRole("button", { name: /Import 2 contacts/ }, { timeout: SLOW });
    await chooseUnitedKingdom("contacts-mapper-country");
    fireEvent.click(importButton);
    await waitFor(() => assert.equal(sent.length, 2), { timeout: SLOW });
    assert.equal(sent[1], sent[0], "EP2 and EP1 send byte-identical CSVs");
    assert.equal(sent[1].split("\n")[1].split(",")[0], UK_E164);
    assert.ok(!sent[1].includes(`\n${BAD_NUMBER},`));
    const results = await screen.findByTestId("contacts-import-results", undefined, { timeout: SLOW });
    const cells = within(results).getAllByTestId("contacts-import-error-row").map((r) => within(r).getAllByRole("cell").map((c) => c.textContent));
    assert.deepEqual(cells, [["5", BAD_NUMBER, INVALID_PHONE_ERROR]]);
});

test("EP5 add-contact form: Saves as line, inline error blocks Save, and the E.164 is what is sent", async () => {
    mockHome(async () => okResponse);
    const addContact = mock.method(dashboardApi, "addContact", async () => ({ message: "ok", contact: { id: "c-1", phone_number: UK_E164 } }));
    render(<Page />);
    const addButton = await screen.findByRole("button", { name: /Add contact/ }, { timeout: SLOW });
    fireEvent.click(addButton);
    const phone = await screen.findByLabelText(/Phone Number/, undefined, { timeout: SLOW }) as HTMLInputElement;
    assert.equal(document.getElementById("contact-phone-help")?.textContent, PHONE_HELP_TEXT);

    await chooseUnitedKingdom("contact-country");
    fireEvent.change(phone, { target: { value: BAD_NUMBER } });
    await waitFor(() => assert.equal(document.getElementById("contact-phone-status")?.textContent, INVALID_PHONE_ERROR), { timeout: SLOW });
    const save = screen.getByRole("button", { name: "Add" }) as HTMLButtonElement;
    assert.equal(save.disabled, true, "Save is blocked while the number is invalid");
    fireEvent.submit(save.closest("form")!);
    assert.equal(addContact.mock.callCount(), 0, "nothing is sent for an invalid number");

    fireEvent.change(phone, { target: { value: UK_NATIONAL } });
    await waitFor(() => assert.equal(document.getElementById("contact-phone-status")?.textContent, `Saves as ${UK_E164}`), { timeout: SLOW });
    assert.equal(save.disabled, false);
    fireEvent.click(save);
    await waitFor(() => assert.equal(addContact.mock.callCount(), 1), { timeout: SLOW });
    const [campaignId, payload] = addContact.mock.calls[0].arguments as [string, { phone_number: string }];
    assert.equal(campaignId, "camp-1");
    assert.equal(payload.phone_number, UK_E164);
});

test("EP5 with no country selected: a national number shows the select-country error and Save stays blocked", async () => {
    mockHome(async () => okResponse);
    const addContact = mock.method(dashboardApi, "addContact", async () => ({ message: "ok", contact: { id: "c-1", phone_number: UK_E164 } }));
    render(<Page />);
    fireEvent.click(await screen.findByRole("button", { name: /Add contact/ }, { timeout: SLOW }));
    const phone = await screen.findByLabelText(/Phone Number/, undefined, { timeout: SLOW });
    fireEvent.change(phone, { target: { value: UK_NATIONAL } });
    await waitFor(() => assert.equal(document.getElementById("contact-phone-status")?.textContent, SELECT_COUNTRY_ERROR), { timeout: SLOW });
    assert.equal((screen.getByRole("button", { name: "Add" }) as HTMLButtonElement).disabled, true);
    assert.equal(addContact.mock.callCount(), 0);
});

// 2.4 F2 Task 1 — optional number cells/fields follow the same rule as the main number.
const MOBILE_FILE_TEXT = [
    "Phone,First Name,Mobile",
    `+447400123456,Ann,07400 123457`, // line 2: valid mobile → E.164
    `+447400123456,Bob,${BAD_NUMBER}`, // line 3: invalid mobile → row error, not sent
].join("\n");

test("EP1: an invalid mobile_number cell is a column-named row error with the original row number and the row is not sent; a valid one is sent as E.164", async () => {
    const sent: string[] = [];
    mockHome(async (file) => { sent.push(await file.text()); return okResponse; });
    await openUploadPanel();
    fireEvent.change(uploadCardFileInput(), { target: { files: [csvFile(MOBILE_FILE_TEXT)] } });
    const rows = await screen.findAllByTestId("contacts-preview-row", undefined, { timeout: SLOW });
    assert.equal(within(rows[0]).getAllByRole("cell")[5].textContent, `mobile_number: ${SELECT_COUNTRY_ERROR}`, "no country: optional national cell named by column");
    await chooseUnitedKingdom("contacts-import-country");
    await waitFor(() => assert.ok(screen.getByText(`mobile_number: ${INVALID_PHONE_ERROR}`)), { timeout: SLOW });
    const [okRow, badRow] = screen.getAllByTestId("contacts-preview-row");
    assert.equal(within(okRow).getAllByRole("cell")[5].textContent, UK_E164);
    assert.equal(within(badRow).getAllByRole("cell")[0].textContent, "3");
    fireEvent.click(screen.getByRole("button", { name: /Upload 1 contacts/ }));
    await waitFor(() => assert.equal(sent.length, 1), { timeout: SLOW });
    const lines = sent[0].split("\n");
    assert.equal(lines.length, 2);
    assert.equal(lines[1].split(",")[6], "+447400123457", "mobile_number column carries the E.164");
    assert.ok(!lines[1].split(",").includes(BAD_NUMBER));
    const results = await screen.findByTestId("contacts-import-results", undefined, { timeout: SLOW });
    const cells = within(results).getAllByTestId("contacts-import-error-row").map((r) => within(r).getAllByRole("cell").map((c) => c.textContent));
    assert.deepEqual(cells, [["3", UK_E164, `mobile_number: ${INVALID_PHONE_ERROR}`]]);
});

test("EP5: an invalid mobile number shows its inline error, blocks Save and sends nothing; a valid one is sent as E.164", async () => {
    mockHome(async () => okResponse);
    const addContact = mock.method(dashboardApi, "addContact", async () => ({ message: "ok", contact: { id: "c-1", phone_number: UK_E164 } }));
    render(<Page />);
    fireEvent.click(await screen.findByRole("button", { name: /Add contact/ }, { timeout: SLOW }));
    const phone = await screen.findByLabelText(/Phone Number/, undefined, { timeout: SLOW });
    await chooseUnitedKingdom("contact-country");
    fireEvent.change(phone, { target: { value: UK_NATIONAL } });
    await waitFor(() => assert.equal(document.getElementById("contact-phone-status")?.textContent, `Saves as ${UK_E164}`), { timeout: SLOW });
    fireEvent.click(screen.getByRole("button", { name: /More details/ }));
    const mobile = await screen.findByLabelText("Mobile Number", undefined, { timeout: SLOW });
    fireEvent.change(mobile, { target: { value: BAD_NUMBER } });
    await waitFor(() => assert.equal(document.getElementById("contact-mobile-number-status")?.textContent, INVALID_PHONE_ERROR), { timeout: SLOW });
    const save = screen.getByRole("button", { name: "Add" }) as HTMLButtonElement;
    assert.equal(save.disabled, true, "Save is blocked by the invalid mobile number");
    fireEvent.submit(save.closest("form")!);
    assert.equal(addContact.mock.callCount(), 0);
    fireEvent.change(mobile, { target: { value: "07400 123457" } });
    await waitFor(() => assert.equal(document.getElementById("contact-mobile-number-status")?.textContent, "Saves as +447400123457"), { timeout: SLOW });
    assert.equal(save.disabled, false);
    fireEvent.click(save);
    await waitFor(() => assert.equal(addContact.mock.callCount(), 1), { timeout: SLOW });
    const payload = addContact.mock.calls[0].arguments[1] as { phone_number: string; mobile_number: string };
    assert.equal(payload.phone_number, UK_E164);
    assert.equal(payload.mobile_number, "+447400123457");
});
