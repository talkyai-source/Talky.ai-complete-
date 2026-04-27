"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { useWhiteLabelBranding } from "@/components/white-label/white-label-branding-provider";

type AgentSettings = {
    systemPrompt: string;
    greetingMessage: string;
    transferEnabled: boolean;
};

type AgentSettingsResponse = {
    partner: { id: string; allowTransfer: boolean };
    tenant: { id: string };
    agentSettings?: { transfer_enabled?: boolean };
    config: AgentSettings;
    updatedAt?: string;
};

function normalizeParam(value: string | string[] | undefined) {
    if (Array.isArray(value)) return value[0] ?? "";
    return value ?? "";
}

function endpointUrl(partnerId: string, tenantId: string) {
    return `/api/v1/white-label/partners/${encodeURIComponent(partnerId)}/tenants/${encodeURIComponent(tenantId)}/agent-settings`;
}

export default function WhiteLabelTenantAgentSettingsPage() {
    const params = useParams();
    const partnerId = normalizeParam(params?.partner as string | string[] | undefined);
    const tenantId = normalizeParam(params?.tenant as string | string[] | undefined);
    const branding = useWhiteLabelBranding()?.branding;

    const [loaded, setLoaded] = useState(false);
    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState(false);
    const [allowTransfer, setAllowTransfer] = useState(false);

    const [systemPrompt, setSystemPrompt] = useState("");
    const [greetingMessage, setGreetingMessage] = useState("");
    const [transferEnabled, setTransferEnabled] = useState(false);

    const [baseline, setBaseline] = useState<AgentSettings | null>(null);
    const [inlineError, setInlineError] = useState<string | null>(null);
    const [success, setSuccess] = useState<string | null>(null);

    const [testParallel, setTestParallel] = useState("10");
    const [testing, setTesting] = useState(false);
    const [testSummary, setTestSummary] = useState<{ ok: number; rateLimited: number; other: number } | null>(null);
    const [cooldownUntil, setCooldownUntil] = useState<number>(0);

    const trimmedPrompt = systemPrompt.trim();
    const trimmedGreeting = greetingMessage.trim();

    const fieldErrors = useMemo(() => {
        return {
            systemPrompt: loaded && trimmedPrompt.length === 0 ? "System prompt is required." : "",
            greetingMessage: loaded && trimmedGreeting.length === 0 ? "Greeting message is required." : "",
        };
    }, [loaded, trimmedGreeting.length, trimmedPrompt.length]);

    const dirty = useMemo(() => {
        if (!baseline) return false;
        return (
            baseline.systemPrompt !== systemPrompt ||
            baseline.greetingMessage !== greetingMessage ||
            baseline.transferEnabled !== transferEnabled
        );
    }, [baseline, greetingMessage, systemPrompt, transferEnabled]);

    const canSave = useMemo(() => {
        if (!loaded) return false;
        if (saving) return false;
        if (trimmedPrompt.length === 0) return false;
        if (trimmedGreeting.length === 0) return false;
        if (transferEnabled && !allowTransfer) return false;
        return dirty;
    }, [allowTransfer, dirty, loaded, saving, transferEnabled, trimmedGreeting.length, trimmedPrompt.length]);

    useEffect(() => {
        if (!partnerId || !tenantId) return;
        let cancelled = false;
        setLoading(true);
        setInlineError(null);
        setSuccess(null);

        fetch(endpointUrl(partnerId, tenantId), { method: "GET", headers: { "content-type": "application/json" }, cache: "no-store" })
            .then(async (res) => {
                const data = (await res.json()) as AgentSettingsResponse | { detail?: string; error?: string };
                if (!res.ok) {
                    const msg = typeof (data as { detail?: string }).detail === "string" ? (data as { detail: string }).detail : "Failed to load agent settings.";
                    throw new Error(msg);
                }
                return data as AgentSettingsResponse;
            })
            .then((data) => {
                if (cancelled) return;
                setAllowTransfer(Boolean(data.agentSettings?.transfer_enabled ?? data.partner.allowTransfer));
                setSystemPrompt(data.config.systemPrompt ?? "");
                setGreetingMessage(data.config.greetingMessage ?? "");
                const transferAllowed = Boolean(data.agentSettings?.transfer_enabled ?? data.partner.allowTransfer);
                setTransferEnabled(Boolean(data.config.transferEnabled) && transferAllowed);
                setBaseline({
                    systemPrompt: data.config.systemPrompt ?? "",
                    greetingMessage: data.config.greetingMessage ?? "",
                    transferEnabled: Boolean(data.config.transferEnabled) && transferAllowed,
                });
                setLoaded(true);
            })
            .catch((err) => {
                if (cancelled) return;
                setInlineError(err instanceof Error ? err.message : "Failed to load agent settings.");
                setLoaded(true);
            })
            .finally(() => {
                if (cancelled) return;
                setLoading(false);
            });

        return () => {
            cancelled = true;
        };
    }, [partnerId, tenantId]);

    useEffect(() => {
        if (!allowTransfer && transferEnabled) setTransferEnabled(false);
    }, [allowTransfer, transferEnabled]);

    const save = async () => {
        setInlineError(null);
        setSuccess(null);

        const prompt = systemPrompt.trim();
        const greeting = greetingMessage.trim();
        if (prompt.length === 0) {
            setInlineError("System prompt cannot be empty.");
            return;
        }
        if (greeting.length === 0) {
            setInlineError("Greeting message cannot be empty.");
            return;
        }
        if (transferEnabled && !allowTransfer) {
            setInlineError("This feature is disabled by partner policy.");
            return;
        }

        setSaving(true);
        try {
            const res = await fetch(endpointUrl(partnerId, tenantId), {
                method: "PATCH",
                headers: { "content-type": "application/json" },
                body: JSON.stringify({ systemPrompt: prompt, greetingMessage: greeting, transferEnabled: Boolean(transferEnabled) }),
            });
            const data = (await res.json()) as AgentSettingsResponse | { detail?: string };
            if (!res.ok) {
                const msg = typeof (data as { detail?: string }).detail === "string" ? (data as { detail: string }).detail : "Failed to save changes.";
                throw new Error(msg);
            }
            const out = data as AgentSettingsResponse;
            setAllowTransfer(Boolean(out.agentSettings?.transfer_enabled ?? out.partner.allowTransfer));
            setSystemPrompt(out.config.systemPrompt ?? "");
            setGreetingMessage(out.config.greetingMessage ?? "");
            const transferAllowed = Boolean(out.agentSettings?.transfer_enabled ?? out.partner.allowTransfer);
            setTransferEnabled(Boolean(out.config.transferEnabled) && transferAllowed);
            setBaseline({
                systemPrompt: out.config.systemPrompt ?? "",
                greetingMessage: out.config.greetingMessage ?? "",
                transferEnabled: Boolean(out.config.transferEnabled) && transferAllowed,
            });
            setSuccess("Saved changes.");
        } catch (err) {
            setInlineError(err instanceof Error ? err.message : "Failed to save changes.");
        } finally {
            setSaving(false);
        }
    };

    const title = branding ? `${branding.displayName} Agent Settings` : "Agent Settings";

    const canRunTest = useMemo(() => {
        if (!tenantId) return false;
        if (loading || saving) return false;
        if (testing) return false;
        if (Date.now() < cooldownUntil) return false;
        const n = Number(testParallel);
        if (!Number.isFinite(n) || n < 1) return false;
        return true;
    }, [cooldownUntil, loading, saving, tenantId, testParallel, testing]);

    const cooldownSeconds = useMemo(() => {
        const ms = cooldownUntil - Date.now();
        if (ms <= 0) return 0;
        return Math.ceil(ms / 1000);
    }, [cooldownUntil]);

    const runConcurrencyTest = async () => {
        if (!canRunTest) return;
        setTesting(true);
        setTestSummary(null);
        try {
            const n = Math.max(1, Math.floor(Number(testParallel)));
            const results = await Promise.all(
                Array.from({ length: n }).map(async () => {
                    try {
                        const res = await fetch("/api/v1/assistant/execute", {
                            method: "POST",
                            headers: { "content-type": "application/json", accept: "application/json" },
                            body: JSON.stringify({ action_type: "execute", source: "tenant", lead_id: tenantId, context: { partner_id: partnerId } }),
                        });
                        return res;
                    } catch {
                        return null;
                    }
                })
            );
            let ok = 0;
            let rateLimited = 0;
            let other = 0;
            let retryAfterMs = 0;
            for (const r of results) {
                if (!r) {
                    other += 1;
                    continue;
                }
                if (r.status === 429) {
                    rateLimited += 1;
                    const ra = r.headers.get("retry-after");
                    const sec = ra ? Number(ra) : 0;
                    if (Number.isFinite(sec) && sec > 0) retryAfterMs = Math.max(retryAfterMs, sec * 1000);
                    continue;
                }
                if (r.ok) ok += 1;
                else other += 1;
            }
            setTestSummary({ ok, rateLimited, other });
            if (retryAfterMs > 0) setCooldownUntil(Date.now() + retryAfterMs);
        } finally {
            setTesting(false);
        }
    };

    return (
        <DashboardLayout
            title={title}
            description={tenantId ? `Configure conversational behavior for tenant "${tenantId}".` : "Configure conversational behavior for this tenant."}
        >
            <div className="space-y-6">
                {inlineError ? (
                    <div className="rounded-2xl border border-red-500/20 bg-red-500/10 px-4 py-3 text-sm text-red-100" role="alert">
                        {inlineError}
                    </div>
                ) : null}
                {success ? (
                    <div className="rounded-2xl border border-emerald-500/20 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-100" role="status">
                        {success}
                    </div>
                ) : null}

                <div className="content-card space-y-3">
                    <div className="text-sm font-semibold text-foreground">System Prompt</div>
                    <div className="text-sm text-muted-foreground">
                        Define the agent&apos;s behavior, tone, and instructions. This stays scoped to the selected tenant.
                    </div>
                    <div className="space-y-2">
                        <Label htmlFor="systemPrompt">Prompt</Label>
                        <textarea
                            id="systemPrompt"
                            value={systemPrompt}
                            onChange={(e) => {
                                setSystemPrompt(e.target.value);
                                setInlineError(null);
                                setSuccess(null);
                            }}
                            rows={10}
                            className={cn(
                                "w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground ring-offset-background transition-[background-color,border-color,box-shadow] duration-150 ease-out hover:bg-accent/20 hover:border-foreground/20 placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
                                fieldErrors.systemPrompt ? "border-red-500/50 focus-visible:ring-red-500/30" : ""
                            )}
                            disabled={loading || saving}
                            placeholder="You are a friendly salon receptionist. Greet callers politely and assist with booking appointments."
                        />
                        {fieldErrors.systemPrompt ? <div className="text-xs text-red-200">{fieldErrors.systemPrompt}</div> : null}
                    </div>
                </div>

                <div className="content-card space-y-3">
                    <div className="text-sm font-semibold text-foreground">Greeting Message</div>
                    <div className="text-sm text-muted-foreground">Spoken at the beginning of every call for this tenant.</div>
                    <div className="space-y-2">
                        <Label htmlFor="greetingMessage">Greeting</Label>
                        <Input
                            id="greetingMessage"
                            value={greetingMessage}
                            onChange={(e) => {
                                setGreetingMessage(e.target.value);
                                setInlineError(null);
                                setSuccess(null);
                            }}
                            disabled={loading || saving}
                            className={fieldErrors.greetingMessage ? "border-red-500/50 focus-visible:ring-red-500/30" : undefined}
                            placeholder="Hello! Thank you for calling. How may I assist you today?"
                        />
                        {fieldErrors.greetingMessage ? <div className="text-xs text-red-200">{fieldErrors.greetingMessage}</div> : null}
                    </div>
                </div>

                {allowTransfer ? (
                    <div className="content-card space-y-3">
                        <div className="text-sm font-semibold text-foreground">Call Transfer</div>
                        <div className="text-sm text-muted-foreground">
                            Allow the agent to transfer callers to a human when needed.
                        </div>
                        <div className="flex items-center justify-between gap-4">
                            <div className="min-w-0">
                                <div className="text-sm font-semibold text-foreground">Enable Call Transfer</div>
                                <div className="mt-1 text-xs text-muted-foreground">Toggle ON to permit transfer.</div>
                            </div>
                            <Switch
                                checked={transferEnabled}
                                onCheckedChange={(next) => {
                                    setTransferEnabled(next);
                                    setInlineError(null);
                                    setSuccess(null);
                                }}
                                ariaLabel="Enable call transfer"
                                disabled={loading || saving}
                            />
                        </div>
                    </div>
                ) : null}

                <div className="content-card space-y-3">
                    <div className="text-sm font-semibold text-foreground">Concurrency Test</div>
                    <div className="text-sm text-muted-foreground">Simulate parallel requests from this sub-tenant to validate limit handling.</div>
                    {testSummary ? (
                        <div className="rounded-2xl border border-border bg-background/60 px-4 py-3 text-sm text-foreground">
                            <div className="flex flex-wrap gap-3">
                                <span>
                                    OK: <span className="font-semibold tabular-nums">{testSummary.ok}</span>
                                </span>
                                <span className="text-border">•</span>
                                <span>
                                    Rate-limited: <span className="font-semibold tabular-nums">{testSummary.rateLimited}</span>
                                </span>
                                <span className="text-border">•</span>
                                <span>
                                    Other: <span className="font-semibold tabular-nums">{testSummary.other}</span>
                                </span>
                            </div>
                        </div>
                    ) : null}
                    {cooldownSeconds > 0 ? (
                        <div className="rounded-2xl border border-amber-500/20 bg-amber-500/10 px-4 py-3 text-sm text-amber-100" role="alert">
                            Concurrency limit reached. Please wait {cooldownSeconds}s before retrying.
                        </div>
                    ) : null}
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
                        <div className="space-y-2">
                            <Label htmlFor="parallelCalls">Parallel calls</Label>
                            <Input
                                id="parallelCalls"
                                type="number"
                                min={1}
                                step={1}
                                value={testParallel}
                                onChange={(e) => setTestParallel(e.target.value)}
                                disabled={loading || saving || testing}
                            />
                        </div>
                        <Button type="button" onClick={runConcurrencyTest} disabled={!canRunTest}>
                            {testing ? "Running…" : cooldownSeconds > 0 ? `Blocked (${cooldownSeconds}s)` : "Run Test"}
                        </Button>
                    </div>
                </div>

                <div className="flex flex-col-reverse gap-2 sm:flex-row sm:items-center sm:justify-end">
                    <Button
                        type="button"
                        variant="secondary"
                        onClick={() => {
                            if (!baseline) return;
                            setSystemPrompt(baseline.systemPrompt);
                            setGreetingMessage(baseline.greetingMessage);
                            setTransferEnabled(baseline.transferEnabled);
                            setInlineError(null);
                            setSuccess(null);
                        }}
                        disabled={!dirty || saving || loading}
                    >
                        Reset
                    </Button>
                    <Button type="button" onClick={save} disabled={!canSave}>
                        {saving ? "Saving…" : "Save Changes"}
                    </Button>
                </div>
            </div>
        </DashboardLayout>
    );
}
