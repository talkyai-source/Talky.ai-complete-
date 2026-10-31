import { test } from "node:test";
import assert from "node:assert/strict";

const SETTINGS_BODY = {
    server_configured: true,
    connected: true,
    status: "connected",
    connector_id: "conn-1",
    org_id: "00D1",
    username: "ops@acme.com",
    instance_url: "https://acme.my.salesforce.com",
    api_version: "v60.0",
    login_url: "https://login.salesforce.com",
    settings: { callback_campaign_id: null, log_calls: true, create_leads: false, sync_inbound: true, callback_priority: 8 },
    webhook: { callback_url: null, outbound_message_url: null, token_set: false, token_masked: null, token: null },
    last_synced_call_at: null,
};

function withFetch(handler: (url: string, init?: RequestInit) => Response) {
    const prevFetch = globalThis.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        return handler(String(url), init);
    }) as typeof fetch;
    return { calls, restore: () => (globalThis.fetch = prevFetch) };
}

test("backendApi.salesforce.settings parses the settings envelope", async () => {
    const f = withFetch(() => new Response(JSON.stringify(SETTINGS_BODY), { status: 200, headers: { "content-type": "application/json" } }));
    try {
        process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
        const { backendApi } = await import("@/lib/backend-api");
        const res = await backendApi.salesforce.settings();
        assert.equal(res.connected, true);
        assert.equal(res.settings.create_leads, false);
        assert.match(f.calls[0]!.url, /\/connectors\/salesforce\/settings$/);
    } finally {
        f.restore();
    }
});

test("backendApi.salesforce.updateSettings PUTs the diff", async () => {
    const f = withFetch(() => new Response(JSON.stringify(SETTINGS_BODY), { status: 200, headers: { "content-type": "application/json" } }));
    try {
        process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
        const { backendApi } = await import("@/lib/backend-api");
        await backendApi.salesforce.updateSettings({ callback_campaign_id: "camp-1", log_calls: false });
        assert.equal(f.calls.length, 1);
        assert.equal(f.calls[0]!.init?.method, "PUT");
        assert.match(f.calls[0]!.url, /\/connectors\/salesforce\/settings$/);
        assert.deepEqual(JSON.parse(String(f.calls[0]!.init?.body)), { callback_campaign_id: "camp-1", log_calls: false });
    } finally {
        f.restore();
    }
});

test("backendApi.salesforce.rotateWebhookToken POSTs and importPeople sends the body", async () => {
    const f = withFetch((url) => {
        if (url.endsWith("/connectors/salesforce/webhook-token")) {
            return new Response(JSON.stringify({ ...SETTINGS_BODY, webhook: { ...SETTINGS_BODY.webhook, token_set: true, token: "tok" } }), {
                status: 200,
                headers: { "content-type": "application/json" },
            });
        }
        return new Response(
            JSON.stringify({ campaign_id: "camp-1", list_id: "l1", fetched: 5, imported: 4, revived: 0, duplicates_skipped: 1, invalid: 0, errors: [] }),
            { status: 200, headers: { "content-type": "application/json" } }
        );
    });
    try {
        process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
        const { backendApi } = await import("@/lib/backend-api");
        const rotated = await backendApi.salesforce.rotateWebhookToken();
        assert.equal(rotated.webhook.token, "tok");
        assert.equal(f.calls[0]!.init?.method, "POST");

        const imported = await backendApi.salesforce.importPeople({ campaign_id: "camp-1", object_type: "Contact", limit: 50 });
        assert.equal(imported.imported, 4);
        assert.match(f.calls[1]!.url, /\/connectors\/salesforce\/import$/);
        assert.deepEqual(JSON.parse(String(f.calls[1]!.init?.body)), { campaign_id: "camp-1", object_type: "Contact", limit: 50 });
    } finally {
        f.restore();
    }
});
