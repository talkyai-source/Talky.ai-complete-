import assert from "node:assert/strict";
import test from "node:test";

import { contactPayload, EMPTY_CONTACT_FORM } from "@/lib/contact-form";

test("manual contact payload carries every operational contact field", () => {
    const payload = contactPayload({
        ...EMPTY_CONTACT_FORM,
        phone_number: " +442079460000 ",
        first_name: " Sian ",
        last_name: " Roberts ",
        company_name: " BuildWright ",
        job_title: " Quantity Surveyor ",
        best_time_to_call: " Mon-Fri 9-11 ",
        timezone: " Europe/London ",
        calling_notes: " Tender closes Friday ",
        preferred_contact_method: "whatsapp",
        do_not_call: true,
    });

    assert.equal(payload.full_name, "Sian Roberts");
    assert.equal(payload.job_title, "Quantity Surveyor");
    assert.equal(payload.preferred_contact_method, "whatsapp");
    assert.equal(payload.do_not_call, true);
    assert.equal(payload.timezone, "Europe/London");
    assert.equal(payload.calling_notes, "Tender closes Friday");
});
