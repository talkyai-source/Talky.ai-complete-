export interface ReviewCapabilities {
    /** May read reviews on a call. Backend: GET /calls/{id}/review → calls:read. */
    canRead: boolean;
    /** May leave or edit a review. Backend: PUT /calls/{id}/review → calls:create. */
    canWrite: boolean;
    source: "effective_permissions" | "unavailable";
}

/**
 * Translate the server's effective permission set into review capabilities.
 *
 * READING AND WRITING A REVIEW ARE DIFFERENT PERMISSIONS
 * -----------------------------------------------------
 * `backend/app/api/v1/endpoints/conversation_reviews.py` gates the GET on
 * `Permission.CALLS_READ` and the PUT on `Permission.CALLS_CREATE`. The readonly
 * role holds the first and not the second (see ROLE_DEFAULT_PERMISSIONS in
 * `backend/app/core/security/rbac.py`), so "can open this call" says nothing
 * about "can review it", and a UI that infers one from the other will show a
 * form that only fails on submit.
 *
 * These names mirror the backend Permission enum exactly. Do not substitute a
 * display role: the effective permission set is the only authority, and a
 * missing or failed lookup yields no capabilities so the form fails closed
 * rather than being inferred.
 */
export function getReviewCapabilities(effectivePermissions?: string[]): ReviewCapabilities {
    if (!effectivePermissions) {
        return { canRead: false, canWrite: false, source: "unavailable" };
    }

    const permissions = new Set(
        effectivePermissions.map((permission) => permission.trim().toLowerCase()),
    );
    const platformAdmin = permissions.has("platform:admin");

    return {
        canRead: platformAdmin || permissions.has("calls:read"),
        canWrite: platformAdmin || permissions.has("calls:create"),
        source: "effective_permissions",
    };
}

/**
 * Whether re-sending an identical request could plausibly succeed.
 *
 * A "Try again" button that re-fires a request the server has already refused on
 * authorization or validation grounds cannot do anything except produce the same
 * refusal, so offering it is worse than offering nothing: it presents a way
 * forward that does not exist. Timeouts, rate limits, server faults and network
 * failures (no status at all) are the cases where the same request really can
 * succeed on a second attempt.
 */
export function isRetryableSubmitStatus(status?: number): boolean {
    if (status === undefined) return true; // network / transport failure
    if (status === 408 || status === 429) return true;
    return status >= 500;
}
