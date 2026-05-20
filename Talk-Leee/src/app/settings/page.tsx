"use client";

import { useState, useEffect } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Select } from "@/components/ui/select";
import { useNotificationsActions, useNotificationsState } from "@/lib/notifications-client";
import type { NotificationPriority, NotificationRouting, NotificationType } from "@/lib/notifications";
import { Key, Lock } from "lucide-react";
import MFASetup from "@/components/auth/mfa-setup";
import PasskeyRegistration from "@/components/auth/passkey-registration";
import DeviceList from "@/components/auth/device-list";
import LogoutButton from "@/components/auth/logout-button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { TelephonyProvidersSection } from "@/components/settings/telephony-providers-section";
import { useAuth } from "@/lib/auth-context";
import { useAccessToken } from "@/lib/auth-hooks";
import { api } from "@/lib/api";
import { notificationsStore } from "@/lib/notifications";
import { Loader2 } from "lucide-react";
import {
    getMfaStatus,
    disableMfa,
    regenerateRecoveryCodes,
    downloadRecoveryCodes,
    validateTotpCode,
} from "@/lib/mfa-utils";

function clampNumber(n: number, min: number, max: number) {
    return Math.min(max, Math.max(min, n));
}

const notificationTypes: NotificationType[] = ["success", "warning", "error", "info"];
const priorities: NotificationPriority[] = ["low", "normal", "high"];
const routings: NotificationRouting[] = ["inApp", "webhook", "both", "none"];

