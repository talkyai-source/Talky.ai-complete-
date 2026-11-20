"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import type { Call } from "@/lib/dashboard-api";
import {
    CALL_HISTORY_LEAD_TYPES,
    defaultCallHistoryWorkflow,
    formatPhoneForDisplay,
    type CallHistoryLeadType,
    type CallHistoryWorkflowEntry,
} from "@/lib/call-history-workflow";
import type { CallWorkflowPatch } from "@/lib/use-call-history-workflow";
import { callerGaveProblem, correctCallerGave } from "@/lib/call-contact-correction";
import { formatTimestamp } from "@/lib/timestamps";
import { humanizeOutcome } from "@/lib/call-outcome-label";

type ColKey =
    | "date" | "direction" | "campaign" | "from" | "to" | "result" | "duration"
    | "leadType" | "notes" | "formContact" | "formInterest" | "formNextStep"
    | "emailGiven" | "numberGiven" | "recording" | "details";

const COLUMNS: ReadonlyArray<{ key: ColKey; header: string; editable?: boolean; max?: number; width: string }> = [
    { key: "date", header: "Date and time", width: "min-w-[13rem]" },
    { key: "direction", header: "Direction", width: "min-w-[6rem]" },
    { key: "campaign", header: "Campaign", width: "min-w-[10rem]" },
    { key: "from", header: "From", width: "min-w-[9rem]" },
    { key: "to", header: "To", width: "min-w-[9rem]" },
    { key: "result", header: "Result", width: "min-w-[7rem]" },
    { key: "duration", header: "Duration", width: "min-w-[5rem]" },
    { key: "leadType", header: "Lead type ✎", editable: true, width: "min-w-[8rem]" },
    { key: "notes", header: "Notes ✎", editable: true, max: 4000, width: "min-w-[14rem]" },
    { key: "formContact", header: "Follow-up: contact ✎", editable: true, max: 160, width: "min-w-[11rem]" },
    { key: "formInterest", header: "Follow-up: interest ✎", editable: true, max: 1000, width: "min-w-[12rem]" },
    { key: "formNextStep", header: "Follow-up: next step ✎", editable: true, max: 1000, width: "min-w-[12rem]" },
    { key: "emailGiven", header: "Email given ✎", editable: true, width: "min-w-[12rem]" },
    { key: "numberGiven", header: "Number given ✎", editable: true, width: "min-w-[10rem]" },
    { key: "recording", header: "Recording", width: "min-w-[6rem]" },
    { key: "details", header: "", width: "min-w-[5rem]" },
];

const LEAD_LABELS: Record<CallHistoryLeadType, string> = { cold: "Cold", warm: "Warm", hot: "Hot", follow_up: "Follow-up" };
const NOT_PROVIDED = "Not provided";

function formatDuration(seconds?: number) {
    if (!seconds) return "--";
    return `${Math.floor(seconds / 60)}:${(seconds % 60).toString().padStart(2, "0")}`;
}

type Move = "down" | "right" | "left" | "none";
type CellState = { value: string; base: string; status: "saving" | "saved" | "error"; message?: string };

