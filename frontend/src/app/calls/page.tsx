"use client";

import { useEffect, useState } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { dashboardApi, Call } from "@/lib/dashboard-api";
import { Phone, PhoneOff, PhoneIncoming, Clock, ChevronRight } from "lucide-react";
import Link from "next/link";
import { motion } from "framer-motion";

function getStatusIcon(status: string) {
    switch (status) {
        case "answered":
        case "completed":
            return <Phone className="w-4 h-4 text-emerald-400" />;
        case "failed":
        case "no_answer":
        case "busy":
            return <PhoneOff className="w-4 h-4 text-red-400" />;
        case "in_progress":
            return <PhoneIncoming className="w-4 h-4 text-blue-400" />;
        default:
            return <Phone className="w-4 h-4 text-gray-400" />;
    }
}

function getStatusStyle(status: string) {
    switch (status) {
        case "answered":
        case "completed":
            return "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30";
        case "failed":
        case "no_answer":
        case "busy":
            return "bg-red-500/20 text-red-400 border border-red-500/30";
        case "in_progress":
            return "bg-blue-500/20 text-blue-400 border border-blue-500/30";
        default:
            return "bg-gray-500/20 text-gray-400 border border-gray-500/30";
    }
}

function formatDuration(seconds?: number) {
    if (!seconds) return "--";
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${secs.toString().padStart(2, "0")}`;
}

export default function CallsPage() {
    const [calls, setCalls] = useState<Call[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [page, setPage] = useState(1);
    const [total, setTotal] = useState(0);
    const pageSize = 20;

    useEffect(() => {
        loadCalls();
    }, [page]);

    async function loadCalls() {
        try {
            setLoading(true);
            const data = await dashboardApi.listCalls(page, pageSize);
            setCalls(data.calls || []);
            setTotal(data.total || 0);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load calls");
        } finally {
            setLoading(false);
        }
    }

    const totalPages = Math.ceil(total / pageSize);

    return (
        <DashboardLayout title="Call History" description="View all calls and their transcripts">
            {loading ? (
                <div className="flex items-center justify-center h-64">
                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-white" />
                </div>
            ) : error ? (
                <div className="content-card border-red-500/30 text-red-400">
                    {error}
                </div>
            ) : calls.length === 0 ? (
                <motion.div
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="content-card py-16 text-center"
                >
                    <div className="w-16 h-16 mx-auto mb-4 bg-white/10 rounded-full flex items-center justify-center">
                        <Phone className="w-8 h-8 text-gray-400" />
                    </div>
                    <h3 className="text-lg font-medium text-white mb-2">No calls yet</h3>
                    <p className="text-gray-400">
                        Start a campaign to begin making calls.
                    </p>
                </motion.div>
            ) : (
                <>
                    {/* Calls Table */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        className="content-card overflow-hidden"
                    >
                        <div className="overflow-x-auto">
                            <table className="w-full">
                                <thead className="border-b border-white/10">
                                    <tr>
                                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">
                                            Phone Number
                                        </th>
                                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">
                                            Status
                                        </th>
                                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">
                                            Outcome
                                        </th>
                                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">
                                            Duration
                                        </th>
                                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">
                                            Date
                                        </th>
                                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">

                                        </th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-white/5">
                                    {calls.map((call, index) => (
                                        <motion.tr
                                            key={call.id}
                                            initial={{ opacity: 0, x: -10 }}
                                            animate={{ opacity: 1, x: 0 }}
                                            transition={{ delay: index * 0.02 }}
                                            className="hover:bg-white/5 transition-colors"
                                        >
                                            <td className="px-6 py-4 whitespace-nowrap">
                                                <div className="flex items-center gap-3">
                                                    {getStatusIcon(call.status)}
                                                    <span className="text-sm font-medium text-white">
                                                        {call.phone_number}
                                                    </span>
                                                </div>
                                            </td>
                                            <td className="px-6 py-4 whitespace-nowrap">
                                                <span className={`px-2 py-1 text-xs font-medium rounded-full ${getStatusStyle(call.status)}`}>
                                                    {call.status}
                                                </span>
                                            </td>
                                            <td className="px-6 py-4 whitespace-nowrap">
                                                <span className="text-sm text-gray-400">
                                                    {call.outcome || "--"}
                                                </span>
                                            </td>
                                            <td className="px-6 py-4 whitespace-nowrap">
                                                <span className="text-sm text-gray-400 flex items-center gap-1">
                                                    <Clock className="w-4 h-4" />
                                                    {formatDuration(call.duration_seconds)}
                                                </span>
                                            </td>
                                            <td className="px-6 py-4 whitespace-nowrap">
                                                <span className="text-sm text-gray-400">
                                                    {new Date(call.created_at).toLocaleString()}
                                                </span>
                                            </td>
                                            <td className="px-6 py-4 whitespace-nowrap text-right">
                                                <Link
                                                    href={`/calls/${call.id}`}
                                                    className="text-gray-500 hover:text-white transition-colors"
                                                >
                                                    <ChevronRight className="w-5 h-5" />
                                                </Link>
                                            </td>
                                        </motion.tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </motion.div>

                    {/* Pagination */}
                    {totalPages > 1 && (
                        <motion.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            transition={{ delay: 0.3 }}
                            className="flex items-center justify-between mt-6"
                        >
                            <p className="text-sm text-gray-400">
                                Showing {(page - 1) * pageSize + 1} to {Math.min(page * pageSize, total)} of {total} calls
                            </p>
                            <div className="flex gap-2">
                                <button
                                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                                    disabled={page === 1}
                                    className="px-3 py-1 text-sm border border-white/20 rounded-md text-white hover:bg-white/10 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                                >
                                    Previous
                                </button>
                                <button
                                    onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                                    disabled={page === totalPages}
                                    className="px-3 py-1 text-sm border border-white/20 rounded-md text-white hover:bg-white/10 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                                >
                                    Next
                                </button>
                            </div>
                        </motion.div>
                    )}
                </>
            )}
        </DashboardLayout>
    );
}
