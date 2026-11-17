import { expect, test, type Page, type Route } from "@playwright/test";
import { E2E_USER, mockApi, mockDashboardApi, signInAsE2eUser } from "./support/session";

/*
 * P2 journey 2 — campaign create. A signed-in customer creates an outbound
 * campaign through the wizard and reaches the new campaign's page.
 *
 * Every request is answered here, in the shape the backend sends (the server
 * code is named beside each answer; BE/ = backend/app/). The mocked backend is
 * stateful: the campaign exists only after the wizard's POST /campaigns, and
 * its page is served from what that request carried.
 *
 * Helpers from ./support/session: signInAsE2eUser (session + page shell),
 * mockDashboardApi (shared reads such as /events), mockApi (GET answers),
 * E2E_USER (the account).
 */

test.setTimeout(120_000);

const CAMPAIGN_ID = "3f0c9b52-7a41-4e6d-9c1b-2d8e5f6a7b90";
const CAMPAIGN_NAME = "Spring Outreach Journey";
const COMPANY_NAME = "Journey Test Co";
const AGENT_NAME = "Alex";
const OPENING_OBJECTIVE = "Confirm the contact handles supplier decisions and offer a short call.";
const GREETING = "Hi, this is Alex from Journey Test Co. Do you have a moment?";
const CREATED_AT = "2026-11-17T09:00:00+00:00";

/*
 * GET /ai-options/voices: { voices: VoiceInfo[] } (BE/api/v1/endpoints/ai_options/providers.py:143,
 * VoiceInfo in BE/domain/models/ai_config.py:193-207).
 */
const VOICE = {
    id: "e2e-voice-harper",
    name: "Harper",
    language: "en",
    description: "Warm and clear",
    gender: "female",
    accent: "American",
    accent_color: "#6366f1",
    preview_text: "Hello, I am your AI voice assistant. How can I help you today?",
    provider: "cartesia",
    provider_is_public: false,
    tags: [],
    preview_url: null,
};

