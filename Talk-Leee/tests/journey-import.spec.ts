import { expect, test, type Page, type Route } from "@playwright/test";
import { E2E_USER, mockApi, mockDashboardApi, signInAsE2eUser } from "./support/session";

/*
 * P2 journey 3 — import. A signed-in customer imports contacts from a CSV file
 * and sees them in the Contacts list.
 *
 * Every request is answered here, in the shape the backend sends (the server
 * code is named beside each answer; BE/ = backend/app/). The mocked backend is
 * stateful: the campaign has no contacts until the upload, and the list is
 * served from the rows of the file the page really sent.
 *
 * Helpers from ./support/session: signInAsE2eUser (session + page shell),
 * mockDashboardApi (shared reads such as /events), mockApi (GET answers),
 * E2E_USER (the account).
 */

test.setTimeout(120_000);

const CAMPAIGN_ID = "6a1e2d3c-4b5f-4a6e-8d7c-9b0a1f2e3d4c";
const CAMPAIGN_NAME = "Import Journey Campaign";
const LIST_ID = "b7c8d9e0-f1a2-4b3c-8d4e-5f6a7b8c9d0e";
const NOW = "2026-11-17T09:00:00+00:00";

const PEOPLE = [
    { first_name: "Ada", last_name: "Lovelace", phone_number: "+14155552671" },
    { first_name: "Grace", last_name: "Hopper", phone_number: "+14155552672" },
    { first_name: "Katherine", last_name: "Johnson", phone_number: "+14155552673" },
];
const CSV = ["first_name,last_name,phone_number", ...PEOPLE.map((p) => `${p.first_name},${p.last_name},${p.phone_number}`)].join("\n");

/* One campaigns row, as GET /campaigns returns it (BE/api/v1/endpoints/campaigns.py:460-494, `select("*")` :487). */
const CAMPAIGN = {
    id: CAMPAIGN_ID,
    tenant_id: E2E_USER.tenant_id,
    name: CAMPAIGN_NAME,
    description: null,
    status: "draft",
    direction: "outbound",
    system_prompt: "",
    voice_id: "e2e-voice",
    goal: null,
    script_config: { agent_names: ["Alex"] },
    calling_config: null,
    max_concurrent_calls: 1,
    total_leads: 0,
    calls_completed: 0,
    calls_failed: 0,
    created_at: NOW,
    updated_at: NOW,
};

