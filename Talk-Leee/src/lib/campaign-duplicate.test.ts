import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";
import { api } from "@/lib/api";
import {
    CampaignDuplicateBlockedError,
    DUPLICATE_KNOWLEDGE_BLOCKED_TEXT,
    duplicateCampaign,
    duplicateCampaignPayload,
    duplicateLeadFieldsFailedText,
    duplicateSuccessText,
} from "@/lib/campaign-duplicate";
import { dashboardApi, type Campaign, type CampaignCreate } from "@/lib/dashboard-api";
import { ApiClientError, createHttpClient } from "@/lib/http-client";
import { leadDetailsApi, type CampaignLeadField } from "@/lib/lead-details-api";

afterEach(() => { mock.restoreAll(); });

const brief = { objective: "Book a demo", audience: "Shop owners", form_action: { fields: ["email"] } };

// Every field a create can carry is filled, next to everything it cannot
// carry: counters, pacing, the line pin, concurrency and retry settings.
const source = {
    id: "src-1", name: "Spring push", description: "Q2 outreach", status: "running", direction: "outbound",
    system_prompt: "legacy prompt", voice_id: "voice-9", tts_provider: "cartesia", goal: "Book a demo",
    max_concurrent_calls: 7, total_leads: 250, calls_completed: 120, calls_failed: 9,
    created_at: "2026-01-01T00:00:00Z", started_at: "2026-01-02T00:00:00Z", completed_at: "2026-01-03T00:00:00Z",
    retry_failed: true, max_retries: 3,
    script_config: {
        pipeline_mode: "realtime", realtime_model: "gpt-realtime", realtime_voice: "marin",
        realtime_settings: { turn_detection: { type: "semantic_vad", eagerness: "low" }, noise_reduction: "near_field" },
        realtime_prompt: { persona: "sales", goal: "Qualify the lead", instructions: "Be brief", opening_greeting: "Hi there" },
        persona_type: "customer_support", company_name: "Acme", agent_names: ["Ava", "Ben"],
        agent_name_genders: { Ava: "female", Ben: "male" },
        campaign_slots: { product: "Widgets", faqs: [{ issue: "Late order", solution: "Refund" }], points: ["Fast", "Cheap"] },
        additional_instructions: "Never promise refunds", campaign_brief: brief, knowledge_driven: true,
    },
    calling_config: {
        timezone: "Europe/London", time_window_start: "09:00", time_window_end: "17:00", allowed_days: [0, 1, 2, 3, 4],
        ignore_schedule: false, batch_size: 5, call_gap_seconds: 30, trunk_id: "trunk-1",
    },
} as unknown as Campaign;

const leadFields: CampaignLeadField[] = [
    { field_key: "email", label: "Email", field_type: "email", is_required: true, agent_visible: true, user_visible: true, options: null, sort_order: 0 },
    { field_key: "budget", label: "Budget", field_type: "select", is_required: false, agent_visible: true, user_visible: false, options: ["Low", "High"], sort_order: 1 },
];

const created = { ...source, id: "new-1", name: "Spring push - Copy", status: "draft", total_leads: 0, calls_completed: 0, calls_failed: 0 } as Campaign;

type Calls = { knowledge: string[]; reads: string[]; bodies: CampaignCreate[]; fieldReads: string[]; fieldWrites: Array<{ id: string; fields: CampaignLeadField[] }> };

function wire(options: {
    from?: Campaign;
    sources?: unknown[];
    knowledgeFails?: boolean;
    knowledgeError?: unknown;
    createFails?: string;
    fieldReadFails?: boolean;
    fieldWriteFails?: boolean;
} = {}): Calls {
    const calls: Calls = { knowledge: [], reads: [], bodies: [], fieldReads: [], fieldWrites: [] };
    mock.method(api, "getCampaignKnowledge", async (id: string) => {
        calls.knowledge.push(id);
        if (options.knowledgeFails) throw new Error("knowledge 503");
        if (options.knowledgeError) throw options.knowledgeError;
        return { campaign_id: id, knowledge_mode: "none", sources: options.sources ?? [], tree: [] };
    });
    mock.method(dashboardApi, "getCampaign", async (id: string) => {
        calls.reads.push(id);
        return { campaign: options.from ?? source };
    });
    mock.method(dashboardApi, "createCampaign", async (body: CampaignCreate) => {
        calls.bodies.push(body);
        if (options.createFails) throw new Error(options.createFails);
        return { campaign: created };
    });
    mock.method(leadDetailsApi, "campaignFields", async (id: string) => {
        calls.fieldReads.push(id);
        if (options.fieldReadFails) throw new Error("lead-fields 500");
        return { fields: leadFields };
    });
    mock.method(leadDetailsApi, "setCampaignFields", async (id: string, fields: CampaignLeadField[]) => {
        calls.fieldWrites.push({ id, fields });
        if (options.fieldWriteFails) throw new Error("lead-fields 403");
        return { fields };
    });
    return calls;
}

