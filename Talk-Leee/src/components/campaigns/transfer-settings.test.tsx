import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { useState } from "react";

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { CampaignBriefFields } from "@/components/campaigns/campaign-brief-fields";
import {
    buildCampaignBrief,
    campaignBriefDraft,
    campaignBriefValidation,
    type CampaignBrief,
    type CampaignBriefDraft,
} from "@/lib/campaign-brief";
import { ensureDom } from "@/test-utils/dom";

ensureDom();
afterEach(cleanup);

const base: CampaignBriefDraft = {
    decision_maker_role: "",
    approved_next_actions: [],
    transfer_destination: "",
    opening_objective: "Confirm who owns vendor selection.",
    max_objection_attempts: 2,
};

const SALES = { name: "Sales", number: "+442071234567", when: "The caller wants prices or wants to buy." };
const SUPPORT = { name: "Support", number: "+442079876543", when: "The caller has a problem with an existing order." };
const HOURS = { timezone: "Europe/London", days: ["mon", "tue", "wed", "thu", "fri"] as const, start: "09:00", end: "17:30" };

function Harness({ initial, onSave }: { initial: CampaignBriefDraft; onSave: (brief: CampaignBrief) => void }) {
    const [draft, setDraft] = useState(initial);
    const error = campaignBriefValidation(draft);
    return (
        <div>
            <CampaignBriefFields idPrefix="t" value={draft} onChange={setDraft} requiredLeadFields={[]} />
            <button
                type="button"
                disabled={Boolean(error)}
                onClick={() => onSave(buildCampaignBrief({ draft, brand: "Acme", representativeNames: ["Alex"], requiredLeadFields: [] }))}
            >
                Save brief
            </button>
        </div>
    );
}

function setup(initial: CampaignBriefDraft) {
    const saved: CampaignBrief[] = [];
    const user = userEvent.setup({ document: globalThis.document });
    render(<Harness initial={initial} onSave={(brief) => saved.push(brief)} />);
    return { saved, user };
}

const rows = () => screen.queryAllByRole("group", { name: /^destination \d/i });
const saveButton = () => screen.getByRole("button", { name: "Save brief" }) as HTMLButtonElement;
const transferBox = () => screen.getByRole("checkbox", { name: /transfer to a human/i });

test("ticking Transfer shows one empty destination row; unticking hides it and sends nothing about transfer", async () => {
    const { saved, user } = setup(base);
    assert.equal(rows().length, 0);
    assert.equal(screen.queryByLabelText("Phone number"), null);

    await user.click(transferBox());
    assert.equal(rows().length, 1);
    const row = within(rows()[0]!);
    assert.equal((row.getByLabelText("Name") as HTMLInputElement).value, "");
    assert.equal((row.getByLabelText("Phone number") as HTMLInputElement).value, "");
    assert.ok(row.getByLabelText("When to transfer here"));
    assert.ok(screen.getByText(/never dials a number that is not on this list/i));

    await user.click(transferBox());
    assert.equal(rows().length, 0);
    await user.click(saveButton());
    assert.equal(saved.length, 1);
    assert.equal("transfer_destinations" in saved[0]!, false);
    assert.equal("transfer_hours" in saved[0]!, false);
    assert.equal(saved[0]!.transfer_destination, "");
});

test("destinations can be added up to five and no further, and removed", async () => {
    const { user } = setup({ ...base, approved_next_actions: ["transfer"], transfer_destinations: [SALES] });
    assert.equal(rows().length, 1);
    assert.equal(screen.queryByRole("button", { name: /remove destination/i }), null, "a lone row cannot be removed");
    for (let count = 2; count <= 5; count += 1) {
        await user.click(screen.getByRole("button", { name: "Add another destination" }));
        assert.equal(rows().length, count);
    }
    assert.equal(screen.queryByRole("button", { name: "Add another destination" }), null);
    await user.click(screen.getByRole("button", { name: "Remove destination 3" }));
    assert.equal(rows().length, 4);
    assert.ok(screen.getByRole("button", { name: "Add another destination" }));
});

test("added row takes focus and removal moves focus to a remaining row", async () => {
    const { user } = setup({ ...base, approved_next_actions: ["transfer"], transfer_destinations: [SALES] });
    await user.click(screen.getByRole("button", { name: "Add another destination" }));
    await new Promise((resolve) => setTimeout(resolve, 20));
    assert.equal(document.activeElement, within(rows()[1]!).getByLabelText("Name"));
    await user.click(screen.getByRole("button", { name: "Remove destination 2" }));
    await new Promise((resolve) => setTimeout(resolve, 20));
    assert.equal(document.activeElement, within(rows()[0]!).getByLabelText("Name"));
});

