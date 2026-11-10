import { z } from "zod";
import { sharedHttpClient } from "@/lib/api";

// QA F1-15 (QA-UI-151): team management (/rbac/tenant-users) had no screen.
// Reading the active members is open to the tenant; changing a role and
// removing someone are tenant-admin actions the server audits. Inviting
// someone new is not offered yet (D4-2 awaits the owner's decisions).

const memberSchema = z.object({
    id: z.string(),
    email: z.string(),
    name: z.string().nullable().optional(),
    role: z.string(),
    is_primary: z.boolean(),
    status: z.string(),
    joined_at: z.string().nullable().optional(),
});
export type TeamMember = z.infer<typeof memberSchema>;

/** Roles a tenant admin may give inside a workspace (the server's ceiling still applies). */
export const ASSIGNABLE_ROLES = ["tenant_admin", "campaign_manager", "user", "agent", "billing_user", "readonly"] as const;
export type AssignableRole = (typeof ASSIGNABLE_ROLES)[number];

export const ROLE_LABELS: Record<string, string> = {
    platform_admin: "Platform admin",
    partner_admin: "Partner admin",
    tenant_admin: "Admin",
    campaign_manager: "Campaign manager",
    user: "Standard user",
    agent: "Agent",
    billing_user: "Billing",
    readonly: "Read only",
};

export function roleName(role: string) {
    return ROLE_LABELS[role] ?? role;
}

export const teamApi = {
    async list(): Promise<TeamMember[]> {
        const data = await sharedHttpClient().request({ path: "/rbac/tenant-users", method: "GET" });
        return z.array(memberSchema).parse(data);
    },
    async changeRole(memberId: string, role: AssignableRole): Promise<void> {
        await sharedHttpClient().request({ path: `/rbac/tenant-users/${encodeURIComponent(memberId)}`, method: "PATCH", body: { role_name: role } });
    },
    async remove(memberId: string): Promise<void> {
        await sharedHttpClient().request({ path: `/rbac/tenant-users/${encodeURIComponent(memberId)}`, method: "DELETE" });
    },
};
