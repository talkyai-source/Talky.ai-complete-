/**
 * FX4a Task 3 — in the contact import, a row's phone error is tied to that
 * row's Phone field: the error has an id, the field points to it with
 * aria-describedby and is aria-invalid="true" while the error shows. A row
 * whose number is fine points to nothing.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { INVALID_PHONE_ERROR, SELECT_COUNTRY_ERROR } from "@/lib/phone-number";
import { BAD_NUMBER, UK_NATIONAL, chooseUnitedKingdom, csvFile } from "@/test-utils/phone-fixtures";

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
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    ({ SmartCsvImport } = require("./smart-csv-import"));
} finally {
    loader._load = originalLoad;
}

const SLOW = 15_000;
afterEach(() => { cleanup(); mock.restoreAll(); });

const FILE_TEXT = [
    "Phone,First Name,Last Name,Email",
    `${UK_NATIONAL},Ann,Lee,ann@example.com`,
    `${BAD_NUMBER},Bad,Row,`,
].join("\n");

const phoneField = (row: HTMLElement) => within(row).getByRole("textbox", { name: /^Phone, row / }) as HTMLInputElement;
function describedText(field: HTMLElement) {
    const id = field.getAttribute("aria-describedby");
    return id ? document.getElementById(id)?.textContent ?? null : null;
}

test("each bad row's Phone field points to that row's own error; a good row points to nothing", async () => {
    render(<SmartCsvImport open campaignId="camp-1" onClose={() => {}} />);
    fireEvent.change(screen.getByTestId("campaign-import-file"), { target: { files: [csvFile(FILE_TEXT)] } });
    let rows = await screen.findAllByTestId("campaign-import-row", undefined, { timeout: SLOW });
    assert.equal(rows.length, 2);
    // No country chosen yet: both rows are in error, each with its own sentence.
    assert.equal(phoneField(rows[0]!).getAttribute("aria-invalid"), "true");
    assert.equal(describedText(phoneField(rows[0]!)), SELECT_COUNTRY_ERROR);
    assert.equal(phoneField(rows[1]!).getAttribute("aria-invalid"), "true");
    assert.equal(describedText(phoneField(rows[1]!)) !== null, true);
    assert.equal(phoneField(rows[0]!).getAttribute("aria-describedby") !== phoneField(rows[1]!).getAttribute("aria-describedby"), true, "one id per row");

    await chooseUnitedKingdom("campaign-import-country");
    await waitFor(() => assert.equal(phoneField(screen.getAllByTestId("campaign-import-row")[0]!).getAttribute("aria-invalid"), "false"), { timeout: SLOW });
    rows = screen.getAllByTestId("campaign-import-row");
    assert.equal(phoneField(rows[0]!).getAttribute("aria-describedby") === null, true, "the good row points to nothing");
    assert.equal(phoneField(rows[1]!).getAttribute("aria-invalid"), "true");
    assert.equal(describedText(phoneField(rows[1]!)), INVALID_PHONE_ERROR);
});

test("fixing the number in the field clears the tie", async () => {
    render(<SmartCsvImport open campaignId="camp-1" onClose={() => {}} />);
    fireEvent.change(screen.getByTestId("campaign-import-file"), { target: { files: [csvFile(FILE_TEXT)] } });
    await screen.findAllByTestId("campaign-import-row", undefined, { timeout: SLOW });
    await chooseUnitedKingdom("campaign-import-country");
    await waitFor(() => assert.equal(describedText(phoneField(screen.getAllByTestId("campaign-import-row")[1]!)), INVALID_PHONE_ERROR), { timeout: SLOW });
    fireEvent.change(phoneField(screen.getAllByTestId("campaign-import-row")[1]!), { target: { value: UK_NATIONAL } });
    await waitFor(() => assert.equal(phoneField(screen.getAllByTestId("campaign-import-row")[1]!).getAttribute("aria-invalid"), "false"), { timeout: SLOW });
    assert.equal(phoneField(screen.getAllByTestId("campaign-import-row")[1]!).getAttribute("aria-describedby") === null, true);
});

// FX4a2 Task 2 — every input in a row is named by its column header and the
// row number, the way the Phone input already is.
test("the First, Last and Email inputs of each row are named by their column header and row number", async () => {
    render(<SmartCsvImport open campaignId="camp-1" onClose={() => {}} />);
    fireEvent.change(screen.getByTestId("campaign-import-file"), { target: { files: [csvFile(FILE_TEXT)] } });
    const rows = await screen.findAllByTestId("campaign-import-row", undefined, { timeout: SLOW });
    const headers = screen.getAllByRole("columnheader").map((th) => th.textContent ?? "");
    for (const header of ["Phone", "First", "Last", "Email"]) assert.equal(headers.includes(header), true, `${header} is a shown column header`);
    const values = [["Ann", "Lee", "ann@example.com"], ["Bad", "Row", ""]];
    rows.forEach((row, index) => {
        const inputs = within(row).getAllByRole("textbox") as HTMLInputElement[];
        assert.equal(inputs.length, 4);
        const number = /^Phone, row (\d+)$/.exec(inputs[0]!.getAttribute("aria-label") ?? "")?.[1];
        assert.equal(typeof number === "string", true, "the Phone input carries the row number");
        ["First", "Last", "Email"].forEach((header, column) => {
            const input = within(row).getByRole("textbox", { name: `${header}, row ${number}` }) as HTMLInputElement;
            assert.equal(input.value, values[index]![column]);
        });
    });
    const names = rows.flatMap((row) => (within(row).getAllByRole("textbox") as HTMLInputElement[]).map((input) => input.getAttribute("aria-label")));
    assert.equal(new Set(names).size, 8, "eight inputs, eight different names");
});
