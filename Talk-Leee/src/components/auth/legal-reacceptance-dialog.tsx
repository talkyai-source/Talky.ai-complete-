"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { usePathname } from "next/navigation";
import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import LogoutButton from "@/components/auth/logout-button";
import { useAuth } from "@/hooks/useAuth";
import { sharedHttpClient } from "@/lib/api";
import { isApiClientError } from "@/lib/http-client";
import { isPublicRoute } from "@/lib/public-routes";

/**
 * Existing customers who have not accepted the CURRENT Terms and Privacy Policy.
 * Only the server's answer to GET /auth/me decides: `legal_acceptance_required`
 * must be exactly `true`, with the versions to send back (`terms_version`,
 * `privacy_version`). A missing field (older server), a pending /auth/me, the
 * dev-mode stub, a public or auth page and a signed-out visitor show nothing.
 * The dialog closes only after POST /auth/legal-acceptance confirms.
 */

function nonEmpty(value: unknown): string | null {
    return typeof value === "string" && value.trim() !== "" ? value : null;
}

const noop = () => undefined;

// Reads made before the acceptance were refused with `legal_acceptance_required`; most of them do not use
// the query library and would stay in error, so the page is loaded again once the acceptance is saved.
const reloadPage = () => window.location.reload();

export function LegalReacceptanceDialog({ onAccepted = reloadPage }: { onAccepted?: () => void } = {}) {
    const pathname = usePathname() ?? "";
    const { user, refreshUser } = useAuth();
    const checkboxId = useId();
    const errorId = useId();
    const [ticked, setTicked] = useState(false);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    // The versions this tab saw confirmed; hides the dialog even if the re-read of /auth/me fails.
    const [confirmed, setConfirmed] = useState<string | null>(null);

    const me = user as unknown as Record<string, unknown> | null;
    const termsVersion = nonEmpty(me?.terms_version);
    const privacyVersion = nonEmpty(me?.privacy_version);
    const owed = !!user && me?.legal_acceptance_required === true && !!termsVersion && !!privacyVersion;
    const onPublicPage = isPublicRoute(pathname) || pathname === "/auth" || pathname.startsWith("/auth/");
    if (!owed || onPublicPage || confirmed === `${termsVersion}|${privacyVersion}`) return null;

    async function accept() {
        if (!ticked || saving || !termsVersion || !privacyVersion) return;
        setSaving(true);
        setError(null);
        try {
            await sharedHttpClient().request({
                path: "/auth/legal-acceptance",
                method: "POST",
                body: { terms_version: termsVersion, privacy_version: privacyVersion },
            });
        } catch (err) {
            if (isApiClientError(err) && err.status === 422) {
                // The versions moved on since this page loaded: read the new ones.
                void refreshUser({ silent: true });
                setError("The terms were updated again. Please read them and try again.");
            } else {
                setError("We could not save your acceptance. Please try again.");
            }
            setSaving(false);
            return;
        }
        setConfirmed(`${termsVersion}|${privacyVersion}`);
        setSaving(false);
        void refreshUser({ silent: true });
        onAccepted();
    }

    return (
        <Modal
            open
            onOpenChange={noop}
            size="sm"
            title="Our terms have been updated"
            description="Please read the updated Terms of Service and Privacy Policy and accept them to keep using Talk-Lee."
            footer={
                <div className="flex items-center justify-between gap-3">
                    <LogoutButton variant="link" size="sm" />
                    <Button type="button" onClick={() => void accept()} disabled={!ticked || saving}>
                        {saving ? "Saving..." : "Accept and continue"}
                    </Button>
                </div>
            }
        >
            <div className="space-y-3 text-sm">
                <p className="text-muted-foreground">
                    Read the{" "}
                    <Link href="/terms" target="_blank" rel="noopener noreferrer" className="underline hover:text-foreground">Terms of Service</Link>
                    {" "}and the{" "}
                    <Link href="/privacy" target="_blank" rel="noopener noreferrer" className="underline hover:text-foreground">Privacy Policy</Link>
                    {" "}(each opens in a new tab).
                </p>
                <div className="flex items-start gap-2">
                    <input
                        id={checkboxId}
                        type="checkbox"
                        checked={ticked}
                        onChange={(event) => setTicked(event.target.checked)}
                        disabled={saving}
                        aria-describedby={error ? errorId : undefined}
                        className="mt-1 h-4 w-4 shrink-0"
                    />
                    <label htmlFor={checkboxId} className="text-muted-foreground">
                        I have read and accept the Terms of Service and the Privacy Policy
                    </label>
                </div>
                {error ? (
                    <p id={errorId} role="alert" className="text-sm text-red-600 dark:text-red-400">{error}</p>
                ) : null}
            </div>
        </Modal>
    );
}