function json(route: Route, status: number, body: unknown) {
    return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

/** "/api/v1/contacts/campaigns/x/upload/" -> "/contacts/campaigns/x/upload". */
function apiPath(route: Route): string {
    return new URL(route.request().url()).pathname.replace(/^.*?\/api\/v1(?=\/|$)/, "").replace(/\/+$/, "");
}

/** The data rows of the CSV inside a multipart upload body, as { header: cell } records. */
function rowsOfUploadedCsv(multipart: string): Array<Record<string, string>> {
    // The file is one part among the permission fields (main 87d049b4): read the part that carries a filename.
    const filePart = multipart.split(/\r?\n--/).find((part) => /filename="/.test(part)) ?? "";
    const csv = filePart.split(/\r?\n\r?\n/).slice(1).join("\n\n");
    const [head, ...lines] = csv.split(/\r?\n/).filter((line) => line.trim() !== "");
    const headers = (head ?? "").split(",").map((h) => h.trim());
    return lines.map((line) => {
        const cells = line.split(",");
        return Object.fromEntries(headers.map((h, i) => [h, (cells[i] ?? "").trim()]));
    });
}

/** The permission-to-call form fields sent beside the file (BE/api/v1/endpoints/contacts.py:297-300). */
function permissionOfUpload(multipart: string): Record<string, string> {
    const fields: Record<string, string> = {};
    for (const part of multipart.split(/\r?\n--/)) {
        const name = /name="(permission_[a-z_]+)"/.exec(part)?.[1];
        if (name) fields[name] = part.split(/\r?\n\r?\n/).slice(1).join("\n\n").trim();
    }
    return fields;
}

async function mockBackend(page: Page) {
    const state = {
        leads: [] as Array<Record<string, unknown>>,
        uploads: [] as Array<{ query: string; rows: Array<Record<string, string>>; permission: Record<string, string> }>,
    };

    await signInAsE2eUser(page);
    await mockDashboardApi(page);
    await mockApi(page, [
        // GET /contacts/fields (BE/api/v1/endpoints/lead_details.py:80-96).
        { path: "/contacts/fields", body: { fields: [], csv_template_headers: [] } },
        // GET /campaigns/{id}/readiness (BE/api/v1/endpoints/campaigns.py:1066-1078; the result is built in
        // BE/domain/services/telephony/outbound_readiness.py:47-90, "ready" at :89).
        {
            path: `/campaigns/${CAMPAIGN_ID}/readiness`,
            body: { campaign_id: CAMPAIGN_ID, ready: true, reason_code: "ready", reason: null, trunk_id: "8c2d4e6f-1a3b-4c5d-9e7f-0a1b2c3d4e5f", caller_id: "+15550100200" },
        },
    ]);

    // Registered last, so it runs first.
    await page.route(
        (url) => /\/api\/v1(?:\/|$)/.test(url.pathname),
        async (route) => {
            const request = route.request();
            if (request.resourceType() === "document") return route.fallback();
            const path = apiPath(route);
            const method = request.method();

            // GET /campaigns: { campaigns } (BE/api/v1/endpoints/campaigns.py:494).
            if (method === "GET" && path === "/campaigns") return json(route, 200, { campaigns: [CAMPAIGN] });

            // GET /campaigns/{id}/contacts: { items, page, page_size, total } (BE/api/v1/endpoints/campaigns.py:2248,
            // answer at :2322-2327; each row through _contact_response :1959-1967).
            if (method === "GET" && path === `/campaigns/${CAMPAIGN_ID}/contacts`) {
                return json(route, 200, { items: state.leads, page: 1, page_size: 50, total: state.leads.length });
            }

            // GET /campaigns/{id}/contact-lists: ContactListOut[] (BE/api/v1/endpoints/contact_lists.py:53-60, :207).
            if (method === "GET" && path === `/campaigns/${CAMPAIGN_ID}/contact-lists`) {
                return json(route, 200, state.leads.length === 0 ? [] : [
                    { id: LIST_ID, name: "contacts.csv", source: "csv", is_active: true, contact_count: state.leads.length, created_at: NOW },
                ]);
            }

            // POST /contacts/campaigns/{id}/upload: BulkImportResponse (BE/api/v1/endpoints/contacts.py:56-77,
            // route :192-199, answer :420-434).
            if (method === "POST" && path === `/contacts/campaigns/${CAMPAIGN_ID}/upload`) {
                const rows = rowsOfUploadedCsv(request.postData() ?? "");
                state.uploads.push({ query: new URL(request.url()).search, rows, permission: permissionOfUpload(request.postData() ?? "") });
                state.leads = rows.map((row, i) => {
                    const first = row.first_name || null;
                    const last = row.last_name || null;
                    return {
                        id: `c0ffee00-0000-4000-8000-00000000000${i + 1}`,
                        tenant_id: E2E_USER.tenant_id,
                        campaign_id: CAMPAIGN_ID,
                        list_id: LIST_ID,
                        phone_number: row.phone_number,
                        mobile_number: row.phone_number,
                        first_name: first,
                        last_name: last,
                        full_name: [first, last].filter(Boolean).join(" ") || null,
                        email: null,
                        custom_fields: {},
                        status: "pending",
                        last_call_result: "pending",
                        call_attempts: 0,
                        created_at: NOW,
                    };
                });
                return json(route, 200, {
                    total_rows: rows.length,
                    imported: rows.length,
                    failed: 0,
                    duplicates_skipped: 0,
                    errors: [],
                    field_errors: [],
                    list_id: LIST_ID,
                    list_name: "contacts.csv",
                    list_contact_count: rows.length,
                });
            }

            return route.fallback();
        },
    );

    return state;
}

test("a signed-in customer imports contacts from a CSV file and sees them in the Contacts list", async ({ page }) => {
    const backend = await mockBackend(page);

    await page.goto("/contacts");
    await expect(page.getByRole("heading", { level: 1, name: "Contacts" })).toBeVisible(); // contacts/page.tsx:1214
    // The campaign has no contacts yet: the page opens on its upload card (contacts/page.tsx:680).
    await expect(page.getByRole("heading", { name: "Upload CSV" })).toBeVisible();

    // Choose the file (contacts/page.tsx:733-741) and check the preview reads all three rows (:819).
    await page.locator("#contacts-csv-file").setInputFiles({ name: "contacts.csv", mimeType: "text/csv", buffer: Buffer.from(CSV, "utf8") });
    await expect(page.getByTestId("contacts-preview-row")).toHaveCount(PEOPLE.length);

    // Permission to call (main 87d049b4): the upload is held until the block is complete (contacts/page.tsx:661-662;
    // its controls and texts: contact-permission-fields.tsx:66-148, lib/contact-permission.ts:46-71).
    await page.getByRole("radio", { name: "They agreed to be called" }).check();
    await page.getByLabel("Where this permission comes from").fill("Website sign-up form, March 2026");
    await page.getByLabel("Date permission was given").fill("2026-03-14");
    await page.getByRole("checkbox", { name: /^I confirm that I am allowed to call the people in this list/ }).check();

    await page.getByRole("button", { name: "Upload 3 contacts" }).click();

    // The result card (contacts/page.tsx:888-899) and, behind it, the Contacts list with the imported people.
    const results = page.getByTestId("contacts-import-results");
    await expect(results.getByRole("heading", { name: "Import Results" })).toBeVisible();
    await expect(results.getByText("Imported", { exact: true })).toBeVisible();
    for (const person of PEOPLE) {
        const row = page.getByRole("row").filter({ hasText: person.phone_number });
        await expect(row).toHaveCount(1);
        await expect(row).toContainText(`${person.first_name} ${person.last_name}`);
    }
    await expect(page.getByRole("alert", { name: "Error notification" })).toHaveCount(0); // notification-toaster.tsx:86

    // What the page sent: one upload with the three people, duplicates skipped (extended-api.ts:149-154).
    expect(backend.uploads).toHaveLength(1);
    expect(backend.uploads[0]?.query).toBe("?skip_duplicates=true");
    expect(backend.uploads[0]?.rows.map((row) => row.phone_number)).toEqual(PEOPLE.map((p) => p.phone_number));
    // ... and the permission the customer confirmed (lib/contact-permission.ts:121-131).
    expect(backend.uploads[0]?.permission).toEqual({
        permission_basis: "express_consent",
        permission_source: "Website sign-up form, March 2026",
        permission_date: "2026-03-14",
        permission_statement_version: "2026-11-v1",
    });
});
