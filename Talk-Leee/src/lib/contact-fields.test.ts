import { test } from "node:test";
import assert from "node:assert/strict";
import { OPTIONAL_CONTACT_FIELDS, contactPayload, contactEditPayload, fetchAllPages } from "@/lib/contact-fields";

test("contactPayload drops empty optional strings and trims the rest", () => {
    assert.deepEqual(
        contactPayload({ phone_number: " +1555 ", first_name: "", last_name: " Lee ", email: "", company_name: "ACME", calling_notes: "  " }),
        { phone_number: "+1555", last_name: "Lee", company_name: "ACME" }
    );
});

test("every optional field offered by the dropdown is a ContactMutation key the API accepts", () => {
    const accepted = new Set([
        "mobile_number", "business_number", "company_name", "job_title", "best_time_to_call",
        "timezone", "calling_notes", "preferred_contact_method",
    ]);
    for (const f of OPTIONAL_CONTACT_FIELDS) assert.ok(accepted.has(f.key), f.key);
    assert.equal(new Set(OPTIONAL_CONTACT_FIELDS.map((f) => f.key)).size, OPTIONAL_CONTACT_FIELDS.length);
});

test("contactEditPayload sends cleared optional fields explicitly so an edit can clear them", () => {
    const out = contactEditPayload({
        phone_number: " +1555 ", first_name: " Ann ", last_name: "", email: "", timezone: "  ",
        mobile_number: "", company_name: "ACME", do_not_call: false,
    }) as unknown as Record<string, unknown>;
    assert.deepEqual(out, {
        phone_number: "+1555", first_name: "Ann", last_name: "", email: null, timezone: null,
        mobile_number: "", company_name: "ACME", do_not_call: false,
    });
});

test("fetchAllPages loads every page up to total, not just the first", async () => {
    const all = Array.from({ length: 230 }, (_, i) => i);
    const calls: number[] = [];
    const res = await fetchAllPages(async (page, size) => {
        calls.push(page);
        return { items: all.slice((page - 1) * size, page * size), total: all.length };
    });
    assert.equal(res.items.length, 230);
    assert.equal(res.total, 230);
    assert.deepEqual(calls, [1, 2, 3]);
});

test("fetchAllPages stops on an empty page and honours the page cap", async () => {
    let n = 0;
    const capped = await fetchAllPages(async () => ({ items: [n++], total: 1000 }), 1, 3);
    assert.equal(capped.items.length, 3);
    const empty = await fetchAllPages(async (page) => ({ items: page === 1 ? [1] : [], total: 5 }), 1);
    assert.equal(empty.items.length, 1);
});
