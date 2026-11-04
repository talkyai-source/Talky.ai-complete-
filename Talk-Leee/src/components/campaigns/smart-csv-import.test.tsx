/**
 * 2.4 — EP3, the campaign detail "Import CSV" modal, CSV tab and Paste tab.
 * AC-PH: with United Kingdom selected 07400 123456 is sent as +447400123456,
 * 12345 is rejected with a row-level error and nothing is sent for it; the
 * CSV tab builds the same CSV as the contacts page (shared builder).
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { extendedApi, type BulkImportResponse } from "@/lib/extended-api";
import { CONTACT_UPLOAD_COLUMNS } from "@/lib/contact-upload-csv";
import { INVALID_PHONE_ERROR, PHONE_HELP_TEXT, SELECT_COUNTRY_ERROR } from "@/lib/phone-number";
import { BAD_NUMBER, UK_E164, UK_NATIONAL, chooseUnitedKingdom, csvFile } from "@/test-utils/phone-fixtures";

// The portal Modal is stubbed so the body and footer render inline.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id.includes("components/ui/modal")) {
        return {
            Modal: ({ open, children, footer }: { open: boolean; children: React.ReactNode; footer: React.ReactNode }) =>
                open ? <div>{children}{footer}</div> : null,
        };
    }
    return originalLoad.call(this, id, ...args);
};
let SmartCsvImport: React.ComponentType<{ open: boolean; campaignId: string; onClose: () => void; onImported?: () => void }>;
let pasteTokens: (text: string) => Array<{ row: number; raw: string }>;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    ({ SmartCsvImport, pasteTokens } = require("./smart-csv-import"));
} finally {
    loader._load = originalLoad;
}

const SLOW = 15_000;
afterEach(() => { cleanup(); mock.restoreAll(); });

const okResponse: BulkImportResponse = { total_rows: 1, imported: 1, failed: 0, duplicates_skipped: 0, errors: [] };

// Same file as the contacts page test: line 3 header, line 4 UK row, line 5 bad row.
const FILE_TEXT = [
    "Export from CRM",
    "",
    "Phone,First Name,Last Name,Email",
    `${UK_NATIONAL},Ann,Lee,ann@example.com`,
    `${BAD_NUMBER},Bad,Row,`,
].join("\n");

/** The CSV the contacts page upload card sends for FILE_TEXT (shared builder). */
const EXPECTED_CSV = `${CONTACT_UPLOAD_COLUMNS.join(",")}\n${UK_E164},Ann Lee,Ann,Lee,ann@example.com,,,,,,,,,`;

test("EP3 CSV tab: Saves as column, original row numbers, UK sends +447400123456 via the shared CSV, 12345 never sent", async () => {
    const sent: string[] = [];
    mock.method(extendedApi, "uploadCSV", async (_id: string, file: File) => { sent.push(await file.text()); return okResponse; });
    render(<SmartCsvImport open campaignId="camp-1" onClose={() => {}} />);
    assert.ok(screen.getByText(PHONE_HELP_TEXT));

    fireEvent.change(screen.getByTestId("campaign-import-file"), { target: { files: [csvFile(FILE_TEXT)] } });
    const rows = await screen.findAllByTestId("campaign-import-row", undefined, { timeout: SLOW });
    assert.equal(rows.length, 2);
    assert.ok(within(rows[0]).getByText(SELECT_COUNTRY_ERROR), "no country yet: national number is an error");
    assert.equal(within(rows[0]).getAllByRole("cell")[1].textContent, "4", "# column is the original file line");
    assert.equal(within(rows[1]).getAllByRole("cell")[1].textContent, "5");

    await chooseUnitedKingdom("campaign-import-country");
    await waitFor(() => assert.ok(screen.getByText(UK_E164)), { timeout: SLOW });
    const [ukRow, badRow] = screen.getAllByTestId("campaign-import-row");
    assert.equal(within(ukRow).getAllByRole("cell")[3].textContent, UK_E164, "Saves as shows the E.164");
    assert.equal(within(badRow).getAllByRole("cell")[3].textContent, INVALID_PHONE_ERROR, "Saves as shows the row's error");
    assert.ok(screen.getAllByText(/1 ready/).length >= 1);

    fireEvent.click(screen.getByRole("button", { name: /Import 1 contact$/ }));
    await waitFor(() => assert.equal(sent.length, 1), { timeout: SLOW });
    assert.equal(sent[0], EXPECTED_CSV);
    assert.ok(!sent[0].includes(`\n${BAD_NUMBER},`));
});

