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

    assert.equal(payload.phone_number, "+442079460000");
    assert.equal(payload.full_name, "Sian Roberts");
    assert.equal(payload.job_title, "Quantity Surveyor");
    assert.equal(payload.preferred_contact_method, "whatsapp");
    assert.equal(payload.do_not_call, true);
    assert.equal(payload.timezone, "Europe/London");
    assert.equal(payload.calling_notes, "Tender closes Friday");
});

// 2.4: the manual form's phone goes through the shared parser.
test("manual contact payload saves a national number as E.164 with the selected country and refuses an invalid one", () => {
    const uk = contactPayload({ ...EMPTY_CONTACT_FORM, phone_number: "07400 123456", mobile_number: "07400 123457" }, "GB");
    assert.equal(uk.phone_number, "+447400123456");
    assert.equal(uk.mobile_number, "+447400123457");
    assert.equal(uk.business_number, "");
    assert.throws(() => contactPayload({ ...EMPTY_CONTACT_FORM, phone_number: "07400 123456" }), /Select a country for numbers without a \+ country code\./);
    assert.throws(() => contactPayload({ ...EMPTY_CONTACT_FORM, phone_number: "12345" }, "GB"), /Not a valid phone number\./);
});
