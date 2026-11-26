import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import { expect, test, type Page, type Route } from "@playwright/test";

/*
 * Admin console → Phone Number Requests: platform staff review the Telephony
 * "Buy Now" requests (list by status, open a request, download each document,
 * approve, or reject with a reason).
 *
 * Like ./journey-admin.spec.ts this drives the Admin app (Admin/frontend), not
 * the customer app. The Admin app must be served from its production build
 * (`vite preview`, by default on 127.0.0.1:4173; ADMIN_BASE_URL overrides it),
 * built with VITE_API_BASE_URL=/api/v1 so it calls the API on its own origin.
 *
 * Every request is answered here, in the shape the backend sends
 * (BE/ = backend/app/, AD/ = Admin/frontend/src/). A request this file does not
 * answer fails the test. The console has one theme, so there is no dark run.
 *
 * Measurements are written to the file named by NUMBER_REQUESTS_MEASUREMENTS
 * (when set), one record per width and state.
 */

test.setTimeout(240_000);

const ADMIN_BASE_URL = (process.env.ADMIN_BASE_URL ?? "http://127.0.0.1:4173").replace(/\/+$/, "");
const MEASUREMENTS_FILE = process.env.NUMBER_REQUESTS_MEASUREMENTS ?? "";
const MFA_MARKER_KEY = "talky.admin.mfa.v1"; // AD/lib/auth.tsx:14
const BASE = "/admin/telephony/number-requests"; // BE/api/v1/endpoints/admin/telephony_number_requests.py:150

const SIZES: Array<[number, number]> = [
    [320, 568], [360, 740], [375, 667], [390, 844], [414, 896], [768, 1024], [820, 1180],
    [1024, 768], [1280, 800], [1366, 768], [1440, 900], [1536, 864], [1920, 1080],
];

type Role = "platform_admin" | "super_admin" | "partner_admin" | "tenant_admin" | "user" | "readonly";
const STAFF_ID = "0d4f5a6b-7c8d-4e9f-a0b1-c2d3e4f5a6b7";
const TENANT_A = "2a3b4c5d-6e7f-4a8b-9c0d-1e2f3a4b5c6d";
const TENANT_B = "3c4d5e6f-7a8b-4c9d-8e0f-1a2b3c4d5e6f";
const REQUEST_1 = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d";
const REQUEST_2 = "b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e";
const REQUEST_3 = "c3d4e5f6-a7b8-4c9d-8e0f-2a3b4c5d6e7f";
const DOC_1 = "d0c00001-1111-4aaa-8bbb-000000000001";
const DOC_2 = "d0c00002-2222-4aaa-8bbb-000000000002";
const USER_ID = "11111111-aaaa-4bbb-8ccc-ddddeeeeffff";
const SUBMITTED_AT = "2026-11-25T12:00:00Z";
const REVIEWED_AT = "2026-11-26T09:30:00Z";
const LONG_NAME = "Northwind International Trading and Logistics Holdings (UK)."; // 60 characters
const LONG_REASON = "The utility bill is older than three months. ".repeat(23).slice(0, 1000); // 1000 characters

/* GET /auth/me: MeResponse (BE/api/v1/endpoints/auth/schemas.py:82-125). */
function me(role: Role) {
    const tenant = role === "tenant_admin" || role === "user" || role === "readonly" ? TENANT_A : null;
    return {
        id: STAFF_ID, email: "staff@example.com", name: "Console User", business_name: null, account_timezone: null, reporting_timezone: null,
        role, legal_acceptance_required: false, terms_version: null, privacy_version: null,
        minutes_remaining: null, minutes_state: "unavailable", partner_id: null, tenant_id: tenant, partner_status: null,
        tenant_status: null, suspended_scope: null, suspension_reason: null, suspended_at: null, subscription_status: null,
        account_state: "active", account_state_reason: null, mfa_enabled: true, mfa_verified: true,
    };
}

/* RequestListItem (BE/api/v1/endpoints/admin/telephony_number_requests.py:52-61). */
type Item = {
    id: string; tenant_id: string; business_name: string | null; requested_by_user_id: string; use_type: string; status: string;
    submitted_at: string; reviewed_at: string | null; files_deleted: boolean;
};
const item = (over: Partial<Item> = {}): Item => ({
    id: REQUEST_1, tenant_id: TENANT_A, business_name: "Contoso Dental", requested_by_user_id: USER_ID, use_type: "business", status: "submitted",
    submitted_at: SUBMITTED_AT, reviewed_at: null, files_deleted: false, ...over,
});
/* DocumentDetail (:71-80) and RequestDetail (:83-96). */
type Doc = { id: string; kind: string; file_name: string | null; content_type: string; size_bytes: number; sha256: string; scan_status: string; created_at: string; available: boolean };
type Detail = Item & { requester_email: string | null; review_note: string | null; reviewed_by_user_id: string | null; documents: Doc[] };
const doc = (over: Partial<Doc> = {}): Doc => ({
    id: DOC_1, kind: "business_registration", file_name: "registration.pdf", content_type: "application/pdf", size_bytes: 482_133,
    sha256: "a".repeat(64), scan_status: "not_scanned", created_at: SUBMITTED_AT, available: true, ...over,
});
const detail = (over: Partial<Detail> = {}): Detail => ({
    ...item(), requester_email: "owner@example.com", review_note: null, reviewed_by_user_id: null,
    documents: [doc(), doc({ id: DOC_2, kind: "business_photo_id", file_name: "id.jpg", content_type: "image/jpeg", size_bytes: 2_621_440 })], ...over,
});

type Answer = { status: number; body: unknown };
const ok = (body: unknown): Answer => ({ status: 200, body });
/* The canonical error envelope (BE/core/error_handlers.py:89-102). */
const refusal = (status: number, code: string, message: string, details: unknown = null): Answer =>
    ({ status, body: { error: { code, message, details, request_id: "req-e2e" } } });
const ALREADY_DECIDED = (status: string) => refusal(409, "already_decided", "That request has already been decided.", { status }); // :321-323
const UNCONFIRMED = refusal(503, "decision_unconfirmed", "The decision is not confirmed. Read the request again before trying again."); // :336-337
const NOTE_REQUIRED = refusal(422, "note_required", "Give a reason when you reject a request.", { field: "note" }); // :306-307

type Call = { method: string; path: string; search: string; body: unknown };
type Backend = {
    list: (status: string, offset: number) => Answer;
    details: Record<string, () => Answer>;
    document: (documentId: string) => Answer | { file: Doc };
    decide: (requestId: string, body: unknown) => Answer;
    hold: null | Promise<void>;
    calls: Call[];
    others: string[];
    unanswered: string[];
};

function json(route: Route, answer: Answer) {
    return route.fulfill({ status: answer.status, contentType: "application/json", body: JSON.stringify(answer.body) });
}
const apiPath = (route: Route): string => new URL(route.request().url()).pathname.replace(/^.*?\/api\/v1(?=\/|$)/, "").replace(/\/+$/, "");

