import { expect, test, type Page } from "@playwright/test";

test.setTimeout(120_000);

const mockLayers = [
    { key: "opening", label: "Opening identity", content: "You are Alex, representing Northstar." },
    { key: "campaign_brief", label: "Structured campaign brief", content: "Opening objective: Earn permission for one discovery question." },
    { key: "campaign_guidance", label: "Additional campaign guidance", content: "Keep the introduction concise." },
];

async function stubCampaignDependencies(page: Page) {
    await page.addInitScript(() => {
        localStorage.setItem("talklee.auth.token", "dev-token");
    });

    await page.route(/\/(?:api\/v1\/)?(?:auth\/me|me)\/?(?:\?.*)?$/, async (route) => {
        await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                id: "usr_dev",
                email: "dev@example.com",
                name: "Dev Operator",
                role: "user",
                minutes_remaining: 100,
            }),
        });
    });
    await page.route(/\/api\/v1\/health\/?(?:\?.*)?$/, async (route) => {
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "ok" }) });
    });
    await page.route(/\/api\/v1\/events\/?(?:\?.*)?$/, async (route) => {
        await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ items: [], next_cursor: null }),
        });
    });
    await page.route(/\/api\/v1\/ai-options\/voices\/?(?:\?.*)?$/, async (route) => {
        await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                voices: [{
                    id: "voice-alex",
                    name: "Alex",
                    language: "en",
                    description: "Clear and professional",
                    gender: "male",
                    accent: "British",
                    provider: "cartesia",
                }],
            }),
        });
    });
    await page.route(/\/api\/v1\/ai-options\/config\/?(?:\?.*)?$/, async (route) => {
        await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                llm_provider: "groq",
                llm_model: "openai/gpt-oss-20b",
                llm_temperature: 0.2,
                llm_max_tokens: 512,
                stt_provider: "deepgram",
                stt_model: "nova-3",
                stt_engine: "deepgram_flux",
                stt_language: "en",
                tts_provider: "cartesia",
                tts_model: "sonic-3",
                tts_voice_id: "voice-alex",
                tts_sample_rate: 24000,
                pipeline_mode: "cascaded",
            }),
        });
    });
    await page.route(/\/api\/v1\/contacts\/fields\/?(?:\?.*)?$/, async (route) => {
        await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                fields: [
                    { key: "email", label: "Email", field_type: "email", aliases: [], agent_usable: true, max_len: 320 },
                    { key: "job_title", label: "Job title", field_type: "text", aliases: [], agent_usable: true, max_len: 160 },
                ],
                csv_template_headers: ["email", "job_title"],
            }),
        });
    });
    await page.route(/\/api\/v1\/campaigns\/preview-prompt\/?(?:\?.*)?$/, async (route) => {
        await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                system_prompt: mockLayers.map((layer) => layer.content).join("\n\n"),
                greeting: "Hi, this is Alex from Northstar. Is now a good time?",
                direction: "outbound",
                opening_mode: "agent_first",
                has_inbound_directive: false,
                prompt_chars: mockLayers.reduce((total, layer) => total + layer.content.length, 4),
                campaign_guidance_chars: 148,
                campaign_guidance_budget_chars: 6000,
                over_budget: false,
                layers: mockLayers,
            }),
        });
    });
}

async function openConfiguredBrief(page: Page) {
    await stubCampaignDependencies(page);
    await page.goto("http://localhost:3100/campaigns/new/", { waitUntil: "domcontentloaded" });

    await page.getByLabel("Campaign name").fill("Operations discovery");
    await page.getByLabel("Brand / company name").fill("Northstar");
    await page.getByLabel("Representative names").fill("Alex");
    await page.getByLabel(/Opening objective/).fill("Earn permission for one discovery question.");
    await page.getByLabel("Decision-maker role").fill("Head of Operations");
    await page.getByLabel("Schedule callback").check();
    await page.getByRole("checkbox", { name: /Transfer to a human/ }).check();
    await page.getByLabel(/Transfer destination/).fill("Northstar sales queue");
    await page.getByLabel("Maximum objection attempts").selectOption("3");
    await page.getByLabel("Additional campaign guidance").fill("Keep the introduction concise.");
    await page.getByRole("radio", { name: /Select Alex/ }).click();
}

test("structured campaign brief stays readable without horizontal overflow", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openConfiguredBrief(page);

    await expect(page.getByRole("heading", { name: "Campaign brief" })).toBeVisible();
    await expect(page.getByText("Live prompt layer")).toBeVisible();
    await expect(page.getByLabel(/Transfer destination/)).toHaveValue("Northstar sales queue");
    await expect(page.getByText(/Nothing is trimmed automatically/)).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);

    await page.screenshot({ path: "output/playwright/campaign-brief-desktop.png", fullPage: true });
});

test("preview exposes exact prompt layers as keyboard-operable disclosures on mobile", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openConfiguredBrief(page);

    await page.getByRole("button", { name: /Next: Knowledge/ }).click();
    await expect(page.getByRole("heading", { name: /Contact details the agent may capture/ })).toBeVisible();
    await page.getByRole("button", { name: /Next: Review/ }).click();
    await expect(page.getByText("Composed prompt layers")).toBeVisible();

    const disclosures = page.locator("[data-testid='prompt-layer-preview'] details");
    await expect(disclosures).toHaveCount(mockLayers.length);
    await expect(disclosures.first()).not.toHaveAttribute("open", "");
    await disclosures.first().locator("summary").focus();
    await page.keyboard.press("Enter");
    await expect(disclosures.first()).toHaveAttribute("open", "");

    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    await page.screenshot({ path: "output/playwright/campaign-brief-mobile.png", fullPage: true });
});
