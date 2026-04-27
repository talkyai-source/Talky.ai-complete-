"use client";

import { useMemo, useState, useEffect } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Select } from "@/components/ui/select";
import { useNotificationsActions, useNotificationsState } from "@/lib/notifications-client";
import type { NotificationPriority, NotificationRouting, NotificationType } from "@/lib/notifications";
import { Download, Trash2, Key, Lock } from "lucide-react";
import Link from "next/link";
import MFASetup from "@/components/auth/mfa-setup";
import PasskeyRegistration from "@/components/auth/passkey-registration";
import DeviceList from "@/components/auth/device-list";
import LogoutButton from "@/components/auth/logout-button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

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

export default function SettingsPage() {
    const { settings } = useNotificationsState();
    const { setSettings, setCategory, setPrivacy, exportHistoryJson, clearAll } = useNotificationsActions();
    const [token, setToken] = useState("");
    const [showMfaSetup, setShowMfaSetup] = useState(false);
    const [showPasskeySetup, setShowPasskeySetup] = useState(false);
    const [mfaEnabled, setMfaEnabled] = useState(false);

    // Get token from localStorage
    useEffect(() => {
        const savedToken = localStorage.getItem("access_token") || "";
        setToken(savedToken);
    }, []);

    const retentionDaysLabel = useMemo(() => {
        const v = settings.historyRetentionDays;
        if (!settings.privacy.storeHistory) return "History disabled";
        if (v <= 0) return "No retention limit";
        return `${v} days`;
    }, [settings.historyRetentionDays, settings.privacy.storeHistory]);

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
                                href="/settings/connectors"
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
                        <CardTitle className="dark:text-white">Account & Security</CardTitle>
                        <CardDescription>Profile, authentication, security, and device management.</CardDescription>
                    </CardHeader>
                    <CardContent>
                        <Tabs defaultValue="profile" className="w-full">
                            <TabsList className="grid w-full grid-cols-4">
                                <TabsTrigger value="profile">Profile</TabsTrigger>
                                <TabsTrigger value="security">Security</TabsTrigger>
                                <TabsTrigger value="devices">Devices</TabsTrigger>
                                <TabsTrigger value="logout">Logout</TabsTrigger>
                            </TabsList>

                            {/* Profile Tab */}
                            <TabsContent value="profile" className="space-y-4">
                                <div className="rounded-2xl border border-gray-200 bg-white p-4 space-y-4 shadow-sm transition-[transform,background-color,box-shadow,border-color] duration-150 ease-out hover:-translate-y-0.5 hover:bg-gray-50 hover:shadow-md dark:border-white/10 dark:bg-white/5 dark:hover:bg-white/10 dark:hover:shadow-[0_10px_30px_rgba(0,0,0,0.22)]">
                                    <div className="text-sm font-semibold text-gray-900 dark:text-zinc-100">Profile Information</div>
                                    <div className="space-y-4">
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
                                                onChange={(e) =>
                                                    setSettings({
                                                        account: {
                                                            ...settings.account,
                                                            profile: { ...settings.account.profile, email: e.target.value },
                                                        },
                                                    })
                                                }
                                            />
                                        </div>
                                    </div>
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
                                    {!showMfaSetup && !mfaEnabled ? (
                                        <Button onClick={() => setShowMfaSetup(true)} variant="outline" className="w-full">
                                            Enable Two-Factor Authentication
                                        </Button>
                                    ) : null}
                                    {showMfaSetup && !mfaEnabled && token && (
                                        <MFASetup
                                            token={token}
                                            onSuccess={() => {
                                                setMfaEnabled(true);
                                                setShowMfaSetup(false);
                                            }}
                                            onCancel={() => setShowMfaSetup(false)}
                                        />
                                    )}
                                    {mfaEnabled && (
                                        <div className="p-3 bg-green-50 dark:bg-green-950/30 border border-green-200 dark:border-green-800 rounded-md">
                                            <p className="text-sm text-green-700 dark:text-green-300">✓ Two-factor authentication is enabled</p>
                                        </div>
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