async function mockBackend(page: Page, role: Role): Promise<Backend> {
    const backend: Backend = {
        list: () => ok({ items: [], limit: 20, offset: 0, has_more: false }),
        details: {},
        document: () => refusal(404, "document_not_found", "That document was not found."),
        decide: () => refusal(404, "number_request_not_found", "That request was not found."),
        hold: null, calls: [], others: [], unanswered: [],
    };
    // The second factor was completed in this browser (AD/lib/auth.tsx:48-53); the session is the cookie one, proven by /auth/me (:97-111).
    await page.addInitScript(([key, id]) => { window.localStorage.setItem(key, JSON.stringify({ userId: id })); }, [MFA_MARKER_KEY, STAFF_ID]);
    await page.route((url) => /\/api\/v1(?:\/|$)/.test(url.pathname), async (route) => {
        const request = route.request();
        const path = apiPath(route);
        const method = request.method();
        const url = new URL(request.url());
        if (method === "GET" && path === "/auth/me") return json(route, ok(me(role)));
        // GET /admin/tenants: TenantListItem[] (BE/api/v1/endpoints/admin/tenants.py:78-91), the shapes of ./journey-admin.spec.ts.
        // Read by the Tenants page and by the company filter on the Support Tickets page.
        if (method === "GET" && path === "/admin/tenants") {
            backend.others.push(`${method} ${path}`);
            const tenant = (id: string, business_name: string, minutes_used: number) => ({
                id, business_name, plan_id: "starter", plan_name: "Starter", minutes_used, minutes_allocated: 500, status: "active",
                user_count: 3, campaign_count: 2, max_concurrent_calls: 2, created_at: "2026-10-01T09:00:00+00:00", updated_at: "2026-11-17T08:00:00+00:00",
            });
            return json(route, ok([tenant(TENANT_B, "Contoso Dental", 120), tenant(TENANT_A, "Northwind Traders", 240)]));
        }
        // GET /admin/support/tickets: the staff queue page, items plus next_cursor (AD/lib/support-contracts.ts:45-50, checked there
        // field by field against BE/domain/services/support/projection.py).
        if (method === "GET" && path === "/admin/support/tickets") {
            backend.others.push(`${method} ${path}`);
            return json(route, ok({ next_cursor: null, items: [{
                id: "5e6f7a8b-9c0d-4e1f-8a2b-3c4d5e6f7a8b", number: "SUP-000123", subject: "Campaign stuck", status: "open", priority: "high",
                category: "calls_campaigns", updated_at: "2026-11-25T10:00:00+00:00", created_at: "2026-11-25T09:00:00+00:00", unread: false,
                tenant: { id: TENANT_A, name: "Northwind Traders" }, assignee: null,
                sla: { first_response_due_at: "2026-11-26T09:00:00+00:00", resolution_due_at: "2026-11-28T09:00:00+00:00", breached: false },
            }] }));
        }
        // GET /admin/actions for the roles that only have the Actions page (AD/lib/api.ts ActionListResponse).
        if (method === "GET" && path === "/admin/actions") {
            return json(route, ok({ items: [], total: 0, page: 1, page_size: Number(url.searchParams.get("page_size") ?? 20) }));
        }
        if (path === BASE || path.startsWith(`${BASE}/`)) {
            backend.calls.push({ method, path, search: url.search, body: method === "POST" ? request.postDataJSON() : null });
            const parts = path.slice(BASE.length).split("/").filter(Boolean);
            // R1 list (:150-180).
            if (method === "GET" && parts.length === 0) {
                return json(route, backend.list(url.searchParams.get("status") ?? "", Number(url.searchParams.get("offset") ?? "0")));
            }
            // R2 one request (:183-211).
            if (method === "GET" && parts.length === 2) {
                const answer = backend.details[parts[1]];
                return json(route, answer ? answer() : refusal(404, "number_request_not_found", "That request was not found."));
            }
            // R3 one document, as a download with the server's own headers (:281-291).
            if (method === "GET" && parts.length === 4 && parts[2] === "documents") {
                const answer = backend.document(parts[3]);
                if (!("file" in answer)) return json(route, answer);
                const extension = { "application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png" }[answer.file.content_type] ?? "";
                return route.fulfill({
                    status: 200, contentType: answer.file.content_type, body: Buffer.from("%PDF-1.7 synthetic document body"),
                    headers: {
                        "Content-Disposition": `attachment; filename="${answer.file.kind}-${answer.file.id.slice(0, 8)}${extension}"`,
                        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
                        "Content-Security-Policy": "default-src 'none'; sandbox", "X-Document-Scan": answer.file.scan_status,
                    },
                });
            }
            // R4 decision (:294-345).
            if (method === "POST" && parts.length === 3 && parts[2] === "decision") {
                const answer = backend.decide(parts[1], request.postDataJSON());
                if (backend.hold) await backend.hold;
                return json(route, answer);
            }
        }
        backend.unanswered.push(`${method} ${path}${url.search}`);
        return json(route, { status: 404, body: { detail: "Not Found" } });
    });
    return backend;
}

const listCalls = (backend: Backend) => backend.calls.filter((call) => call.method === "GET" && call.path === BASE);
const posts = (backend: Backend) => backend.calls.filter((call) => call.method === "POST");
const requestPath = (tenant: string, request: string) => `/number-requests/${tenant}/${request}`;
function gate() {
    let release: () => void = () => undefined;
    const promise = new Promise<void>((resolve) => { release = resolve; });
    return { promise, release };
}

// ───────────────────────── platform staff: the whole review ─────────────────────────

