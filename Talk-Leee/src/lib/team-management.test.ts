import assert from "node:assert/strict";
import { test } from "node:test";
import type { RbacRole } from "@/lib/models";
import {
    TEAM_INVITE_UNAVAILABLE_TEXT,
    canManageTeam,
    ownRoleLevel,
    selectableTeamRoles,
    teamMemberDisplayName,
    teamRoleLabel,
    teamStatusLabel,
} from "@/lib/team-management";

// Backend roles and levels (rbac.py:87-108); tenant_scoped as GET /rbac/roles reports it.
const role = (name: string, level: number, tenant_scoped: boolean): RbacRole => ({
    id: `role-${name}`, name, description: null, level, is_system_role: true, tenant_scoped,
});
const ROLES: RbacRole[] = [
    role("platform_admin", 100, false),
    role("partner_admin", 80, false),
    role("tenant_admin", 60, true),
    role("campaign_manager", 50, true),
    role("user", 40, true),
    role("agent", 35, true),
    role("billing_user", 30, true),
    role("readonly", 20, true),
];

test("the invite sentence is exactly the agreed text", () => {
    assert.equal(TEAM_INVITE_UNAVAILABLE_TEXT, "Inviting teammates isn't available yet.");
});

test("canManageTeam follows require_role(TENANT_ADMIN) plus the tenant/partner admin membership recheck", () => {
    assert.equal(canManageTeam("tenant_admin"), true);
    assert.equal(canManageTeam("partner_admin"), true);
    assert.equal(canManageTeam("platform_admin"), true);
    for (const r of ["campaign_manager", "user", "agent", "billing_user", "readonly", "", null, undefined]) {
        assert.equal(canManageTeam(r), false, `${String(r)} cannot manage`);
    }
});

test("role options: tenant-scoped roles at or below the own level; platform roles never appear", () => {
    const forTenantAdmin = selectableTeamRoles(ROLES, "tenant_admin").map((r) => r.name);
    assert.deepEqual(forTenantAdmin, ["tenant_admin", "campaign_manager", "user", "agent", "billing_user", "readonly"]);
    assert.equal(forTenantAdmin.includes("platform_admin"), false);
    assert.equal(forTenantAdmin.includes("partner_admin"), false);

    // A partner admin (80) still gets no platform role: both are not tenant scoped.
    const forPartnerAdmin = selectableTeamRoles(ROLES, "partner_admin").map((r) => r.name);
    assert.deepEqual(forPartnerAdmin, ["tenant_admin", "campaign_manager", "user", "agent", "billing_user", "readonly"]);

    // Even a platform role flagged tenant_scoped by mistake is kept out.
    const mislabelled = ROLES.map((r) => ({ ...r, tenant_scoped: true }));
    const forPlatformAdmin = selectableTeamRoles(mislabelled, "platform_admin").map((r) => r.name);
    assert.equal(forPlatformAdmin.includes("platform_admin"), false);
    assert.equal(forPlatformAdmin.includes("partner_admin"), false);
    assert.equal(forPlatformAdmin.includes("tenant_admin"), true);

    // A campaign manager (50) could only grant up to its own level.
    assert.deepEqual(selectableTeamRoles(ROLES, "campaign_manager").map((r) => r.name), ["campaign_manager", "user", "agent", "billing_user", "readonly"]);
    // Unknown own role or no roles: nothing to offer.
    assert.deepEqual(selectableTeamRoles(ROLES, "mystery"), []);
    assert.deepEqual(selectableTeamRoles(undefined, "tenant_admin"), []);
});

test("ownRoleLevel reads the level from the roles list; platform admins are above every level", () => {
    assert.equal(ownRoleLevel(ROLES, "tenant_admin"), 60);
    assert.equal(ownRoleLevel(ROLES, "readonly"), 20);
    assert.equal(ownRoleLevel(ROLES, "platform_admin"), Number.POSITIVE_INFINITY);
    assert.equal(ownRoleLevel(ROLES, "nobody"), null);
    assert.equal(ownRoleLevel(ROLES, null), null);
});

test("role labels: every role shows its own name, underscores as spaces, capital first letter", () => {
    assert.equal(teamRoleLabel("tenant_admin"), "Tenant admin");
    assert.equal(teamRoleLabel("user"), "User");
    assert.equal(teamRoleLabel("readonly"), "Readonly");
    assert.equal(teamRoleLabel("campaign_manager"), "Campaign manager");
    assert.equal(teamRoleLabel("billing_user"), "Billing user");
    assert.equal(teamRoleLabel("agent"), "Agent");
});

test("status labels are the backend status with a capital first letter", () => {
    assert.equal(teamStatusLabel("active"), "Active");
    assert.equal(teamStatusLabel("pending"), "Pending");
    assert.equal(teamStatusLabel("suspended"), "Suspended");
});

test("a member without a name is shown by email", () => {
    assert.equal(teamMemberDisplayName({ name: "Alex Example", email: "alex@example.test" }), "Alex Example");
    assert.equal(teamMemberDisplayName({ name: "  ", email: "alex@example.test" }), "alex@example.test");
    assert.equal(teamMemberDisplayName({ name: null, email: "alex@example.test" }), "alex@example.test");
});