test("typing two destinations saves exactly the list and no conflicting single value", async () => {
    const { saved, user } = setup(base);
    await user.click(transferBox());
    await user.click(screen.getByRole("button", { name: "Add another destination" }));
    for (const [index, entry] of [SALES, SUPPORT].entries()) {
        const row = within(rows()[index]!);
        await user.type(row.getByLabelText("Name"), entry.name);
        await user.type(row.getByLabelText("Phone number"), entry.number);
        await user.type(row.getByLabelText("When to transfer here"), entry.when);
    }
    assert.equal(saveButton().disabled, false);
    await user.click(saveButton());
    assert.deepEqual(saved[0]!.transfer_destinations, [SALES, SUPPORT]);
    assert.equal(saved[0]!.transfer_destination, null);
    assert.equal("transfer_hours" in saved[0]!, false);
});

const approved = { ...base, approved_next_actions: ["transfer" as const] };
const invalidCases: Array<[string, CampaignBriefDraft, RegExp]> = [
    ["empty name", { ...approved, transfer_destinations: [{ ...SALES, name: "  " }] }, /enter a name for this destination/i],
    ["duplicate name ignoring case", { ...approved, transfer_destinations: [SALES, { ...SUPPORT, name: "sALES" }] }, /different name/i],
    ["missing when with two destinations", { ...approved, transfer_destinations: [SALES, { ...SUPPORT, when: "" }] }, /describe when to transfer here/i],
    ["when over 200 characters", { ...approved, transfer_destinations: [{ ...SALES, when: "x".repeat(201) }] }, /200 characters or fewer/i],
    ["name over 40 characters", { ...approved, transfer_destinations: [{ ...SALES, name: "n".repeat(41) }] }, /40 characters or fewer/i],
    ["invalid number", { ...approved, transfer_destinations: [{ ...SALES, number: "call me" }] }, /valid phone number/i],
    ["end not after start", { ...approved, transfer_destinations: [SALES], transfer_hours: { ...HOURS, days: [...HOURS.days], start: "17:30", end: "09:00" } }, /end time must be later than the start time/i],
    ["no day chosen", { ...approved, transfer_destinations: [SALES], transfer_hours: { ...HOURS, days: [] } }, /choose at least one day/i],
];
for (const [label, draft, message] of invalidCases) {
    test(`validation shows a message beside the field and blocks save: ${label}`, async () => {
        const { saved, user } = setup(draft);
        assert.ok(screen.getByText(message));
        assert.equal(saveButton().disabled, true);
        await user.click(saveButton());
        assert.equal(saved.length, 0);
    });
}

test("hours switch off sends no transfer_hours; on sends the object", async () => {
    const { saved, user } = setup({ ...approved, transfer_destinations: [SALES] });
    const toggle = screen.getByRole("switch", { name: "Only during these hours" }) as HTMLInputElement;
    assert.equal(toggle.checked, false);
    assert.equal(screen.queryByLabelText("Start time"), null);
    await user.click(saveButton());
    assert.equal("transfer_hours" in saved[0]!, false);

    await user.click(toggle);
    assert.equal(toggle.checked, true);
    assert.ok(screen.getByRole("combobox", { name: /transfer timezone/i }));
    assert.equal(screen.getAllByRole("checkbox", { name: /^(mon|tues|wednes|thurs|fri|satur|sun)day$/i }).length, 7);
    assert.ok(screen.getByText(/the team is unavailable and offers to take a message/i));
    // The defaults must be a valid window the user can save as is, then edit.
    assert.equal(saveButton().disabled, false);
    await user.click(saveButton());
    const hours = saved[1]!.transfer_hours!;
    assert.deepEqual(hours.days, ["mon", "tue", "wed", "thu", "fri"]);
    assert.equal(hours.start < hours.end, true);

    await user.click(toggle);
    await user.click(saveButton());
    assert.equal("transfer_hours" in saved[2]!, false);
});

