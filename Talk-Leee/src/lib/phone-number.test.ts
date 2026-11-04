import assert from "node:assert/strict";
import test from "node:test";

import {
    COUNTRY_SELECT_PLACEHOLDER,
    INVALID_PHONE_ERROR,
    PHONE_HELP_TEXT,
    SELECT_COUNTRY_ERROR,
    countryOptions,
    parsePhoneNumber,
    savesAsText,
} from "@/lib/phone-number";

// 2.4 Task 8.1 — shared module cases. The UK fixture is 07400 123456:
// libphonenumber-js 1.13.15 reports Ofcom's reserved drama range 07700 900xxx
// as NOT valid (both min and max metadata), and this module only returns an
// E.164 value when the library reports the number as valid (Task 1). The
// drama-range verdict is pinned by its own test below.
test("a UK national number with United Kingdom selected saves as E.164", () => {
    assert.deepEqual(parsePhoneNumber("07400 123456", "GB"), { ok: true, e164: "+447400123456" });
});

test("a national number with no country selected is the select-country error, never a guess", () => {
    assert.deepEqual(parsePhoneNumber("07400 123456", ""), { ok: false, error: SELECT_COUNTRY_ERROR });
});

test("a + number parses without any country", () => {
    assert.deepEqual(parsePhoneNumber("+447400123456", ""), { ok: true, e164: "+447400123456" });
});

test("12345 is rejected with any country", () => {
    for (const country of ["GB", "US", "DE"] as const) {
        assert.deepEqual(parsePhoneNumber("12345", country), { ok: false, error: INVALID_PHONE_ERROR }, country);
    }
});

test("a US national number with United States selected saves as E.164", () => {
    assert.deepEqual(parsePhoneNumber("202-555-0123", "US"), { ok: true, e164: "+12025550123" });
});

test("libphonenumber-js marks the 07700 900xxx drama range invalid, so the module rejects it", () => {
    assert.deepEqual(parsePhoneNumber("07700 900123", "GB"), { ok: false, error: INVALID_PHONE_ERROR });
    assert.deepEqual(parsePhoneNumber("+447700900123", ""), { ok: false, error: INVALID_PHONE_ERROR });
});

test("the selected country never changes a + number", () => {
    assert.deepEqual(parsePhoneNumber("+12025550123", "GB"), { ok: true, e164: "+12025550123" });
});

test("short numbers and 4–5 digit extensions are rejected, nothing adds +1 or +", () => {
    assert.equal(parsePhoneNumber("1234", "US").ok, false);
    assert.equal(parsePhoneNumber("4155551234", "").ok, false, "a 10-digit US number without + needs a country");
    assert.deepEqual(parsePhoneNumber("+1 555 0123", ""), { ok: false, error: INVALID_PHONE_ERROR });
    assert.deepEqual(parsePhoneNumber("", "GB"), { ok: false, error: INVALID_PHONE_ERROR });
});

test("only spaces, dashes, dots and brackets are ignored; other characters make the number invalid", () => {
    assert.deepEqual(parsePhoneNumber("(020) 7946.0958", "GB"), { ok: true, e164: "+442079460958" });
    assert.deepEqual(parsePhoneNumber("+44 (0) 7400-123-456", ""), { ok: true, e164: "+447400123456" });
    assert.deepEqual(parsePhoneNumber("1-800-FLOWERS", "US"), { ok: false, error: INVALID_PHONE_ERROR });
    assert.deepEqual(parsePhoneNumber("call 2025550123", "US"), { ok: false, error: INVALID_PHONE_ERROR });
    assert.deepEqual(parsePhoneNumber("+44 7400 123456 x12", ""), { ok: false, error: INVALID_PHONE_ERROR });
});

test("the error, help and Saves-as texts are exact", () => {
    assert.equal(SELECT_COUNTRY_ERROR, "Select a country for numbers without a + country code.");
    assert.equal(INVALID_PHONE_ERROR, "Not a valid phone number.");
    assert.equal(
        PHONE_HELP_TEXT,
        "Numbers starting with + are used as written. For numbers without a country code, select the country first.",
    );
    assert.equal(savesAsText("+447400123456"), "Saves as +447400123456");
    assert.equal(COUNTRY_SELECT_PLACEHOLDER, "Select country");
});

test("country options cover every libphonenumber-js country with the English region name and calling code", () => {
    const options = countryOptions();
    assert.ok(options.length >= 240, `only ${options.length} countries`);
    assert.ok(options.some((o) => o.code === "GB" && o.label === "United Kingdom (+44)"));
    assert.ok(options.some((o) => o.code === "US" && o.label === "United States (+1)"));
    assert.ok(options.every((o) => /^.+ \(\+\d+\)$/.test(o.label)), "every label is 'Name (+code)'");
    assert.equal(new Set(options.map((o) => o.code)).size, options.length, "no duplicate codes");
    const labels = options.map((o) => o.label);
    assert.deepEqual(labels, [...labels].sort((a, b) => a.localeCompare(b, "en")), "sorted by name");
});
