/**
 * FX7d Task 2 — the campaign brief (wizard step 1 and the shared campaign
 * form): the message at the foot of the brief has an id, and the field it is
 * about names it in aria-describedby and carries aria-invalid="true" while it
 * shows. No error text is new.
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { cleanup, render, screen } from "@testing-library/react";

import { CampaignBriefFields } from "@/components/campaigns/campaign-brief-fields";
import { campaignBriefInvalidFields, campaignBriefValidation, type CampaignBriefDraft } from "@/lib/campaign-brief";
import { ensureDom } from "@/test-utils/dom";

ensureDom();
afterEach(cleanup);

const VALID: CampaignBriefDraft = {
    decision_maker_role: "",
    approved_next_actions: [],
    transfer_destination: "",
    opening_objective: "Confirm who owns vendor selection.",
    max_objection_attempts: 2,
};
const P = "b";
const MESSAGE_ID = `${P}-validation-error`;

function show(draft: CampaignBriefDraft) {
    render(<CampaignBriefFields idPrefix={P} value={draft} onChange={() => undefined} requiredLeadFields={[]} />);
}
const byId = (id: string) => document.getElementById(id) as HTMLElement;
const invalid = (field: Element) => field.getAttribute("aria-invalid") === "true";
const describedText = (field: Element) => (field.getAttribute("aria-describedby") ?? "").split(" ").filter(Boolean)
    .map((id) => document.getElementById(id)?.textContent?.trim() ?? null);
/** The field is in error and one of the messages it names is exactly `message`. */
function assertTied(field: Element, message: string) {
    assert.equal(document.getElementById(MESSAGE_ID)?.textContent?.trim(), message, "the message shown has the id");
    assert.equal(invalid(field), true, "aria-invalid");
    assert.equal(describedText(field).includes(message), true, "aria-describedby resolves to the message");
}
function assertNotTied(field: Element) {
    assert.equal(invalid(field), false);
    assert.equal((field.getAttribute("aria-describedby") ?? "").split(" ").includes(MESSAGE_ID), false);
}

test("a valid brief shows no message and ties nothing", () => {
    show(VALID);
    assert.equal(document.getElementById(MESSAGE_ID) === null, true);
    assertNotTied(byId(`${P}-opening-objective`));
    assertNotTied(byId(`${P}-decision-role`));
    assertNotTied(screen.getByRole("combobox", { name: "Maximum objection attempts" }));
    // The opening objective keeps its help text.
    assert.equal(byId(`${P}-opening-objective`).getAttribute("aria-describedby"), `${P}-opening-help`);
});

test("opening objective: the 'add an opening objective' message is tied to it, beside its help text", () => {
    show({ ...VALID, opening_objective: "  " });
    const field = byId(`${P}-opening-objective`);
    assertTied(field, "Add an opening objective so the representative knows what a successful first exchange should achieve.");
    assert.equal(field.getAttribute("aria-describedby"), `${P}-opening-help ${MESSAGE_ID}`);
    assertNotTied(byId(`${P}-decision-role`));
});

test("opening objective: the 500-character message is tied to it", () => {
    show({ ...VALID, opening_objective: "x".repeat(501) });
    assertTied(byId(`${P}-opening-objective`), "Opening objective must be 500 characters or fewer.");
});

test("decision-maker role: the 160-character message is tied to it", () => {
    show({ ...VALID, decision_maker_role: "r".repeat(161) });
    assertTied(byId(`${P}-decision-role`), "Decision-maker role must be 160 characters or fewer.");
    assertNotTied(byId(`${P}-opening-objective`));
});

test("maximum objection attempts: the 1-to-5 message is tied to the select", () => {
    show({ ...VALID, max_objection_attempts: 9 });
    assertTied(screen.getByRole("combobox", { name: "Maximum objection attempts" }), "Maximum objection attempts must be from 1 to 5.");
});

test("transfer destination: the 'add a transfer destination' message is tied to the first phone number", () => {
    show({ ...VALID, approved_next_actions: ["transfer"] });
    assertTied(byId(`${P}-transfer-0-number`), "Add a transfer destination or remove Transfer from the approved next actions.");
    assert.equal(byId(`${P}-transfer-0-name`).hasAttribute("aria-invalid"), false);
});

test("transfer action: the 'select Transfer' message is tied to the Transfer checkbox only", () => {
    show({ ...VALID, approved_next_actions: ["schedule_callback"], transfer_destination: "+442071234567" });
    const boxes = Array.from(document.querySelectorAll<HTMLInputElement>("fieldset input[type=checkbox]"));
    const tied = boxes.filter(invalid);
    assert.equal(tied.length, 1);
    assert.equal(tied[0].closest("label")?.textContent?.includes("Transfer"), true);
    assertTied(tied[0], "Select Transfer as an approved next action or clear the transfer destination.");
});

