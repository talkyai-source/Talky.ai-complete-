"use client";

import { useEffect, useMemo, useState } from "react";
import { Copy, Eye, Loader2, RefreshCw, Download, PlugZap } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import type { SalesforceImportInput, SalesforceSettingsUpdateInput } from "@/lib/backend-api";
import {
    useImportSalesforcePeople,
    useOutboundCampaigns,
    useRevealSalesforceWebhookToken,
    useRotateSalesforceWebhookToken,
    useSalesforceSettings,
    useTestSalesforceConnection,
    useUpdateSalesforceSettings,
} from "@/lib/api-hooks";
import { isApiClientError } from "@/lib/http-client";
import type { SalesforceImportResult, SalesforceProbe, SalesforceSettings, SalesforceSettingsResponse } from "@/lib/models";
import { cn } from "@/lib/utils";

export type SalesforceCampaignOption = { id: string; name: string; status: string };

export type SalesforceSettingsViewProps = {
    data: SalesforceSettingsResponse | undefined;
    isLoading?: boolean;
    error?: string | null;
    campaigns: SalesforceCampaignOption[];
    onSave: (input: SalesforceSettingsUpdateInput) => Promise<unknown>;
    onReveal: () => Promise<unknown>;
    onRotate: () => Promise<unknown>;
    onTest: () => Promise<SalesforceProbe>;
    onImport: (input: SalesforceImportInput) => Promise<SalesforceImportResult>;
    busy?: { save?: boolean; reveal?: boolean; rotate?: boolean; test?: boolean; import?: boolean };
};

function formatError(err: unknown) {
    if (isApiClientError(err)) return err.message;
    return err instanceof Error ? err.message : "Request failed";
}

async function copyText(value: string): Promise<boolean> {
    try {
        if (typeof navigator !== "undefined" && navigator.clipboard) {
            await navigator.clipboard.writeText(value);
            return true;
        }
    } catch {
        // fall through
    }
    return false;
}

const FLOW_BODY_EXAMPLE = `{
  "record_id": "{!$Record.Id}",
  "object_type": "Lead",
  "phone": "{!$Record.Phone}",
  "first_name": "{!$Record.FirstName}",
  "last_name": "{!$Record.LastName}",
  "email": "{!$Record.Email}",
  "company": "{!$Record.Company}",
  "notes": "Requested a callback from Salesforce"
}`;

