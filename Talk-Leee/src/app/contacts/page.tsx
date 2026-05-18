"use client";

import { useState, useEffect, useRef, useMemo } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { dashboardApi, Campaign } from "@/lib/dashboard-api";
import { extendedApi, BulkImportResponse } from "@/lib/extended-api";
import { Upload, FileText, CheckCircle, AlertCircle, Loader2, Download, X } from "lucide-react";
import { motion } from "framer-motion";

const MAX_FILE_BYTES = 10 * 1024 * 1024; // 10 MB
const MAX_ROWS = 50_000;
const PREVIEW_ROWS = 5;
const REQUIRED_HEADERS = ["phone_number"] as const;

type ParsedRow = {
    rowNum: number;
    phone: string;
    firstName: string;
    lastName: string;
    email: string;
    valid: boolean;
    error?: string;
};

type ParseSummary = {
    headers: string[];
    rows: ParsedRow[];
    valid: number;
    invalid: number;
    headerError?: string;
};

// Lightweight CSV line splitter that respects double-quoted values.
function splitCsvLine(line: string): string[] {
    const out: string[] = [];
    let cur = "";
    let inQuotes = false;
    for (let i = 0; i < line.length; i++) {
        const c = line[i];
        if (inQuotes) {
            if (c === '"' && line[i + 1] === '"') { cur += '"'; i++; }
            else if (c === '"') inQuotes = false;
            else cur += c;
        } else {
            if (c === '"') inQuotes = true;
            else if (c === ",") { out.push(cur); cur = ""; }
            else cur += c;
        }
    }
    out.push(cur);
    return out.map((v) => v.trim());
}

function isLikelyValidPhone(raw: string): boolean {
    const cleaned = raw.replace(/[^\d]/g, "");
    if (cleaned.length < 3) return false;
    if (cleaned.length > 15) return false;
    return /\d/.test(cleaned);
}

function isLikelyValidEmail(raw: string): boolean {
    if (!raw) return true; // optional
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(raw);
}

function parseCsvText(text: string): ParseSummary {
    const lines = text.split(/\r?\n/).filter((l) => l.trim().length > 0);
    if (lines.length === 0) {
        return { headers: [], rows: [], valid: 0, invalid: 0, headerError: "File is empty." };
    }
    const headers = splitCsvLine(lines[0]).map((h) => h.toLowerCase());
    const missing = REQUIRED_HEADERS.filter((r) => !headers.includes(r));
    if (missing.length > 0) {
        return {
            headers,
            rows: [],
            valid: 0,
            invalid: 0,
            headerError: `Missing required column(s): ${missing.join(", ")}. Found: ${headers.join(", ") || "(none)"}.`,
        };
    }
    const idx = (name: string) => headers.indexOf(name);
    const phoneIdx = idx("phone_number");
    const firstIdx = idx("first_name");
    const lastIdx = idx("last_name");
    const emailIdx = idx("email");

    const rows: ParsedRow[] = [];
    const seen = new Set<string>();
    let valid = 0;
    let invalid = 0;

    for (let i = 1; i < lines.length; i++) {
        const cells = splitCsvLine(lines[i]);
        const phone = (cells[phoneIdx] ?? "").trim();
        const firstName = firstIdx >= 0 ? (cells[firstIdx] ?? "").trim() : "";
        const lastName = lastIdx >= 0 ? (cells[lastIdx] ?? "").trim() : "";
        const email = emailIdx >= 0 ? (cells[emailIdx] ?? "").trim() : "";

        let error: string | undefined;
        if (!phone) error = "phone_number is empty";
        else if (!isLikelyValidPhone(phone)) error = "phone_number looks invalid";
        else if (!isLikelyValidEmail(email)) error = "email looks invalid";
        else if (seen.has(phone)) error = "duplicate phone_number in this file";

        if (!error) seen.add(phone);
        const ok = !error;
        if (ok) valid++; else invalid++;

        rows.push({
            rowNum: i + 1,
            phone,
            firstName,
            lastName,
            email,
            valid: ok,
            error,
        });
    }

    return { headers, rows, valid, invalid };
}

