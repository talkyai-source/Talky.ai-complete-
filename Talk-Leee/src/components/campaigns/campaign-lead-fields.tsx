"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Check, Loader2, RefreshCw, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
    leadDetailsApi,
    type CampaignLeadField,
    type ContactFieldSpec,
} from "@/lib/lead-details-api";

const DEFAULT_CAPTURE_KEYS = new Set([
    "email",
    "company_name",
    "job_title",
    "best_time_to_call",
    "calling_notes",
]);

export function defaultCampaignLeadFields(specs: ContactFieldSpec[]): CampaignLeadField[] {
    return specs
        .filter((field) => field.agent_usable && DEFAULT_CAPTURE_KEYS.has(field.key))
        .map((field, index) => ({
            field_key: field.key,
            label: field.label,
            field_type: field.field_type,
            is_required: false,
            agent_visible: true,
            user_visible: true,
            options: null,
            sort_order: index,
        }));
}

export function useCampaignLeadFieldDraft(campaignId?: string) {
    const [draft, setDraft] = useState<CampaignLeadField[] | null>(null);
    const specQuery = useQuery({
        queryKey: ["contactFieldSpec"],
        queryFn: () => leadDetailsApi.fieldSpec(),
        staleTime: 10 * 60_000,
    });
    const savedQuery = useQuery({
        queryKey: ["campaignLeadFields", campaignId],
        queryFn: () => leadDetailsApi.campaignFields(campaignId!),
        enabled: Boolean(campaignId),
    });

    const initial = useMemo(() => {
        if (campaignId) return savedQuery.data?.fields ?? [];
        return specQuery.data ? defaultCampaignLeadFields(specQuery.data.fields) : [];
    }, [campaignId, savedQuery.data, specQuery.data]);

    return {
        specs: specQuery.data?.fields ?? [],
        fields: draft ?? initial,
        setFields: setDraft,
        isLoading: specQuery.isLoading || (Boolean(campaignId) && savedQuery.isLoading),
        isError: specQuery.isError || savedQuery.isError,
        error: specQuery.error ?? savedQuery.error,
        retry: () => {
            void specQuery.refetch();
            if (campaignId) void savedQuery.refetch();
        },
    };
}

