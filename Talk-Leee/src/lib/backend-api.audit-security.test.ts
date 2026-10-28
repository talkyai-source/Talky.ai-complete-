import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { backendApi } from "@/lib/backend-api";
import { AuditLogEventSchema, SecurityEventSchema } from "@/lib/models";
import { invalidateRequestIdentity, setRequestIdentity, setTokenProvider } from "@/lib/http-client";
import { ACTOR_ID, EVENT_TIME, PARTNER_ID, SECURITY_STATUSES, SEVERITIES, SUBJECT_ID, TENANT_ID, auditRow, securityRow } from "@/test-utils/admin-events-fixtures";

const originalFetch = globalThis.fetch;
const AUDIT_PATH = "/api/v1/admin/audit/logs", SECURITY_PATH = "/api/v1/admin/security-events/events";
let payload: unknown, status: number, strictRoute: boolean, calls: URL[];
beforeEach(() => {
    invalidateRequestIdentity(); setRequestIdentity({ userId: ACTOR_ID }); setTokenProvider(() => "synthetic-only");
    payload = []; status = 200; strictRoute = true; calls = [];
    globalThis.fetch = (async input => {
        const url = new URL(String(input), "http://localhost"); calls.push(url);
        const actualStatus = strictRoute && ![AUDIT_PATH, SECURITY_PATH].includes(url.pathname) ? 404 : status;
        return new Response(JSON.stringify(actualStatus === 200 ? payload : { detail: "Owned read unavailable" }),
            { status: actualStatus, headers: { "content-type": "application/json" } });
    }) as typeof fetch;
});
afterEach(() => { globalThis.fetch = originalFetch; setTokenProvider(null); invalidateRequestIdentity(); });

test("audit uses the mounted raw-array route and preserves the saved identity/time", async () => {
    payload = [auditRow()];
    const result = await backendApi.admin.auditLogs.list({ pageSize: 20 });
    assert.equal(calls[0]!.pathname, AUDIT_PATH);
    assert.equal(result.items[0]!.id, auditRow().event_id);
    assert.equal(result.items[0]!.timestamp, EVENT_TIME);
    assert.equal(result.items[0]!.actor?.id, ACTOR_ID);
    assert.equal(result.items[0]!.actionType, auditRow().action);
});

test("audit sends real grouped filters and inclusive microsecond UTC endpoints", async () => {
    const input = { pageSize: 20, eventGroup: "role_change", userQuery: "name%_@example.test",
        tenantId: TENANT_ID, partnerId: PARTNER_ID, from: "2026-10-21T00:00:00Z", to: "2026-10-21T23:59:59.999999Z" };
    await backendApi.admin.auditLogs.list(input);
    const query = calls[0]!.searchParams;
    assert.equal(query.get("limit"), "21"); assert.equal(query.has("offset"), false);
    assert.equal(query.get("event_group"), "role_change"); assert.equal(query.get("user_query"), input.userQuery);
    assert.equal(query.get("start_date"), input.from); assert.equal(query.get("end_date"), input.to);
    assert.equal(query.get("tenant_id"), TENANT_ID); assert.equal(query.get("partner_id"), PARTNER_ID);
    for (const fictional of ["page", "page_size", "from", "to", "user"]) assert.equal(query.has(fictional), false);
});

for (const count of [0, 20, 21]) test(`audit lookahead ${count} rows has no fabricated total`, async () => {
    payload = Array.from({ length: count }, (_, index) => auditRow(index + 1));
    const result = await backendApi.admin.auditLogs.list({ pageSize: 20 });
    assert.equal(result.items.length, Math.min(count, 20));
    assert.equal(Reflect.get(result, "hasNext"), count > 20);
    assert.equal(Object.hasOwn(result, "total"), false);
});

test("canonical security retains all five severities/statuses and the affected subject", async () => {
    payload = SEVERITIES.map((severity, index) => securityRow(index + 1, { severity, status: SECURITY_STATUSES[index] }));
    const result = await backendApi.admin.securityEvents.list({ pageSize: 20 });
    assert.equal(calls[0]!.pathname, SECURITY_PATH);
    assert.deepEqual(result.items.map(item => item.severity), SEVERITIES);
    assert.deepEqual(result.items.map(item => Reflect.get(item, "status")), SECURITY_STATUSES);
    assert.equal(Reflect.get(result.items[0]!, "subjectId"), SUBJECT_ID);
    assert.equal(Reflect.get(result.items[0]!, "title"), securityRow().title);
    assert.equal(Reflect.get(result.items[0]!, "actor"), undefined, "An affected user must not be invented as the actor");
});