test("platform staff list requests by status, open a request, download both documents, approve one and reject another", async ({ page }) => {
    const backend = await mockBackend(page, "platform_admin");
    const state: Record<string, Detail> = {
        [REQUEST_1]: detail(),
        [REQUEST_2]: detail({ id: REQUEST_2, tenant_id: TENANT_B, business_name: "Fabrikam Logistics", use_type: "personal", documents: [
            doc({ kind: "utility_bill", content_type: "image/png" }), doc({ id: DOC_2, kind: "personal_photo_id", content_type: "image/jpeg" }),
        ] }),
        [REQUEST_3]: detail({ id: REQUEST_3, business_name: "Adventure Works", status: "approved", reviewed_at: REVIEWED_AT, reviewed_by_user_id: STAFF_ID }),
    };
    const row = (value: Detail): Item => ({
        id: value.id, tenant_id: value.tenant_id, business_name: value.business_name, requested_by_user_id: value.requested_by_user_id,
        use_type: value.use_type, status: value.status, submitted_at: value.submitted_at, reviewed_at: value.reviewed_at, files_deleted: value.files_deleted,
    });
    backend.list = (status, offset) => ok({ items: Object.values(state).filter((value) => value.status === status).map(row), limit: 20, offset, has_more: false });
    for (const id of Object.keys(state)) backend.details[id] = () => ok(state[id]);
    backend.document = (documentId) => ({ file: Object.values(state).flatMap((value) => value.documents).find((value) => value.id === documentId) ?? doc() });
    backend.decide = (requestId, body) => {
        const sent = body as { decision: "approved" | "rejected"; note?: string };
        const current = state[requestId];
        if (current.status !== "submitted") return ALREADY_DECIDED(current.status);
        state[requestId] = { ...current, status: sent.decision, review_note: sent.note ?? null, reviewed_at: REVIEWED_AT, reviewed_by_user_id: STAFF_ID };
        // DecisionOut (:105-109): approve was emailed; the rejected account has no email address on file.
        return ok({ id: requestId, status: sent.decision, reviewed_at: REVIEWED_AT, email: sent.decision === "approved" ? "sent" : "skipped" });
    };

    await page.goto(`${ADMIN_BASE_URL}/number-requests`);
    await expect(page.getByRole("heading", { level: 1, name: "Phone Number Requests" })).toBeVisible();
    await expect(page.getByText("Phone number requests across every company. Open a request to download its documents, then approve or reject it.")).toBeVisible();
    await expect(page.getByRole("link", { name: "Phone Number Requests" })).toBeVisible(); // the sidebar entry
    expect(listCalls(backend).map((call) => call.search)).toEqual(["?status=submitted&limit=20&offset=0"]);

    // The submitted list.
    await expect(page.getByRole("columnheader")).toHaveText(["Company", "Use", "Status", "Submitted", "Reviewed", "Files"]);
    await expect(page.getByRole("row").filter({ hasText: "Contoso Dental" })).toContainText("Business Use");
    await expect(page.getByRole("row").filter({ hasText: "Fabrikam Logistics" })).toContainText("Personal Use");
    await expect(page.getByRole("row").filter({ hasText: "Contoso Dental" })).toContainText("Available");
    const tabs = page.getByRole("navigation", { name: "Filter by status" });
    await expect(tabs.getByRole("link")).toHaveText(["Submitted", "Approved", "Rejected"]);
    await expect(tabs.locator('[aria-current="true"]')).toHaveText("Submitted");

    // Each tab re-reads its own status and is kept in the address.
    await tabs.getByRole("link", { name: "Approved" }).click();
    await expect(page).toHaveURL(`${ADMIN_BASE_URL}/number-requests?status=approved`);
    await expect(page.getByRole("row").filter({ hasText: "Adventure Works" })).toContainText("Approved");
    await expect(tabs.locator('[aria-current="true"]')).toHaveText("Approved");
    await tabs.getByRole("link", { name: "Rejected" }).click();
    await expect(page).toHaveURL(`${ADMIN_BASE_URL}/number-requests?status=rejected`);
    await expect(page.getByText("No requests with this status.")).toBeVisible();
    await tabs.getByRole("link", { name: "Submitted" }).click();
    await expect(page).toHaveURL(`${ADMIN_BASE_URL}/number-requests`);
    await expect(tabs.locator('[aria-current="true"]')).toHaveText("Submitted");
    await expect(page.getByRole("link", { name: "Contoso Dental" })).toBeVisible();
    await expect.poll(() => listCalls(backend).map((call) => call.search)).toEqual([
        "?status=submitted&limit=20&offset=0", "?status=approved&limit=20&offset=0", "?status=rejected&limit=20&offset=0", "?status=submitted&limit=20&offset=0",
    ]);

    // Open the first request.
    await page.getByRole("link", { name: "Contoso Dental" }).click();
    await expect(page).toHaveURL(`${ADMIN_BASE_URL}${requestPath(TENANT_A, REQUEST_1)}`);
    await expect(page.getByRole("heading", { level: 1, name: "Phone number request" })).toBeVisible();
    await expect(page.getByText("owner@example.com")).toBeVisible();
    const registration = page.getByRole("article", { name: "Business registration", exact: true });
    const photoId = page.getByRole("article", { name: "Photo ID matching the business registration" });
    await expect(registration).toContainText("PDF");
    await expect(registration).toContainText("471 KB");
    await expect(registration).toContainText("Not scanned");
    await expect(registration).toContainText("That document has not been cleared by a virus scan.");
    await expect(photoId).toContainText("JPG");
    await expect(photoId).toContainText("2.5 MB");

    // Download both documents: the browser's download event and the saved name.
    for (const [block, name] of [[registration, "business_registration-d0c00001.pdf"], [photoId, "business_photo_id-d0c00002.jpg"]] as const) {
        const [download] = await Promise.all([page.waitForEvent("download"), block.getByRole("button", { name: "Download" }).click()]);
        expect(download.suggestedFilename()).toBe(name);
    }
    expect(backend.calls.filter((call) => call.path.includes("/documents/")).map((call) => call.path)).toEqual([
        `${BASE}/${TENANT_A}/${REQUEST_1}/documents/${DOC_1}`, `${BASE}/${TENANT_A}/${REQUEST_1}/documents/${DOC_2}`,
    ]);

    // Approve, with the confirmation.
    await page.getByRole("button", { name: "Approve" }).click();
    const approve = page.getByRole("dialog");
    await expect(approve).toContainText("Approve this request?");
    await expect(approve).toContainText("The customer will be emailed that their phone number request was approved. This cannot be undone.");
    await expect(approve.getByRole("textbox")).toHaveCount(0);
    await approve.getByRole("button", { name: "Approve" }).click();
    await expect(page.getByRole("status")).toHaveText("Approved. The customer was emailed.");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Approve" })).toHaveCount(0);
    expect(posts(backend)).toEqual([{ method: "POST", path: `${BASE}/${TENANT_A}/${REQUEST_1}/decision`, search: "", body: { decision: "approved" } }]);

    // Back to the queue, and reject the other request with a reason.
    await page.getByRole("link", { name: "Back to the queue" }).click();
    await expect(page).toHaveURL(`${ADMIN_BASE_URL}/number-requests`);
    await expect(page.getByRole("row").filter({ hasText: "Contoso Dental" })).toHaveCount(0);
    await page.getByRole("link", { name: "Fabrikam Logistics" }).click();
    await expect(page.getByRole("article", { name: "Utility bill (proof of address)" })).toContainText("PNG");
    await expect(page.getByRole("article", { name: "Photo ID matching the utility bill" })).toBeVisible();
    await page.getByRole("button", { name: "Reject" }).click();
    const reject = page.getByRole("dialog");
    await expect(reject).toContainText("Reject this request?");
    await expect(reject).toContainText("The customer will be emailed that their phone number request was not approved. This cannot be undone.");
    await expect(reject).toContainText("The customer is emailed this reason.");
    await expect(reject.getByRole("button", { name: "Reject" })).toBeDisabled();
    await reject.getByRole("textbox", { name: "Reason" }).fill("The utility bill is older than three months");
    await reject.getByRole("button", { name: "Reject" }).click();
    await expect(page.getByRole("status")).toHaveText("Rejected. No email address is on file, so no email was sent.");
    await expect(page.getByText("The utility bill is older than three months")).toBeVisible(); // the stored reason, read again
    expect(posts(backend)[1]).toEqual({
        method: "POST", path: `${BASE}/${TENANT_B}/${REQUEST_2}/decision`, search: "", body: { decision: "rejected", note: "The utility bill is older than three months" },
    });
    expect(posts(backend)).toHaveLength(2);
    expect(backend.unanswered).toEqual([]);
});

// ───────────────────────── super_admin, as the backend is built ─────────────────────────

