import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { PartnerSummarySchema } from "@/lib/models";
import { invalidateRequestIdentity, setRequestIdentity, setTokenProvider } from "@/lib/http-client";

const originalFetch = globalThis.fetch;
const ID = "10000000-0000-4000-8000-000000000001";
const AT = "2026-10-21T12:34:56+00:00";
const row = (status: string, fields: Record<string, unknown> = {}) => ({
    id: ID, name: "Owned directory partner", status, tenant_count: 0,
    suspended_at: status === "suspended" ? AT : null, updated_at: AT, ...fields,
});
let payload: unknown;
let requests: URL[];
beforeEach(() => {
    invalidateRequestIdentity(); setRequestIdentity({ userId: "operator" }); setTokenProvider(() => "synthetic-only");
    requests = [];
    globalThis.fetch = (async (input) => {
        requests.push(new URL(String(input), "http://localhost"));
        return new Response(JSON.stringify(payload), { status: 200, headers: { "content-type": "application/json" } });
    }) as typeof fetch;
});
afterEach(() => { globalThis.fetch = originalFetch; setTokenProvider(null); invalidateRequestIdentity(); });

for (const state of ["active", "suspended"] as const) test(`actual ${state} partner HTTP row retains snake_case metadata`, async () => {
    payload = { items: [row(state)], total: 1, page: 1, page_size: 8 };
    const { backendApi } = await import("@/lib/backend-api");
    const result = await backendApi.admin.partners.list({ page: 1, pageSize: 8 });
    assert.equal(result.items[0]!.status, state);
    assert.equal(result.items[0]!.tenantCount, 0, "Zero is a recorded tenant count, not missing metadata");
    assert.equal(result.items[0]!.updatedAt, AT);
    assert.equal(result.items[0]!.suspendedAt, state === "suspended" ? AT : undefined);
    assert.equal(result.total, 1);
    assert.equal(requests.length, 1);
});

test("one pending-deletion or database-NULL projection does not reject the whole partner page", async () => {
    // The backend maps its nullable DB status to the explicit wire value unknown.
    payload = { items: [row("active"), row("pending_deletion", { id: `${ID.slice(0, -1)}2` }),
        row("unknown", { id: `${ID.slice(0, -1)}3` })], total: 3, page: 1, page_size: 8 };
    const { backendApi } = await import("@/lib/backend-api");
    const result = await backendApi.admin.partners.list({ page: 1, pageSize: 8 });
    assert.deepEqual(result.items.map(item => item.status), ["active", "pending_deletion", "unknown"]);
    assert.deepEqual(result.items.map(item => item.tenantCount), [0, 0, 0]);
});

for (const state of ["active", "suspended", "pending_deletion", "unknown"] as const) test(`partner list forwards exact ${state} filter and ordinary pagination`, async () => {
    payload = { items: [], total: 0, page: 2, page_size: 8 };
    const { backendApi } = await import("@/lib/backend-api");
    // Exercise the runtime seam before the older TypeScript input union is expanded.
    await backendApi.admin.partners.list({ page: 2, pageSize: 8, query: "Owned", status: state as "active" });
    assert.equal(requests.length, 1);
    assert.equal(requests[0]!.pathname, "/api/v1/admin/partners");
    assert.equal(requests[0]!.searchParams.get("status"), state);
    assert.equal(requests[0]!.searchParams.get("page"), "2");
    assert.equal(requests[0]!.searchParams.get("page_size"), "8");
    assert.equal(requests[0]!.searchParams.get("q"), "Owned");
});

test("existing camelCase and legacy partner_id rows remain compatible", () => {
    assert.deepEqual(PartnerSummarySchema.parse({ id: ID, name: "Camel", status: "suspended",
        tenantCount: 2, suspendedAt: AT, updatedAt: AT }),
    { id: ID, name: "Camel", status: "suspended", tenantCount: 2, suspendedAt: AT, updatedAt: AT });
    assert.deepEqual(PartnerSummarySchema.parse({ partner_id: ID, display_name: "Legacy", status: "active",
        tenant_count: 3, suspended_at: null, updated_at: AT }),
    { id: ID, name: "Legacy", status: "active", tenantCount: 3, suspendedAt: undefined, updatedAt: AT });
});

test("malformed unknown wire status is not silently treated as active", () => {
    assert.equal(PartnerSummarySchema.safeParse(row("future_misspelling")).success, false);
    assert.equal(PartnerSummarySchema.safeParse({ id: ID, name: "Missing status" }).success, false);
});
