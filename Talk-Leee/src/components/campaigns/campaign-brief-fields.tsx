"use client";

import { Route, ShieldCheck, Target } from "lucide-react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    CAMPAIGN_NEXT_ACTION_OPTIONS,
    campaignBriefValidation,
    type CampaignBriefDraft,
    type CampaignBriefLeadField,
    type CampaignNextAction,
} from "@/lib/campaign-brief";

export function CampaignBriefFields({
    value,
    onChange,
    requiredLeadFields,
    disabled = false,
    idPrefix,
}: {
    value: CampaignBriefDraft;
    onChange: (value: CampaignBriefDraft) => void;
    requiredLeadFields: CampaignBriefLeadField[];
    disabled?: boolean;
    idPrefix: string;
}) {
    const validation = campaignBriefValidation(value);
    const transferApproved = value.approved_next_actions.includes("transfer");

    function update(patch: Partial<CampaignBriefDraft>) {
        onChange({ ...value, ...patch });
    }

    function toggleAction(action: CampaignNextAction, checked: boolean) {
        const next = checked
            ? [...value.approved_next_actions, action]
            : value.approved_next_actions.filter((candidate) => candidate !== action);
        update({
            approved_next_actions: [...new Set(next)],
            ...(action === "transfer" && !checked ? { transfer_destination: "" } : {}),
        });
    }

    return (
        <section
            className="space-y-5 rounded-2xl border border-emerald-500/20 bg-emerald-500/[0.035] p-4 sm:p-5"
            aria-labelledby={`${idPrefix}-heading`}
        >
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="max-w-2xl">
                    <h2 id={`${idPrefix}-heading`} className="flex items-center gap-2 text-base font-semibold text-foreground">
                        <Target className="h-4 w-4 text-emerald-500" aria-hidden />
                        Campaign brief
                    </h2>
                    <p className="mt-1 text-sm text-muted-foreground">
                        Structured instructions used by the live agent. Brand and representative come from
                        the identity fields above; everything here is saved and appears in prompt preview.
                    </p>
                </div>
                <span className="w-fit rounded-full border border-emerald-500/25 bg-emerald-500/10 px-2.5 py-1 text-xs font-medium text-emerald-700 dark:text-emerald-300">
                    Live prompt layer
                </span>
            </div>

            <div className="grid gap-4 md:grid-cols-[minmax(0,1.35fr)_minmax(12rem,0.65fr)]">
                <div className="space-y-2">
                    <Label htmlFor={`${idPrefix}-opening-objective`}>
                        Opening objective <span className="text-destructive">*</span>
                    </Label>
                    <textarea
                        id={`${idPrefix}-opening-objective`}
                        value={value.opening_objective}
                        onChange={(event) => update({ opening_objective: event.target.value })}
                        placeholder="e.g. Confirm whether the operations lead owns vendor selection, then earn permission for one discovery question."
                        rows={3}
                        maxLength={500}
                        required
                        disabled={disabled}
                        aria-describedby={`${idPrefix}-opening-help`}
                        className="flex w-full resize-y rounded-lg border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                    />
                    <p id={`${idPrefix}-opening-help`} className="flex justify-between gap-3 text-xs text-muted-foreground">
                        <span>Describe the first useful outcome, not a full script.</span>
                        <span className="shrink-0 tabular-nums">{value.opening_objective.length}/500</span>
                    </p>
                </div>
                <div className="space-y-2">
                    <Label htmlFor={`${idPrefix}-decision-role`}>Decision-maker role</Label>
                    <Input
                        id={`${idPrefix}-decision-role`}
                        value={value.decision_maker_role}
                        onChange={(event) => update({ decision_maker_role: event.target.value })}
                        placeholder="Head of Operations"
                        maxLength={160}
                        disabled={disabled}
                    />
                    <p className="text-xs text-muted-foreground">
                        Leave blank when any informed contact can progress the call.
                    </p>
                </div>
            </div>

            <fieldset className="space-y-3">
                <legend className="text-sm font-medium text-foreground">Approved next actions</legend>
                <p className="text-xs text-muted-foreground">
                    Approval sets a boundary; the agent still waits for the corresponding tool to report success.
                </p>
                <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                    {CAMPAIGN_NEXT_ACTION_OPTIONS.map((option) => {
                        const checked = value.approved_next_actions.includes(option.value);
                        return (
                            <label
                                key={option.value}
                                className={`flex cursor-pointer items-start gap-3 rounded-xl border p-3 transition-colors ${
                                    checked
                                        ? "border-emerald-500/40 bg-emerald-500/10"
                                        : "border-border bg-background/60 hover:bg-muted/50"
                                }`}
                            >
                                <input
                                    type="checkbox"
                                    checked={checked}
                                    onChange={(event) => toggleAction(option.value, event.target.checked)}
                                    disabled={disabled}
                                    className="mt-0.5 h-4 w-4 rounded border-input accent-emerald-600"
                                />
                                <span>
                                    <span className="block text-sm font-medium text-foreground">{option.label}</span>
                                    <span className="mt-0.5 block text-xs leading-relaxed text-muted-foreground">
                                        {option.description}
                                    </span>
                                </span>
                            </label>
                        );
                    })}
                </div>
            </fieldset>

            <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                    <Label htmlFor={`${idPrefix}-transfer-destination`}>
                        Transfer destination {transferApproved ? <span className="text-destructive">*</span> : null}
                    </Label>
                    <div className="relative">
                        <Route className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" aria-hidden />
                        <Input
                            id={`${idPrefix}-transfer-destination`}
                            value={value.transfer_destination}
                            onChange={(event) => update({ transfer_destination: event.target.value })}
                            placeholder={transferApproved ? "Approved sales queue or destination reference" : "Enable Transfer to configure"}
                            maxLength={255}
                            required={transferApproved}
                            disabled={disabled || !transferApproved}
                            className="pl-9"
                        />
                    </div>
                    <p className="text-xs text-muted-foreground">
                        This records intent only. It never bypasses transfer policy or runtime availability.
                    </p>
                </div>
                <div className="space-y-2">
                    <Label htmlFor={`${idPrefix}-max-objections`}>Maximum objection attempts</Label>
                    <select
                        id={`${idPrefix}-max-objections`}
                        value={value.max_objection_attempts}
                        onChange={(event) => update({ max_objection_attempts: Number(event.target.value) })}
                        disabled={disabled}
                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                        {[1, 2, 3, 4, 5].map((attempts) => (
                            <option key={attempts} value={attempts}>
                                {attempts} {attempts === 1 ? "attempt" : "attempts"}
                            </option>
                        ))}
                    </select>
                    <p className="text-xs text-muted-foreground">
                        After this many genuine objections, the agent stops pushing and closes politely.
                    </p>
                </div>
            </div>

            <div className="rounded-xl border border-border bg-background/60 p-3">
                <div className="flex items-center gap-2 text-sm font-medium text-foreground">
                    <ShieldCheck className="h-4 w-4 text-emerald-500" aria-hidden />
                    Required lead fields
                </div>
                {requiredLeadFields.length ? (
                    <ul className="mt-2 flex flex-wrap gap-2" aria-label="Required lead fields in the campaign brief">
                        {requiredLeadFields.map((field) => (
                            <li key={field.field_key} className="rounded-full border border-border bg-muted px-2.5 py-1 text-xs text-foreground">
                                {field.label}
                            </li>
                        ))}
                    </ul>
                ) : (
                    <p className="mt-1 text-xs text-muted-foreground">
                        None marked required. Use the contact-details picker below to select and require fields.
                    </p>
                )}
            </div>

            {validation ? (
                <p className="text-sm text-destructive" role="alert">{validation}</p>
            ) : null}
        </section>
    );
}