test("approved email: the message is tied to the empty subject", () => {
    show({ ...VALID, approved_next_actions: ["send_email"], email_action: { subject: " ", body: "Thanks for your time." } });
    assertTied(byId(`${P}-email-subject`), "Add the approved email subject and message, or remove its configuration.");
    assertNotTied(byId(`${P}-email-body`));
});

test("approved email: the message is tied to the empty message", () => {
    show({ ...VALID, approved_next_actions: ["send_email"], email_action: { subject: "Following up", body: "" } });
    assertTied(byId(`${P}-email-body`), "Add the approved email subject and message, or remove its configuration.");
    assertNotTied(byId(`${P}-email-subject`));
});

const FORM_MESSAGE = "Add the form name, destination email, subject and at least one field.";
const FORM_OK = { name: "Quote request", recipient: "inbox@example.com", subject: "New quote", fields: ["email" as const] };

test("form: the message is tied to the empty form name", () => {
    show({ ...VALID, approved_next_actions: ["submit_form"], form_action: { ...FORM_OK, name: "" } });
    assertTied(byId(`${P}-form-name`), FORM_MESSAGE);
    assertNotTied(byId(`${P}-form-recipient`));
    assertNotTied(byId(`${P}-form-subject`));
});

test("form: the message is tied to a destination email that is not an address", () => {
    show({ ...VALID, approved_next_actions: ["submit_form"], form_action: { ...FORM_OK, recipient: "inbox" } });
    assertTied(byId(`${P}-form-recipient`), FORM_MESSAGE);
    assertNotTied(byId(`${P}-form-name`));
});

test("form: the message is tied to the empty email subject", () => {
    show({ ...VALID, approved_next_actions: ["submit_form"], form_action: { ...FORM_OK, subject: "" } });
    assertTied(byId(`${P}-form-subject`), FORM_MESSAGE);
    assertNotTied(byId(`${P}-form-name`));
});

test("form: the message is tied to every 'fields to submit' checkbox when none is ticked", () => {
    show({ ...VALID, approved_next_actions: ["submit_form"], form_action: { ...FORM_OK, fields: [] } });
    const boxes = Array.from(document.querySelectorAll<HTMLInputElement>('[aria-label="Fields to submit"] input[type=checkbox]'));
    assert.equal(boxes.length, 5);
    for (const box of boxes) assertTied(box, FORM_MESSAGE);
    assertNotTied(byId(`${P}-form-name`));
});

test("transfer days: the 'choose at least one day' message is tied to every day checkbox", () => {
    show({
        ...VALID, approved_next_actions: ["transfer"], transfer_destinations: [{ name: "Sales", number: "+442071234567", when: "" }],
        transfer_hours: { timezone: "Europe/London", days: [], start: "09:00", end: "17:00" },
    });
    const days = Array.from(document.querySelectorAll<HTMLInputElement>('[aria-label="Transfer days"] input[type=checkbox]'));
    assert.equal(days.length, 7);
    for (const day of days) {
        assert.equal(invalid(day), true);
        assert.deepEqual(describedText(day), ["Choose at least one day."]);
    }
});

test("transfer days: nothing is tied while a day is chosen", () => {
    show({
        ...VALID, approved_next_actions: ["transfer"], transfer_destinations: [{ name: "Sales", number: "+442071234567", when: "" }],
        transfer_hours: { timezone: "Europe/London", days: ["mon"], start: "09:00", end: "17:00" },
    });
    const days = Array.from(document.querySelectorAll<HTMLInputElement>('[aria-label="Transfer days"] input[type=checkbox]'));
    assert.equal(days.some((day) => invalid(day) || day.hasAttribute("aria-describedby")), false);
});

test("the fields named always belong to a message, and only the transfer summary names none", () => {
    const drafts: CampaignBriefDraft[] = [
        VALID,
        { ...VALID, opening_objective: "" },
        { ...VALID, decision_maker_role: "r".repeat(161) },
        { ...VALID, max_objection_attempts: 0 },
        { ...VALID, approved_next_actions: ["transfer"] },
        { ...VALID, transfer_destination: "+442071234567" },
        { ...VALID, approved_next_actions: ["send_email"], email_action: { subject: "", body: "" } },
        { ...VALID, approved_next_actions: ["submit_form"], form_action: { ...FORM_OK, name: "", fields: [] } },
    ];
    for (const draft of drafts) {
        assert.equal(campaignBriefInvalidFields(draft).length > 0, campaignBriefValidation(draft) !== null, JSON.stringify(draft));
    }
    const summary: CampaignBriefDraft = { ...VALID, approved_next_actions: ["transfer"], transfer_destinations: [{ name: "", number: "nope", when: "" }] };
    assert.equal(campaignBriefValidation(summary), "Fix the transfer settings marked above before saving.");
    assert.deepEqual(campaignBriefInvalidFields(summary), []);
    assert.deepEqual(campaignBriefInvalidFields({ ...VALID, approved_next_actions: ["send_email"], email_action: { subject: "", body: "" } }), ["email_subject", "email_body"]);
});
