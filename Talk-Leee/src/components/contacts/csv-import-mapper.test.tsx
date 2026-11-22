/**
 * 2.4 — EP2, the "Check the file first" mapper. Its Import button hands the
 * file AND the chosen default country to the page, which sends it through the
 * same upload path as the upload card (see src/app/contacts/page.test.tsx).
 */
import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { CsvImportMapper } from "@/components/contacts/csv-import-mapper";
import { leadDetailsApi } from "@/lib/lead-details-api";
import { COUNTRY_SELECT_PLACEHOLDER, PHONE_HELP_TEXT } from "@/lib/phone-number";
import { UK_NATIONAL, chooseUnitedKingdom, csvFile } from "@/test-utils/phone-fixtures";

const SLOW = 15_000;
afterEach(() => { cleanup(); mock.restoreAll(); });

test("the mapper starts with no country, shows the help text, and Import passes the file with the selected country", async () => {
    mock.method(leadDetailsApi, "previewImport", async () => ({
        headers: { Phone: "phone_number" }, dial_column: "Phone", unmapped: [], total_rows: 1, valid_rows: 1, issues: [], duplicates_in_file: [], sample: [],
    }));
    const confirmed: Array<[File, string]> = [];
    const { container } = render(<CsvImportMapper onConfirm={(file, country) => confirmed.push([file, country])} />);
    assert.equal(document.getElementById("contacts-mapper-country")?.textContent, COUNTRY_SELECT_PLACEHOLDER);
    assert.ok(screen.getByText(PHONE_HELP_TEXT));

    const file = csvFile(`Phone\n${UK_NATIONAL}`);
    fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [file] } });
    const button = await screen.findByRole("button", { name: /Import 1 contact$/ }, { timeout: SLOW });
    await chooseUnitedKingdom("contacts-mapper-country");
    fireEvent.click(button);
    assert.equal(confirmed.length, 1);
    assert.equal(confirmed[0][0], file);
    assert.equal(confirmed[0][1], "GB");
});

test("while the page is importing, the mapper's Import button is disabled", async () => {
    mock.method(leadDetailsApi, "previewImport", async () => ({
        headers: { Phone: "phone_number" }, dial_column: "Phone", unmapped: [], total_rows: 1, valid_rows: 1, issues: [], duplicates_in_file: [], sample: [],
    }));
    const { container } = render(<CsvImportMapper onConfirm={() => {}} busy />);
    fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [csvFile(`Phone\n${UK_NATIONAL}`)] } });
    const button = await screen.findByRole("button", { name: /Import 1 contact$/ }, { timeout: SLOW }) as HTMLButtonElement;
    assert.equal(button.disabled, true);
});

const previewOf = (headers: Record<string, string | null>) => ({
    headers, dial_column: Object.entries(headers).find(([, k]) => k === "phone_number" || k === "mobile_number")?.[0] ?? null, unmapped: [], total_rows: 1, valid_rows: 1, issues: [], duplicates_in_file: [], sample: [],
});

test("a Name column is shown as the person's name, in plain words", async () => {
    mock.method(leadDetailsApi, "previewImport", async () => previewOf({ Name: "full_name", Phone: "phone_number" }));
    const { container } = render(<CsvImportMapper onConfirm={() => {}} />);
    fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [csvFile("Name,Phone\nAlex Example,07400 123456")] } });
    await screen.findByText(/as each person.s name/, {}, { timeout: SLOW });
    assert.ok(screen.getByText(/\u201CName\u201D/, { exact: false }));
});

test("a file with no name column warns that calls will start without a name", async () => {
    mock.method(leadDetailsApi, "previewImport", async () => previewOf({ Phone: "phone_number" }));
    const { container } = render(<CsvImportMapper onConfirm={() => {}} />);
    fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [csvFile(`Phone\n${UK_NATIONAL}`)] } });
    await screen.findByText(/No column looks like a name/, {}, { timeout: SLOW });
});

test("the sample file button downloads a CSV built in the browser, with its plain-words note beside it", async () => {
    const blobs: Blob[] = [];
    mock.method(URL, "createObjectURL", (b: Blob) => { blobs.push(b); return "blob:sample"; });
    mock.method(URL, "revokeObjectURL", () => {});
    render(<CsvImportMapper onConfirm={() => {}} />);
    assert.ok(screen.getByText(/Only the phone number is needed/));
    fireEvent.click(screen.getByRole("button", { name: /Download a sample file/ }));
    assert.equal(blobs.length, 1);
    const text = await blobs[0].text();
    assert.match(text, /^first_name,last_name,phone_number,/);
    assert.match(text, /Alex,Example,\+442079460123/);
});

test("a Name,Mobile file enables Import and names the column it will dial", async () => {
    mock.method(leadDetailsApi, "previewImport", async () => ({
        ...previewOf({ Name: "full_name", Mobile: "mobile_number" }), dial_column: "Mobile",
    }));
    const { container } = render(<CsvImportMapper onConfirm={() => {}} />);
    fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [csvFile(`Name,Mobile
Alex Example,${UK_NATIONAL}`)] } });
    const button = await screen.findByRole("button", { name: /Import 1 contact$/ }, { timeout: SLOW }) as HTMLButtonElement;
    assert.equal(button.disabled, false);
    assert.ok(screen.getByText(/We.ll dial the “Mobile” column/));
    assert.equal(screen.queryByText(/No column maps to a phone number/), null);
});

test("with a server from before the change (no dial column sent, Name not mapped) Import still works and the name is still named", async () => {
    // The website goes live on merge, the server on its next release.
    mock.method(leadDetailsApi, "previewImport", async () => ({
        headers: { Name: null, Phone: "phone_number" }, unmapped: ["Name"], total_rows: 1, valid_rows: 1, issues: [], duplicates_in_file: [], sample: [],
    }));
    const { container } = render(<CsvImportMapper onConfirm={() => {}} />);
    fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [csvFile(`Name,Phone\nAlex Example,${UK_NATIONAL}`)] } });
    const button = await screen.findByRole("button", { name: /Import 1 contact$/ }, { timeout: SLOW }) as HTMLButtonElement;
    assert.equal(button.disabled, false);
    assert.ok(screen.getByText(/We.ll dial the “Phone” column/));
    assert.ok(screen.getByText(/as each person.s name/));
    assert.equal(screen.queryByText(/No column looks like a name/), null);
});

test("the name note follows the reader that does the import: Forename and Surname are both named", async () => {
    mock.method(leadDetailsApi, "previewImport", async () => previewOf({ Forename: "first_name", Surname: "last_name", Phone: "phone_number" }));
    const { container } = render(<CsvImportMapper onConfirm={() => {}} />);
    fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [csvFile(`Forename,Surname,Phone\nAlex,Example,${UK_NATIONAL}`)] } });
    await screen.findByText(/as each person.s name/, {}, { timeout: SLOW });
    assert.ok(screen.getByText(/“Forename” and “Surname”/, { exact: false }));
});
