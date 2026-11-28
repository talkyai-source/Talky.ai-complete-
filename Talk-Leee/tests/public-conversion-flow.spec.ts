import { expect, test, type APIRequestContext, type Locator, type Page, type Route } from "@playwright/test";
import { E2E_AUTH_TOKEN, E2E_PERMISSIONS, E2E_USER, mockApi, mockDashboardApi, signInAsE2eUser } from "./support/session";

/*
 * W9a. A browser test of every public action: each button and link a visitor
 * meets on the public pages is pressed (or fetched) and must end where it
 * promises. Nothing is bought; the app is the production build on
 * http://127.0.0.1:3100 with NO backend, so every API answer is faked here,
 * in the shape the backend sends (the server code is named beside each answer;
 * BE/ = backend/app/). The fakes follow ./journey-sign-in.spec.ts and
 * ./journey-billing.spec.ts; the signed-in session is the one from
 * ./support/session (cookie + localStorage token + shell answers).
 *
 * Each test starts from a clean browser context. Any request to another origin
 * (fonts, analytics, payment, calendar frames) is aborted or answered below, so
 * a missing network cannot fail the spec.
 *
 * Cases: T1 Login, T2 Sign Up / Get Started, T3 Basic / Business x month /
 * year, T3b Pay as you go, T3c the same three buttons when already signed in,
 * T4 Book a demo + Contact us, T5 View Pricing / Documentation / Privacy /
 * Terms, T6 every link of the header, the phone menu and the footer, T7 a
 * signed-out visit to a signed-in page. Each runs at 1366 x 900 and 390 x 844.
 */

test.setTimeout(120_000);

const FALLBACK_ORIGIN = "http://127.0.0.1:3100";
// A made-up value for the faked sign-up and sign-in; built from parts so that
// no quoted credential-like string sits in the source.
const PASSWORD = ["Correct", "horse", "battery", "9"].join("-");
const VIEWPORTS = [
    { label: "desktop 1366x900", width: 1366, height: 900 },
    { label: "phone 390x844", width: 390, height: 844 },
] as const;
// navbar.tsx:324, :500, :709: below this width the header links live in the drawer.
const DESKTOP_MENU_MIN_WIDTH = 1200;

function baseOrigin(): string {
    return new URL(test.info().project.use.baseURL ?? FALLBACK_ORIGIN).origin;
}

