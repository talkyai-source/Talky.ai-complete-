"use client";

/**
 * Look at the file before it becomes 4,000 live dials.
 *
 * goals.md §11 asks for column mapping, row-level validation failures and a
 * duplicate merge/skip decision. All three are things a person needs to SEE
 * first, so this is a preview step that writes nothing: upload, check what we
 * understood, fix anything wrong, then import.
 *
 * An importer that decides all this silently is how a campaign ends up dialling
 * a column of postcodes.
 *
 * WHAT IT SHOWS, AND WHY
 * -----------------------
 *   mapping      every column with our guess beside it. An UNMAPPED column is
 *                shown prominently and is NOT an error — it is kept verbatim in
 *                custom_fields. Losing a column silently is worse than not
 *                understanding it.
 *   issues       per row, per field, with the reason. A 4,000-row file with
 *                nine bad numbers should import 3,991 and tell you about nine.
 *   duplicates   same person twice in one file, by phone or email. Never by
 *                name — two Michael Smiths at two companies are two people.
 */
import { useCallback, useRef, useState } from "react";
import {
    AlertTriangle,
    CheckCircle2,
    Download,
    FileSpreadsheet,
    Loader2,
    Upload,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { InfoTip } from "@/components/ui/info-tip";
import {
    buildCsvTemplate,
    leadDetailsApi,
    type ImportPreview,
} from "@/lib/lead-details-api";

export function CsvImportMapper({
    onConfirm,
}: {
    /** Called with the file once the user has seen the preview and accepted it. */
    onConfirm?: (file: File) => void;
}) {
    const inputRef = useRef<HTMLInputElement | null>(null);
    const [file, setFile] = useState<File | null>(null);
    const [preview, setPreview] = useState<ImportPreview | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const analyse = useCallback(async (f: File) => {
        setBusy(true);
        setError(null);
        setPreview(null);
        try {
            setPreview(await leadDetailsApi.previewImport(f));
            setFile(f);
        } catch (e) {
            setError(e instanceof Error ? e.message : "Couldn't read that file.");
        } finally {
            setBusy(false);
        }
    }, []);

    const downloadTemplate = useCallback(async () => {
        try {
            const spec = await leadDetailsApi.fieldSpec();
            const blob = new Blob([buildCsvTemplate(spec.csv_template_headers)], {
                type: "text/csv",
            });
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = "talklee-contacts-template.csv";
            a.click();
            URL.revokeObjectURL(url);
        } catch {
            setError("Couldn't build the template just now.");
        }
    }, []);

    const mappedPairs = Object.entries(preview?.headers ?? {});
    const hasPhone = mappedPairs.some(([, key]) => key === "phone_number");

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
                <input
                    ref={inputRef}
                    type="file"
                    accept=".csv,text/csv"
                    className="sr-only"
                    onChange={(e) => {
                        const f = e.target.files?.[0];
                        if (f) void analyse(f);
                    }}
                />
                <Button onClick={() => inputRef.current?.click()} disabled={busy}>
                    {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
                    Choose a CSV
                </Button>
                <Button variant="outline" onClick={downloadTemplate}>
                    <Download className="h-4 w-4" />
                    Download template
                </Button>
                <InfoTip label="About the import template">
                    The template uses our own column names, so a file built from it maps
                    with no guessing. Your own headings usually work too — we recognise
                    things like &ldquo;Mobile&rdquo;, &ldquo;Organisation&rdquo; and
                    &ldquo;Job Title&rdquo;.
                </InfoTip>
                {file && (
                    <span className="inline-flex items-center gap-1.5 text-sm text-muted-foreground">
                        <FileSpreadsheet className="h-4 w-4" /> {file.name}
                    </span>
                )}
            </div>

            {error && (
                <p role="alert" className="text-sm text-red-600 dark:text-red-400">{error}</p>
            )}

            {preview && (
                <>
                    <div className="grid gap-3 sm:grid-cols-3">
                        <Stat label="Rows read" value={preview.total_rows} />
                        <Stat label="Rows with no problems" value={preview.valid_rows} tone="good" />
                        <Stat
                            label="Rows needing attention"
                            value={preview.total_rows - preview.valid_rows}
                            tone={preview.total_rows - preview.valid_rows > 0 ? "warn" : "good"}
                        />
                    </div>

                    {!hasPhone && (
                        <p className="flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/5 px-3 py-2 text-sm text-red-700 dark:text-red-400">
                            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                            No column maps to a phone number. Nothing in this file can be
                            dialled.
                        </p>
                    )}

                    {/* mapping */}
                    <section>
                        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                            How we read your columns
                        </h4>
                        <div className="overflow-x-auto rounded-lg border border-border">
                            <table className="w-full text-sm">
                                <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
                                    <tr>
                                        <th className="px-3 py-2 font-medium">Your column</th>
                                        <th className="px-3 py-2 font-medium">Imported as</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {mappedPairs.map(([header, key]) => (
                                        <tr key={header} className="border-t border-border">
                                            <td className="px-3 py-2 text-foreground">{header}</td>
                                            <td className="px-3 py-2">
                                                {key ? (
                                                    <span className="text-foreground">{key.replace(/_/g, " ")}</span>
                                                ) : (
                                                    <span className="text-muted-foreground">
                                                        kept as a custom field
                                                    </span>
                                                )}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                        {preview.unmapped.length > 0 && (
                            <p className="mt-2 text-xs text-muted-foreground">
                                {preview.unmapped.length} column
                                {preview.unmapped.length === 1 ? "" : "s"} we didn&apos;t
                                recognise are kept exactly as they are — nothing is
                                discarded.
                            </p>
                        )}
                    </section>

                    {/* row issues */}
                    {preview.issues.length > 0 && (
                        <section>
                            <h4 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-amber-600 dark:text-amber-400">
                                <AlertTriangle className="h-3.5 w-3.5" />
                                {preview.issues.length} thing
                                {preview.issues.length === 1 ? "" : "s"} to look at
                            </h4>
                            <div className="max-h-56 overflow-y-auto rounded-lg border border-border">
                                <table className="w-full text-sm">
                                    <tbody>
                                        {preview.issues.map((iss, i) => (
                                            <tr key={i} className="border-b border-border last:border-0">
                                                <td className="w-16 px-3 py-1.5 text-xs text-muted-foreground">
                                                    {iss.row ? `row ${iss.row}` : "file"}
                                                </td>
                                                <td className="px-3 py-1.5 text-xs font-medium text-foreground">
                                                    {iss.field.replace(/_/g, " ")}
                                                </td>
                                                <td className="px-3 py-1.5 text-xs text-muted-foreground">
                                                    {iss.value && <code className="mr-2">{iss.value}</code>}
                                                    {iss.reason}
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </section>
                    )}

                    {/* duplicates */}
                    {preview.duplicates_in_file.length > 0 && (
                        <section className="rounded-lg border border-border bg-muted/30 px-3 py-2.5">
                            <p className="text-sm text-foreground">
                                <strong>{preview.duplicates_in_file.length}</strong> row
                                {preview.duplicates_in_file.length === 1 ? " appears" : "s appear"}{" "}
                                more than once in this file.
                            </p>
                            <p className="mt-1 text-xs text-muted-foreground">
                                Matched on phone number, or email where there is no number.
                                Importing keeps the first and skips the rest, so nobody is
                                called twice.
                            </p>
                        </section>
                    )}

                    <div className="flex items-center gap-3 pt-1">
                        <Button
                            disabled={!hasPhone || !file || preview.valid_rows === 0}
                            onClick={() => file && onConfirm?.(file)}
                        >
                            <CheckCircle2 className="h-4 w-4" />
                            Import {preview.valid_rows} contact
                            {preview.valid_rows === 1 ? "" : "s"}
                        </Button>
                        <span className="text-xs text-muted-foreground">
                            Nothing has been saved yet.
                        </span>
                    </div>
                </>
            )}
        </div>
    );
}

function Stat({
    label,
    value,
    tone = "plain",
}: {
    label: string;
    value: number;
    tone?: "plain" | "good" | "warn";
}) {
    const toneClass =
        tone === "good"
            ? "text-emerald-600 dark:text-emerald-400"
            : tone === "warn"
                ? "text-amber-600 dark:text-amber-400"
                : "text-foreground";
    return (
        <div className="rounded-lg border border-border bg-background px-3 py-2">
            <div className={`text-xl font-semibold tabular-nums ${toneClass}`}>{value}</div>
            <div className="text-xs text-muted-foreground">{label}</div>
        </div>
    );
}
