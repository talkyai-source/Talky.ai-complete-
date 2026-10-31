import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { api, KnowledgeTestResponseSchema } from "@/lib/api";
import { ApiClientError } from "@/lib/http-client";

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

function response() {
    const text = "- Fees: Original fee is $20.";
    return { query: "Fees", hits: [{ id: "n", summary: "Unapproved summary" }], evidence: {
        status: "matched", text, passages: [{ node_id: "n", source_id: "s", source_version: 2,
            version: "2026-10-06T00:00:00Z", text, coverage: 1 }],
    } };
}

test("actual API preserves shared source evidence and scoped request", async () => {
    let request: { url: string; init?: RequestInit } | undefined;
    globalThis.fetch = async (url, init) => {
        request = { url: String(url), init };
        return new Response(JSON.stringify(response()), { headers: { "content-type": "application/json" } });
    };
    assert.deepEqual(await api.testCampaignKnowledge("campaign-a", "Fees", 3), response());
    assert.match(request!.url, /\/campaigns\/campaign-a\/knowledge\/test$/);
    assert.equal(request!.init?.method, "POST");
    assert.deepEqual(JSON.parse(String(request!.init?.body)), { query: "Fees", k: 3 });
});

for (const malformed of [
    { query: "Fees", hits: [] },
    { ...response(), evidence: { ...response().evidence, status: "constructor" } },
    { ...response(), evidence: { ...response().evidence, status: "no_match" } },
    { ...response(), evidence: { ...response().evidence, passages: [] } },
    { ...response(), evidence: { ...response().evidence, text: "Different source" } },
    ...[true, "1", null, 2, -1].map((coverage) => ({ ...response(), evidence: {
        ...response().evidence, passages: [{ ...response().evidence.passages[0], coverage }],
    } })),
    { ...response(), evidence: { ...response().evidence, passages: [{ ...response().evidence.passages[0], source_version: true }] } },
]) {
    test(`malformed diagnostic cannot fall back to raw-hit text: ${JSON.stringify(malformed)}`, () => {
        assert.equal(KnowledgeTestResponseSchema.safeParse(malformed).success, false);
    });
}

test("actual API rejects cross-query and legacy response without evidence", async () => {
    for (const data of [{ ...response(), query: "Other question" }, { query: "Fees", hits: response().hits }]) {
        globalThis.fetch = async () => new Response(JSON.stringify(data), { headers: { "content-type": "application/json" } });
        await assert.rejects(api.testCampaignKnowledge("campaign-a", "Fees"), (error: unknown) => (
            error instanceof ApiClientError && error.code === "invalid_response"
        ));
    }
});

test("unknown coverage/provenance stays nullable weak evidence", () => {
    const data = response();
    const weak = { ...data, evidence: { ...data.evidence, status: "weak_match", passages: [{
        ...data.evidence.passages[0], coverage: null, source_id: null, source_version: null, version: null,
    }] } };
    assert.deepEqual(KnowledgeTestResponseSchema.parse(weak), weak);
});
