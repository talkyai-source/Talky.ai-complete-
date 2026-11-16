/**
 * What each API function puts on the wire: the confirmation as the contract
 * says (a `permission` object for JSON routes, four form fields for uploads).
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { dashboardApi } from "@/lib/dashboard-api";
import { extendedApi } from "@/lib/extended-api";
import type { ContactPermission } from "@/lib/contact-permission";

const PERMISSION: ContactPermission = {
    basis: "existing_customer",
    source: "Customer list from our billing system",
    permission_date: "2026-02-01",
    statement_version: "2026-11-v1",
};

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

function captureFetch(body: unknown = {}) {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
    }) as typeof fetch;
    return calls;
}

test("uploadCSV sends the four permission form fields next to the file", async () => {
    const calls = captureFetch({ total_rows: 1, imported: 1, failed: 0, duplicates_skipped: 0, errors: [] });
    await extendedApi.uploadCSV("camp-1", new File(["phone\n+447400123456"], "c.csv", { type: "text/csv" }), PERMISSION, true);
    assert.match(calls[0]!.url, /\/contacts\/campaigns\/camp-1\/upload\?skip_duplicates=true$/);
    const form = calls[0]!.init?.body as FormData;
    assert.ok(form instanceof FormData);
    assert.equal(form.get("permission_basis"), "existing_customer");
    assert.equal(form.get("permission_source"), "Customer list from our billing system");
    assert.equal(form.get("permission_date"), "2026-02-01");
    assert.equal(form.get("permission_statement_version"), "2026-11-v1");
    assert.ok(form.get("file") instanceof File);
});

test("pasteContacts sends text and a permission object", async () => {
    const calls = captureFetch({ total_rows: 1, imported: 1, failed: 0, duplicates_skipped: 0, errors: [] });
    await extendedApi.pasteContacts("camp-1", "+447400123456", PERMISSION);
    assert.match(calls[0]!.url, /\/contacts\/campaigns\/camp-1\/paste$/);
    assert.deepEqual(JSON.parse(String(calls[0]!.init?.body)), { text: "+447400123456", permission: PERMISSION });
});

test("addContact sends the contact fields and a permission object", async () => {
    const calls = captureFetch({ message: "ok", contact: { id: "c1" } });
    await dashboardApi.addContact("camp-1", { phone_number: "+447400123456", first_name: "Ann" }, PERMISSION);
    assert.match(calls[0]!.url, /\/campaigns\/camp-1\/contacts$/);
    assert.deepEqual(JSON.parse(String(calls[0]!.init?.body)), { phone_number: "+447400123456", first_name: "Ann", permission: PERMISSION });
});

test("recordContactListPermission POSTs the confirmation to the list", async () => {
    const calls = captureFetch({ id: "list-1", name: "x", is_active: true, contact_count: 1, created_at: null });
    await dashboardApi.recordContactListPermission("list-1", PERMISSION);
    assert.match(calls[0]!.url, /\/contact-lists\/list-1\/permission$/);
    assert.equal(calls[0]!.init?.method, "POST");
    assert.deepEqual(JSON.parse(String(calls[0]!.init?.body)), PERMISSION);
});
