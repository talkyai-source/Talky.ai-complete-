import assert from "node:assert/strict";
import { test } from "node:test";
import { createInMemoryVoiceSecurityBackend, createVoiceSecurityService, startGuardedVoiceCallSessionWithService } from "@/server/voice-security";

function baseAuthz(input: { userId: string; tenantId: string; partnerId: string; canStartCall?: boolean }) {
    return {
        userId: input.userId,
        platformRole: null,
        partnerRoles: [],
        tenantRoles: [{ tenantId: input.tenantId, partnerId: input.partnerId, role: "tenant_admin" as const, tenantStatus: "active" as const }],
        permissions: new Set(input.canStartCall === false ? [] : ["start_call"]),
    };
}

test("call_guard allows valid calls and lifecycle updates counters", async () => {
    const backend = createInMemoryVoiceSecurityBackend();
    backend.seedPartner({
        partnerId: "acme",
        allowTransfer: true,
        limits: { maxConcurrentCalls: 5, callsPerMinute: 50, allowedFeatures: ["voice", "transfer"] },
    });
    backend.seedTenant({
        tenantId: "tenant-a",
        partnerId: "acme",
        limits: { maxConcurrentCalls: 2, callsPerMinute: 20, allowedFeatures: ["voice", "transfer"] },
    });
    backend.setAuthz({ userId: "user-1", ctx: baseAuthz({ userId: "user-1", tenantId: "tenant-a", partnerId: "acme" }) });
    const service = createVoiceSecurityService(backend);

    const allowed = await service.call_guard({
        tenantId: "tenant-a",
        partnerId: "acme",
        userId: "user-1",
        ipAddress: "1.1.1.1",
        requestedFeatures: ["voice"],
    });

    assert.equal(allowed.outcome, "ALLOW");
    assert.ok(allowed.reservationId);
    assert.equal(allowed.activeCalls.tenant, 1);
    assert.equal(allowed.activeCalls.partner, 1);
    assert.ok(allowed.usageAccountId);
    assert.ok(allowed.billingAccountId);

    const started = await service.confirmGuardedCallStart({
        reservationId: allowed.reservationId,
        callId: "call-1",
        providerCallId: "provider-1",
    });
    assert.equal(started.ok, true);
    assert.equal(started.status, "active");

    const ended = await service.endGuardedCall({ reservationId: allowed.reservationId });
    assert.equal(ended.ok, true);
    assert.equal(ended.status, "ended");
    assert.deepEqual(backend.getActiveCalls({ tenantId: "tenant-a", partnerId: "acme" }), { tenant: 0, partner: 0 });
});

test("startGuardedVoiceCallSessionWithService makes call_guard non-bypassable for start flow", async () => {
    const backend = createInMemoryVoiceSecurityBackend();
    backend.seedPartner({
        partnerId: "acme",
        allowTransfer: true,
        limits: { maxConcurrentCalls: 5, callsPerMinute: 50, allowedFeatures: ["voice", "transfer"] },
    });
    backend.seedTenant({
        tenantId: "tenant-start",
        partnerId: "acme",
        limits: { maxConcurrentCalls: 2, callsPerMinute: 20, allowedFeatures: ["voice", "transfer"] },
    });
    backend.setAuthz({ userId: "user-start", ctx: baseAuthz({ userId: "user-start", tenantId: "tenant-start", partnerId: "acme" }) });
    const service = createVoiceSecurityService(backend);

    const started = await startGuardedVoiceCallSessionWithService(service, {
        tenantId: "tenant-start",
        partnerId: "acme",
        userId: "user-start",
        ipAddress: "1.1.1.2",
        requestedFeatures: ["voice"],
    });

    assert.equal(started.outcome, "ALLOW");
    assert.equal(started.status, "active");
    assert.ok(started.callId.length > 0);
    assert.ok(started.reservationId);
    assert.deepEqual(backend.getActiveCalls({ tenantId: "tenant-start", partnerId: "acme" }), { tenant: 1, partner: 1 });
});

test("call_guard enforces concurrency limits and supports overage reservations", async () => {
    const backend = createInMemoryVoiceSecurityBackend();
    backend.seedPartner({
        partnerId: "acme",
        limits: { maxConcurrentCalls: 5, callsPerMinute: 50, allowedFeatures: ["voice"] },
    });
    backend.seedTenant({
        tenantId: "tenant-b",
        partnerId: "acme",
        limits: { maxConcurrentCalls: 1, callsPerMinute: 50, allowedFeatures: ["voice"] },
    });
    backend.setAuthz({ userId: "user-2", ctx: baseAuthz({ userId: "user-2", tenantId: "tenant-b", partnerId: "acme" }) });
    const service = createVoiceSecurityService(backend);

    const first = await service.call_guard({
        tenantId: "tenant-b",
        userId: "user-2",
        ipAddress: "2.2.2.2",
    });
    assert.equal(first.outcome, "ALLOW");

    const blocked = await service.call_guard({
        tenantId: "tenant-b",
        userId: "user-2",
        ipAddress: "2.2.2.3",
    });
    assert.equal(blocked.outcome, "REJECT");
    assert.equal(blocked.code, "tenant_concurrency_exceeded");

    const overage = await service.call_guard({
        tenantId: "tenant-b",
        userId: "user-2",
        ipAddress: "2.2.2.4",
        allowOverage: true,
    });
    assert.equal(overage.outcome, "ALLOW");
    assert.equal(overage.overage.tenant, true);
    assert.deepEqual(backend.getActiveCalls({ tenantId: "tenant-b", partnerId: "acme" }), { tenant: 2, partner: 2 });
});

