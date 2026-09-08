import { test } from "node:test";
import assert from "node:assert/strict";
import { INBOUND_RETURN, afterCampaignCreateHref } from "@/lib/campaign-create-return";

test("a campaign created for an inbound number continues to the number-and-routing step", () => {
    assert.equal(afterCampaignCreateHref("c 1", INBOUND_RETURN), "/inbound-campaigns/new?campaign_id=c%201");
});

test("any other creation opens the campaign page as before", () => {
    assert.equal(afterCampaignCreateHref("abc", null), "/campaigns/abc");
    assert.equal(afterCampaignCreateHref("abc", "somewhere-else"), "/campaigns/abc");
});
