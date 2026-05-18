"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    AlertCircle,
    CheckCircle2,
    Loader2,
    Phone,
    Power,
    PowerOff,
    Trash2,
    XCircle,
    Zap,
} from "lucide-react";
import {
    useTelephonyProviders,
    useSaveTelephonyProvider,
    useDeleteTelephonyProvider,
    useTestTelephonyProvider,
    useActivateTelephonyProvider,
    type ActiveProvider,
    type ProviderRow,
    type TelephonyProvider,
} from "@/lib/telephony-api";
import { notificationsStore } from "@/lib/notifications";
import { SipTrunksList } from "@/components/settings/sip-trunks-list";

function ActiveBanner({ active }: { active: ActiveProvider }) {
    const map: Record<ActiveProvider, { label: string; className: string }> = {
        twilio: {
            label: "Twilio is the active provider",
            className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
        },
        vonage: {
            label: "Vonage is the active provider",
            className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
        },
        sip: {
            label: "Local PBX (SIP) is the active provider",
            className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
        },
        none: {
            label: "No active telephony provider — outbound calls will use the platform default",
            className: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
        },
    };
    const b = map[active];
    return (
        <div role="status" className={`flex items-center gap-2 rounded-xl border px-4 py-3 text-sm font-medium ${b.className}`}>
            <Zap className="h-5 w-5 flex-shrink-0" aria-hidden /> {b.label}
        </div>
    );
}

interface CredentialFormDefn<T extends Record<string, string>> {
    initial: T;
    fields: Array<{ key: keyof T & string; label: string; type?: string; placeholder?: string; multiline?: boolean }>;
}

const TWILIO_FORM: CredentialFormDefn<{ account_sid: string; auth_token: string }> = {
    initial: { account_sid: "", auth_token: "" },
    fields: [
        { key: "account_sid", label: "Account SID", placeholder: "AC********************************" },
        { key: "auth_token", label: "Auth token", type: "password", placeholder: "********************************" },
    ],
};

const VONAGE_FORM: CredentialFormDefn<{ api_key: string; api_secret: string; app_id: string; private_key: string }> = {
    initial: { api_key: "", api_secret: "", app_id: "", private_key: "" },
    fields: [
        { key: "api_key", label: "API key" },
        { key: "api_secret", label: "API secret", type: "password" },
        { key: "app_id", label: "Application ID" },
        { key: "private_key", label: "Private key (PEM)", multiline: true, placeholder: "-----BEGIN PRIVATE KEY-----\n..." },
    ],
};

