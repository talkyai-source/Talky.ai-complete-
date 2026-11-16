"use client";

import { useCallback, useMemo, useState } from "react";

import {
    buildContactPermission,
    contactPermissionServerError,
    emptyContactPermissionDraft,
    type ContactPermission,
    type ContactPermissionDraft,
    type ContactPermissionServerError,
} from "@/lib/contact-permission";

/**
 * State for one "Permission to call these contacts" block: the draft, whether
 * a submit has been attempted, and a backend refusal to show beside it.
 * Spread `fieldProps` onto <ContactPermissionFields>.
 */
export function useContactPermissionForm() {
    const [draft, setDraftState] = useState<ContactPermissionDraft>(emptyContactPermissionDraft);
    const [attempted, setAttempted] = useState(false);
    const [serverError, setServerError] = useState<ContactPermissionServerError | null>(null);

    const setDraft = useCallback((next: ContactPermissionDraft) => {
        setDraftState(next);
        setServerError(null);
    }, []);

    const permission = useMemo(() => buildContactPermission(draft), [draft]);

    /** The confirmation to send, or null after making every error visible. */
    const require = useCallback((): ContactPermission | null => {
        const built = buildContactPermission(draft);
        if (!built) setAttempted(true);
        return built;
    }, [draft]);

    /** Shows a backend `contact_permission_*` refusal beside the block. False for any other error. */
    const showServerError = useCallback((err: unknown): boolean => {
        const parsed = contactPermissionServerError(err);
        if (!parsed) return false;
        setServerError(parsed);
        setAttempted(true);
        return true;
    }, []);

    /** Start over (a new import never inherits the last confirmation). */
    const reset = useCallback(() => {
        setDraftState(emptyContactPermissionDraft());
        setAttempted(false);
        setServerError(null);
    }, []);

    return {
        draft,
        permission,
        require,
        showServerError,
        reset,
        fieldProps: { value: draft, onChange: setDraft, showErrors: attempted, serverError },
    };
}