/** The body as it goes on the wire: keys set to undefined are not sent. */
function wireBody(body: CampaignCreate): Record<string, unknown> {
    return JSON.parse(JSON.stringify(body)) as Record<string, unknown>;
}

test("the POST body carries every copyable field from a fully filled source", async () => {
    const calls = wire();
    await duplicateCampaign("src-1");
    assert.deepEqual(calls.reads, ["src-1"]);
    assert.equal(calls.bodies.length, 1);
    assert.deepEqual(wireBody(calls.bodies[0]!), {
        pipeline_mode: "realtime",
        realtime_model: "gpt-realtime",
        realtime_voice: "marin",
        realtime_settings: { turn_detection: { type: "semantic_vad", eagerness: "low" }, noise_reduction: "near_field" },
        realtime_prompt: { persona: "sales", goal: "Qualify the lead", instructions: "Be brief", opening_greeting: "Hi there" },
        name: "Spring push - Copy",
        description: "Q2 outreach",
        system_prompt: "Never promise refunds",
        voice_id: "voice-9",
        tts_provider: "cartesia",
        goal: "Book a demo",
        persona_type: "customer_support",
        company_name: "Acme",
        agent_names: ["Ava", "Ben"],
        agent_name_genders: { Ava: "female", Ben: "male" },
        campaign_slots: { product: "Widgets", faqs: [{ issue: "Late order", solution: "Refund" }], points: ["Fast", "Cheap"] },
        campaign_brief: brief,
        knowledge_driven: true,
        direction: "outbound",
        calling_schedule: {
            timezone: "Europe/London", time_window_start: "09:00", time_window_end: "17:00",
            allowed_days: [0, 1, 2, 3, 4], ignore_schedule: false,
        },
    });
});

test("the POST body never carries leads, counters, status, pacing, the line, concurrency or retries", async () => {
    const calls = wire();
    await duplicateCampaign("src-1");
    const body = wireBody(calls.bodies[0]!);
    for (const key of [
        "id", "status", "total_leads", "calls_completed", "calls_failed", "created_at", "started_at", "completed_at",
        "max_concurrent_calls", "retry_failed", "max_retries", "calling_config", "script_config",
    ]) {
        assert.equal(key in body, false, `${key} must not be sent`);
    }
    const schedule = body.calling_schedule as Record<string, unknown>;
    for (const key of ["batch_size", "call_gap_seconds", "trunk_id"]) {
        assert.equal(key in schedule, false, `${key} must not be sent`);
    }
});

test("a source saved without additional instructions sends its top-level instructions, as the edit page does", () => {
    const legacy = { ...source, description: undefined, tts_provider: null, script_config: { persona_type: "lead_gen", company_name: "Acme", agent_names: ["Ava"] }, calling_config: null } as unknown as Campaign;
    const body = wireBody(duplicateCampaignPayload(legacy));
    assert.equal(body.system_prompt, "legacy prompt");
    assert.equal("description" in body, false);
    assert.equal("tts_provider" in body, false);
    assert.equal("agent_name_genders" in body, false);
    assert.deepEqual(body.campaign_slots, {});
    assert.equal(body.knowledge_driven, false);
});

test("an inbound source is copied as inbound and sends no calling schedule", () => {
    const body = wireBody(duplicateCampaignPayload({ ...source, direction: "inbound" } as Campaign));
    assert.equal(body.direction, "inbound");
    assert.equal("calling_schedule" in body, false);
});

test("the lead fields are copied from the source to the new campaign", async () => {
    const calls = wire();
    const result = await duplicateCampaign("src-1");
    assert.deepEqual(calls.fieldReads, ["src-1"]);
    assert.deepEqual(calls.fieldWrites, [{ id: "new-1", fields: leadFields }]);
    assert.equal(result.leadFieldsCopied, true);
    assert.equal(result.campaign.id, "new-1");
});

test("full success returns the exact success message", async () => {
    wire();
    const result = await duplicateCampaign("src-1");
    assert.equal(
        result.message,
        "Duplicated as “Spring push - Copy”. These settings use their defaults on the copy: pacing, caller line, concurrent calls and retries.",
    );
    assert.equal(result.message, duplicateSuccessText("Spring push - Copy"));
});

test("a failed lead-fields write still returns the copy, with the exact lead-fields message", async () => {
    const calls = wire({ fieldWriteFails: true });
    const result = await duplicateCampaign("src-1");
    assert.equal(calls.bodies.length, 1);
    assert.equal(result.campaign.id, "new-1");
    assert.equal(result.leadFieldsCopied, false);
    assert.equal(
        result.message,
        "Duplicated as “Spring push - Copy”, but its lead fields could not be copied. Set them on the copy's edit page.",
    );
    assert.equal(result.message, duplicateLeadFieldsFailedText("Spring push - Copy"));
});