test("a legacy campaign with only the single destination loads as one row and saves untouched as before", async () => {
    const legacy = campaignBriefDraft({
        approved_next_actions: ["transfer"],
        transfer_destination: "+442071234567",
        opening_objective: "Confirm who owns vendor selection.",
    });
    const { saved, user } = setup(legacy);
    assert.equal(rows().length, 1);
    const row = within(rows()[0]!);
    assert.equal((row.getByLabelText("Phone number") as HTMLInputElement).value, "+442071234567");
    assert.equal((row.getByLabelText("Name") as HTMLInputElement).value, "");
    assert.equal(saveButton().disabled, false);
    await user.click(saveButton());
    assert.equal(saved[0]!.transfer_destination, "+442071234567");
    assert.equal("transfer_destinations" in saved[0]!, false);

    await user.type(row.getByLabelText("Name"), "Sales");
    await user.click(saveButton());
    assert.deepEqual(saved[1]!.transfer_destinations, [{ name: "Sales", number: "+442071234567", when: "" }]);
    assert.equal(saved[1]!.transfer_destination, null);
});

test("a campaign saved with the list and hours loads them back and re-saves the same values", async () => {
    const stored = {
        approved_next_actions: ["transfer" as const],
        transfer_destination: SALES.number,
        transfer_destinations: [SALES, SUPPORT],
        transfer_hours: { ...HOURS, days: [...HOURS.days] },
        opening_objective: "Confirm who owns vendor selection.",
    };
    const { saved, user } = setup(campaignBriefDraft(stored));
    assert.equal(rows().length, 2);
    assert.equal((within(rows()[1]!).getByLabelText("Name") as HTMLInputElement).value, "Support");
    assert.equal((screen.getByRole("switch", { name: "Only during these hours" }) as HTMLInputElement).checked, true);
    assert.equal((screen.getByLabelText("Start time") as HTMLInputElement).value, "09:00");
    assert.equal((screen.getByLabelText("End time") as HTMLInputElement).value, "17:30");
    assert.equal((screen.getByRole("checkbox", { name: "Friday" }) as HTMLInputElement).checked, true);
    assert.equal((screen.getByRole("checkbox", { name: "Saturday" }) as HTMLInputElement).checked, false);
    await user.click(saveButton());
    assert.deepEqual(saved[0]!.transfer_destinations, [SALES, SUPPORT]);
    assert.deepEqual(saved[0]!.transfer_hours, stored.transfer_hours);
});

test("an inbound campaign says transfers are not switched on yet; an outbound one does not", () => {
    const withTransfer: CampaignBriefDraft = { ...base, approved_next_actions: ["transfer"], transfer_destinations: [SALES] };
    const view = render(
        <CampaignBriefFields idPrefix="in" value={withTransfer} onChange={() => undefined} requiredLeadFields={[]} direction="inbound" />,
    );
    const note = screen.getByTestId("in-transfer-inbound-note");
    assert.match(note.textContent ?? "", /inbound campaigns are not switched on yet/i);
    view.unmount();

    render(
        <CampaignBriefFields idPrefix="out" value={withTransfer} onChange={() => undefined} requiredLeadFields={[]} direction="outbound" />,
    );
    assert.equal(screen.queryByTestId("out-transfer-inbound-note"), null);
    cleanup();

    // A form that does not know the direction shows no claim either way.
    render(<CampaignBriefFields idPrefix="any" value={withTransfer} onChange={() => undefined} requiredLeadFields={[]} />);
    assert.equal(screen.queryByTestId("any-transfer-inbound-note"), null);
});

// ---- "If nobody answers": ring time and what happens next ----
const ringBox = () => screen.getByLabelText("Ring for") as HTMLInputElement;
const thenRadio = (name: string) => screen.getByRole("radio", { name }) as HTMLInputElement;
const RING_MESSAGE = /enter a whole number from 10 to 60/i;
const withDest = { ...approved, transfer_destinations: [SALES] };

test("no-answer controls: hidden and unsent with Transfer unticked", async () => {
    const { saved, user } = setup(base);
    assert.equal(screen.queryByLabelText("Ring for"), null);
    assert.equal(screen.queryByRole("radiogroup", { name: "Then" }), null);
    await user.click(saveButton());
    assert.equal("transfer_ring_seconds" in saved[0]!, false);
    assert.equal("transfer_if_no_answer" in saved[0]!, false);
});

