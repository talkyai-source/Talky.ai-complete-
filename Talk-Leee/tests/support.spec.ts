import { expect, test, type Page, type Route } from "@playwright/test";

/*
 * The customer support flows against a mocked API: the six scenarios in the
 * brief. Every /api/v1/* call is answered here; nothing reaches a backend.
 */

test.setTimeout(120_000);

const CONVERSATION = "11111111-1111-4111-8111-111111111111";
const TICKET_NEW = "22222222-2222-4222-8222-222222222222";
const TICKET_OLD = "33333333-3333-4333-8333-333333333333";
const TICKET_RESOLVED = "44444444-4444-4444-8444-444444444444";
const STAMP = "2026-10-30T10:00:00+00:00";

const config = (over: Record<string, unknown> = {}) => ({
    enabled: true, bot_enabled: true, permissions: { report: true, read_team: false },
    categories: [{ id: "calls_campaigns", label: "Calls and campaigns" }, { id: "other", label: "Something else" }],
    suggestions: ["My campaign isn't making calls", "I have a billing question"],
    attachments: { max_mb: 10, max_per_message: 5, types: ["png", "jpg", "pdf", "txt"] },
    ...over,
});
const bot = (content: string, sources: Array<{ slug: string; title: string; url: string }> = []) =>
    ({ id: crypto.randomUUID(), role: "assistant", content, sources, created_at: STAMP });
const escalation = (existing: null | { id: string; number: string; subject: string; status: string } = null) => ({
    reason: "no_answer_found", priority_hint: "normal",
    draft: { subject: "My campaign is stuck", category: "calls_campaigns", description: "Problem:\n- My campaign is stuck\n\nWhat the assistant checked:\n- campaign_status: paused by minutes" },
    existing_ticket: existing,
});
const ticket = (id: string, over: Record<string, unknown> = {}) => ({
    id, number: "SUP-000123", subject: "My campaign is stuck", category: "calls_campaigns", priority: "normal", status: "open",
    created_at: STAMP, updated_at: STAMP, requester: { id: "usr_dev", name: "Dev Operator" }, related: {},
    messages: [{ id: crypto.randomUUID(), author_type: "customer", author_name: "Dev Operator", body: "My campaign is stuck", attachments: [], created_at: STAMP }],
    csat: null, allowed_actions: ["reply", "resolve"], ...over,
});

type Mode = "solves" | "escalates" | "existing" | "off";

type Server = {
    posts: Array<{ path: string; body: Record<string, unknown> }>;
    gets: string[];
    tickets: Map<string, ReturnType<typeof ticket>>;
    /** The one conversation as the server holds it, so a reload can read it back. */
    conversation: Array<ReturnType<typeof bot>>;
    signedOut: boolean;
};

