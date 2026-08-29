import assert from "node:assert/strict";
import { test } from "node:test";

import { INBOUND_RETRY_WINDOW_MS, inboundApi, inboundErrorCode, parseInboundCampaign, parseInboundCampaignList, parseInboundRuntimeCapabilities, parsePhoneNumberAvailability, type InboundCampaignInput } from "@/lib/inbound-api";

const serverCampaign = {
    id: "in-1",
    name: "Main line",
    status: "draft",
    version: 4,
    did_number: "+14155550123",
    campaign_id: "campaign-1",
    campaign_name: "Reception agent",
    sip_trunk_id: "trunk-1",
    sip_trunk_name: "Primary inbound",
    timezone: "America/New_York",
    after_hours_action: "voicemail",
    transfer_number: null,
    recording_enabled: true,
    consent_message: "This call may be recorded.",
    readiness: {
        ready: false,
        checks: [{ key: "trunk_ready", label: "Trunk ready", passed: false, detail: "Activate the trunk." }],
    },
    active_at: null,
    created_at: "2026-08-20T10:00:00Z",
    updated_at: "2026-08-20T11:00:00Z",
};

const input: InboundCampaignInput = {
    name: " Main line ",
    did_number: "+14155550123",
    campaign_id: "campaign-1",
    sip_trunk_id: "trunk-1",
    opening_mode: "caller_first",
    greeting: "",
    silence_timeout_seconds: 8,
    timezone: "UTC",
    weekly_schedule: [],
    holiday_policy: "closed",
    after_hours_action: "voicemail",
    after_hours_message: null,
    transfer_number: null,
    transfer_enabled: false,
    transfer_destinations: [],
    transfer_failure_action: "voicemail",
    max_transfer_attempts: 2,
    max_transfer_hops: 2,
    max_call_duration_seconds: 1800,
    recording_enabled: false,
    consent_message: null,
};

test("inbound parser accepts the production envelope and masks the DID", () => {
    const campaign = parseInboundCampaign({ inbound_campaign: { ...serverCampaign, qualification_config: { silence_timeout_seconds: 19 } } });
    assert.equal(campaign.id, "in-1");
    assert.equal(campaign.phone_number?.e164, serverCampaign.did_number);
    assert.notEqual(campaign.phone_number?.masked_number, serverCampaign.did_number);
    assert.match(campaign.phone_number?.masked_number ?? "", /23$/);
    assert.equal(campaign.readiness.ready, false);
    assert.equal(campaign.readiness.checks[0]?.passed, false);
    assert.equal(campaign.silence_timeout_seconds, 19);
    assert.equal(parseInboundCampaignList({ items: [serverCampaign] }).length, 1);
});

test("verified phone inventory excludes assigned and unverified numbers", () => {
    const numbers = parsePhoneNumberAvailability({
        phone_numbers: [
            { id: "did-1", e164: "+14155550123", status: "verified", available: true, assignment_status: "available" },
            { id: "did-2", e164: "+14155550124", status: "pending_verification", available: true, assignment_status: "available" },
            { id: "did-3", e164: "+14155550125", status: "verified", available: false, assignment_status: "assigned" },
        ],
    });
    assert.equal(numbers.length, 1);
    assert.equal(numbers[0]?.id, "did-1");
});

test("readiness fails closed without an explicit server ready flag", () => {
    const campaign = parseInboundCampaign({
        ...serverCampaign,
        readiness: { checks: [{ key: "looks_ready", label: "Looks ready", passed: true, detail: "Client-visible checks pass." }] },
    });
    assert.equal(campaign.readiness.ready, false);
});

test("transfer capability parsing requires all explicit server gates", () => {
    assert.deepEqual(parseInboundRuntimeCapabilities({
        transfer_runtime_available: true,
        transfer_platform_enabled: true,
        transfer_configuration_available: true,
    }), {
        transfer_runtime_available: true,
        transfer_platform_enabled: true,
        transfer_configuration_available: true,
    });
    assert.equal(parseInboundRuntimeCapabilities({
        transfer_runtime_available: "true",
        transfer_platform_enabled: true,
        transfer_configuration_available: true,
    }).transfer_configuration_available, false);
    assert.equal(parseInboundRuntimeCapabilities({
        transfer_runtime_available: true,
        transfer_platform_enabled: false,
        transfer_configuration_available: true,
    }).transfer_configuration_available, false);
    assert.equal(inboundErrorCode({ code: "transfer_platform_disabled" }), "transfer_platform_disabled");
    assert.equal(inboundErrorCode({ code: true }), null);
});

