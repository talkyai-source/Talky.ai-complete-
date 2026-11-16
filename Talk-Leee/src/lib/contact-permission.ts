/**
 * Permission to call imported contacts: the confirmation every route that adds
 * contacts requires (basis, source, date, statement version), the same rules
 * the backend applies, and the reading of its 422 refusals.
 */

import { isApiClientError } from "@/lib/http-client";

/** Must equal the backend constant; bump together with the wording in CONTACT_PERMISSION_TEXT. */
export const CONTACT_PERMISSION_STATEMENT_VERSION = "2026-11-v1";

export type ContactPermissionBasis =
    | "express_consent"
    | "existing_customer"
    | "made_an_enquiry"
    | "other_documented";

/** What is sent to the backend. */
export interface ContactPermission {
    basis: ContactPermissionBasis;
    source: string;
    permission_date: string;
    statement_version: string;
}

/** What a list reports back. Absent on a backend that does not send it yet. */
export interface ContactListPermission {
    confirmed: boolean;
    basis: string | null;
    source: string | null;
    permission_date: string | null;
    confirmed_at: string | null;
}

/** What the person is typing. `basis` is "" until one is chosen. */
export interface ContactPermissionDraft {
    basis: ContactPermissionBasis | "";
    source: string;
    permission_date: string;
    confirmed: boolean;
}

export type ContactPermissionField = "basis" | "source" | "permission_date" | "confirmed";

export const CONTACT_PERMISSION_BASES: ReadonlyArray<{ value: ContactPermissionBasis; label: string }> = [
    { value: "express_consent", label: "They agreed to be called" },
    { value: "existing_customer", label: "They are existing customers" },
    { value: "made_an_enquiry", label: "They asked us to contact them" },
    { value: "other_documented", label: "Another documented reason" },
];

/** All wording for the permission block, in one place (the legal check may change it). */
export const CONTACT_PERMISSION_TEXT = {
    heading: "Permission to call these contacts",
    intro: "We can only call people you are allowed to contact. Tell us where that permission comes from. We keep this with the list.",
    basisLabel: "Why you can call them",
    sourceLabel: "Where this permission comes from",
    sourcePlaceholder: "For example: website sign-up form, March 2026",
    dateLabel: "Date permission was given",
    dateHelp: "If it varies, use the most recent date.",
    confirmLabel: "I confirm that I am allowed to call the people in this list for this purpose, and that I can show a record of this if asked.",
    errors: {
        basis: "Choose why you can call them.",
        sourceShort: "Describe where this permission comes from (at least 5 characters).",
        sourceLong: "Keep this to 500 characters or fewer.",
        dateMissing: "Choose the date permission was given.",
        dateFuture: "This date cannot be in the future.",
        confirmed: "Tick this box to confirm.",
    },
} as const;

export const SOURCE_MIN_LENGTH = 5;
export const SOURCE_MAX_LENGTH = 500;

export function emptyContactPermissionDraft(): ContactPermissionDraft {
    return { basis: "", source: "", permission_date: "", confirmed: false };
}

export function contactPermissionBasisLabel(basis: string): string {
    return CONTACT_PERMISSION_BASES.find((b) => b.value === basis)?.label ?? basis;
}

/** Today as YYYY-MM-DD in UTC (the backend compares against UTC today). */
export function todayUtc(now: Date = new Date()): string {
    return now.toISOString().slice(0, 10);
}

function isRealDate(value: string): boolean {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    const parsed = new Date(`${value}T00:00:00Z`);
    return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

/** The same rules as the backend, one message per failing part. */
export function contactPermissionErrors(
    draft: ContactPermissionDraft,
    today: string = todayUtc(),
): Partial<Record<ContactPermissionField, string>> {
    const errors: Partial<Record<ContactPermissionField, string>> = {};
    const text = CONTACT_PERMISSION_TEXT.errors;
    if (!CONTACT_PERMISSION_BASES.some((b) => b.value === draft.basis)) errors.basis = text.basis;
    const source = draft.source.trim();
    if (source.length < SOURCE_MIN_LENGTH) errors.source = text.sourceShort;
    else if (source.length > SOURCE_MAX_LENGTH) errors.source = text.sourceLong;
    if (!isRealDate(draft.permission_date)) errors.permission_date = text.dateMissing;
    else if (draft.permission_date > today) errors.permission_date = text.dateFuture;
    if (!draft.confirmed) errors.confirmed = text.confirmed;
    return errors;
}

/** The object to send, or null while the draft is not valid. */
export function buildContactPermission(
    draft: ContactPermissionDraft,
    today?: string,
): ContactPermission | null {
    if (Object.keys(contactPermissionErrors(draft, today)).length > 0) return null;
    return {
        basis: draft.basis as ContactPermissionBasis,
        source: draft.source.trim(),
        permission_date: draft.permission_date,
        statement_version: CONTACT_PERMISSION_STATEMENT_VERSION,
    };
}

/** The four form fields the multipart routes take. */
export function contactPermissionFormFields(permission: ContactPermission): Record<string, string> {
    return {
        permission_basis: permission.basis,
        permission_source: permission.source,
        permission_date: permission.permission_date,
        permission_statement_version: permission.statement_version,
    };
}

export interface ContactPermissionServerError {
    /** The part of the block the message belongs to, or null for the block as a whole. */
    field: "basis" | "source" | "permission_date" | null;
    message: string;
}

function asRecord(value: unknown): Record<string, unknown> | null {
    return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

/** Reads a 422 `contact_permission_required` / `contact_permission_invalid` refusal; null for any other error. */
export function contactPermissionServerError(err: unknown): ContactPermissionServerError | null {
    if (!isApiClientError(err)) return null;
    const outer = asRecord(err.details);
    const inner = asRecord(outer?.detail) ?? outer;
    const code = typeof inner?.code === "string" ? inner.code : err.code;
    if (code !== "contact_permission_required" && code !== "contact_permission_invalid") return null;
    const message = typeof inner?.message === "string" && inner.message ? inner.message : err.message;
    const field = inner?.field;
    if (code === "contact_permission_invalid" && (field === "basis" || field === "source" || field === "permission_date")) {
        return { field, message };
    }
    return { field: null, message };
}
