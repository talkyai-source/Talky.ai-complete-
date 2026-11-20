"use client";

import { useEffect, useRef, useState } from "react";
import { Download, Loader2 } from "lucide-react";
import type { Call } from "@/lib/dashboard-api";
import type { CallHistoryWorkflowEntry } from "@/lib/call-history-workflow";
import { exportCallHistory } from "@/lib/call-history-export";

type Format = "xlsx" | "pdf";
type Phase = { kind: "idle" } | { kind: "running"; text: string } | { kind: "done"; text: string } | { kind: "error"; message: string; format: Format };

const FORMATS: Array<{ format: Format; label: string }> = [
    { format: "xlsx", label: "Excel (.xlsx)" },
    { format: "pdf", label: "PDF (.pdf)" },
];

/** The filters in force, in plain words, for the file's header. */
export function describeCallHistoryFilters(args: {
    direction: "all" | "inbound" | "outbound";
    filter: "all" | "leads" | "issues";
    search: string;
    did?: string;
    campaignName?: string;
}): string[] {
    const sentences: string[] = [];
    if (args.direction !== "all") sentences.push(`Direction: ${args.direction === "inbound" ? "incoming" : "outbound"}`);
    if (args.filter !== "all") sentences.push(`Showing: ${args.filter} only`);
    if (args.search.trim()) sentences.push(`Search: ${args.search.trim()}`);
    if (args.did) sentences.push(`Phone number: ${args.did}`);
    if (args.campaignName) sentences.push(`Campaign: ${args.campaignName}`);
    return sentences;
}

function isAbort(error: unknown): boolean {
    return typeof error === "object" && error !== null && (error as { name?: string }).name === "AbortError";
}

