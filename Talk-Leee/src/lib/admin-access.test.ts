import assert from "node:assert/strict";
import { test } from "node:test";
import { getAdminUiCapabilities, isPlatformAdminRole, isPartnerAdminRole, isTenantScopedRole, roleLabel } from "@/lib/admin-access";

test("isPlatformAdminRole recognizes platform_admin and admin roles", () => {
    assert.equal(isPlatformAdminRole("platform_admin"), true);
    assert.equal(isPlatformAdminRole("admin"), true);
    assert.equal(isPlatformAdminRole("partner_admin"), false);
    assert.equal(isPlatformAdminRole("tenant_admin"), false);
    assert.equal(isPlatformAdminRole(null), false);
    assert.equal(isPlatformAdminRole(undefined), false);
});

test("isPartnerAdminRole recognizes partner_admin role", () => {
    assert.equal(isPartnerAdminRole("partner_admin"), true);
    assert.equal(isPartnerAdminRole("platform_admin"), false);
    assert.equal(isPartnerAdminRole("admin"), false);
    assert.equal(isPartnerAdminRole("tenant_admin"), false);
    assert.equal(isPartnerAdminRole(null), false);
    assert.equal(isPartnerAdminRole(undefined), false);
});

test("isTenantScopedRole recognizes tenant-scoped roles", () => {
    assert.equal(isTenantScopedRole("tenant_admin"), true);
    assert.equal(isTenantScopedRole("user"), true);
    assert.equal(isTenantScopedRole("readonly"), true);
    assert.equal(isTenantScopedRole("platform_admin"), false);
    assert.equal(isTenantScopedRole("partner_admin"), false);
    assert.equal(isTenantScopedRole(null), false);
    assert.equal(isTenantScopedRole(undefined), false);
});

test("getAdminUiCapabilities grants full access to platform_admin", () => {
    const capabilities = getAdminUiCapabilities({
        role: "platform_admin",
        partner_id: "partner-123",
        tenant_id: "tenant-456",
    });

    assert.equal(capabilities.canViewAuditLogs, true);
    assert.equal(capabilities.canViewSecurityEvents, true);
    assert.equal(capabilities.canManagePartnerSuspensions, true);
    assert.equal(capabilities.canManageTenantSuspensions, true);
    assert.equal(capabilities.allowedPartnerId, null);
    assert.equal(capabilities.allowedTenantId, null);
});

test("getAdminUiCapabilities grants full access to admin role", () => {
    const capabilities = getAdminUiCapabilities({
        role: "admin",
        partner_id: "partner-789",
        tenant_id: "tenant-012",
    });

    assert.equal(capabilities.canViewAuditLogs, true);
    assert.equal(capabilities.canViewSecurityEvents, true);
    assert.equal(capabilities.canManagePartnerSuspensions, true);
    assert.equal(capabilities.canManageTenantSuspensions, true);
    assert.equal(capabilities.allowedPartnerId, null);
    assert.equal(capabilities.allowedTenantId, null);
});

test("getAdminUiCapabilities grants scoped access to partner_admin", () => {
    const capabilities = getAdminUiCapabilities({
        role: "partner_admin",
        partner_id: "partner-acme",
        tenant_id: null,
    });

    assert.equal(capabilities.canViewAuditLogs, true);
    assert.equal(capabilities.canViewSecurityEvents, true);
    assert.equal(capabilities.canManagePartnerSuspensions, false);
    assert.equal(capabilities.canManageTenantSuspensions, true);
    assert.equal(capabilities.allowedPartnerId, "partner-acme");
    assert.equal(capabilities.allowedTenantId, null);
});

test("getAdminUiCapabilities restricts access for tenant_admin", () => {
    const capabilities = getAdminUiCapabilities({
        role: "tenant_admin",
        partner_id: null,
        tenant_id: "tenant-xyz",
    });

    assert.equal(capabilities.canViewAuditLogs, false);
    assert.equal(capabilities.canViewSecurityEvents, false);
    assert.equal(capabilities.canManagePartnerSuspensions, false);
    assert.equal(capabilities.canManageTenantSuspensions, false);
    assert.equal(capabilities.allowedPartnerId, null);
    assert.equal(capabilities.allowedTenantId, "tenant-xyz");
});

test("getAdminUiCapabilities restricts access for user role", () => {
    const capabilities = getAdminUiCapabilities({
        role: "user",
        partner_id: null,
        tenant_id: "tenant-abc",
    });

    assert.equal(capabilities.canViewAuditLogs, false);
    assert.equal(capabilities.canViewSecurityEvents, false);
    assert.equal(capabilities.canManagePartnerSuspensions, false);
    assert.equal(capabilities.canManageTenantSuspensions, false);
    assert.equal(capabilities.allowedPartnerId, null);
    assert.equal(capabilities.allowedTenantId, "tenant-abc");
});

test("getAdminUiCapabilities handles null user", () => {
    const capabilities = getAdminUiCapabilities(null);

    assert.equal(capabilities.canViewAuditLogs, false);
    assert.equal(capabilities.canViewSecurityEvents, false);
    assert.equal(capabilities.canManagePartnerSuspensions, false);
    assert.equal(capabilities.canManageTenantSuspensions, false);
    assert.equal(capabilities.allowedPartnerId, null);
    assert.equal(capabilities.allowedTenantId, null);
});

test("getAdminUiCapabilities ignores whitespace-only IDs", () => {
    const capabilities = getAdminUiCapabilities({
        role: "partner_admin",
        partner_id: "   ",
        tenant_id: "\t",
    });

    assert.equal(capabilities.allowedPartnerId, null);
    assert.equal(capabilities.allowedTenantId, null);
});

test("roleLabel returns correct labels for all roles", () => {
    assert.equal(roleLabel("platform_admin"), "Platform Admin");
    assert.equal(roleLabel("admin"), "Platform Admin");
    assert.equal(roleLabel("partner_admin"), "Partner Admin");
    assert.equal(roleLabel("tenant_admin"), "Tenant Admin");
    assert.equal(roleLabel("user"), "User");
    assert.equal(roleLabel("readonly"), "Read Only");
    assert.equal(roleLabel("white_label_admin"), "White-Label Admin");
    assert.equal(roleLabel("unknown_role"), "User");
    assert.equal(roleLabel(null), "User");
    assert.equal(roleLabel(undefined), "User");
});