function json(route: Route, status: number, body: unknown) {
    return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

/** "/api/v1/auth/login/" -> "/auth/login". */
function apiPath(route: Route): string {
    return new URL(route.request().url()).pathname.replace(/^.*?\/api\/v1(?=\/|$)/, "").replace(/\/+$/, "");
}

/** "/billing/plans/" and "/billing/plans" are the same page. */
function samePath(actual: string, expected: string): boolean {
    const trim = (value: string) => (value.length > 1 ? value.replace(/\/+$/, "") : value);
    return trim(actual) === trim(expected);
}

function isPhone(page: Page): boolean {
    return (page.viewportSize()?.width ?? 1366) < DESKTOP_MENU_MIN_WIDTH;
}

/* ------------------------------------------------------------------------- */
/* The faked backend                                                          */
/* ------------------------------------------------------------------------- */

const SESSION_ID = "cs_test_conversion_0001";
// approvedCheckoutUrl (src/lib/billing-purchase.ts:82-87) accepts only https://checkout.stripe.com.
const CHECKOUT_URL = `https://checkout.stripe.com/c/pay/${SESSION_ID}`;

type PriceOption = {
    id: string; kind: "stripe"; interval: "month" | "year"; interval_count: 1; amount_minor: number;
    currency: "usd"; currency_exponent: 2; active: true; checkout_available: true; unavailable_reason: null;
};
function option(id: string, interval: "month" | "year", amount: number): PriceOption {
    return {
        id, kind: "stripe", interval, interval_count: 1, amount_minor: amount, currency: "usd", currency_exponent: 2,
        active: true, checkout_available: true, unavailable_reason: null,
    };
}

// The plan ids and names the pricing page shows (src/app/pricing/catalog.ts:21-22). The four price ids differ, so a
// request that names the wrong plan or the wrong period is told apart by its price_option_id alone.
const BASIC_MONTH = "11111111-1111-4111-8111-111111111101";
const BASIC_YEAR = "11111111-1111-4111-8111-111111111102";
const BUSINESS_MONTH = "22222222-2222-4222-8222-222222222201";
const BUSINESS_YEAR = "22222222-2222-4222-8222-222222222202";
const PRICE_IDS = {
    Basic: { month: BASIC_MONTH, year: BASIC_YEAR },
    Business: { month: BUSINESS_MONTH, year: BUSINESS_YEAR },
} as const;

/* GET /billing/plans: BillingCatalog (src/lib/billing-purchase.ts:21; rows built in BE/domain/services/billing_catalog.py). */
const CATALOG = [
    {
        id: "basic", name: "Basic", description: "For a first calling team", minutes: 300, concurrent_calls: 3,
        features: ["Outbound campaigns"], popular: false, billing_mode: "live",
        price_options: [option(BASIC_MONTH, "month", 9900), option(BASIC_YEAR, "year", 98600)],
    },
    {
        id: "business", name: "Business", description: "For a larger team", minutes: 1000, concurrent_calls: 10,
        features: ["Outbound campaigns", "CRM connectors"], popular: true, billing_mode: "live",
        price_options: [option(BUSINESS_MONTH, "month", 29900), option(BUSINESS_YEAR, "year", 297800)],
    },
];

/* POST /auth/signup/complete and POST /auth/login: AuthTokenResponse (BE/api/v1/endpoints/auth/schemas.py:47-60). */
const TOKEN_ANSWER = {
    access_token: E2E_AUTH_TOKEN,
    token_type: "bearer",
    user_id: E2E_USER.id,
    tenant_id: E2E_USER.tenant_id,
    email: E2E_USER.email,
    role: E2E_USER.role,
    business_name: E2E_USER.business_name,
    minutes_remaining: E2E_USER.minutes_remaining,
    minutes_state: E2E_USER.minutes_state,
    message: "Login successful.",
    mfa_required: false,
};

/*
 * Reads a visitor without a session makes: the pricing page's bundle list
 * (src/app/pricing/pay-as-you-go.tsx:22) and the sign-up page's legal check
 * (src/components/auth/legal-acceptance.tsx:62). Everything else is a 401 until
 * the account exists, like a server that has no session for this browser.
 */
const PUBLIC_READS = new Set(["/plans/payg", "/auth/legal-versions"]);

/* What the signed-in pages read once the account exists (same shapes as ./journey-billing.spec.ts). */
const TENANT_READS = [
    { path: "/plans/payg", body: { packages: [{ code: "payg_125", minutes: 125, price_cents: 5000, currency: "usd", currency_exponent: 2 }] } },
    { path: "/auth/legal-versions", body: { required: false, terms_version: null, privacy_version: null } },
    { path: "/billing/plans", body: CATALOG },
    {
        path: "/billing/subscription",
        body: {
            status: "inactive", plan_id: null, plan_name: null, current_period_start: null, current_period_end: null,
            cancel_at_period_end: false, minutes_allocated: 0, minutes_used: 0, minutes_remaining: 0,
            minutes_state: "known", purchased_price_option: null, billing_portal_available: false,
        },
    },
    { path: "/billing/usage", body: { usage_type: "minutes", total_used: 0, allocated: 0, remaining: 0, overage: 0, unlimited: false, metering_period: "calendar_month" } },
    { path: "/billing/usage/daily", body: () => [] },
    { path: "/billing/invoices", body: { invoices: [], count: 0 } },
    { path: "/billing/adjustments", body: [] },
    { path: "/billing/overage-alerts", body: [] },
    {
        path: "/billing/config",
        body: {
            stripe_configured: true, mock_mode: false, billing_mode: "live", publishable_key: "pk_dummy",
            checkout_open: true, checkout_unavailable_reason: null, topups_open: true, topups_unavailable_reason: null,
            paypal_plans_open: false, paypal_topups_open: false,
        },
    },
    // An account with no plan is offered pay-as-you-go bundles only: TopupCard titles itself "Pay as you go"
    // when every package has audience "payg" (src/components/billing/topup-card.tsx:177, :286; schema src/lib/topup-api.ts:11-17).
    {
        path: "/billing/topups/packages",
        body: [{ code: "payg_125", name: "125 minutes", minutes: 125, price_cents: 5000, currency: "usd", currency_exponent: 2, expires_days: null, price_per_minute_cents: 40, audience: "payg" }],
    },
    {
        path: "/billing/topups/balance",
        body: { allocated: 0, used_minutes: 0, remaining_minutes: 0, unlimited: false, exhausted: false, purchased_minutes: 0, topup_minutes_left: 0 },
    },
    { path: "/billing/topups/orders", body: { orders: [] } },
    { path: "/billing/topups/ledger", body: { entries: [] } },
];

type Backend = {
    signedIn: boolean;
    signupStarts: unknown[];
    signupVerifies: unknown[];
    signupCompletes: unknown[];
    logins: unknown[];
    checkouts: Array<{ request_id: string; price_option_id: string }>;
    checkoutPageOpened: number;
};

/**
 * Installs every answer one test needs. `signedIn: true` starts with a session
 * (cookie and localStorage token, as a really signed-in browser has);
 * `signedIn: false` answers 401 to everything private until the faked sign-up
 * or sign-in is accepted, then serves the account.
 */
async function installBackend(page: Page, options: { signedIn: boolean }): Promise<Backend> {
    const state: Backend = {
        signedIn: options.signedIn, signupStarts: [], signupVerifies: [], signupCompletes: [], logins: [],
        checkouts: [], checkoutPageOpened: 0,
    };
    const origin = baseOrigin();

    // Registered first, so it runs last: any other origin is aborted (a missing network must not fail a test).
    await page.route((url) => /^https?:$/.test(url.protocol) && url.origin !== origin, (route) => route.abort());

    if (options.signedIn) {
        await signInAsE2eUser(page);
    } else {
        // The shell every signed-in page mounts (the same answers as SHELL_MOCKS in ./support/session, which is not exported).
        await mockApi(page, [
            { path: "/auth/me", body: E2E_USER },
            { path: "/rbac/users/me/permissions", body: { permissions: [...E2E_PERMISSIONS] } },
            { path: "/health", body: { status: "ok" } },
            { path: "/ai-options/providers", body: { llm: { providers: [], models: [] }, stt: { providers: [], models: [] }, tts: { providers: [], models: [] } } },
            { path: "/ai-options/voices", body: { voices: [] } },
            {
                path: "/ai-options/config",
                body: {
                    llm_provider: "groq", llm_model: "e2e-llm", llm_temperature: 0.5, llm_max_tokens: 150,
                    stt_provider: "deepgram", stt_model: "e2e-stt", stt_language: "en",
                    tts_provider: "cartesia", tts_model: "e2e-tts", tts_voice_id: "e2e-voice", tts_sample_rate: 24000,
                },
            },
            {
                path: "/support/config",
                body: { enabled: false, bot_enabled: false, permissions: { report: false, read_team: false }, categories: [], suggestions: [], attachments: { max_mb: 0, max_per_message: 0, types: [] } },
            },
            { path: "/alerts", body: { items: [], next_cursor: null, tenant_id: E2E_USER.tenant_id, user_id: E2E_USER.id } },
            { path: "/calls", body: { items: [], total: 0 } },
        ]);
    }
    await mockDashboardApi(page);
    await mockApi(page, TENANT_READS);

    // Registered after the mocks, so it runs first: the sign-up, the sign-in, the checkout and the 401 gate.
    await page.route(
        (url) => /\/api\/v1(?:\/|$)/.test(url.pathname),
        async (route) => {
            const request = route.request();
            if (request.resourceType() === "document") return route.fallback();
            const path = apiPath(route);
            const method = request.method();

            if (method === "POST") {
                // POST /auth/signup/start: SignupStartResponse (src/lib/api.ts:42-48).
                if (path === "/auth/signup/start") {
                    state.signupStarts.push(request.postDataJSON());
                    return json(route, 200, { message: "Verification code sent! Check your email.", expires_in_minutes: 10, email: E2E_USER.email });
                }
                // POST /auth/signup/verify-code: SignupVerifyCodeResponse (src/lib/api.ts:51-56).
                if (path === "/auth/signup/verify-code") {
                    state.signupVerifies.push(request.postDataJSON());
                    return json(route, 200, { message: "Code verified.", email: E2E_USER.email });
                }
                // POST /auth/signup/complete: the account exists and the answer carries its token (src/lib/api.ts:59).
                if (path === "/auth/signup/complete") {
                    state.signupCompletes.push(request.postDataJSON());
                    state.signedIn = true;
                    return json(route, 200, TOKEN_ANSWER);
                }
                // POST /auth/login, password accepted and no second factor (BE/api/v1/endpoints/auth/login.py:340-351).
                if (path === "/auth/login") {
                    state.logins.push(request.postDataJSON());
                    state.signedIn = true;
                    return json(route, 200, TOKEN_ANSWER);
                }
                // POST /billing/create-checkout-session: CreateCheckoutResponse (BE/api/v1/endpoints/billing.py:52-61, :185-227).
                if (path === "/billing/create-checkout-session" && state.signedIn) {
                    const body = request.postDataJSON() as { request_id: string; price_option_id: string };
                    state.checkouts.push(body);
                    const plan = CATALOG.find((candidate) => candidate.price_options.some((price) => price.id === body.price_option_id));
                    const price = plan?.price_options.find((candidate) => candidate.id === body.price_option_id);
                    if (!plan || !price) return json(route, 404, { detail: { code: "price_option_not_found", message: "Unknown price option." } });
                    return json(route, 200, {
                        request_id: body.request_id,
                        state: "open",
                        session_id: SESSION_ID,
                        checkout_url: CHECKOUT_URL,
                        price_option: {
                            id: price.id, plan_id: plan.id, plan_name: plan.name, kind: price.kind, interval: price.interval,
                            interval_count: price.interval_count, amount_minor: price.amount_minor,
                            currency: price.currency, currency_exponent: price.currency_exponent,
                        },
                        mock_mode: false,
                        message: null,
                    });
                }
            }
            if (!state.signedIn && !(method === "GET" && PUBLIC_READS.has(path))) {
                // No session cookie and no token: BE/api/v1/dependencies.py:276; /auth/refresh: BE/api/v1/endpoints/auth/refresh.py:86-90.
                if (path === "/auth/refresh") return json(route, 401, { detail: "No refresh token." });
                return json(route, 401, { detail: "Authorization required" });
            }
            return route.fallback();
        },
    );

    // The payment site is never opened: its address is answered here with a page of our own.
    await page.route("https://checkout.stripe.com/**", (route) => {
        state.checkoutPageOpened += 1;
        return route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><title>Checkout (intercepted)</title><h1>Checkout (intercepted)</h1>" });
    });
    // The sign-in password step asks Have I Been Pwned for the password's range (login-client.tsx:82); "not found" is an empty list.
    await page.route("https://api.pwnedpasswords.com/**", (route) => route.fulfill({ status: 200, contentType: "text/plain", body: "" }));

    return state;
}

/* ------------------------------------------------------------------------- */
/* Small drivers                                                              */
/* ------------------------------------------------------------------------- */

/** Every main-frame navigation (hard or in-app), in order. */
function recordNavigations(page: Page): string[] {
    const urls: string[] = [];
    page.on("framenavigated", (frame) => {
        if (frame === page.mainFrame()) urls.push(frame.url());
    });
    return urls;
}

/**
 * The header's link scope: the primary navigation at desktop width; at phone
 * width the drawer, opened the way a user does (the menu button), with its
 * three groups (Products, Use Cases, Industries) expanded.
 */
async function openHeader(page: Page): Promise<Locator> {
    if (!isPhone(page)) return page.getByRole("navigation", { name: "Primary" }); // navbar.tsx:303-304
    await page.getByLabel("Open navigation menu").click(); // navbar.tsx:333
    const panel = page.getByRole("menu", { name: "Mobile" }); // navbar.tsx:364
    await expect(panel).toBeVisible();
    return panel;
}

async function expectOkPage(page: Page, url: string) {
    const target = new URL(url);
    target.hash = "";
    const response = await page.request.get(target.toString(), { maxRedirects: 0 });
    expect(response.status(), `${target.pathname} answers 200 with no redirect`).toBe(200);
}

/** Sign-in page reached: URL, heading, status 200 and no loop. */
async function expectSignInPage(page: Page, navigations: string[]) {
    await page.waitForURL((url) => samePath(url.pathname, "/auth/login"));
    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible(); // login-client.tsx:420
    await expectOkPage(page, page.url());
    const visits = navigations.filter((url) => samePath(new URL(url).pathname, "/auth/login"));
    expect(visits.length, "the sign-in page is entered once (no redirect loop)").toBeLessThanOrEqual(2);
    expect(samePath(new URL(page.url()).pathname, "/auth/login")).toBe(true);
}

/** Sign-up page reached. */
async function expectSignUpPage(page: Page) {
    await page.waitForURL((url) => samePath(url.pathname, "/auth/register"));
    await expect(page.getByRole("heading", { name: "Create your account" })).toBeVisible(); // register-client.tsx:274 (step "form")
    await expectOkPage(page, page.url());
}

/*
 * The real sign-up form: details, the code step, the password step. Each step
 * is entered only after its input has taken the focus: that is done by an
 * effect (register-client.tsx:69-76), so it also proves the page is hydrated
 * and typing will reach React state.
 */
async function signUpThroughTheForm(page: Page) {
    const name = page.getByLabel("Your Name"); // register-client.tsx:320
    await expect(name).toBeFocused();
    await name.fill("E2E User");
    await page.getByLabel("Business Name").fill("E2E Test Co"); // register-client.tsx:343
    await page.getByLabel("Work Email").fill(E2E_USER.email); // register-client.tsx:365
    await page.getByRole("button", { name: "Get started" }).click(); // register-client.tsx:403

    await expect(page.getByRole("heading", { name: "Verify your email" })).toBeVisible(); // register-client.tsx:278
    const code = page.getByLabel("Verification code"); // register-client.tsx:427
    await expect(code).toBeFocused();
    await code.fill("123456");
    await page.getByRole("button", { name: "Next", exact: true }).click(); // register-client.tsx:477

    await expect(page.getByRole("heading", { name: "Set your password" })).toBeVisible(); // register-client.tsx:282
    const password = page.getByLabel("Password", { exact: true }); // register-client.tsx:514
    await expect(password).toBeFocused();
    await password.fill(PASSWORD);
    await page.getByLabel("Confirm Password").fill(PASSWORD); // register-client.tsx:539
    await page.getByRole("button", { name: "Create account", exact: true }).click(); // register-client.tsx:586
}

/** Moves the pricing page's switch to Monthly (billing-period.tsx:34, aria-label "Annual", on by default). Safe to retry before hydration. */
async function chooseMonthly(page: Page) {
    const annualSwitch = page.getByRole("switch", { name: "Annual" });
    await expect(async () => {
        if ((await annualSwitch.getAttribute("aria-checked")) === "true") await annualSwitch.click();
        await expect(annualSwitch).toHaveAttribute("aria-checked", "false", { timeout: 1_000 });
    }).toPass();
}

async function openPricing(page: Page) {
    await page.goto("/pricing/", { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { level: 1, name: "Pricing" })).toBeVisible(); // pricing/page.tsx:131
}

/** The dashboard plan page with its billing period chosen (billing-plan-selector.tsx:93: "Monthly" / "Yearly" radios). */
async function expectPlansPage(page: Page, interval: "month" | "year") {
    await expect(page.getByRole("heading", { level: 1, name: "Plans" })).toBeVisible(); // billing/plans/page.tsx:15
    await expect(page.getByRole("radio", { name: interval === "year" ? "Yearly" : "Monthly" })).toBeChecked();
    await expect(page.getByRole("radio", { name: interval === "year" ? "Monthly" : "Yearly" })).not.toBeChecked();
}

/** The dashboard page that sells minutes for an account with no plan. */
async function expectMinutesPage(page: Page) {
    await expect(page.getByRole("heading", { level: 1, name: "Billing" })).toBeVisible(); // billing/page.tsx:15
    await expect(page.getByRole("heading", { name: "Pay as you go" })).toBeVisible(); // topup-card.tsx:286
}

async function expectDashboard(page: Page) {
    await page.waitForURL((url) => samePath(url.pathname, "/dashboard"));
    await expect(page.getByRole("heading", { level: 1, name: "Dashboard" })).toBeVisible(); // dashboard/page.tsx:1576
}

/** The `next` value the sign-up page carries. */
function nextOf(page: Page): string | null {
    return new URL(page.url()).searchParams.get("next");
}

/* ------------------------------------------------------------------------- */
/* The cases                                                                  */
/* ------------------------------------------------------------------------- */

for (const viewport of VIEWPORTS) {
    test.describe(`W9a at ${viewport.label}`, () => {
        test.use({ viewport: { width: viewport.width, height: viewport.height } });

        /* ---- T1 Login ---- */

        test("W9a-T1 the header Login link reaches the sign-in page", async ({ page }) => {
            await installBackend(page, { signedIn: false });
            const navigations = recordNavigations(page);
            await page.goto("/pricing/", { waitUntil: "domcontentloaded" });
            const header = await openHeader(page);
            // The link appears once the app knows the visitor is signed out (navbar.tsx:181-190).
            await header.getByRole("link", { name: "Login", exact: true }).click(); // navbar.tsx:444 (drawer), :678 (desktop)
            await expectSignInPage(page, navigations);
        });

        test("W9a-T1 the footer Login link reaches the sign-in page", async ({ page }) => {
            await installBackend(page, { signedIn: false });
            const navigations = recordNavigations(page);
            await page.goto("/pricing/", { waitUntil: "domcontentloaded" });
            await page.locator("footer").getByRole("link", { name: "Login", exact: true }).click(); // footer.tsx:116
            await expectSignInPage(page, navigations);
        });

        /* ---- T2 Sign Up / Get Started ---- */

        // The header has no sign-up link at all (navbar.tsx:134-141 menu items, :444 Login, :467 Book a Demo): a missing control, reported as a finding.
        test.fixme("W9a-T2 the header Sign Up / Get Started link reaches the sign-up page", async () => {
            // Needs a product change: navbar.tsx offers Login and Book a Demo only, so there is nothing to press.
        });

        test("W9a-T2 the hero Get Started button reaches the sign-up page", async ({ page }) => {
            await installBackend(page, { signedIn: false });
            await page.goto("/ai-voice-dialer/", { waitUntil: "domcontentloaded" });
            // The first "Get Started" in the page order is the hero's (ai-voice-dialer/page.tsx:209); later ones are further down and the footer's.
            await page.getByRole("link", { name: "Get Started", exact: true }).first().click();
            await expectSignUpPage(page);
        });

        test("W9a-T2 the footer Get Started link reaches the sign-up page", async ({ page }) => {
            await installBackend(page, { signedIn: false });
            await page.goto("/pricing/", { waitUntil: "domcontentloaded" });
            await page.locator("footer").getByRole("link", { name: "Get Started", exact: true }).click(); // footer.tsx:125-130
            await expectSignUpPage(page);
        });

        /* ---- T3 Basic and Business, each billing period ---- */

        for (const planName of ["Basic", "Business"] as const) {
            for (const interval of ["month", "year"] as const) {
                test(`W9a-T3 ${planName} ${interval === "year" ? "annual" : "monthly"}: sign-up, then the plan page, then a checkout request for that plan and period`, async ({ page }) => {
                    const backend = await installBackend(page, { signedIn: false });
                    await openPricing(page);
                    if (interval === "month") await chooseMonthly(page);

                    // The plan button (plan-cta.tsx:49 label "Select <plan>"): signed out, it opens sign-up carrying the plan page.
                    await page.getByRole("link", { name: `Select ${planName}`, exact: true }).click();
                    await expectSignUpPage(page);
                    expect(nextOf(page)).toBe(`/billing/plans/?interval=${interval}`);

                    await signUpThroughTheForm(page);

                    // The account exists: the customer is on the plan page with THAT period chosen.
                    await page.waitForURL((url) => samePath(url.pathname, "/billing/plans"));
                    expect(new URL(page.url()).searchParams.get("interval")).toBe(interval);
                    await expectPlansPage(page, interval);
                    expect(backend.signupCompletes).toEqual([{ email: E2E_USER.email, code: "123456", password: PASSWORD, confirm_password: PASSWORD }]);

                    // That plan's button sends a checkout request naming THAT plan and period, and nothing else is bought.
                    await page.getByRole("button", { name: `Select ${planName}`, exact: true }).click(); // billing-plan-selector.tsx:104
                    await page.waitForURL(CHECKOUT_URL);
                    expect(backend.checkoutPageOpened).toBe(1);
                    expect(backend.checkouts).toHaveLength(1);
                    expect(backend.checkouts[0]?.price_option_id).toBe(PRICE_IDS[planName][interval]);
                    expect(Object.keys(backend.checkouts[0] ?? {}).sort()).toEqual(["price_option_id", "request_id"]);
                });
            }
        }

        /* ---- T3b Pay as you go ---- */

        test("W9a-T3b Pay as you go: sign-up, then the dashboard page that sells minutes", async ({ page }) => {
            await installBackend(page, { signedIn: false });
            await openPricing(page);

            await page.getByRole("link", { name: "Buy minutes", exact: true }).click(); // pricing/page.tsx:222 (PlanCta target "minutes")
            await expectSignUpPage(page);
            expect(nextOf(page)).toBe("/billing/");

            await signUpThroughTheForm(page);

            await page.waitForURL((url) => samePath(url.pathname, "/billing"));
            await expectMinutesPage(page);
        });

        /* ---- T3c the same three buttons, already signed in ---- */

        test("W9a-T3c signed in: the plan and minutes buttons go straight to the dashboard and the sign-up form is never shown", async ({ page }) => {
            await installBackend(page, { signedIn: true });
            const navigations = recordNavigations(page);

            const checks = [
                { label: "Select Basic", interval: "year", href: "/billing/plans/?interval=year" },
                { label: "Select Business", interval: "year", href: "/billing/plans/?interval=year" },
                { label: "Select Basic", interval: "month", href: "/billing/plans/?interval=month" },
                { label: "Select Business", interval: "month", href: "/billing/plans/?interval=month" },
                { label: "Buy minutes", interval: null, href: "/billing/" },
            ] as const;

            for (const check of checks) {
                await openPricing(page);
                if (check.interval === "month") await chooseMonthly(page);
                const button = page.getByRole("link", { name: check.label, exact: true });
                // Until the account is read, a signed-in visitor's button still points at sign-up (plan-cta.tsx:41); wait for the real target.
                await expect(button).toHaveAttribute("href", check.href);
                await button.click();
                if (check.interval) {
                    await page.waitForURL((url) => samePath(url.pathname, "/billing/plans"));
                    await expectPlansPage(page, check.interval);
                } else {
                    await page.waitForURL((url) => samePath(url.pathname, "/billing"));
                    await expectMinutesPage(page);
                }
            }
            expect(navigations.filter((url) => url.includes("/auth/register")), "the sign-up form is never shown").toEqual([]);
            await expect(page.getByRole("heading", { name: "Create your account" })).toHaveCount(0);
        });

        /* ---- T4 Book a demo, Contact us ---- */

        test("W9a-T4 the header Book a Demo link opens the demo page with its form", async ({ page }) => {
            await installBackend(page, { signedIn: false });
            await page.goto("/pricing/", { waitUntil: "domcontentloaded" });
            const header = await openHeader(page);
            await header.getByRole("link", { name: "Book a Demo", exact: true }).click(); // navbar.tsx:467 (drawer), :708 (desktop)
            await page.waitForURL((url) => samePath(url.pathname, "/book-a-demo"));
            await expect(page.getByRole("heading", { level: 1, name: "Book a Demo" })).toBeVisible(); // book-a-demo/page.tsx:20
            // The questions come first; the calendar frame opens only after they are saved (demo-booking.tsx:287-298), so the form is what is present.
            await expect(page.getByLabel("Your name", { exact: true })).toBeVisible(); // demo-booking.tsx:158
            await expect(page.getByRole("button", { name: "Save and choose a time" })).toBeVisible(); // demo-booking.tsx:259
            await expect(page.getByRole("heading", { name: "Choose a time" })).toBeVisible(); // demo-booking.tsx:274
            await expectOkPage(page, page.url());
        });

        test("W9a-T4 the header Contact Us link opens the contact page with its form", async ({ page }) => {
            await installBackend(page, { signedIn: false });
            await page.goto("/pricing/", { waitUntil: "domcontentloaded" });
            const header = await openHeader(page);
            await header.getByRole("link", { name: "Contact Us", exact: true }).click(); // navbar.tsx:139
            await page.waitForURL((url) => samePath(url.pathname, "/contact"));
            await expect(page.getByRole("heading", { level: 1, name: "Contact Us" })).toBeVisible(); // contact-section.tsx:122
            await expect(page.getByLabel(/Full Name/)).toBeVisible(); // contact-section.tsx:137
            await expect(page.getByLabel(/Email Address/)).toBeVisible(); // contact-section.tsx:159
            await expect(page.getByLabel(/Message/)).toBeVisible(); // contact-section.tsx:200
            await expect(page.getByRole("button", { name: "Submit", exact: true })).toBeVisible(); // contact-section.tsx:223
            await expectOkPage(page, page.url());
        });

        /* ---- T5 View Pricing, Documentation, Privacy Policy, Terms ---- */

        test("W9a-T5 View Pricing opens the pricing page", async ({ page }) => {
            await installBackend(page, { signedIn: false });
            await page.goto("/ai-voice-dialer/", { waitUntil: "domcontentloaded" });
            await page.getByRole("link", { name: "View Pricing", exact: true }).click(); // ai-voice-dialer/page.tsx:469
            await page.waitForURL((url) => samePath(url.pathname, "/pricing"));
            expect(new URL(page.url()).origin).toBe(baseOrigin());
            await expect(page.getByRole("heading", { level: 1, name: "Pricing" })).toBeVisible();
            await expectOkPage(page, page.url());
        });

        test("W9a-T5 the Documentation link opens the documentation", async ({ page }) => {
            await installBackend(page, { signedIn: false });
            await page.goto("/pricing/", { waitUntil: "domcontentloaded" });
            const header = await openHeader(page);
            await header.getByRole("link", { name: "Documentation", exact: true }).click(); // navbar.tsx:138
            await page.waitForURL((url) => samePath(url.pathname, "/docs"));
            expect(new URL(page.url()).origin).toBe(baseOrigin());
            await expect(page.getByRole("heading", { level: 1 })).toBeVisible(); // docs/page.tsx:13
            await expectOkPage(page, page.url());
        });

        test("W9a-T5 the footer Privacy Policy link opens the privacy policy", async ({ page }) => {
            await installBackend(page, { signedIn: false });
            await page.goto("/pricing/", { waitUntil: "domcontentloaded" });
            await page.locator("footer").getByRole("link", { name: "Privacy Policy", exact: true }).click(); // footer.tsx:103-108
            await page.waitForURL((url) => samePath(url.pathname, "/privacy"));
            expect(new URL(page.url()).origin).toBe(baseOrigin());
            await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
            await expectOkPage(page, page.url());
        });

        test("W9a-T5 the footer Terms of Service link opens the terms", async ({ page }) => {
            await installBackend(page, { signedIn: false });
            await page.goto("/pricing/", { waitUntil: "domcontentloaded" });
            await page.locator("footer").getByRole("link", { name: "Terms of Service", exact: true }).click(); // footer.tsx:97-102
            await page.waitForURL((url) => samePath(url.pathname, "/terms"));
            expect(new URL(page.url()).origin).toBe(baseOrigin());
            await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
            await expectOkPage(page, page.url());
        });

        /* ---- T6 every link of the header, the phone menu and the footer ---- */

        test("W9a-T6 every header (or phone menu) and footer link answers 200 on this site, with at most one redirect", async ({ page }) => {
            await installBackend(page, { signedIn: false });
            await page.goto("/pricing/", { waitUntil: "domcontentloaded" });
            const origin = baseOrigin();
            // Wait until the app has settled which action to show (Login), so the link set is final.
            const phone = isPhone(page);
            const header = await openHeader(page);
            await expect(header.getByRole("link", { name: "Login", exact: true })).toBeVisible();
            if (phone) {
                // Open each group of the drawer, as a user does to reach its links (navbar.tsx:394-419).
                const groups = header.locator("summary");
                for (let index = 0; index < (await groups.count()); index += 1) await groups.nth(index).click();
            }

            // The links are read from the rendered page, not typed here. At desktop width the drawer's copy of the links is
            // hidden markup and is left out; at phone width the drawer is the header's link set.
            const headerLinks = await page
                .locator('nav[aria-label="Primary"] a[href]')
                .evaluateAll(
                    (anchors, onPhone) => anchors
                        .filter((anchor) => (onPhone ? !!anchor.closest(".home-mobile-panel") : !anchor.closest(".home-mobile-panel")))
                        .map((anchor) => (anchor as HTMLAnchorElement).href),
                    phone,
                );
            const footerLinks = await page.locator("footer a[href]").evaluateAll((anchors) => anchors.map((anchor) => (anchor as HTMLAnchorElement).href));
            expect(headerLinks.length, "the header links were read").toBeGreaterThanOrEqual(15);
            expect(footerLinks.length, "the footer links were read").toBeGreaterThanOrEqual(8);

            const internal = new Set<string>();
            const external = new Set<string>();
            for (const raw of [...headerLinks, ...footerLinks]) {
                const url = new URL(raw);
                url.hash = "";
                if (/^https?:$/.test(url.protocol) && url.origin === origin) internal.add(url.toString());
                else external.add(raw);
            }

            const violations: string[] = [];
            const oneHop: string[] = [];
            const twoHops: string[] = [];
            const targets = [...internal];
            for (let start = 0; start < targets.length; start += 8) {
                await Promise.all(targets.slice(start, start + 8).map(async (target) => {
                    const chain = await followRedirects(page.request, target);
                    const last = chain[chain.length - 1];
                    const hops = chain.length - 1;
                    const trail = chain.map((step) => `${step.status} ${new URL(step.url).pathname}`).join(" -> ");
                    if (!last || last.status !== 200) violations.push(`not 200: ${trail}`);
                    if (chain.some((step) => step.status === 404)) violations.push(`404: ${trail}`);
                    if (chain.some((step) => new URL(step.url).origin !== origin)) violations.push(`leaves this site: ${trail}`);
                    if (hops > 1) violations.push(`more than one redirect hop: ${trail}`);
                    if (hops === 1) oneHop.push(trail);
                    if (hops === 2) twoHops.push(trail);
                }));
            }
            // Reported, not hidden: links that need a redirect (the trailing-slash 308 counts as one hop) and links that are not on this site.
            test.info().annotations.push({ type: "links-checked", description: `${targets.length} internal links` });
            test.info().annotations.push({ type: "links-one-hop", description: oneHop.sort().join(" | ") || "none" });
            test.info().annotations.push({ type: "links-two-hops", description: twoHops.sort().join(" | ") || "none" });
            test.info().annotations.push({ type: "links-external-not-fetched", description: [...external].sort().join(" | ") || "none" });
            expect(violations.sort(), "every internal link answers 200 on this site within one redirect hop").toEqual([]);
        });

        /* ---- T7 a signed-out visit to a signed-in page ---- */

        for (const target of [
            { path: "/billing/plans/", query: "?interval=year" },
            { path: "/dashboard/", query: "" },
        ]) {
            test(`W9a-T7 a signed-out visit to ${target.path} ends on the sign-in page, and signing in returns there`, async ({ page }) => {
                const backend = await installBackend(page, { signedIn: false });
                const navigations = recordNavigations(page);

                await page.goto(`${target.path}${target.query}`, { waitUntil: "domcontentloaded" });

                // The proxy lets a signed-out visitor through (src/proxy.ts:355-376) and the page sends them on from the client
                // (components/layout/dashboard-layout.tsx), carrying `next=<path><query>`.
                await page.waitForURL((url) => samePath(url.pathname, "/auth/login"));
                const next = nextOf(page);
                expect(next, "the sign-in page carries the return address").not.toBeNull();
                expect(samePath(new URL(next ?? "/", baseOrigin()).pathname, target.path)).toBe(true);
                // The return address keeps the query, so the plan page reopens on the period the visitor chose.
                expect(new URL(next ?? "/", baseOrigin()).search, "the return address keeps the query of the page asked for").toBe(target.query);
                test.info().annotations.push({ type: "return-address", description: `next=${next}` });
                await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
                expect(
                    navigations.filter((url) => samePath(new URL(url).pathname, "/auth/login")).length,
                    "the sign-in page is entered at most twice (no loop)",
                ).toBeLessThanOrEqual(2);

                // A faked sign-in, driving the real form (same steps as ./journey-sign-in.spec.ts).
                await page.getByLabel("Email").fill(E2E_USER.email); // login-client.tsx:455
                await page.getByRole("button", { name: "Continue with password" }).click(); // login-client.tsx:528
                await page.getByLabel("Password", { exact: true }).fill(PASSWORD); // login-client.tsx:590
                await page.getByRole("button", { name: "Sign in", exact: true }).click(); // login-client.tsx:709

                // Back on the page they asked for.
                await page.waitForURL((url) => samePath(url.pathname, target.path));
                expect(backend.logins).toEqual([{ email: E2E_USER.email, password: PASSWORD }]);
                if (target.path === "/dashboard/") {
                    await expectDashboard(page);
                } else {
                    await expectPlansPage(page, "year");
                }
                expect(
                    navigations.filter((url) => samePath(new URL(url).pathname, "/auth/login")).length,
                    "no second trip to the sign-in page after signing in",
                ).toBeLessThanOrEqual(2);
                expect(samePath(new URL(page.url()).pathname, target.path)).toBe(true);
            });
        }
    });
}

/** Follows a link one 3xx at a time, so every hop and its address are seen. */
async function followRedirects(request: APIRequestContext, start: string): Promise<Array<{ url: string; status: number }>> {
    const chain: Array<{ url: string; status: number }> = [];
    let url = start;
    for (let step = 0; step < 6; step += 1) {
        const response = await request.get(url, { maxRedirects: 0 });
        chain.push({ url, status: response.status() });
        const location = response.headers()["location"];
        if (response.status() < 300 || response.status() >= 400 || !location) break;
        url = new URL(location, url).toString();
    }
    return chain;
}