test("transfer capability request is scoped to the edited inbound config", async () => {
    const previousFetch = globalThis.fetch;
    const urls: string[] = [];
    globalThis.fetch = (async (url: RequestInfo | URL) => {
        urls.push(String(url));
        return new Response(JSON.stringify({
            transfer_runtime_available: true,
            transfer_platform_enabled: true,
            transfer_configuration_available: true,
        }), {
            status: 200,
            headers: { "content-type": "application/json" },
        });
    }) as typeof fetch;
    try {
        await inboundApi.getCapabilities("33333333-3333-3333-3333-333333333333");
        await inboundApi.getCapabilities();
        assert.match(urls[0] ?? "", /config_id=33333333-3333-3333-3333-333333333333/);
        assert.doesNotMatch(urls[1] ?? "", /config_id=/);
    } finally {
        globalThis.fetch = previousFetch;
    }
});

test("archived campaign inventory is requested only for the archived view", async () => {
    const previousFetch = globalThis.fetch;
    const urls: string[] = [];
    globalThis.fetch = (async (url: RequestInfo | URL) => {
        urls.push(String(url));
        return new Response(JSON.stringify({ items: [serverCampaign] }), {
            status: 200,
            headers: { "content-type": "application/json" },
        });
    }) as typeof fetch;
    try {
        await inboundApi.list();
        await inboundApi.list({ includeArchived: true });
        assert.doesNotMatch(urls[0] ?? "", /include_archived/);
        assert.match(urls[1] ?? "", /include_archived=true/);
    } finally {
        globalThis.fetch = previousFetch;
    }
});

test("create sends only the confirmed inbound contract and an idempotency key", async () => {
    const previousFetch = globalThis.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        return new Response(JSON.stringify(serverCampaign), { status: 201, headers: { "content-type": "application/json" } });
    }) as typeof fetch;
    try {
        await inboundApi.create(input, input.did_number);
        const body = JSON.parse(String(calls[0]?.init?.body)) as Record<string, unknown>;
        assert.equal(calls[0]?.init?.method, "POST");
        assert.equal(body.name, "Main line");
        assert.equal(body.did_number, "+14155550123");
        assert.deepEqual(body.qualification_config, {});
        assert.deepEqual(Object.keys(body).sort(), ["after_hours_action", "business_hours", "campaign_id", "consent_message", "did_number", "greeting", "name", "opening_mode", "qualification_config", "recording_enabled", "recording_policy", "sip_trunk_id", "timezone", "transfer_number", "transfer_policy"].sort());
        assert.ok(calls[0]?.init?.headers && new Headers(calls[0].init.headers).get("Idempotency-Key"));

        await inboundApi.create({ ...input, system_prompt: " Answer as reception. ", voice_id: " voice-live-1 ", purpose: "New enquiries", agent_persona: "Warm and concise", silence_timeout_seconds: 12 }, input.did_number);
        const overrideBody = JSON.parse(String(calls[1]?.init?.body)) as { qualification_config?: Record<string, unknown> };
        assert.deepEqual(overrideBody.qualification_config, {
            purpose: "New enquiries",
            persona: "Warm and concise",
            system_prompt: "Answer as reception.",
            voice_id: "voice-live-1",
            silence_timeout_seconds: 12,
        });
    } finally {
        globalThis.fetch = previousFetch;
    }
});

test("an ambiguous retry reuses its idempotency key and a later operation gets a fresh key", async () => {
    const previousFetch = globalThis.fetch;
    const keys: Array<string | null> = [];
    let attempt = 0;
    globalThis.fetch = (async (_url: RequestInfo | URL, init?: RequestInit) => {
        keys.push(new Headers(init?.headers).get("Idempotency-Key"));
        attempt += 1;
        if (attempt === 1) throw new TypeError("connection closed after request upload");
        return new Response(JSON.stringify(serverCampaign), { status: 201, headers: { "content-type": "application/json" } });
    }) as typeof fetch;
    try {
        await assert.rejects(inboundApi.create(input, input.did_number), /connection closed/i);
        await inboundApi.create(input, input.did_number);
        await inboundApi.create(input, input.did_number);

        assert.ok(keys[0]);
        assert.equal(keys[1], keys[0], "the explicit retry must replay the ambiguous attempt");
        assert.notEqual(keys[2], keys[1], "a completed operation must not leak its key into a new mutation");
    } finally {
        globalThis.fetch = previousFetch;
    }
});

