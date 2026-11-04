import { test } from "node:test";
import assert from "node:assert/strict";
import { OPTIONAL_CONTACT_FIELDS, contactPayload, contactEditPayload, fetchAllPages } from "@/lib/contact-fields";

// 2.4: phone_number is the shared parser's E.164 (a trim-only " +1555 " is
// no longer accepted — it is not a valid number).
test("contactPayload drops empty optional strings, trims the rest and sends the phone as E.164", () => {
    assert.deepEqual(
        contactPayload({ phone_number: " +1 202 555 0123 ", first_name: "", last_name: " Lee ", email: "", company_name: "ACME", calling_notes: "  " }),
        { phone_number: "+12025550123", last_name: "Lee", company_name: "ACME" }
    );
});

test("contactPayload turns a national number into E.164 with the selected country and refuses an invalid one", () => {
    assert.equal(contactPayload({ phone_number: "07400 123456" }, "GB").phone_number, "+447400123456");
    assert.equal(contactPayload({ phone_number: "+447400123456", mobile_number: "07400 123457" }, "GB").mobile_number, "+447400123457");
    assert.throws(() => contactPayload({ phone_number: "07400 123456" }, ""), /Select a country for numbers without a \+ country code\./);
    assert.throws(() => contactPayload({ phone_number: "12345" }, "GB"), /Not a valid phone number\./);
    assert.throws(() => contactPayload({ phone_number: "+447400123456", mobile_number: "12345" }, "GB"), /Not a valid phone number\./);
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
    // 2.4: the phone fixture is a valid number because the payload now sends
    // the shared parser's E.164, not a trimmed string.
    const out = contactEditPayload({
        phone_number: " +1 202 555 0123 ", first_name: " Ann ", last_name: "", email: "", timezone: "  ",
        mobile_number: "", company_name: "ACME", do_not_call: false,
    }) as unknown as Record<string, unknown>;
    assert.deepEqual(out, {
        phone_number: "+12025550123", first_name: "Ann", last_name: "", email: null, timezone: null,
        mobile_number: "", company_name: "ACME", do_not_call: false,
    });
    assert.throws(() => contactEditPayload({ phone_number: "12345" }, "GB"), /Not a valid phone number\./);
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