const TEMPLATE_CSV = "phone_number,first_name,last_name,email\n+15551234567,Jane,Doe,jane@example.com\n+15557654321,John,Smith,\n";

export default function ContactsPage() {
    const [campaigns, setCampaigns] = useState<Campaign[]>([]);
    const [selectedCampaign, setSelectedCampaign] = useState<string>("");
    const [file, setFile] = useState<File | null>(null);
    const [parsed, setParsed] = useState<ParseSummary | null>(null);
    const [dragActive, setDragActive] = useState(false);
    const [uploading, setUploading] = useState(false);
    const [result, setResult] = useState<BulkImportResponse | null>(null);
    const [error, setError] = useState("");
    const [loading, setLoading] = useState(true);
    const fileInputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        loadCampaigns();
    }, []);

    async function loadCampaigns() {
        try {
            setLoading(true);
            const data = await dashboardApi.listCampaigns();
            setCampaigns(data.campaigns);
            if (data.campaigns.length > 0) {
                setSelectedCampaign(data.campaigns[0].id);
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load campaigns");
        } finally {
            setLoading(false);
        }
    }

    function clearFile() {
        setFile(null);
        setParsed(null);
        setResult(null);
        if (fileInputRef.current) fileInputRef.current.value = "";
    }

    async function acceptFile(f: File) {
        setError("");
        setResult(null);
        if (!f.name.toLowerCase().endsWith(".csv")) {
            setError("File must be a .csv");
            clearFile();
            return;
        }
        if (f.size === 0) {
            setError("File is empty.");
            clearFile();
            return;
        }
        if (f.size > MAX_FILE_BYTES) {
            setError(`File exceeds the ${(MAX_FILE_BYTES / (1024 * 1024)).toFixed(0)} MB limit.`);
            clearFile();
            return;
        }
        const text = await f.text();
        const summary = parseCsvText(text);
        if (summary.headerError) {
            setError(summary.headerError);
            setFile(f);
            setParsed(summary);
            return;
        }
        if (summary.rows.length === 0) {
            setError("CSV has a header but no data rows.");
            setFile(f);
            setParsed(summary);
            return;
        }
        if (summary.rows.length > MAX_ROWS) {
            setError(`File has ${summary.rows.length.toLocaleString()} rows. Maximum is ${MAX_ROWS.toLocaleString()}.`);
            setFile(f);
            setParsed(summary);
            return;
        }
        setFile(f);
        setParsed(summary);
    }

    function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
        const selectedFile = e.target.files?.[0];
        if (selectedFile) void acceptFile(selectedFile);
    }

    function handleDragOver(e: React.DragEvent<HTMLDivElement>) {
        e.preventDefault();
        e.stopPropagation();
        if (uploading) return;
        setDragActive(true);
    }

    function handleDragLeave(e: React.DragEvent<HTMLDivElement>) {
        e.preventDefault();
        e.stopPropagation();
        setDragActive(false);
    }

    function handleDrop(e: React.DragEvent<HTMLDivElement>) {
        e.preventDefault();
        e.stopPropagation();
        if (uploading) return;
        setDragActive(false);
        const dropped = e.dataTransfer.files?.[0];
        if (dropped) void acceptFile(dropped);
    }

    function downloadTemplate() {
        const blob = new Blob([TEMPLATE_CSV], { type: "text/csv;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "contacts-template.csv";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    const canUpload = useMemo(() => {
        if (!file || !selectedCampaign) return false;
        if (uploading) return false;
        if (!parsed || parsed.headerError) return false;
        return parsed.valid > 0;
    }, [file, selectedCampaign, uploading, parsed]);

    async function handleUpload() {
        if (!canUpload || !file) return;
        try {
            setUploading(true);
            setError("");
            const response = await extendedApi.uploadCSV(selectedCampaign, file, true);
            setResult(response);
            clearFile();
        } catch (err) {
            setError(err instanceof Error ? err.message : "Upload failed");
        } finally {
            setUploading(false);
        }
    }

    const previewRows = parsed?.rows.slice(0, PREVIEW_ROWS) ?? [];

    return (
        <DashboardLayout title="Import Contacts" description="Upload a CSV to add leads to one of your campaigns">
            {loading ? (
                <div className="flex items-center justify-center h-64">
                    <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-foreground/60" />
                </div>
            ) : (
                <div className="max-w-3xl space-y-6">
                    <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="content-card">
                        <div className="mb-4 flex items-center justify-between">
                            <h3 className="text-sm font-semibold text-foreground">Upload CSV</h3>
                            <Button variant="outline" size="sm" onClick={downloadTemplate}>
                                <Download className="w-4 h-4" />
                                Download template
                            </Button>
                        </div>
                        <div className="space-y-4">
                            <div className="group rounded-2xl border border-border bg-muted/60 p-4 shadow-sm">
                                <label className="text-[11px] font-bold uppercase tracking-wide text-muted-foreground">Target Campaign</label>
                                <Select
                                    value={selectedCampaign}
                                    onChange={(next) => setSelectedCampaign(next)}
                                    ariaLabel="Select target campaign"
                                    lightThemeGreen
                                    className="mt-2 w-full"
                                    selectClassName="rounded-xl border border-border bg-background/70 px-3 py-2 text-sm font-semibold text-foreground shadow-sm outline-none focus:border-border focus:outline-none focus-visible:ring-0 focus-visible:ring-offset-0 disabled:opacity-60 h-auto hover:bg-background/80"
                                    disabled={uploading}
                                >
                                    {campaigns.map((campaign) => (
                                        <option key={campaign.id} value={campaign.id}>{campaign.name}</option>
                                    ))}
                                </Select>
                            </div>

                            <div className="group rounded-2xl border border-border bg-muted/60 p-4 shadow-sm">
                                <label className="text-[11px] font-bold uppercase tracking-wide text-muted-foreground">CSV File</label>
                                <div
                                    className={`mt-2 rounded-2xl border-2 border-dashed p-8 text-center shadow-sm transition-[transform,background-color,border-color,box-shadow] duration-150 ease-out ${file || dragActive ? "border-ring/60 bg-background/60" : "border-border bg-background/50 hover:border-ring/50"} ${uploading ? "cursor-not-allowed opacity-70" : "cursor-pointer hover:-translate-y-0.5 hover:shadow-md"}`}
                                    onClick={() => !uploading && fileInputRef.current?.click()}
                                    onDragEnter={handleDragOver}
                                    onDragOver={handleDragOver}
                                    onDragLeave={handleDragLeave}
                                    onDrop={handleDrop}
                                >
                                    <input
                                        ref={fileInputRef}
                                        type="file"
                                        accept=".csv,text/csv"
                                        onChange={handleFileChange}
                                        className="hidden"
                                        disabled={uploading}
                                    />
                                    {file ? (
                                        <div className="flex flex-wrap items-center justify-center gap-2">
                                            <FileText className="h-5 w-5 text-foreground" />
                                            <span className="text-sm font-semibold text-foreground">{file.name}</span>
                                            <span className="text-sm text-muted-foreground tabular-nums">({(file.size / 1024).toFixed(1)} KB)</span>
                                            <button
                                                type="button"
                                                onClick={(e) => { e.stopPropagation(); clearFile(); }}
                                                className="ml-2 inline-flex items-center gap-1 rounded-md border border-border bg-background/60 px-2 py-0.5 text-xs text-muted-foreground hover:text-foreground"
                                                aria-label="Remove file"
                                            >
                                                <X className="h-3 w-3" /> Remove
                                            </button>
                                        </div>
                                    ) : (
                                        <div>
                                            <Upload className="mx-auto mb-2 h-8 w-8 text-muted-foreground" />
                                            <p className="text-sm font-medium text-foreground">Click to upload or drag and drop</p>
                                            <p className="mt-1 text-xs text-muted-foreground">CSV only · max 10 MB · max {MAX_ROWS.toLocaleString()} rows</p>
                                        </div>
                                    )}
                                </div>
                            </div>

                            <div className="group rounded-2xl border border-border bg-muted/60 p-4 shadow-sm">
                                <h4 className="text-sm font-semibold text-foreground mb-2">CSV Format</h4>
                                <p className="text-xs text-muted-foreground mb-2">Required column: <span className="font-semibold text-foreground">phone_number</span>. Optional: <span className="font-semibold text-foreground">first_name, last_name, email</span>. Any extra columns are stored as custom fields.</p>
                                <code className="inline-flex rounded-lg border border-border bg-background/70 px-2 py-1 text-xs font-semibold text-foreground">
                                    phone_number,first_name,last_name,email
                                </code>
                                <p className="text-xs text-muted-foreground mt-2">Phone numbers are normalized to E.164 server-side. 10-digit US numbers get +1 added automatically.</p>
                            </div>

                            {parsed && !parsed.headerError && (
                                <div className="rounded-2xl border border-border bg-background/50 p-4 shadow-sm">
                                    <div className="mb-3 flex flex-wrap items-center gap-3">
                                        <span className="text-sm font-semibold text-foreground">Preview</span>
                                        <span className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-xs font-semibold text-emerald-700 dark:text-emerald-300">
                                            {parsed.valid.toLocaleString()} valid
                                        </span>
                                        {parsed.invalid > 0 && (
                                            <span className="rounded-full border border-red-500/30 bg-red-500/10 px-2 py-0.5 text-xs font-semibold text-red-700 dark:text-red-300">
                                                {parsed.invalid.toLocaleString()} invalid
                                            </span>
                                        )}
                                        <span className="text-xs text-muted-foreground tabular-nums">{parsed.rows.length.toLocaleString()} total rows</span>
                                    </div>
                                    <div className="overflow-x-auto rounded-xl border border-border">
                                        <table className="w-full text-xs">
                                            <thead className="bg-muted/60">
                                                <tr>
                                                    <th className="px-2 py-1.5 text-left font-bold uppercase tracking-wide text-muted-foreground">#</th>
                                                    <th className="px-2 py-1.5 text-left font-bold uppercase tracking-wide text-muted-foreground">Phone</th>
                                                    <th className="px-2 py-1.5 text-left font-bold uppercase tracking-wide text-muted-foreground">First</th>
                                                    <th className="px-2 py-1.5 text-left font-bold uppercase tracking-wide text-muted-foreground">Last</th>
                                                    <th className="px-2 py-1.5 text-left font-bold uppercase tracking-wide text-muted-foreground">Email</th>
                                                    <th className="px-2 py-1.5 text-left font-bold uppercase tracking-wide text-muted-foreground">Status</th>
                                                </tr>
                                            </thead>
                                            <tbody className="divide-y divide-border">
                                                {previewRows.map((r) => (
                                                    <tr key={r.rowNum} className={r.valid ? undefined : "bg-red-500/5"}>
                                                        <td className="px-2 py-1.5 tabular-nums text-muted-foreground">{r.rowNum}</td>
                                                        <td className="px-2 py-1.5 text-foreground">{r.phone || <span className="text-muted-foreground">—</span>}</td>
                                                        <td className="px-2 py-1.5 text-foreground">{r.firstName || <span className="text-muted-foreground">—</span>}</td>
                                                        <td className="px-2 py-1.5 text-foreground">{r.lastName || <span className="text-muted-foreground">—</span>}</td>
                                                        <td className="px-2 py-1.5 text-foreground">{r.email || <span className="text-muted-foreground">—</span>}</td>
                                                        <td className="px-2 py-1.5">
                                                            {r.valid ? (
                                                                <span className="text-emerald-700 dark:text-emerald-300 font-semibold">OK</span>
                                                            ) : (
                                                                <span className="text-red-700 dark:text-red-300 font-semibold" title={r.error}>{r.error}</span>
                                                            )}
                                                        </td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                    {parsed.invalid > 0 && (
                                        <p className="mt-2 text-xs text-muted-foreground">Invalid rows are skipped during upload. Only the {parsed.valid.toLocaleString()} valid rows will be sent.</p>
                                    )}
                                </div>
                            )}

                            {error && (
                                <div className="flex items-center gap-2 rounded-2xl border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
                                    <AlertCircle className="h-4 w-4" />
                                    {error}
                                </div>
                            )}

                            <Button
                                onClick={handleUpload}
                                disabled={!canUpload}
                                className="w-full hover:scale-[1.02] hover:shadow-md active:scale-[0.99]"
                            >
                                {uploading ? (
                                    <><Loader2 className="w-4 h-4 animate-spin" />Uploading...</>
                                ) : (
                                    <><Upload className="w-4 h-4" />{parsed?.valid ? `Upload ${parsed.valid.toLocaleString()} contacts` : "Upload Contacts"}</>
                                )}
                            </Button>
                        </div>
                    </motion.div>

                    {result && (
                        <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="content-card">
                            <h3 className="mb-4 text-sm font-semibold text-foreground">Import Results</h3>
                            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
                                <div className="rounded-2xl border border-border bg-muted/60 p-3 text-center shadow-sm">
                                    <p className="text-2xl font-black tabular-nums text-foreground">{result.total_rows}</p>
                                    <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Total Rows</p>
                                </div>
                                <div className="rounded-2xl border border-emerald-500/30 bg-emerald-500/10 p-3 text-center shadow-sm">
                                    <p className="text-2xl font-black tabular-nums text-emerald-700 dark:text-emerald-300">{result.imported}</p>
                                    <p className="text-xs font-semibold uppercase tracking-wide text-emerald-700/80 dark:text-emerald-300/80">Imported</p>
                                </div>
                                <div className="rounded-2xl border border-yellow-500/30 bg-yellow-500/10 p-3 text-center shadow-sm">
                                    <p className="text-2xl font-black tabular-nums text-yellow-700 dark:text-yellow-300">{result.duplicates_skipped}</p>
                                    <p className="text-xs font-semibold uppercase tracking-wide text-yellow-700/80 dark:text-yellow-300/80">Duplicates</p>
                                </div>
                                <div className="rounded-2xl border border-red-500/30 bg-red-500/10 p-3 text-center shadow-sm">
                                    <p className="text-2xl font-black tabular-nums text-red-700 dark:text-red-300">{result.failed}</p>
                                    <p className="text-xs font-semibold uppercase tracking-wide text-red-700/80 dark:text-red-300/80">Failed</p>
                                </div>
                            </div>

                            {result.imported > 0 && (
                                <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-emerald-700 dark:text-emerald-300">
                                    <CheckCircle className="h-4 w-4" />
                                    Successfully imported {result.imported} contacts
                                </div>
                            )}

                            {result.errors.length > 0 && (
                                <div>
                                    <h4 className="mb-2 text-sm font-semibold text-foreground">Errors ({result.errors.length})</h4>
                                    <div className="max-h-48 overflow-y-auto rounded-2xl border border-border bg-background/50">
                                        <table className="w-full text-sm">
                                            <thead className="sticky top-0 bg-muted/60">
                                                <tr>
                                                    <th className="px-3 py-2 text-left text-xs font-bold uppercase tracking-wide text-muted-foreground">Row</th>
                                                    <th className="px-3 py-2 text-left text-xs font-bold uppercase tracking-wide text-muted-foreground">Phone</th>
                                                    <th className="px-3 py-2 text-left text-xs font-bold uppercase tracking-wide text-muted-foreground">Error</th>
                                                </tr>
                                            </thead>
                                            <tbody className="divide-y divide-border">
                                                {result.errors.map((err, i) => (
                                                    <tr key={i}>
                                                        <td className="px-3 py-2 font-semibold text-foreground">{err.row}</td>
                                                        <td className="px-3 py-2 text-muted-foreground">{err.phone || "--"}</td>
                                                        <td className="px-3 py-2 text-destructive">{err.error}</td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                </div>
                            )}
                        </motion.div>
                    )}
                </div>
            )}
        </DashboardLayout>
    );
}
