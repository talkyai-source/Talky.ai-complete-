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
        headers: { Phone: "phone_number" }, unmapped: [], total_rows: 1, valid_rows: 1, issues: [], duplicates_in_file: [], sample: [],
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
        headers: { Phone: "phone_number" }, unmapped: [], total_rows: 1, valid_rows: 1, issues: [], duplicates_in_file: [], sample: [],
    }));
    const { container } = render(<CsvImportMapper onConfirm={() => {}} busy />);
    fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [csvFile(`Phone\n${UK_NATIONAL}`)] } });
    const button = await screen.findByRole("button", { name: /Import 1 contact$/ }, { timeout: SLOW }) as HTMLButtonElement;
    assert.equal(button.disabled, true);
});
