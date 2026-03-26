"use client";

import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Select } from "@/components/ui/select";
import { useNotificationsActions, useNotificationsState } from "@/lib/notifications-client";
import type { NotificationPriority, NotificationRouting, NotificationType } from "@/lib/notifications";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { registerPasskey, listPasskeys, deletePasskey, isWebAuthnSupported, type PasskeyCredential } from "@/lib/passkeys";
import { Download, Trash2, ShieldCheck, Fingerprint, Monitor, Loader2, Plus, Copy, Check } from "lucide-react";
import Link from "next/link";

function clampNumber(n: number, min: number, max: number) {
    return Math.min(max, Math.max(min, n));
}

function downloadTextFile(filename: string, contents: string) {
    const blob = new Blob([contents], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
}

const notificationTypes: NotificationType[] = ["success", "warning", "error", "info"];
const priorities: NotificationPriority[] = ["low", "normal", "high"];
const routings: NotificationRouting[] = ["inApp", "webhook", "both", "none"];

// ---- MFA Setup Component ----
function MfaSetupSection() {
    const [mfaStatus, setMfaStatus] = useState<{ enabled: boolean; recovery_codes_remaining: number } | null>(null);
    const [loading, setLoading] = useState(true);
    const [actionLoading, setActionLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [message, setMessage] = useState<string | null>(null);

    // Setup flow state
    const [setupStep, setSetupStep] = useState<"idle" | "qr" | "confirm" | "codes">("idle");
    const [qrCode, setQrCode] = useState("");
    const [totpCode, setTotpCode] = useState("");
    const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
    const [codesCopied, setCodesCopied] = useState(false);

    // Disable flow
    const [disablePassword, setDisablePassword] = useState("");

    const loadMfaStatus = useCallback(async () => {
        try {
            const status = await api.getMfaStatus();
            setMfaStatus(status);
        } catch {
            setMfaStatus({ enabled: false, recovery_codes_remaining: 0 });
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { loadMfaStatus(); }, [loadMfaStatus]);

    async function handleSetupMfa() {
        setActionLoading(true);
        setError(null);
        try {
            const result = await api.setupMfa();
            setQrCode(result.qr_code);
            setSetupStep("qr");
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to start MFA setup.");
        } finally {
            setActionLoading(false);
        }
    }

    async function handleConfirmMfa() {
        setActionLoading(true);
        setError(null);
        try {
            const result = await api.confirmMfa(totpCode);
            setRecoveryCodes(result.recovery_codes);
            setSetupStep("codes");
            setMessage(result.message);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Invalid code. Try again.");
        } finally {
            setActionLoading(false);
        }
    }

    async function handleDisableMfa() {
        setActionLoading(true);
        setError(null);
        try {
            await api.disableMfa(disablePassword);
            setMfaStatus({ enabled: false, recovery_codes_remaining: 0 });
            setDisablePassword("");
            setMessage("MFA disabled successfully.");
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to disable MFA.");
        } finally {
            setActionLoading(false);
        }
    }

    function handleCopyCodes() {
        navigator.clipboard.writeText(recoveryCodes.join("\n"));
        setCodesCopied(true);
        setTimeout(() => setCodesCopied(false), 2000);
    }

    function handleFinishSetup() {
        setSetupStep("idle");
        setTotpCode("");
        setQrCode("");
        setRecoveryCodes([]);
        loadMfaStatus();
    }

    if (loading) {
        return (
            <div className="flex items-center gap-2 text-muted-foreground text-sm py-2">
                <Loader2 className="h-4 w-4 animate-spin" /> Loading MFA status...
            </div>
        );
    }

    return (
        <div className="space-y-4">
            <div>
                <div className="text-sm font-semibold text-gray-900 dark:text-zinc-100 flex items-center gap-2">
                    <ShieldCheck className="h-4 w-4" />
                    Two-Factor Authentication
                </div>
                <div className="mt-1 text-sm text-muted-foreground">
                    {mfaStatus?.enabled
                        ? `MFA is active. ${mfaStatus.recovery_codes_remaining} recovery code(s) remaining.`
                        : "Add an extra layer of security with an authenticator app."}
                </div>
            </div>

            {error && <div className="text-xs text-red-500 bg-red-50 border border-red-200 rounded-md p-2">{error}</div>}
            {message && <div className="text-xs text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-800/30 rounded-md p-2">{message}</div>}

            {/* Idle â€” MFA not enabled */}
            {setupStep === "idle" && !mfaStatus?.enabled && (
                <Button
                    type="button"
                    onClick={handleSetupMfa}
                    disabled={actionLoading}
                    className="border-teal-600 bg-teal-600 text-white hover:bg-teal-700 hover:border-teal-700 hover:text-white"
                >
                    {actionLoading ? <><Loader2 className="h-4 w-4 animate-spin" /> Setting up...</> : "Enable Two-Factor Authentication"}
                </Button>
            )}

            {/* QR code step */}
            {setupStep === "qr" && (
                <div className="space-y-3">
                    <p className="text-sm text-muted-foreground">
                        Scan this QR code with your authenticator app (Google Authenticator, Authy, etc.):
                    </p>
                    {qrCode && (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={qrCode} alt="TOTP QR Code" className="mx-auto w-48 h-48 rounded-lg border" />
                    )}
                    <div className="space-y-2">
                        <Label htmlFor="totp-confirm">Enter the 6-digit code from your app</Label>
                        <Input
                            id="totp-confirm"
                            value={totpCode}
                            onChange={(e) => setTotpCode(e.target.value)}
                            placeholder="123456"
                            maxLength={6}
                            inputMode="numeric"
                            pattern="[0-9]{6}"
                            className="text-center tracking-widest text-lg max-w-[200px]"
                        />
                    </div>
                    <div className="flex gap-2">
                        <Button
                            type="button"
                            onClick={handleConfirmMfa}
                            disabled={actionLoading || totpCode.trim().length !== 6}
                            className="border-teal-600 bg-teal-600 text-white hover:bg-teal-700 hover:border-teal-700 hover:text-white"
                        >
                            {actionLoading ? <><Loader2 className="h-4 w-4 animate-spin" /> Verifying...</> : "Verify & Activate"}
                        </Button>
                        <Button type="button" variant="outline" onClick={() => { setSetupStep("idle"); setQrCode(""); setTotpCode(""); setError(null); }}>
                            Cancel
                        </Button>
                    </div>
                </div>
            )}

            {/* Recovery codes shown once */}
            {setupStep === "codes" && (
                <div className="space-y-3">
                    <p className="text-sm font-semibold text-amber-600 dark:text-amber-400">
                        âš  Save these recovery codes now â€” they will not be shown again!
                    </p>
                    <div className="grid grid-cols-2 gap-2 rounded-lg border bg-gray-50 dark:bg-white/5 p-3 font-mono text-sm">
                        {recoveryCodes.map((code, i) => (
                            <div key={i} className="text-center py-1">{code}</div>
                        ))}
                    </div>
                    <div className="flex gap-2">
                        <Button type="button" variant="outline" onClick={handleCopyCodes}>
                            {codesCopied ? <><Check className="h-4 w-4" /> Copied!</> : <><Copy className="h-4 w-4" /> Copy codes</>}
                        </Button>
                        <Button
                            type="button"
                            onClick={handleFinishSetup}
                            className="border-teal-600 bg-teal-600 text-white hover:bg-teal-700 hover:border-teal-700 hover:text-white"
                        >
                            I&apos;ve saved my codes
                        </Button>
                    </div>
                </div>
            )}

            {/* MFA enabled â€” disable option */}
            {setupStep === "idle" && mfaStatus?.enabled && (
                <div className="space-y-3">
                    <div className="flex items-center justify-between gap-3 rounded-xl border border-emerald-200 dark:border-emerald-800/30 bg-emerald-50 dark:bg-emerald-900/20 px-4 py-3">
                        <div className="text-sm font-semibold text-emerald-700 dark:text-emerald-400">MFA Active</div>
                        <ShieldCheck className="h-5 w-5 text-emerald-600 dark:text-emerald-400" />
                    </div>
                    <div className="space-y-2">
                        <Label htmlFor="disable-mfa-password">Enter your password to disable MFA</Label>
                        <Input
                            id="disable-mfa-password"
                            type="password"
                            value={disablePassword}
                            onChange={(e) => setDisablePassword(e.target.value)}
                            placeholder="Current password"
                        />
                    </div>
                    <Button
                        type="button"
                        variant="destructive"
                        onClick={handleDisableMfa}
                        disabled={actionLoading || !disablePassword.trim()}
                    >
                        {actionLoading ? <><Loader2 className="h-4 w-4 animate-spin" /> Disabling...</> : "Disable MFA"}
                    </Button>
                </div>
            )}
        </div>
    );
}

// ---- Passkeys Management Component ----
function PasskeysSection() {
    const [passkeys, setPasskeys] = useState<PasskeyCredential[]>([]);
    const [loading, setLoading] = useState(true);
    const [actionLoading, setActionLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [message, setMessage] = useState<string | null>(null);
    const [webauthnSupported, setWebauthnSupported] = useState(false);

    useEffect(() => { setWebauthnSupported(isWebAuthnSupported()); }, []);

    const loadPasskeys = useCallback(async () => {
        try {
            const list = await listPasskeys();
            setPasskeys(list);
        } catch {
            setPasskeys([]);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { loadPasskeys(); }, [loadPasskeys]);

    async function handleRegister() {
        setActionLoading(true);
        setError(null);
        try {
            await registerPasskey("any", `Passkey ${new Date().toLocaleDateString()}`);
            setMessage("Passkey registered successfully.");
            await loadPasskeys();
        } catch (err) {
            if (err instanceof Error && (err.message.includes("cancelled") || err.message.includes("abort"))) {
                setActionLoading(false);
                return;
            }
            setError(err instanceof Error ? err.message : "Failed to register passkey.");
        } finally {
            setActionLoading(false);
        }
    }

    async function handleDelete(id: string) {
        setActionLoading(true);
        setError(null);
        try {
            await deletePasskey(id);
            setMessage("Passkey removed.");
            await loadPasskeys();
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to remove passkey.");
        } finally {
            setActionLoading(false);
        }
    }

    return (
        <div className="space-y-4">
            <div>
                <div className="text-sm font-semibold text-gray-900 dark:text-zinc-100 flex items-center gap-2">
                    <Fingerprint className="h-4 w-4" />
                    Passkeys
                </div>
                <div className="mt-1 text-sm text-muted-foreground">
                    Sign in with biometrics or a security key â€” no password needed.
                </div>
            </div>

            {error && <div className="text-xs text-red-500 bg-red-50 border border-red-200 rounded-md p-2">{error}</div>}
            {message && <div className="text-xs text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-800/30 rounded-md p-2">{message}</div>}

            {loading ? (
                <div className="flex items-center gap-2 text-muted-foreground text-sm">
                    <Loader2 className="h-4 w-4 animate-spin" /> Loading passkeys...
                </div>
            ) : (
                <>
                    {passkeys.length === 0 ? (
                        <p className="text-sm text-muted-foreground">No passkeys registered yet.</p>
                    ) : (
                        <div className="space-y-2">
                            {passkeys.map((pk) => (
                                <div key={pk.id} className="flex items-center justify-between gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-sm dark:border-white/10 dark:bg-white/5">
                                    <div className="min-w-0">
                                        <div className="text-sm font-medium truncate">{pk.display_name}</div>
                                        <div className="text-xs text-muted-foreground">
                                            {pk.device_type === "multiDevice" ? "Synced" : "Single device"} Â· Created {new Date(pk.created_at).toLocaleDateString()}
                                        </div>
                                    </div>
                                    <Button
                                        type="button"
                                        variant="destructive"
                                        size="sm"
                                        onClick={() => handleDelete(pk.id)}
                                        disabled={actionLoading}
                                    >
                                        <Trash2 className="h-3 w-3" />
                                    </Button>
                                </div>
                            ))}
                        </div>
                    )}

                    {webauthnSupported && (
                        <Button
                            type="button"
                            variant="outline"
                            onClick={handleRegister}
                            disabled={actionLoading}
                        >
                            {actionLoading ? <><Loader2 className="h-4 w-4 animate-spin" /> Adding...</> : <><Plus className="h-4 w-4" /> Add passkey</>}
                        </Button>
                    )}

                    {!webauthnSupported && (
                        <p className="text-xs text-muted-foreground">Passkeys are not supported in this browser.</p>
                    )}
                </>
            )}
        </div>
    );
}

// ---- Active Sessions Component ----
function ActiveSessionsSection() {
    const [sessions, setSessions] = useState<Array<{
        id: string; ip_address: string; user_agent: string | null;
        created_at: string; last_active_at: string; is_current: boolean;
    }>>([]);
    const [loading, setLoading] = useState(true);
    const [actionLoading, setActionLoading] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    const loadSessions = useCallback(async () => {
        try {
            const result = await api.getActiveSessions();
            setSessions(result.sessions ?? []);
        } catch {
            setSessions([]);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { loadSessions(); }, [loadSessions]);

    async function handleRevoke(sessionId: string) {
        setActionLoading(sessionId);
        setError(null);
        try {
            await api.revokeSession(sessionId);
            await loadSessions();
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to revoke session.");
        } finally {
            setActionLoading(null);
        }
    }

    return (
        <div className="space-y-4">
            <div>
                <div className="text-sm font-semibold text-gray-900 dark:text-zinc-100 flex items-center gap-2">
                    <Monitor className="h-4 w-4" />
                    Active Sessions
                </div>
                <div className="mt-1 text-sm text-muted-foreground">
                    Manage your active login sessions across devices.
                </div>
            </div>

            {error && <div className="text-xs text-red-500 bg-red-50 border border-red-200 rounded-md p-2">{error}</div>}

            {loading ? (
                <div className="flex items-center gap-2 text-muted-foreground text-sm">
                    <Loader2 className="h-4 w-4 animate-spin" /> Loading sessions...
                </div>
            ) : sessions.length === 0 ? (
                <p className="text-sm text-muted-foreground">No active sessions found.</p>
            ) : (
                <div className="space-y-2">
                    {sessions.map((session) => (
                        <div key={session.id} className="flex items-center justify-between gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-sm dark:border-white/10 dark:bg-white/5">
                            <div className="min-w-0 flex-1">
                                <div className="text-sm font-medium flex items-center gap-2">
                                    {session.ip_address}
                                    {session.is_current && (
                                        <span className="inline-flex items-center rounded-full bg-teal-100 px-2 py-0.5 text-[10px] font-semibold text-teal-700 dark:bg-teal-900/40 dark:text-teal-400">
                                            Current
                                        </span>
                                    )}
                                </div>
                                <div className="text-xs text-muted-foreground truncate">
                                    {session.user_agent ? session.user_agent.split(" ").slice(0, 4).join(" ") : "Unknown device"}
                                    {" Â· "}Last active {new Date(session.last_active_at).toLocaleString()}
                                </div>
                            </div>
                            {!session.is_current && (
                                <Button
                                    type="button"
                                    variant="destructive"
                                    size="sm"
                                    onClick={() => handleRevoke(session.id)}
                                    disabled={actionLoading === session.id}
                                >
                                    {actionLoading === session.id ? <Loader2 className="h-3 w-3 animate-spin" /> : "Revoke"}
                                </Button>
                            )}
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}

// ---- Main Settings Page ----
export default function SettingsPage() {
    const { settings } = useNotificationsState();
    const { setSettings, setCategory, setPrivacy, exportHistoryJson, clearAll } = useNotificationsActions();
    const { user, refreshUser } = useAuth();
    const profileHydratedRef = useRef(false);

    const [profileSaving, setProfileSaving] = useState(false);
    const [profileMessage, setProfileMessage] = useState<string | null>(null);
    const [profileError, setProfileError] = useState<string | null>(null);

    const [passwordSaving, setPasswordSaving] = useState(false);
    const [passwordMessage, setPasswordMessage] = useState<string | null>(null);
    const [passwordError, setPasswordError] = useState<string | null>(null);
    const [passwordForm, setPasswordForm] = useState({
        oldPassword: "",
        newPassword: "",
        confirmPassword: "",
    });

    useEffect(() => {
        if (!user) return;
        const name = user.name ?? "";
        const email = user.email ?? "";
        if (
            profileHydratedRef.current &&
            settings.account.profile.name === name &&
            settings.account.profile.email === email
        ) {
            return;
        }
        profileHydratedRef.current = true;
        setSettings({
            account: {
                ...settings.account,
                profile: { name, email },
            },
        });
    }, [setSettings, settings.account, user]);

    const retentionDaysLabel = useMemo(() => {
        const v = settings.historyRetentionDays;
        if (!settings.privacy.storeHistory) return "History disabled";
        if (v <= 0) return "No retention limit";
        return `${v} days`;
    }, [settings.historyRetentionDays, settings.privacy.storeHistory]);

    async function handleSaveProfile() {
        setProfileSaving(true);
        setProfileError(null);
        setProfileMessage(null);
        try {
            const payload = {
                name: settings.account.profile.name.trim(),
            };
            await api.updateMe(payload);
            await refreshUser();
            setProfileMessage("Profile updated.");
        } catch (err) {
            setProfileError(err instanceof Error ? err.message : "Failed to update profile.");
        } finally {
            setProfileSaving(false);
        }
    }

    async function handleChangePassword() {
        setPasswordSaving(true);
        setPasswordError(null);
        setPasswordMessage(null);
        try {
            if (!passwordForm.oldPassword || !passwordForm.newPassword || !passwordForm.confirmPassword) {
                throw new Error("Please fill in all password fields.");
            }
            if (passwordForm.newPassword.length < 8) {
                throw new Error("New password must be at least 8 characters.");
            }
            if (passwordForm.newPassword !== passwordForm.confirmPassword) {
                throw new Error("New password and confirm password do not match.");
            }
            await api.changePassword(passwordForm.oldPassword, passwordForm.newPassword);
            setPasswordForm({ oldPassword: "", newPassword: "", confirmPassword: "" });
            setPasswordMessage("Password changed successfully.");
        } catch (err) {
            setPasswordError(err instanceof Error ? err.message : "Failed to change password.");
        } finally {
            setPasswordSaving(false);
        }
    }

    return (
        <DashboardLayout title="Settings" description="Configure notifications, privacy, integrations, and account.">
            <div className="mx-auto w-full max-w-5xl space-y-6">
                <div className="rounded-2xl border border-border bg-background/70 backdrop-blur-sm p-4 transition-[transform,background-color,box-shadow,border-color] duration-150 ease-out hover:scale-[1.02] hover:bg-gray-50 hover:shadow-md dark:border-white/10 dark:bg-white/5 dark:hover:bg-white/10 dark:hover:shadow-[0_10px_30px_rgba(0,0,0,0.22)]">
                    <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                        <div className="min-w-0">
                            <div className="text-sm font-semibold text-foreground">Quick links</div>
                            <div className="text-sm text-muted-foreground">Jump to related settings pages.</div>
                        </div>
                        <div className="flex flex-wrap gap-2">
                            <Link
                                href="/connectors"
                                className="inline-flex items-center justify-center rounded-xl border border-teal-600 bg-teal-600 px-3 py-2 text-sm font-semibold text-white shadow-sm transition-[transform,background-color,color,border-color,box-shadow] duration-150 ease-out hover:bg-teal-700 hover:border-teal-700 hover:shadow-md hover:scale-[1.02] active:scale-[0.99]"
                            >
                                Connectors
                            </Link>
                        </div>
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
                        <CardTitle className="dark:text-white">Data & Privacy</CardTitle>
                        <CardDescription>Retention, export, clear, and consent.</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-5">
                        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                            <div className="rounded-2xl border border-gray-200 bg-white p-4 space-y-3 shadow-sm transition-[transform,background-color,box-shadow,border-color] duration-150 ease-out hover:-translate-y-0.5 hover:bg-gray-50 hover:shadow-md dark:border-white/10 dark:bg-white/5 dark:hover:bg-white/10 dark:hover:shadow-[0_10px_30px_rgba(0,0,0,0.22)]">
                                <div className="flex items-center justify-between gap-3">
                                    <div>
                                        <div className="text-sm font-semibold text-gray-900 dark:text-zinc-100">Store notification history</div>
                                        <div className="mt-1 text-sm text-muted-foreground">Saved locally in your browser.</div>
                                    </div>
                                    <Switch
                                        checked={settings.privacy.storeHistory}
                                        onCheckedChange={(v) => setPrivacy({ storeHistory: v })}
                                        ariaLabel="Store notification history"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="retentionDays">History retention (days)</Label>
                                    <Input
                                        id="retentionDays"
                                        type="number"
                                        min={1}
                                        max={365}
                                        value={settings.historyRetentionDays}
                                        onChange={(e) =>
                                            setSettings({ historyRetentionDays: clampNumber(Number(e.target.value) || 0, 1, 365) })
                                        }
                                        disabled={!settings.privacy.storeHistory}
                                    />
                                    <div className="text-xs font-semibold text-muted-foreground">{retentionDaysLabel}</div>
                                </div>
                            </div>

                            <div className="rounded-2xl border border-gray-200 bg-white p-4 space-y-3 shadow-sm transition-[transform,background-color,box-shadow,border-color] duration-150 ease-out hover:-translate-y-0.5 hover:bg-gray-50 hover:shadow-md dark:border-white/10 dark:bg-white/5 dark:hover:bg-white/10 dark:hover:shadow-[0_10px_30px_rgba(0,0,0,0.22)]">
                                <div className="flex items-center justify-between gap-3">
                                    <div>
                                        <div className="text-sm font-semibold text-gray-900 dark:text-zinc-100">Third-party consent</div>
                                        <div className="mt-1 text-sm text-muted-foreground">Allows sending notifications to integrations.</div>
                                    </div>
                                    <Switch
                                        checked={settings.privacy.consentThirdParty}
                                        onCheckedChange={(v) => setPrivacy({ consentThirdParty: v })}
                                        ariaLabel="Consent to third-party integrations"
                                    />
                                </div>
                                <div className="flex flex-wrap gap-2">
                                    <Button
                                        type="button"
                                        variant="outline"
                                        className="border-teal-600 bg-teal-600 text-white hover:bg-teal-700 hover:border-teal-700 hover:text-white"
                                        onClick={() => {
                                            const json = exportHistoryJson();
                                            const filename = `talklee-notifications-${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
                                            downloadTextFile(filename, json);
                                        }}
                                    >
                                        <Download className="h-4 w-4" />
                                        Export data
                                    </Button>
                                    <Button
                                        type="button"
                                        variant="destructive"
                                        onClick={clearAll}
                                    >
                                        <Trash2 className="h-4 w-4" />
                                        Clear history
                                    </Button>
                                </div>
                            </div>
                        </div>
                    </CardContent>
                </Card>

                <Card className="dark:border-white/10 dark:bg-white/5 dark:text-white dark:hover:bg-white/10 dark:hover:shadow-[0_10px_30px_rgba(0,0,0,0.22)]">
                    <CardHeader>
                        <CardTitle className="dark:text-white">Integrations</CardTitle>
                        <CardDescription>Webhook setup and routing rules.</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="rounded-2xl border border-gray-200 bg-white p-4 space-y-4 shadow-sm transition-[transform,background-color,box-shadow,border-color] duration-150 ease-out hover:-translate-y-0.5 hover:bg-gray-50 hover:shadow-md dark:border-white/10 dark:bg-white/5 dark:hover:bg-white/10 dark:hover:shadow-[0_10px_30px_rgba(0,0,0,0.22)]">
                            <div className="flex items-center justify-between gap-3">
                                <div>
                                    <div className="text-sm font-semibold text-gray-900 dark:text-zinc-100">Webhook</div>
                                    <div className="mt-1 text-sm text-muted-foreground">Sends JSON payloads to your endpoint.</div>
                                </div>
                                <Switch
                                    checked={settings.integrations.webhook.enabled}
                                    onCheckedChange={(v) => setSettings({ integrations: { ...settings.integrations, webhook: { ...settings.integrations.webhook, enabled: v } } })}
                                    ariaLabel="Enable webhook integration"
                                    disabled={!settings.privacy.consentThirdParty}
                                />
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="webhookUrl">Webhook URL</Label>
                                <Input
                                    id="webhookUrl"
                                    type="url"
                                    value={settings.integrations.webhook.url}
                                    onChange={(e) =>
                                        setSettings({
                                            integrations: {
                                                ...settings.integrations,
                                                webhook: { ...settings.integrations.webhook, url: e.target.value },
                                            },
                                        })
                                    }
                                    disabled={!settings.privacy.consentThirdParty || !settings.integrations.webhook.enabled}
                                />
                            </div>
                        </div>

                        <div className="rounded-2xl border border-gray-200 bg-white p-4 space-y-3 shadow-sm transition-[transform,background-color,box-shadow,border-color] duration-150 ease-out hover:-translate-y-0.5 hover:bg-gray-50 hover:shadow-md dark:border-white/10 dark:bg-white/5 dark:hover:bg-white/10 dark:hover:shadow-[0_10px_30px_rgba(0,0,0,0.22)]">
                            <div className="text-sm font-semibold text-gray-900 dark:text-zinc-100">Routing rules</div>
                            <div className="text-sm text-muted-foreground">
                                Webhook routing requires third-party consent and an enabled webhook URL.
                            </div>
                        </div>
                    </CardContent>
                </Card>

                <Card className="dark:border-white/10 dark:bg-white/5 dark:text-white dark:hover:bg-white/10 dark:hover:shadow-[0_10px_30px_rgba(0,0,0,0.22)]">
                    <CardHeader>
                        <CardTitle className="dark:text-white">Account</CardTitle>
                        <CardDescription>Profile, authentication, and account linking.</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-5">
                        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                            <div className="rounded-2xl border border-gray-200 bg-white p-4 space-y-3 shadow-sm transition-[transform,background-color,box-shadow,border-color] duration-150 ease-out hover:-translate-y-0.5 hover:bg-gray-50 hover:shadow-md dark:border-white/10 dark:bg-white/5 dark:hover:bg-white/10 dark:hover:shadow-[0_10px_30px_rgba(0,0,0,0.22)]">
                                <div className="text-sm font-semibold text-gray-900 dark:text-zinc-100">Profile</div>
                                <div className="space-y-2">
                                    <Label htmlFor="profileName">Name</Label>
                                    <Input
                                        id="profileName"
                                        value={settings.account.profile.name}
                                        onChange={(e) =>
                                            setSettings({
                                                account: {
                                                    ...settings.account,
                                                    profile: { ...settings.account.profile, name: e.target.value },
                                                },
                                            })
                                        }
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="profileEmail">Email</Label>
                                    <Input
                                        id="profileEmail"
                                        type="email"
                                        value={settings.account.profile.email}
                                        readOnly
                                    />
                                    <p className="text-xs text-muted-foreground">Email is managed from your account and cannot be edited here.</p>
                                </div>
                                {profileError ? <div className="text-xs text-red-500">{profileError}</div> : null}
                                {profileMessage ? <div className="text-xs text-emerald-600 dark:text-emerald-400">{profileMessage}</div> : null}
                                <div className="pt-1">
                                    <Button
                                        type="button"
                                        onClick={handleSaveProfile}
                                        disabled={profileSaving}
                                        className="border-teal-600 bg-teal-600 text-white hover:bg-teal-700 hover:border-teal-700 hover:text-white"
                                    >
                                        {profileSaving ? "Saving..." : "Save profile"}
                                    </Button>
                                </div>
                            </div>

                            <div className="rounded-2xl border border-gray-200 bg-white p-4 space-y-4 shadow-sm transition-[transform,background-color,box-shadow,border-color] duration-150 ease-out hover:-translate-y-0.5 hover:bg-gray-50 hover:shadow-md dark:border-white/10 dark:bg-white/5 dark:hover:bg-white/10 dark:hover:shadow-[0_10px_30px_rgba(0,0,0,0.22)]">
                                <div className="space-y-2">
                                    <Label htmlFor="oldPassword">Current password</Label>
                                    <Input
                                        id="oldPassword"
                                        type="password"
                                        value={passwordForm.oldPassword}
                                        onChange={(e) =>
                                            setPasswordForm((prev) => ({ ...prev, oldPassword: e.target.value }))
                                        }
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="newPassword">New password</Label>
                                    <Input
                                        id="newPassword"
                                        type="password"
                                        value={passwordForm.newPassword}
                                        onChange={(e) =>
                                            setPasswordForm((prev) => ({ ...prev, newPassword: e.target.value }))
                                        }
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="confirmPassword">Confirm new password</Label>
                                    <Input
                                        id="confirmPassword"
                                        type="password"
                                        value={passwordForm.confirmPassword}
                                        onChange={(e) =>
                                            setPasswordForm((prev) => ({ ...prev, confirmPassword: e.target.value }))
                                        }
                                    />
                                </div>
                                {passwordError ? <div className="text-xs text-red-500">{passwordError}</div> : null}
                                {passwordMessage ? <div className="text-xs text-emerald-600 dark:text-emerald-400">{passwordMessage}</div> : null}
                                <div>
                                    <Button
                                        type="button"
                                        onClick={handleChangePassword}
                                        disabled={passwordSaving}
                                        className="border-teal-600 bg-teal-600 text-white hover:bg-teal-700 hover:border-teal-700 hover:text-white"
                                    >
                                        {passwordSaving ? "Updating..." : "Change password"}
                                    </Button>
                                </div>

                                <div>
                                    <div className="text-sm font-semibold text-gray-900 dark:text-zinc-100">Account linking</div>
                                    <div className="mt-2 space-y-2">
                                        <div className="flex items-center justify-between gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-sm transition-[transform,background-color,box-shadow,border-color] duration-150 ease-out hover:-translate-y-0.5 hover:bg-gray-50 hover:shadow-md dark:border-white/10 dark:bg-white/5 dark:hover:bg-white/10 dark:hover:shadow-[0_10px_30px_rgba(0,0,0,0.22)]">
                                            <div className="text-sm font-semibold text-gray-900 dark:text-zinc-100">Google</div>
                                            <Switch
                                                checked={settings.account.linking.google}
                                                onCheckedChange={(v) =>
                                                    setSettings({
                                                        account: {
                                                            ...settings.account,
                                                            linking: { ...settings.account.linking, google: v },
                                                        },
                                                    })
                                                }
                                                ariaLabel="Link Google account"
                                            />
                                        </div>
                                        <div className="flex items-center justify-between gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-sm transition-[transform,background-color,box-shadow,border-color] duration-150 ease-out hover:-translate-y-0.5 hover:bg-gray-50 hover:shadow-md dark:border-white/10 dark:bg-white/5 dark:hover:bg-white/10 dark:hover:shadow-[0_10px_30px_rgba(0,0,0,0.22)]">
                                            <div className="text-sm font-semibold text-gray-900 dark:text-zinc-100">GitHub</div>
                                            <Switch
                                                checked={settings.account.linking.github}
                                                onCheckedChange={(v) =>
                                                    setSettings({
                                                        account: {
                                                            ...settings.account,
                                                            linking: { ...settings.account.linking, github: v },
                                                        },
                                                    })
                                                }
                                                ariaLabel="Link GitHub account"
                                            />
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </CardContent>
                </Card>

                {/* Security Section â€” MFA, Passkeys, Sessions */}
                <Card className="dark:border-white/10 dark:bg-white/5 dark:text-white dark:hover:bg-white/10 dark:hover:shadow-[0_10px_30px_rgba(0,0,0,0.22)]">
                    <CardHeader>
                        <CardTitle className="dark:text-white">Security</CardTitle>
                        <CardDescription>Two-factor authentication, passkeys, and active sessions.</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-6">
                        <div className="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm dark:border-white/10 dark:bg-white/5">
                            <MfaSetupSection />
                        </div>

                        <div className="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm dark:border-white/10 dark:bg-white/5">
                            <PasskeysSection />
                        </div>

                        <div className="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm dark:border-white/10 dark:bg-white/5">
                            <ActiveSessionsSection />
                        </div>
                    </CardContent>
                </Card>
            </div>
        </DashboardLayout>
    );
}