test("a failed lead-fields read is reported the same way and writes nothing to the copy", async () => {
    const calls = wire({ fieldReadFails: true });
    const result = await duplicateCampaign("src-1");
    assert.equal(result.leadFieldsCopied, false);
    assert.equal(result.message, duplicateLeadFieldsFailedText("Spring push - Copy"));
    assert.deepEqual(calls.fieldWrites, []);
});

test("a refused create rejects with the server's message and copies no lead fields", async () => {
    const serverMessage = "Voice 'voice-9' is not available for TTS provider 'cartesia'. Pick a matching voice or change the provider.";
    const calls = wire({ createFails: serverMessage });
    await assert.rejects(duplicateCampaign("src-1"), (err: Error) => err.message === serverMessage);
    assert.deepEqual(calls.fieldReads, []);
    assert.deepEqual(calls.fieldWrites, []);
});

test("a source with a knowledge source is refused with the exact sentence and nothing is created", async () => {
    const calls = wire({ sources: [{ id: "k1", filename: "faq.pdf", token_count: 10, version: 1, status: "ready", created_at: "2026-01-01T00:00:00Z" }] });
    await assert.rejects(duplicateCampaign("src-1"), (err: Error) => (
        err instanceof CampaignDuplicateBlockedError
        && err.message === "Duplicate isn't available yet for campaigns with knowledge files."
        && err.message === DUPLICATE_KNOWLEDGE_BLOCKED_TEXT
    ));
    assert.deepEqual(calls.knowledge, ["src-1"]);
    assert.deepEqual(calls.reads, []);
    assert.deepEqual(calls.bodies, []);
});

test("a failed knowledge read is refused with the same sentence and nothing is created", async () => {
    const calls = wire({ knowledgeFails: true });
    await assert.rejects(duplicateCampaign("src-1"), (err: Error) => (
        err instanceof CampaignDuplicateBlockedError && err.message === DUPLICATE_KNOWLEDGE_BLOCKED_TEXT
    ));
    assert.deepEqual(calls.bodies, []);
});

/** The error the app's HTTP client raises for this exact backend response. */
async function errorFromServer(status: number, body: unknown): Promise<ApiClientError> {
    const holder = globalThis as unknown as { fetch: unknown };
    const prevFetch = holder.fetch;
    holder.fetch = async () => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
    try {
        await createHttpClient({ baseUrl: "http://example.test" }).request({ path: "/campaigns/src-1/knowledge", method: "GET" });
    } catch (err) {
        assert.equal(err instanceof ApiClientError, true);
        return err as ApiClientError;
    } finally {
        holder.fetch = prevFetch;
    }
    throw new Error("the request was expected to fail");
}

// backend campaign_knowledge.py:70 through core/error_handlers.py: a string
// detail becomes the envelope message and the code is the 404 default.
const FEATURE_OFF_BODY = { error: { code: "not_found", message: "Campaign knowledge is not enabled", details: null, request_id: "req-1" } };

test("the knowledge feature being off counts as no knowledge: the copy is made", async () => {
    const featureOff = await errorFromServer(404, FEATURE_OFF_BODY);
    assert.equal(featureOff.status, 404);
    assert.equal(featureOff.message, "Campaign knowledge is not enabled");
    const calls = wire({ knowledgeError: featureOff });
    const result = await duplicateCampaign("src-1");
    assert.deepEqual(calls.knowledge, ["src-1"]);
    assert.equal(calls.bodies.length, 1);
    assert.equal(result.campaign.id, "new-1");
    assert.equal(result.message, duplicateSuccessText("Spring push - Copy"));
});

test("every other failed knowledge read is still refused with the sentence", async () => {
    const others = [
        await errorFromServer(404, { error: { code: "campaign_not_found", message: "Campaign not found", details: null, request_id: "req-2" } }),
        await errorFromServer(404, { error: { code: "not_found", message: "Not Found", details: null, request_id: "req-3" } }),
        await errorFromServer(404, { detail: "Not Found" }),
        await errorFromServer(403, { error: { code: "knowledge_access_denied", message: "Knowledge access denied", details: { required: "campaigns:read" }, request_id: "req-4" } }),
        await errorFromServer(400, { error: { code: "bad_request", message: "Campaign knowledge is not enabled", details: null, request_id: "req-5" } }),
        new Error("Campaign knowledge is not enabled"),
    ];
    for (const failure of others) {
        mock.restoreAll();
        const calls = wire({ knowledgeError: failure });
        await assert.rejects(duplicateCampaign("src-1"), (err: Error) => (
            err instanceof CampaignDuplicateBlockedError && err.message === DUPLICATE_KNOWLEDGE_BLOCKED_TEXT
        ));
        assert.deepEqual(calls.bodies, []);
    }
});
