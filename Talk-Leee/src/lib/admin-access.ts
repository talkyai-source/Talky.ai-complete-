export type FrontendRole = "platform_admin" | "partner_admin" | "tenant_admin" | "user" | "readonly" | "admin" | "white_label_admin" | string;

export type ScopedUserLike = {
    role?: string;
    partner_id?: string | null;
    tenant_id?: string | null;
};

export type AdminUiCapabilities = {
    canViewAuditLogs: boolean;
    canViewSecurityEvents: boolean;
    canManagePartnerSuspensions: boolean;
    canManageTenantSuspensions: boolean;
    allowedPartnerId: string | null;
    allowedTenantId: string | null;
};

export function isPlatformAdminRole(role: string | null | undefined) {
    return role === "platform_admin" || role === "admin";
}

export function isPartnerAdminRole(role: string | null | undefined) {
    return role === "partner_admin";
}

export function isTenantScopedRole(role: string | null | undefined) {
    return role === "tenant_admin" || role === "user" || role === "readonly";
}

export function getAdminUiCapabilities(user: ScopedUserLike | null | undefined): AdminUiCapabilities {
    const role = user?.role ?? "";
    const allowedPartnerId = user?.partner_id?.trim() ? user.partner_id.trim() : null;
    const allowedTenantId = user?.tenant_id?.trim() ? user.tenant_id.trim() : null;
    const platformAdmin = isPlatformAdminRole(role);
    const partnerAdmin = isPartnerAdminRole(role);

    return {
        canViewAuditLogs: platformAdmin || partnerAdmin,
        canViewSecurityEvents: platformAdmin || partnerAdmin,
        canManagePartnerSuspensions: platformAdmin,
        canManageTenantSuspensions: platformAdmin || partnerAdmin,
        allowedPartnerId: platformAdmin ? null : allowedPartnerId,
        allowedTenantId: platformAdmin || partnerAdmin ? null : allowedTenantId,
    };
}

export function roleLabel(role: string | null | undefined) {
    if (isPlatformAdminRole(role)) return "Platform Admin";
    if (role === "partner_admin") return "Partner Admin";
    if (role === "tenant_admin") return "Tenant Admin";
    if (role === "readonly") return "Read Only";
    if (role === "white_label_admin") return "White-Label Admin";
    if (role === "user") return "User";
    return "User";
}
