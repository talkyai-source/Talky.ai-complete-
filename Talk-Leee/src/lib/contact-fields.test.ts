import { test } from "node:test";
import assert from "node:assert/strict";
import { OPTIONAL_CONTACT_FIELDS, contactPayload } from "@/lib/contact-fields";

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