function json(route: Route, status: number, body: unknown) {
    return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

/** "/api/v1/campaigns/" -> "/campaigns". */
function apiPath(route: Route): string {
    return new URL(route.request().url()).pathname.replace(/^.*?\/api\/v1(?=\/|$)/, "").replace(/\/+$/, "");
}

type CreateBody = {
    name: string;
    system_prompt: string;
    voice_id: string;
    tts_provider?: string;
    goal?: string;
    company_name: string;
    agent_names: string[];
    persona_type: string;
    calling_schedule?: unknown;
};

async function mockBackend(page: Page) {
    const state = { created: null as Record<string, unknown> | null, createBodies: [] as CreateBody[], leadFieldWrites: [] as unknown[] };

    await signInAsE2eUser(page);
    await mockDashboardApi(page);
    await mockApi(page, [
        { path: "/ai-options/voices", body: { voices: [VOICE] } },
        // GET /contacts/fields (BE/api/v1/endpoints/lead_details.py:80-96): the wizard's lead-field picker.
        { path: "/contacts/fields", body: { fields: [], csv_template_headers: [] } },
        // The campaign page's own reads, in the shapes ./support/session documents for the same page
        // (PAGE_MOCKS), keyed by the new campaign's id.
        { path: `/campaigns/${CAMPAIGN_ID}/contacts`, body: { items: [], total: 0, page: 1, page_size: 100 } },
        {
            path: `/campaigns/${CAMPAIGN_ID}/stats`,
            body: {
                campaign_id: CAMPAIGN_ID, campaign_status: "draft", total_leads: 0, qualified_leads: 0,
                job_status_counts: {}, call_outcome_counts: {}, goals_achieved: 0, blocking_reason: null, activity: null,
            },
        },
        { path: "/campaigns/minutes/status", body: { allocated: 600, used_minutes: 0, remaining_minutes: 600, unlimited: false, exhausted: false } },
        { path: `/campaigns/${CAMPAIGN_ID}/knowledge`, body: { campaign_id: CAMPAIGN_ID, knowledge_mode: "none", sources: [], tree: [] } },
        { path: `/campaigns/${CAMPAIGN_ID}/contact-lists`, body: [] },
        { path: "/calls/live", body: { items: [], server_time: CREATED_AT } },
        { path: "/calls/issues", body: { items: [], server_time: CREATED_AT } },
        { path: `/campaigns/${CAMPAIGN_ID}/calls`, body: { items: [], page: 1, page_size: 20, total: 0 } },
        // GET /campaigns/{id}/readiness (BE/api/v1/endpoints/campaigns.py:1066-1078; the result is built in
        // BE/domain/services/telephony/outbound_readiness.py:47-90, "ready" at :89).
        {
            path: `/campaigns/${CAMPAIGN_ID}/readiness`,
            body: { campaign_id: CAMPAIGN_ID, ready: true, reason_code: "ready", reason: null, trunk_id: "8c2d4e6f-1a3b-4c5d-9e7f-0a1b2c3d4e5f", caller_id: "+15550100200" },
        },
        // GET /campaigns/{id}/lead-fields (BE/api/v1/endpoints/lead_details.py:247-252).
        { path: `/campaigns/${CAMPAIGN_ID}/lead-fields`, body: { fields: [] } },
    ]);

    // Registered last, so it runs first: the wizard's writes and the campaign they create.
    await page.route(
        (url) => /\/api\/v1(?:\/|$)/.test(url.pathname),
        async (route) => {
            const request = route.request();
            if (request.resourceType() === "document") return route.fallback();
            const path = apiPath(route);
            const method = request.method();

            // POST /campaigns/preview-prompt: CampaignPromptPreviewResponse (BE/api/v1/schemas/campaigns.py:392-435).
            if (method === "POST" && path === "/campaigns/preview-prompt") {
                const system_prompt = `You are ${AGENT_NAME} from ${COMPANY_NAME}.`;
                return json(route, 200, {
                    system_prompt,
                    greeting: GREETING,
                    direction: "outbound",
                    has_inbound_directive: false,
                    prompt_chars: system_prompt.length,
                    opening_mode: "agent_first",
                    campaign_guidance_chars: 0,
                    campaign_guidance_budget_chars: 4000,
                    layers: [{ key: "persona", label: "Persona", content: system_prompt }],
                    over_budget: false,
                });
            }

            // POST /campaigns: { campaign: <the inserted row> } (BE/api/v1/endpoints/campaigns.py:729-754).
            if (method === "POST" && path === "/campaigns") {
                const body = request.postDataJSON() as CreateBody;
                state.createBodies.push(body);
                state.created = {
                    id: CAMPAIGN_ID,
                    tenant_id: E2E_USER.tenant_id,
                    name: body.name,
                    description: null,
                    status: "draft",
                    direction: "outbound",
                    system_prompt: body.system_prompt,
                    voice_id: body.voice_id,
                    tts_provider: body.tts_provider ?? null,
                    goal: body.goal ?? null,
                    script_config: { company_name: body.company_name, agent_names: body.agent_names, persona_type: body.persona_type },
                    calling_config: null,
                    max_concurrent_calls: 1,
                    total_leads: 0,
                    calls_completed: 0,
                    calls_failed: 0,
                    created_at: CREATED_AT,
                    updated_at: CREATED_AT,
                };
                return json(route, 200, { campaign: state.created });
            }

            // PUT /campaigns/{id}/lead-fields: { fields } (BE/api/v1/endpoints/lead_details.py:255-375).
            if (method === "PUT" && path === `/campaigns/${CAMPAIGN_ID}/lead-fields`) {
                state.leadFieldWrites.push(request.postDataJSON());
                return json(route, 200, { fields: [] });
            }

            // GET /campaigns: { campaigns } (BE/api/v1/endpoints/campaigns.py:460-494).
            if (method === "GET" && path === "/campaigns") {
                return json(route, 200, { campaigns: state.created ? [state.created] : [] });
            }

            // GET /campaigns/{id}: { campaign, effective_schedule, progress } (BE/api/v1/endpoints/campaigns.py:888-919;
            // both extras are null when the runtime read is unavailable, :878-881). 404 until it exists (:923).
            if (method === "GET" && path === `/campaigns/${CAMPAIGN_ID}`) {
                if (!state.created) return json(route, 404, { detail: "Campaign not found" });
                return json(route, 200, { campaign: state.created, effective_schedule: null, progress: null });
            }

            return route.fallback();
        },
    );

    return state;
}

test("a signed-in customer creates an outbound campaign through the wizard and reaches its page", async ({ page }) => {
    const backend = await mockBackend(page);

    await page.goto("/campaigns/new");
    await expect(page.getByRole("heading", { level: 1, name: "Create campaign" })).toBeVisible(); // campaigns/new/page.tsx:49

    // Step 1: the basics (campaign-wizard.tsx:512-543), a voice (voice-provider-picker.tsx:241) and the brief's
    // one required field (campaign-brief-fields.tsx:72-73).
    await page.getByLabel("Campaign name").fill(CAMPAIGN_NAME);
    await page.getByLabel("Brand / company name").fill(COMPANY_NAME);
    await page.getByLabel("Representative names").fill(AGENT_NAME);
    await page.getByRole("radio", { name: `Select ${VOICE.name}` }).click();
    await page.getByLabel("Opening objective").fill(OPENING_OBJECTIVE);
    await page.getByRole("button", { name: "Next: Knowledge" }).click(); // campaign-wizard.tsx:600

    // Step 2: knowledge is optional (campaign-wizard.tsx:616, :658-661).
    await expect(page.getByText("Upload your knowledge")).toBeVisible();
    await page.getByRole("button", { name: "Next: Review" }).click(); // campaign-wizard.tsx:677

    // Step 3: the review shows what was entered and the server's opening line (campaign-wizard.tsx:685-707).
    await expect(page.getByText("How the agent will open")).toBeVisible();
    await expect(page.getByText(GREETING)).toBeVisible();
    await expect(page.getByText(COMPANY_NAME, { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Create campaign" }).click(); // campaign-wizard.tsx:725

    // The new campaign's own page (campaigns/[id]/page.tsx:503: its title is the campaign's name).
    await page.waitForURL(new RegExp(`/campaigns/${CAMPAIGN_ID}/?$`));
    await expect(page.getByRole("heading", { level: 1, name: CAMPAIGN_NAME })).toBeVisible();
    // No failed read on the new page: the app's error pop-up is an alert named "Error notification".
    await expect(page.getByRole("alert", { name: "Error notification" })).toHaveCount(0);

    // What the wizard sent: CampaignCreateRequest fields the customer typed or picked.
    expect(backend.createBodies).toHaveLength(1);
    expect(backend.createBodies[0]).toMatchObject({
        name: CAMPAIGN_NAME,
        company_name: COMPANY_NAME,
        agent_names: [AGENT_NAME],
        voice_id: VOICE.id,
        persona_type: "lead_gen",
    });
    expect(backend.leadFieldWrites).toEqual([[]]);
});