test("an expired ambiguous retry is blocked before the server claim can roll over", async () => {
    const previousFetch = globalThis.fetch;
    const previousNow = Date.now;
    let now = 2_000_000_000_000;
    let requests = 0;
    Date.now = () => now;
    globalThis.fetch = (async () => {
        requests += 1;
        throw new TypeError("ambiguous network failure");
    }) as typeof fetch;
    const expiredInput = { ...input, name: "Expiry safety test" };
    try {
        await assert.rejects(inboundApi.create(expiredInput, expiredInput.did_number), /ambiguous network failure/i);
        now += INBOUND_RETRY_WINDOW_MS + 1;
        await assert.rejects(inboundApi.create(expiredInput, expiredInput.did_number), /safe retry window expired/i);
        assert.equal(requests, 1, "an expired retry must not reach the server with a claim that can roll over");
    } finally {
        Date.now = previousNow;
        globalThis.fetch = previousFetch;
    }
});

test("update uses PUT and includes the stale-edit token", async () => {
    const previousFetch = globalThis.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        return new Response(JSON.stringify(serverCampaign), { status: 200, headers: { "content-type": "application/json" } });
    }) as typeof fetch;
    try {
        await inboundApi.update("in-1", input, serverCampaign.version);
        const body = JSON.parse(String(calls[0]?.init?.body)) as Record<string, unknown>;
        assert.equal(calls[0]?.init?.method, "PUT");
        assert.equal(body.expected_version, serverCampaign.version);
        assert.equal(body.did_number, undefined);
        assert.equal(body.sip_trunk_id, undefined);
    } finally {
        globalThis.fetch = previousFetch;
    }
});

test("assignment uses the explicit audited endpoint and optimistic version", async () => {
    const previousFetch = globalThis.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        return new Response(JSON.stringify({ ...serverCampaign, version: 6 }), { status: 200, headers: { "content-type": "application/json" } });
    }) as typeof fetch;
    try {
        const assigned = await inboundApi.assign("in-1", {
            didNumber: "+14155550199",
            sipTrunkId: "trunk-2",
            expectedVersion: 5,
            reason: " Routing changed by an administrator. ",
        });
        const body = JSON.parse(String(calls[0]?.init?.body)) as Record<string, unknown>;
        assert.match(calls[0]?.url ?? "", /\/inbound-campaigns\/in-1\/assign$/);
        assert.equal(calls[0]?.init?.method, "POST");
        assert.deepEqual(body, {
            did_number: "+14155550199",
            sip_trunk_id: "trunk-2",
            expected_version: 5,
            reason: "Routing changed by an administrator.",
        });
        assert.ok(calls[0]?.init?.headers && new Headers(calls[0].init.headers).get("Idempotency-Key"));
        assert.equal(assigned.version, 6);
    } finally {
        globalThis.fetch = previousFetch;
    }
});

test("archive uses the confirmed lifecycle endpoint and optimistic version", async () => {
    const previousFetch = globalThis.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        return new Response(JSON.stringify({ ...serverCampaign, status: "archived", version: 5 }), { status: 200, headers: { "content-type": "application/json" } });
    }) as typeof fetch;
    try {
        const archived = await inboundApi.archive("in-1", serverCampaign.version);
        const body = JSON.parse(String(calls[0]?.init?.body)) as Record<string, unknown>;
        assert.match(calls[0]?.url ?? "", /\/inbound-campaigns\/in-1\/archive$/);
        assert.equal(calls[0]?.init?.method, "POST");
        assert.equal(body.expected_version, serverCampaign.version);
        assert.equal(archived.status, "archived");
        assert.equal(archived.version, 5);
    } finally {
        globalThis.fetch = previousFetch;
    }
});
