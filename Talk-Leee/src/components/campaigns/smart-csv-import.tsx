"use client";

/**
 * Smart CSV contact import.
 *
 * Parses the CSV in the browser, auto-maps headers, validates + normalizes
 * every phone (same rules as the backend), flags bad/short/missing numbers in
 * red, lets the user inline-edit the bad rows, dedupes by normalized phone, and
 * imports only the clean rows. Under the hood it re-serializes the cleaned rows
 * to the backend's expected CSV (phone_number,first_name,last_name,email) and
 * reuses the existing upload endpoint — no backend change.
 */

import { useMemo, useRef, useState } from "react";
import { AlertCircle, CheckCircle2, FileUp, Loader2, Upload } from "lucide-react";

import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import { extendedApi, BulkImportResponse } from "@/lib/extended-api";
import { parseContactsCsv, type ContactRow } from "@/lib/contact-csv";

type Row = ContactRow;
type PhoneCheck = { ok: boolean; normalized: string; reason?: string };

/** Mirror of the backend normalize_phone_number, but flags <7 digits as a
 *  problem (a real dialer campaign wants real phone numbers, not extensions). */
function checkPhone(raw: string): PhoneCheck {
    const v = (raw || "").trim();
    if (!v) return { ok: false, normalized: "", reason: "missing" };
    const hasPlus = v.startsWith("+");
    const digits = v.replace(/[^\d]/g, "");
    if (!digits) return { ok: false, normalized: "", reason: "no digits" };
    if (digits.length < 7) return { ok: false, normalized: "", reason: "too short" };
    if (digits.length > 15) return { ok: false, normalized: "", reason: "too long" };
    let normalized: string;
    if (hasPlus) normalized = `+${digits}`;
    else if (digits.length === 10) normalized = `+1${digits}`;
    else if (digits.length === 11 && digits.startsWith("1")) normalized = `+${digits}`;
    else normalized = `+${digits}`;
    return { ok: true, normalized };
}

// CSV parsing (preamble skip + fuzzy column mapping + Full Name split + company)
// lives in the shared `@/lib/contact-csv` module so this modal and the global
// /contacts page use ONE parser. See parseContactsCsv.