async function mockApi(page: Page, mode: Mode): Promise<Server> {
    const server: Server = { posts: [], gets: [], tickets: new Map(), conversation: [], signedOut: false };
    const resolved = ticket(TICKET_RESOLVED, {
        number: "SUP-000077", subject: "Caller ID was wrong", status: "resolved", allowed_actions: ["reply", "reopen", "rate"],
        messages: [
            { id: "m1", author_type: "customer", author_name: "Dev Operator", body: "The caller ID is wrong.", attachments: [], created_at: STAMP },
            { id: "m2", author_type: "staff", author_name: "Support", body: "We fixed the number on your line.", attachments: [], created_at: STAMP },
            { id: "m3", author_type: "robot", author_name: "X", body: "SHOULD NOT RENDER", attachments: [], created_at: STAMP },
        ],
    });
    server.tickets.set(TICKET_RESOLVED, resolved);
    server.tickets.set(TICKET_OLD, ticket(TICKET_OLD, { number: "SUP-000009", subject: "Older campaign problem" }));

    await page.addInitScript(() => { localStorage.setItem("talklee.auth.token", "dev-token"); });
    const json = (route: Route, body: unknown, status = 200, headers: Record<string, string> = {}) =>
        route.fulfill({ status, contentType: "application/json", headers, body: JSON.stringify(body) });

    await page.route(/\/api\/v1\//, async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        const path = url.pathname.replace(/^.*\/api\/v1/, "");
        const method = request.method();
        const body = method === "POST" ? (request.postDataJSON() as Record<string, unknown> | null) ?? {} : {};
        if (method === "POST") server.posts.push({ path, body });
        if (method === "GET") server.gets.push(path);

        if (/^\/(auth\/me|me)\/?$/.test(path)) {
            if (server.signedOut) return json(route, { error: { code: "unauthorized", message: "Not signed in.", details: null, request_id: "r" } }, 401);
            return json(route, { id: "usr_dev", email: "dev@example.com", name: "Dev Operator", role: "tenant_admin", tenant_id: "tenant-dev", minutes_remaining: 100 });
        }
        if (path === "/auth/logout" && method === "POST") { server.signedOut = true; return route.fulfill({ status: 204, body: "" }); }
        if (path === "/health") return json(route, { status: "ok" });
        if (path === "/events") return json(route, { items: [], next_cursor: null });
        if (path === "/support/config") return json(route, mode === "off" ? config({ enabled: false, permissions: { report: false, read_team: false }, categories: [], suggestions: [], attachments: { max_mb: 0, max_per_message: 0, types: [] } }) : config());
        if (mode === "off" && path.startsWith("/support/")) return json(route, { error: { code: "not_found", message: "Not found.", details: null, request_id: "r" } }, 404);
        if (path === "/support/unread") return json(route, { count: 0, ticket_ids: [] });
        if (path === "/support/articles/search") return json(route, { items: [] });
        if (path === "/support/conversations" && method === "POST") {
            server.conversation = [bot("Hi, tell me what's going wrong.")];
            return json(route, { conversation_id: CONVERSATION, greeting: "Hi, tell me what's going wrong.", suggestions: config().suggestions }, 201);
        }
        if (path === `/support/conversations/${CONVERSATION}` && method === "GET") return json(route, { conversation_id: CONVERSATION, status: "active", messages: server.conversation, escalation: null });
        if (path === `/support/conversations/${CONVERSATION}/messages`) {
            const reply = mode === "solves"
                ? { message: bot("Your campaign is paused because the minutes ran out. Buy a top-up under **Billing**.", [{ slug: "billing-top-ups", title: "Top-ups", url: "/docs/billing#top-ups" }]), resolution_prompt: true, escalation: null }
                : { message: bot("I couldn't find this. A person can help: review the ticket below and send it."), resolution_prompt: false, escalation: escalation(mode === "existing" ? { id: TICKET_OLD, number: "SUP-000009", subject: "Older campaign problem", status: "open" } : null) };
            server.conversation = [...server.conversation, { ...bot(String(body.content)), role: "user" }, reply.message];
            return json(route, reply);
        }
        if (path === `/support/conversations/${CONVERSATION}/feedback`) return json(route, { status: body.solved ? "resolved_by_bot" : "active", message: null, escalation: null });
        if (path === `/support/conversations/${CONVERSATION}/escalate`) return json(route, { escalation: { ...escalation(), reason: "customer_requested" } });
        if (path === "/support/tickets" && method === "POST") {
            const created = ticket(TICKET_NEW, { subject: body.subject, category: body.category, messages: [{ id: crypto.randomUUID(), author_type: "customer", author_name: "Dev Operator", body: body.description, attachments: [], created_at: STAMP }] });
            server.tickets.set(TICKET_NEW, created);
            return json(route, created, 201);
        }
        if (path === "/support/tickets" && method === "GET") {
            const status = url.searchParams.get("status");
            const items = [...server.tickets.values()].filter((entry) => !status || entry.status === status)
                .map((entry) => ({ id: entry.id, number: entry.number, subject: entry.subject, status: entry.status, priority: entry.priority, category: entry.category, updated_at: entry.updated_at, unread: false }));
            return json(route, { items, next_cursor: null });
        }
        const ticketMatch = path.match(/^\/support\/tickets\/([^/]+)(?:\/(messages|resolve|reopen|csat))?$/);
        if (ticketMatch) {
            const current = server.tickets.get(ticketMatch[1]!);
            if (!current) return json(route, { error: { code: "not_found", message: "Not found.", details: null, request_id: "r" } }, 404);
            const action = ticketMatch[2];
            if (!action) return json(route, current);
            if (action === "messages") {
                const message = { id: crypto.randomUUID(), author_type: "customer", author_name: "Dev Operator", body: String(body.body), attachments: [], created_at: STAMP };
                current.messages = [...current.messages, message];
                return json(route, message, 201);
            }
            if (action === "csat") {
                current.csat = { score: Number(body.score), comment: (body.comment as string | undefined) ?? null };
                current.allowed_actions = current.allowed_actions.filter((entry) => entry !== "rate");
                return json(route, current);
            }
            current.status = action === "resolve" ? "resolved" : "open";
            return json(route, current);
        }
        return json(route, { error: { code: "not_found", message: "Not found.", details: null, request_id: "r" } }, 404);
    });
    return server;
}

// The dev server compiles a route on first visit; the first element on a page waits longer.
const FIRST_PAINT = { timeout: 45_000 };

