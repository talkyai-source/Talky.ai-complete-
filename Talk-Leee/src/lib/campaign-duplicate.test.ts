import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";
import { api } from "@/lib/api";
import { duplicateCampaign, duplicateSuccessText } from "@/lib/campaign-duplicate";
import { dashboardApi, type Campaign } from "@/lib/dashboard-api";
import { ApiClientError } from "@/lib/http-client";
import { leadDetailsApi } from "@/lib/lead-details-api";

afterEach(() => { mock.restoreAll(); });

// What the backend reports for the new row (POST /campaigns/{id}/duplicate):
// its own name, a draft, and zero counters.
const created = {
    id: "new-1", name: "Spring push - Copy", status: "draft", direction: "outbound",
    total_leads: 0, calls_completed: 0, calls_failed: 0,
} as unknown as Campaign;

type Calls = { duplicates: string[]; others: string[] };

/** Stub the duplicate route and record any call to the old copy-by-create path. */
function wire(duplicate: (id: string) => Promise<{ campaign: Campaign }> = async () => ({ campaign: created })): Calls {
    const calls: Calls = { duplicates: [], others: [] };
    mock.method(dashboardApi, "duplicateCampaign", async (id: string) => {
        calls.duplicates.push(id);
        return duplicate(id);
    });
    for (const [target, name] of [
        [dashboardApi, "getCampaign"], [dashboardApi, "createCampaign"],
        [api, "getCampaignKnowledge"], [leadDetailsApi, "campaignFields"], [leadDetailsApi, "setCampaignFields"],
    ] as const) {
        mock.method(target as never, name as never, (async () => { calls.others.push(name); throw new Error(`${name} must not be called`); }) as never);
    }
    return calls;
}

test("Duplicate asks the backend's duplicate route for the source and returns the campaign it created", async () => {
    const calls = wire();
    const result = await duplicateCampaign("src-1");
    assert.deepEqual(calls.duplicates, ["src-1"]);
    assert.deepEqual(calls.others, [], "no create, no knowledge read, no lead-field copy from the browser");
    assert.equal(result.campaign, created);
});

test("a campaign that has knowledge files is duplicated, not refused: the browser does not read knowledge at all", async () => {
    const calls = wire();
    const result = await duplicateCampaign("src-with-knowledge");
    assert.deepEqual(calls.duplicates, ["src-with-knowledge"]);
    assert.deepEqual(calls.others, []);
    assert.equal(result.campaign.id, "new-1");
});

test("the success message names the backend's copy and says only what the endpoint copies", async () => {
    wire();
    const result = await duplicateCampaign("src-1");
    assert.equal(
        result.message,
        "Duplicated as “Spring push - Copy”. The copy is a draft with the same settings, knowledge files and lead fields. Contacts, calls and results are not copied.",
    );
    assert.equal(result.message, duplicateSuccessText("Spring push - Copy"));
    // Pacing, caller line, concurrent calls and retries are copied by the endpoint: no "defaults" claim.
    assert.equal(/default/i.test(result.message), false);
});

test("the copy is named by the backend's response, not by the browser", async () => {
    wire(async () => ({ campaign: { ...created, name: "Renamed by server" } }));
    const result = await duplicateCampaign("src-1");
    assert.equal(result.message, duplicateSuccessText("Renamed by server"));
});

test("a refusal rejects with the backend's own message", async () => {
    const refusal = new ApiClientError({ status: 409, code: "conflict", message: "Campaign knowledge must be ready before duplicating.", url: "http://example.test/campaigns/src-1/duplicate", method: "POST" });
    wire(async () => { throw refusal; });
    await assert.rejects(duplicateCampaign("src-1"), (err: Error) => err === refusal && err.message === "Campaign knowledge must be ready before duplicating.");
});

test("over HTTP: POST /campaigns/{id}/duplicate; a 409 error envelope's message is what the caller gets", async () => {
    const holder = globalThis as unknown as { fetch: unknown };
    const prevFetch = holder.fetch;
    const seen: { path: string; method: string | undefined }[] = [];
    let answer: { status: number; body: unknown } = { status: 201, body: { campaign: created } };
    holder.fetch = async (input: unknown, init?: { method?: string }) => {
        seen.push({ path: new URL(String(input)).pathname, method: init?.method });
        return new Response(JSON.stringify(answer.body), { status: answer.status, headers: { "content-type": "application/json" } });
    };
    try {
        const ok = await duplicateCampaign("src-1");
        assert.deepEqual(seen, [{ path: "/api/v1/campaigns/src-1/duplicate", method: "POST" }]);
        assert.equal(ok.campaign.id, "new-1");
        answer = { status: 409, body: { error: { code: "conflict", message: "Inbound campaigns must use their managed routing lifecycle.", details: null, request_id: "req-1" } } };
        await assert.rejects(duplicateCampaign("src-1"), (err: Error) => err.message === "Inbound campaigns must use their managed routing lifecycle.");
    } finally {
        holder.fetch = prevFetch;
    }
});
