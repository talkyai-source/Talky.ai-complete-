"use client";

import { useEffect, useRef, useState } from "react";
import { Download, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { Call } from "@/lib/dashboard-api";
import { SOURCE_LABEL, type CapturedDetail } from "@/lib/lead-details-api";
import type { CallHistoryWorkflowEntry } from "@/lib/call-history-workflow";
import {
    buildExportRows,
    buildSingleCallPdf,
    exportFileName,
    loadBrandSignaturePng,
    saveBytes,
    type CallExportBrand,
    type CallExportLogo,
    type CallExportMeta,
    type SingleCallExport,
} from "@/lib/call-history-export";

export type CallDownloadTranscriptState = "loading" | "ready" | "failed";

export const DOWNLOAD_FAILED_SENTENCE = "The file could not be made. Try again.";
export const DOWNLOAD_WITHOUT_TRANSCRIPT_SENTENCE = "Downloaded without the transcript, which could not be loaded.";
export const DOWNLOAD_DONE_SENTENCE = "Downloaded.";

export interface CallDownloadButtonProps {
    call: Call;
    entry: CallHistoryWorkflowEntry | undefined;
    capturedFields: SingleCallExport["capturedFields"];
    transcript: SingleCallExport["transcript"];
    transcriptState: CallDownloadTranscriptState;
    /** The page's fuller summary; when given it replaces the row's summary column. */
    summaryText?: string | null;
    timeZone?: string | null;
    accountName?: string | null;
    brand: CallExportBrand;
    /** Test seams for the library calls; production callers never pass these. */
    buildFn?: (detail: SingleCallExport, meta: CallExportMeta, brand: CallExportBrand, logo: CallExportLogo | null) => Promise<Uint8Array>;
    loadLogoFn?: (src: string) => Promise<CallExportLogo | null>;
    saveFn?: (bytes: Uint8Array, fileName: string, mime: string) => void;
}

export function CallDownloadButton({
    call,
    entry,
    capturedFields,
    transcript,
    transcriptState,
    summaryText,
    timeZone,
    accountName,
    brand,
    buildFn = buildSingleCallPdf,
    loadLogoFn = (src: string) => loadBrandSignaturePng({ ...brand, logoSrc: src }),
    saveFn = saveBytes,
}: CallDownloadButtonProps) {
    const [building, setBuilding] = useState(false);
    const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
    const mounted = useRef(true);
    useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
        };
    }, []);

    const loading = transcriptState === "loading";

    const download = async () => {
        if (building || loading) return;
        setBuilding(true);
        setMessage(null);
        try {
            const [row] = buildExportRows([call], { [call.id]: entry }, timeZone);
            const fuller = summaryText?.trim();
            if (fuller) row.summary = fuller;
            const meta: CallExportMeta = {
                accountName: accountName ?? null,
                timeZone: timeZone ?? null,
                // The library prints "All calls" when this is empty; this file is one call.
                filters: ["This call only"],
                generatedAt: new Date(),
                complete: true,
            };
            const logo = await loadLogoFn(brand.logoSrc);
            const bytes = await buildFn(
                { row, capturedFields, transcript: transcriptState === "failed" ? [] : transcript },
                meta,
                brand,
                logo,
            );
            saveFn(bytes, exportFileName("pdf", meta.generatedAt, call.id), "application/pdf");
            if (mounted.current) {
                setMessage({
                    kind: "ok",
                    text: transcriptState === "failed" ? DOWNLOAD_WITHOUT_TRANSCRIPT_SENTENCE : DOWNLOAD_DONE_SENTENCE,
                });
            }
        } catch {
            if (mounted.current) setMessage({ kind: "error", text: DOWNLOAD_FAILED_SENTENCE });
        } finally {
            if (mounted.current) setBuilding(false);
        }
    };

    return (
        <div className="flex flex-col items-end gap-1 print:hidden">
            <Button variant="outline" size="sm" onClick={() => void download()} disabled={building || loading} className="gap-2">
                {building ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Download className="h-4 w-4" aria-hidden />}
                {building ? "Preparing…" : loading ? "Loading the transcript…" : "Download this call (PDF)"}
            </Button>
            {message ? (
                <p role={message.kind === "error" ? "alert" : "status"} className={`text-xs ${message.kind === "error" ? "text-destructive" : "text-muted-foreground"}`}>
                    {message.text}
                </p>
            ) : null}
        </div>
    );
}

/** Every captured detail on the call, with the labels the call page shows. */
export function capturedFieldsFromDetails(details: CapturedDetail[]): SingleCallExport["capturedFields"] {
    return details
        .filter((detail) => detail.value !== null && detail.value.trim() !== "")
        .map((detail) => ({
            label: detail.field_key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
            value: detail.value as string,
            source: SOURCE_LABEL[detail.source] ?? null,
            confirmed: detail.confirmed,
        }));
}

/** The transcript in the page's own words: "Agent" / "Caller", times as the page shows them. */
export function transcriptForExport(
    turns: Array<{ role: string; content: string; timestamp: string }>,
    formatAt: (timestamp: string) => string,
): SingleCallExport["transcript"] {
    return turns.map((turn) => ({
        speaker: turn.role === "assistant" ? "Agent" : "Caller",
        text: turn.content,
        at: turn.timestamp ? formatAt(turn.timestamp) : null,
    }));
}