test("super_admin: the entry shows, and the list shows the server's 403 because the route needs the stored role to be platform_admin", async ({ page }) => {
    const backend = await mockBackend(page, "super_admin");
    // The route's session check (BE/api/v1/endpoints/admin/telephony_number_requests.py:130-131).
    backend.list = () => refusal(403, "forbidden", "Current platform admin authority is required.");
    await page.goto(`${ADMIN_BASE_URL}/number-requests`);
    await expect(page.getByRole("link", { name: "Phone Number Requests" })).toBeVisible();
    await expect(page.getByRole("alert")).toHaveText("Requests unavailable: Current platform admin authority is required.");
    await expect(page.getByRole("table")).toHaveCount(0);
    await expect(page.getByText("No requests with this status.")).toHaveCount(0);
    await page.waitForTimeout(1000);
    expect(backend.calls.map((call) => `${call.method} ${call.path}${call.search}`)).toEqual([`GET ${BASE}?status=submitted&limit=20&offset=0`]);
    expect(backend.unanswered).toEqual([]);
});

// ───────────────────────── every other role ─────────────────────────

for (const role of ["partner_admin", "tenant_admin", "user", "readonly"] as const) {
    test(`${role}: no sidebar entry, Access Denied at the list and at a request, and no number-request call`, async ({ page }) => {
        const backend = await mockBackend(page, role);
        for (const path of ["/number-requests", requestPath(TENANT_A, REQUEST_1)]) {
            await page.goto(`${ADMIN_BASE_URL}${path}`);
            await expect(page.getByRole("heading", { name: "Access Denied" })).toBeVisible(); // AD/components/AdminRouteGuard.tsx:53
            await expect(page.getByText("Phone Number Requests")).toHaveCount(0);
            await expect(page.getByRole("heading", { name: "Phone number request" })).toHaveCount(0);
        }
        if (role === "partner_admin" || role === "tenant_admin") {
            // These roles have one page, Actions (AD/lib/admin-routes.ts:12): its sidebar must not list the screen.
            await page.goto(`${ADMIN_BASE_URL}/actions`);
            await expect(page.getByRole("link", { name: "Actions", exact: true })).toBeVisible();
            await expect(page.locator(".sidebar-nav-item")).toHaveText(["Actions"]);
        } else {
            // These roles reach no page with a sidebar at all (AD/lib/admin-routes.ts:30).
            await page.goto(`${ADMIN_BASE_URL}/actions`);
            await expect(page.getByRole("heading", { name: "Access Denied" })).toBeVisible();
            await expect(page.locator(".sidebar-nav-item")).toHaveCount(0);
        }
        await expect(page.locator('a[href="/number-requests"]')).toHaveCount(0);
        expect(backend.calls).toEqual([]);
        expect(backend.unanswered).toEqual([]);
    });
}

// ───────────────────────── one error per route, and the second decision ─────────────────────────

test("each route's error shows the server's own message, and nothing is sent again", async ({ page }) => {
    const backend = await mockBackend(page, "platform_admin");

    // R1 503 (:112-114).
    backend.list = () => refusal(503, "staff_unavailable", "This could not be completed. Nothing was changed. Try again.");
    await page.goto(`${ADMIN_BASE_URL}/number-requests`);
    await expect(page.getByRole("alert")).toHaveText("Requests unavailable: This could not be completed. Nothing was changed. Try again.");
    await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
    await page.waitForTimeout(700);
    expect(listCalls(backend)).toHaveLength(1);

    // R2 404 (:199): a request asked for under an account it does not belong to.
    await page.goto(`${ADMIN_BASE_URL}${requestPath(TENANT_B, REQUEST_3)}`);
    await expect(page.getByRole("alert")).toHaveText("That request was not found.");
    await expect(page.getByRole("button", { name: "Approve" })).toHaveCount(0);

    // R3 410 (:236): the file was deleted after the page was read.
    backend.details[REQUEST_1] = () => ok(detail());
    backend.document = () => refusal(410, "document_deleted", "That document has been deleted.");
    await page.goto(`${ADMIN_BASE_URL}${requestPath(TENANT_A, REQUEST_1)}`);
    const registration = page.getByRole("article", { name: "Business registration", exact: true });
    let downloads = 0;
    page.on("download", () => { downloads += 1; });
    await registration.getByRole("button", { name: "Download" }).click();
    await expect(registration.getByRole("alert")).toHaveText("That document has been deleted.");
    await expect(page.getByRole("article", { name: "Photo ID matching the business registration" }).getByRole("alert")).toHaveCount(0);
    expect(downloads).toBe(0);
    expect(backend.calls.filter((call) => call.path.includes("/documents/"))).toHaveLength(1);

    // R4 422 note_required (:306-307): the dialog stays open with the server's sentence.
    backend.decide = () => NOTE_REQUIRED;
    await page.getByRole("button", { name: "Reject" }).click();
    const reject = page.getByRole("dialog");
    await reject.getByRole("textbox", { name: "Reason" }).fill("A reason");
    await reject.getByRole("button", { name: "Reject" }).click();
    await expect(reject.getByRole("alert")).toHaveText("Give a reason when you reject a request.");
    await expect(reject.getByRole("textbox", { name: "Reason" })).toHaveValue("A reason");
    await page.waitForTimeout(500);
    expect(posts(backend)).toHaveLength(1);
    await expect(page.getByRole("status")).toHaveCount(0);
    expect(backend.unanswered).toEqual([]);
});

