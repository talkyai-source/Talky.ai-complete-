import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { CampaignBriefFields } from "@/components/campaigns/campaign-brief-fields";
import { PromptLayerPreview } from "@/components/campaigns/prompt-layer-preview";
import type { CampaignBriefDraft } from "@/lib/campaign-brief";
import { ensureDom } from "@/test-utils/dom";

ensureDom();
afterEach(cleanup);

const configured: CampaignBriefDraft = {
    decision_maker_role: "Head of Operations",
    approved_next_actions: ["schedule_callback", "transfer"],
    transfer_destination: "Sales desk",
    opening_objective: "Confirm who owns vendor selection.",
    max_objection_attempts: 3,
};

test("campaign brief fields expose every structured control with accessible names", () => {
    render(
        <CampaignBriefFields
            idPrefix="test-brief"
            value={configured}
            onChange={() => undefined}
            requiredLeadFields={[{ field_key: "email", label: "Email address" }]}
        />,
    );

    assert.equal(screen.getByLabelText(/opening objective/i).getAttribute("maxlength"), "500");
    assert.equal((screen.getByLabelText(/decision-maker role/i) as HTMLInputElement).value, "Head of Operations");
    assert.equal((screen.getByRole("checkbox", { name: /schedule callback/i }) as HTMLInputElement).checked, true);
    assert.equal((screen.getByRole("checkbox", { name: /transfer to a human/i }) as HTMLInputElement).checked, true);
    assert.equal((screen.getByLabelText(/transfer destination/i) as HTMLInputElement).disabled, false);
    assert.equal((screen.getByLabelText(/maximum objection attempts/i) as HTMLSelectElement).value, "3");
    assert.ok(screen.getByRole("list", { name: /required lead fields/i }));
    assert.ok(screen.getByText("Email address"));
});

test("prompt preview renders ordered keyboard-toggleable collapsed layers", async () => {
    const user = userEvent.setup({ document: globalThis.document });
    render(
        <PromptLayerPreview
            headingId="test-layers"
            promptChars={42}
            layers={[
                { key: "persona", label: "Persona", content: "Persona content" },
                { key: "campaign_brief", label: "Campaign brief", content: "Brief content" },
            ]}
        />,
    );

    const details = Array.from(document.querySelectorAll("details"));
    assert.equal(details.length, 2);
    assert.equal(details.every((item) => !item.open), true);
    assert.match(screen.getByText(/2 layers/).textContent ?? "", /42 chars/);

    await user.click(screen.getByText("Campaign brief"));
    assert.equal(details[1]?.open, true);
    assert.ok(screen.getByText("Brief content"));
});