export function SalesforceSettingsView({
    data,
    isLoading,
    error,
    campaigns,
    onSave,
    onReveal,
    onRotate,
    onTest,
    onImport,
    busy,
}: SalesforceSettingsViewProps) {
    const settings = data?.settings;

    // Form state is a sparse draft layered over the saved settings, so the
    // saved values never need to be copied into state (no setState in effects).
    const [draft, setDraft] = useState<Partial<SalesforceSettings>>({});
    const [saveError, setSaveError] = useState<string | null>(null);
    const effective: SalesforceSettings = {
        callback_campaign_id: settings?.callback_campaign_id ?? null,
        log_calls: settings?.log_calls ?? true,
        create_leads: settings?.create_leads ?? true,
        sync_inbound: settings?.sync_inbound ?? true,
        callback_priority: settings?.callback_priority ?? 8,
        ...draft,
    };
    const callbackCampaignId = effective.callback_campaign_id ?? "";
    const logCalls = effective.log_calls;
    const createLeads = effective.create_leads;
    const syncInbound = effective.sync_inbound;
    const priority = effective.callback_priority;
    const setCallbackCampaignId = (v: string) => setDraft((d) => ({ ...d, callback_campaign_id: v || null }));
    const setLogCalls = (v: boolean) => setDraft((d) => ({ ...d, log_calls: v }));
    const setCreateLeads = (v: boolean) => setDraft((d) => ({ ...d, create_leads: v }));
    const setSyncInbound = (v: boolean) => setDraft((d) => ({ ...d, sync_inbound: v }));
    const setPriority = (v: number) => setDraft((d) => ({ ...d, callback_priority: v }));

    const [probe, setProbe] = useState<SalesforceProbe | null>(null);
    const [copied, setCopied] = useState<string | null>(null);

    const [importObject, setImportObject] = useState<"Lead" | "Contact">("Lead");
    const [importCampaignChoice, setImportCampaignChoice] = useState<string | null>(null);
    const importCampaignId = importCampaignChoice ?? settings?.callback_campaign_id ?? "";
    const setImportCampaignId = (v: string) => setImportCampaignChoice(v);
    const [importLimit, setImportLimit] = useState("500");
    const [importWhere, setImportWhere] = useState("");
    const [importListName, setImportListName] = useState("");
    const [importResult, setImportResult] = useState<SalesforceImportResult | null>(null);
    const [importError, setImportError] = useState<string | null>(null);

    const dirty = useMemo(() => {
        if (!settings) return false;
        return (
            (settings.callback_campaign_id ?? "") !== callbackCampaignId ||
            settings.log_calls !== logCalls ||
            settings.create_leads !== createLeads ||
            settings.sync_inbound !== syncInbound ||
            settings.callback_priority !== priority
        );
    }, [callbackCampaignId, createLeads, logCalls, priority, settings, syncInbound]);

    const connected = Boolean(data?.connected);
    const revealed = Boolean(data?.webhook.token);

    const save = async () => {
        if (!settings) return;
        setSaveError(null);
        const input: SalesforceSettingsUpdateInput = {};
        if ((settings.callback_campaign_id ?? "") !== callbackCampaignId) {
            if (callbackCampaignId) input.callback_campaign_id = callbackCampaignId;
            else input.clear_callback_campaign = true;
        }
        if (settings.log_calls !== logCalls) input.log_calls = logCalls;
        if (settings.create_leads !== createLeads) input.create_leads = createLeads;
        if (settings.sync_inbound !== syncInbound) input.sync_inbound = syncInbound;
        if (settings.callback_priority !== priority) input.callback_priority = priority;
        try {
            await onSave(input);
            setDraft({});
        } catch (err) {
            setSaveError(formatError(err));
        }
    };

    const runTest = async () => {
        setProbe(null);
        try {
            setProbe(await onTest());
        } catch (err) {
            setProbe({ ok: false, error: formatError(err) });
        }
    };

    const runImport = async () => {
        setImportError(null);
        setImportResult(null);
        if (!importCampaignId) {
            setImportError("Choose a campaign to import into.");
            return;
        }
        const limit = Math.max(1, Math.min(2000, Number.parseInt(importLimit, 10) || 500));
        try {
            const result = await onImport({
                campaign_id: importCampaignId,
                object_type: importObject,
                limit,
                where: importWhere.trim() || undefined,
                list_name: importListName.trim() || undefined,
            });
            setImportResult(result);
        } catch (err) {
            setImportError(formatError(err));
        }
    };

    const copy = async (label: string, value?: string | null) => {
        if (!value) return;
        if (await copyText(value)) {
            setCopied(label);
            window.setTimeout(() => setCopied(null), 1500);
        }
    };

    return (
        <Card data-testid="salesforce-settings">
            <CardHeader>
                <CardTitle>Salesforce</CardTitle>
                <CardDescription>
                    Every finished call is logged as a Salesforce Task on the matching Lead or Contact; unknown callees become Leads. Salesforce can also ask the Talky.ai agent to call someone back.
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
                {error ? <div className="rounded-2xl border border-red-500/30 bg-background/70 p-4 text-sm text-red-500">{error}</div> : null}
                {isLoading && !data ? <div className="h-4 w-1/2 animate-pulse rounded bg-foreground/10" /> : null}

                {data && !data.server_configured ? (
                    <div className="rounded-2xl border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-100">
                        This server has no Salesforce Connected App configured (SALESFORCE_CLIENT_ID / SALESFORCE_CLIENT_SECRET). Connecting is disabled until an operator adds them.
                    </div>
                ) : null}

                {data && data.server_configured && !connected ? (
                    <div className="rounded-2xl border border-border bg-background/70 p-4 text-sm text-muted-foreground" data-testid="salesforce-not-connected">
                        Connect Salesforce with the card above. Settings, callback webhooks and imports unlock once the OAuth connection is active.
                    </div>
                ) : null}

                {data && connected ? (
                    <>
                        {/* Connection */}
                        <section className="rounded-2xl border border-border bg-background/70 p-4">
                            <div className="flex flex-wrap items-start justify-between gap-3">
                                <div className="min-w-0 text-sm">
                                    <div className="font-semibold text-foreground">Connected org</div>
                                    <div className="mt-1 text-xs text-muted-foreground">
                                        {data.username ? <span>{data.username}</span> : null}
                                        {data.org_id ? <span> · org {data.org_id}</span> : null}
                                        {data.instance_url ? <span> · {data.instance_url}</span> : null}
                                        <span> · API {data.api_version}</span>
                                    </div>
                                    {data.last_synced_call_at ? (
                                        <div className="mt-1 text-xs text-muted-foreground">Last call synced: {new Date(data.last_synced_call_at).toLocaleString()}</div>
                                    ) : (
                                        <div className="mt-1 text-xs text-muted-foreground">No call has been synced yet.</div>
                                    )}
                                </div>
                                <Button type="button" variant="outline" disabled={busy?.test} onClick={() => void runTest()} data-testid="salesforce-test">
                                    {busy?.test ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <PlugZap className="h-4 w-4" aria-hidden />}
                                    Test connection
                                </Button>
                            </div>
                            {probe ? (
                                <div className={cn("mt-3 text-xs", probe.ok ? "text-emerald-600" : "text-red-500")} data-testid="salesforce-probe">
                                    {probe.ok ? `OK — ${probe.username ?? "authenticated"} on ${probe.instance_url ?? "Salesforce"}` : `Failed: ${probe.error ?? "unknown error"}`}
                                </div>
                            ) : null}
                        </section>

                        {/* Sync + callbacks */}
                        <section className="rounded-2xl border border-border bg-background/70 p-4 space-y-4">
                            <div>
                                <div className="text-sm font-semibold text-foreground">What gets synced</div>
                                <div className="mt-1 text-xs text-muted-foreground">Applied to every call from now on. Historical calls are not backfilled.</div>
                            </div>
                            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                                <div className="flex items-center justify-between gap-3 rounded-xl border border-border p-3">
                                    <Label className="text-xs">Log calls as Tasks</Label>
                                    <Switch checked={logCalls} onCheckedChange={setLogCalls} ariaLabel="Log calls as Salesforce Tasks" />
                                </div>
                                <div className="flex items-center justify-between gap-3 rounded-xl border border-border p-3">
                                    <Label className="text-xs">Create Leads for unknown callees</Label>
                                    <Switch checked={createLeads} onCheckedChange={setCreateLeads} ariaLabel="Create Salesforce Leads for unknown callees" />
                                </div>
                                <div className="flex items-center justify-between gap-3 rounded-xl border border-border p-3">
                                    <Label className="text-xs">Include inbound calls</Label>
                                    <Switch checked={syncInbound} onCheckedChange={setSyncInbound} ariaLabel="Include inbound calls in Salesforce sync" />
                                </div>
                            </div>

                            <div className="grid grid-cols-1 gap-3 sm:grid-cols-[2fr_1fr]">
                                <div className="space-y-1">
                                    <Label className="text-xs">Callback campaign (outbound)</Label>
                                    <Select value={callbackCampaignId} onChange={setCallbackCampaignId} ariaLabel="Salesforce callback campaign">
                                        <option value="">— Not set —</option>
                                        {campaigns.map((c) => (
                                            <option key={c.id} value={c.id}>
                                                {c.name} ({c.status})
                                            </option>
                                        ))}
                                    </Select>
                                    <div className="text-xs text-muted-foreground">
                                        When Salesforce requests a callback, the contact is added to this campaign&apos;s &quot;Salesforce callbacks&quot; list and dialed by the campaign&apos;s agent, inside its calling window. Inbound campaigns cannot be chosen.
                                    </div>
                                </div>
                                <div className="space-y-1">
                                    <Label className="text-xs" htmlFor="sf-priority">Callback priority (1–10)</Label>
                                    <Input
                                        id="sf-priority"
                                        type="number"
                                        min={1}
                                        max={10}
                                        value={priority}
                                        onChange={(e) => setPriority(Math.max(1, Math.min(10, Number.parseInt(e.target.value, 10) || 8)))}
                                    />
                                </div>
                            </div>

                            <div className="flex flex-wrap items-center justify-between gap-2">
                                <div className="text-xs text-red-500">{saveError}</div>
                                <Button type="button" disabled={!dirty || busy?.save} onClick={() => void save()} data-testid="salesforce-save">
                                    {busy?.save ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null}
                                    Save settings
                                </Button>
                            </div>
                        </section>

                        {/* Webhooks */}
                        <section className="rounded-2xl border border-border bg-background/70 p-4 space-y-3">
                            <div>
                                <div className="text-sm font-semibold text-foreground">Let Salesforce request agent callbacks</div>
                                <div className="mt-1 text-xs text-muted-foreground">
                                    Two ways in. Both carry a per-tenant secret token in the URL — treat these URLs like passwords. Rotate the token if it leaks.
                                </div>
                            </div>

                            {!data.webhook.token_set ? (
                                <div className="text-xs text-amber-600">No webhook token yet. Generate one to get your URLs.</div>
                            ) : null}

                            <div className="grid grid-cols-1 gap-3">
                                <div className="space-y-1">
                                    <Label className="text-xs">JSON callback URL (Flow HTTP callout, Apex, Zapier/Make)</Label>
                                    <div className="flex items-center gap-2">
                                        <Input readOnly value={data.webhook.callback_url ?? ""} placeholder="Generate a token first" data-testid="salesforce-callback-url" />
                                        <Button type="button" variant="outline" disabled={!revealed} onClick={() => void copy("json", data.webhook.callback_url)} aria-label="Copy JSON callback URL">
                                            <Copy className="h-4 w-4" aria-hidden />
                                        </Button>
                                    </div>
                                </div>
                                <div className="space-y-1">
                                    <Label className="text-xs">Outbound Message endpoint (no-code: Flow / Workflow action)</Label>
                                    <div className="flex items-center gap-2">
                                        <Input readOnly value={data.webhook.outbound_message_url ?? ""} placeholder="Generate a token first" data-testid="salesforce-om-url" />
                                        <Button type="button" variant="outline" disabled={!revealed} onClick={() => void copy("om", data.webhook.outbound_message_url)} aria-label="Copy Outbound Message URL">
                                            <Copy className="h-4 w-4" aria-hidden />
                                        </Button>
                                    </div>
                                </div>
                            </div>

                            <div className="flex flex-wrap items-center gap-2">
                                {data.webhook.token_set && !revealed ? (
                                    <Button type="button" variant="outline" disabled={busy?.reveal} onClick={() => void onReveal()} data-testid="salesforce-reveal">
                                        {busy?.reveal ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Eye className="h-4 w-4" aria-hidden />}
                                        Reveal URLs
                                    </Button>
                                ) : null}
                                <Button type="button" variant={data.webhook.token_set ? "secondary" : "default"} disabled={busy?.rotate} onClick={() => void onRotate()} data-testid="salesforce-rotate">
                                    {busy?.rotate ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <RefreshCw className="h-4 w-4" aria-hidden />}
                                    {data.webhook.token_set ? "Rotate token" : "Generate token"}
                                </Button>
                                {copied ? <span className="text-xs text-emerald-600">Copied.</span> : null}
                            </div>

                            <details className="rounded-xl border border-border p-3 text-xs text-muted-foreground">
                                <summary className="cursor-pointer font-medium text-foreground">Setup guide</summary>
                                <div className="mt-2 space-y-3">
                                    <div>
                                        <div className="font-semibold text-foreground">A. Flow HTTP callout (JSON)</div>
                                        <p className="mt-1">
                                            Create an External Credential / Named Credential for your JSON callback URL (no auth needed — the token is in the URL), then in a record-triggered Flow on Lead or Contact add an HTTP Callout action with method POST and this body:
                                        </p>
                                        <pre className="mt-2 overflow-x-auto rounded bg-muted/50 p-2 text-[11px] leading-relaxed">{FLOW_BODY_EXAMPLE}</pre>
                                        <p className="mt-1">Optional: <code>campaign_id</code> overrides the callback campaign; <code>priority</code> (1–10) overrides the default.</p>
                                    </div>
                                    <div>
                                        <div className="font-semibold text-foreground">B. Outbound Message (no code)</div>
                                        <p className="mt-1">
                                            Setup → Workflow Actions → Outbound Messages → New. Object: Lead or Contact. Endpoint URL: the Outbound Message endpoint above. Fields to send: <code>Id</code>, <code>FirstName</code>, <code>LastName</code>, <code>Phone</code> (or <code>MobilePhone</code>), <code>Email</code>, <code>Company</code>. Optional custom fields <code>Talky_Notes__c</code> and <code>Talky_Campaign_Id__c</code> are honoured. Trigger it from a Flow (&quot;Send Outbound Message&quot; action) or a Workflow Rule such as &quot;Callback_Requested__c = true&quot;.
                                        </p>
                                    </div>
                                    <div>
                                        <div className="font-semibold text-foreground">What happens next</div>
                                        <p className="mt-1">
                                            The contact lands in the callback campaign&apos;s &quot;Salesforce callbacks&quot; list and is dialed immediately if the campaign is running and inside its calling window (paused campaigns store the contact without dialing). Do-not-call contacts are refused. After the call, a Task with the outcome, duration and AI summary is written back to the same Salesforce record.
                                        </p>
                                    </div>
                                </div>
                            </details>
                        </section>

                        {/* Import */}
                        <section className="rounded-2xl border border-border bg-background/70 p-4 space-y-3">
                            <div>
                                <div className="text-sm font-semibold text-foreground">Import Leads or Contacts into a campaign</div>
                                <div className="mt-1 text-xs text-muted-foreground">
                                    Pulls records that have a phone number (unconverted Leads only), newest first, into a new contact list. Each imported contact remembers its Salesforce Id.
                                </div>
                            </div>
                            <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
                                <div className="space-y-1">
                                    <Label className="text-xs">Object</Label>
                                    <Select value={importObject} onChange={(v) => setImportObject(v === "Contact" ? "Contact" : "Lead")} ariaLabel="Salesforce object to import">
                                        <option value="Lead">Leads</option>
                                        <option value="Contact">Contacts</option>
                                    </Select>
                                </div>
                                <div className="space-y-1 sm:col-span-2">
                                    <Label className="text-xs">Into campaign</Label>
                                    <Select value={importCampaignId} onChange={setImportCampaignId} ariaLabel="Campaign to import into">
                                        <option value="">— Choose —</option>
                                        {campaigns.map((c) => (
                                            <option key={c.id} value={c.id}>
                                                {c.name} ({c.status})
                                            </option>
                                        ))}
                                    </Select>
                                </div>
                                <div className="space-y-1">
                                    <Label className="text-xs" htmlFor="sf-import-limit">Max records</Label>
                                    <Input id="sf-import-limit" type="number" min={1} max={2000} value={importLimit} onChange={(e) => setImportLimit(e.target.value)} />
                                </div>
                            </div>
                            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                                <div className="space-y-1">
                                    <Label className="text-xs" htmlFor="sf-import-where">SOQL filter (optional)</Label>
                                    <Input id="sf-import-where" placeholder="Status = 'Open - Not Contacted'" value={importWhere} onChange={(e) => setImportWhere(e.target.value)} />
                                </div>
                                <div className="space-y-1">
                                    <Label className="text-xs" htmlFor="sf-import-list">List name (optional)</Label>
                                    <Input id="sf-import-list" placeholder="Salesforce Leads 2026-09-07" value={importListName} onChange={(e) => setImportListName(e.target.value)} />
                                </div>
                            </div>
                            <div className="flex flex-wrap items-center justify-between gap-2">
                                <div className="text-xs">
                                    {importError ? <span className="text-red-500">{importError}</span> : null}
                                    {importResult ? (
                                        <span className="text-muted-foreground" data-testid="salesforce-import-result">
                                            Fetched {importResult.fetched} · imported {importResult.imported} · revived {importResult.revived} · already present {importResult.duplicates_skipped} · invalid {importResult.invalid}
                                        </span>
                                    ) : null}
                                </div>
                                <Button type="button" variant="outline" disabled={busy?.import} onClick={() => void runImport()} data-testid="salesforce-import">
                                    {busy?.import ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Download className="h-4 w-4" aria-hidden />}
                                    Import now
                                </Button>
                            </div>
                        </section>
                    </>
                ) : null}
            </CardContent>
        </Card>
    );
}

