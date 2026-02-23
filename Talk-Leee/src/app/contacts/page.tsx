"use client";

import { useState, useEffect, useRef } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { dashboardApi, Campaign } from "@/lib/dashboard-api";
import { extendedApi, BulkImportResponse } from "@/lib/extended-api";
import { Upload, FileText, CheckCircle, AlertCircle, Loader2 } from "lucide-react";
import { motion } from "framer-motion";

export default function ContactsPage() {
    const [campaigns, setCampaigns] = useState<Campaign[]>([]);
    const [selectedCampaign, setSelectedCampaign] = useState<string>("");
    const [file, setFile] = useState<File | null>(null);
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

    function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
        const selectedFile = e.target.files?.[0];
        if (selectedFile) {
            if (!selectedFile.name.toLowerCase().endsWith(".csv")) {
                setError("Please select a CSV file");
                setFile(null);
                setResult(null);
                e.target.value = "";
                return;
            }
            setFile(selectedFile);
            setError("");
            setResult(null);
        }
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
        if (!dropped) return;
        if (!dropped.name.toLowerCase().endsWith(".csv")) {
            setError("Please select a CSV file");
            setFile(null);
            setResult(null);
            if (fileInputRef.current) fileInputRef.current.value = "";
            return;
        }
        setFile(dropped);
        setError("");
        setResult(null);
        if (fileInputRef.current) fileInputRef.current.value = "";
    }

    async function handleUpload() {
        if (!file || !selectedCampaign) return;

        try {
            setUploading(true);
            setError("");
            const response = await extendedApi.uploadCSV(selectedCampaign, file, true);
            setResult(response);
            setFile(null);
            if (fileInputRef.current) {
                fileInputRef.current.value = "";
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : "Upload failed");
        } finally {
            setUploading(false);
        }
    }

    return (
        <DashboardLayout title="Import Contacts" description="Upload CSV files to add contacts to campaigns">
            {loading ? (
                <div className="flex items-center justify-center h-64">
                    <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-foreground/60" />
                </div>
            ) : (
                <div className="max-w-2xl space-y-6">
                    {/* Upload Card */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        className="content-card"
                    >
                        <h3 className="mb-4 text-sm font-semibold text-foreground">Upload CSV</h3>
                        <div className="space-y-4">
                            {/* Campaign Select */}
                            <div className="group rounded-2xl border border-border bg-muted/60 p-4 shadow-sm transition-[transform,background-color,border-color,box-shadow] duration-150 ease-out hover:-translate-y-0.5 hover:bg-background hover:shadow-md">
                                <label className="text-[11px] font-bold uppercase tracking-wide text-muted-foreground">
                                    Target Campaign
                                </label>
                                <Select
                                    value={selectedCampaign}
                                    onChange={(next) => setSelectedCampaign(next)}
                                    ariaLabel="Select target campaign"
                                    lightThemeGreen
                                    className="mt-2 w-full"
                                    selectClassName="rounded-xl border border-border bg-background/70 px-3 py-2 text-sm font-semibold text-foreground shadow-sm outline-none transition-colors focus:border-border focus:outline-none focus-visible:ring-0 focus-visible:ring-offset-0 disabled:opacity-60 h-auto hover:bg-background/80"
                                    disabled={uploading}
                                >
                                    {campaigns.map((campaign) => (
                                        <option key={campaign.id} value={campaign.id}>
                                            {campaign.name}
                                        </option>
                                    ))}
                                </Select>
                            </div>

                            {/* File Upload */}
                            <div className="group rounded-2xl border border-border bg-muted/60 p-4 shadow-sm transition-[transform,background-color,border-color,box-shadow] duration-150 ease-out hover:-translate-y-0.5 hover:bg-background hover:shadow-md">
                                <label className="text-[11px] font-bold uppercase tracking-wide text-muted-foreground">
                                    CSV File
                                </label>
                                <div
                                    className={`mt-2 rounded-2xl border-2 border-dashed p-8 text-center shadow-sm transition-[transform,background-color,border-color,box-shadow] duration-150 ease-out ${file || dragActive ? "border-ring/60 bg-background/60" : "border-border bg-background/50 hover:border-ring/50"
                                        } ${uploading ? "cursor-not-allowed opacity-70" : "cursor-pointer hover:-translate-y-0.5 hover:shadow-md"}`}
                                    onClick={() => !uploading && fileInputRef.current?.click()}
                                    onDragEnter={handleDragOver}
                                    onDragOver={handleDragOver}
                                    onDragLeave={handleDragLeave}
                                    onDrop={handleDrop}
                                >
                                    <input
                                        ref={fileInputRef}
                                        type="file"
                                        accept=".csv"
                                        onChange={handleFileChange}
                                        className="hidden"
                                        disabled={uploading}
                                    />
                                    {file ? (
                                        <div className="flex flex-wrap items-center justify-center gap-2">
                                            <FileText className="h-5 w-5 text-foreground" />
                                            <span className="text-sm font-semibold text-foreground">{file.name}</span>
                                            <span className="text-sm text-muted-foreground tabular-nums">
                                                ({(file.size / 1024).toFixed(1)} KB)
                                            </span>
                                        </div>
                                    ) : (
                                        <div>
                                            <Upload className="mx-auto mb-2 h-8 w-8 text-muted-foreground" />
                                            <p className="text-sm font-medium text-foreground">
                                                Click to upload or drag and drop
                                            </p>
                                            <p className="mt-1 text-xs text-muted-foreground">CSV files only</p>
                                        </div>
                                    )}
                                </div>
                            </div>

                            {/* CSV Format Info */}
                            <div className="group rounded-2xl border border-border bg-muted/60 p-4 shadow-sm transition-[transform,background-color,border-color,box-shadow] duration-150 ease-out hover:-translate-y-0.5 hover:bg-background hover:shadow-md">
                                <h4 className="text-sm font-semibold text-foreground mb-2">CSV Format</h4>
                                <p className="text-xs text-muted-foreground mb-2">
                                    Your CSV should have the following columns:
                                </p>
                                <code className="inline-flex rounded-lg border border-border bg-background/70 px-2 py-1 text-xs font-semibold text-foreground">
                                    phone_number, first_name, last_name, email
                                </code>
                                <p className="text-xs text-muted-foreground mt-2">
                                    Only phone_number is required. Phone numbers are automatically normalized.
                                </p>
                            </div>

                            {error && (
                                <div className="flex items-center gap-2 rounded-2xl border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
                                    <AlertCircle className="h-4 w-4" />
                                    {error}
                                </div>
                            )}

                            <Button
                                onClick={handleUpload}
                                disabled={!file || !selectedCampaign || uploading}
                                className="w-full hover:scale-[1.02] hover:shadow-md active:scale-[0.99]"
                            >
                                {uploading ? (
                                    <>
                                        <Loader2 className="w-4 h-4 animate-spin" />
                                        Uploading...
                                    </>
                                ) : (
                                    <>
                                        <Upload className="w-4 h-4" />
                                        Upload Contacts
                                    </>
                                )}
                            </Button>
                        </div>
                    </motion.div>

                    {/* Results */}
                    {result && (
                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            className="content-card"
                        >
                            <h3 className="mb-4 text-sm font-semibold text-foreground">Import Results</h3>
                            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
                                <div className="rounded-2xl border border-border bg-muted/60 p-3 text-center shadow-sm transition-[transform,background-color,box-shadow] duration-150 ease-out hover:-translate-y-0.5 hover:bg-background hover:shadow-md">
                                    <p className="text-2xl font-black tabular-nums text-foreground">{result.total_rows}</p>
                                    <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Total Rows</p>
                                </div>
                                <div className="rounded-2xl border border-emerald-500/30 bg-emerald-500/10 p-3 text-center shadow-sm transition-[transform,box-shadow] duration-150 ease-out hover:-translate-y-0.5 hover:shadow-md">
                                    <p className="text-2xl font-black tabular-nums text-emerald-700 dark:text-emerald-300">{result.imported}</p>
                                    <p className="text-xs font-semibold uppercase tracking-wide text-emerald-700/80 dark:text-emerald-300/80">Imported</p>
                                </div>
                                <div className="rounded-2xl border border-yellow-500/30 bg-yellow-500/10 p-3 text-center shadow-sm transition-[transform,box-shadow] duration-150 ease-out hover:-translate-y-0.5 hover:shadow-md">
                                    <p className="text-2xl font-black tabular-nums text-yellow-700 dark:text-yellow-300">
                                        {result.duplicates_skipped}
                                    </p>
                                    <p className="text-xs font-semibold uppercase tracking-wide text-yellow-700/80 dark:text-yellow-300/80">Duplicates</p>
                                </div>
                                <div className="rounded-2xl border border-red-500/30 bg-red-500/10 p-3 text-center shadow-sm transition-[transform,box-shadow] duration-150 ease-out hover:-translate-y-0.5 hover:shadow-md">
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
                                    <h4 className="mb-2 text-sm font-semibold text-foreground">
                                        Errors ({result.errors.length})
                                    </h4>
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
