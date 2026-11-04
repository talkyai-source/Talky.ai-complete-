"use client";

/**
 * Smart CSV contact import.
 *
 * Parses the CSV in the browser, auto-maps headers, runs every phone through
 * the ONE shared phone module (@/lib/phone-number — E.164 or an error, never a
 * guess), shows what each row saves as, lets the user inline-edit the bad
 * rows, dedupes by E.164, and imports only the valid rows. The clean CSV is
 * built by the ONE shared builder (@/lib/contact-upload-csv — same columns as
 * the contacts page upload card) and sent to the existing upload endpoint.
 * The Paste tab parses every pasted number the same way and sends only the
 * valid E.164 values.
 */

import { useMemo, useRef, useState } from "react";
import { AlertCircle, CheckCircle2, FileUp, Loader2, Upload } from "lucide-react";

import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import { DefaultCountrySelect } from "@/components/contacts/default-country-select";
import { extendedApi, BulkImportResponse } from "@/lib/extended-api";
import { parseContactsCsv, type ContactRow } from "@/lib/contact-csv";
import { buildContactsUpload, parseRowNumbers, uploadFileFromCsv, type RowNumbers } from "@/lib/contact-upload-csv";
import { PHONE_HELP_TEXT, parsePhoneNumber, type CountrySelection } from "@/lib/phone-number";

type Row = ContactRow;

/** The backend numbers pasted tokens from 1. */
const PASTE_FIRST_ROW = 1;

/** Split pasted text into the tokens the user wrote: one per line, or
 *  comma / semicolon / pipe / tab separated. The token index (1-based) is the
 *  row number shown for its error. */
