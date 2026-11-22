/**
 * Names in a CSV must reach the upload (20 Nov 2026: "the names that were in
 * the CSV list were not imported"), and the sample file must import as shown.
 */
import assert from "node:assert/strict";
import test from "node:test";

import { parseContactsCsv, semicolonFileMessage } from "@/lib/contact-csv";
import { buildContactsUpload, parseRowNumbers } from "@/lib/contact-upload-csv";
import { SAMPLE_CSV_HEADERS, SAMPLE_CSV_ROWS, buildSampleCsv } from "@/lib/contact-sample-csv";

const ROWS = [["Alex Example", "+442079460123"], ["Sam Sample", "+442079460124"]];

for (const header of ["Name,Phone", "name,phone", "NAME,PHONE", "Customer,Number", "Client Name,Phone", " Name , Phone ", "\uFEFFName,Phone"]) {
    test(`a "${header.replace("\uFEFF", "<BOM>")}" file keeps each person's name, split into first and last`, () => {
        const text = [header, ...ROWS.map((r) => r.join(","))].join("\n");
        const { rows, phoneFound } = parseContactsCsv(text);
        assert.equal(phoneFound, true);
        assert.deepEqual(rows.map((r) => [r.first_name, r.last_name]), [["Alex", "Example"], ["Sam", "Sample"]]);
    });
}

test("a Company Name column is not mistaken for the person's name", () => {
    const { rows } = parseContactsCsv("Company Name,Phone\nBuildWright,+442079460123\n");
    assert.equal(rows[0].first_name, "");
    assert.equal(rows[0].company, "BuildWright");
});

test("a column we do not recognise travels to the upload under its own heading", () => {
    const { rows } = parseContactsCsv("Name,Phone,Favourite colour\nAlex Example,+442079460123,Blue\n");
    const numbers = parseRowNumbers(rows[0], "GB");
    assert.ok(numbers.ok);
    const { csv } = buildContactsUpload([{ numbers: numbers.ok ? numbers.numbers : (null as never), row: rows[0] }]);
    const [head, line] = csv.split("\n");
    assert.ok(head.endsWith(",Favourite colour"), head);
    assert.ok(line.endsWith(",Blue"), line);
});

test("a semicolon-separated file gets a clear message", () => {
    const { headers, phoneFound } = parseContactsCsv("Name;Phone\nAlex Example;+442079460123\n");
    assert.equal(phoneFound, false);
    assert.match(semicolonFileMessage(headers) ?? "", /semicolons/);
    assert.equal(semicolonFileMessage(["Name", "Phone"]), null);
});

test("the sample file, uploaded unchanged, imports names, numbers and every optional column as shown", () => {
    const { rows, phoneFound } = parseContactsCsv(buildSampleCsv());
    assert.equal(phoneFound, true);
    assert.equal(rows.length, SAMPLE_CSV_ROWS.length);
    const cols = [...SAMPLE_CSV_HEADERS] as string[];
    const cell = (i: number, key: string) => SAMPLE_CSV_ROWS[i][cols.indexOf(key)];
    rows.forEach((row, i) => {
        const numbers = parseRowNumbers(row, "GB");
        assert.ok(numbers.ok, `row ${i + 1} numbers must be accepted`);
        if (!numbers.ok) return;
        assert.equal(numbers.numbers.e164, cell(i, "phone_number"));
        assert.equal(numbers.numbers.mobile_e164, cell(i, "mobile_number"));
        assert.equal(numbers.numbers.business_e164, cell(i, "business_number"));
        assert.equal(row.first_name, cell(i, "first_name"));
        assert.equal(row.last_name, cell(i, "last_name"));
        assert.equal(row.email, cell(i, "email"));
        assert.equal(row.company, cell(i, "company_name"));
        assert.equal(row.job_title, cell(i, "job_title"));
        assert.equal(row.best_time_to_call, cell(i, "best_time_to_call"));
        assert.equal(row.timezone, cell(i, "timezone"));
        assert.equal(row.calling_notes, cell(i, "calling_notes"));
        assert.equal(row.preferred_contact_method, cell(i, "preferred_contact_method"));
        assert.equal(row.do_not_call, cell(i, "do_not_call"));
        assert.equal(row.extras, undefined, "no sample column may be left unread");
    });
});

test("a Forename,Surname file keeps both names (the server already read Forename; this reader did not)", () => {
    const { rows } = parseContactsCsv("Forename,Surname,Phone\nAlex,Example,+442079460123");
    assert.deepEqual(rows.map((r) => [r.first_name, r.last_name]), [["Alex", "Example"]]);
});
