import { sharedHttpClient } from "@/lib/api";
import { getRecordingCapabilities } from "@/lib/media-permissions";

export interface InboundCapabilities {
    canView: boolean;
    canCreate: boolean;
    canEdit: boolean;
    canAssignNumber: boolean;
    canChangeLifecycle: boolean;
    canChangeControls: boolean;
    canPlayMedia: boolean;
    canDownloadMedia: boolean;
    canDeleteMedia: boolean;
    source: "effective_permissions" | "unavailable";
}

export interface EffectivePermissions {
    permissions: string[];
    role?: string;
}

function normalizePermissions(value: unknown): EffectivePermissions {
    const record = value && typeof value === "object" ? value as Record<string, unknown> : {};
    const source = Array.isArray(record.permissions)
        ? record.permissions
        : Array.isArray(record.effective_permissions)
            ? record.effective_permissions
            : [];
    return {
        permissions: source.filter((permission): permission is string => typeof permission === "string"),
        role: typeof record.role === "string" ? record.role : undefined,
    };
}

export async function fetchEffectivePermissions(signal?: AbortSignal): Promise<EffectivePermissions> {
    const response = await sharedHttpClient().request({
        path: "/rbac/users/me/permissions",
        method: "GET",
        signal,
        suppressAuthRedirect: true,
    });
    return normalizePermissions(response);
}

/**
 * The server's effective permission set is the only authority. A missing or
 * failed permission lookup returns no capabilities so routing controls fail
 * closed instead of being inferred from a display role.
 */
export function getInboundCapabilities(
    _role: string | null | undefined,
    effectivePermissions?: string[],
): InboundCapabilities {
    if (effectivePermissions) {
        const permissions = new Set(effectivePermissions.map((permission) => permission.toLowerCase()));
        const platformAdmin = permissions.has("platform:admin");
        const inboundManager = platformAdmin || permissions.has("inbound:manage");
        const canView = inboundManager || permissions.has("inbound:read");
        const canCreate = inboundManager;
        const canEdit = inboundManager;
        const canAssignNumber = inboundManager || permissions.has("inbound:assign");
        const canChangeLifecycle = inboundManager;
        const canChangeControls = platformAdmin || permissions.has("inbound:controls");
        const media = getRecordingCapabilities(effectivePermissions);
        return {
            canView,
            canCreate,
            canEdit,
            canAssignNumber,
            canChangeLifecycle,
            canChangeControls,
            canPlayMedia: media.canRead,
            canDownloadMedia: media.canDownload,
            canDeleteMedia: media.canDelete,
            source: "effective_permissions",
        };
    }

    return {
        canView: false,
        canCreate: false,
        canEdit: false,
        canAssignNumber: false,
        canChangeLifecycle: false,
        canChangeControls: false,
        canPlayMedia: false,
        canDownloadMedia: false,
        canDeleteMedia: false,
        source: "unavailable",
    };
}

export function canManageInboundCampaigns(role: string | null | undefined): boolean {
    return getInboundCapabilities(role).canChangeLifecycle;
}

export function isInboundCampaignActive(status: string): boolean {
    return status.trim().toLowerCase() === "active";
}
