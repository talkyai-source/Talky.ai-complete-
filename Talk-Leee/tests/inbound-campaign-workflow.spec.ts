import { expect, test, type Page } from "@playwright/test";

// Two uncompiled routes on a cold `next dev` server (/inbound-campaigns/new,
// then the /inbound-campaigns/[id] detail route) plus the underlying
// /api/v1/[...path] proxy route itself can each take 15-20s to compile on
// first hit — the same reason tests/demo.smoke.spec.ts and
// tests/responsive.overlap.spec.ts both raise this above the 30s default.
test.setTimeout(180_000);

/**
 * End-to-end coverage for the inbound campaign create → configure → activate
 * flow, entirely against mocked responses (`page.route`), the same
 * interception mechanism `tests/responsive.overlap.spec.ts` and
 * `tests/demo.smoke.spec.ts` already use for their own fixtures — no live
 * backend is reachable from this harness.
 *
 * This spec stops at activation. It does not attempt to simulate an inbound
 * telephone call reaching the number: that requires a real SIP trunk,
 * Asterisk, and carrier — none of which exist in a Playwright run against
 * `npm run dev`. Faking a call event here would assert behavior nobody has
 * observed the real system perform, which is exactly the failure mode this
 * task was written to avoid. Live-call coverage remains
 * `docs/inbound/INBOUND-TEST-MATRIX-AND-SCORECARD.md`'s job, scored on a
 * live/staging pass, not from a desk.
 */

const baseUrl = "http://127.0.0.1:3100";
const campaignId = "campaign-e2e-1";

const baseCampaignFixture = {
    id: "camp-e2e-1",
    name: "Support Line AI",
    status: "active",
    direction: "inbound",
    system_prompt: "You help callers with support questions.",
    voice_id: "voice-001",
    max_concurrent_calls: 5,
    total_leads: 0,
    calls_completed: 0,
    calls_failed: 0,
    created_at: "2026-08-01T00:00:00Z",
};

const trunkFixture = {
    id: "trunk-e2e-1",
    trunk_name: "Primary inbound trunk",
    direction: "inbound",
    is_active: true,
    runtime_ready: true,
    runtime_status_detail: "Registration heartbeat received 4s ago.",
};

const numberFixture = {
    id: "num-e2e-1",
    e164: "+14155550100",
    label: "Main line",
    verification_status: "verified",
};

const voiceFixture = [
    {
        id: "voice-001",
        name: "Avery",
        language: "en",
        description: "Neutral support voice",
        provider: "cartesia",
        accent_color: "#64748B",
        preview_text: "Hello, this is a preview voice.",
        tags: ["neutral"],
    },
];

/** Readiness immediately after create: the trunk has not yet proved fresh runtime state. */
const readinessBlocked = {
    ready: false,
    checks: [{ key: "trunk_ready", label: "Inbound SIP trunk runtime healthy", passed: false, detail: "Waiting on the next Asterisk registration heartbeat." }],
    blockers: [{ code: "trunk_ready", message: "Trunk has not yet proven fresh runtime state", remediation: "Wait for the trunk's next registration heartbeat, then refresh." }],
};

/** Readiness after the heartbeat lands: every check now passes. */
const readinessReady = {
    ready: true,
    checks: [{ key: "trunk_ready", label: "Inbound SIP trunk runtime healthy", passed: true, detail: "Registration heartbeat received 2s ago." }],
    blockers: [],
};

function campaignFixture(overrides: Record<string, unknown>) {
    return {
        id: campaignId,
        name: "Support line",
        status: "draft",
        campaign_id: baseCampaignFixture.id,
        campaign_name: baseCampaignFixture.name,
        sip_trunk_id: trunkFixture.id,
        sip_trunk_name: trunkFixture.trunk_name,
        did_number: numberFixture.e164,
        phone_number: { id: numberFixture.id, e164: numberFixture.e164, verification_status: "verified", assignment_status: "assigned", version: 1, available: false },
        opening_mode: "caller_first",
        greeting: "",
        silence_timeout_seconds: 8,
        timezone: "UTC",
        after_hours_action: "hangup",
        transfer_enabled: false,
        transfer_destinations: [],
        recording_enabled: false,
        version: 1,
        config_version: 1,
        created_at: "2026-09-04T00:00:00Z",
        updated_at: "2026-09-04T00:00:00Z",
        readiness: readinessBlocked,
        ...overrides,
    };
}

async function jsonRoute(page: Page, pattern: RegExp, body: unknown, method = "GET") {
    await page.route(pattern, async (route) => {
        const req = route.request();
        if (req.resourceType() === "document") return route.continue();
        if (req.method() !== method) return route.continue();
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    });
}

