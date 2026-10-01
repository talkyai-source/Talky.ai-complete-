import assert from "node:assert/strict";
import test from "node:test";

import {
    buildCampaignBrief,
    campaignBriefGuidanceText,
    campaignBriefValidation,
    type CampaignBriefDraft,
    campaignBriefDraft,
} from "@/lib/campaign-brief";

const draft: CampaignBriefDraft = {
    decision_maker_role: "Head of Operations",
    approved_next_actions: ["schedule_callback", "send_email", "transfer"],
    transfer_destination: "Sales desk",
    opening_objective: "Confirm whether the operations lead owns vendor selection.",
    max_objection_attempts: 4,
};

test("operator-approved messages and form destination survive editing and save", () => {
    const configured = { ...draft,
        email_action: { subject: "Follow-up", body: "First line\nSecond line" },
        form_action: { name: "Enquiry", recipient: "sales@example.com", subject: "New enquiry", fields: ["email", "phone"] as const },
    };
    const saved = buildCampaignBrief({ draft: { ...configured, form_action: { ...configured.form_action, fields: [...configured.form_action.fields] } }, brand: "Acme", representativeNames: ["Alex"], requiredLeadFields: [] });
    const restored = campaignBriefDraft(saved);
    assert.deepEqual(restored.email_action, configured.email_action);
    assert.deepEqual(restored.form_action, configured.form_action);
    assert.equal(campaignBriefValidation(restored), null);
    assert.match(campaignBriefValidation({ ...restored, form_action: { ...saved.form_action!, recipient: "invalid" } }) ?? "", /destination email/);
});

test("buildCampaignBrief creates the saved nested contract from canonical UI state", () => {
    const brief = buildCampaignBrief({
        draft,
        brand: " Acme ",
        representativeNames: ["Alex", "Sam"],
        requiredLeadFields: [
            { field_key: "email", label: "Email address" },
            { field_key: "best_time_to_call", label: "Best time to call" },
        ],
    });

    assert.deepEqual(brief, {
        representative_name: "Alex",
        brand: "Acme",
        decision_maker_role: "Head of Operations",
        approved_next_actions: ["schedule_callback", "send_email", "transfer"],
        transfer_destination: "Sales desk",
        required_lead_fields: [
            { field_key: "email", label: "Email address" },
            { field_key: "best_time_to_call", label: "Best time to call" },
        ],
        opening_objective: "Confirm whether the operations lead owns vendor selection.",
        max_objection_attempts: 4,
    });
});

test("campaign brief validation blocks missing objective and incomplete transfer approval", () => {
    assert.match(
        campaignBriefValidation({ ...draft, opening_objective: "" }) ?? "",
        /opening objective/i,
    );
    assert.match(
        campaignBriefValidation({ ...draft, transfer_destination: "" }) ?? "",
        /transfer destination/i,
    );
    assert.equal(campaignBriefValidation(draft), null);
});

test("structured guidance is explicit text and therefore shares the one prompt budget", () => {
    const brief = buildCampaignBrief({
        draft,
        brand: "Acme",
        representativeNames: ["Alex"],
        requiredLeadFields: [{ field_key: "email", label: "Email address" }],
    });
    const text = campaignBriefGuidanceText("Keep the call concise.", brief);

    assert.match(text, /CAMPAIGN BRIEF/);
    assert.match(text, /Head of Operations/);
    assert.match(text, /Email address \(email\)/);
    assert.match(text, /Keep the call concise\./);
    assert.doesNotMatch(text, /book a meeting/i);
});

test("structured values use the same one-line normalization as the backend", () => {
    const brief = buildCampaignBrief({
        draft: {
            ...draft,
            decision_maker_role: "  Head\n\tof {Operations}  ",
            opening_objective: "Confirm\n\n  ownership before discovery.",
        },
        brand: " Acme\n Holdings ",
        representativeNames: [" Alex\tSmith "],
        requiredLeadFields: [
            { field_key: " email ", label: " Email\n address " },
        ],
    });

    assert.equal(brief.representative_name, "Alex Smith");
    assert.equal(brief.brand, "Acme Holdings");
    assert.equal(brief.decision_maker_role, "Head of (Operations)");
    assert.equal(brief.opening_objective, "Confirm ownership before discovery.");
    assert.deepEqual(brief.required_lead_fields, [
        { field_key: "email", label: "Email address" },
    ]);
});
