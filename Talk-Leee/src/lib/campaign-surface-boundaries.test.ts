import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";

function pageSource(relativePath: string): string {
    return readFileSync(path.join(process.cwd(), "src/app", relativePath), "utf8");
}

test("the inbound detail page exposes campaign-scoped operational panels", () => {
    const source = pageSource("inbound-campaigns/[id]/page.tsx");

    assert.match(source, /<LiveCallsPanel(?=[^>]*campaignId=\{campaign\.campaign_id\})(?=[^>]*direction="inbound")[^>]*\/>/);
    assert.match(source, /<RejectedInboundCallsPanel[^>]*campaignId=\{campaign\.campaign_id\}[^>]*\/>/);
    assert.match(source, /<CallIssuesPanel(?=[^>]*campaignId=\{campaign\.campaign_id\})(?=[^>]*direction="inbound")[^>]*\/>/);
    assert.match(source, /const canManageKnowledge = permissions\.isSuccess && capabilities\.canEdit/);
    assert.match(source, /<KnowledgePanel(?=[^>]*campaignId=\{campaign\.campaign_id\})(?=[^>]*readOnly=\{!canManageKnowledge\})[^>]*\/>/);
});

test("generic campaign detail and edit pages redirect inbound rows before outbound work", () => {
    const detail = pageSource("campaigns/[id]/page.tsx");
    const edit = pageSource("campaigns/[id]/edit/page.tsx");
    const detailBoundary = detail.indexOf("!isOutboundCampaign(campaignData.campaign)");
    const detailOutboundWork = detail.indexOf("listContacts");
    const editBoundary = edit.indexOf("!isOutboundCampaign(campaign)");
    const editOutboundWork = edit.indexOf("setInitial({");

    assert.notEqual(detailBoundary, -1);
    assert.notEqual(detailOutboundWork, -1);
    assert.ok(detailBoundary < detailOutboundWork);
    assert.notEqual(editBoundary, -1);
    assert.notEqual(editOutboundWork, -1);
    assert.ok(editBoundary < editOutboundWork);
});

test("the dashboard filters the mixed campaign response before rendering it", () => {
    const source = pageSource("dashboard/page.tsx");

    assert.match(source, /setCampaigns\(outboundCampaignsOnly\(campaignsData\.campaigns \?\? \[\]\)\)/);
    assert.match(source, /getCallAnalytics\(month\.from, month\.to, "day", "outbound", false\)/);
    assert.match(source, /getCallAnalytics\(month\.from, month\.to, "hour", "outbound", false\)/);
    assert.match(source, /getCallAnalyticsByCampaign\(month\.from, month\.to, "day", "outbound", false\)/);
    assert.match(source, /Real outbound call volume over time/);
    assert.match(source, /Recent Outbound Campaigns/);
});

test("the outbound detail surface is explicit and never renders inbound rejections", () => {
    const source = pageSource("campaigns/[id]/page.tsx");

    assert.match(source, /<LiveCallsPanel[^>]*campaignId=\{campaignId\}[^>]*direction="outbound"[^>]*\/>/);
    assert.doesNotMatch(source, /RejectedInboundCallsPanel/);
});

test("the open outbound detail route redirects if polling observes conversion", () => {
    const source = pageSource("campaigns/[id]/page.tsx");
    const pollStart = source.indexOf("const tick = async () =>");
    const pollBoundary = source.indexOf("!isOutboundCampaign(c.campaign)", pollStart);
    const pollRender = source.indexOf("setCampaign(c.campaign)", pollStart);

    assert.notEqual(pollStart, -1);
    assert.notEqual(pollBoundary, -1);
    assert.ok(pollBoundary < pollRender);
    assert.match(source, /status !== "running" && status !== "active" && status !== "draft"/);
});

test("campaign detail initial loads discard superseded responses before navigation or state writes", () => {
    const source = pageSource("campaigns/[id]/page.tsx");

    assert.match(source, /const generation = \+\+loadGeneration\.current/);
    assert.match(source, /generation !== loadGeneration\.current/);
    assert.match(source, /loadGeneration\.current \+= 1/);
});

test("a successful analytics reload clears a prior request error", () => {
    const source = pageSource("analytics/page.tsx");
    const response = source.indexOf("const response = await extendedApi.getCallAnalytics");
    const success = source.indexOf("setData(response.series)", response);
    const clear = source.indexOf('setError("")', response);

    assert.notEqual(response, -1);
    assert.ok(clear > response && clear <= success);
});