/** Everything the create form and the detail page need before a live backend would answer. */
async function stubInboundDependencies(page: Page) {
    await page.addInitScript(() => {
        localStorage.setItem("talklee.auth.token", "e2e-token");
    });
    await page.context().addCookies([{ name: "talklee_auth_token", value: "e2e-token", url: baseUrl }]);

    await jsonRoute(page, /\/rbac\/users\/me\/permissions\/?(\?.*)?$/, { permissions: ["platform:admin"], role: "platform_admin" });
    await jsonRoute(page, /\/campaigns\/?(\?.*)?$/, { campaigns: [baseCampaignFixture] });
    await jsonRoute(page, /\/campaigns\/minutes\/status\/?(\?.*)?$/, { allocated: 500, used_minutes: 10, remaining_minutes: 490, unlimited: false, exhausted: false });
    await jsonRoute(page, /\/telephony\/sip\/trunks\/?(\?.*)?$/, [trunkFixture]);
    await jsonRoute(page, /\/tenant-phone-numbers\/?(\?.*)?$/, { items: [numberFixture] });
    await jsonRoute(page, /\/inbound-campaigns\/dids\/availability\/?(\?.*)?$/, { available: true });
    await jsonRoute(page, /\/inbound-campaigns\/capabilities\/?(\?.*)?$/, { transfer_runtime_available: false, transfer_platform_enabled: false, transfer_configuration_available: false });
    for (const pattern of [/\/ai-options\/voices\/?(\?.*)?$/, /\/ai\/voices\/?(\?.*)?$/, /\/voices\/?(\?.*)?$/]) {
        await jsonRoute(page, pattern, voiceFixture);
    }
}

test.describe("Inbound campaign: create → configure → activate (mocked, no live backend)", () => {
    test("a new campaign can be created, its readiness re-checked, and activated once the server reports ready", async ({ page }) => {
        await stubInboundDependencies(page);

        // Registered before any navigation: the client redirects itself onto
        // the detail route immediately after create resolves, and that route
        // must already be interceptable or the first GETs race an unmocked
        // request instead of this fixture.
        await jsonRoute(page, /\/inbound-campaigns$/, campaignFixture({}), "POST");
        await jsonRoute(page, new RegExp(`/inbound-campaigns/${campaignId}$`), campaignFixture({}), "GET");
        await jsonRoute(page, new RegExp(`/inbound-campaigns/${campaignId}/readiness$`), readinessBlocked, "GET");

        await page.goto("/inbound-campaigns/new", { waitUntil: "domcontentloaded" });
        // A generous timeout here, not everywhere: this is the first hit on an
        // uncompiled route on a cold `next dev` server, which this sandbox has
        // measured at 15-20s (see the 180s test.setTimeout comment above).
        await expect(page.getByRole("heading", { name: "Number and routing" })).toBeVisible({ timeout: 60_000 });

        await page.locator("#inbound-name").fill("Support line");
        await page.locator("#inbound-campaign_id").selectOption(baseCampaignFixture.id);
        await page.locator('input[name="verified-phone-number"]').check();
        await page.locator("#inbound-sip_trunk_id").selectOption(trunkFixture.id);

        // after_hours_action defaults to "hangup" (always runtime-supported) and
        // opening_mode defaults to "caller_first" (no greeting required), so the
        // rest of the form is submittable unmodified — see
        // inbound-validation.ts:initialInboundCampaignInput.
        await page.getByRole("button", { name: "Create inactive campaign" }).click();

        await expect(page).toHaveURL(new RegExp(`/inbound-campaigns/${campaignId}$`), { timeout: 15_000 });
        // Same cold-compile allowance: the [id] detail route is also hit for
        // the first time here.
        await expect(page.getByText("Support line")).toBeVisible({ timeout: 60_000 });
        await expect(page.getByText("Activation is blocked")).toBeVisible();
        await expect(page.getByText("Inbound SIP trunk runtime healthy")).toBeVisible();
        await expect(page.getByText("Trunk has not yet proven fresh runtime state")).toBeVisible();
        const activateButton = page.getByRole("button", { name: "Activate", exact: true });
        await expect(activateButton).toBeDisabled();

        // The trunk's heartbeat lands — re-point the readiness route before the
        // page's own "Refresh readiness" button asks the server again. Nothing
        // client-side recomputes this; only a new server answer can.
        await jsonRoute(page, new RegExp(`/inbound-campaigns/${campaignId}/readiness$`), readinessReady, "GET");
        await page.getByRole("button", { name: "Refresh readiness" }).click();
        await expect(page.getByText("Safe to request activation")).toBeVisible();
        await expect(activateButton).toBeEnabled();

        await jsonRoute(page, new RegExp(`/inbound-campaigns/${campaignId}/activate$`), campaignFixture({ status: "active", readiness: readinessReady, active_at: "2026-09-04T00:05:00Z" }), "POST");

        await activateButton.click();
        const dialog = page.getByRole("dialog", { name: "Activate inbound calling?" });
        await expect(dialog).toBeVisible();
        await dialog.getByRole("button", { name: "Activate calling" }).click();

        await expect(page.getByRole("status", { name: "Success notification" }).first()).toContainText("Inbound calling activated", { timeout: 10_000 });
        await expect(page.getByText("Active", { exact: true }).first()).toBeVisible();

        // --- Stop here. ---
        // Configuring and activating the number is as far as this flow can go
        // without a real Asterisk/carrier delivering an actual call to
        // +14155550100. That leg is out of reach for a Playwright run against
        // `npm run dev`: it needs live telephony infrastructure this harness
        // has no way to stand up, and nothing here should pretend a mocked
        // route.fulfill() is what a real inbound call looks like.
    });
});
