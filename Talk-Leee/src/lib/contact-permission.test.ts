import assert from "node:assert/strict";
import { test } from "node:test";

import {
    CONTACT_PERMISSION_STATEMENT_VERSION,
    CONTACT_PERMISSION_BASES,
    buildContactPermission,
    contactPermissionBasisLabel,
    contactPermissionErrors,
    contactPermissionServerError,
    emptyContactPermissionDraft,
    type ContactPermissionDraft,
} from "@/lib/contact-permission";
import { ApiClientError } from "@/lib/http-client";

const TODAY = "2026-11-16";

function valid(overrides: Partial<ContactPermissionDraft> = {}): ContactPermissionDraft {
    return {
        basis: "express_consent",
        source: "Website sign-up form, March 2026",
        permission_date: "2026-03-14",
        confirmed: true,
        ...overrides,
    };
}

test("the statement version is the backend constant", () => {
    assert.equal(CONTACT_PERMISSION_STATEMENT_VERSION, "2026-11-v1");
});

test("an empty draft has an error on every part", () => {
    const errors = contactPermissionErrors(emptyContactPermissionDraft(), TODAY);
    assert.deepEqual(Object.keys(errors).sort(), ["basis", "confirmed", "permission_date", "source"]);
    assert.equal(errors.basis, "Choose why you can call them.");
    assert.equal(errors.source, "Describe where this permission comes from (at least 5 characters).");
    assert.equal(errors.permission_date, "Choose the date permission was given.");
    assert.equal(errors.confirmed, "Tick this box to confirm.");
});

test("a complete draft has no errors", () => {
    assert.deepEqual(contactPermissionErrors(valid(), TODAY), {});
});

test("the four bases carry the exact labels", () => {
    assert.deepEqual(
        CONTACT_PERMISSION_BASES.map((b) => [b.value, b.label]),
        [
            ["express_consent", "They agreed to be called"],
            ["existing_customer", "They are existing customers"],
            ["made_an_enquiry", "They asked us to contact them"],
            ["other_documented", "Another documented reason"],
        ],
    );
    assert.equal(contactPermissionBasisLabel("existing_customer"), "They are existing customers");
    assert.equal(contactPermissionBasisLabel("something_new"), "something_new");
});

test("source: 4 characters refused, 5 accepted, 500 accepted, 501 refused; trimmed before counting", () => {
    assert.equal(contactPermissionErrors(valid({ source: "abcd" }), TODAY).source, "Describe where this permission comes from (at least 5 characters).");
    assert.equal(contactPermissionErrors(valid({ source: "abcde" }), TODAY).source, undefined);
    assert.equal(contactPermissionErrors(valid({ source: "a".repeat(500) }), TODAY).source, undefined);
    assert.equal(contactPermissionErrors(valid({ source: "a".repeat(501) }), TODAY).source, "Keep this to 500 characters or fewer.");
    assert.equal(contactPermissionErrors(valid({ source: "  ab  " }), TODAY).source, "Describe where this permission comes from (at least 5 characters).");
    assert.equal(contactPermissionErrors(valid({ source: `  ${"a".repeat(500)}  ` }), TODAY).source, undefined);
});

test("date: today accepted, tomorrow refused, garbage and impossible dates refused", () => {
    assert.equal(contactPermissionErrors(valid({ permission_date: TODAY }), TODAY).permission_date, undefined);
    assert.equal(contactPermissionErrors(valid({ permission_date: "2026-11-15" }), TODAY).permission_date, undefined);
    assert.equal(contactPermissionErrors(valid({ permission_date: "2026-11-17" }), TODAY).permission_date, "This date cannot be in the future.");
    assert.equal(contactPermissionErrors(valid({ permission_date: "" }), TODAY).permission_date, "Choose the date permission was given.");
    assert.equal(contactPermissionErrors(valid({ permission_date: "14/03/2026" }), TODAY).permission_date, "Choose the date permission was given.");
    assert.equal(contactPermissionErrors(valid({ permission_date: "2026-02-30" }), TODAY).permission_date, "Choose the date permission was given.");
});

test("date: with no 'today' given, tomorrow in UTC is refused and today in UTC is accepted", () => {
    const now = new Date();
    const todayUtc = now.toISOString().slice(0, 10);
    const tomorrow = new Date(now.getTime() + 24 * 3600 * 1000).toISOString().slice(0, 10);
    assert.equal(contactPermissionErrors(valid({ permission_date: todayUtc })).permission_date, undefined);
    assert.equal(contactPermissionErrors(valid({ permission_date: tomorrow })).permission_date, "This date cannot be in the future.");
});

test("the confirmation box is required", () => {
    assert.equal(contactPermissionErrors(valid({ confirmed: false }), TODAY).confirmed, "Tick this box to confirm.");
});

test("buildContactPermission returns the exact contract object with a trimmed source", () => {
    assert.deepEqual(buildContactPermission(valid({ source: "  Website sign-up form, March 2026  " }), TODAY), {
        basis: "express_consent",
        source: "Website sign-up form, March 2026",
        permission_date: "2026-03-14",
        statement_version: "2026-11-v1",
    });
});

test("buildContactPermission returns null for any invalid draft", () => {
    assert.equal(buildContactPermission(emptyContactPermissionDraft(), TODAY), null);
    assert.equal(buildContactPermission(valid({ confirmed: false }), TODAY), null);
    assert.equal(buildContactPermission(valid({ permission_date: "2026-11-17" }), TODAY), null);
    assert.equal(buildContactPermission(valid({ source: "abc" }), TODAY), null);
    assert.equal(buildContactPermission(valid({ basis: "" }), TODAY), null);
});

function apiError(detail: Record<string, unknown>): ApiClientError {
    // The shape http-client builds from {"detail": {...}} with HTTP 422.
    return new ApiClientError({
        status: 422,
        code: String(detail.code),
        message: String(detail.message),
        details: detail,
        url: "http://x/api/v1/contacts",
        method: "POST",
    });
}

test("a 422 contact_permission_invalid becomes an error for the field the backend names", () => {
    const err = apiError({ code: "contact_permission_invalid", field: "permission_date", message: "The date cannot be in the future." });
    assert.deepEqual(contactPermissionServerError(err), { field: "permission_date", message: "The date cannot be in the future." });
});

test("a 422 contact_permission_required becomes a general error with the backend message", () => {
    const err = apiError({ code: "contact_permission_required", message: "Confirm you have permission to call these contacts before adding them." });
    assert.deepEqual(contactPermissionServerError(err), {
        field: null,
        message: "Confirm you have permission to call these contacts before adding them.",
    });
});

test("the envelope shape is understood too, and an unknown field becomes a general error", () => {
    const err = new ApiClientError({
        status: 422,
        code: "contact_permission_invalid",
        message: "Bad source.",
        details: { detail: { code: "contact_permission_invalid", field: "nonsense", message: "Bad source." } },
        url: "u",
        method: "POST",
    });
    assert.deepEqual(contactPermissionServerError(err), { field: null, message: "Bad source." });
});

test("other errors are not permission errors", () => {
    assert.equal(contactPermissionServerError(new Error("boom")), null);
    assert.equal(contactPermissionServerError(apiError({ code: "something_else", message: "x" })), null);
    assert.equal(contactPermissionServerError(null), null);
});
