import assert from "node:assert/strict";
import test from "node:test";

import { parseCsvGridWithLines, parseContactsCsv } from "@/lib/contact-csv";

test("every parsed row carries the line number of the user's original file", () => {
    const text = [
        "Export from CRM", // line 1: preamble
        "", // line 2: blank
        "Full Name,Phone", // line 3: header
        "Ann Lee,+447400123456", // line 4
        "", // line 5: blank, dropped
        "\"Bob\nSmith\",12345", // lines 6-7: quoted newline
        "Cy Day,+12025550123", // line 8
    ].join("\n");
    const { headerRow, rows } = parseContactsCsv(text);
    assert.equal(headerRow, 1, "the header is the second non-blank row");
    assert.deepEqual(rows.map((r) => [r.phone, r.source_row]), [
        ["+447400123456", 4],
        ["12345", 6],
        ["+12025550123", 8],
    ]);
});

test("parseCsvGridWithLines keeps CRLF files on the same line numbers as LF files", () => {
    const grid = parseCsvGridWithLines("a,b\r\n1,2\r\n\r\n3,4\r\n");
    assert.deepEqual(grid.map((r) => r.line), [1, 2, 4]);
});