test("security group and exact event filters remain distinct", async () => {
    const input = { pageSize: 20, eventGroup: "failed_login", eventType: "failed_login_wrong_password",
        severity: "CRITICAL" as const, status: "investigating" as const, userQuery: SUBJECT_ID,
        from: "2026-10-21T00:00:00Z", to: "2026-10-21T23:59:59.999999Z" };
    await backendApi.admin.securityEvents.list(input);
    const query = calls[0]!.searchParams;
    assert.equal(query.get("event_group"), "failed_login"); assert.equal(query.get("event_type"), input.eventType);
    assert.equal(query.get("severity"), "CRITICAL"); assert.equal(query.get("status"), "investigating");
    assert.equal(query.get("user_query"), SUBJECT_ID); assert.equal(query.has("offset"), false);
    assert.equal(query.get("start_date"), input.from); assert.equal(query.get("end_date"), input.to);
});

test("an obsolete fictional audit envelope is rejected instead of being treated as server history", async () => {
    strictRoute = false; // Isolate decoder behavior; route coverage is separate above.
    payload = { items: [{ id: "evt-fictional", timestamp: EVENT_TIME, actionType: "role_change" }], total: 1 };
    await assert.rejects(backendApi.admin.auditLogs.list({ pageSize: 20 }));
});

test("saved audit identity/time are required and no epoch date is fabricated", () => {
    assert.equal(AuditLogEventSchema.safeParse(auditRow()).success, true);
    for (const extra of [{ event_id: "not-a-uuid" }, { event_time: undefined }, { event_time: "not-a-date" }, { event_time: "2026-10-21T12:00:00" }]) {
        assert.equal(AuditLogEventSchema.safeParse(auditRow(1, extra)).success, false);
    }
});

test("security rejects malformed severity/status and does not collapse them to a low alert", () => {
    assert.equal(SecurityEventSchema.safeParse(securityRow()).success, true);
    for (const extra of [{ severity: "URGENT" }, { status: "done" }, { user_id: "not-a-uuid" }, { created_at: undefined }]) {
        assert.equal(SecurityEventSchema.safeParse(securityRow(1, extra)).success, false);
    }
});

for (const httpStatus of [403, 503]) test(`canonical HTTP ${httpStatus} stays an error, never an empty history`, async () => {
    strictRoute = false; status = httpStatus;
    await assert.rejects(backendApi.admin.auditLogs.list({}), error => Reflect.get(error as object, "status") === httpStatus);
    await assert.rejects(backendApi.admin.securityEvents.list({}), error => Reflect.get(error as object, "status") === httpStatus);
});

for (const kind of ["auditLogs", "securityEvents"] as const) test(`seek ${kind} sends exact microsecond boundary without an offset`, async () => {
    strictRoute = false;
    const input = { pageSize: 20, before: { time: EVENT_TIME, id: auditRow().event_id } };
    // Inspect transport independently of the known old decoder defect.
    await backendApi.admin[kind].list(input).catch(() => undefined);
    assert.equal(calls.length, 1);
    assert.equal(calls[0]!.searchParams.get("before_time"), EVENT_TIME);
    assert.equal(calls[0]!.searchParams.get("before_id"), input.before.id);
    assert.equal(calls[0]!.searchParams.has("offset"), false);
});

test("seek boundary is taken from last visible raw row, not the lookahead or a millisecond conversion", async () => {
    payload = Array.from({ length: 21 }, (_, index) => auditRow(index + 1));
    const result = await backendApi.admin.auditLogs.list({ pageSize: 20 });
    assert.deepEqual(Reflect.get(result, "nextBoundary"), { time: EVENT_TIME, id: auditRow(20).event_id });
});

for (const before of [{ time: EVENT_TIME }, { id: auditRow().event_id }, { time: "2026-10-21T12:00:00", id: auditRow().event_id },
    { time: EVENT_TIME, id: "bad-uuid" }]) test(`seek malformed pair ${JSON.stringify(before)} is refused before transport`, async () => {
    await assert.rejects(backendApi.admin.auditLogs.list({ pageSize: 20, before } as Parameters<typeof backendApi.admin.auditLogs.list>[0]));
    assert.equal(calls.length, 0);
});
