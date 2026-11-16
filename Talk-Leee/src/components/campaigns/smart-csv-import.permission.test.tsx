/**
 * Permission to call: the campaign Import contacts dialog (CSV and Paste tabs)
 * sends nothing until the confirmation is complete, then sends it with the
 * request, and shows a backend refusal beside the block.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { extendedApi, type BulkImportResponse } from "@/lib/extended-api";
import { ApiClientError } from "@/lib/http-client";
import { EXPECTED_PERMISSION, fillContactPermission } from "@/test-utils/contact-permission";
import { UK_E164, chooseUnitedKingdom, csvFile } from "@/test-utils/phone-fixtures";

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

const okResponse: BulkImportResponse = { total_rows: 1, imported: 1, failed: 0, duplicates_skipped: 0, errors: [] };

function refusal(detail: Record<string, unknown>) {
    return new ApiClientError({ status: 422, code: String(detail.code), message: String(detail.message), details: detail, url: "u", method: "POST" });
}

async function openPasteWithOneNumber() {
    render(<SmartCsvImport open campaignId="camp-1" onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Paste numbers" }));
    fireEvent.change(screen.getByLabelText("Paste phone numbers"), { target: { value: "07400 123456" } });
    await chooseUnitedKingdom("campaign-import-country");
    await waitFor(() => assert.ok(screen.getByText(UK_E164)), { timeout: SLOW });
}

async function openCsvPreview() {
    render(<SmartCsvImport open campaignId="camp-1" onClose={() => {}} />);
    fireEvent.change(screen.getByTestId("campaign-import-file"), { target: { files: [csvFile("Phone,First Name\n07400 123456,Ann")] } });
    await screen.findAllByTestId("campaign-import-row", undefined, { timeout: SLOW });
    await chooseUnitedKingdom("campaign-import-country");
    await waitFor(() => assert.ok(screen.getByText(UK_E164)), { timeout: SLOW });
}

test("Paste: with the permission block empty nothing is sent and every error shows", async () => {
    const paste = mock.method(extendedApi, "pasteContacts", async () => okResponse);
    await openPasteWithOneNumber();
    assert.equal(screen.queryByText("Tick this box to confirm."), null, "no error before the first attempt");
    fireEvent.click(screen.getByRole("button", { name: /Import 1 contact$/ }));
    assert.equal(paste.mock.callCount(), 0);
    assert.ok(screen.getByText("Choose why you can call them."));
    assert.ok(screen.getByText("Describe where this permission comes from (at least 5 characters)."));
    assert.ok(screen.getByText("Choose the date permission was given."));
    assert.ok(screen.getByText("Tick this box to confirm."));
});

test("Paste: a block missing only the tick is still not sent", async () => {
    const paste = mock.method(extendedApi, "pasteContacts", async () => okResponse);
    await openPasteWithOneNumber();
    fillContactPermission({ skipConfirm: true });
    fireEvent.click(screen.getByRole("button", { name: /Import 1 contact$/ }));
    assert.equal(paste.mock.callCount(), 0);
    assert.ok(screen.getByText("Tick this box to confirm."));
});

test("Paste: a complete block is sent with the request as the permission object", async () => {
    const paste = mock.method(extendedApi, "pasteContacts", async () => okResponse);
    await openPasteWithOneNumber();
    fillContactPermission({ source: "  Website sign-up form, March 2026  " });
    fireEvent.click(screen.getByRole("button", { name: /Import 1 contact$/ }));
    await waitFor(() => assert.equal(paste.mock.callCount(), 1), { timeout: SLOW });
    assert.deepEqual(paste.mock.calls[0].arguments, ["camp-1", UK_E164, EXPECTED_PERMISSION]);
});

test("CSV preview: empty block sends nothing; a complete block sends the file and the four values", async () => {
    const calls: Array<unknown[]> = [];
    mock.method(extendedApi, "uploadCSV", async (...args: unknown[]) => { calls.push(args); return okResponse; });
    await openCsvPreview();
    fireEvent.click(screen.getByRole("button", { name: /Import 1 contact$/ }));
    assert.equal(calls.length, 0);
    assert.ok(screen.getByText("Tick this box to confirm."));
    fillContactPermission();
    fireEvent.click(screen.getByRole("button", { name: /Import 1 contact$/ }));
    await waitFor(() => assert.equal(calls.length, 1), { timeout: SLOW });
    assert.equal(calls[0]![0], "camp-1");
    assert.ok(calls[0]![1] instanceof File);
    assert.deepEqual(calls[0]![2], EXPECTED_PERMISSION);
    assert.equal(calls[0]![3], true);
});

test("a 422 contact_permission_invalid for the date shows its message beside the date field, not as a generic error", async () => {
    mock.method(extendedApi, "pasteContacts", async () => {
        throw refusal({ code: "contact_permission_invalid", field: "permission_date", message: "The date cannot be in the future." });
    });
    await openPasteWithOneNumber();
    fillContactPermission();
    fireEvent.click(screen.getByRole("button", { name: /Import 1 contact$/ }));
    const date = screen.getByLabelText("Date permission was given");
    await waitFor(() => assert.equal(date.getAttribute("aria-invalid"), "true"), { timeout: SLOW });
    const described = (date.getAttribute("aria-describedby") ?? "").split(" ").map((id) => document.getElementById(id)?.textContent);
    assert.ok(described.includes("The date cannot be in the future."));
    assert.equal(document.body.textContent?.includes("Import failed"), false);
});

test("a 422 contact_permission_required shows the backend message in the block", async () => {
    mock.method(extendedApi, "pasteContacts", async () => {
        throw refusal({ code: "contact_permission_required", message: "Confirm you have permission to call these contacts before adding them." });
    });
    await openPasteWithOneNumber();
    fillContactPermission();
    fireEvent.click(screen.getByRole("button", { name: /Import 1 contact$/ }));
    await screen.findByText("Confirm you have permission to call these contacts before adding them.", undefined, { timeout: SLOW });
});

test("closing and reopening the dialog asks again: the new dialog starts empty", async () => {
    const view = render(<SmartCsvImport open campaignId="camp-1" onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Paste numbers" }));
    fillContactPermission();
    assert.equal((screen.getByRole("checkbox") as HTMLInputElement).checked, true);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    view.rerender(<SmartCsvImport open campaignId="camp-1" onClose={() => {}} />);
    assert.equal((screen.getByRole("checkbox") as HTMLInputElement).checked, false);
    assert.equal((screen.getByLabelText("Where this permission comes from") as HTMLInputElement).value, "");
});