test("EP3 CSV tab result lists backend errors by the original file row", async () => {
    mock.method(extendedApi, "uploadCSV", async () => ({ total_rows: 1, imported: 0, failed: 1, duplicates_skipped: 0, errors: [{ row: 2, error: "duplicate", phone: UK_E164 }] }));
    render(<SmartCsvImport open campaignId="camp-1" onClose={() => {}} />);
    fireEvent.change(screen.getByTestId("campaign-import-file"), { target: { files: [csvFile(FILE_TEXT)] } });
    await screen.findAllByTestId("campaign-import-row", undefined, { timeout: SLOW });
    await chooseUnitedKingdom("campaign-import-country");
    await waitFor(() => assert.ok(screen.getByRole("button", { name: /Import 1 contact$/ })), { timeout: SLOW });
    fireEvent.click(screen.getByRole("button", { name: /Import 1 contact$/ }));
    await screen.findByText(/row 4 \(\+447400123456\): duplicate/, undefined, { timeout: SLOW });
});

test("EP3 Paste tab: every number is parsed first, invalid ones are row errors, only valid E.164 values are sent", async () => {
    const paste = mock.method(extendedApi, "pasteContacts", async () => ({ ...okResponse, errors: [{ row: 1, error: "duplicate", phone: UK_E164 }], failed: 1, imported: 0 }));
    render(<SmartCsvImport open campaignId="camp-1" onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Paste numbers" }));
    const box = screen.getByLabelText("Paste phone numbers");
    fireEvent.change(box, { target: { value: `${BAD_NUMBER}\n${UK_NATIONAL}` } });
    let rows = await screen.findAllByTestId("paste-row", undefined, { timeout: SLOW });
    assert.equal(rows.length, 2);
    // No country yet: every number without "+" is the select-country error, never a guess.
    assert.equal(within(rows[0]).getAllByRole("cell")[2].textContent, SELECT_COUNTRY_ERROR);
    assert.equal(within(rows[1]).getAllByRole("cell")[2].textContent, SELECT_COUNTRY_ERROR);
    assert.equal((screen.getByRole("button", { name: /Import 0 contacts/ }) as HTMLButtonElement).disabled, true);

    await chooseUnitedKingdom("campaign-import-country");
    await waitFor(() => assert.ok(screen.getByText(UK_E164)), { timeout: SLOW });
    rows = screen.getAllByTestId("paste-row");
    assert.deepEqual(rows.map((r) => within(r).getAllByRole("cell").map((c) => c.textContent)), [
        ["1", BAD_NUMBER, INVALID_PHONE_ERROR],
        ["2", UK_NATIONAL, UK_E164],
    ]);
    fireEvent.click(screen.getByRole("button", { name: /Import 1 contact$/ }));
    await waitFor(() => assert.equal(paste.mock.callCount(), 1), { timeout: SLOW });
    assert.deepEqual(paste.mock.calls[0].arguments, ["camp-1", UK_E164]);
    // Backend row 1 (the first value sent) is the user's second pasted row.
    await screen.findByText(/row 2 \(\+447400123456\): duplicate/, undefined, { timeout: SLOW });
    assert.equal(document.body.textContent?.includes("normalized"), false);
});

test("pasteTokens keeps the user's row numbers", () => {
    assert.deepEqual(pasteTokens(" +1 415 555 1234 \n\n12345; 07400 123456"), [
        { row: 1, raw: "+1 415 555 1234" },
        { row: 2, raw: "12345" },
        { row: 3, raw: "07400 123456" },
    ]);
});

// 2.4 F2 Task 1 — optional number cells follow the same rule as the main number.
test("EP3 CSV tab: an invalid mobile_number cell is a column-named row error and the row is not sent; a valid one is sent as E.164", async () => {
    const sent: string[] = [];
    mock.method(extendedApi, "uploadCSV", async (_id: string, file: File) => { sent.push(await file.text()); return okResponse; });
    render(<SmartCsvImport open campaignId="camp-1" onClose={() => {}} />);
    const text = `Phone,First Name,Mobile\n+447400123456,Ann,07400 123457\n+447400123456,Bob,${BAD_NUMBER}`;
    fireEvent.change(screen.getByTestId("campaign-import-file"), { target: { files: [csvFile(text)] } });
    const rows = await screen.findAllByTestId("campaign-import-row", undefined, { timeout: SLOW });
    assert.equal(within(rows[0]).getAllByRole("cell")[3].textContent, `mobile_number: ${SELECT_COUNTRY_ERROR}`);
    await chooseUnitedKingdom("campaign-import-country");
    await waitFor(() => assert.ok(screen.getByText(`mobile_number: ${INVALID_PHONE_ERROR}`)), { timeout: SLOW });
    const [okRow, badRow] = screen.getAllByTestId("campaign-import-row");
    assert.equal(within(okRow).getAllByRole("cell")[3].textContent, UK_E164);
    assert.equal(within(badRow).getAllByRole("cell")[1].textContent, "3", "original row number");
    fireEvent.click(screen.getByRole("button", { name: /Import 1 contact$/ }));
    await waitFor(() => assert.equal(sent.length, 1), { timeout: SLOW });
    const lines = sent[0].split("\n");
    assert.equal(lines.length, 2);
    assert.equal(lines[1].split(",")[6], "+447400123457");
    assert.ok(!lines[1].split(",").includes(BAD_NUMBER));
});
