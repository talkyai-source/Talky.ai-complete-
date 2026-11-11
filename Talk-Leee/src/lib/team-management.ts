"use client";

import { useQuery } from "@tanstack/react-query";
import { backendApi, type TenantMemberStatus } from "@/lib/backend-api";
import { isPlatformAdminRole } from "@/lib/admin-access";
import type { RbacRole, TenantMember } from "@/lib/models";

// Plan 3.5, Team page. The backend cannot invite or create a person (no
// invite endpoint; POST /rbac/tenant-users needs an existing user_id), so the
// Invite control is disabled with exactly this sentence and sends nothing.
export const TEAM_INVITE_UNAVAILABLE_TEXT = "Inviting teammates isn't available yet.";

// Platform-only roles (backend rbac.py: platform_admin 100, partner_admin 80).
// GET /rbac/roles lists them too; they never appear in the role options.
const PLATFORM_ONLY_ROLES = new Set(["platform_admin", "partner_admin"]);

export function capitalizeBackendName(value: string): string {
    const spaced = value.replace(/_/g, " ");
    return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

// GET /rbac/roles carries no display name, so every role shows its own name:
// each underscore as a space and a capital first letter ("Campaign manager").
// The sidebar's roleLabel is not used here: it answers "User" for
// campaign_manager, agent and billing_user.
export function teamRoleLabel(role: string): string {
    return capitalizeBackendName(role);
}

export function teamStatusLabel(status: string): string {
    return capitalizeBackendName(status);
}

/**
 * Who may change, deactivate or remove a member: the backend's rule, not a
 * frontend one. PATCH and DELETE /rbac/tenant-users/{id} depend on
 * require_role(UserRole.TENANT_ADMIN) (tenant_users.py:252 and :398), which
 * passes platform_admin outright and otherwise needs level >= tenant_admin
 * (rbac.py:913-928), and _current_target_admin (tenant_users.py:45-51) then
 * requires tenant_admin or partner_admin membership in the tenant.
 */
export function canManageTeam(role: string | null | undefined): boolean {
    if (!role) return false;
    if (isPlatformAdminRole(role)) return true;
    return role === "tenant_admin" || role === "partner_admin";
}

export function ownRoleLevel(roles: RbacRole[] | undefined, role: string | null | undefined): number | null {
    if (!role) return null;
    if (isPlatformAdminRole(role)) return Number.POSITIVE_INFINITY;
    const match = roles?.find((r) => r.name === role);
    return match ? match.level : null;
}

/**
 * The role options offered on a row: tenant-scoped roles at or below the
 * signed-in user's own level (the backend refuses "Cannot grant a role above
 * your own tier", tenant_users.py:305-311). platform_admin and partner_admin
 * never appear.
 */
export function selectableTeamRoles(roles: RbacRole[] | undefined, ownRole: string | null | undefined): RbacRole[] {
    const level = ownRoleLevel(roles, ownRole);
    if (level === null || !roles) return [];
    return roles.filter((r) => r.tenant_scoped && !PLATFORM_ONLY_ROLES.has(r.name) && r.level <= level);
}

export function teamMemberDisplayName(member: Pick<TenantMember, "name" | "email">): string {
    const name = member.name?.trim();
    return name ? name : member.email;
}

export const teamQueryKeys = {
    members: (allStatuses: boolean) => ["team", "members", allStatuses ? "all" : "active"] as const,
    roles: () => ["team", "roles"] as const,
};

// Statuses a manager sees. "removed" members are gone from the team, so the
// page does not list them. Non-managers get active members only from the
// backend whatever they ask for (tenant_users.py:85-86), so one call is made.
export const MANAGED_MEMBER_STATUSES: readonly TenantMemberStatus[] = ["active", "pending", "suspended"];

export async function fetchTeamMembers(allStatuses: boolean, signal?: AbortSignal): Promise<TenantMember[]> {
    if (!allStatuses) return backendApi.team.members("active", signal);
    const lists = await Promise.all(MANAGED_MEMBER_STATUSES.map((status) => backendApi.team.members(status, signal)));
    return lists.flat();
}

export function useTeamMembers(allStatuses: boolean, options?: { enabled?: boolean }) {
    return useQuery({
        queryKey: teamQueryKeys.members(allStatuses),
        queryFn: ({ signal }) => fetchTeamMembers(allStatuses, signal),
        refetchOnWindowFocus: false,
        enabled: options?.enabled ?? true,
    });
}

export function useTeamRoles(options?: { enabled?: boolean }) {
    return useQuery({
        queryKey: teamQueryKeys.roles(),
        queryFn: ({ signal }) => backendApi.team.roles(signal),
        refetchOnWindowFocus: false,
        staleTime: 5 * 60_000,
        enabled: options?.enabled ?? true,
    });
}
