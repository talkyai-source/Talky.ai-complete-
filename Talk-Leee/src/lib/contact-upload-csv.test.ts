import assert from "node:assert/strict";
import test from "node:test";

import { parseContactsCsv } from "@/lib/contact-csv";
import { CONTACT_UPLOAD_COLUMNS, UPLOAD_FIRST_DATA_ROW, buildContactsUpload, parseRowNumbers, type RowNumbers } from "@/lib/contact-upload-csv";
import { INVALID_PHONE_ERROR, SELECT_COUNTRY_ERROR } from "@/lib/phone-number";

test("the shared upload CSV carries every column the backend upload reads, in one fixed order", () => {
    assert.deepEqual([...CONTACT_UPLOAD_COLUMNS], [
        "phone_number", "full_name", "first_name", "last_name", "email",
        "company_name", "mobile_number", "business_number", "job_title",
        "best_time_to_call", "timezone", "calling_notes",
        "preferred_contact_method", "do_not_call",
    ]);
});

function accepted(text: string, country: "GB" | "US" | "") {
    return parseContactsCsv(text).rows
        .map((row) => ({ row, check: parseRowNumbers(row, country) }))
        .filter((r): r is { row: typeof r.row; check: { ok: true; numbers: RowNumbers } } => r.check.ok)
        .map((r) => ({ numbers: r.check.numbers, row: r.row }));
}

test("the upload CSV writes the parser's E.164 in phone_number and maps backend rows to original file rows", () => {
    const text = [
        "Phone,First Name,Last Name,Email,Company,Mobile",
        "", // blank line 2 is skipped by the parser
        "07400 123456,Ann,Lee,ann@example.com,\"Acme, Ltd\",07400 123457",
        "12345,Bad,Row,,,",
        "+12025550123,Bob,,,,",
    ].join("\n");
    const { csv, rowMap } = buildContactsUpload(accepted(text, "GB"));
    const lines = csv.split("\n");
    assert.equal(lines[0], CONTACT_UPLOAD_COLUMNS.join(","));
    assert.equal(lines.length, 3, "only the two valid rows are written");
    assert.equal(lines[1], '+447400123456,Ann Lee,Ann,Lee,ann@example.com,"Acme, Ltd",+447400123457,,,,,,,');
    assert.equal(lines[2], "+12025550123,Bob,Bob,,,,,,,,,,,");
    assert.ok(lines.every((l) => !l.startsWith("12345")), "an invalid number never reaches the upload");
    // Backend row 2 = first data line of the upload = original file line 3.
    assert.equal(rowMap.get(UPLOAD_FIRST_DATA_ROW), 3);
    assert.equal(rowMap.get(UPLOAD_FIRST_DATA_ROW + 1), 5);
});

// 2.4 F2 Task 1: optional number cells follow the same rule as the main number.
test("an optional number cell is E.164 when valid, empty when empty, and a column-named row error when invalid", () => {
    const text = [
        "Phone,First Name,Mobile,Business Number",
        "+447400123456,Ann,07400 123457,", // line 2: valid mobile, empty business
        "+447400123456,Bob,12345,", // line 3: invalid mobile
        "+447400123456,Cy,,07700 900123", // line 4: invalid business (drama range)
        "+447400123456,Di,07400 123458,", // line 5: national mobile, needs the country
    ].join("\n");
    const { rows } = parseContactsCsv(text);
    const withUk = rows.map((row) => parseRowNumbers(row, "GB"));
    assert.deepEqual(withUk[0], { ok: true, numbers: { e164: "+447400123456", mobile_e164: "+447400123457", business_e164: "" } });
    assert.deepEqual(withUk[1], { ok: false, error: `mobile_number: ${INVALID_PHONE_ERROR}` });
    assert.deepEqual(withUk[2], { ok: false, error: `business_number: ${INVALID_PHONE_ERROR}` });
    assert.deepEqual(withUk[3], { ok: true, numbers: { e164: "+447400123456", mobile_e164: "+447400123458", business_e164: "" } });
    // Without a country the optional national number is the select-country error, named by column.
    assert.deepEqual(parseRowNumbers(rows[3], ""), { ok: false, error: `mobile_number: ${SELECT_COUNTRY_ERROR}` });
    // The main phone's error wins over an optional cell's.
    assert.deepEqual(parseRowNumbers({ ...rows[1], phone: "12345" }, "GB"), { ok: false, error: INVALID_PHONE_ERROR });

    const { csv } = buildContactsUpload(accepted(text, "GB"));
    const lines = csv.split("\n");
    assert.equal(lines.length, 3, "rows with an invalid optional cell are never sent");
    assert.equal(lines[1].split(",")[6], "+447400123457");
    assert.equal(lines[2].split(",")[6], "+447400123458");
    for (const line of lines.slice(1)) {
        const [, , , , , , mobile, business] = line.split(",");
        assert.ok(/^(\+\d+)?$/.test(mobile) && /^(\+\d+)?$/.test(business), `optional cells are E.164 or empty: ${line}`);
    }
});
