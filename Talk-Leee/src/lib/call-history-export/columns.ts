import type { Call } from "@/lib/dashboard-api";
import {
    classifyCall,
    formatPhoneForDisplay,
    inferCallLeadType,
    type CallHistoryLeadType,
    type CallHistoryWorkflowEntry,
} from "@/lib/call-history-workflow";
import { humanizeOutcome } from "@/lib/call-outcome-label";

export type CallExportFormat = "xlsx" | "pdf";
export interface CallExportBrand { name: string; logoSrc: string }
export interface CallExportLogo { dataUrl: string; width: number; height: number }
export interface CallExportMeta {
    accountName?: string | null;
    timeZone?: string | null;
    filters: string[];
    generatedAt: Date;
    complete: boolean;
}
export interface CallExportProgress { phase: "notes" | "file"; done: number; total: number }
export interface CallExportRow { [columnKey: string]: string | number | null }

export const DEFAULT_BRAND: CallExportBrand = { name: "Talk-Lee", logoSrc: "/favicon.svg" };
export const NOT_COMPLETE_SENTENCE =
    "This file was made before every call had finished loading, so it may not hold all of them.";
export const NOT_PROVIDED = "Not provided";

export const CALL_EXPORT_COLUMNS: ReadonlyArray<{
    key: string;
    header: string;
    width: number;
    kind: "text" | "datetime" | "duration" | "number" | "yesno";
    wrap?: boolean;
    inPdfTable?: boolean;
}> = [
    { key: "date", header: "Date and time", width: 22, kind: "datetime", inPdfTable: true },
    { key: "direction", header: "Direction", width: 11, kind: "text", inPdfTable: true },
    { key: "campaign", header: "Campaign", width: 26, kind: "text", wrap: true, inPdfTable: true },
    { key: "from", header: "From", width: 18, kind: "text", inPdfTable: true },
    { key: "to", header: "To", width: 18, kind: "text", inPdfTable: true },
    { key: "result", header: "Result", width: 16, kind: "text", inPdfTable: true },
    { key: "answered", header: "Answered", width: 10, kind: "yesno" },
    { key: "duration", header: "Duration", width: 10, kind: "duration", inPdfTable: true },
    { key: "durationSeconds", header: "Duration in seconds", width: 12, kind: "number" },
    { key: "leadType", header: "Lead type", width: 12, kind: "text", inPdfTable: true },
    { key: "aiVerdict", header: "AI verdict", width: 34, kind: "text", wrap: true },
    { key: "emailGiven", header: "Email given", width: 30, kind: "text", wrap: true, inPdfTable: true },
    { key: "emailConfirmed", header: "Email confirmed", width: 12, kind: "yesno" },
    { key: "numberGiven", header: "Number given", width: 20, kind: "text", inPdfTable: true },
    { key: "numberConfirmed", header: "Number confirmed", width: 12, kind: "yesno" },
    { key: "bestTime", header: "Best time to call", width: 20, kind: "text", wrap: true },
    { key: "summary", header: "AI summary", width: 60, kind: "text", wrap: true },
    { key: "notes", header: "Notes", width: 50, kind: "text", wrap: true },
    { key: "formContact", header: "Form: contact", width: 24, kind: "text", wrap: true },
    { key: "formInterest", header: "Form: interest", width: 34, kind: "text", wrap: true },
    { key: "formNextStep", header: "Form: next step", width: 34, kind: "text", wrap: true },
    { key: "recording", header: "Recording", width: 10, kind: "yesno" },
    { key: "voiceNote", header: "Voice note left", width: 12, kind: "yesno" },
    // Extra fields the list carries that a person may want in a file.
    { key: "status", header: "Call status", width: 14, kind: "text" },
    { key: "consent", header: "Consent", width: 14, kind: "text" },
    { key: "recordingStatus", header: "Recording status", width: 16, kind: "text" },
    { key: "billingStatus", header: "Billing status", width: 16, kind: "text" },
    { key: "callId", header: "Call ID", width: 38, kind: "text" },
];

const LEAD_TYPE_LABELS: Record<CallHistoryLeadType, string> = {
    cold: "Cold",
    warm: "Warm",
    hot: "Hot",
    follow_up: "Follow-up",
};

/** Characters XML (and so xlsx) cannot hold, plus lone control codes. */
const CONTROL_CHARS = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g;

export function cleanText(value: unknown): string {
    if (value === null || value === undefined) return "";
    return String(value).replace(CONTROL_CHARS, "").trim();
}

/** A valid IANA zone, or "UTC". */
export function resolveZone(timeZone?: string | null): string {
    const zone = (timeZone ?? "").trim();
    if (!zone) return "UTC";
    try {
        new Intl.DateTimeFormat("en-US", { timeZone: zone });
        return zone;
    } catch {
        return "UTC";
    }
}

/** "2026-11-21 14:05:09" in the given zone, whatever the process zone is. */
export function formatInZone(value: Date | string | null | undefined, timeZone?: string | null): string {
    if (value === null || value === undefined || value === "") return "";
    const date = value instanceof Date ? value : new Date(value);
    if (!Number.isFinite(date.getTime())) return "";
    const parts = new Intl.DateTimeFormat("en-US", {
        timeZone: resolveZone(timeZone),
        year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", second: "2-digit",
        hourCycle: "h23",
    }).formatToParts(date);
    const get = (type: string) => parts.find((part) => part.type === type)?.value ?? "00";
    const hour = get("hour") === "24" ? "00" : get("hour");
    return `${get("year")}-${get("month")}-${get("day")} ${hour}:${get("minute")}:${get("second")}`;
}