function ProviderCard({
    provider,
    iconLabel,
    description,
    existing,
    active,
}: {
    provider: TelephonyProvider;
    iconLabel: string;
    description: string;
    existing?: ProviderRow;
    active: ActiveProvider;
}) {
    const def = provider === "twilio" ? TWILIO_FORM : VONAGE_FORM;
    const [creds, setCreds] = useState<Record<string, string>>({ ...def.initial });
    const [fromNumber, setFromNumber] = useState<string>(existing?.from_number ?? "");
    const [showSaved, setShowSaved] = useState<boolean>(Boolean(existing?.has_credentials));

    useEffect(() => {
        setShowSaved(Boolean(existing?.has_credentials));
        setFromNumber(existing?.from_number ?? "");
    }, [existing?.has_credentials, existing?.from_number]);

    const saveMutation = useSaveTelephonyProvider();
    const testMutation = useTestTelephonyProvider();
    const deleteMutation = useDeleteTelephonyProvider();
    const activateMutation = useActivateTelephonyProvider();

    const isActive = active === provider;
    const lastTest = existing?.last_test_result || null;

    async function handleSave() {
        try {
            await saveMutation.mutateAsync({
                provider,
                credentials: creds as never,
                from_number: fromNumber || undefined,
            });
            notificationsStore.create({
                type: "success",
                title: `${iconLabel} credentials saved`,
                message: "Click Test to verify them with the provider.",
            });
            setCreds({ ...def.initial });
            setShowSaved(true);
        } catch (e: unknown) {
            notificationsStore.create({
                type: "error",
                title: "Save failed",
                message: e instanceof Error ? e.message : "Unknown error",
            });
        }
    }

    async function handleTest() {
        try {
            const r = await testMutation.mutateAsync(provider);
            if (r.ok) {
                notificationsStore.create({
                    type: "success",
                    title: `${iconLabel} credentials OK`,
                    message: `${r.latency_ms ?? 0} ms${r.account_status ? ` · status ${r.account_status}` : ""}`,
                });
            } else {
                notificationsStore.create({
                    type: "error",
                    title: `${iconLabel} test failed`,
                    message: r.error || "Provider rejected the credentials",
                });
            }
        } catch (e: unknown) {
            notificationsStore.create({
                type: "error",
                title: `${iconLabel} test failed`,
                message: e instanceof Error ? e.message : "Unknown error",
            });
        }
    }

    async function handleDelete() {
        if (!confirm(`Forget ${iconLabel} credentials for this tenant?`)) return;
        try {
            await deleteMutation.mutateAsync(provider);
            notificationsStore.create({
                type: "success",
                title: `${iconLabel} disconnected`,
                message: "Credentials removed.",
            });
            setShowSaved(false);
            setFromNumber("");
        } catch (e: unknown) {
            notificationsStore.create({
                type: "error",
                title: "Delete failed",
                message: e instanceof Error ? e.message : "Unknown error",
            });
        }
    }

    async function handleActivate() {
        try {
            await activateMutation.mutateAsync(provider);
            notificationsStore.create({
                type: "success",
                title: `${iconLabel} is now active`,
                message: "Outbound calls will route through this provider.",
            });
        } catch (e: unknown) {
            notificationsStore.create({
                type: "error",
                title: "Activate failed",
                message: e instanceof Error ? e.message : "Unknown error",
            });
        }
    }

    return (
        <Card className={`flex flex-col ${isActive ? "ring-2 ring-emerald-500/50" : ""}`}>
            <CardHeader>
                <div className="flex items-center justify-between">
                    <CardTitle className="flex items-center gap-2">
                        <Phone className="h-5 w-5" aria-hidden /> {iconLabel}
                    </CardTitle>
                    {isActive && (
                        <span className="inline-flex items-center rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-xs font-semibold text-emerald-700 dark:text-emerald-400">
                            Active
                        </span>
                    )}
                </div>
                <CardDescription>{description}</CardDescription>
            </CardHeader>
            <CardContent className="flex flex-1 flex-col gap-3">
                {showSaved ? (
                    <div className="rounded-md border border-border bg-muted/30 p-3 text-xs text-muted-foreground">
                        Credentials are saved (encrypted). Re-enter to overwrite.
                    </div>
                ) : null}

                {def.fields.map((f) => (
                    <div key={f.key}>
                        <Label htmlFor={`${provider}-${f.key}`}>{f.label}</Label>
                        {f.multiline ? (
                            <textarea
                                id={`${provider}-${f.key}`}
                                className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-sm font-mono"
                                rows={5}
                                value={creds[f.key] || ""}
                                onChange={(e) => setCreds({ ...creds, [f.key]: e.target.value })}
                                placeholder={f.placeholder}
                            />
                        ) : (
                            <Input
                                id={`${provider}-${f.key}`}
                                type={f.type || "text"}
                                value={creds[f.key] || ""}
                                onChange={(e) => setCreds({ ...creds, [f.key]: e.target.value })}
                                placeholder={f.placeholder}
                            />
                        )}
                    </div>
                ))}

                <div>
                    <Label htmlFor={`${provider}-from-number`}>Caller ID / From number</Label>
                    <Input
                        id={`${provider}-from-number`}
                        value={fromNumber}
                        onChange={(e) => setFromNumber(e.target.value)}
                        placeholder="+15551234567"
                    />
                </div>

                {lastTest && (
                    <div
                        className={`rounded-md border px-3 py-2 text-xs ${lastTest.ok
                            ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                            : "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400"
                            }`}
                    >
                        {lastTest.ok ? (
                            <span className="inline-flex items-center gap-2">
                                <CheckCircle2 className="h-3 w-3" aria-hidden /> Last test OK · {lastTest.latency_ms ?? 0} ms
                                {lastTest.account_status ? ` · ${lastTest.account_status}` : ""}
                            </span>
                        ) : (
                            <span className="inline-flex items-center gap-2">
                                <XCircle className="h-3 w-3" aria-hidden /> Last test failed: {lastTest.error}
                            </span>
                        )}
                        {existing?.last_tested_at && (
                            <div className="mt-1 text-[10px] opacity-70">
                                {new Date(existing.last_tested_at).toLocaleString()}
                            </div>
                        )}
                    </div>
                )}

                <div className="mt-auto flex flex-wrap gap-2 pt-2">
                    <Button
                        onClick={handleSave}
                        disabled={saveMutation.isPending || Object.values(creds).every((v) => !v.trim())}
                        size="sm"
                    >
                        {saveMutation.isPending ? (
                            <><Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden /> Saving</>
                        ) : (
                            "Save"
                        )}
                    </Button>
                    <Button
                        onClick={handleTest}
                        disabled={!showSaved || testMutation.isPending}
                        variant="outline"
                        size="sm"
                    >
                        {testMutation.isPending ? (
                            <><Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden /> Testing</>
                        ) : (
                            "Test"
                        )}
                    </Button>
                    {showSaved && !isActive && (
                        <Button
                            onClick={handleActivate}
                            disabled={activateMutation.isPending || existing?.status !== "active"}
                            variant="outline"
                            size="sm"
                            title={existing?.status !== "active" ? "Test successfully before activating" : "Make active"}
                        >
                            <Power className="mr-1 h-3 w-3" aria-hidden /> Make active
                        </Button>
                    )}
                    {showSaved && (
                        <Button onClick={handleDelete} disabled={deleteMutation.isPending} variant="ghost" size="sm">
                            <Trash2 className="h-3 w-3" aria-hidden />
                        </Button>
                    )}
                </div>
            </CardContent>
        </Card>
    );
}

