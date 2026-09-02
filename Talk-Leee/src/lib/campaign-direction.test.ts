import assert from "node:assert/strict";
import { test } from "node:test";
import { isOutboundCampaign, outboundCampaignsOnly } from "@/lib/campaign-direction";

test("an inbound campaign is never an outbound campaign", () => {
    assert.equal(isOutboundCampaign({ direction: "inbound" }), false);
});

test("historical campaigns with no direction are outbound (the column default)", () => {
    assert.equal(isOutboundCampaign({}), true);
    assert.equal(isOutboundCampaign({ direction: undefined }), true);
    assert.equal(isOutboundCampaign({ direction: "outbound" }), true);
});

test("outboundCampaignsOnly drops inbound rows and keeps order", () => {
    const rows = [
        { id: "a", direction: "inbound" as const },
        { id: "b", direction: "outbound" as const },
        { id: "c" },
    ];
    assert.deepEqual(outboundCampaignsOnly(rows).map((r) => r.id), ["b", "c"]);
});