test("no-answer controls: shown with Transfer ticked, labelled, nothing chosen, nothing sent", async () => {
    const { saved, user } = setup(base);
    await user.click(transferBox());
    assert.ok(screen.getByText("If nobody answers"));
    assert.equal(ringBox().value, "");
    assert.ok(screen.getByText("seconds"));
    assert.ok(screen.getByText("How long the person's phone rings before the agent gives up. Leave empty for 30 seconds."));
    assert.ok(screen.getByRole("radiogroup", { name: "Then" }));
    assert.equal(thenRadio("Stay with the caller").checked, false);
    assert.equal(thenRadio("End the call").checked, false);
    assert.equal(thenRadio("Use the standard behaviour (stay with the caller)").checked, true);
    assert.ok(screen.getByText("The agent tells the caller nobody is available and offers to take a message."));
    assert.ok(screen.getByText("The agent tells the caller nobody is available and ends the call politely."));
    await user.type(within(rows()[0]!).getByLabelText("Name"), SALES.name);
    await user.type(within(rows()[0]!).getByLabelText("Phone number"), SALES.number);
    await user.click(saveButton());
    assert.equal("transfer_ring_seconds" in saved[0]!, false);
    assert.equal("transfer_if_no_answer" in saved[0]!, false);
});

test("ring time 20 is sent as the number 20", async () => {
    const { saved, user } = setup(withDest);
    await user.type(ringBox(), "20");
    assert.equal(saveButton().disabled, false);
    await user.click(saveButton());
    assert.strictEqual(saved[0]!.transfer_ring_seconds, 20);
});

for (const bad of ["9", "61", "12.5"]) {
    test(`ring time ${bad} shows the message and blocks save; clearing removes it and sends no key`, async () => {
        const { saved, user } = setup(withDest);
        await user.type(ringBox(), bad);
        assert.ok(screen.getByText(RING_MESSAGE));
        assert.equal(ringBox().getAttribute("aria-invalid"), "true");
        assert.ok(ringBox().getAttribute("aria-describedby"));
        assert.equal(saveButton().disabled, true);
        await user.click(saveButton());
        assert.equal(saved.length, 0);
        await user.clear(ringBox());
        assert.equal(screen.queryByText(RING_MESSAGE), null);
        assert.equal(saveButton().disabled, false);
        await user.click(saveButton());
        assert.equal("transfer_ring_seconds" in saved[0]!, false);
    });
}

test("a non-number loaded into the draft shows the message and blocks save", async () => {
    setup({ ...withDest, transfer_ring_seconds: "abc" });
    assert.ok(screen.getByText(RING_MESSAGE));
    assert.equal(saveButton().disabled, true);
});

test("the then-choice sends end_call or return_to_agent, and the standard option sends nothing", async () => {
    const { saved, user } = setup(withDest);
    await user.click(thenRadio("End the call"));
    await user.click(saveButton());
    assert.equal(saved[0]!.transfer_if_no_answer, "end_call");
    await user.click(thenRadio("Stay with the caller"));
    await user.click(saveButton());
    assert.equal(saved[1]!.transfer_if_no_answer, "return_to_agent");
    await user.click(thenRadio("Use the standard behaviour (stay with the caller)"));
    await user.click(saveButton());
    assert.equal("transfer_if_no_answer" in saved[2]!, false);
});

test("a campaign saved with both settings loads them back and re-saves them unchanged", async () => {
    const { saved, user } = setup(campaignBriefDraft({
        approved_next_actions: ["transfer"],
        transfer_destination: SALES.number,
        transfer_destinations: [SALES],
        transfer_ring_seconds: 45,
        transfer_if_no_answer: "end_call",
        opening_objective: "Confirm who owns vendor selection.",
    }));
    assert.equal(ringBox().value, "45");
    assert.equal(thenRadio("End the call").checked, true);
    await user.click(saveButton());
    assert.strictEqual(saved[0]!.transfer_ring_seconds, 45);
    assert.equal(saved[0]!.transfer_if_no_answer, "end_call");
});

test("a campaign saved before these settings existed sends neither key untouched", async () => {
    const { saved, user } = setup(campaignBriefDraft({
        approved_next_actions: ["transfer"],
        transfer_destination: SALES.number,
        opening_objective: "Confirm who owns vendor selection.",
    }));
    assert.equal(ringBox().value, "");
    await user.click(saveButton());
    assert.equal("transfer_ring_seconds" in saved[0]!, false);
    assert.equal("transfer_if_no_answer" in saved[0]!, false);
});

test("unticking Transfer clears both settings", async () => {
    const { saved, user } = setup({ ...withDest, transfer_ring_seconds: "40", transfer_if_no_answer: "end_call" });
    await user.click(transferBox());
    assert.equal(screen.queryByLabelText("Ring for"), null);
    await user.click(saveButton());
    assert.equal("transfer_ring_seconds" in saved[0]!, false);
    assert.equal("transfer_if_no_answer" in saved[0]!, false);
    await user.click(transferBox());
    assert.equal(ringBox().value, "");
    assert.equal(thenRadio("End the call").checked, false);
});
