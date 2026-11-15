import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React, { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { ContactPermissionFields } from "@/components/contacts/contact-permission-fields";
import {
    CONTACT_PERMISSION_TEXT,
    buildContactPermission,
    emptyContactPermissionDraft,
    todayUtc,
    type ContactPermissionDraft,
    type ContactPermissionServerError,
} from "@/lib/contact-permission";
import { fillContactPermission } from "@/test-utils/contact-permission";

afterEach(() => cleanup());

let latest: ContactPermissionDraft = emptyContactPermissionDraft();

function Harness({ showErrors = false, serverError = null }: { showErrors?: boolean; serverError?: ContactPermissionServerError | null }) {
    const [draft, setDraft] = useState(emptyContactPermissionDraft());
    return <ContactPermissionFields idPrefix="t" value={draft} onChange={(next) => { latest = next; setDraft(next); }} showErrors={showErrors} serverError={serverError} />;
}

test("shows the heading, intro and four labelled controls with the exact wording", () => {
    render(<Harness />);
    assert.ok(screen.getByText("Permission to call these contacts"));
    assert.ok(screen.getByText("We can only call people you are allowed to contact. Tell us where that permission comes from. We keep this with the list."));
    const group = screen.getByRole("radiogroup", { name: "Why you can call them" });
    assert.deepEqual(
        Array.from(group.querySelectorAll("input[type=radio]")).map((r) => document.querySelector(`label[for="${r.id}"]`)?.textContent),
        ["They agreed to be called", "They are existing customers", "They asked us to contact them", "Another documented reason"],
    );
    const source = screen.getByLabelText("Where this permission comes from") as HTMLInputElement;
    assert.equal(source.placeholder, "For example: website sign-up form, March 2026");
    const date = screen.getByLabelText("Date permission was given") as HTMLInputElement;
    assert.equal(date.type, "date");
    assert.equal(date.max, todayUtc());
    assert.ok(screen.getByText("If it varies, use the most recent date."));
    assert.ok(screen.getByRole("checkbox", { name: CONTACT_PERMISSION_TEXT.confirmLabel }));
});

test("no error is shown on first render", () => {
    render(<Harness />);
    assert.equal(screen.queryByText("Choose why you can call them."), null);
    assert.equal(screen.queryByText("Tick this box to confirm."), null);
    assert.equal(screen.getByLabelText("Where this permission comes from").getAttribute("aria-invalid"), "false");
});

test("after a submit attempt every error shows with its own text, wired by aria-describedby and aria-invalid", () => {
    render(<Harness showErrors />);
    const checks: Array<[HTMLElement, string]> = [
        [screen.getByRole("radiogroup", { name: "Why you can call them" }), "Choose why you can call them."],
        [screen.getByLabelText("Where this permission comes from"), "Describe where this permission comes from (at least 5 characters)."],
        [screen.getByLabelText("Date permission was given"), "Choose the date permission was given."],
        [screen.getByRole("checkbox", { name: CONTACT_PERMISSION_TEXT.confirmLabel }), "Tick this box to confirm."],
    ];
    for (const [control, text] of checks) {
        assert.equal(control.getAttribute("aria-invalid"), "true", text);
        const ids = (control.getAttribute("aria-describedby") ?? "").split(" ");
        assert.ok(ids.some((id) => document.getElementById(id)?.textContent === text), `${text} is described by the control`);
    }
});

test("leaving a field shows only that field's error", () => {
    render(<Harness />);
    const source = screen.getByLabelText("Where this permission comes from");
    fireEvent.change(source, { target: { value: "abc" } });
    fireEvent.blur(source);
    assert.ok(screen.getByText("Describe where this permission comes from (at least 5 characters)."));
    assert.equal(screen.queryByText("Tick this box to confirm."), null);
});

test("the long-source and future-date errors have their own text", () => {
    render(<Harness showErrors />);
    fireEvent.change(screen.getByLabelText("Where this permission comes from"), { target: { value: "a".repeat(501) } });
    assert.ok(screen.getByText("Keep this to 500 characters or fewer."));
    fireEvent.change(screen.getByLabelText("Date permission was given"), { target: { value: "2999-01-01" } });
    assert.ok(screen.getByText("This date cannot be in the future."));
});

test("filling all four parts gives a valid confirmation and clears every error", () => {
    render(<Harness showErrors />);
    fillContactPermission();
    assert.deepEqual(buildContactPermission(latest), {
        basis: "express_consent",
        source: "Website sign-up form, March 2026",
        permission_date: "2026-03-14",
        statement_version: "2026-11-v1",
    });
    assert.equal(screen.queryByText("Tick this box to confirm."), null);
    assert.equal(screen.getByLabelText("Where this permission comes from").getAttribute("aria-invalid"), "false");
});

test("not ticking the box keeps the confirmation invalid", () => {
    render(<Harness />);
    fillContactPermission({ skipConfirm: true });
    assert.equal(buildContactPermission(latest), null);
});

test("a backend message for the date shows beside the date field, even when the browser rules pass", () => {
    render(<Harness serverError={{ field: "permission_date", message: "The date is before the list existed." }} />);
    const date = screen.getByLabelText("Date permission was given");
    const ids = (date.getAttribute("aria-describedby") ?? "").split(" ");
    assert.ok(ids.some((id) => document.getElementById(id)?.textContent === "The date is before the list existed."));
    assert.equal(date.getAttribute("aria-invalid"), "true");
});

test("a general backend message shows once, in the block, as an alert", () => {
    render(<Harness serverError={{ field: null, message: "Confirm you have permission to call these contacts before adding them." }} />);
    assert.equal(screen.getByRole("alert").textContent, "Confirm you have permission to call these contacts before adding them.");
});
