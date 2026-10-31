import assert from "node:assert/strict";
import { test } from "node:test";
import {
    inboundCampaignHrefForBase,
    isOutboundCampaign,
    outboundCampaignsOnly,
} from "@/lib/campaign-direction";

test("an inbound campaign is never an outbound campaign", () => {
    assert.equal(isOutboundCampaign({ direction: "inbound" }), false);
});

test("historical campaigns with no direction are outbound (the column default)", () => {
    assert.equal(isOutboundCampaign({}), true);
    assert.equal(isOutboundCampaign({ direction: undefined }), true);
    assert.equal(isOutboundCampaign({ direction: "outbound" }), true);
});

test("malformed or future direction values fail closed", () => {
    assert.equal(isOutboundCampaign({ direction: null }), false);
    assert.equal(isOutboundCampaign({ direction: "sideways" }), false);
    assert.equal(isOutboundCampaign({ direction: "INBOUND" }), false);
});

test("outboundCampaignsOnly drops inbound rows and keeps order", () => {
    const rows = [
        { id: "a", direction: "inbound" as const },
        { id: "b", direction: "outbound" as const },
        { id: "c" },
    ];
    assert.deepEqual(outboundCampaignsOnly(rows).map((r) => r.id), ["b", "c"]);
});

test("an inbound base campaign resolves only to its dedicated config route", () => {
    const rows = [
        { id: "config/a", campaign_id: "base-a" },
        { id: "config-b", campaign_id: "base-b" },
    ];

    assert.equal(
        inboundCampaignHrefForBase("base-a", rows),
        "/inbound-campaigns/config%2Fa",
    );
    assert.equal(
        inboundCampaignHrefForBase("missing", rows),
        "/inbound-campaigns",
    );
});