export function pasteTokens(text: string): Array<{ row: number; raw: string }> {
    return text
        .split(/[\n\r;,|\t]+/)
        .map((s) => s.trim())
        .filter(Boolean)
        .map((raw, i) => ({ row: i + 1, raw }));
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
    // Backend row number in what was sent → row number in the user's own file / paste.
    const [sentRowMap, setSentRowMap] = useState<Map<number, number>>(() => new Map());
    const [tab, setTab] = useState<"csv" | "paste">("csv");
    const [pasteText, setPasteText] = useState("");
    // "Default country" for this entry point (both tabs): starts empty,
    // applies only to numbers without "+", never stored between visits.
    const [country, setCountry] = useState<CountrySelection>("");
    const fileRef = useRef<HTMLInputElement>(null);

    const reset = () => {
        setRows([]); setFileName(""); setParseError(null); setResult(null);
        setSentRowMap(new Map());
        setPasteText("");
        if (fileRef.current) fileRef.current.value = "";
    };

    // Every pasted number through the shared module: valid ones carry their
    // E.164, the rest their error, by the row the user wrote it on.
    const pasted = useMemo(
        () => pasteTokens(pasteText).map((t) => ({ ...t, check: parsePhoneNumber(t.raw, country) })),
        [pasteText, country],
    );
    const pasteValid = pasted.filter((p) => p.check.ok);
    const pasteInvalid = pasted.filter((p) => !p.check.ok);

    const doPasteImport = async () => {
        if (pasteValid.length === 0) return;
        setImporting(true);
        setParseError(null);
        try {
            // Only the valid E.164 values are sent, one per line; the backend
            // numbers them from 1 in the order sent.
            const rowMap = new Map<number, number>();
            const lines = pasteValid.map((p, i) => {
                rowMap.set(i + PASTE_FIRST_ROW, p.row);
                return (p.check as { ok: true; e164: string }).e164;
            });
            const res = await extendedApi.pasteContacts(campaignId, lines.join("\n"));
            setSentRowMap(rowMap);
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

    // Per-row validation of EVERY number cell (shared rule) + duplicate
    // detection (by the main phone's E.164).
    const analyzed = useMemo(() => {
        const seen = new Set<string>();
        return rows.map((r) => {
            const check = parseRowNumbers(r, country);
            let dup = false;
            if (check.ok) {
                if (seen.has(check.numbers.e164)) dup = true;
                else seen.add(check.numbers.e164);
            }
            return { row: r, check, dup };
        });
    }, [rows, country]);

    const readyCount = analyzed.filter((a) => a.check.ok && !a.dup).length;
    const problemCount = analyzed.filter((a) => !a.check.ok).length;
    const dupCount = analyzed.filter((a) => a.dup).length;

    const editCell = (idx: number, field: keyof Row, value: string) =>
        setRows((prev) => prev.map((r, i) => (i === idx ? { ...r, [field]: value } : r)));

    const doImport = async () => {
        setImporting(true);
        setParseError(null);
        try {
            const ready = analyzed
                .filter((a): a is typeof a & { check: { ok: true; numbers: RowNumbers } } => a.check.ok && !a.dup)
                .map((a) => ({ numbers: a.check.numbers, row: a.row }));
            const { csv, rowMap } = buildContactsUpload(ready);
            const res = await extendedApi.uploadCSV(campaignId, uploadFileFromCsv(csv), true);
            setSentRowMap(rowMap);
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

    const countrySelect = (
        <div className="mb-3 max-w-sm">
            <DefaultCountrySelect
                id="campaign-import-country"
                value={country}
                onChange={setCountry}
                disabled={importing}
            />
            <p className="mt-2 text-xs text-muted-foreground">{PHONE_HELP_TEXT}</p>
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
                                    <li key={i}>row {sentRowMap.get(e.row) ?? e.row}{e.phone ? ` (${e.phone})` : ""}: {e.error}</li>
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

                    {countrySelect}

                    {tab === "csv" ? (
                        <>
                            <input ref={fileRef} type="file" accept=".csv,text/csv" className="hidden" data-testid="campaign-import-file"
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
                                aria-label="Paste phone numbers"
                                placeholder={"Paste phone numbers — one per line, or comma/semicolon separated.\n\n+1 415 555 1234\n4155559999\n(212) 555-0000"}
                                rows={10}
                                className="w-full rounded-xl border border-gray-300 dark:border-white/15 bg-white dark:bg-zinc-900 px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-emerald-500"
                            />
                            {pasted.length > 0 && (
                                <div className="max-h-48 overflow-y-auto rounded-lg border border-border">
                                    <table className="w-full text-xs">
                                        <thead className="bg-muted/50 text-left text-muted-foreground">
                                            <tr>
                                                <th className="px-2 py-1 font-medium">#</th>
                                                <th className="px-2 py-1 font-medium">Number</th>
                                                <th className="px-2 py-1 font-medium">Saves as</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {pasted.map((p) => (
                                                <tr key={p.row} className={`border-t border-border ${p.check.ok ? "" : "bg-red-500/5"}`} data-testid="paste-row">
                                                    <td className="px-2 py-1 tabular-nums text-muted-foreground">{p.row}</td>
                                                    <td className="px-2 py-1 font-mono">{p.raw}</td>
                                                    <td className="px-2 py-1">
                                                        {p.check.ok
                                                            ? <span className="font-semibold tabular-nums text-emerald-700 dark:text-emerald-300">{p.check.e164}</span>
                                                            : <span className="font-semibold text-red-700 dark:text-red-300">{p.check.error}</span>}
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            )}
                            <div className="flex flex-wrap items-center justify-between gap-2">
                                <span className="text-xs text-muted-foreground">
                                    {pasted.length > 0
                                        ? `${pasteValid.length} ready · ${pasteInvalid.length} need fixing`
                                        : "One per line, or comma/semicolon separated."}
                                </span>
                                <Button onClick={doPasteImport} disabled={importing || pasteValid.length === 0}>
                                    {importing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
                                    {importing ? "Importing…" : `Import ${pasteValid.length} contact${pasteValid.length === 1 ? "" : "s"}`}
                                </Button>
                            </div>
                        </div>
                    )}
                </div>
            ) : (
                <div className="overflow-x-auto">
                    {countrySelect}
                    <div className="mb-2 text-xs text-muted-foreground">
                        {fileName} — <span className="text-emerald-600 dark:text-emerald-400">{readyCount} ready</span>
                        {problemCount > 0 && <>, <span className="text-red-600 dark:text-red-400">{problemCount} need fixing</span></>}
                        {dupCount > 0 && <>, {dupCount} duplicate</>}. Edit any cell to fix it.
                    </div>
                    <table className="w-full text-sm">
                        <thead className="text-xs uppercase text-muted-foreground">
                            <tr>
                                <th className="px-2 py-1 text-left w-6"></th>
                                <th className="px-2 py-1 text-left">#</th>
                                <th className="px-2 py-1 text-left">Phone</th>
                                <th className="px-2 py-1 text-left">Saves as</th>
                                <th className="px-2 py-1 text-left">First</th>
                                <th className="px-2 py-1 text-left">Last</th>
                                <th className="px-2 py-1 text-left">Email</th>
                            </tr>
                        </thead>
                        <tbody className="max-h-80">
                            {analyzed.map((a, i) => {
                                const bad = !a.check.ok;
                                return (
                                    <tr key={i} className={a.dup ? "opacity-50" : ""} data-testid="campaign-import-row">
                                        <td className="px-1 py-0.5">
                                            {bad ? <AlertCircle className="h-4 w-4 text-red-500" />
                                                : a.dup ? <span className="text-[10px] text-muted-foreground">dup</span>
                                                    : <CheckCircle2 className="h-4 w-4 text-emerald-500" />}
                                        </td>
                                        <td className="px-1 py-0.5 tabular-nums text-xs text-muted-foreground">{a.row.source_row}</td>
                                        <td className="px-1 py-0.5">
                                            <input
                                                value={a.row.phone}
                                                onChange={(e) => editCell(i, "phone", e.target.value)}
                                                aria-label={`Phone, row ${a.row.source_row}`}
                                                aria-invalid={bad}
                                                className={`w-36 rounded border px-2 py-1 text-sm bg-white dark:bg-zinc-900 focus:outline-none focus:ring-2 ${
                                                    bad
                                                        ? "border-red-400 text-red-700 dark:text-red-300 focus:ring-red-500"
                                                        : "border-gray-300 dark:border-white/15 focus:ring-emerald-500"}`}
                                            />
                                        </td>
                                        <td className="px-1 py-0.5 text-xs">
                                            {a.check.ok
                                                ? <span className="font-semibold tabular-nums text-emerald-700 dark:text-emerald-300">{a.check.numbers.e164}</span>
                                                : <span className="font-semibold text-red-700 dark:text-red-300">{a.check.error}</span>}
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