export function CallHistoryDownload({
    calls,
    complete,
    knownWorkflow,
    filters,
    timeZone,
    accountName,
    brand,
    exportFn = exportCallHistory,
}: {
    calls: Call[];
    complete: boolean;
    knownWorkflow: Record<string, CallHistoryWorkflowEntry | undefined>;
    filters: string[];
    timeZone?: string | null;
    accountName?: string | null;
    brand: { name: string; logoSrc: string };
    exportFn?: typeof exportCallHistory;
}) {
    const [menuOpen, setMenuOpen] = useState(false);
    const [phase, setPhase] = useState<Phase>({ kind: "idle" });
    const [announce, setAnnounce] = useState("");
    const controllerRef = useRef<AbortController | null>(null);
    const runningRef = useRef(false);
    const lastBucketRef = useRef(-1);
    const buttonRef = useRef<HTMLButtonElement | null>(null);
    const rootRef = useRef<HTMLDivElement | null>(null);
    const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);
    const focusItemRef = useRef<number | null>(null);
    const [focusTick, setFocusTick] = useState(0);

    useEffect(() => () => controllerRef.current?.abort(), []);

    useEffect(() => {
        if (!menuOpen) return;
        const onPointer = (event: MouseEvent) => {
            if (rootRef.current && !rootRef.current.contains(event.target as Node)) setMenuOpen(false);
        };
        document.addEventListener("mousedown", onPointer);
        return () => document.removeEventListener("mousedown", onPointer);
    }, [menuOpen]);

    useEffect(() => {
        const index = focusItemRef.current;
        if (menuOpen && index !== null) {
            focusItemRef.current = null;
            itemRefs.current[index]?.focus();
        }
    }, [menuOpen, focusTick]);

    const running = phase.kind === "running";
    const loading = !complete;
    const empty = calls.length === 0;

    const openMenu = (index: number) => {
        focusItemRef.current = index;
        setPhase((current) => (current.kind === "done" ? { kind: "idle" } : current));
        setMenuOpen(true);
        setFocusTick((tick) => tick + 1);
    };
    const closeMenu = (returnFocus: boolean) => {
        setMenuOpen(false);
        if (returnFocus) buttonRef.current?.focus();
    };

    const start = async (format: Format) => {
        if (runningRef.current) return;
        runningRef.current = true;
        const controller = new AbortController();
        controllerRef.current = controller;
        lastBucketRef.current = -1;
        setMenuOpen(false);
        setAnnounce("");
        setPhase({ kind: "running", text: "Reading notes" });
        try {
            const result = await exportFn({
                format,
                calls,
                knownWorkflow,
                meta: { accountName, timeZone, filters, complete },
                brand,
                signal: controller.signal,
                onProgress: (progress) => {
                    if (controller.signal.aborted) return;
                    if (progress.phase === "file") {
                        setPhase({ kind: "running", text: "Writing the file" });
                        if (lastBucketRef.current !== 100) {
                            lastBucketRef.current = 100;
                            setAnnounce("Writing the file");
                        }
                        return;
                    }
                    const text = `Reading notes ${progress.done.toLocaleString("en-US")} of ${progress.total.toLocaleString("en-US")}`;
                    setPhase({ kind: "running", text });
                    const bucket = progress.total > 0 ? Math.floor((progress.done / progress.total) * 10) : 0;
                    if (bucket !== lastBucketRef.current) {
                        lastBucketRef.current = bucket;
                        setAnnounce(text);
                    }
                },
            });
            setPhase({ kind: "done", text: `Downloaded ${result.fileName} (${result.calls.toLocaleString("en-US")} calls)` });
        } catch (error) {
            if (isAbort(error) || controller.signal.aborted) setPhase({ kind: "idle" });
            else {
                const reason = error instanceof Error ? error.message.trim() : "";
                setPhase({ kind: "error", message: reason && reason.length <= 200 ? reason : "", format });
            }
        } finally {
            runningRef.current = false;
            controllerRef.current = null;
        }
    };

    const onButtonKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
        if (event.key === "ArrowDown") { event.preventDefault(); openMenu(0); }
        else if (event.key === "ArrowUp") { event.preventDefault(); openMenu(FORMATS.length - 1); }
        else if (event.key === "Escape" && menuOpen) { event.preventDefault(); closeMenu(true); }
    };

    const onMenuKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
        const current = itemRefs.current.findIndex((item) => item === document.activeElement);
        if (event.key === "ArrowDown") { event.preventDefault(); itemRefs.current[(current + 1) % FORMATS.length]?.focus(); }
        else if (event.key === "ArrowUp") { event.preventDefault(); itemRefs.current[(current - 1 + FORMATS.length) % FORMATS.length]?.focus(); }
        else if (event.key === "Home") { event.preventDefault(); itemRefs.current[0]?.focus(); }
        else if (event.key === "End") { event.preventDefault(); itemRefs.current[FORMATS.length - 1]?.focus(); }
        else if (event.key === "Escape") { event.preventDefault(); closeMenu(true); }
        else if (event.key === "Tab") setMenuOpen(false);
    };

    const label = loading ? "Loading every call…" : running ? (phase.kind === "running" ? phase.text : "") : "Download";

    return (
        <div ref={rootRef} className="relative flex flex-col items-start gap-1 sm:items-end" data-call-history-download>
            <div className="flex items-center gap-2">
                <button
                    ref={buttonRef}
                    type="button"
                    aria-haspopup="menu"
                    aria-expanded={menuOpen}
                    disabled={loading || empty || running}
                    onClick={() => (menuOpen ? closeMenu(false) : openMenu(0))}
                    onKeyDown={onButtonKeyDown}
                    className="inline-flex items-center gap-2 rounded-lg border border-border bg-background px-3 py-2 text-xs font-semibold text-foreground transition-colors hover:bg-accent hover:text-accent-foreground disabled:cursor-not-allowed disabled:opacity-60"
                >
                    {running ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Download className="h-4 w-4" aria-hidden />}
                    {label}
                </button>
                {running ? (
                    <button
                        type="button"
                        onClick={() => controllerRef.current?.abort()}
                        className="rounded-lg border border-border bg-background px-3 py-2 text-xs font-semibold text-foreground hover:bg-accent"
                    >
                        Cancel
                    </button>
                ) : null}
            </div>
            {menuOpen ? (
                <div
                    role="menu"
                    tabIndex={-1}
                    aria-label="Download call history"
                    onKeyDown={onMenuKeyDown}
                    className="absolute right-0 top-full z-30 mt-1 min-w-[11rem] rounded-lg border border-border bg-background p-1 shadow-lg"
                >
                    {FORMATS.map((item, index) => (
                        <button
                            key={item.format}
                            ref={(el) => { itemRefs.current[index] = el; }}
                            type="button"
                            role="menuitem"
                            tabIndex={-1}
                            onClick={() => void start(item.format)}
                            className="block w-full rounded-md px-3 py-2 text-left text-xs font-semibold text-foreground hover:bg-accent focus:bg-accent focus:outline-none"
                        >
                            {item.label}
                        </button>
                    ))}
                </div>
            ) : null}
            <p role="status" aria-live="polite" className="sr-only">{announce}</p>
            {phase.kind === "done" ? <p className="text-xs text-muted-foreground">{phase.text}</p> : null}
            {phase.kind === "error" ? (
                <p role="alert" className="text-xs text-red-700 dark:text-red-300">
                    The file could not be made. Try again.{phase.message ? ` ${phase.message}` : ""}
                    <button type="button" onClick={() => void start(phase.format)} className="ml-2 font-semibold underline">Retry</button>
                </p>
            ) : null}
        </div>
    );
}