export function SalesforceSettingsPanel({ connected }: { connected?: boolean }) {
    const q = useSalesforceSettings();
    const campaignsQ = useOutboundCampaigns();
    const save = useUpdateSalesforceSettings();
    const reveal = useRevealSalesforceWebhookToken();
    const rotate = useRotateSalesforceWebhookToken();
    const test = useTestSalesforceConnection();
    const importPeople = useImportSalesforcePeople();

    const campaigns = useMemo<SalesforceCampaignOption[]>(
        () => (campaignsQ.data ?? []).map((c) => ({ id: c.id, name: c.name, status: c.status })),
        [campaignsQ.data]
    );

    // The card's status is the source of truth for "connected"; the settings
    // endpoint agrees, but refetch it when the OAuth popup flips the card.
    useEffect(() => {
        if (connected) void q.refetch();
        // eslint-disable-next-line react-hooks/exhaustive-deps -- refetch only when the card's connection state changes
    }, [connected]);

    return (
        <SalesforceSettingsView
            data={q.data}
            isLoading={q.isLoading}
            error={q.isError ? formatError(q.error) : null}
            campaigns={campaigns}
            onSave={save.mutateAsync}
            onReveal={reveal.mutateAsync}
            onRotate={rotate.mutateAsync}
            onTest={test.mutateAsync}
            onImport={importPeople.mutateAsync}
            busy={{
                save: save.isPending,
                reveal: reveal.isPending,
                rotate: rotate.isPending,
                test: test.isPending,
                import: importPeople.isPending,
            }}
        />
    );
}
