"use client";

import { useState, useEffect, useRef } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { dashboardApi, Campaign } from "@/lib/dashboard-api";
import { extendedApi, BulkImportResponse } from "@/lib/extended-api";
import { Upload, FileText, CheckCircle, XCircle, AlertCircle, Loader2 } from "lucide-react";
import { motion } from "framer-motion";

export default function ContactsPage() {
    const [campaigns, setCampaigns] = useState<Campaign[]>([]);
    const [selectedCampaign, setSelectedCampaign] = useState<string>("");
    const [file, setFile] = useState<File | null>(null);
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
            if (!selectedFile.name.endsWith(".csv")) {
                setError("Please select a CSV file");
                return;
            }
            setFile(selectedFile);
            setError("");
            setResult(null);
        }
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
                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-white" />
                </div>
            ) : (
                <div className="max-w-2xl space-y-6">
                    {/* Upload Card */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        className="content-card"
                    >
                        <h3 className="text-lg font-semibold text-white mb-4">Upload CSV</h3>
                        <div className="space-y-4">
                            {/* Campaign Select */}
                            <div>
                                <label className="block text-sm font-medium text-gray-400 mb-1">
                                    Target Campaign
                                </label>
                                <select
                                    value={selectedCampaign}
                                    onChange={(e) => setSelectedCampaign(e.target.value)}
                                    className="w-full bg-white/10 border border-white/20 text-white rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-white/30"
                                    disabled={uploading}
                                >
                                    {campaigns.map((campaign) => (
                                        <option key={campaign.id} value={campaign.id} className="bg-gray-900">
                                            {campaign.name}
                                        </option>
                                    ))}
                                </select>
                            </div>

                            {/* File Upload */}
                            <div>
                                <label className="block text-sm font-medium text-gray-400 mb-1">
                                    CSV File
                                </label>
                                <div
                                    className={`border-2 border-dashed rounded-lg p-8 text-center transition-colors cursor-pointer ${file ? "border-white/40 bg-white/5" : "border-white/20 hover:border-white/40"
                                        }`}
                                    onClick={() => !uploading && fileInputRef.current?.click()}
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
                                        <div className="flex items-center justify-center gap-2">
                                            <FileText className="w-5 h-5 text-white" />
                                            <span className="text-sm font-medium text-white">{file.name}</span>
                                            <span className="text-sm text-gray-400">
                                                ({(file.size / 1024).toFixed(1)} KB)
                                            </span>
                                        </div>
                                    ) : (
                                        <div>
                                            <Upload className="w-8 h-8 mx-auto mb-2 text-gray-400" />
                                            <p className="text-sm text-gray-300">
                                                Click to upload or drag and drop
                                            </p>
                                            <p className="text-xs text-gray-500 mt-1">CSV files only</p>
                                        </div>
                                    )}
                                </div>
                            </div>

                            {/* CSV Format Info */}
                            <div className="bg-white/5 rounded-lg p-4 border border-white/10">
                                <h4 className="text-sm font-medium text-white mb-2">CSV Format</h4>
                                <p className="text-xs text-gray-400 mb-2">
                                    Your CSV should have the following columns:
                                </p>
                                <code className="text-xs bg-white/10 px-2 py-1 rounded text-gray-300">
                                    phone_number, first_name, last_name, email
                                </code>
                                <p className="text-xs text-gray-500 mt-2">
                                    Only phone_number is required. Phone numbers are automatically normalized.
                                </p>
                            </div>

                            {error && (
                                <div className="flex items-center gap-2 text-sm text-red-400 bg-red-500/10 border border-red-500/30 rounded-lg p-3">
                                    <AlertCircle className="w-4 h-4" />
                                    {error}
                                </div>
                            )}

                            <Button
                                onClick={handleUpload}
                                disabled={!file || !selectedCampaign || uploading}
                                className="w-full bg-white text-gray-900 hover:bg-gray-100"
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
                            <h3 className="text-lg font-semibold text-white mb-4">Import Results</h3>
                            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
                                <div className="text-center p-3 bg-white/5 rounded-lg border border-white/10">
                                    <p className="text-2xl font-semibold text-white">{result.total_rows}</p>
                                    <p className="text-sm text-gray-400">Total Rows</p>
                                </div>
                                <div className="text-center p-3 bg-emerald-500/10 rounded-lg border border-emerald-500/30">
                                    <p className="text-2xl font-semibold text-emerald-400">{result.imported}</p>
                                    <p className="text-sm text-emerald-400">Imported</p>
                                </div>
                                <div className="text-center p-3 bg-yellow-500/10 rounded-lg border border-yellow-500/30">
                                    <p className="text-2xl font-semibold text-yellow-400">
                                        {result.duplicates_skipped}
                                    </p>
                                    <p className="text-sm text-yellow-400">Duplicates</p>
                                </div>
                                <div className="text-center p-3 bg-red-500/10 rounded-lg border border-red-500/30">
                                    <p className="text-2xl font-semibold text-red-400">{result.failed}</p>
                                    <p className="text-sm text-red-400">Failed</p>
                                </div>
                            </div>

                            {result.imported > 0 && (
                                <div className="flex items-center gap-2 text-sm text-emerald-400 mb-4">
                                    <CheckCircle className="w-4 h-4" />
                                    Successfully imported {result.imported} contacts
                                </div>
                            )}

                            {result.errors.length > 0 && (
                                <div>
                                    <h4 className="text-sm font-medium text-white mb-2">
                                        Errors ({result.errors.length})
                                    </h4>
                                    <div className="max-h-48 overflow-y-auto border border-white/10 rounded-lg">
                                        <table className="w-full text-sm">
                                            <thead className="bg-white/5 sticky top-0">
                                                <tr>
                                                    <th className="px-3 py-2 text-left text-gray-400">Row</th>
                                                    <th className="px-3 py-2 text-left text-gray-400">Phone</th>
                                                    <th className="px-3 py-2 text-left text-gray-400">Error</th>
                                                </tr>
                                            </thead>
                                            <tbody className="divide-y divide-white/5">
                                                {result.errors.map((err, i) => (
                                                    <tr key={i}>
                                                        <td className="px-3 py-2 text-white">{err.row}</td>
                                                        <td className="px-3 py-2 text-gray-400">{err.phone || "--"}</td>
                                                        <td className="px-3 py-2 text-red-400">{err.error}</td>
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
