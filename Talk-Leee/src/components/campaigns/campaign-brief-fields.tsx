"use client";

import { Plus, Route, ShieldCheck, Target, Trash2 } from "lucide-react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import {
    CAMPAIGN_NEXT_ACTION_OPTIONS,
    CAMPAIGN_TRANSFER_DAYS,
    campaignBriefInvalidFields,
    campaignBriefValidation,
    campaignTransferErrors,
    MAX_TRANSFER_DESTINATIONS,
    transferRowsForEditor,
    type CampaignTransferDay,
    type CampaignTransferDestination,
    type CampaignTransferHours,
    type CampaignTransferIfNoAnswer,
    type CampaignBriefDraft,
    type CampaignBriefInvalidField,
    type CampaignBriefLeadField,
    type CampaignNextAction,
    type CampaignFormField,
} from "@/lib/campaign-brief";
import { TZ_OPTIONS } from "@/components/campaigns/calling-schedule-editor";

const NO_ANSWER_OPTIONS: ReadonlyArray<{ value: CampaignTransferIfNoAnswer | ""; label: string; description?: string }> = [
    { value: "return_to_agent", label: "Stay with the caller", description: "The agent tells the caller nobody is available and offers to take a message." },
    { value: "end_call", label: "End the call", description: "The agent tells the caller nobody is available and ends the call politely." },
    { value: "", label: "Use the standard behaviour (stay with the caller)" },
];

// The same timezone list the calling-schedule editor offers.
const TRANSFER_TZ_OPTIONS = TZ_OPTIONS;

/** Shown on inbound campaigns: transfers there stay off until they are switched on platform-wide. */
export const INBOUND_TRANSFER_NOT_ON_TEXT =
    "Transfers on inbound campaigns are not switched on yet. You can save these settings now; the agent starts using them once inbound transfer is turned on.";

const TRANSFER_INPUT_CLASS =
    "flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";

function focusLater(id: string) {
    setTimeout(() => document.getElementById(id)?.focus(), 0);
}