function esc(s: string): string {
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function SmartCsvImport({
    open, campaignId, onClose, onImported,
}: {
    open: boolean;
    campaignId: string;
    onClose: () => void;
    onImported?: () => void;
}) {
    const [rows, setRows] = useState<Row[]>([]);
    const [fileName, setFileName] = useState("");
    const [parseError, setParseError] = useState<string | null>(null);
    const [importing, setImporting] = useState(false);
    const [result, setResult] = useState<BulkImportResponse | null>(null);
    const [tab, setTab] = useState<"csv" | "paste">("csv");
    const [pasteText, setPasteText] = useState("");
    const fileRef = useRef<HTMLInputElement>(null);

    const reset = () => {
        setRows([]); setFileName(""); setParseError(null); setResult(null);
        setPasteText("");
        if (fileRef.current) fileRef.current.value = "";
    };

    // Count the non-empty lines/tokens in the paste box for the button label.
    const pasteCount = useMemo(
        () => pasteText.split(/[\n\r;,|\t]+/).map((s) => s.trim()).filter(Boolean).length,
        [pasteText],
    );

    const doPasteImport = async () => {
        setImporting(true);
        setParseError(null);
        try {
            const res = await extendedApi.pasteContacts(campaignId, pasteText);
            setResult(res);
            onImported?.();
        } catch (err) {
            setParseError(err instanceof Error ? err.message : "Import failed");
        } finally {
            setImporting(false);
        }
    };

    const onFile = async (f: File | null) => {
        reset();
        if (!f) return;
        setFileName(f.name);
        try {
            const text = await f.text();
            const { rows: parsed, phoneFound } = parseContactsCsv(text);
            if (!phoneFound) {
                setParseError("Couldn't find a phone column. Make sure the file has a header row with a column like 'Phone', 'Mobile', or 'To Number'.");
                return;
            }
            if (parsed.length === 0) { setParseError("The file has no data rows."); return; }
            setRows(parsed);
        } catch {
            setParseError("Couldn't read the file. Make sure it's a UTF-8 .csv.");
        }
    };

    // Per-row validation + duplicate detection (by normalized phone).
    const analyzed = useMemo(() => {
        const seen = new Set<string>();
        return rows.map((r) => {
            const check = checkPhone(r.phone);
            let dup = false;
            if (check.ok) {
                if (seen.has(check.normalized)) dup = true;
                else seen.add(check.normalized);
            }
            return { row: r, check, dup };
        });
    }, [rows]);

    const readyCount = analyzed.filter((a) => a.check.ok && !a.dup).length;
    const problemCount = analyzed.filter((a) => !a.check.ok).length;
    const dupCount = analyzed.filter((a) => a.dup).length;

    const editCell = (idx: number, field: keyof Row, value: string) =>
        setRows((prev) => prev.map((r, i) => (i === idx ? { ...r, [field]: value } : r)));

    const doImport = async () => {
        setImporting(true);
        setParseError(null);
        try {
            const ready = analyzed.filter((a) => a.check.ok && !a.dup);
            const csv = ["phone_number,first_name,last_name,email"]
                .concat(ready.map((a) => [
                    a.check.normalized, a.row.first_name, a.row.last_name, a.row.email,
                ].map(esc).join(",")))
                .join("\n");
            const file = new File([csv], "contacts.csv", { type: "text/csv" });
            const res = await extendedApi.uploadCSV(campaignId, file, true);
            setResult(res);
            onImported?.();
        } catch (err) {
            setParseError(err instanceof Error ? err.message : "Import failed");
        } finally {
            setImporting(false);
        }
    };

    const footer = (
        <div className="flex items-center justify-between gap-2">
            <span className="text-xs text-muted-foreground">
                {rows.length > 0 && !result
                    ? `${readyCount} ready · ${problemCount} need fixing · ${dupCount} duplicate`
                    : ""}
            </span>
            <div className="flex gap-2">
                <Button variant="ghost" onClick={() => { reset(); onClose(); }} disabled={importing}>
                    {result ? "Close" : "Cancel"}
                </Button>
                {!result && rows.length > 0 && (
                    <Button onClick={doImport} disabled={importing || readyCount === 0}>
                        {importing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
                        {importing ? "Importing…" : `Import ${readyCount} contact${readyCount === 1 ? "" : "s"}`}
                    </Button>
                )}
            </div>
        </div>
    );

    return (
        <Modal
            open={open}
            onOpenChange={(o) => { if (!o) { reset(); onClose(); } }}
            title="Import contacts"
            description="Upload a CSV or paste a list of numbers. We check every phone number, flag the bad ones, and dedupe before importing."
            size="xl"
            footer={footer}
        >
            {parseError && (
                <div className="mb-3 flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/50 dark:text-red-300">
                    <AlertCircle className="h-4 w-4 shrink-0" /> {parseError}
                </div>
            )}

            {result ? (
                <div className="space-y-2 py-2 text-sm">
                    <div className="flex items-center gap-2 text-emerald-700 dark:text-emerald-400">
                        <CheckCircle2 className="h-5 w-5" /> Imported {result.imported} of {result.total_rows} contacts.
                    </div>
                    {result.duplicates_skipped > 0 && <p className="text-muted-foreground">{result.duplicates_skipped} duplicate(s) skipped by the server.</p>}
                    {result.failed > 0 && (
                        <div className="text-yellow-700 dark:text-yellow-400">
                            {result.failed} failed:
                            <ul className="mt-1 max-h-32 overflow-auto text-xs">
                                {result.errors.slice(0, 20).map((e, i) => (
                                    <li key={i}>row {e.row}{e.phone ? ` (${e.phone})` : ""}: {e.error}</li>
                                ))}
                            </ul>
                        </div>
                    )}
                </div>
            ) : rows.length === 0 ? (
                <div>
                    {/* CSV ↔ Paste switcher */}
                    <div className="mb-3 inline-flex rounded-lg border border-border p-0.5 text-sm">
                        <button
                            type="button"
                            onClick={() => { setTab("csv"); setParseError(null); }}
                            className={`rounded-md px-3 py-1 font-medium ${tab === "csv" ? "bg-emerald-600 text-white" : "text-muted-foreground hover:text-foreground"}`}
                        >
                            Upload CSV
                        </button>
                        <button
                            type="button"
                            onClick={() => { setTab("paste"); setParseError(null); }}
                            className={`rounded-md px-3 py-1 font-medium ${tab === "paste" ? "bg-emerald-600 text-white" : "text-muted-foreground hover:text-foreground"}`}
                        >
                            Paste numbers
                        </button>
                    </div>

                    {tab === "csv" ? (
                        <>
                            <input ref={fileRef} type="file" accept=".csv,text/csv" className="hidden"
                                onChange={(e) => onFile(e.target.files?.[0] ?? null)} />
                            <button
                                type="button" onClick={() => fileRef.current?.click()}
                                className="flex w-full flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-gray-300 dark:border-white/15 px-4 py-12 text-center hover:border-emerald-400 hover:bg-emerald-50/40 dark:hover:bg-emerald-950/20"
                            >
                                <FileUp className="h-8 w-8 text-muted-foreground" />
                                <span className="text-sm font-medium text-gray-900 dark:text-zinc-100">Choose a .csv file</span>
                                <span className="text-xs text-muted-foreground">Needs a phone column; first_name / last_name / email optional</span>
                            </button>
                        </>
                    ) : (
                        <div className="space-y-3">
                            <textarea
                                value={pasteText}
                                onChange={(e) => setPasteText(e.target.value)}
                                placeholder={"Paste phone numbers — one per line, or comma/semicolon separated.\n\n+1 415 555 1234\n4155559999\n(212) 555-0000"}
                                rows={10}
                                className="w-full rounded-xl border border-gray-300 dark:border-white/15 bg-white dark:bg-zinc-900 px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-emerald-500"
                            />
                            <div className="flex items-center justify-between">
                                <span className="text-xs text-muted-foreground">
                                    {pasteCount > 0 ? `${pasteCount} number${pasteCount === 1 ? "" : "s"} detected · normalized & deduped server-side` : "One per line, or comma/semicolon separated. Normalized and deduped automatically."}
                                </span>
                                <Button onClick={doPasteImport} disabled={importing || pasteCount === 0}>
                                    {importing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
                                    {importing ? "Importing…" : `Import ${pasteCount || ""} contact${pasteCount === 1 ? "" : "s"}`}
                                </Button>
                            </div>
                        </div>
                    )}
                </div>
            ) : (
                <div className="overflow-x-auto">
                    <div className="mb-2 text-xs text-muted-foreground">
                        {fileName} — <span className="text-emerald-600 dark:text-emerald-400">{readyCount} ready</span>
                        {problemCount > 0 && <>, <span className="text-red-600 dark:text-red-400">{problemCount} need fixing</span></>}
                        {dupCount > 0 && <>, {dupCount} duplicate</>}. Edit any cell to fix it.
                    </div>
                    <table className="w-full text-sm">
                        <thead className="text-xs uppercase text-muted-foreground">
                            <tr>
                                <th className="px-2 py-1 text-left w-6"></th>
                                <th className="px-2 py-1 text-left">Phone</th>
                                <th className="px-2 py-1 text-left">First</th>
                                <th className="px-2 py-1 text-left">Last</th>
                                <th className="px-2 py-1 text-left">Email</th>
                            </tr>
                        </thead>
                        <tbody className="max-h-80">
                            {analyzed.map((a, i) => {
                                const bad = !a.check.ok;
                                return (
                                    <tr key={i} className={a.dup ? "opacity-50" : ""}>
                                        <td className="px-1 py-0.5">
                                            {bad ? <AlertCircle className="h-4 w-4 text-red-500" />
                                                : a.dup ? <span className="text-[10px] text-muted-foreground">dup</span>
                                                    : <CheckCircle2 className="h-4 w-4 text-emerald-500" />}
                                        </td>
                                        <td className="px-1 py-0.5">
                                            <input
                                                value={a.row.phone}
                                                onChange={(e) => editCell(i, "phone", e.target.value)}
                                                title={bad ? a.check.reason : a.check.normalized}
                                                className={`w-36 rounded border px-2 py-1 text-sm bg-white dark:bg-zinc-900 focus:outline-none focus:ring-2 ${
                                                    bad
                                                        ? "border-red-400 text-red-700 dark:text-red-300 focus:ring-red-500"
                                                        : "border-gray-300 dark:border-white/15 focus:ring-emerald-500"}`}
                                            />
                                            {bad && <span className="ml-1 text-[10px] text-red-600 dark:text-red-400">{a.check.reason}</span>}
                                        </td>
                                        {(["first_name", "last_name", "email"] as const).map((f) => (
                                            <td key={f} className="px-1 py-0.5">
                                                <input
                                                    value={a.row[f]}
                                                    onChange={(e) => editCell(i, f, e.target.value)}
                                                    className="w-full min-w-[100px] rounded border border-gray-300 dark:border-white/15 px-2 py-1 text-sm bg-white dark:bg-zinc-900 focus:outline-none focus:ring-2 focus:ring-emerald-500"
                                                />
                                            </td>
                                        ))}
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}
        </Modal>
    );
}

export default SmartCsvImport;
