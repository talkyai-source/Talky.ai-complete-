export interface RecordingCapabilities {
    canRead: boolean;
    canDownload: boolean;
    canDelete: boolean;
    source: "effective_permissions" | "unavailable";
}

/**
 * Translate the server's effective permission set into recording capabilities.
 *
 * These names deliberately mirror the backend Permission enum exactly.  Do not
 * infer media access from a display role or from broader call permissions: a
 * user may be allowed to inspect call metadata without being allowed to export
 * or permanently erase the audio.
 */
export function getRecordingCapabilities(
    effectivePermissions?: string[],
): RecordingCapabilities {
    if (!effectivePermissions) {
        return {
            canRead: false,
            canDownload: false,
            canDelete: false,
            source: "unavailable",
        };
    }

    const permissions = new Set(
        effectivePermissions.map((permission) => permission.trim().toLowerCase()),
    );
    const platformAdmin = permissions.has("platform:admin");

    return {
        canRead: platformAdmin || permissions.has("recordings:read"),
        canDownload: platformAdmin || permissions.has("recordings:download"),
        canDelete: platformAdmin || permissions.has("recordings:delete"),
        source: "effective_permissions",
    };
}