export function CampaignBriefFields({
    value,
    onChange,
    requiredLeadFields,
    disabled = false,
    idPrefix,
    direction,
}: {
    value: CampaignBriefDraft;
    onChange: (value: CampaignBriefDraft) => void;
    requiredLeadFields: CampaignBriefLeadField[];
    disabled?: boolean;
    idPrefix: string;
    /** The campaign's direction, when the form knows it. Inbound gets an honest note about transfers. */
    direction?: "outbound" | "inbound";
}) {
    const validation = campaignBriefValidation(value);
    // The message at the foot of the brief is tied to the field it is about.
    const validationId = `${idPrefix}-validation-error`;
    const invalidFields = validation ? campaignBriefInvalidFields(value) : [];
    const inError = (field: CampaignBriefInvalidField) => invalidFields.includes(field);
    const errorTie = (field: CampaignBriefInvalidField) => (inError(field) ? { "aria-invalid": true, "aria-describedby": validationId } : {});
    const transferApproved = value.approved_next_actions.includes("transfer");

    function update(patch: Partial<CampaignBriefDraft>) {
        onChange({ ...value, ...patch });
    }

    const transferErrors = campaignTransferErrors(value);
    const rows = transferRowsForEditor(value);
    const hours = value.transfer_hours;
    const rowId = (index: number, field: string) => `${idPrefix}-transfer-${index}-${field}`;

    function setRows(next: CampaignTransferDestination[]) {
        update({ transfer_destinations: next });
    }

    function editRow(index: number, patch: Partial<CampaignTransferDestination>) {
        setRows(rows.map((row, position) => (position === index ? { ...row, ...patch } : row)));
    }

    function addRow() {
        if (rows.length >= MAX_TRANSFER_DESTINATIONS) return;
        setRows([...rows, { name: "", number: "", when: "" }]);
        focusLater(rowId(rows.length, "name"));
    }

    function removeRow(index: number) {
        if (rows.length <= 1) return;
        setRows(rows.filter((_, position) => position !== index));
        focusLater(rowId(Math.max(index - 1, 0), "name"));
    }

    function setHours(patch: Partial<CampaignTransferHours>) {
        if (hours) update({ transfer_hours: { ...hours, ...patch } });
    }

    function toggleHours(on: boolean) {
        if (!on) {
            update({ transfer_hours: undefined });
            return;
        }
        let zone = "UTC";
        try {
            zone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
        } catch {
            zone = "UTC";
        }
        update({ transfer_hours: { timezone: zone, days: ["mon", "tue", "wed", "thu", "fri"], start: "09:00", end: "17:00" } });
    }

    function toggleDay(day: CampaignTransferDay, checked: boolean) {
        if (!hours) return;
        setHours({ days: checked ? [...new Set([...hours.days, day])] : hours.days.filter((item) => item !== day) });
    }

    function toggleAction(action: CampaignNextAction, checked: boolean) {
        const next = checked
            ? [...value.approved_next_actions, action]
            : value.approved_next_actions.filter((candidate) => candidate !== action);
        update({
            approved_next_actions: [...new Set(next)],
            ...(action === "transfer" && !checked
                ? { transfer_destination: "", transfer_destinations: undefined, transfer_hours: undefined, transfer_ring_seconds: undefined, transfer_if_no_answer: undefined }
                : {}),
            ...(action === "send_email" && !checked ? { email_action: undefined } : {}),
            ...(action === "submit_form" && !checked ? { form_action: undefined } : {}),
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
                        aria-invalid={inError("opening_objective") || undefined}
                        aria-describedby={`${idPrefix}-opening-help${inError("opening_objective") ? ` ${validationId}` : ""}`}
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
                        {...errorTie("decision_maker_role")}
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
                                    {...(option.value === "transfer" ? errorTie("transfer_action") : {})}
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

            {value.approved_next_actions.includes("send_email") && (
                <fieldset className="space-y-3 rounded-xl border border-border bg-background/60 p-3">
                    <legend className="text-sm font-medium">Approved follow-up email</legend>
                    <p className="text-xs text-muted-foreground">Configure the exact message below and connect your email integration. The agent sends it only after confirming the caller&apos;s address and request.</p>
                    <Label htmlFor={`${idPrefix}-email-subject`}>Subject</Label>
                    <Input id={`${idPrefix}-email-subject`} maxLength={200} disabled={disabled} {...errorTie("email_subject")}
                        value={value.email_action?.subject ?? ""}
                        onChange={(event) => update({ email_action: { subject: event.target.value, body: value.email_action?.body ?? "" } })} />
                    <Label htmlFor={`${idPrefix}-email-body`}>Message</Label>
                    <textarea id={`${idPrefix}-email-body`} rows={4} maxLength={8000} disabled={disabled} {...errorTie("email_body")}
                        className="w-full rounded-md border border-input bg-background p-3 text-sm"
                        value={value.email_action?.body ?? ""}
                        onChange={(event) => update({ email_action: { subject: value.email_action?.subject ?? "", body: event.target.value } })} />
                    {!value.email_action && <p className="text-xs text-muted-foreground">Email sending stays unavailable until this message is configured.</p>}
                </fieldset>
            )}

            {value.approved_next_actions.includes("submit_form") && (
                <fieldset className="space-y-3 rounded-xl border border-border bg-background/60 p-3">
                    <legend className="text-sm font-medium">Form sent to your approved inbox</legend>
                    <p className="text-xs text-muted-foreground">Collected fields are emailed to this destination after the caller confirms submission. Your connected email account sends the form.</p>
                    {([ ["name", "Form name"], ["recipient", "Destination email"], ["subject", "Email subject"] ] as const).map(([key, label]) => (
                        <div key={key} className="space-y-1">
                            <Label htmlFor={`${idPrefix}-form-${key}`}>{label}</Label>
                            <Input id={`${idPrefix}-form-${key}`} type={key === "recipient" ? "email" : "text"}
                                disabled={disabled} maxLength={key === "name" ? 100 : key === "recipient" ? 254 : 200}
                                {...errorTie(`form_${key}`)}
                                value={value.form_action?.[key] ?? ""}
                                onChange={(event) => update({ form_action: {
                                    name: "", recipient: "", subject: "", fields: ["email"],
                                    ...value.form_action, [key]: event.target.value,
                                } })} />
                        </div>
                    ))}
                    <div className="flex flex-wrap gap-3" aria-label="Fields to submit">
                        {(["email", "phone", "follow_up", "project_type", "bidding_active"] as CampaignFormField[]).map((field) => (
                            <label key={field} className="flex items-center gap-2 text-xs">
                                <input type="checkbox" disabled={disabled} checked={(value.form_action?.fields ?? ["email"]).includes(field)}
                                    {...errorTie("form_fields")}
                                    onChange={(event) => {
                                        const current = value.form_action ?? { name: "", recipient: "", subject: "", fields: ["email"] as CampaignFormField[] };
                                        update({ form_action: { ...current, fields: event.target.checked ? [...current.fields, field] : current.fields.filter((item) => item !== field) } });
                                    }} />
                                {field.replaceAll("_", " ")}
                            </label>
                        ))}
                    </div>
                    {!value.form_action && <p className="text-xs text-muted-foreground">Form submission stays unavailable until its destination and fields are configured.</p>}
                </fieldset>
            )}

            {transferApproved && (
                <fieldset className="space-y-3 rounded-xl border border-border bg-background/60 p-3" disabled={disabled}>
                    <legend className="flex items-center gap-2 text-sm font-medium">
                        <Route className="h-4 w-4 text-muted-foreground" aria-hidden />
                        Who to transfer to
                    </legend>
                    <p className="text-xs text-muted-foreground">
                        The agent picks a destination by its name and your description. It never dials a number that is not on this list.
                    </p>
                    {rows.map((row, index) => {
                        const errors = transferErrors.rows[index] ?? {};
                        return (
                            <fieldset key={index} className="space-y-2 rounded-lg border border-border p-3">
                                <legend className="px-1 text-xs font-medium text-muted-foreground">{`Destination ${index + 1}`}</legend>
                                <div className="grid gap-3 sm:grid-cols-2">
                                    <div className="space-y-1">
                                        <Label htmlFor={rowId(index, "name")}>Name</Label>
                                        <Input
                                            id={rowId(index, "name")}
                                            value={row.name}
                                            placeholder="Sales"
                                            onChange={(event) => editRow(index, { name: event.target.value })}
                                            aria-invalid={errors.name ? true : undefined}
                                            aria-describedby={errors.name ? `${rowId(index, "name")}-error` : undefined}
                                        />
                                        {errors.name && <p id={`${rowId(index, "name")}-error`} className="text-xs text-destructive">{errors.name}</p>}
                                    </div>
                                    <div className="space-y-1">
                                        <Label htmlFor={rowId(index, "number")}>Phone number</Label>
                                        <Input
                                            id={rowId(index, "number")}
                                            value={row.number}
                                            placeholder="+442071234567"
                                            maxLength={255}
                                            onChange={(event) => editRow(index, { number: event.target.value })}
                                            aria-invalid={errors.number || (index === 0 && inError("transfer_destination")) ? true : undefined}
                                            aria-describedby={errors.number ? `${rowId(index, "number")}-error` : index === 0 && inError("transfer_destination") ? validationId : undefined}
                                        />
                                        {errors.number && <p id={`${rowId(index, "number")}-error`} className="text-xs text-destructive">{errors.number}</p>}
                                    </div>
                                </div>
                                <div className="space-y-1">
                                    <Label htmlFor={rowId(index, "when")}>When to transfer here</Label>
                                    <Input
                                        id={rowId(index, "when")}
                                        value={row.when}
                                        placeholder="The caller wants prices or wants to buy."
                                        onChange={(event) => editRow(index, { when: event.target.value })}
                                        aria-invalid={errors.when ? true : undefined}
                                        aria-describedby={errors.when ? `${rowId(index, "when")}-error` : undefined}
                                    />
                                    {errors.when && <p id={`${rowId(index, "when")}-error`} className="text-xs text-destructive">{errors.when}</p>}
                                </div>
                                {rows.length > 1 && (
                                    <button
                                        type="button"
                                        onClick={() => removeRow(index)}
                                        aria-label={`Remove destination ${index + 1}`}
                                        className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                    >
                                        <Trash2 className="h-3.5 w-3.5" aria-hidden />
                                        Remove
                                    </button>
                                )}
                            </fieldset>
                        );
                    })}
                    {rows.length < MAX_TRANSFER_DESTINATIONS && (
                        <button
                            type="button"
                            onClick={addRow}
                            className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-sm text-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        >
                            <Plus className="h-4 w-4" aria-hidden />
                            Add another destination
                        </button>
                    )}

                    <div className="space-y-3 border-t border-border pt-3">
                        <label className="flex items-center gap-2 text-sm font-medium text-foreground">
                            <input
                                type="checkbox"
                                role="switch"
                                checked={Boolean(hours)}
                                onChange={(event) => toggleHours(event.target.checked)}
                                className="h-4 w-4 rounded border-input accent-emerald-600"
                            />
                            Only during these hours
                        </label>
                        {hours && (
                            <div className="space-y-3">
                                <div className="space-y-1">
                                    <Label htmlFor={`${idPrefix}-transfer-timezone`}>Timezone</Label>
                                    <Select
                                        id={`${idPrefix}-transfer-timezone`}
                                        ariaLabel="Transfer timezone"
                                        value={hours.timezone}
                                        onChange={(next) => setHours({ timezone: next })}
                                        selectClassName="h-auto rounded-lg border-border px-3 py-2 pr-9 text-sm"
                                    >
                                        {!TRANSFER_TZ_OPTIONS.some((option) => option.value === hours.timezone) && (
                                            <option value={hours.timezone}>{hours.timezone}</option>
                                        )}
                                        {TRANSFER_TZ_OPTIONS.map((option) => (
                                            <option key={option.value} value={option.value}>{option.label}</option>
                                        ))}
                                    </Select>
                                </div>
                                <div role="group" aria-label="Transfer days" aria-describedby={transferErrors.days ? `${idPrefix}-transfer-days-error` : undefined} className="flex flex-wrap gap-3">
                                    {CAMPAIGN_TRANSFER_DAYS.map((day) => (
                                        <label key={day.value} className="flex items-center gap-1.5 text-sm">
                                            <input
                                                type="checkbox"
                                                aria-label={day.long}
                                                aria-invalid={transferErrors.days ? true : undefined}
                                                aria-describedby={transferErrors.days ? `${idPrefix}-transfer-days-error` : undefined}
                                                checked={hours.days.includes(day.value)}
                                                onChange={(event) => toggleDay(day.value, event.target.checked)}
                                                className="h-4 w-4 rounded border-input accent-emerald-600"
                                            />
                                            <span aria-hidden>{day.short}</span>
                                        </label>
                                    ))}
                                </div>
                                {transferErrors.days && <p id={`${idPrefix}-transfer-days-error`} className="text-xs text-destructive">{transferErrors.days}</p>}
                                <div className="grid gap-3 sm:grid-cols-2">
                                    <div className="space-y-1">
                                        <Label htmlFor={`${idPrefix}-transfer-start`}>Start time</Label>
                                        <input
                                            id={`${idPrefix}-transfer-start`}
                                            type="time"
                                            value={hours.start}
                                            onChange={(event) => setHours({ start: event.target.value })}
                                            aria-invalid={transferErrors.start ? true : undefined}
                                            aria-describedby={transferErrors.start ? `${idPrefix}-transfer-start-error` : undefined}
                                            className={TRANSFER_INPUT_CLASS}
                                        />
                                        {transferErrors.start && <p id={`${idPrefix}-transfer-start-error`} className="text-xs text-destructive">{transferErrors.start}</p>}
                                    </div>
                                    <div className="space-y-1">
                                        <Label htmlFor={`${idPrefix}-transfer-end`}>End time</Label>
                                        <input
                                            id={`${idPrefix}-transfer-end`}
                                            type="time"
                                            value={hours.end}
                                            onChange={(event) => setHours({ end: event.target.value })}
                                            aria-invalid={transferErrors.end ? true : undefined}
                                            aria-describedby={transferErrors.end ? `${idPrefix}-transfer-end-error` : undefined}
                                            className={TRANSFER_INPUT_CLASS}
                                        />
                                        {transferErrors.end && <p id={`${idPrefix}-transfer-end-error`} className="text-xs text-destructive">{transferErrors.end}</p>}
                                    </div>
                                </div>
                            </div>
                        )}
                        <p className="text-xs text-muted-foreground">
                            Outside these hours the agent tells the caller the team is unavailable and offers to take a message.
                        </p>
                    </div>

                    <div className="space-y-3 border-t border-border pt-3">
                        <p className="text-sm font-medium text-foreground">If nobody answers</p>
                        <div className="space-y-1">
                            <Label htmlFor={`${idPrefix}-transfer-ring`}>Ring for</Label>
                            <div className="flex items-center gap-2">
                                <input
                                    id={`${idPrefix}-transfer-ring`}
                                    type="number"
                                    inputMode="numeric"
                                    min={10}
                                    max={60}
                                    step={5}
                                    value={value.transfer_ring_seconds ?? ""}
                                    onChange={(event) => update({ transfer_ring_seconds: event.target.value })}
                                    aria-invalid={transferErrors.ring ? true : undefined}
                                    aria-describedby={`${idPrefix}-transfer-ring-help${transferErrors.ring ? ` ${idPrefix}-transfer-ring-error` : ""}`}
                                    className={`${TRANSFER_INPUT_CLASS} w-24`}
                                />
                                <span className="text-sm text-muted-foreground">seconds</span>
                            </div>
                            {transferErrors.ring && <p id={`${idPrefix}-transfer-ring-error`} className="text-xs text-destructive">{transferErrors.ring}</p>}
                            <p id={`${idPrefix}-transfer-ring-help`} className="text-xs text-muted-foreground">
                                How long the person&apos;s phone rings before the agent gives up. Leave empty for 30 seconds.
                            </p>
                        </div>
                        <div role="radiogroup" aria-label="Then" className="space-y-2">
                            <p className="text-sm font-medium text-foreground" aria-hidden>Then</p>
                            {NO_ANSWER_OPTIONS.map((option) => (
                                <div key={option.value || "standard"} className="flex items-start gap-2">
                                    <input
                                        id={`${idPrefix}-transfer-noanswer-${option.value || "standard"}`}
                                        type="radio"
                                        name={`${idPrefix}-transfer-noanswer`}
                                        checked={(value.transfer_if_no_answer ?? "") === option.value}
                                        onChange={() => update({ transfer_if_no_answer: option.value || undefined })}
                                        aria-describedby={option.description ? `${idPrefix}-transfer-noanswer-${option.value}-help` : undefined}
                                        className="mt-1 h-4 w-4 accent-emerald-600"
                                    />
                                    <div>
                                        <label htmlFor={`${idPrefix}-transfer-noanswer-${option.value || "standard"}`} className="text-sm text-foreground">
                                            {option.label}
                                        </label>
                                        {option.description && (
                                            <p id={`${idPrefix}-transfer-noanswer-${option.value}-help`} className="text-xs text-muted-foreground">{option.description}</p>
                                        )}
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                    <p className="text-xs text-muted-foreground">
                        The agent offers a transfer during a live call only when your phone line supports it, a destination on this list fits, and the call is inside your hours (if you set them). Otherwise it carries on without transferring.
                    </p>
                    {direction === "inbound" ? (
                        <p role="note" data-testid={`${idPrefix}-transfer-inbound-note`} className="text-xs font-medium text-foreground">
                            {INBOUND_TRANSFER_NOT_ON_TEXT}
                        </p>
                    ) : null}
                </fieldset>
            )}

            <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                    <Label htmlFor={`${idPrefix}-max-objections`}>Maximum objection attempts</Label>
                    <Select
                        id={`${idPrefix}-max-objections`}
                        ariaLabel="Maximum objection attempts"
                        value={String(value.max_objection_attempts)}
                        onChange={(next) => update({ max_objection_attempts: Number(next) })}
                        disabled={disabled}
                        ariaInvalid={inError("max_objection_attempts") || undefined}
                        ariaDescribedBy={inError("max_objection_attempts") ? validationId : undefined}
                    >
                        {[1, 2, 3, 4, 5].map((attempts) => (
                            <option key={attempts} value={attempts}>
                                {`${attempts} ${attempts === 1 ? "attempt" : "attempts"}`}
                            </option>
                        ))}
                    </Select>
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
                <p id={validationId} className="text-sm text-destructive" role="alert">{validation}</p>
            ) : null}
        </section>
    );
}
