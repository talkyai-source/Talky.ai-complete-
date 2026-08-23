"use client";

/**
 * Security — a main sidebar destination, not a tab inside Settings (goals.md §4).
 *
 * WHY IT MOVED
 * ------------
 * Everything here is something a person goes looking for in a hurry: "is 2FA
 * on?", "what's still signed in?", "who changed that?". Buried three levels
 * down — Settings → Account & Security → Security tab — none of it is
 * findable at the moment it matters. §4 asks for one place, reachable in one
 * click.
 *
 * ROLE-GATED BY WHAT YOU CAN ACTUALLY MANAGE
 * -------------------------------------------
 * §4: "Display only security controls the current role can manage." Account
 * controls (password, MFA, passkeys, your own sessions) are yours regardless of
 * role. API keys and audit activity are tenant-wide and admin-only — and the
 * backend enforces that independently, so hiding them here is convenience, not
 * the security boundary.
 *
 * WHAT IS DELIBERATELY ABSENT
 * ----------------------------
 * §4 lists "Allowed IPs, **if supported**" and it is not: there is no IP
 * allow-list anywhere in the backend. Rather than ship an empty panel that
 * implies a control exists, the section says plainly that it is unavailable.
 * A security page that overstates what it enforces is worse than one that
 * admits a gap.
 */
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
    AlertTriangle,
    Check,
    ChevronRight,
    Clock,
    Fingerprint,
    Key,
    Loader2,
    Lock,
    Monitor,
    ScrollText,
    Shield,
    ShieldCheck,
} from "lucide-react";

import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth-context";
import { useAccessToken } from "@/lib/auth-hooks";
import { isPlatformAdminRole } from "@/lib/admin-access";
import { getMfaStatus, disableMfa, regenerateRecoveryCodes, validateTotpCode } from "@/lib/mfa-utils";
import { api } from "@/lib/api";
import MFASetup from "@/components/auth/mfa-setup";
import PasskeyRegistration from "@/components/auth/passkey-registration";
import PasskeyList from "@/components/auth/passkey-list";
import DeviceList from "@/components/auth/device-list";
import { InfoTip } from "@/components/ui/info-tip";

function Section({
    icon: Icon,
    title,
    description,
    children,
    tip,
}: {
    icon: React.ComponentType<{ className?: string }>;
    title: string;
    description: string;
    children: React.ReactNode;
    tip?: string;
}) {
    return (
        <section className="rounded-2xl border border-border bg-background p-5 shadow-sm">
            <div className="mb-4 flex items-start gap-3">
                <span className="mt-0.5 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-border bg-muted/50 text-muted-foreground">
                    <Icon className="h-4 w-4" />
                </span>
                <div className="min-w-0">
                    <h2 className="flex items-center gap-1.5 text-sm font-semibold text-foreground">
                        {title}
                        {tip && <InfoTip label={`About ${title}`}>{tip}</InfoTip>}
                    </h2>
                    <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>
                </div>
            </div>
            {children}
        </section>
    );
}

function AdminLinkCard({
    href,
    icon: Icon,
    title,
    description,
}: {
    href: string;
    icon: React.ComponentType<{ className?: string }>;
    title: string;
    description: string;
}) {
    return (
        <Link
            href={href}
            className="flex items-center gap-3 rounded-xl border border-border bg-background px-4 py-3 transition-colors hover:bg-accent"
        >
            <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium text-foreground">{title}</span>
                <span className="block truncate text-xs text-muted-foreground">{description}</span>
            </span>
            <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
        </Link>
    );
}