/** h:mm:ss (hours are not capped, so 25 hours reads 25:00:00). */
export function formatHms(totalSeconds: number): string {
    const safe = Math.max(0, Math.round(Number.isFinite(totalSeconds) ? totalSeconds : 0));
    const h = Math.floor(safe / 3600);
    const m = Math.floor((safe % 3600) / 60);
    const s = safe % 60;
    return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function titleCase(value: string): string {
    return value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/** "qualified | wants a quote" -> "Qualified - wants a quote". */
export function humanizeVerdict(value: string | null | undefined): string {
    const raw = cleanText(value);
    if (!raw) return "";
    const [head, ...rest] = raw.split("|");
    const label = titleCase(head.trim().toLowerCase());
    const detail = rest.join("|").trim();
    return detail ? `${label} - ${detail}` : label;
}

function yesNo(value: boolean): "Yes" | "No" {
    return value ? "Yes" : "No";
}

function givenText(value: string | null | undefined, format?: (v: string) => string): string {
    const text = cleanText(value);
    return text ? (format ? format(text) : text) : NOT_PROVIDED;
}

function confirmedText(value: string | null | undefined, confirmed: boolean | null | undefined): string {
    if (!cleanText(value)) return "";
    return yesNo(confirmed !== false);
}

function statusLabel(value: string | null | undefined): string {
    const text = cleanText(value);
    return text ? titleCase(text.toLowerCase()) : "";
}

function phone(value: string | null | undefined): string {
    const text = cleanText(value);
    return text ? formatPhoneForDisplay(text) : "";
}

export function buildExportRows(
    calls: Call[],
    workflow: Record<string, CallHistoryWorkflowEntry | undefined>,
    timeZone?: string | null,
): CallExportRow[] {
    return calls.map((call) => {
        const entry = workflow[call.id];
        const inbound = call.direction === "inbound";
        const { answered, failed } = classifyCall(call);
        const leadType = entry?.leadType ?? inferCallLeadType(call);
        const seconds = typeof call.duration_seconds === "number" && Number.isFinite(call.duration_seconds)
            ? Math.max(0, Math.round(call.duration_seconds))
            : null;
        const outcomeLabel = call.outcome ? humanizeOutcome(call.outcome) : statusLabel(call.status);
        return {
            date: formatInZone(call.created_at, timeZone),
            direction: inbound ? "Incoming" : "Outbound",
            campaign: cleanText(call.campaign_name),
            from: phone(call.from_number ?? call.caller_ani ?? (inbound ? call.phone_number : null)),
            to: phone(call.to_number ?? call.called_did ?? (inbound ? null : call.phone_number)),
            result: cleanText(outcomeLabel),
            answered: yesNo(answered),
            duration: seconds === null ? "" : formatHms(seconds),
            durationSeconds: seconds,
            leadType: LEAD_TYPE_LABELS[leadType] ?? LEAD_TYPE_LABELS.warm,
            aiVerdict: humanizeVerdict(call.lead_outcome),
            emailGiven: givenText(call.captured_email),
            emailConfirmed: confirmedText(call.captured_email, call.captured_email_confirmed),
            numberGiven: givenText(call.captured_phone, formatPhoneForDisplay),
            numberConfirmed: confirmedText(call.captured_phone, call.captured_phone_confirmed),
            bestTime: cleanText(call.best_time_to_call),
            summary: cleanText(call.summary),
            notes: cleanText(entry?.notes),
            formContact: cleanText(entry?.form?.contact),
            formInterest: cleanText(entry?.form?.interest),
            formNextStep: cleanText(entry?.form?.nextStep),
            recording: yesNo(Boolean(call.recording_id)),
            voiceNote: yesNo(call.has_feedback === true),
            status: statusLabel(call.status),
            consent: statusLabel(call.consent_status),
            recordingStatus: statusLabel(call.recording_status),
            billingStatus: statusLabel(call.billing_status),
            callId: call.id,
            // Not a column: summarizeExport needs the answer classifyCall gave.
            failed: yesNo(failed),
        };
    });
}

function tally(rows: CallExportRow[], pick: (row: CallExportRow) => string): Array<[string, number]> {
    const counts = new Map<string, number>();
    for (const row of rows) {
        const key = pick(row);
        counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

export function summarizeExport(rows: CallExportRow[]) {
    let answered = 0;
    let failed = 0;
    let talkSeconds = 0;
    for (const row of rows) {
        if (row.answered === "Yes") answered += 1;
        if (row.failed === "Yes") failed += 1;
        if (typeof row.durationSeconds === "number") talkSeconds += row.durationSeconds;
    }
    return {
        total: rows.length,
        answered,
        failed,
        talkSeconds,
        byResult: tally(rows, (row) => cleanText(row.result) || "(no result)"),
        byLeadType: tally(rows, (row) => cleanText(row.leadType) || "(none)"),
        byCampaign: tally(rows, (row) => cleanText(row.campaign) || "(no campaign)"),
    };
}

export function exportFileName(format: CallExportFormat, now: Date, callId?: string): string {
    const day = now.toISOString().slice(0, 10);
    const id = callId ? cleanText(callId).replace(/[^A-Za-z0-9]/g, "").slice(0, 8) : "";
    return id ? `talk-lee-call-${id}-${day}.${format}` : `talk-lee-call-history-${day}.${format}`;
}

export function filtersLine(meta: Pick<CallExportMeta, "filters">): string {
    const filters = meta.filters.map(cleanText).filter(Boolean);
    return filters.length ? `Filters: ${filters.join("; ")}` : "All calls";
}

export function generatedLine(meta: Pick<CallExportMeta, "generatedAt" | "timeZone">): string {
    return `Generated: ${formatInZone(meta.generatedAt, meta.timeZone)} (${resolveZone(meta.timeZone)})`;
}
