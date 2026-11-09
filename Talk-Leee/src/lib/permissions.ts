import { useEffectivePermissions } from "@/lib/queries/inbound-queries";

/**
 * QA F1-21: screens follow the server's effective permission list
 * (GET /rbac/users/me/permissions), decided the way check_permission in
 * backend/app/core/security/rbac.py decides: the permission itself, its
 * resource's "<resource>:admin", or "platform:admin". The server still
 * enforces every request; this only stops offering what it would refuse.
 */
export function hasPermission(permissions: readonly string[], required: string): boolean {
    const granted = new Set(permissions.map((permission) => permission.trim().toLowerCase()));
    return granted.has(required) || granted.has(`${required.split(":")[0]}:admin`) || granted.has("platform:admin");
}

/**
 * False only once the list is known and lacks the permission. While it loads,
 * or when it cannot be read, things stay offered and the server's answer stands.
 */
export function permits(permissions: readonly string[] | undefined, required: string | undefined): boolean {
    return !required || permissions === undefined || hasPermission(permissions, required);
}

/** What a role may do to campaigns, as the campaign endpoints gate it. */
export type CampaignActions = { create: boolean; update: boolean; delete: boolean };

export function campaignActions(permissions: readonly string[] | undefined): CampaignActions {
    return {
        create: permits(permissions, "campaigns:create"),
        update: permits(permissions, "campaigns:update"),
        delete: permits(permissions, "campaigns:delete"),
    };
}

/** The effective permission list once the server has answered, else undefined. */
export function useKnownPermissions(): readonly string[] | undefined {
    const query = useEffectivePermissions();
    return query.isSuccess ? query.data.permissions : undefined;
}