async function openDrawer(page: Page) {
    await page.goto("/dashboard");
    const help = page.locator("[data-support-help-button]");
    await expect(help).toBeVisible(FIRST_PAINT);
    await help.click();
    const dialog = page.getByRole("dialog", { name: "Support" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("log")).toContainText("Hi, tell me what's going wrong.", { timeout: 15_000 });
    return dialog;
}

async function sendMessage(page: Page, text: string) {
    await page.locator("#support-chat-input").fill(text);
    await page.locator("#support-chat-input").press("Enter");
}

test("the bot solves it: feedback yes, no ticket", async ({ page }) => {
    const server = await mockApi(page, "solves");
    const dialog = await openDrawer(page);
    await expect(dialog).toContainText("Includes: this page");
    await sendMessage(page, "My campaign is stuck");
    await expect(dialog.getByRole("log")).toContainText("minutes ran out");
    await expect(dialog.getByRole("link", { name: "Top-ups" })).toHaveAttribute("href", "/docs/billing#top-ups");
    await expect(dialog).toContainText("Did this solve your problem?");
    await dialog.getByRole("button", { name: "Yes" }).click();
    await expect(dialog).toContainText("Glad that helped.");
    expect(server.posts.filter((post) => post.path === "/support/tickets")).toHaveLength(0);
    const feedback = server.posts.find((post) => post.path.endsWith("/feedback"));
    expect(feedback?.body).toMatchObject({ solved: true });
    expect(typeof feedback?.body.request_id).toBe("string");
});

test("the bot cannot: form, ticket, ticket page", async ({ page }) => {
    const server = await mockApi(page, "escalates");
    const dialog = await openDrawer(page);
    await sendMessage(page, "My campaign is stuck");
    const form = dialog.locator("[data-support-ticket-form]");
    await expect(form).toBeVisible();
    await expect(form.getByLabel("Subject")).toHaveValue("My campaign is stuck");
    await expect(form.getByLabel("Description")).toContainText("What the assistant checked");
    await expect(dialog).not.toContainText("SUP-000123");
    await form.getByRole("button", { name: "Send to support" }).click();
    await expect(dialog.locator("[data-support-ticket-number]")).toHaveText("SUP-000123");
    const created = server.posts.find((post) => post.path === "/support/tickets");
    expect(created?.body).toMatchObject({ conversation_id: CONVERSATION, subject: "My campaign is stuck", category: "calls_campaigns", attachment_ids: [] });
    await dialog.getByRole("link", { name: "View ticket" }).click();
    // The first client-side visit to the ticket route compiles it in dev (measured 26 s on a loaded machine).
    await expect(page).toHaveURL(new RegExp(`/support/tickets/${TICKET_NEW}`), { timeout: 120_000 });
    await expect(page.getByRole("dialog", { name: "Support" })).toHaveCount(0);
    await expect(page.locator("[data-support-ticket-number]")).toHaveText("SUP-000123", FIRST_PAINT);
    await expect(page.locator("[data-support-thread]")).toContainText("What the assistant checked");
});

test("Talk to a person escalates straight to the form", async ({ page }) => {
    const server = await mockApi(page, "solves");
    const dialog = await openDrawer(page);
    await dialog.locator("[data-support-talk-to-person]").click();
    await expect(dialog.locator("[data-support-ticket-form]")).toBeVisible();
    expect(server.posts.some((post) => post.path.endsWith("/escalate"))).toBe(true);
    expect(server.posts.filter((post) => post.path.endsWith("/messages"))).toHaveLength(0);
});

test("an existing open ticket can take the message instead of a new ticket", async ({ page }) => {
    const server = await mockApi(page, "existing");
    const dialog = await openDrawer(page);
    await sendMessage(page, "My campaign is stuck");
    await expect(dialog).toContainText("You already have an open ticket");
    await dialog.getByRole("button", { name: "Add this to SUP-000009 instead" }).click();
    await dialog.getByRole("button", { name: "Add to SUP-000009" }).click();
    await expect(dialog).toContainText("Your message was added.");
    await expect(dialog.locator("[data-support-ticket-number]")).toHaveText("SUP-000009");
    expect(server.posts.filter((post) => post.path === "/support/tickets")).toHaveLength(0);
    const appended = server.posts.find((post) => post.path === `/support/tickets/${TICKET_OLD}/messages`);
    expect(appended?.body).toMatchObject({ attachment_ids: [] });
    expect(String(appended?.body.body)).toContain("My campaign is stuck");
});

test("reply and rating on a resolved ticket", async ({ page }) => {
    const server = await mockApi(page, "solves");
    await page.goto(`/support/tickets/${TICKET_RESOLVED}`);
    await expect(page.locator("[data-support-ticket-number]")).toHaveText("SUP-000077", FIRST_PAINT);
    const thread = page.locator("[data-support-thread]");
    await expect(thread).toContainText("We fixed the number on your line.");
    await expect(thread).not.toContainText("SHOULD NOT RENDER");
    await page.getByLabel("Your reply").fill("Thanks, confirmed.");
    await page.getByRole("button", { name: "Send reply" }).click();
    await expect(thread).toContainText("Thanks, confirmed.");
    const reply = server.posts.find((post) => post.path === `/support/tickets/${TICKET_RESOLVED}/messages`);
    expect(reply?.body).toMatchObject({ body: "Thanks, confirmed.", attachment_ids: [] });
    const rating = page.locator("[data-support-rating-card]");
    await expect(rating).toBeVisible();
    // The radio input is visually hidden under its star; a user clicks the label.
    await rating.locator("label", { hasText: /^5$/ }).click();
    await expect(rating.getByRole("radio", { name: "5" })).toBeChecked();
    await rating.getByLabel("Comment (optional)").fill("Quick and clear.");
    await rating.getByRole("button", { name: "Send rating" }).click();
    await expect(page.getByText("5 / 5 · Quick and clear.")).toBeVisible();
    await expect(rating).toHaveCount(0);
    expect(server.posts.find((post) => post.path.endsWith("/csat"))?.body).toMatchObject({ score: 5, comment: "Quick and clear." });
});

test("support switched off: no Help button, no sidebar entry, pages say it is unavailable", async ({ page }) => {
    await mockApi(page, "off");
    await page.goto("/dashboard");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible(FIRST_PAINT);
    await expect(page.locator("[data-support-help-button]")).toHaveCount(0);
    await expect(page.locator("aside a[href='/support']")).toHaveCount(0);
    for (const path of ["/support", "/support/tickets", `/support/tickets/${TICKET_RESOLVED}`]) {
        await page.goto(path);
        await expect(page.locator("[data-support-unavailable]")).toContainText("Support isn't available yet", FIRST_PAINT);
    }
});

const STORAGE_KEY = "talklee.support.conversation.v1:tenant-dev:usr_dev";
const conversationReads = (server: Server) => server.gets.filter((path) => path === `/support/conversations/${CONVERSATION}`).length;
const conversationStarts = (server: Server) => server.posts.filter((post) => post.path === "/support/conversations").length;

test("a reload resumes the stored conversation through GET with its messages intact", async ({ page }) => {
    const server = await mockApi(page, "solves");
    const dialog = await openDrawer(page);
    await sendMessage(page, "My campaign is stuck");
    await expect(dialog.getByRole("log")).toContainText("minutes ran out");
    expect(await page.evaluate((key) => sessionStorage.getItem(key), STORAGE_KEY)).toBe(CONVERSATION);
    expect(conversationStarts(server)).toBe(1);
    expect(conversationReads(server)).toBe(0);

    await page.reload();
    const help = page.locator("[data-support-help-button]");
    await expect(help).toBeVisible(FIRST_PAINT);
    await help.click();
    const resumed = page.getByRole("dialog", { name: "Support" });
    await expect(resumed.getByRole("log")).toContainText("minutes ran out", { timeout: 15_000 });
    await expect(resumed.getByRole("log")).toContainText("My campaign is stuck");
    await expect(resumed.getByRole("log")).toContainText("Hi, tell me what's going wrong.");
    expect(conversationReads(server)).toBe(1);
    expect(conversationStarts(server)).toBe(1);
    expect(server.posts.filter((post) => post.path.endsWith("/messages"))).toHaveLength(1);
});

test("signing out removes the stored conversation and nothing resumes afterwards", async ({ page }) => {
    const server = await mockApi(page, "solves");
    await openDrawer(page);
    expect(await page.evaluate((key) => sessionStorage.getItem(key), STORAGE_KEY)).toBe(CONVERSATION);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: "Support" })).toHaveCount(0);
    const readsBefore = conversationReads(server);

    await page.locator("aside").getByRole("button", { name: /Logout/ }).first().click();
    await expect(page).toHaveURL(/\/auth\/login/, { timeout: 60_000 });
    expect(server.signedOut).toBe(true);
    expect(await page.evaluate(() => Object.keys(sessionStorage).filter((key) => key.startsWith("talklee.support.conversation.v1:")))).toEqual([]);

    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/auth\/login/, FIRST_PAINT);
    await expect(page.locator("[data-support-help-button]")).toHaveCount(0);
    expect(conversationReads(server)).toBe(readsBefore);
});
