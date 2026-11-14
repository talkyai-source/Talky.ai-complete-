"use client";

import Link from "next/link";
import { useEffect, useId, useState } from "react";
import { z } from "zod";
import { sharedHttpClient } from "@/lib/api";
import { isApiClientError } from "@/lib/http-client";

/**
 * QA F12-10. When the backend requires acceptance of the current Terms and
 * Privacy Policy (`legal_acceptance_required`), sign-up and invite acceptance
 * must send the versions back. With it off, nothing is shown and nothing extra
 * is sent.
 */

const versionsSchema = z.object({
    required: z.boolean(),
    terms_version: z.string().nullable().optional(),
    privacy_version: z.string().nullable().optional(),
});

export type LegalVersions = { termsVersion: string; privacyVersion: string };

type State = {
    required: boolean;
    versions: LegalVersions | null; // null while required means "not configured"
    accepted: boolean;
    updated: boolean; // the server refused the versions we sent
};

const NOT_REQUIRED: State = { required: false, versions: null, accepted: false, updated: false };

function usable(value: string | null | undefined) {
    return typeof value === "string" && value.trim() !== "" ? value : null;
}

/** Reads the versions out of a 422 `legal_acceptance_required` refusal, or null. */
function versionsFromRefusal(err: unknown): LegalVersions | null {
    if (!isApiClientError(err) || err.status !== 422 || err.code !== "legal_acceptance_required") return null;
    const details = err.details;
    if (!details || typeof details !== "object") return null;
    const record = details as Record<string, unknown>;
    const termsVersion = usable(record.terms_version as string | undefined);
    const privacyVersion = usable(record.privacy_version as string | undefined);
    return termsVersion && privacyVersion ? { termsVersion, privacyVersion } : null;
}

export function useLegalAcceptance() {
    const [state, setState] = useState<State>(NOT_REQUIRED);

    useEffect(() => {
        let live = true;
        sharedHttpClient()
            .request({ path: "/auth/legal-versions", method: "GET", timeoutMs: 8_000, suppressAuthRedirect: true })
            .then((data) => {
                const parsed = versionsSchema.parse(data);
                if (!live || !parsed.required) return;
                const termsVersion = usable(parsed.terms_version);
                const privacyVersion = usable(parsed.privacy_version);
                setState((current) => ({
                    ...current,
                    required: true,
                    versions: termsVersion && privacyVersion ? { termsVersion, privacyVersion } : null,
                }));
            })
            // A failed read shows nothing; a refused submit still brings the checkbox up.
            .catch(() => undefined);
        return () => { live = false; };
    }, []);

    return {
        required: state.required,
        /** Required, but the server has no versions configured: nothing can be accepted. */
        unavailable: state.required && state.versions === null,
        accepted: state.accepted,
        updated: state.updated,
        versions: state.versions,
        /** True when the submit must wait for the box to be ticked. */
        blocked: state.required && (state.versions === null || !state.accepted),
        setAccepted: (accepted: boolean) => setState((current) => ({ ...current, accepted })),
        /** Fields to merge into the request: both versions when required, nothing otherwise. */
        fields(): { termsVersion?: string; privacyVersion?: string } {
            return state.required && state.versions ? { ...state.versions } : {};
        },
        /** Call from a submit's catch. True when it was a legal refusal and the checkbox is now showing. */
        handleRefusal(err: unknown): boolean {
            const versions = versionsFromRefusal(err);
            if (!versions) return false;
            setState({ required: true, versions, accepted: false, updated: true });
            return true;
        },
    };
}

export type LegalAcceptance = ReturnType<typeof useLegalAcceptance>;

export function LegalAcceptanceField({ legal, disabled }: { legal: LegalAcceptance; disabled?: boolean }) {
    const checkboxId = useId();
    const noticeId = useId();
    if (!legal.required) return null;

    if (legal.unavailable) {
        return (
            <p id={noticeId} role="alert" className="text-sm text-red-600 dark:text-red-400">
                Sign-up is temporarily unavailable. Please try again later.
            </p>
        );
    }

    return (
        <div className="space-y-2">
            {legal.updated ? (
                <p id={noticeId} role="alert" className="text-sm text-red-600 dark:text-red-400">
                    Our terms were updated. Please review and accept them to continue.
                </p>
            ) : null}
            <div className="flex items-start gap-2">
                <input
                    id={checkboxId}
                    type="checkbox"
                    checked={legal.accepted}
                    onChange={(event) => legal.setAccepted(event.target.checked)}
                    disabled={disabled}
                    required
                    aria-describedby={legal.updated ? noticeId : undefined}
                    className="mt-1 h-4 w-4 shrink-0"
                />
                <label htmlFor={checkboxId} className="text-sm text-muted-foreground">
                    I agree to the{" "}
                    <Link href="/terms" target="_blank" rel="noopener noreferrer" className="underline hover:text-foreground">Terms of Service</Link>
                    {" "}and{" "}
                    <Link href="/privacy" target="_blank" rel="noopener noreferrer" className="underline hover:text-foreground">Privacy Policy</Link>
                </label>
            </div>
        </div>
    );
}
