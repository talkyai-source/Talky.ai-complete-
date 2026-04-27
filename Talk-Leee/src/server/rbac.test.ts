import { test } from "node:test";
import assert from "node:assert/strict";
import { canAccessPartner, canAccessTenant, hasPermission, type AuthzContext } from "@/server/rbac";

function ctx(input: Partial<AuthzContext> & { userId: string }): AuthzContext {
    return {
        userId: input.userId,
        platformRole: input.platformRole ?? null,
        partnerRoles: input.partnerRoles ?? [],
        tenantRoles: input.tenantRoles ?? [],
        permissions: input.permissions ?? new Set<string>(),
    };
}

test("platform_admin can access any partner and tenant", () => {
    const c = ctx({ userId: "u1", platformRole: "platform_admin" });
    assert.equal(canAccessPartner({ ctx: c, partnerId: "acme" }), true);
    assert.equal(canAccessTenant({ ctx: c, partnerId: "acme", tenantId: "t_1" }), true);
});

test("partner_admin is restricted to their partner", () => {
    const c = ctx({ userId: "u1", partnerRoles: [{ partnerId: "acme", role: "partner_admin" }] });
    assert.equal(canAccessPartner({ ctx: c, partnerId: "acme" }), true);
    assert.equal(canAccessPartner({ ctx: c, partnerId: "zen" }), false);
    assert.equal(canAccessTenant({ ctx: c, partnerId: "acme", tenantId: "t_1" }), true);
    assert.equal(canAccessTenant({ ctx: c, partnerId: "zen", tenantId: "t_1" }), false);
});

test("tenant users cannot access other tenants or partners", () => {
    const c = ctx({
        userId: "u1",
        tenantRoles: [{ tenantId: "t_a", partnerId: "acme", role: "tenant_admin", tenantStatus: "active" }],
    });
    assert.equal(canAccessTenant({ ctx: c, partnerId: "acme", tenantId: "t_a" }), true);
    assert.equal(canAccessTenant({ ctx: c, partnerId: "acme", tenantId: "t_b" }), false);
    assert.equal(canAccessTenant({ ctx: c, partnerId: "zen", tenantId: "t_a" }), false);
});

test("permission checks always allow platform_admin", () => {
    const c = ctx({ userId: "u1", platformRole: "platform_admin", permissions: new Set() });
    assert.equal(hasPermission({ ctx: c, permission: "manage_users" }), true);
});