function ChangePasswordForm() {
    const [current, setCurrent] = useState("");
    const [next, setNext] = useState("");
    const [confirm, setConfirm] = useState("");
    const [busy, setBusy] = useState(false);
    const [done, setDone] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const mismatch = confirm.length > 0 && next !== confirm;

    const submit = useCallback(
        async (e: React.FormEvent) => {
            e.preventDefault();
            setError(null);
            setDone(false);
            if (next !== confirm) {
                setError("The new passwords do not match.");
                return;
            }
            setBusy(true);
            try {
                await api.changePassword(current, next);
                setDone(true);
                setCurrent("");
                setNext("");
                setConfirm("");
            } catch (err) {
                setError(err instanceof Error ? err.message : "Couldn't change your password.");
            } finally {
                setBusy(false);
            }
        },
        [current, next, confirm],
    );

    return (
        <form onSubmit={submit} className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-3">
                <label className="block">
                    <span className="mb-1 block text-xs font-medium text-muted-foreground">
                        Current password
                    </span>
                    <input
                        type="password"
                        autoComplete="current-password"
                        value={current}
                        onChange={(e) => setCurrent(e.target.value)}
                        required
                        className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-ring"
                    />
                </label>
                <label className="block">
                    <span className="mb-1 block text-xs font-medium text-muted-foreground">
                        New password
                    </span>
                    <input
                        type="password"
                        autoComplete="new-password"
                        value={next}
                        onChange={(e) => setNext(e.target.value)}
                        required
                        minLength={8}
                        className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-ring"
                    />
                </label>
                <label className="block">
                    <span className="mb-1 block text-xs font-medium text-muted-foreground">
                        Confirm new password
                    </span>
                    <input
                        type="password"
                        autoComplete="new-password"
                        value={confirm}
                        onChange={(e) => setConfirm(e.target.value)}
                        required
                        aria-invalid={mismatch}
                        className={`w-full rounded-lg border bg-background px-3 py-2 text-sm outline-none focus:border-ring ${mismatch ? "border-red-500/60" : "border-border"
                            }`}
                    />
                </label>
            </div>

            {/* Not a footnote: changing your password signs out every other
                session, which is the point of doing it after a scare. */}
            <p className="text-xs text-muted-foreground">
                Changing your password signs out all of your other sessions.
            </p>

            <div className="flex items-center gap-3">
                <Button type="submit" disabled={busy || mismatch}>
                    {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Lock className="h-4 w-4" />}
                    Change password
                </Button>
                {done && (
                    <span role="status" className="inline-flex items-center gap-1 text-sm text-emerald-600 dark:text-emerald-400">
                        <Check className="h-4 w-4" /> Password changed
                    </span>
                )}
            </div>

            {(error || mismatch) && (
                <p role="alert" className="text-sm text-red-600 dark:text-red-400">
                    {error ?? "The new passwords do not match."}
                </p>
            )}
        </form>
    );
}