test("a second decision shows the server's 409, is never sent again, and Reload reads the request", async ({ page }) => {
    const backend = await mockBackend(page, "platform_admin");
    let current = detail();
    backend.details[REQUEST_1] = () => ok(current);
    backend.decide = () => {
        // Someone else approved it first (:317-323).
        current = detail({ status: "approved", reviewed_at: REVIEWED_AT, reviewed_by_user_id: USER_ID });
        return ALREADY_DECIDED("approved");
    };
    await page.goto(`${ADMIN_BASE_URL}${requestPath(TENANT_A, REQUEST_1)}`);
    await page.getByRole("button", { name: "Approve" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "Approve" }).click();
    await expect(dialog.getByRole("alert")).toHaveText("That request has already been decided.");
    await expect(dialog.getByRole("button", { name: "Approve" })).toBeDisabled();
    await dialog.getByRole("button", { name: "Approve" }).click({ force: true });
    await dialog.getByRole("button", { name: "Cancel" }).click();
    const banner = page.locator(".number-request-decision").getByRole("alert");
    await expect(banner).toContainText("That request has already been decided.");
    await expect(page.getByRole("button", { name: "Approve" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Reject" })).toBeDisabled();
    const reads = backend.calls.filter((call) => call.method === "GET").length;
    await banner.getByRole("button", { name: "Reload" }).click();
    await expect(page.locator(".number-request-summary")).toContainText("Approved");
    await expect(page.getByRole("button", { name: "Approve" })).toHaveCount(0);
    await expect(page.getByRole("alert")).toHaveCount(0);
    expect(backend.calls.filter((call) => call.method === "GET").length).toBe(reads + 1);
    expect(posts(backend)).toHaveLength(1);
    expect(backend.unanswered).toEqual([]);
});

// ───────────────────────── measurements, every width ─────────────────────────

type Measure = { width: number; height: number; group: "dialog" | "tabs" | "page" | "sidebar"; state: string; pass: boolean; failures: string[]; numbers: Record<string, unknown> };

function record(entries: Measure[]) {
    if (!MEASUREMENTS_FILE || entries.length === 0) return;
    mkdirSync(dirname(MEASUREMENTS_FILE), { recursive: true });
    const before: Measure[] = existsSync(MEASUREMENTS_FILE) ? (JSON.parse(readFileSync(MEASUREMENTS_FILE, "utf8")) as { measurements: Measure[] }).measurements : [];
    const kept = before.filter((old) => !entries.some((entry) => entry.width === old.width && entry.group === old.group && entry.state === old.state));
    const measurements = [...kept, ...entries].sort((a, b) => a.width - b.width || a.group.localeCompare(b.group) || a.state.localeCompare(b.state));
    writeFileSync(MEASUREMENTS_FILE, `${JSON.stringify({
        widths: SIZES.map(([width, height]) => `${width}x${height}`), theme: "light (the console has one theme)", role: "platform_admin",
        total: measurements.length, passed: measurements.filter((entry) => entry.pass).length, failed: measurements.filter((entry) => !entry.pass).length,
        measurements,
    }, null, 1)}\n`);
}

/** The opened dialog: inside the window, scrolls inside when tall, every control on top, nothing spilling, no text cut. */
async function measureDialog(page: Page, state: string): Promise<Measure> {
    const dialog = page.locator(".modal-content");
    await expect(dialog).toBeVisible();
    // The opening animation scales the box; measure it at rest. (The busy spinner turns for ever and is not waited for.)
    await page.waitForFunction(() => {
        const box = document.querySelector(".modal-content"), backdrop = document.querySelector(".modal-backdrop");
        return Boolean(box && backdrop) && [...box!.getAnimations(), ...backdrop!.getAnimations()].every((animation) => animation.playState !== "running");
    });
    const result = await page.evaluate(() => {
        const T = 0.5;
        const box = document.querySelector<HTMLElement>(".modal-content")!;
        const body = box.querySelector<HTMLElement>(".modal-body")!;
        const rectOf = (el: Element): [number, number, number, number] => { const r = el.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; };
        const round = (r: [number, number, number, number]) => r.map((value) => Math.round(value * 10) / 10) as [number, number, number, number];
        const within = (inner: [number, number, number, number], outer: [number, number, number, number]) =>
            inner[0] >= outer[0] - T && inner[1] >= outer[1] - T && inner[2] <= outer[2] + T && inner[3] <= outer[3] + T;
        const failures: string[] = [];
        const view: [number, number, number, number] = [0, 0, window.innerWidth, window.innerHeight];
        const boxRect = rectOf(box);
        if (!within(boxRect, view)) failures.push("dialog outside the window");

        // Taller than its room: the body scrolls inside, the header and the buttons stay put.
        const bodyScrolls = body.scrollHeight > body.clientHeight + 1;
        const bodyOverflow = getComputedStyle(body).overflowY;
        if (bodyScrolls && !["auto", "scroll"].includes(bodyOverflow)) failures.push("tall content does not scroll inside");
        if (bodyScrolls) {
            body.scrollTop = body.scrollHeight;
            if (body.scrollTop + body.clientHeight < body.scrollHeight - 1) failures.push("the end of the content cannot be scrolled to");
            body.scrollTop = 0;
        }

        // No overlap: the centre and two corners of every control hit that control.
        const footer = [...box.querySelectorAll<HTMLElement>(".modal-footer button")];
        const controls: Array<[string, HTMLElement | null]> = [
            ["Close", box.querySelector<HTMLElement>(".modal-close")], ["Reason", box.querySelector<HTMLElement>("textarea")],
            ["Cancel", footer[0] ?? null], ["Confirm", footer[1] ?? null],
        ];
        const hits: Record<string, unknown> = {};
        for (const [name, control] of controls) {
            if (!control) { if (name !== "Reason") failures.push(`${name} missing`); continue; }
            if (body.contains(control)) control.scrollIntoView({ block: "nearest" });
            const r = control.getBoundingClientRect();
            const points: Array<[number, number]> = [[r.left + r.width / 2, r.top + r.height / 2], [r.left + 3, r.top + 3], [r.right - 3, r.bottom - 3]];
            const onTop = points.map(([x, y]) => { const hit = document.elementFromPoint(x, y); return Boolean(hit && (hit === control || control.contains(hit))); });
            const inWindow = within([r.left, r.top, r.right, r.bottom], view);
            if (!onTop.every(Boolean)) failures.push(`${name} is covered`);
            if (!inWindow) failures.push(`${name} outside the window`);
            hits[name] = { rect: round([r.left, r.top, r.right, r.bottom]), onTop, inWindow, disabled: (control as HTMLButtonElement).disabled === true };
        }
        body.scrollTop = 0;

        // No spill: no sideways page scroll, and no child outside the dialog. What the scrolling body clips is checked sideways only.
        const pageScrollWidth = document.documentElement.scrollWidth;
        if (pageScrollWidth > window.innerWidth) failures.push("sideways page scroll");
        const fresh = rectOf(box);
        let outside = 0;
        for (const child of box.querySelectorAll<HTMLElement>("*")) {
            const r = rectOf(child);
            if (r[2] - r[0] === 0 && r[3] - r[1] === 0) continue;
            const inBody = body.contains(child) && child !== body;
            const okay = inBody ? r[0] >= fresh[0] - T && r[2] <= fresh[2] + T && (bodyScrolls || within(r, fresh)) : within(r, fresh);
            if (!okay) outside += 1;
        }
        if (outside) failures.push(`${outside} child element(s) outside the dialog`);

        // No clipping: every text element shows all of its text. The reason box is a text field: it may scroll inside itself, never sideways.
        const texts: Record<string, unknown> = {};
        const textElements: Array<[string, HTMLElement | null]> = [
            ["title", box.querySelector<HTMLElement>(".modal-title")], ["message", box.querySelector<HTMLElement>(".modal-message")],
            ["reason label", box.querySelector<HTMLElement>(".modal-reason-field > span")], ["helper line", box.querySelector<HTMLElement>(".modal-reason-field small")],
            ["error", box.querySelector<HTMLElement>(".error-banner p")], ["Cancel", footer[0] ?? null], ["Confirm", footer[1] ?? null],
        ];
        for (const [name, element] of textElements) {
            if (!element) continue;
            const inline = getComputedStyle(element).display === "inline";
            const cut = !inline && (element.scrollWidth > element.clientWidth + 1 || element.scrollHeight > element.clientHeight + 1);
            if (cut) failures.push(`${name} text is cut`);
            texts[name] = inline ? { inline: true } : { scrollWidth: element.scrollWidth, clientWidth: element.clientWidth, scrollHeight: element.scrollHeight, clientHeight: element.clientHeight };
        }
        const reason = box.querySelector<HTMLTextAreaElement>("textarea");
        if (reason) {
            const scrollsInside = reason.scrollHeight > reason.clientHeight + 1;
            if (reason.scrollWidth > reason.clientWidth + 1) failures.push("the reason box scrolls sideways");
            if (scrollsInside && !["auto", "scroll"].includes(getComputedStyle(reason).overflowY)) failures.push("the reason text is cut");
            texts["reason box"] = { characters: reason.value.length, scrollWidth: reason.scrollWidth, clientWidth: reason.clientWidth,
                scrollHeight: reason.scrollHeight, clientHeight: reason.clientHeight, scrollsInside };
        }
        return { failures, numbers: { window: [window.innerWidth, window.innerHeight], dialog: round(boxRect), bodyScrollsInside: bodyScrolls,
            body: { scrollHeight: body.scrollHeight, clientHeight: body.clientHeight, overflowY: bodyOverflow }, pageScrollWidth, controls: hits, texts } };
    });
    const size = page.viewportSize()!;
    return { width: size.width, height: size.height, group: "dialog", state, pass: result.failures.length === 0, failures: result.failures, numbers: result.numbers };
}

/** The three tabs: inside the window and their row, text whole, not overlapping, reachable without a sideways page scroll. */
async function measureTabs(page: Page, state: string): Promise<Measure> {
    const result = await page.evaluate(() => {
        const T = 0.5;
        const failures: string[] = [];
        const row = document.querySelector<HTMLElement>('nav[aria-label="Filter by status"]')!;
        const rowRect = row.getBoundingClientRect();
        const tabs = [...row.querySelectorAll<HTMLElement>("a")];
        if (tabs.length !== 3) failures.push(`${tabs.length} tabs`);
        if (document.documentElement.scrollWidth > window.innerWidth) failures.push("sideways page scroll");
        if (row.scrollWidth > row.clientWidth + 1) failures.push("the tab row scrolls sideways");
        const numbers: Record<string, unknown> = { window: [window.innerWidth, window.innerHeight], row: [rowRect.left, rowRect.top, rowRect.right, rowRect.bottom].map(Math.round),
            pageScrollWidth: document.documentElement.scrollWidth };
        const rects = tabs.map((tab) => tab.getBoundingClientRect());
        tabs.forEach((tab, index) => {
            const r = rects[index], name = tab.textContent!.trim();
            const inWindow = r.left >= -T && r.top >= -T && r.right <= window.innerWidth + T && r.bottom <= window.innerHeight + T;
            const inRow = r.left >= rowRect.left - T && r.right <= rowRect.right + T && r.top >= rowRect.top - T && r.bottom <= rowRect.bottom + T;
            const whole = tab.scrollWidth <= tab.clientWidth + 1 && tab.scrollHeight <= tab.clientHeight + 1;
            const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
            const onTop = Boolean(hit && (hit === tab || tab.contains(hit)));
            const overlaps = rects.some((other, at) => at !== index && r.left < other.right - T && other.left < r.right - T && r.top < other.bottom - T && other.top < r.bottom - T);
            if (!inWindow) failures.push(`${name} outside the window`);
            if (!inRow) failures.push(`${name} outside its row`);
            if (!whole) failures.push(`${name} text is cut`);
            if (!onTop) failures.push(`${name} is covered`);
            if (overlaps) failures.push(`${name} overlaps another tab`);
            numbers[name] = { rect: [r.left, r.top, r.right, r.bottom].map((value) => Math.round(value * 10) / 10), inWindow, inRow, whole, onTop, overlaps,
                current: tab.getAttribute("aria-current") === "true" };
        });
        return { failures, numbers };
    });
    const size = page.viewportSize()!;
    return { width: size.width, height: size.height, group: "tabs", state, pass: result.failures.length === 0, failures: result.failures, numbers: result.numbers };
}

/** A whole page: no sideways page scroll, the table scrolls inside its own box, the named long values and the sidebar entry stay whole. */
async function measurePage(page: Page, state: string, longValues: string[]): Promise<Measure> {
    const result = await page.evaluate((values) => {
        const T = 0.5;
        const failures: string[] = [];
        const numbers: Record<string, unknown> = { window: [window.innerWidth, window.innerHeight], pageScrollWidth: document.documentElement.scrollWidth,
            bodyScrollWidth: document.body.scrollWidth };
        if (document.documentElement.scrollWidth > window.innerWidth || document.body.scrollWidth > window.innerWidth) failures.push("sideways page scroll");

        const container = document.querySelector<HTMLElement>(".table-container");
        if (container) {
            const r = container.getBoundingClientRect();
            const table = container.querySelector<HTMLElement>("table")!;
            const scrolls = container.scrollWidth > container.clientWidth + 1;
            const inWindow = r.left >= -T && r.right <= window.innerWidth + T;
            if (!inWindow) failures.push("the table's box is outside the window");
            if (scrolls && !["auto", "scroll"].includes(getComputedStyle(container).overflowX)) failures.push("the table does not scroll inside its own box");
            numbers.table = { box: [r.left, r.right].map(Math.round), tableWidth: Math.round(table.getBoundingClientRect().width), scrollsInsideItsBox: scrolls, inWindow };
        }

        // Each long value: fully inside the window, or inside a sideways-scrolling box that is itself inside the window.
        const found: Record<string, unknown> = {};
        for (const value of values) {
            const walker = document.createTreeWalker(document.querySelector("main") ?? document.body, NodeFilter.SHOW_TEXT);
            let element: HTMLElement | null = null;
            for (let node = walker.nextNode(); node; node = walker.nextNode()) {
                if (node.textContent === value) { element = node.parentElement; break; }
            }
            const key = value.length > 40 ? `${value.slice(0, 24)}… (${value.length} characters)` : value;
            if (!element) { failures.push(`long value not on the page: ${key}`); continue; }
            element.scrollIntoView({ block: "nearest", inline: "nearest" });
            const r = element.getBoundingClientRect();
            const insideWindow = r.left >= -T && r.right <= window.innerWidth + T;
            let scroller: HTMLElement | null = element.parentElement;
            while (scroller && !(["auto", "scroll"].includes(getComputedStyle(scroller).overflowX) && scroller.scrollWidth > scroller.clientWidth + 1)) scroller = scroller.parentElement;
            const scrollerRect = scroller?.getBoundingClientRect();
            const insideScroller = Boolean(scrollerRect && scrollerRect.left >= -T && scrollerRect.right <= window.innerWidth + T);
            const whole = element.scrollWidth <= element.clientWidth + 1 || getComputedStyle(element).display === "inline";
            if (!insideWindow && !insideScroller) failures.push(`long value outside the window: ${key}`);
            if (!whole) failures.push(`long value is cut: ${key}`);
            found[key] = { rect: [r.left, r.top, r.right, r.bottom].map(Math.round), insideWindow, insideItsScrollingBox: !insideWindow && insideScroller, whole };
        }
        numbers.longValues = found;
        document.querySelector(".table-container")?.scrollTo({ left: 0 });

        // The sidebar entry: its words are whole and it can be brought fully into the window.
        const entry = document.querySelector<HTMLElement>('a.sidebar-nav-item[href="/number-requests"]');
        if (!entry) failures.push("no sidebar entry");
        else {
            entry.scrollIntoView({ block: "nearest", inline: "nearest" });
            const words = entry.querySelector<HTMLElement>("span")!;
            const r = entry.getBoundingClientRect(), w = words.getBoundingClientRect();
            const whole = words.scrollWidth <= Math.ceil(w.width) + 1 && w.left >= r.left - T && w.right <= r.right + T && w.top >= r.top - T && w.bottom <= r.bottom + T;
            const inWindow = r.left >= -T && r.right <= window.innerWidth + T && r.top >= -T && r.bottom <= window.innerHeight + T;
            const hit = document.elementFromPoint(w.left + w.width / 2, w.top + w.height / 2);
            const onTop = Boolean(hit && entry.contains(hit));
            if (!whole) failures.push("the sidebar entry's text is cut");
            if (!inWindow) failures.push("the sidebar entry cannot be brought inside the window");
            if (!onTop) failures.push("the sidebar entry is covered");
            numbers.sidebarEntry = { entry: [r.left, r.top, r.right, r.bottom].map(Math.round), words: [w.left, w.top, w.right, w.bottom].map(Math.round),
                wordsScrollWidth: words.scrollWidth, lines: Math.round(w.height / parseFloat(getComputedStyle(words).lineHeight || "20")) || 1, whole, inWindow, onTop };
        }
        return { failures, numbers };
    }, longValues);
    const size = page.viewportSize()!;
    return { width: size.width, height: size.height, group: "page", state, pass: result.failures.length === 0, failures: result.failures, numbers: result.numbers };
}

/**
 * The sidebar on an existing page. From 601px up it is a column fixed to the window's height: it is inside the window, its entries
 * scroll inside it only when they are taller than it, and the last entry ("Phone Number Requests") can then be scrolled fully into
 * view. At 600px and below it is the sideways strip, unchanged. At every width no entry's text is cut and the page does not scroll sideways.
 */
async function measureSidebar(page: Page, state: string): Promise<Measure> {
    const result = await page.evaluate(() => {
        const T = 0.5;
        const failures: string[] = [];
        const column = window.innerWidth >= 601;
        const sidebar = document.querySelector<HTMLElement>("aside.sidebar")!;
        const nav = sidebar.querySelector<HTMLElement>(".sidebar-nav")!;
        const entries = [...nav.querySelectorAll<HTMLElement>(".sidebar-nav-item")];
        const last = entries[entries.length - 1];
        const round = (r: DOMRect) => [r.left, r.top, r.right, r.bottom].map((value) => Math.round(value * 10) / 10);
        const pageScrollWidth = document.documentElement.scrollWidth;
        if (pageScrollWidth > window.innerWidth || document.body.scrollWidth > window.innerWidth) failures.push("sideways page scroll");
        if (entries.length !== 17) failures.push(`${entries.length} entries`);
        if (last?.textContent?.trim() !== "Phone Number Requests") failures.push("the last entry is not Phone Number Requests");

        // No entry's text is cut: its words fit their own box, and that box lies inside the entry.
        const cut: string[] = [];
        for (const entry of entries) {
            const words = entry.querySelector<HTMLElement>("span")!;
            const e = entry.getBoundingClientRect(), w = words.getBoundingClientRect();
            const whole = words.scrollWidth <= Math.ceil(w.width) + 1 && words.scrollHeight <= Math.ceil(w.height) + 1
                && w.left >= e.left - T && w.right <= e.right + T && w.top >= e.top - T && w.bottom <= e.bottom + T;
            if (!whole) cut.push(words.textContent ?? "");
        }
        if (cut.length) failures.push(`text cut: ${cut.join(", ")}`);

        const navStyle = getComputedStyle(nav);
        const numbers: Record<string, unknown> = {
            window: [window.innerWidth, window.innerHeight], layout: column ? "column (601px and wider)" : "strip (600px and narrower)",
            pageScrollWidth, entries: entries.length, entriesWithCutText: cut.length,
            nav: { scrollHeight: nav.scrollHeight, clientHeight: nav.clientHeight, scrollWidth: nav.scrollWidth, clientWidth: nav.clientWidth,
                overflowY: navStyle.overflowY, overflowX: navStyle.overflowX },
        };
        if (column) {
            const s = sidebar.getBoundingClientRect();
            const sidebarInWindow = s.left >= -T && s.top >= -T && s.right <= window.innerWidth + T && s.bottom <= window.innerHeight + T;
            if (!sidebarInWindow) failures.push("the sidebar column is outside the window");
            const taller = nav.scrollHeight > nav.clientHeight + 1;
            numbers.sidebar = round(s);
            numbers.sidebarInWindow = sidebarInWindow;
            numbers.entriesTallerThanColumn = taller;
            if (taller) {
                if (!["auto", "scroll"].includes(navStyle.overflowY)) failures.push("the entries are taller than the column and the nav does not scroll inside itself");
                last.scrollIntoView({ block: "nearest" });
                const n = nav.getBoundingClientRect(), r = last.getBoundingClientRect();
                const inNav = r.top >= n.top - T && r.bottom <= n.bottom + T;
                const inWindow = r.left >= -T && r.top >= -T && r.right <= window.innerWidth + T && r.bottom <= window.innerHeight + T;
                const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
                const onTop = Boolean(hit && last.contains(hit));
                if (!inNav || !inWindow || !onTop) failures.push("the last entry cannot be scrolled fully into view");
                numbers.lastEntry = { rect: round(r), scrolledTo: nav.scrollTop, fullyInView: inNav && inWindow && onTop };
                nav.scrollTop = 0;
            } else {
                // The entries fit: nothing scrolls inside the nav, and the last entry is in the window as it stands.
                if (nav.scrollHeight > nav.clientHeight) failures.push("the nav has an inner scroll although the entries fit");
                const r = last.getBoundingClientRect();
                const inWindow = r.top >= -T && r.bottom <= window.innerHeight + T && r.left >= -T && r.right <= window.innerWidth + T;
                if (!inWindow) failures.push("the last entry is outside the window");
                numbers.lastEntry = { rect: round(r), scrolledTo: 0, fullyInView: inWindow };
            }
        } else {
            // The strip, as before: one row that scrolls sideways inside the nav, never the page.
            const rows = new Set(entries.map((entry) => Math.round(entry.getBoundingClientRect().top)));
            numbers.strip = { rows: rows.size, scrollsSidewaysInsideItself: nav.scrollWidth > nav.clientWidth + 1, position: getComputedStyle(sidebar).position };
            if (rows.size !== 1) failures.push(`the strip has ${rows.size} rows`);
            if (nav.scrollWidth > nav.clientWidth + 1 && !["auto", "scroll"].includes(navStyle.overflowX)) failures.push("the strip does not scroll inside itself");
        }
        return { failures, numbers };
    });
    const size = page.viewportSize()!;
    return { width: size.width, height: size.height, group: "sidebar", state, pass: result.failures.length === 0, failures: result.failures, numbers: result.numbers };
}

for (const [width, height] of SIZES) {
    test(`platform staff ${width}x${height}: every dialog state opened, the tabs and both pages fit`, async ({ page }) => {
        await page.setViewportSize({ width, height });
        const backend = await mockBackend(page, "platform_admin");
        const measures: Measure[] = [];
        expect([LONG_NAME.length, LONG_REASON.length]).toEqual([60, 1000]);
        const take = async (measure: Promise<Measure>) => {
            const entry = await measure;
            measures.push(entry);
            console.log(`[measure] ${width}x${height} ${entry.group} ${entry.state}: ${entry.pass ? "pass" : `FAIL ${entry.failures.join("; ")}`}`);
        };

        // ── the list, with a 60-character company name and an account id standing in for a missing name ──
        backend.list = (status, offset) => ok({ limit: 20, offset, has_more: true, items: [
            item({ id: REQUEST_2, business_name: LONG_NAME, status, reviewed_at: status === "submitted" ? null : REVIEWED_AT }),
            item({ id: REQUEST_3, tenant_id: TENANT_B, business_name: null, use_type: "personal", status, reviewed_at: status === "submitted" ? null : REVIEWED_AT, files_deleted: status !== "submitted" }),
            item({ status, reviewed_at: status === "submitted" ? null : REVIEWED_AT }),
        ] });
        await page.goto(`${ADMIN_BASE_URL}/number-requests`);
        await expect(page.getByRole("link", { name: LONG_NAME })).toBeVisible();
        await take(measurePage(page, "list (60-character company, account id for a missing name)", [LONG_NAME, TENANT_B]));
        const tabs = page.getByRole("navigation", { name: "Filter by status" });
        await take(measureTabs(page, "Submitted current"));
        for (const label of ["Approved", "Rejected"]) {
            await tabs.getByRole("link", { name: label }).click();
            await expect(tabs.locator('[aria-current="true"]')).toHaveText(label);
            await expect(page.getByRole("row").filter({ hasText: LONG_NAME })).toContainText(label);
            await take(measureTabs(page, `${label} current`));
        }
        // Every tab can be reached and pressed without a sideways page scroll (checked above), from the keyboard too.
        await tabs.getByRole("link", { name: "Submitted" }).focus();
        await page.keyboard.press("Enter");
        await expect(tabs.locator('[aria-current="true"]')).toHaveText("Submitted");

        // ── a decided request: 60-character company and a 1000-character stored reason ──
        backend.details[REQUEST_2] = () => ok(detail({ id: REQUEST_2, business_name: LONG_NAME, status: "rejected", review_note: LONG_REASON, reviewed_at: REVIEWED_AT, reviewed_by_user_id: STAFF_ID }));
        await page.goto(`${ADMIN_BASE_URL}${requestPath(TENANT_A, REQUEST_2)}`);
        await expect(page.locator(".number-request-reason dd")).toHaveText(LONG_REASON);
        await take(measurePage(page, "request (60-character company, 1000-character stored reason)", [LONG_NAME, LONG_REASON]));

        // ── a request whose company name is missing: the account id is shown ──
        backend.details[REQUEST_3] = () => ok(detail({ id: REQUEST_3, tenant_id: TENANT_B, business_name: null, requester_email: null }));
        await page.goto(`${ADMIN_BASE_URL}${requestPath(TENANT_B, REQUEST_3)}`);
        await expect(page.locator(".number-request-summary")).toContainText(TENANT_B);
        await take(measurePage(page, "request (account id for a missing name)", [TENANT_B]));

        // ── the dialogs, on a submitted request ──
        backend.details[REQUEST_1] = () => ok(detail());
        await page.goto(`${ADMIN_BASE_URL}${requestPath(TENANT_A, REQUEST_1)}`);
        const dialog = page.getByRole("dialog");
        const confirm = (label: string) => dialog.locator(".modal-footer").getByRole("button").last().filter({ hasText: new RegExp(`${label}|Processing`) });
        const reloadFromBanner = async () => {
            await dialog.getByRole("button", { name: "Cancel" }).click();
            await page.locator(".number-request-decision").getByRole("alert").getByRole("button", { name: "Reload" }).click();
            await expect(page.locator(".number-request-decision").getByRole("alert")).toHaveCount(0);
            await expect(page.getByRole("button", { name: "Approve" })).toBeEnabled();
        };
        /** Press confirm with the answer held back, measure the busy dialog, then let the answer through. */
        const sendHeld = async (label: string, answer: Answer, busyState: string) => {
            const held = gate();
            backend.hold = held.promise;
            backend.decide = () => answer;
            await confirm(label).click();
            await expect(dialog).toContainText("Processing...");
            await expect(dialog.getByRole("button", { name: "Cancel" })).toBeDisabled();
            await take(measureDialog(page, busyState));
            held.release();
            backend.hold = null;
            await expect(dialog.getByRole("alert")).toBeVisible();
        };

        // Approve: open, busy, 409 shown.
        await page.getByRole("button", { name: "Approve" }).click();
        await expect(dialog).toContainText("Approve this request?");
        await take(measureDialog(page, "approve: open"));
        await sendHeld("Approve", ALREADY_DECIDED("rejected"), "approve: busy");
        await expect(dialog.getByRole("alert")).toHaveText("That request has already been decided.");
        await take(measureDialog(page, "approve: 409 shown"));
        await reloadFromBanner();
        // Approve: 503 decision_unconfirmed shown.
        await page.getByRole("button", { name: "Approve" }).click();
        backend.decide = () => UNCONFIRMED;
        await confirm("Approve").click();
        await expect(dialog.getByRole("alert")).toHaveText("The decision is not confirmed. Read the request again before trying again.");
        await take(measureDialog(page, "approve: 503 decision_unconfirmed shown"));
        await reloadFromBanner();

        // Reject: open and empty, a short reason, a 1000-character reason, busy, then 422, 409 and 503 with the 1000 characters.
        const reason = dialog.getByRole("textbox", { name: "Reason" });
        await page.getByRole("button", { name: "Reject" }).click();
        await expect(dialog).toContainText("Reject this request?");
        await expect(confirm("Reject")).toBeDisabled();
        await take(measureDialog(page, "reject: open and empty (confirm disabled)"));
        await reason.fill("Photo is unreadable");
        await expect(confirm("Reject")).toBeEnabled();
        await take(measureDialog(page, "reject: short reason"));
        await reason.fill(LONG_REASON);
        await expect(reason).toHaveValue(LONG_REASON);
        await take(measureDialog(page, "reject: 1000-character reason"));
        await sendHeld("Reject", NOTE_REQUIRED, "reject: busy");
        await expect(dialog.getByRole("alert")).toHaveText("Give a reason when you reject a request.");
        await take(measureDialog(page, "reject: 1000-character reason + 422 note_required"));
        backend.decide = () => ALREADY_DECIDED("approved");
        await confirm("Reject").click();
        await expect(dialog.getByRole("alert")).toHaveText("That request has already been decided.");
        await take(measureDialog(page, "reject: 1000-character reason + 409"));
        await reloadFromBanner();
        await page.getByRole("button", { name: "Reject" }).click();
        await reason.fill(LONG_REASON);
        backend.decide = () => UNCONFIRMED;
        await confirm("Reject").click();
        await expect(dialog.getByRole("alert")).toHaveText("The decision is not confirmed. Read the request again before trying again.");
        await take(measureDialog(page, "reject: 1000-character reason + 503 decision_unconfirmed"));

        // ── the sidebar on two existing pages: Tenants and Support Tickets ──
        await page.goto(`${ADMIN_BASE_URL}/tenants`);
        await expect(page.getByRole("row").filter({ hasText: "Northwind Traders" })).toHaveCount(1);
        await take(measureSidebar(page, "Tenants page"));
        await page.goto(`${ADMIN_BASE_URL}/support`);
        await expect(page.getByRole("link", { name: "SUP-000123" })).toBeVisible();
        await take(measureSidebar(page, "Support Tickets page"));
        expect(backend.others).toEqual(expect.arrayContaining(["GET /admin/tenants", "GET /admin/support/tickets"]));

        record(measures);
        expect(measures.map((entry) => `${entry.group} | ${entry.state}`)).toHaveLength(19);
        expect(measures.filter((entry) => !entry.pass).map((entry) => `${entry.group} | ${entry.state}: ${entry.failures.join("; ")}`)).toEqual([]);
        expect(backend.unanswered).toEqual([]);
    });
}
