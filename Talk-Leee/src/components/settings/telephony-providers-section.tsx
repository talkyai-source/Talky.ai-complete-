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
    type ProviderAvailability,
    type TestResult,
} from "@/lib/telephony-api";
import { useNotificationsActions } from "@/lib/notifications-client";
import { SipTrunksList } from "@/components/settings/sip-trunks-list";

const UNVERIFIED_AVAILABILITY: ProviderAvailability = {
    activation_allowed: false, qualification_only: false,
    reason: "Availability could not be verified. Refresh settings before activating.",
};

function checkLabel(check: TestResult): string {
    if (!check.ok) return "Check failed";
    if (check.check_scope === "provider_account") return "Provider account verified";
    if (check.check_scope === "sdk_initialization") return "Local configuration checked; provider was not contacted";
    return "Previous check passed; scope not recorded";
}

function ActiveBanner({ active, availability }: { active: ActiveProvider; availability?: ProviderAvailability }) {
    if (active === "twilio" || active === "vonage") {
        const state = availability ?? UNVERIFIED_AVAILABILITY;
        const name = active === "twilio" ? "Twilio" : "Vonage";
        return <div role="status" className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-700 dark:text-amber-400">
            <div className="font-medium">{state.activation_allowed ? `${name} selected for nonproduction qualification` : `${name} selection needs attention`}</div>
            <div className="mt-1 text-xs">{state.reason}</div>
        </div>;
    }
    const map: Record<"sip" | "none", { label: string; className: string }> = {
        sip: {
            label: "Local PBX (SIP) is selected",
            className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
        },
        none: {
            label: "No tenant telephony provider selected. Call readiness is checked before dialing.",
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
    availability = UNVERIFIED_AVAILABILITY,
}: {
    provider: TelephonyProvider;
    iconLabel: string;
    description: string;
    existing?: ProviderRow;
    active: ActiveProvider;
    availability?: ProviderAvailability;
}) {
    const { create: createNotification } = useNotificationsActions();
    const def = provider === "twilio" ? TWILIO_FORM : VONAGE_FORM;
    const [creds, setCreds] = useState<Record<string, string>>({ ...def.initial });
    const [fromNumber, setFromNumber] = useState<string>(existing?.from_number ?? "");
    const [showSaved, setShowSaved] = useState<boolean>(Boolean(existing?.has_credentials));

    useEffect(() => {
        // Re-sync local editable state whenever the server-backed `existing`
        // row changes (e.g. after a save/delete refetch) — these are
        // editable fields, not values that can be computed during render.
        // eslint-disable-next-line react-hooks/set-state-in-effect -- resyncs local editable fields when the server-backed provider row changes
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
            createNotification({
                type: "success",
                title: `${iconLabel} credentials saved`,
                message: "Saved securely. Configuration checks do not verify call readiness.",
            });
            setCreds({ ...def.initial });
            setShowSaved(true);
        } catch (e: unknown) {
            createNotification({
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
                createNotification({
                    type: "success",
                    title: `${iconLabel}: ${checkLabel(r)}`,
                    message: `This check does not verify call readiness.${r.account_status ? ` Account status: ${r.account_status}.` : ""}`,
                });
            } else {
                createNotification({
                    type: "error",
                    title: `${iconLabel} test failed`,
                    message: r.error || "Provider rejected the credentials",
                });
            }
        } catch (e: unknown) {
            createNotification({
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
            createNotification({
                type: "success",
                title: `${iconLabel} disconnected`,
                message: "Credentials removed.",
            });
            setShowSaved(false);
            setFromNumber("");
        } catch (e: unknown) {
            createNotification({
                type: "error",
                title: "Delete failed",
                message: e instanceof Error ? e.message : "Unknown error",
            });
        }
    }

    async function handleActivate() {
        if (!availability.activation_allowed) return;
        try {
            await activateMutation.mutateAsync(provider);
            createNotification({
                type: "success",
                title: `${iconLabel} selection saved`,
                message: "Nonproduction qualification only. Outbound campaign routing has not been verified.",
            });
        } catch (e: unknown) {
            createNotification({
                type: "error",
                title: "Activate failed",
                message: e instanceof Error ? e.message : "Unknown error",
            });
        }
    }

    return (
        <Card className={`flex flex-col ${isActive ? "ring-2 ring-amber-500/50" : ""}`}>
            <CardHeader>
                <div className="flex items-center justify-between">
                    <CardTitle className="flex items-center gap-2">
                        <Phone className="h-5 w-5" aria-hidden /> {iconLabel}
                    </CardTitle>
                    {isActive && (
                        <span className="inline-flex items-center rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-xs font-semibold text-amber-700 dark:text-amber-400">
                            {availability.activation_allowed ? "Qualification only" : "Needs attention"}
                        </span>
                    )}
                </div>
                <CardDescription>{description}</CardDescription>
            </CardHeader>
            <CardContent className="flex flex-1 flex-col gap-3">
                <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-700 dark:text-amber-400">{availability.reason}</div>
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
                                <CheckCircle2 className="h-3 w-3" aria-hidden /> {checkLabel(lastTest)}
                                {lastTest.latency_ms !== undefined ? ` · ${lastTest.latency_ms} ms` : ""}
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
                        <div className="mt-1">This check does not verify call readiness.</div>
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
                            disabled={activateMutation.isPending || existing?.status !== "active" || !availability.activation_allowed}
                            variant="outline"
                            size="sm"
                            title={!availability.activation_allowed ? availability.reason : existing?.status !== "active" ? "Run a successful configuration check first" : "Select for nonproduction qualification"}
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
    const { create: createNotification } = useNotificationsActions();
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

    const data = query.data ?? { active: "none" as ActiveProvider, providers: [], availability: {} };
    const twilio = data.providers.find((p) => p.provider === "twilio");
    const vonage = data.providers.find((p) => p.provider === "vonage");

    async function handleDeactivate() {
        try {
            await activateMutation.mutateAsync("none");
            createNotification({
                type: "success",
                title: "Provider selection cleared",
                message: "Call readiness is still checked before dialing.",
            });
        } catch (e: unknown) {
            createNotification({
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
                    <ActiveBanner active={data.active} availability={data.active === "twilio" || data.active === "vonage" ? data.availability?.[data.active] : undefined} />
                </div>
                {data.active !== "none" && (
                    <Button onClick={handleDeactivate} variant="outline" size="sm">
                        <PowerOff className="mr-1 h-3 w-3" aria-hidden /> Clear provider selection
                    </Button>
                )}
            </div>

            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                <ProviderCard
                    provider="twilio"
                    iconLabel="Twilio"
                    description="Twilio Programmable Voice. Paste your Account SID + Auth Token from console.twilio.com → Account Info."
                    existing={twilio}
                    availability={data.availability?.twilio}
                    active={data.active}
                />
                <ProviderCard
                    provider="vonage"
                    iconLabel="Vonage"
                    description="Vonage Voice API. Requires API key/secret plus an Application with private key for voice."
                    existing={vonage}
                    availability={data.availability?.vonage}
                    active={data.active}
                />
            </div>

            <SipTrunksList />

            {data.active === "sip" && (
                <div role="note" className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-700 dark:text-emerald-400">
                    Local SIP is selected. Each call must pass readiness checks for its assigned route before dialing.
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