export function CampaignLeadFieldsPicker({
    specs,
    value,
    onChange,
    isLoading = false,
    error,
    onRetry,
    disabled = false,
}: {
    specs: ContactFieldSpec[];
    value: CampaignLeadField[];
    onChange: (fields: CampaignLeadField[]) => void;
    isLoading?: boolean;
    error?: unknown;
    onRetry?: () => void;
    disabled?: boolean;
}) {
    const usable = specs.filter((field) => field.agent_usable && field.key !== "full_name");
    const selected = new Map(value.map((field) => [field.field_key, field]));

    function normalized(fields: CampaignLeadField[]): CampaignLeadField[] {
        const byKey = new Map(fields.map((field) => [field.field_key, field]));
        return usable
            .filter((field) => byKey.has(field.key))
            .map((field, index) => ({ ...byKey.get(field.key)!, sort_order: index }));
    }

    function toggle(field: ContactFieldSpec, checked: boolean) {
        if (checked) {
            onChange(normalized([
                ...value,
                {
                    field_key: field.key,
                    label: field.label,
                    field_type: field.field_type,
                    is_required: false,
                    agent_visible: true,
                    user_visible: true,
                    options: null,
                    sort_order: value.length,
                },
            ]));
        } else {
            onChange(normalized(value.filter((item) => item.field_key !== field.key)));
        }
    }

    function setRequired(fieldKey: string, required: boolean) {
        onChange(value.map((field) => (
            field.field_key === fieldKey ? { ...field, is_required: required } : field
        )));
    }

    return (
        <section className="space-y-4" aria-labelledby="campaign-lead-fields-heading">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h2 id="campaign-lead-fields-heading" className="flex items-center gap-2 text-base font-semibold text-foreground">
                        <Sparkles className="h-4 w-4 text-emerald-500" aria-hidden />
                        Contact details the agent may capture
                    </h2>
                    <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
                        Choose only information this campaign needs. “Required” means a missing
                        answer remains visibly incomplete; the agent must never invent it.
                    </p>
                </div>
                {!isLoading && !error ? (
                    <span className="rounded-full border border-border bg-muted px-2.5 py-1 text-xs text-muted-foreground">
                        {value.length} selected
                    </span>
                ) : null}
            </div>

            {isLoading ? (
                <div className="flex items-center gap-2 rounded-xl border border-border bg-muted/30 p-4 text-sm text-muted-foreground" role="status">
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Loading contact fields…
                </div>
            ) : error ? (
                <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4" role="alert">
                    <p className="flex items-center gap-2 text-sm text-destructive">
                        <AlertCircle className="h-4 w-4" aria-hidden />
                        Contact-field settings could not be loaded. Campaign saving is blocked so the agent cannot receive an accidental field set.
                    </p>
                    {onRetry ? <Button type="button" variant="outline" size="sm" onClick={onRetry}><RefreshCw className="h-4 w-4" aria-hidden /> Retry</Button> : null}
                </div>
            ) : (
                <div className="grid gap-3 sm:grid-cols-2">
                    {usable.map((field) => {
                        const current = selected.get(field.key);
                        return (
                            <div key={field.key} className={`rounded-xl border p-3 transition-colors ${current ? "border-emerald-500/35 bg-emerald-500/5" : "border-border bg-background/40"}`}>
                                <label className="flex cursor-pointer items-start gap-3">
                                    <input
                                        type="checkbox"
                                        checked={Boolean(current)}
                                        disabled={disabled}
                                        onChange={(event) => toggle(field, event.target.checked)}
                                        className="mt-0.5 h-4 w-4 rounded border-input accent-emerald-600"
                                    />
                                    <span className="min-w-0">
                                        <span className="block text-sm font-medium text-foreground">{field.label}</span>
                                        <span className="block text-xs text-muted-foreground">{field.field_type.replace(/_/g, " ")}</span>
                                    </span>
                                </label>
                                {current ? (
                                    <label className="mt-3 flex cursor-pointer items-center gap-2 border-t border-border/60 pt-2 text-xs text-muted-foreground">
                                        <input
                                            type="checkbox"
                                            checked={current.is_required}
                                            disabled={disabled}
                                            onChange={(event) => setRequired(field.key, event.target.checked)}
                                            className="h-3.5 w-3.5 rounded border-input accent-amber-600"
                                        />
                                        Required for this campaign
                                    </label>
                                ) : null}
                            </div>
                        );
                    })}
                </div>
            )}
        </section>
    );
}

export function CampaignLeadFieldsManager({ campaignId }: { campaignId: string }) {
    const queryClient = useQueryClient();
    const draft = useCampaignLeadFieldDraft(campaignId);
    const save = useMutation({
        mutationFn: () => leadDetailsApi.setCampaignFields(campaignId, draft.fields),
        onSuccess: (response) => {
            draft.setFields(response.fields);
            queryClient.setQueryData(["campaignLeadFields", campaignId], response);
        },
    });

    return (
        <div className="content-card space-y-4">
            <CampaignLeadFieldsPicker
                specs={draft.specs}
                value={draft.fields}
                onChange={draft.setFields}
                isLoading={draft.isLoading}
                error={draft.isError ? draft.error : undefined}
                onRetry={draft.retry}
                disabled={save.isPending}
            />
            {save.isError ? (
                <p className="text-sm text-destructive" role="alert">
                    {save.error instanceof Error ? save.error.message : "The contact-field settings could not be saved."}
                </p>
            ) : null}
            {save.isSuccess ? (
                <p className="flex items-center gap-2 text-sm text-emerald-700 dark:text-emerald-300" role="status">
                    <Check className="h-4 w-4" aria-hidden /> Contact-field settings saved.
                </p>
            ) : null}
            <div className="flex justify-end">
                <Button type="button" onClick={() => save.mutate()} disabled={draft.isLoading || draft.isError || save.isPending}>
                    {save.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Check className="h-4 w-4" aria-hidden />}
                    {save.isPending ? "Saving…" : "Save contact fields"}
                </Button>
            </div>
        </div>
    );
}
