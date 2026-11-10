import { z } from "zod";
import { sharedHttpClient } from "@/lib/api";

// QA F1-15 (QA-UI-151): team management (/rbac/tenant-users) had no screen.
// Reading the active members is open to the tenant; changing a role,
// removing someone and inviting by email (D4-2) are tenant-admin actions the
// server audits. Owner decisions, 11 Nov 2026: an email that already has an
// account is refused, an invite lasts 7 days, any role up to Admin.

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

const inviteSchema = z.object({
    id: z.string(),
    email: z.string(),
    role: z.string(),
    created_at: z.string(),
    expires_at: z.string(),
});
export type TeamInvite = z.infer<typeof inviteSchema>;
const sentInviteSchema = inviteSchema.extend({ email_sent: z.boolean() });
export type SentInvite = z.infer<typeof sentInviteSchema>;

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
    async listInvites(): Promise<TeamInvite[]> {
        const data = await sharedHttpClient().request({ path: "/rbac/tenant-invites", method: "GET" });
        return z.array(inviteSchema).parse(data);
    },
    /** Inviting the same email again sends a new link; the old one stops working. */
    async invite(email: string, role: AssignableRole): Promise<SentInvite> {
        const data = await sharedHttpClient().request({ path: "/rbac/tenant-invites", method: "POST", body: { email, role_name: role } });
        return sentInviteSchema.parse(data);
    },
    async withdrawInvite(inviteId: string): Promise<void> {
        await sharedHttpClient().request({ path: `/rbac/tenant-invites/${encodeURIComponent(inviteId)}`, method: "DELETE" });
    },
};

const previewSchema = z.object({ email: z.string(), workspace_name: z.string(), role: z.string(), expires_at: z.string() });
export type InvitePreview = z.infer<typeof previewSchema>;
const acceptedSchema = z.object({ email: z.string(), message: z.string() });

/** The invited person's side: the token comes from the email link's fragment and travels only in request bodies. */
export const inviteApi = {
    async preview(token: string): Promise<InvitePreview> {
        const data = await sharedHttpClient().request({ path: "/auth/invites/preview", method: "POST", body: { token } });
        return previewSchema.parse(data);
    },
    async accept(input: { token: string; name: string; password: string; confirmPassword: string }) {
        const data = await sharedHttpClient().request({
            path: "/auth/invites/accept",
            method: "POST",
            body: { token: input.token, name: input.name, password: input.password, confirm_password: input.confirmPassword },
        });
        return acceptedSchema.parse(data);
    },
};
