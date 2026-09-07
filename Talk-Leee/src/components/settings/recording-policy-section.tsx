"use client";

import { useEffect, useMemo, useState } from "react";
import { Loader2, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { sharedHttpClient } from "@/lib/api";
import { isApiClientError } from "@/lib/http-client";
import { notificationsStore } from "@/lib/notifications";
import { cn } from "@/lib/utils";

export type RecordingConsentMode = "disabled" | "one_party" | "two_party";

export type RecordingPolicy = {
    configured: boolean;
    default_consent_mode?: RecordingConsentMode | null;
    announcement_text?: string | null;
    opt_out_dtmf_digit?: string | null;
    two_party_country_codes?: string[];
    retention_days?: number | null;
    updated_at?: string | null;
    effect: string;
};

export type RecordingPolicyInput = {
    default_consent_mode: RecordingConsentMode;
    announcement_text?: string | null;
    opt_out_dtmf_digit?: string | null;
    two_party_country_codes: string[];
    retention_days: number;
};

function formatError(err: unknown) {
    if (isApiClientError(err)) return err.message;
    return err instanceof Error ? err.message : "Request failed";
}

export async function fetchRecordingPolicy(signal?: AbortSignal): Promise<RecordingPolicy> {
    return (await sharedHttpClient().request({ path: "/recordings/policy", timeoutMs: 12_000, signal })) as RecordingPolicy;
}

export async function saveRecordingPolicy(input: RecordingPolicyInput): Promise<RecordingPolicy> {
    return (await sharedHttpClient().request({ path: "/recordings/policy", method: "PUT", body: input, timeoutMs: 12_000 })) as RecordingPolicy;
}

const MODE_LABELS: Record<RecordingConsentMode, string> = {
    disabled: "Do not record calls",
    one_party: "Record without a spoken notice (one-party consent)",
    two_party: "Record with a spoken notice (two-party consent)",
};

export function RecordingPolicyView({
    policy,
    isLoading,
    error,
    canEdit,
    onSave,
    saving,
}: {
    policy: RecordingPolicy | undefined;
    isLoading?: boolean;
    error?: string | null;
    canEdit: boolean;
    onSave: (input: RecordingPolicyInput) => Promise<unknown>;
    saving?: boolean;
}) {
    const [draft, setDraft] = useState<Partial<RecordingPolicyInput>>({});
    const [saveError, setSaveError] = useState<string | null>(null);

    const effective: RecordingPolicyInput = {
        default_consent_mode: policy?.default_consent_mode ?? "disabled",
        announcement_text: policy?.announcement_text ?? "",
        opt_out_dtmf_digit: policy?.opt_out_dtmf_digit ?? "",
        two_party_country_codes: policy?.two_party_country_codes ?? [],
        retention_days: policy?.retention_days ?? 90,
        ...draft,
    };
    const codesText = useMemo(() => effective.two_party_country_codes.join(", "), [effective.two_party_country_codes]);
    const dirty = Object.keys(draft).length > 0;

    const save = async () => {
        setSaveError(null);
        try {
            await onSave({
                default_consent_mode: effective.default_consent_mode,
                announcement_text: (effective.announcement_text ?? "").trim() || null,
                opt_out_dtmf_digit: (effective.opt_out_dtmf_digit ?? "").trim() || null,
                two_party_country_codes: effective.two_party_country_codes,
                retention_days: effective.retention_days,
            });
            setDraft({});
        } catch (err) {
            setSaveError(formatError(err));
        }
    };

    return (
        <Card data-testid="recording-policy">
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <ShieldCheck className="h-5 w-5" aria-hidden /> Call recording policy
                </CardTitle>
                <CardDescription>
                    Recording is off until a policy is saved. This is a legal statement about the jurisdictions you call, so only tenant admins can change it.
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
                {error ? <div className="rounded-2xl border border-red-500/30 bg-background/70 p-4 text-sm text-red-500">{error}</div> : null}
                {isLoading && !policy ? <div className="h-4 w-1/2 animate-pulse rounded bg-foreground/10" /> : null}

                {policy ? (
                    <div
                        className={cn(
                            "rounded-2xl border p-4 text-sm",
                            policy.configured ? "border-emerald-500/30 bg-emerald-500/10" : "border-amber-500/30 bg-amber-500/10"
                        )}
                        data-testid="recording-policy-effect"
                    >
                        {policy.effect}
                        {policy.updated_at ? <span className="ml-2 text-xs text-muted-foreground">Updated {new Date(policy.updated_at).toLocaleString()}</span> : null}
                    </div>
                ) : null}

                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <div className="space-y-1 sm:col-span-2">
                        <Label className="text-xs">Consent mode</Label>
                        <Select
                            value={effective.default_consent_mode}
                            onChange={(v) => setDraft((d) => ({ ...d, default_consent_mode: v as RecordingConsentMode }))}
                            ariaLabel="Recording consent mode"
                            disabled={!canEdit}
                        >
                            {(Object.keys(MODE_LABELS) as RecordingConsentMode[]).map((mode) => (
                                <option key={mode} value={mode}>
                                    {MODE_LABELS[mode]}
                                </option>
                            ))}
                        </Select>
                        <div className="text-xs text-muted-foreground">
                            One-party: lawful only where you and your callees are in one-party jurisdictions (or you are calling your own numbers). Two-party: the agent speaks a recording notice as the first thing on the call, in the countries you list below (empty list = everywhere).
                        </div>
                    </div>

                    <div className="space-y-1 sm:col-span-2">
                        <Label className="text-xs" htmlFor="rp-announcement">Spoken notice (two-party)</Label>
                        <Input
                            id="rp-announcement"
                            placeholder="This call may be recorded for quality and training. Press 9 to opt out."
                            value={effective.announcement_text ?? ""}
                            onChange={(e) => setDraft((d) => ({ ...d, announcement_text: e.target.value }))}
                            disabled={!canEdit || effective.default_consent_mode !== "two_party"}
                        />
                    </div>

                    <div className="space-y-1">
                        <Label className="text-xs" htmlFor="rp-countries">Countries needing the notice</Label>
                        <Input
                            id="rp-countries"
                            placeholder="GB, DE, US-CA"
                            value={codesText}
                            onChange={(e) =>
                                setDraft((d) => ({
                                    ...d,
                                    two_party_country_codes: e.target.value
                                        .split(/[,\s]+/)
                                        .map((c) => c.trim().toUpperCase())
                                        .filter(Boolean),
                                }))
                            }
                            disabled={!canEdit || effective.default_consent_mode !== "two_party"}
                        />
                    </div>

                    <div className="grid grid-cols-2 gap-3">
                        <div className="space-y-1">
                            <Label className="text-xs" htmlFor="rp-dtmf">Opt-out key</Label>
                            <Input
                                id="rp-dtmf"
                                maxLength={1}
                                placeholder="9"
                                value={effective.opt_out_dtmf_digit ?? ""}
                                onChange={(e) => setDraft((d) => ({ ...d, opt_out_dtmf_digit: e.target.value }))}
                                disabled={!canEdit || effective.default_consent_mode !== "two_party"}
                            />
                        </div>
                        <div className="space-y-1">
                            <Label className="text-xs" htmlFor="rp-retention">Keep for (days)</Label>
                            <Input
                                id="rp-retention"
                                type="number"
                                min={1}
                                max={3650}
                                value={effective.retention_days}
                                onChange={(e) => setDraft((d) => ({ ...d, retention_days: Math.max(1, Math.min(3650, Number.parseInt(e.target.value, 10) || 90)) }))}
                                disabled={!canEdit}
                            />
                        </div>
                    </div>
                </div>

                <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="text-xs text-red-500">{saveError}</div>
                    {canEdit ? (
                        <Button type="button" disabled={!dirty || saving} onClick={() => void save()} data-testid="recording-policy-save">
                            {saving ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null}
                            Save policy
                        </Button>
                    ) : (
                        <span className="text-xs text-muted-foreground">Only tenant admins can change this.</span>
                    )}
                </div>
            </CardContent>
        </Card>
    );
}

export function RecordingPolicySection({ canEdit }: { canEdit: boolean }) {
    const [policy, setPolicy] = useState<RecordingPolicy | undefined>(undefined);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        const ctrl = new AbortController();
        fetchRecordingPolicy(ctrl.signal)
            .then((p) => {
                setPolicy(p);
                setError(null);
            })
            .catch((err) => {
                if (ctrl.signal.aborted) return;
                setError(formatError(err));
            })
            .finally(() => {
                if (!ctrl.signal.aborted) setIsLoading(false);
            });
        return () => ctrl.abort();
    }, []);

    const onSave = async (input: RecordingPolicyInput) => {
        setSaving(true);
        try {
            const saved = await saveRecordingPolicy(input);
            setPolicy(saved);
            notificationsStore.create({ type: "success", title: "Recording policy saved", message: saved.effect });
            return saved;
        } finally {
            setSaving(false);
        }
    };

    // Remount the form when the server hands back a new policy version so any
    // stale draft is discarded without syncing state inside an effect.
    return (
        <RecordingPolicyView
            key={policy?.updated_at ?? "unconfigured"}
            policy={policy}
            isLoading={isLoading}
            error={error}
            canEdit={canEdit}
            onSave={onSave}
            saving={saving}
        />
    );
}
