import { test } from "node:test";
import assert from "node:assert/strict";
import { NEW_CAMPAIGN_FOR_INBOUND_HREF, afterCampaignCreateHref } from "@/lib/campaign-create-return";

test("a campaign created for an inbound number returns to the inbound form pre-selected", () => {
    assert.equal(afterCampaignCreateHref("c 1", "inbound"), "/inbound-campaigns/new?campaign_id=c%201");
});

test("any other creation opens the campaign page as before", () => {
    assert.equal(afterCampaignCreateHref("abc", null), "/campaigns/abc");
    assert.equal(afterCampaignCreateHref("abc", "somewhere-else"), "/campaigns/abc");
});

test("the inbound form's create link carries the return marker the creator reads", () => {
    assert.equal(new URL(NEW_CAMPAIGN_FOR_INBOUND_HREF, "https://x").searchParams.get("for"), "inbound");
});