export default function SecurityPage() {
    const { user } = useAuth();
    const token = useAccessToken() ?? "";
    const isAdmin = isPlatformAdminRole(user?.role) || user?.role === "tenant_admin";

    const [showMfaSetup, setShowMfaSetup] = useState(false);
    const [showPasskeySetup, setShowPasskeySetup] = useState(false);
    const [passkeyRefreshKey, setPasskeyRefreshKey] = useState(0);
    const [mfaEnabled, setMfaEnabled] = useState(false);
    const [mfaVerifiedAt, setMfaVerifiedAt] = useState<string | null>(null);
    const [mfaCodesRemaining, setMfaCodesRemaining] = useState(0);
    const [mfaLoading, setMfaLoading] = useState(false);

    // Turning 2FA off and rotating recovery codes moved here with the rest of
    // the security controls. They used to live in the Settings tab; leaving
    // them behind would have made this page tell users to go somewhere that no
    // longer has them.
    const [showDisable, setShowDisable] = useState(false);
    const [showRegen, setShowRegen] = useState(false);
    const [disablePassword, setDisablePassword] = useState("");
    const [regenCode, setRegenCode] = useState("");
    const [regenCodes, setRegenCodes] = useState<string[] | null>(null);
    const [busy, setBusy] = useState(false);
    const [mfaError, setMfaError] = useState<string | null>(null);

    const refreshMfa = useCallback(async (t: string) => {
        // No early return on an empty token: in cookie-only mode the JWT is
        // HttpOnly, so `t` is "" while the shared client still authenticates.
        try {
            setMfaLoading(true);
            const s = await getMfaStatus(t);
            setMfaEnabled(s.enabled);
            setMfaVerifiedAt(s.verifiedAt);
            setMfaCodesRemaining(s.recoveryCodesRemaining);
        } catch {
            /* 404 / network — leave the defaults */
        } finally {
            setMfaLoading(false);
        }
    }, []);

    useEffect(() => {
        void refreshMfa(token);
    }, [token, refreshMfa]);

    const regenerate = useCallback(async () => {
        setMfaError(null);
        if (!validateTotpCode(regenCode)) {
            setMfaError("Enter the current 6-digit code from your authenticator.");
            return;
        }
        setBusy(true);
        try {
            const { recoveryCodes } = await regenerateRecoveryCodes(token, regenCode);
            setRegenCodes(recoveryCodes);
            setRegenCode("");
            await refreshMfa(token);
        } catch (e) {
            setMfaError(e instanceof Error ? e.message : "That code was not accepted.");
        } finally {
            setBusy(false);
        }
    }, [regenCode, token, refreshMfa]);

    const disable = useCallback(async () => {
        setMfaError(null);
        if (!disablePassword) {
            setMfaError("Enter your password to confirm.");
            return;
        }
        setBusy(true);
        try {
            await disableMfa(token, disablePassword);
            setDisablePassword("");
            setShowDisable(false);
            await refreshMfa(token);
        } catch (e) {
            setMfaError(e instanceof Error ? e.message : "That password was not accepted.");
        } finally {
            setBusy(false);
        }
    }, [disablePassword, token, refreshMfa]);

    return (
        <DashboardLayout
            title="Security"
            description="Your account protection, active sessions, and — for admins — tenant-wide keys and audit activity."
        >
            <div className="space-y-4">
                {/* ── 1. Password / account security ───────────────────────── */}
                <Section
                    icon={Lock}
                    title="Password and account"
                    description="Change your password, or sign in without one using a passkey."
                    tip="A passkey is tied to this device's biometrics or PIN. It cannot be phished or reused on another site, which is why it is stronger than a password even with 2FA."
                >
                    <ChangePasswordForm />

                    <div className="mt-5 border-t border-border pt-4">
                        <div className="mb-3 flex items-center justify-between gap-3">
                            <div className="flex items-center gap-2">
                                <Fingerprint className="h-4 w-4 text-muted-foreground" />
                                <span className="text-sm font-medium text-foreground">Passkeys</span>
                            </div>
                            <Button
                                variant="outline"
                                size="sm"
                                onClick={() => setShowPasskeySetup((v) => !v)}
                            >
                                {showPasskeySetup ? "Cancel" : "Add a passkey"}
                            </Button>
                        </div>
                        {showPasskeySetup && (
                            <div className="mb-3">
                                <PasskeyRegistration
                                    token={token}
                                    onSuccess={() => {
                                        setShowPasskeySetup(false);
                                        setPasskeyRefreshKey((k) => k + 1);
                                    }}
                                />
                            </div>
                        )}
                        <PasskeyList refreshKey={passkeyRefreshKey} />
                    </div>
                </Section>

                {/* ── 2. Multi-factor authentication status ─────────────────── */}
                <Section
                    icon={ShieldCheck}
                    title="Two-factor authentication"
                    description="A second step at sign-in, from an authenticator app."
                    tip="Recovery codes are the way back in if you lose the authenticator. Each one works once. If the remaining count is low, regenerate them."
                >
                    {mfaLoading ? (
                        <p className="flex items-center gap-2 text-sm text-muted-foreground">
                            <Loader2 className="h-4 w-4 animate-spin" /> Checking status…
                        </p>
                    ) : mfaEnabled ? (
                        <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-3">
                            <p className="flex items-center gap-2 text-sm font-medium text-emerald-700 dark:text-emerald-300">
                                <Check className="h-4 w-4" /> Two-factor authentication is on
                            </p>
                            {mfaVerifiedAt && (
                                <p className="mt-1 text-xs text-emerald-700/80 dark:text-emerald-300/80">
                                    Verified {new Date(mfaVerifiedAt).toLocaleString()}
                                </p>
                            )}
                            <p className="mt-1 text-xs text-emerald-700/80 dark:text-emerald-300/80">
                                Recovery codes remaining: <strong>{mfaCodesRemaining}</strong>
                                {mfaCodesRemaining <= 2 && " — running low, regenerate them below."}
                            </p>

                            <div className="mt-3 flex flex-wrap gap-2">
                                <Button
                                    type="button"
                                    variant="outline"
                                    size="sm"
                                    onClick={() => { setShowRegen((v) => !v); setShowDisable(false); }}
                                >
                                    Regenerate recovery codes
                                </Button>
                                <Button
                                    type="button"
                                    variant="outline"
                                    size="sm"
                                    onClick={() => { setShowDisable((v) => !v); setShowRegen(false); }}
                                >
                                    Turn off
                                </Button>
                            </div>

                            {showRegen && (
                                <div className="mt-3 space-y-2 border-t border-emerald-500/20 pt-3">
                                    <label className="block text-xs font-medium text-foreground">
                                        Enter your current 6-digit code to confirm
                                        <input
                                            inputMode="numeric"
                                            autoComplete="one-time-code"
                                            value={regenCode}
                                            onChange={(e) => setRegenCode(e.target.value)}
                                            className="mt-1 w-40 rounded-lg border border-border bg-background px-3 py-2 text-sm tracking-widest outline-none focus:border-ring"
                                        />
                                    </label>
                                    <Button size="sm" disabled={busy} onClick={regenerate}>
                                        {busy && <Loader2 className="h-4 w-4 animate-spin" />}
                                        Generate new codes
                                    </Button>
                                    {regenCodes && (
                                        <div className="rounded-lg border border-border bg-background p-3">
                                            {/* Shown ONCE. Nothing stores these in a
                                                retrievable form, so a user who closes
                                                this without copying them has lost them. */}
                                            <p className="mb-2 text-xs font-semibold text-foreground">
                                                Save these now — they are shown only once.
                                            </p>
                                            <ul className="grid grid-cols-2 gap-1 font-mono text-xs text-foreground">
                                                {regenCodes.map((c) => <li key={c}>{c}</li>)}
                                            </ul>
                                        </div>
                                    )}
                                </div>
                            )}

                            {showDisable && (
                                <div className="mt-3 space-y-2 border-t border-emerald-500/20 pt-3">
                                    <label className="block text-xs font-medium text-foreground">
                                        Confirm your password to turn two-factor off
                                        <input
                                            type="password"
                                            autoComplete="current-password"
                                            value={disablePassword}
                                            onChange={(e) => setDisablePassword(e.target.value)}
                                            className="mt-1 w-full max-w-xs rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-ring"
                                        />
                                    </label>
                                    <Button size="sm" variant="outline" disabled={busy} onClick={disable}>
                                        {busy && <Loader2 className="h-4 w-4 animate-spin" />}
                                        Turn off two-factor authentication
                                    </Button>
                                </div>
                            )}

                            {mfaError && (
                                <p role="alert" className="mt-2 text-xs text-red-600 dark:text-red-400">
                                    {mfaError}
                                </p>
                            )}
                        </div>
                    ) : showMfaSetup ? (
                        <MFASetup
                            token={token}
                            onSuccess={() => {
                                setShowMfaSetup(false);
                                void refreshMfa(token);
                            }}
                            onCancel={() => setShowMfaSetup(false)}
                        />
                    ) : (
                        <div className="flex flex-wrap items-center gap-3">
                            <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-500/30 bg-amber-500/10 px-2.5 py-1 text-xs font-semibold text-amber-700 dark:text-amber-400">
                                <AlertTriangle className="h-3.5 w-3.5" /> Not enabled
                            </span>
                            <Button variant="outline" size="sm" onClick={() => setShowMfaSetup(true)}>
                                Enable two-factor authentication
                            </Button>
                        </div>
                    )}
                </Section>

                {/* ── 3. Active sessions ───────────────────────────────────── */}
                <Section
                    icon={Monitor}
                    title="Active sessions"
                    description="Everywhere your account is currently signed in. Revoke anything you do not recognise."
                    tip="Each entry is one browser or device. Revoking ends that session immediately; it does not change your password."
                >
                    <DeviceList token={token} />
                </Section>

                {/* ── 4 + 5. Tenant-wide, admin only ───────────────────────── */}
                {isAdmin && (
                    <Section
                        icon={Shield}
                        title="Tenant-wide security"
                        description="Keys and activity for the whole account. Visible because you are an administrator."
                    >
                        <div className="grid gap-3 sm:grid-cols-2">
                            <AdminLinkCard
                                href="/admin/api-keys"
                                icon={Key}
                                title="API keys and tokens"
                                description="Create, rotate and revoke programmatic access"
                            />
                            <AdminLinkCard
                                href="/admin/audit-logs"
                                icon={ScrollText}
                                title="Audit activity"
                                description="Who changed what, and when"
                            />
                            <AdminLinkCard
                                href="/admin"
                                icon={Shield}
                                title="Audit and access"
                                description="Security events and access review"
                            />
                            <AdminLinkCard
                                href="/admin/voice-security"
                                icon={ShieldCheck}
                                title="Voice security"
                                description="Call origination and telephony protections"
                            />
                        </div>
                    </Section>
                )}

                {/* ── 6. Data retention and recording controls ─────────────── */}
                <Section
                    icon={Clock}
                    title="Data retention and recordings"
                    description="How long call recordings and transcripts are kept."
                    tip="Retention is set by your plan, not per user. Recordings are removed after the recording window; transcripts are kept longer because they are far smaller."
                >
                    <p className="text-sm text-muted-foreground">
                        Retention is determined by your plan and applied automatically. See{" "}
                        <Link href="/billing" className="font-medium text-foreground underline underline-offset-2">
                            Billing
                        </Link>{" "}
                        for your current plan, and{" "}
                        <Link href="/recordings" className="font-medium text-foreground underline underline-offset-2">
                            Recordings
                        </Link>{" "}
                        for what is currently stored — each recording shows its own remaining
                        retention.
                    </p>
                </Section>

                {/* ── 7. Allowed IPs — honestly, not supported ─────────────── */}
                <Section
                    icon={AlertTriangle}
                    title="IP allow-list"
                    description="Restricting sign-in to specific networks."
                >
                    <p className="text-sm text-muted-foreground">
                        Not available yet. There is no IP allow-list enforced anywhere in the
                        platform today, so this is listed here only so its absence is explicit
                        rather than assumed.
                    </p>
                </Section>
            </div>
        </DashboardLayout>
    );
}
