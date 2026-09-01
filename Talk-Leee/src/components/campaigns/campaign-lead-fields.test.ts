import assert from "node:assert/strict";
import test, { afterEach } from "node:test";
import { createElement } from "react";
import { cleanup, screen } from "@testing-library/react";

import { CampaignLeadFieldsPicker, defaultCampaignLeadFields } from "@/components/campaigns/campaign-lead-fields";
import type { CampaignLeadField, ContactFieldSpec } from "@/lib/lead-details-api";
import { ensureDom } from "@/test-utils/dom";

ensureDom();
afterEach(cleanup);

function spec(key: string, agentUsable = true): ContactFieldSpec {
    return {
        key,
        label: key.replace(/_/g, " "),
        field_type: "text",
        aliases: [],
        agent_usable: agentUsable,
        max_len: 255,
    };
}

test("new campaign defaults include useful conversational fields only", () => {
    const fields = defaultCampaignLeadFields([
        spec("email"),
        spec("company_name"),
        spec("job_title"),
        spec("best_time_to_call"),
        spec("calling_notes"),
        spec("timezone", false),
        spec("do_not_call", false),
    ]);

    assert.deepEqual(fields.map((field) => field.field_key), [
        "email",
        "company_name",
        "job_title",
        "best_time_to_call",
        "calling_notes",
    ]);
    assert.equal(fields.every((field) => field.agent_visible && field.user_visible), true);
    assert.equal(fields.every((field) => !field.is_required), true);
});

test("the picker makes field access and requiredness explicit", async () => {
    const userEvent = (await import("@testing-library/user-event")).default;
    const user = userEvent.setup({ document: globalThis.document });
    const specs = [spec("email"), spec("company_name"), spec("timezone", false)];
    let value: CampaignLeadField[] = [];

    const view = () => createElement(CampaignLeadFieldsPicker, {
        specs,
        value,
        onChange: (next: CampaignLeadField[]) => { value = next; },
    });
    const rendered = (await import("@testing-library/react")).render(view());

    await user.click(screen.getByRole("checkbox", { name: /email/i }));
    rendered.rerender(view());
    await user.click(screen.getByRole("checkbox", { name: /required for this campaign/i }));

    assert.equal(value.length, 1);
    assert.equal(value[0]?.field_key, "email");
    assert.equal(value[0]?.is_required, true);
    assert.equal(screen.queryByRole("checkbox", { name: /timezone/i }), null);
});