export default function SettingsPage() {
    const { settings } = useNotificationsState();
    const { setSettings, setCategory } = useNotificationsActions();
    // Phase 5 universal-auth-state: token now comes from AuthContext via
    // the reactive useAccessToken() hook. The previous implementation
    // dynamically imported `getBrowserAuthToken` inside a mount-time
    // useEffect — which raced AuthContext bootstrap and snapshotted the
    // token at that moment, so a later /auth/refresh wouldn't reach the
    // MFA / Passkey panels. Empty string preserves the previous truthy
    // gating in the JSX (`token && <Foo token={token}/>`).
    const token = useAccessToken() ?? "";
    const [showMfaSetup, setShowMfaSetup] = useState(false);
    const [showPasskeySetup, setShowPasskeySetup] = useState(false);
    const [mfaEnabled, setMfaEnabled] = useState(false);
    const [mfaVerifiedAt, setMfaVerifiedAt] = useState<string | null>(null);
    const [mfaCodesRemaining, setMfaCodesRemaining] = useState<number>(0);
    const [mfaStatusLoading, setMfaStatusLoading] = useState(false);
    const [disablePassword, setDisablePassword] = useState("");
    const [disabling, setDisabling] = useState(false);
    const [regenCode, setRegenCode] = useState("");
    const [regenerating, setRegenerating] = useState(false);
    const [regenCodes, setRegenCodes] = useState<string[] | null>(null);
    const [showDisable, setShowDisable] = useState(false);
    const [showRegen, setShowRegen] = useState(false);

    async function refreshMfaStatus(t: string) {
        if (!t) return;
        try {
            setMfaStatusLoading(true);
            const s = await getMfaStatus(t);
            setMfaEnabled(s.enabled);
            setMfaVerifiedAt(s.verifiedAt);
            setMfaCodesRemaining(s.recoveryCodesRemaining);
        } catch {
            // 404 / network — leave defaults (mfaEnabled=false)
        } finally {
            setMfaStatusLoading(false);
        }
    }

    useEffect(() => {
        if (token) {
            void refreshMfaStatus(token);
        }
    }, [token]);

    async function handleDisableMfa() {
        if (!token || !disablePassword) return;
        setDisabling(true);
        try {
            await disableMfa(token, disablePassword);
            notificationsStore.create({
                type: "success",
                title: "Two-factor authentication disabled",
                message: "Re-enable any time from this screen.",
            });
            setDisablePassword("");
            setShowDisable(false);
            await refreshMfaStatus(token);
        } catch (e: unknown) {
            notificationsStore.create({
                type: "error",
                title: "Disable failed",
                message: e instanceof Error ? e.message : "Wrong password",
            });
        } finally {
            setDisabling(false);
        }
    }

    async function handleRegenCodes() {
        if (!token || !validateTotpCode(regenCode)) {
            notificationsStore.create({
                type: "error",
                title: "Enter your 6-digit authenticator code",
                message: "Recovery code regeneration requires reauthentication.",
            });
            return;
        }
        setRegenerating(true);
        try {
            const { recoveryCodes } = await regenerateRecoveryCodes(token, regenCode);
            setRegenCodes(recoveryCodes);
            setRegenCode("");
            await refreshMfaStatus(token);
        } catch (e: unknown) {
            notificationsStore.create({
                type: "error",
                title: "Regenerate failed",
                message: e instanceof Error ? e.message : "Invalid code",
            });
        } finally {
            setRegenerating(false);
        }
    }

    // Real profile, loaded from /auth/me via AuthContext.
    const { user, refreshUser } = useAuth();
    const [profileName, setProfileName] = useState("");
    const [profileBusiness, setProfileBusiness] = useState("");
    const [savingProfile, setSavingProfile] = useState(false);

    useEffect(() => {
        setProfileName(user?.name ?? "");
        setProfileBusiness(user?.business_name ?? "");
    }, [user?.name, user?.business_name]);

    const profileDirty =
        (user?.name ?? "") !== profileName.trim() ||
        (user?.business_name ?? "") !== profileBusiness.trim();

    async function handleSaveProfile() {
        setSavingProfile(true);
        try {
            const payload: { name?: string; business_name?: string } = {};
            if ((user?.name ?? "") !== profileName.trim()) payload.name = profileName.trim();
            if ((user?.business_name ?? "") !== profileBusiness.trim()) {
                if (!profileBusiness.trim()) throw new Error("Business name cannot be empty");
                payload.business_name = profileBusiness.trim();
            }
            if (Object.keys(payload).length === 0) return;
            await api.updateMe(payload);
            await refreshUser();
            notificationsStore.create({
                type: "success",
                title: "Profile updated",
                message: "Your changes have been saved.",
            });
        } catch (e: unknown) {
            notificationsStore.create({
                type: "error",
                title: "Save failed",
                message: e instanceof Error ? e.message : "Unknown error",
            });
        } finally {
            setSavingProfile(false);
        }
    }

    // Phase 5: token sourcing moved to useAccessToken() above. The
    // previous mount-time dynamic-import useEffect is no longer needed.

    return (
        <DashboardLayout title="Settings" description="Configure notifications, telephony, and your account.">
            <div className="mx-auto w-full max-w-5xl space-y-6">
                {/*
                 * Assistant tile — moved here from "AI Options". The
                 * underlying agent backend (langgraph state machine + tool
                 * calls) is feature-complete and the floating chat widget on
                 * every dashboard route already talks to it; the
                 * standalone /assistant management page (configurable
                 * runs / saved actions / scheduled jobs) is queued for
                 * v2 so we surface it here as "Coming soon" rather than
                 * dead space in the sidebar.
                 *
                 * Connectors used to live in this same row as a Quick-link
                 * tile and was promoted to a top-level sidebar item.
                 */}
                <div
                    className="rounded-2xl border border-border bg-background/70 p-4 backdrop-blur-sm dark:border-white/10 dark:bg-white/5"
                    aria-label="Assistant — Coming soon"
                >
                    <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                        <div className="min-w-0">
                            <div className="flex items-center gap-2">
                                <span className="text-sm font-semibold text-foreground">Assistant</span>
                                <span className="rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-amber-700 dark:text-amber-300">
                                    Coming soon · v2
                                </span>
                            </div>
                            <div className="mt-1 text-sm text-muted-foreground">
                                Configure assistant action plans, scheduled runs, and saved tool presets. The chat assistant in the bottom-left of every dashboard page already works today; the dedicated configuration screen ships with v2.
                            </div>
                        </div>
                        <button
                            type="button"
                            disabled
                            aria-disabled="true"
                            className="inline-flex cursor-not-allowed items-center justify-center rounded-xl border border-border bg-muted px-3 py-2 text-sm font-semibold text-muted-foreground opacity-70"
                        >
                            Coming soon
                        </button>
                    </div>
                </div>

                <Card className="dark:border-white/10 dark:bg-white/5 dark:text-white dark:hover:bg-white/10 dark:hover:shadow-[0_10px_30px_rgba(0,0,0,0.22)]">
                    <CardHeader>
                        <CardTitle className="dark:text-white">Display Preferences</CardTitle>
                        <CardDescription>Toast timing and sound.</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-5">
                        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                            <div className="space-y-2">
                                <Label htmlFor="toastDuration">Notification display duration (ms)</Label>
                                <Input
                                    id="toastDuration"
                                    type="number"
                                    min={1200}
                                    max={30000}
                                    value={settings.toastDurationMs}
                                    onChange={(e) =>
                                        setSettings({ toastDurationMs: clampNumber(Number(e.target.value) || 0, 1200, 30000) })
                                    }
                                />
                            </div>
                            <div className="space-y-2">
                                <Label>Notification sounds</Label>
                                <div className="flex items-center justify-between rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-sm transition-[transform,background-color,box-shadow,border-color] duration-150 ease-out hover:-translate-y-0.5 hover:bg-gray-50 hover:shadow-md dark:border-white/10 dark:bg-white/5 dark:hover:bg-white/10 dark:hover:shadow-[0_10px_30px_rgba(0,0,0,0.22)]">
                                    <div className="text-sm font-semibold text-gray-900 dark:text-zinc-100">Enable sounds</div>
                                    <Switch
                                        checked={settings.soundsEnabled}
                                        onCheckedChange={(v) => setSettings({ soundsEnabled: v })}
                                        ariaLabel="Enable notification sounds"
                                    />
                                </div>
                            </div>
                        </div>
                    </CardContent>
                </Card>

                <Card className="dark:border-white/10 dark:bg-white/5 dark:text-white dark:hover:bg-white/10 dark:hover:shadow-[0_10px_30px_rgba(0,0,0,0.22)]">
                    <CardHeader>
                        <CardTitle className="dark:text-white">Notification Preferences</CardTitle>
                        <CardDescription>Enable categories, tune priority, and configure routing.</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        {notificationTypes.map((type) => (
                            <div key={type} className="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm transition-[transform,background-color,box-shadow,border-color] duration-150 ease-out hover:-translate-y-0.5 hover:bg-gray-50 hover:shadow-md dark:border-white/10 dark:bg-white/5 dark:hover:bg-white/10 dark:hover:shadow-[0_10px_30px_rgba(0,0,0,0.22)]">
                                <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                                    <div className="min-w-0">
                                        <div className="text-sm font-semibold capitalize text-gray-900 dark:text-zinc-100">{type}</div>
                                    <div className="mt-1 text-sm text-muted-foreground">Toggle delivery and adjust priority.</div>
                                    </div>
                                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
                                        <div className="flex items-center justify-between gap-3 rounded-xl border border-gray-200 bg-white px-4 py-2 shadow-sm transition-[transform,background-color,box-shadow,border-color] duration-150 ease-out hover:-translate-y-0.5 hover:bg-gray-50 hover:shadow-md dark:border-white/10 dark:bg-white/5 dark:hover:bg-white/10 dark:hover:shadow-[0_10px_30px_rgba(0,0,0,0.22)] sm:w-[220px]">
                                            <div className="text-sm font-semibold text-gray-900 dark:text-zinc-100">Enabled</div>
                                            <Switch
                                                checked={settings.category[type].enabled}
                                                onCheckedChange={(v) => setCategory(type, { enabled: v })}
                                                ariaLabel={`Enable ${type} notifications`}
                                            />
                                        </div>
                                        <div className="sm:w-[220px]">
                                            <Select
                                                value={settings.category[type].priority}
                                                onChange={(v) => setCategory(type, { priority: v as NotificationPriority })}
                                                ariaLabel={`${type} priority`}
                                            >
                                                {priorities.map((p) => (
                                                    <option key={p} value={p}>
                                                        {p}
                                                    </option>
                                                ))}
                                            </Select>
                                        </div>
                                        <div className="sm:w-[220px]">
                                            <Select
                                                value={settings.category[type].routing}
                                                onChange={(v) => setCategory(type, { routing: v as NotificationRouting })}
                                                ariaLabel={`${type} routing`}
                                            >
                                                {routings.map((r) => (
                                                    <option key={r} value={r}>
                                                        {r}
                                                    </option>
                                                ))}
                                            </Select>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        ))}
                    </CardContent>
                </Card>

                <Card className="dark:border-white/10 dark:bg-white/5 dark:text-white dark:hover:bg-white/10 dark:hover:shadow-[0_10px_30px_rgba(0,0,0,0.22)]">
                    <CardHeader>
                        <CardTitle className="dark:text-white">Account & Security</CardTitle>
                        <CardDescription>Profile, authentication, security, and device management.</CardDescription>
                    </CardHeader>
                    <CardContent>
                        <Tabs defaultValue="profile" className="w-full">
                            <TabsList className="grid w-full grid-cols-5">
                                <TabsTrigger value="profile">Profile</TabsTrigger>
                                <TabsTrigger value="security">Security</TabsTrigger>
                                <TabsTrigger value="devices">Devices</TabsTrigger>
                                <TabsTrigger value="telephony">Telephony</TabsTrigger>
                                <TabsTrigger value="logout">Logout</TabsTrigger>
                            </TabsList>

                            {/* Profile Tab */}
                            <TabsContent value="profile" className="space-y-4">
                                <div className="rounded-2xl border border-gray-200 bg-white p-4 space-y-4 shadow-sm transition-[transform,background-color,box-shadow,border-color] duration-150 ease-out hover:-translate-y-0.5 hover:bg-gray-50 hover:shadow-md dark:border-white/10 dark:bg-white/5 dark:hover:bg-white/10 dark:hover:shadow-[0_10px_30px_rgba(0,0,0,0.22)]">
                                    <div className="text-sm font-semibold text-gray-900 dark:text-zinc-100">Profile Information</div>
                                    {!user ? (
                                        <div className="flex items-center gap-2 text-sm text-muted-foreground py-4">
                                            <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Loading profile…
                                        </div>
                                    ) : (
                                        <div className="space-y-4">
                                            <div className="space-y-2">
                                                <Label htmlFor="profileName">Name</Label>
                                                <Input
                                                    id="profileName"
                                                    value={profileName}
                                                    onChange={(e) => setProfileName(e.target.value)}
                                                    placeholder="Your full name"
                                                />
                                            </div>
                                            <div className="space-y-2">
                                                <Label htmlFor="profileBusiness">Business name</Label>
                                                <Input
                                                    id="profileBusiness"
                                                    value={profileBusiness}
                                                    onChange={(e) => setProfileBusiness(e.target.value)}
                                                    placeholder="Acme Inc."
                                                />
                                            </div>
                                            <div className="space-y-2">
                                                <Label htmlFor="profileEmail">Email</Label>
                                                <Input
                                                    id="profileEmail"
                                                    type="email"
                                                    value={user.email}
                                                    readOnly
                                                    disabled
                                                />
                                                <div className="text-xs text-muted-foreground">
                                                    Email is the identifier you signed in with — contact support to change it.
                                                </div>
                                            </div>
                                            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3 rounded-lg border border-border bg-muted/30 p-3 text-xs">
                                                <div>
                                                    <div className="font-semibold text-muted-foreground">Role</div>
                                                    <div className="mt-0.5 text-foreground">{user.role}</div>
                                                </div>
                                                <div>
                                                    <div className="font-semibold text-muted-foreground">Minutes remaining</div>
                                                    <div className="mt-0.5 text-foreground tabular-nums">
                                                        {user.minutes_remaining.toLocaleString()}
                                                    </div>
                                                </div>
                                                <div>
                                                    <div className="font-semibold text-muted-foreground">Tenant</div>
                                                    <div className="mt-0.5 text-foreground font-mono truncate" title={user.tenant_id ?? ""}>
                                                        {user.tenant_id ? user.tenant_id.slice(0, 8) + "…" : "—"}
                                                    </div>
                                                </div>
                                            </div>
                                            <div className="flex justify-end">
                                                <Button onClick={handleSaveProfile} disabled={!profileDirty || savingProfile}>
                                                    {savingProfile ? (
                                                        <><Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> Saving…</>
                                                    ) : (
                                                        "Save changes"
                                                    )}
                                                </Button>
                                            </div>
                                        </div>
                                    )}
                                </div>
                            </TabsContent>

                            {/* Security Tab */}
                            <TabsContent value="security" className="space-y-4">
                                {/* Two-Factor Authentication */}
                                <div className="rounded-2xl border border-gray-200 bg-white p-4 space-y-4 shadow-sm transition-[transform,background-color,box-shadow,border-color] duration-150 ease-out hover:-translate-y-0.5 hover:bg-gray-50 hover:shadow-md dark:border-white/10 dark:bg-white/5 dark:hover:bg-white/10 dark:hover:shadow-[0_10px_30px_rgba(0,0,0,0.22)]">
                                    <div className="flex items-center justify-between">
                                        <div>
                                            <div className="text-sm font-semibold text-gray-900 dark:text-zinc-100">Two-Factor Authentication</div>
                                            <div className="mt-1 text-sm text-muted-foreground">Add an extra layer of security to your account</div>
                                        </div>
                                        <Lock className="h-5 w-5 text-muted-foreground" aria-hidden />
                                    </div>
                                    {mfaStatusLoading ? (
                                        <div className="flex items-center gap-2 text-sm text-muted-foreground py-2">
                                            <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Checking status…
                                        </div>
                                    ) : null}

                                    {!mfaStatusLoading && !showMfaSetup && !mfaEnabled && (
                                        <Button onClick={() => setShowMfaSetup(true)} variant="outline" className="w-full">
                                            Enable Two-Factor Authentication
                                        </Button>
                                    )}

                                    {showMfaSetup && !mfaEnabled && token && (
                                        <MFASetup
                                            token={token}
                                            onSuccess={() => {
                                                setShowMfaSetup(false);
                                                void refreshMfaStatus(token);
                                            }}
                                            onCancel={() => setShowMfaSetup(false)}
                                        />
                                    )}

                                    {mfaEnabled && (
                                        <>
                                            <div className="rounded-md border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-700 dark:border-green-800 dark:bg-green-950/30 dark:text-green-300">
                                                ✓ Two-factor authentication is active
                                                {mfaVerifiedAt && (
                                                    <span className="block text-xs opacity-80">
                                                        Verified {new Date(mfaVerifiedAt).toLocaleString()}
                                                    </span>
                                                )}
                                                <span className="block text-xs opacity-80">
                                                    Recovery codes remaining: {mfaCodesRemaining}
                                                </span>
                                            </div>

                                            <div className="flex flex-wrap gap-2">
                                                <Button
                                                    type="button"
                                                    variant="outline"
                                                    size="sm"
                                                    onClick={() => {
                                                        setShowRegen((v) => !v);
                                                        setShowDisable(false);
                                                    }}
                                                >
                                                    Regenerate recovery codes
                                                </Button>
                                                <Button
                                                    type="button"
                                                    variant="destructive"
                                                    size="sm"
                                                    onClick={() => {
                                                        setShowDisable((v) => !v);
                                                        setShowRegen(false);
                                                    }}
                                                >
                                                    Disable
                                                </Button>
                                            </div>

                                            {showRegen && !regenCodes && (
                                                <div className="space-y-2 rounded-md border border-border bg-muted/30 p-3">
                                                    <Label htmlFor="regen-code">Current 6-digit code</Label>
                                                    <Input
                                                        id="regen-code"
                                                        inputMode="numeric"
                                                        pattern="[0-9]*"
                                                        maxLength={8}
                                                        value={regenCode}
                                                        onChange={(e) => setRegenCode(e.target.value.replace(/\D/g, "").slice(0, 8))}
                                                        placeholder="123 456"
                                                    />
                                                    <div className="text-xs text-muted-foreground">
                                                        Regenerating invalidates your existing recovery codes.
                                                    </div>
                                                    <Button
                                                        onClick={handleRegenCodes}
                                                        disabled={regenerating || !validateTotpCode(regenCode)}
                                                        size="sm"
                                                    >
                                                        {regenerating ? (
                                                            <><Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> Regenerating…</>
                                                        ) : (
                                                            "Generate new codes"
                                                        )}
                                                    </Button>
                                                </div>
                                            )}

                                            {regenCodes && (
                                                <div className="space-y-2 rounded-md border border-emerald-500/30 bg-emerald-500/10 p-3">
                                                    <div className="text-sm font-semibold text-emerald-700 dark:text-emerald-400">
                                                        New recovery codes — save them now, they won&apos;t be shown again.
                                                    </div>
                                                    <div className="grid grid-cols-1 gap-1 sm:grid-cols-2">
                                                        {regenCodes.map((c, i) => (
                                                            <code key={i} className="rounded bg-background/60 px-2 py-1 text-xs font-mono">
                                                                {c}
                                                            </code>
                                                        ))}
                                                    </div>
                                                    <div className="flex gap-2 pt-1">
                                                        <Button
                                                            variant="outline"
                                                            size="sm"
                                                            onClick={() => downloadRecoveryCodes(regenCodes)}
                                                        >
                                                            Download
                                                        </Button>
                                                        <Button
                                                            variant="ghost"
                                                            size="sm"
                                                            onClick={() => {
                                                                setRegenCodes(null);
                                                                setShowRegen(false);
                                                            }}
                                                        >
                                                            Done
                                                        </Button>
                                                    </div>
                                                </div>
                                            )}

                                            {showDisable && (
                                                <div className="space-y-2 rounded-md border border-red-500/30 bg-red-500/10 p-3">
                                                    <Label htmlFor="disable-password">Current password</Label>
                                                    <Input
                                                        id="disable-password"
                                                        type="password"
                                                        value={disablePassword}
                                                        onChange={(e) => setDisablePassword(e.target.value)}
                                                        autoComplete="current-password"
                                                    />
                                                    <div className="text-xs text-muted-foreground">
                                                        Disabling 2FA weakens your account security. We recommend keeping it on.
                                                    </div>
                                                    <Button
                                                        variant="destructive"
                                                        size="sm"
                                                        onClick={handleDisableMfa}
                                                        disabled={disabling || disablePassword.length < 4}
                                                    >
                                                        {disabling ? (
                                                            <><Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> Disabling…</>
                                                        ) : (
                                                            "Confirm disable"
                                                        )}
                                                    </Button>
                                                </div>
                                            )}
                                        </>
                                    )}
                                </div>

                                {/* Passkeys */}
                                <div className="rounded-2xl border border-gray-200 bg-white p-4 space-y-4 shadow-sm transition-[transform,background-color,box-shadow,border-color] duration-150 ease-out hover:-translate-y-0.5 hover:bg-gray-50 hover:shadow-md dark:border-white/10 dark:bg-white/5 dark:hover:bg-white/10 dark:hover:shadow-[0_10px_30px_rgba(0,0,0,0.22)]">
                                    <div className="flex items-center justify-between">
                                        <div>
                                            <div className="text-sm font-semibold text-gray-900 dark:text-zinc-100">Passkeys</div>
                                            <div className="mt-1 text-sm text-muted-foreground">Sign in with biometric or security keys</div>
                                        </div>
                                        <Key className="h-5 w-5 text-muted-foreground" aria-hidden />
                                    </div>
                                    {!showPasskeySetup && (
                                        <Button onClick={() => setShowPasskeySetup(true)} variant="outline" className="w-full">
                                            Add a Passkey
                                        </Button>
                                    )}
                                    {showPasskeySetup && token && (
                                        <PasskeyRegistration
                                            token={token}
                                            onSuccess={() => setShowPasskeySetup(false)}
                                            onCancel={() => setShowPasskeySetup(false)}
                                        />
                                    )}
                                </div>
                            </TabsContent>

                            {/* Devices Tab */}
                            <TabsContent value="devices" className="space-y-4">
                                {token && <DeviceList token={token} />}
                            </TabsContent>

                            {/* Telephony Tab */}
                            <TabsContent value="telephony" className="space-y-4">
                                <TelephonyProvidersSection />
                            </TabsContent>

                            {/* Logout Tab */}
                            <TabsContent value="logout" className="space-y-4">
                                <div className="rounded-2xl border border-gray-200 bg-white p-4 space-y-4 shadow-sm transition-[transform,background-color,box-shadow,border-color] duration-150 ease-out hover:-translate-y-0.5 hover:bg-gray-50 hover:shadow-md dark:border-white/10 dark:bg-white/5 dark:hover:bg-white/10 dark:hover:shadow-[0_10px_30px_rgba(0,0,0,0.22)]">
                                    <div>
                                        <div className="text-sm font-semibold text-gray-900 dark:text-zinc-100">Sign out</div>
                                        <div className="mt-1 text-sm text-muted-foreground">End your current session and return to the login page</div>
                                    </div>
                                    {token && (
                                        <LogoutButton
                                            token={token}
                                            variant="destructive"
                                            size="default"
                                            showLabel={true}
                                        />
                                    )}
                                </div>
                            </TabsContent>
                        </Tabs>
                    </CardContent>
                </Card>
            </div>
        </DashboardLayout>
    );
}