export function TelephonyProvidersSection() {
    const query = useTelephonyProviders();
    const activateMutation = useActivateTelephonyProvider();

    if (query.isLoading) {
        return (
            <div className="flex items-center justify-center py-12 text-muted-foreground">
                <Loader2 className="mr-2 h-5 w-5 animate-spin" aria-hidden /> Loading telephony settings…
            </div>
        );
    }

    if (query.error) {
        return (
            <Card>
                <CardContent className="py-10 text-center">
                    <AlertCircle className="mx-auto h-6 w-6 text-red-500" aria-hidden />
                    <div className="mt-2 text-sm font-semibold text-foreground">Failed to load telephony settings</div>
                    <div className="mt-1 text-xs text-muted-foreground">
                        {query.error instanceof Error ? query.error.message : String(query.error)}
                    </div>
                </CardContent>
            </Card>
        );
    }

    const data = query.data ?? { active: "none" as ActiveProvider, providers: [] };
    const twilio = data.providers.find((p) => p.provider === "twilio");
    const vonage = data.providers.find((p) => p.provider === "vonage");

    async function handleDeactivate() {
        try {
            await activateMutation.mutateAsync("none");
            notificationsStore.create({
                type: "success",
                title: "Telephony provider deactivated",
                message: "Outbound calls fall back to the platform default.",
            });
        } catch (e: unknown) {
            notificationsStore.create({
                type: "error",
                title: "Deactivate failed",
                message: e instanceof Error ? e.message : "Unknown error",
            });
        }
    }

    return (
        <div className="space-y-6">
            <div className="flex flex-wrap items-center gap-3">
                <div className="flex-1 min-w-[260px]">
                    <ActiveBanner active={data.active} />
                </div>
                {data.active !== "none" && (
                    <Button onClick={handleDeactivate} variant="outline" size="sm">
                        <PowerOff className="mr-1 h-3 w-3" aria-hidden /> Disable telephony
                    </Button>
                )}
            </div>

            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                <ProviderCard
                    provider="twilio"
                    iconLabel="Twilio"
                    description="Twilio Programmable Voice. Paste your Account SID + Auth Token from console.twilio.com → Account Info."
                    existing={twilio}
                    active={data.active}
                />
                <ProviderCard
                    provider="vonage"
                    iconLabel="Vonage"
                    description="Vonage Voice API. Requires API key/secret plus an Application with private key for voice."
                    existing={vonage}
                    active={data.active}
                />
            </div>

            <SipTrunksList />

            {data.active === "sip" && (
                <div role="note" className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-700 dark:text-emerald-400">
                    A local SIP trunk is the active provider. Calls dial out through whichever trunk is currently
                    marked active in the table above.
                </div>
            )}

            {data.active !== "sip" && (
                <div className="flex justify-end">
                    <Button
                        onClick={() => activateMutation.mutate("sip")}
                        variant="outline"
                        size="sm"
                        disabled={activateMutation.isPending}
                    >
                        <Power className="mr-1 h-3 w-3" aria-hidden /> Use local SIP trunk as active provider
                    </Button>
                </div>
            )}
        </div>
    );
}