test("call_guard enforces dedicated call rate limits per tenant", async () => {
    const backend = createInMemoryVoiceSecurityBackend();
    backend.seedPartner({
        partnerId: "acme",
        limits: { maxConcurrentCalls: 10, callsPerMinute: 50, allowedFeatures: ["voice"] },
    });
    backend.seedTenant({
        tenantId: "tenant-rate",
        partnerId: "acme",
        limits: { maxConcurrentCalls: 10, callsPerMinute: 1, allowedFeatures: ["voice"] },
    });
    backend.setAuthz({ userId: "user-rate", ctx: baseAuthz({ userId: "user-rate", tenantId: "tenant-rate", partnerId: "acme" }) });
    const service = createVoiceSecurityService(backend);
    const now = new Date("2026-01-01T00:05:00.000Z");

    const first = await service.call_guard({
        tenantId: "tenant-rate",
        userId: "user-rate",
        ipAddress: "5.5.5.5",
        now,
    });
    assert.equal(first.outcome, "ALLOW");

    const second = await service.call_guard({
        tenantId: "tenant-rate",
        userId: "user-rate",
        ipAddress: "5.5.5.6",
        now: new Date(now.getTime() + 1_000),
    });
    assert.equal(second.outcome, "REJECT");
    assert.equal(second.code, "tenant_rate_limited");
    assert.ok((second.retryAfterSeconds ?? 0) > 0);
});

test("call_guard rejects disallowed features and then blocks repeated failed attempts", async () => {
    const backend = createInMemoryVoiceSecurityBackend();
    backend.seedPartner({
        partnerId: "acme",
        limits: { maxConcurrentCalls: 5, callsPerMinute: 100, allowedFeatures: ["voice"] },
    });
    backend.seedTenant({
        tenantId: "tenant-c",
        partnerId: "acme",
        limits: { maxConcurrentCalls: 5, callsPerMinute: 100, allowedFeatures: ["voice"] },
    });
    backend.setAuthz({ userId: "user-3", ctx: baseAuthz({ userId: "user-3", tenantId: "tenant-c", partnerId: "acme" }) });
    const service = createVoiceSecurityService(backend);
    const baseTime = new Date("2026-01-01T00:00:00.000Z");

    for (let i = 0; i < 7; i += 1) {
        const rejected = await service.call_guard({
            tenantId: "tenant-c",
            userId: "user-3",
            ipAddress: "3.3.3.3",
            requestedFeatures: ["premium"],
            now: new Date(baseTime.getTime() + i * 1000),
        });
        assert.equal(rejected.outcome, "REJECT");
        assert.equal(rejected.code, "feature_not_allowed");
    }

    const blocked = await service.call_guard({
        tenantId: "tenant-c",
        userId: "user-3",
        ipAddress: "3.3.3.3",
        now: new Date(baseTime.getTime() + 8_000),
    });
    assert.equal(blocked.outcome, "REJECT");
    assert.equal(blocked.code, "temporary_block");
    assert.ok(blocked.blockExpiresAt);
});

test("call_guard rejects callers outside the tenant scope", async () => {
    const backend = createInMemoryVoiceSecurityBackend();
    backend.seedPartner({
        partnerId: "acme",
        limits: { maxConcurrentCalls: 5, callsPerMinute: 50, allowedFeatures: ["voice"] },
    });
    backend.seedTenant({
        tenantId: "tenant-d",
        partnerId: "acme",
        limits: { maxConcurrentCalls: 2, callsPerMinute: 20, allowedFeatures: ["voice"] },
    });
    backend.setAuthz({ userId: "user-4", ctx: baseAuthz({ userId: "user-4", tenantId: "other-tenant", partnerId: "acme" }) });
    const service = createVoiceSecurityService(backend);

    const rejected = await service.call_guard({
        tenantId: "tenant-d",
        userId: "user-4",
        ipAddress: "4.4.4.4",
    });

    assert.equal(rejected.outcome, "REJECT");
    assert.equal(rejected.code, "forbidden");
});
