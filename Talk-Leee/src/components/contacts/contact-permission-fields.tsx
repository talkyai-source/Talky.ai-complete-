"use client";

/**
 * "Permission to call these contacts" — the one block every screen that adds
 * contacts shows (CSV upload, paste, single add, Salesforce import, and the
 * Confirm permission dialog on a list). Controlled: the screen owns the draft
 * and decides when a submit attempt has happened (`showErrors`). A field also
 * shows its own error after the person leaves it. All wording lives in
 * CONTACT_PERMISSION_TEXT.
 */

import { useState } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    CONTACT_PERMISSION_BASES,
    CONTACT_PERMISSION_TEXT,
    SOURCE_MAX_LENGTH,
    contactPermissionErrors,
    todayUtc,
    type ContactPermissionDraft,
    type ContactPermissionField,
    type ContactPermissionServerError,
} from "@/lib/contact-permission";

export function ContactPermissionFields({
    idPrefix,
    value,
    onChange,
    showErrors = false,
    serverError = null,
    disabled = false,
}: {
    /** Unique per place the block is shown, so ids never collide. */
    idPrefix: string;
    value: ContactPermissionDraft;
    onChange: (next: ContactPermissionDraft) => void;
    /** True once the person has tried to submit. */
    showErrors?: boolean;
    /** A 422 `contact_permission_*` refusal from the backend. */
    serverError?: ContactPermissionServerError | null;
    disabled?: boolean;
}) {
    const [touched, setTouched] = useState<Partial<Record<ContactPermissionField, boolean>>>({});
    const touch = (field: ContactPermissionField) => setTouched((prev) => (prev[field] ? prev : { ...prev, [field]: true }));

    const local = contactPermissionErrors(value);
    const errorFor = (field: ContactPermissionField): string | undefined => {
        if (field !== "confirmed" && serverError?.field === field) return serverError.message;
        return showErrors || touched[field] ? local[field] : undefined;
    };
    const errors = {
        basis: errorFor("basis"),
        source: errorFor("source"),
        permission_date: errorFor("permission_date"),
        confirmed: errorFor("confirmed"),
    };
    const id = (part: string) => `${idPrefix}-permission-${part}`;
    const describedBy = (part: string, error: string | undefined, extra?: string) =>
        [extra, error ? id(`${part}-error`) : undefined].filter(Boolean).join(" ") || undefined;
    const errorClass = "mt-1 text-xs text-destructive";
    const text = CONTACT_PERMISSION_TEXT;

    return (
        <section aria-labelledby={id("heading")} className="space-y-4 rounded-2xl border border-border bg-muted/60 p-4 shadow-sm">
            <div>
                <h4 id={id("heading")} className="text-sm font-semibold text-foreground">{text.heading}</h4>
                <p className="mt-1 text-xs text-muted-foreground">{text.intro}</p>
            </div>

            <fieldset
                role="radiogroup"
                aria-invalid={errors.basis ? true : false}
                aria-describedby={describedBy("basis", errors.basis)}
                disabled={disabled}
                className="space-y-2"
            >
                <legend className="text-sm font-medium text-foreground">{text.basisLabel}</legend>
                {CONTACT_PERMISSION_BASES.map((option) => (
                    <div key={option.value} className="flex items-center gap-2">
                        <input
                            id={id(`basis-${option.value}`)}
                            type="radio"
                            name={id("basis")}
                            value={option.value}
                            checked={value.basis === option.value}
                            onChange={() => onChange({ ...value, basis: option.value })}
                            onBlur={() => touch("basis")}
                            className="h-4 w-4"
                        />
                        <label htmlFor={id(`basis-${option.value}`)} className="text-sm text-foreground">{option.label}</label>
                    </div>
                ))}
                {errors.basis ? <p id={id("basis-error")} className={errorClass}>{errors.basis}</p> : null}
            </fieldset>

            <div>
                <Label htmlFor={id("source")}>{text.sourceLabel}</Label>
                <Input
                    id={id("source")}
                    className="mt-1"
                    value={value.source}
                    maxLength={SOURCE_MAX_LENGTH + 100}
                    placeholder={text.sourcePlaceholder}
                    onChange={(e) => onChange({ ...value, source: e.target.value })}
                    onBlur={() => touch("source")}
                    aria-invalid={errors.source ? true : false}
                    aria-describedby={describedBy("source", errors.source)}
                    disabled={disabled}
                />
                {errors.source ? <p id={id("source-error")} className={errorClass}>{errors.source}</p> : null}
            </div>

            <div>
                <Label htmlFor={id("date")}>{text.dateLabel}</Label>
                <Input
                    id={id("date")}
                    className="mt-1"
                    type="date"
                    max={todayUtc()}
                    value={value.permission_date}
                    onChange={(e) => onChange({ ...value, permission_date: e.target.value })}
                    onBlur={() => touch("permission_date")}
                    aria-invalid={errors.permission_date ? true : false}
                    aria-describedby={describedBy("date", errors.permission_date, id("date-help"))}
                    disabled={disabled}
                />
                <p id={id("date-help")} className="mt-1 text-xs text-muted-foreground">{text.dateHelp}</p>
                {errors.permission_date ? <p id={id("date-error")} className={errorClass}>{errors.permission_date}</p> : null}
            </div>

            <div>
                <div className="flex items-start gap-2">
                    <input
                        id={id("confirm")}
                        type="checkbox"
                        checked={value.confirmed}
                        onChange={(e) => onChange({ ...value, confirmed: e.target.checked })}
                        onBlur={() => touch("confirmed")}
                        aria-invalid={errors.confirmed ? true : false}
                        aria-describedby={describedBy("confirm", errors.confirmed)}
                        disabled={disabled}
                        className="mt-0.5 h-4 w-4"
                    />
                    <label htmlFor={id("confirm")} className="text-sm text-foreground">{text.confirmLabel}</label>
                </div>
                {errors.confirmed ? <p id={id("confirm-error")} className={errorClass}>{errors.confirmed}</p> : null}
            </div>

            {serverError && serverError.field === null ? (
                <p role="alert" className="text-sm text-destructive">{serverError.message}</p>
            ) : null}
        </section>
    );
}