export function CallHistoryTable({
    calls,
    workflow,
    timeZone,
    canEdit,
    onWorkflowChange,
    onContactCorrected,
    correctFn = correctCallerGave,
}: {
    calls: Call[];
    workflow: Record<string, CallHistoryWorkflowEntry | undefined>;
    timeZone?: string | null;
    canEdit: boolean;
    onWorkflowChange: (call: Call, patch: CallWorkflowPatch) => void;
    onContactCorrected: () => void;
    correctFn?: typeof correctCallerGave;
}) {
    const [active, setActive] = useState({ r: 0, c: 0 });
    const [editing, setEditing] = useState<{ r: number; c: number } | null>(null);
    const [draft, setDraft] = useState("");
    const [cells, setCells] = useState<Record<string, CellState>>({});
    const [focusTick, setFocusTick] = useState(0);
    const rootRef = useRef<HTMLDivElement | null>(null);
    const pendingFocus = useRef(false);
    const skipBlur = useRef(false);

    useEffect(() => {
        if (!pendingFocus.current) return;
        pendingFocus.current = false;
        rootRef.current?.querySelector<HTMLElement>(`[data-cell="${active.r}-${active.c}"]`)?.focus();
    }, [active, focusTick]);

    const refocus = () => { pendingFocus.current = true; setFocusTick((tick) => tick + 1); };
    const entryOf = (call: Call) => workflow[call.id] ?? defaultCallHistoryWorkflow(call);
    const rawContact = (call: Call, key: ColKey) => (key === "emailGiven" ? call.captured_email : call.captured_phone)?.trim() ?? "";
    const cellKey = (call: Call, key: ColKey) => `${call.id}:${key}`;
    const liveState = (call: Call, key: ColKey): CellState | undefined => {
        const state = cells[cellKey(call, key)];
        return state && state.base === rawContact(call, key) ? state : undefined;
    };
    const isEditable = (c: number) => canEdit && COLUMNS[c].editable === true;

    const textOf = (call: Call, key: ColKey): string => {
        const entry = entryOf(call);
        const inbound = call.direction === "inbound";
        switch (key) {
            case "date": return formatTimestamp(call.created_at, timeZone ?? undefined).label;
            case "direction": return inbound ? "Inbound" : "Outbound";
            case "campaign": return call.campaign_name || (inbound ? "Inbound calls" : "No campaign");
            case "from": return inbound ? call.from_number || "Private caller" : call.from_number || "not recorded";
            case "to": return inbound ? call.to_number || "assigned phone number" : call.phone_number;
            case "result": return call.outcome ? humanizeOutcome(call.outcome) : "--";
            case "duration": return formatDuration(call.duration_seconds);
            case "leadType": return LEAD_LABELS[entry.leadType] ?? LEAD_LABELS.warm;
            case "notes": return entry.notes;
            case "formContact": return entry.form.contact;
            case "formInterest": return entry.form.interest;
            case "formNextStep": return entry.form.nextStep;
            case "emailGiven": {
                const value = (liveState(call, key)?.value ?? rawContact(call, key)).trim();
                return value || NOT_PROVIDED;
            }
            case "numberGiven": {
                const value = (liveState(call, key)?.value ?? rawContact(call, key)).trim();
                return value ? formatPhoneForDisplay(value) : NOT_PROVIDED;
            }
            case "recording": return call.recording_id ? "Yes" : "No";
            default: return "";
        }
    };

    const editValueOf = (call: Call, key: ColKey): string => {
        const entry = entryOf(call);
        switch (key) {
            case "leadType": return entry.leadType;
            case "notes": return entry.notes;
            case "formContact": return entry.form.contact;
            case "formInterest": return entry.form.interest;
            case "formNextStep": return entry.form.nextStep;
            default: return liveState(call, key)?.value ?? rawContact(call, key);
        }
    };

    const startEdit = (r: number, c: number) => {
        if (!isEditable(c) || !calls[r]) return;
        skipBlur.current = false;
        setActive({ r, c });
        setDraft(editValueOf(calls[r], COLUMNS[c].key));
        setEditing({ r, c });
    };

    const nextEditable = (r: number, c: number, step: 1 | -1): { r: number; c: number } | null => {
        for (let col = c + step; col >= 0 && col < COLUMNS.length; col += step) if (isEditable(col)) return { r, c: col };
        const row = r + step;
        if (row < 0 || row >= calls.length) return null;
        for (let col = step === 1 ? 0 : COLUMNS.length - 1; col >= 0 && col < COLUMNS.length; col += step) if (isEditable(col)) return { r: row, c: col };
        return null;
    };

    const goTo = (r: number, c: number) => {
        setActive({ r, c });
        refocus();
    };

    const move = (r: number, c: number, how: Move) => {
        if (how === "down") goTo(Math.min(r + 1, calls.length - 1), c);
        else if (how === "right" || how === "left") {
            const target = nextEditable(r, c, how === "right" ? 1 : -1);
            goTo(target ? target.r : r, target ? target.c : c);
        } else goTo(r, c);
    };

    const setCell = (call: Call, key: ColKey, state: CellState | null) =>
        setCells((current) => {
            const next = { ...current };
            if (state) next[cellKey(call, key)] = state;
            else delete next[cellKey(call, key)];
            return next;
        });

    /** Returns nothing; leaves edit mode unless a bad value is typed by keyboard. */
    const commit = (r: number, c: number, value: string, how: Move, fromBlur: boolean) => {
        const call = calls[r];
        const key = COLUMNS[c].key;
        if (!call) return;
        skipBlur.current = true;
        const entry = entryOf(call);
        const finish = () => {
            setEditing(null);
            if (fromBlur) return;
            move(r, c, how);
        };
        if (key === "leadType") {
            if (value !== entry.leadType && (CALL_HISTORY_LEAD_TYPES as readonly string[]).includes(value)) {
                onWorkflowChange(call, { leadType: value as CallHistoryLeadType });
            }
            return finish();
        }
        if (key === "notes") {
            if (value !== entry.notes) onWorkflowChange(call, { notes: value });
            return finish();
        }
        if (key === "formContact" || key === "formInterest" || key === "formNextStep") {
            const field = key === "formContact" ? "contact" : key === "formInterest" ? "interest" : "nextStep";
            if (value !== entry.form[field]) {
                onWorkflowChange(call, { form: { ...entry.form, [field]: value, completed: entry.form.completed } });
            }
            return finish();
        }
        if (key === "emailGiven" || key === "numberGiven") {
            const kind = key === "emailGiven" ? "email" : "phone";
            const base = rawContact(call, key);
            const trimmed = value.trim();
            const live = liveState(call, key);
            if (trimmed === (live && live.status !== "error" ? live.value : base)) {
                setCell(call, key, null);
                return finish();
            }
            const problem = callerGaveProblem(kind, trimmed);
            if (problem) {
                setCell(call, key, { value, base, status: "error", message: problem });
                if (fromBlur) setEditing(null);
                else skipBlur.current = false;
                return;
            }
            setCell(call, key, { value: trimmed, base, status: "saving" });
            finish();
            void correctFn(call.id, kind, trimmed).then(
                () => {
                    setCell(call, key, { value: trimmed, base, status: "saved" });
                    onContactCorrected();
                },
                (error: unknown) => {
                    const reason = error instanceof Error ? error.message.trim() : "";
                    setCell(call, key, { value: trimmed, base, status: "error", message: `Not saved. Try again.${reason ? ` ${reason}` : ""}` });
                },
            );
        }
    };

    const cancelEdit = (call: Call, key: ColKey) => {
        skipBlur.current = true;
        if (key === "emailGiven" || key === "numberGiven") setCell(call, key, null);
        setEditing(null);
        refocus();
    };

    const onEditKeyDown = (event: React.KeyboardEvent, r: number, c: number) => {
        if (event.key === "Enter") { event.preventDefault(); commit(r, c, draft, "down", false); }
        else if (event.key === "Escape") { event.preventDefault(); cancelEdit(calls[r], COLUMNS[c].key); }
        else if (event.key === "Tab") {
            const target = nextEditable(r, c, event.shiftKey ? -1 : 1);
            if (target) event.preventDefault();
            commit(r, c, draft, event.shiftKey ? "left" : "right", !target);
        }
    };

    const onCellKeyDown = (event: React.KeyboardEvent<HTMLTableCellElement>, r: number, c: number) => {
        if (event.target !== event.currentTarget) return;
        const last = COLUMNS.length - 1;
        if (event.key === "ArrowDown") { event.preventDefault(); goTo(Math.min(r + 1, calls.length - 1), c); }
        else if (event.key === "ArrowUp") { event.preventDefault(); goTo(Math.max(r - 1, 0), c); }
        else if (event.key === "ArrowRight") { event.preventDefault(); goTo(r, Math.min(c + 1, last)); }
        else if (event.key === "ArrowLeft") { event.preventDefault(); goTo(r, Math.max(c - 1, 0)); }
        else if ((event.key === "Enter" || event.key === "F2") && isEditable(c)) { event.preventDefault(); startEdit(r, c); }
    };

    return (
        <div className="space-y-2" data-call-history-table>
            <p className="text-xs text-muted-foreground">
                {canEdit
                    ? "Click a white cell to edit it. Dates, numbers and results are the record of the call and cannot be changed."
                    : "You can view this table. Your role cannot edit call details."}
            </p>
            <div ref={rootRef} className="max-h-[70vh] max-w-full overflow-auto rounded-lg border border-border">
                <table className="w-max min-w-full border-separate border-spacing-0 text-xs">
                    <thead>
                        <tr>
                            {COLUMNS.map((col, c) => (
                                <th
                                    key={col.key}
                                    scope="col"
                                    className={`sticky top-0 h-10 whitespace-nowrap border-b border-r border-border bg-muted px-2 text-left font-semibold text-foreground ${col.width} ${c === 0 ? "left-0 z-30" : "z-20"}`}
                                >
                                    {col.header}
                                </th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {calls.map((call, r) => (
                            <tr key={call.id}>
                                {COLUMNS.map((col, c) => {
                                    const editable = isEditable(c);
                                    const isEditing = editing?.r === r && editing.c === c;
                                    const state = col.key === "emailGiven" || col.key === "numberGiven" ? liveState(call, col.key) : undefined;
                                    const text = textOf(call, col.key);
                                    const readBg = r % 2 === 0 ? "bg-muted/40" : "bg-muted/70";
                                    const bg = editable ? "bg-background" : readBg;
                                    const isActive = active.r === r && active.c === c;
                                    return (
                                        <td
                                            key={col.key}
                                            data-cell={`${r}-${c}`}
                                            data-editable={editable ? "true" : "false"}
                                            tabIndex={isActive ? 0 : -1}
                                            aria-busy={state?.status === "saving" ? true : undefined}
                                            onClick={() => { if (editable && !isEditing) startEdit(r, c); else setActive({ r, c }); }}
                                            onFocus={(event) => { if (event.target === event.currentTarget && !isActive) setActive({ r, c }); }}
                                            onKeyDown={(event) => onCellKeyDown(event, r, c)}
                                            className={`h-10 max-w-[22rem] border-b border-r border-border px-2 align-middle text-foreground focus:outline-none focus:ring-2 focus:ring-inset focus:ring-primary ${bg} ${col.width} ${c === 0 ? "sticky left-0 z-10 whitespace-nowrap" : ""}`}
                                        >
                                            {col.key === "details" ? (
                                                <Link href={`/calls/${call.id}`} className="font-semibold underline" tabIndex={isActive ? 0 : -1}>Details</Link>
                                            ) : isEditing ? (
                                                col.key === "leadType" ? (
                                                    <select
                                                        autoFocus
                                                        aria-label={`Lead type for ${call.phone_number || call.from_number || "call"}`}
                                                        value={draft}
                                                        onChange={(event) => { setDraft(event.target.value); commit(r, c, event.target.value, "none", false); }}
                                                        onKeyDown={(event) => onEditKeyDown(event, r, c)}
                                                        onBlur={() => { if (!skipBlur.current) commit(r, c, draft, "none", true); }}
                                                        className="h-8 w-full rounded border border-border bg-background px-1 text-xs"
                                                    >
                                                        {CALL_HISTORY_LEAD_TYPES.map((type) => <option key={type} value={type}>{LEAD_LABELS[type]}</option>)}
                                                    </select>
                                                ) : (
                                                    <input
                                                        autoFocus
                                                        type="text"
                                                        aria-label={`${col.header.replace(" ✎", "")} for ${call.phone_number || call.from_number || "call"}`}
                                                        value={draft}
                                                        maxLength={col.max}
                                                        onChange={(event) => setDraft(event.target.value)}
                                                        onFocus={(event) => event.currentTarget.select()}
                                                        onKeyDown={(event) => onEditKeyDown(event, r, c)}
                                                        onBlur={() => { if (!skipBlur.current) commit(r, c, draft, "none", true); }}
                                                        className="h-8 w-full rounded border border-ring bg-background px-1 text-xs"
                                                    />
                                                )
                                            ) : (
                                                <span className={`block truncate ${c === 0 ? "" : "max-w-[20rem]"} ${state?.status === "saving" ? "opacity-60" : ""}`} title={text}>
                                                    {text}
                                                    {state?.status === "saving" ? " (saving…)" : ""}
                                                </span>
                                            )}
                                            {state?.status === "error" && state.message ? (
                                                <span role="alert" className="block whitespace-normal text-[11px] leading-tight text-red-700 dark:text-red-300">{state.message}</span>
                                            ) : null}
                                        </td>
                                    );
                                })}
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
