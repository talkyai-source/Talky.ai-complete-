import assert from "node:assert/strict";
import { test } from "node:test";
import { describePageContext, supportPageContext } from "@/lib/support-page-context";

const campaign = "3fa85f64-5717-4562-b3fc-2c963f66afa6";

test("the route, a campaign id, a call id, the version and the viewport are picked up", () => {
    const context = supportPageContext({ pathname: `/campaigns/${campaign.toUpperCase()}?tab=calls#x`, commitSha: "abc1234", viewport: { width: 1280.4, height: 800 } });
    assert.deepEqual(context, { route: `/campaigns/${campaign.toUpperCase()}`, campaign_id: campaign, app_version: "abc1234", viewport: "1280x800" });
    assert.equal(supportPageContext({ pathname: `/calls/${campaign}` }).call_id, campaign);
});

test("ids that are not UUIDs, bad versions and empty viewports are left out", () => {
    const context = supportPageContext({ pathname: "/campaigns/new", commitSha: "bad sha!", viewport: { width: 0, height: 0 } });
    assert.deepEqual(context, { route: "/campaigns/new" });
    assert.deepEqual(supportPageContext({ pathname: null }), {});
});

test("the Includes line names what will be shared", () => {
    assert.equal(describePageContext({ route: "/dashboard", viewport: "360x640" }), "Includes: this page, your screen size.");
    assert.equal(describePageContext({ route: "/x", campaign_id: campaign, app_version: "v1" }), "Includes: this page, the campaign you are viewing, the app version.");
    assert.equal(describePageContext({}), "Includes: nothing about this page.");
});
