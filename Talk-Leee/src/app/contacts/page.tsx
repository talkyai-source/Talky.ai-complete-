"use client";

import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { dashboardApi, Campaign, Contact } from "@/lib/dashboard-api";
import { extendedApi, BulkImportResponse } from "@/lib/extended-api";
import { parseContactsCsv } from "@/lib/contact-csv";
import { ContactLists } from "@/components/campaigns/contact-lists";
import { Upload, FileText, CheckCircle, AlertCircle, Loader2, Download, X, Search, Plus, Pencil, Trash2 } from "lucide-react";
import { motion } from "framer-motion";

const MAX_FILE_BYTES = 10 * 1024 * 1024; // 10 MB
const MAX_ROWS = 50_000;
const PREVIEW_ROWS = 5;

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
    // ONE shared parser (skips preamble, fuzzy-maps columns, splits Full Name,
    // captures company) — see @/lib/contact-csv. This page keeps its own
    // per-row validation (phone/email sanity + in-file dedupe) on top.
    const { headers, rows: contactRows, phoneFound } = parseContactsCsv(text);
    if (!phoneFound) {
        return {
            headers,
            rows: [],
            valid: 0,
            invalid: 0,
            headerError: headers.length
                ? `Couldn't find a phone column. Found: ${headers.join(", ")}. Add a column like 'phone_number', 'Phone', 'Mobile', or 'To Number'.`
                : "File is empty.",
        };
    }

    const rows: ParsedRow[] = [];
    const seen = new Set<string>();
    let valid = 0;
    let invalid = 0;

    contactRows.forEach((c, i) => {
        const phone = c.phone;
        const email = c.email;

        let error: string | undefined;
        if (!phone) error = "phone_number is empty";
        else if (!isLikelyValidPhone(phone)) error = "phone_number looks invalid";
        else if (!isLikelyValidEmail(email)) error = "email looks invalid";
        else if (seen.has(phone)) error = "duplicate phone_number in this file";

        if (!error) seen.add(phone);
        const ok = !error;
        if (ok) valid++; else invalid++;

        rows.push({
            rowNum: i + 2, // +2: 1 header row + 1-based display
            phone,
            firstName: c.first_name,
            lastName: c.last_name,
            email,
            valid: ok,
            error,
        });
    });

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

    // Contacts-home state. `campaignHasContacts` (recomputed only on UNFILTERED
    // loads) decides whether we show the upload-only view or the full home; the
    // search/list filters never flip it, so an empty search result doesn't kick
    // the user back to the upload screen.
    const [campaignHasContacts, setCampaignHasContacts] = useState(false);
    const [firstLoadDone, setFirstLoadDone] = useState(false);
    const [contacts, setContacts] = useState<Contact[]>([]);
    const [contactsLoading, setContactsLoading] = useState(false);
    const [contactsError, setContactsError] = useState("");
    const [search, setSearch] = useState("");
    const [debouncedSearch, setDebouncedSearch] = useState("");
    const [selectedListId, setSelectedListId] = useState<string | null>(null);
    const [showUploadPanel, setShowUploadPanel] = useState(false);
    const [listsRefreshToken, setListsRefreshToken] = useState(0);

    // Per-contact add/edit/delete (mirrors the campaign detail page).
    const [showAddContact, setShowAddContact] = useState(false);
    const [contactForm, setContactForm] = useState({ phone_number: "", first_name: "", last_name: "", email: "" });
    const [editingContact, setEditingContact] = useState<Contact | null>(null);
    const [savingContact, setSavingContact] = useState(false);
    const [deletingContactId, setDeletingContactId] = useState<string | null>(null);

    useEffect(() => {
        loadCampaigns();
    }, []);

    // Debounce the search box before hitting the backend.
    useEffect(() => {
        const t = setTimeout(() => setDebouncedSearch(search), 300);
        return () => clearTimeout(t);
    }, [search]);

    // Reset filters + the home/upload split whenever the campaign changes.
    useEffect(() => {
        setSearch("");
        setDebouncedSearch("");
        setSelectedListId(null);
        setShowUploadPanel(false);
        setShowAddContact(false);
        setEditingContact(null);
        setCampaignHasContacts(false);
        setFirstLoadDone(false);
        setContactsError("");
    }, [selectedCampaign]);

    const loadContacts = useCallback(async () => {
        if (!selectedCampaign) return;
        const unfiltered = !selectedListId && !debouncedSearch.trim();
        try {
            setContactsLoading(true);
            setContactsError("");
            const res = await dashboardApi.listContacts(selectedCampaign, 1, 50, {
                listId: selectedListId,
                search: debouncedSearch,
            });
            setContacts(res.items);
            // Only an UNFILTERED load is authoritative for "does this campaign
            // have any contacts at all".
            if (unfiltered) setCampaignHasContacts(res.total > 0);
        } catch (err) {
            setContactsError(err instanceof Error ? err.message : "Failed to load contacts");
        } finally {
            setContactsLoading(false);
            setFirstLoadDone(true);
        }
    }, [selectedCampaign, selectedListId, debouncedSearch]);

    useEffect(() => {
        void loadContacts();
    }, [loadContacts]);

    async function handleSaveContact(e: React.FormEvent) {
        e.preventDefault();
        try {
            setSavingContact(true);
            if (editingContact) {
                await dashboardApi.updateContact(selectedCampaign, editingContact.id, contactForm);
            } else {
                await dashboardApi.addContact(selectedCampaign, contactForm);
            }
            setContactForm({ phone_number: "", first_name: "", last_name: "", email: "" });
            setShowAddContact(false);
            setEditingContact(null);
            setCampaignHasContacts(true);
            setListsRefreshToken((t) => t + 1);
            await loadContacts();
        } catch (err) {
            alert(err instanceof Error ? err.message : `Failed to ${editingContact ? "update" : "add"} contact`);
        } finally {
            setSavingContact(false);
        }
    }

    function startEditContact(contact: Contact) {
        setEditingContact(contact);
        setContactForm({
            phone_number: contact.phone_number || "",
            first_name: contact.first_name || "",
            last_name: contact.last_name || "",
            email: contact.email || "",
        });
        setShowAddContact(true);
    }

    async function handleDeleteContact(contactId: string, phone: string) {
        if (!confirm(`Remove ${phone} from this campaign? It will no longer be dialed.`)) return;
        try {
            setDeletingContactId(contactId);
            await dashboardApi.deleteContact(selectedCampaign, contactId);
            setContacts((prev) => prev.filter((c) => c.id !== contactId));
            setListsRefreshToken((t) => t + 1);
            loadContacts().catch(() => {});
        } catch (err) {
            alert(err instanceof Error ? err.message : "Failed to delete contact");
        } finally {
            setDeletingContactId(null);
        }
    }

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
            // Rebuild a CLEAN csv from the shared parser (skips the export's
            // title/metadata preamble + normalizes headers to what the backend
            // expects) so the raw file's junk rows never reach the server. Same
            // approach as the campaign SmartCsvImport.
            const esc = (s: string) => (/[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s);
            const { rows: contactRows } = parseContactsCsv(await file.text());
            const clean = contactRows.filter((r) => r.phone && isLikelyValidPhone(r.phone));
            const csv = ["phone_number,first_name,last_name,email,company"]
                .concat(clean.map((r) => [r.phone, r.first_name, r.last_name, r.email, r.company].map(esc).join(",")))
                .join("\n");
            const cleanFile = new File([csv], "contacts.csv", { type: "text/csv" });
            const response = await extendedApi.uploadCSV(selectedCampaign, cleanFile, true);
            setResult(response);
            clearFile();
            // A successful import means the campaign now has contacts — flip into
            // the home view and refresh the lists row + contacts table.
            if (response.imported > 0) setCampaignHasContacts(true);
            setShowUploadPanel(false);
            setListsRefreshToken((t) => t + 1);
            void loadContacts();
        } catch (err) {
            setError(err instanceof Error ? err.message : "Upload failed");
        } finally {
            setUploading(false);
        }
    }

    const previewRows = parsed?.rows.slice(0, PREVIEW_ROWS) ?? [];

    // The full upload card (drop zone + preview + upload button). Rendered on
    // its own for an empty campaign, or inside a collapsible "Add more" panel.
    const uploadCard = (
                    <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="content-card">
                        <div className="mb-4 flex items-center justify-between">
                            <h3 className="text-sm font-semibold text-foreground">{campaignHasContacts ? "Add more contacts" : "Upload CSV"}</h3>
                            <div className="flex items-center gap-2">
                                <Button variant="outline" size="sm" onClick={downloadTemplate}>
                                    <Download className="w-4 h-4" />
                                    Download template
                                </Button>
                                {campaignHasContacts && (
                                    <Button variant="ghost" size="sm" onClick={() => { setShowUploadPanel(false); clearFile(); setError(""); }}>
                                        <X className="w-4 h-4" />
                                        Close
                                    </Button>
                                )}
                            </div>
                        </div>
                        <div className="space-y-4">
                            {!campaignHasContacts && (
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
                            )}

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
    );

    const resultCard = result ? (
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
    ) : null;

    const activeListName = selectedListId ? "the selected list" : null;

    return (
        <DashboardLayout title="Contacts" description="Import and manage the contacts for your campaigns">
            {loading ? (
                <div className="flex items-center justify-center h-64">
                    <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-foreground/60" />
                </div>
            ) : campaigns.length === 0 ? (
                <div className="max-w-3xl">
                    <div className="content-card text-center text-sm text-muted-foreground">
                        You don&apos;t have any campaigns yet.{" "}
                        <a href="/campaigns" className="font-semibold text-emerald-700 hover:underline dark:text-emerald-400">
                            Create a campaign
                        </a>{" "}
                        first, then import contacts here.
                    </div>
                </div>
            ) : !firstLoadDone ? (
                <div className="flex items-center justify-center h-64">
                    <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-foreground/60" />
                </div>
            ) : campaignHasContacts ? (
                // ─────────────── Contacts HOME ───────────────
                <div className="space-y-6">
                    {/* Toolbar: campaign selector + search + add-more */}
                    <motion.div
                        initial={{ opacity: 0, y: 10 }}
                        animate={{ opacity: 1, y: 0 }}
                        className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"
                    >
                        <div className="flex flex-1 flex-col gap-3 sm:flex-row sm:items-center">
                            <Select
                                value={selectedCampaign}
                                onChange={(next) => setSelectedCampaign(next)}
                                ariaLabel="Select campaign"
                                lightThemeGreen
                                className="w-full sm:w-64"
                                selectClassName="rounded-xl border border-border bg-background/70 px-3 py-2 text-sm font-semibold text-foreground shadow-sm outline-none focus:border-border focus:outline-none focus-visible:ring-0 focus-visible:ring-offset-0 h-auto hover:bg-background/80"
                            >
                                {campaigns.map((campaign) => (
                                    <option key={campaign.id} value={campaign.id}>{campaign.name}</option>
                                ))}
                            </Select>
                            <div className="relative w-full sm:max-w-xs">
                                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                                <input
                                    type="text"
                                    value={search}
                                    onChange={(e) => setSearch(e.target.value)}
                                    placeholder="Search by phone, name or email…"
                                    className="w-full rounded-lg border border-border bg-background py-2 pl-9 pr-3 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring/40"
                                />
                            </div>
                        </div>
                        <Button onClick={() => { setShowUploadPanel((v) => !v); setResult(null); }}>
                            <Plus className="w-4 h-4" />
                            Add more contacts
                        </Button>
                    </motion.div>

                    {/* Collapsible upload panel + last import result */}
                    {showUploadPanel && uploadCard}
                    {resultCard}

                    {/* Contact lists — one card per uploaded list */}
                    <ContactLists
                        campaignId={selectedCampaign}
                        refreshToken={listsRefreshToken}
                        selectedListId={selectedListId}
                        onSelectList={setSelectedListId}
                    />

                    {/* Contacts table */}
                    <motion.div
                        initial={{ opacity: 0, y: 10 }}
                        animate={{ opacity: 1, y: 0 }}
                        className="content-card"
                    >
                        <div className="mb-4 flex items-center justify-between">
                            <h3 className="text-lg font-semibold text-foreground">
                                Contacts
                                {activeListName && (
                                    <span className="ml-2 text-sm font-normal text-muted-foreground">· filtered to {activeListName}</span>
                                )}
                            </h3>
                            <Button
                                size="sm"
                                onClick={() => {
                                    setEditingContact(null);
                                    setContactForm({ phone_number: "", first_name: "", last_name: "", email: "" });
                                    setShowAddContact(true);
                                }}
                            >
                                <Plus className="w-4 h-4" />
                                Add contact
                            </Button>
                        </div>

                        {showAddContact && (
                            <form onSubmit={handleSaveContact} className="mb-6 rounded-lg border border-border bg-muted/30 p-4">
                                <div className="mb-4 grid grid-cols-2 gap-4 md:grid-cols-4">
                                    <div>
                                        <Label htmlFor="phone">Phone Number</Label>
                                        <Input id="phone" value={contactForm.phone_number} onChange={(e) => setContactForm((p) => ({ ...p, phone_number: e.target.value }))} placeholder="+1234567890" required />
                                    </div>
                                    <div>
                                        <Label htmlFor="first_name">First Name</Label>
                                        <Input id="first_name" value={contactForm.first_name} onChange={(e) => setContactForm((p) => ({ ...p, first_name: e.target.value }))} placeholder="John" />
                                    </div>
                                    <div>
                                        <Label htmlFor="last_name">Last Name</Label>
                                        <Input id="last_name" value={contactForm.last_name} onChange={(e) => setContactForm((p) => ({ ...p, last_name: e.target.value }))} placeholder="Doe" />
                                    </div>
                                    <div>
                                        <Label htmlFor="email">Email</Label>
                                        <Input id="email" type="email" value={contactForm.email} onChange={(e) => setContactForm((p) => ({ ...p, email: e.target.value }))} placeholder="john@example.com" />
                                    </div>
                                </div>
                                <div className="flex gap-2">
                                    <Button type="submit" size="sm" disabled={savingContact}>
                                        {savingContact ? <Loader2 className="h-4 w-4 animate-spin" /> : (editingContact ? "Save changes" : "Add")}
                                    </Button>
                                    <Button type="button" variant="outline" size="sm" onClick={() => { setShowAddContact(false); setEditingContact(null); }}>
                                        Cancel
                                    </Button>
                                </div>
                            </form>
                        )}

                        {contactsError && (
                            <div className="mb-4 flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
                                <AlertCircle className="h-4 w-4" /> {contactsError}
                            </div>
                        )}

                        {contactsLoading ? (
                            <div className="flex items-center justify-center py-10">
                                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                            </div>
                        ) : contacts.length === 0 ? (
                            <div className="py-10 text-center text-muted-foreground">
                                No contacts match your search or filter.
                            </div>
                        ) : (
                            <div className="overflow-x-auto">
                                <table className="w-full">
                                    <thead className="border-b border-border">
                                        <tr>
                                            <th className="whitespace-nowrap px-4 py-2 text-left text-xs font-medium uppercase text-muted-foreground">Phone</th>
                                            <th className="whitespace-nowrap px-4 py-2 text-left text-xs font-medium uppercase text-muted-foreground">Name</th>
                                            <th className="whitespace-nowrap px-4 py-2 text-left text-xs font-medium uppercase text-muted-foreground">Email</th>
                                            <th className="whitespace-nowrap px-4 py-2 text-left text-xs font-medium uppercase text-muted-foreground">Status</th>
                                            <th className="whitespace-nowrap px-4 py-2 text-right text-xs font-medium uppercase text-muted-foreground">Actions</th>
                                        </tr>
                                    </thead>
                                    <tbody className="divide-y divide-border/60">
                                        {contacts.map((contact) => (
                                            <tr key={contact.id} className={`transition-colors hover:bg-muted/30 ${contact.is_lead ? "bg-green-500/5" : ""}`}>
                                                <td className="whitespace-nowrap px-4 py-3 text-sm tabular-nums text-foreground">{contact.phone_number}</td>
                                                <td className="whitespace-nowrap px-4 py-3 text-sm text-muted-foreground">
                                                    {contact.first_name || contact.last_name ? `${contact.first_name || ""} ${contact.last_name || ""}`.trim() : "--"}
                                                </td>
                                                <td className="whitespace-nowrap px-4 py-3 text-sm text-muted-foreground">{contact.email || "--"}</td>
                                                <td className="whitespace-nowrap px-4 py-3 text-sm">
                                                    {contact.is_lead ? (
                                                        <span className="w-fit rounded-full border border-emerald-500/20 bg-emerald-500/10 px-2 py-0.5 text-xs font-medium text-emerald-700 dark:text-emerald-400">
                                                            Lead — follow up
                                                        </span>
                                                    ) : (
                                                        <span className="rounded-full border border-border bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
                                                            {contact.last_call_result || contact.status}
                                                        </span>
                                                    )}
                                                </td>
                                                <td className="whitespace-nowrap px-4 py-3 text-right">
                                                    <div className="flex items-center justify-end gap-1">
                                                        <button
                                                            type="button"
                                                            onClick={() => startEditContact(contact)}
                                                            aria-label={`Edit ${contact.phone_number}`}
                                                            title="Edit contact"
                                                            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                                                        >
                                                            <Pencil className="h-3.5 w-3.5" />
                                                        </button>
                                                        <button
                                                            type="button"
                                                            onClick={() => handleDeleteContact(contact.id, contact.phone_number)}
                                                            disabled={deletingContactId === contact.id}
                                                            aria-label={`Delete ${contact.phone_number}`}
                                                            title="Remove contact"
                                                            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive disabled:opacity-50"
                                                        >
                                                            {deletingContactId === contact.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                                                        </button>
                                                    </div>
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </motion.div>
                </div>
            ) : (
                // ─────────────── Empty campaign: upload only ───────────────
                <div className="max-w-3xl space-y-6">
                    {uploadCard}
                    {resultCard}
                </div>
            )}
        </DashboardLayout>
    );
}
