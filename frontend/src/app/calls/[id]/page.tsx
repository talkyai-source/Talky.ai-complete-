"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { dashboardApi, CallDetail } from "@/lib/dashboard-api";
import { ArrowLeft, Phone, Clock, FileText, Play } from "lucide-react";
import { motion } from "framer-motion";

function getStatusStyle(status: string) {
    switch (status) {
        case "answered":
        case "completed":
            return "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30";
        case "failed":
        case "no_answer":
        case "busy":
            return "bg-red-500/20 text-red-400 border border-red-500/30";
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

interface TranscriptTurn {
    role: string;
    content: string;
    timestamp: string;
}

export default function CallDetailPage() {
    const params = useParams();
    const router = useRouter();
    const callId = params.id as string;

    const [call, setCall] = useState<CallDetail | null>(null);
    const [transcript, setTranscript] = useState<TranscriptTurn[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    useEffect(() => {
        if (callId) {
            loadCallDetails();
        }
    }, [callId]);

    async function loadCallDetails() {
        try {
            setLoading(true);
            const callData = await dashboardApi.getCall(callId);
            setCall(callData);

            try {
                const transcriptData = await dashboardApi.getCallTranscript(callId, "json");
                if (transcriptData.turns) {
                    setTranscript(transcriptData.turns);
                }
            } catch {
                // Transcript may not exist
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load call details");
        } finally {
            setLoading(false);
        }
    }

    return (
        <DashboardLayout>
            <motion.div
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                className="mb-6"
            >
                <button
                    onClick={() => router.back()}
                    className="flex items-center gap-2 text-sm text-gray-400 hover:text-white transition-colors"
                >
                    <ArrowLeft className="w-4 h-4" />
                    Back to calls
                </button>
            </motion.div>

            {loading ? (
                <div className="flex items-center justify-center h-64">
                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-white" />
                </div>
            ) : error ? (
                <div className="content-card border-red-500/30 text-red-400">
                    {error}
                </div>
            ) : call ? (
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                    {/* Call Info */}
                    <div className="lg:col-span-1 space-y-6">
                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            className="content-card"
                        >
                            <h3 className="text-lg font-semibold text-white mb-4">Call Details</h3>
                            <div className="space-y-4">
                                <div className="flex items-center gap-3">
                                    <div className="p-2 bg-white/10 rounded-lg">
                                        <Phone className="w-5 h-5 text-white" />
                                    </div>
                                    <div>
                                        <p className="text-sm text-gray-400">Phone Number</p>
                                        <p className="font-medium text-white">{call.phone_number}</p>
                                    </div>
                                </div>

                                <div className="flex items-center gap-3">
                                    <div className="p-2 bg-white/10 rounded-lg">
                                        <Clock className="w-5 h-5 text-white" />
                                    </div>
                                    <div>
                                        <p className="text-sm text-gray-400">Duration</p>
                                        <p className="font-medium text-white">{formatDuration(call.duration_seconds)}</p>
                                    </div>
                                </div>

                                <div>
                                    <p className="text-sm text-gray-400 mb-2">Status</p>
                                    <span className={`px-3 py-1 text-sm font-medium rounded-full ${getStatusStyle(call.status)}`}>
                                        {call.status}
                                    </span>
                                </div>

                                {call.outcome && (
                                    <div>
                                        <p className="text-sm text-gray-400 mb-1">Outcome</p>
                                        <p className="font-medium text-white">{call.outcome}</p>
                                    </div>
                                )}

                                <div>
                                    <p className="text-sm text-gray-400 mb-1">Date</p>
                                    <p className="font-medium text-white">{new Date(call.created_at).toLocaleString()}</p>
                                </div>
                            </div>
                        </motion.div>

                        {call.summary && (
                            <motion.div
                                initial={{ opacity: 0, y: 20 }}
                                animate={{ opacity: 1, y: 0 }}
                                transition={{ delay: 0.1 }}
                                className="content-card"
                            >
                                <h3 className="text-lg font-semibold text-white mb-4">Summary</h3>
                                <p className="text-gray-300">{call.summary}</p>
                            </motion.div>
                        )}

                        {call.recording_id && (
                            <motion.div
                                initial={{ opacity: 0, y: 20 }}
                                animate={{ opacity: 1, y: 0 }}
                                transition={{ delay: 0.2 }}
                                className="content-card"
                            >
                                <h3 className="text-lg font-semibold text-white mb-4">Recording</h3>
                                <Button variant="outline" className="w-full border-white/20 text-white hover:bg-white/10">
                                    <Play className="w-4 h-4" />
                                    Play Recording
                                </Button>
                            </motion.div>
                        )}
                    </div>

                    {/* Transcript */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.3 }}
                        className="lg:col-span-2"
                    >
                        <div className="content-card">
                            <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
                                <FileText className="w-5 h-5" />
                                Transcript
                            </h3>
                            {transcript.length === 0 ? (
                                <div className="text-center py-8 text-gray-400">
                                    No transcript available
                                </div>
                            ) : (
                                <div className="space-y-4">
                                    {transcript.map((turn, index) => (
                                        <motion.div
                                            key={index}
                                            initial={{ opacity: 0, x: turn.role === "assistant" ? -10 : 10 }}
                                            animate={{ opacity: 1, x: 0 }}
                                            transition={{ delay: 0.4 + index * 0.05 }}
                                            className={`flex gap-3 ${turn.role === "assistant" ? "flex-row" : "flex-row-reverse"
                                                }`}
                                        >
                                            <div
                                                className={`p-2 rounded-full h-8 w-8 flex items-center justify-center text-sm font-medium ${turn.role === "assistant"
                                                    ? "bg-white text-gray-900"
                                                    : "bg-white/20 text-white"
                                                    }`}
                                            >
                                                {turn.role === "assistant" ? "AI" : "U"}
                                            </div>
                                            <div
                                                className={`flex-1 max-w-[80%] p-4 rounded-lg ${turn.role === "assistant"
                                                    ? "bg-white/10 border border-white/10"
                                                    : "bg-white text-gray-900"
                                                    }`}
                                            >
                                                <p className="text-sm">{turn.content}</p>
                                                <p className={`text-xs mt-2 ${turn.role === "assistant" ? "text-gray-500" : "text-gray-500"
                                                    }`}>
                                                    {new Date(turn.timestamp).toLocaleTimeString()}
                                                </p>
                                            </div>
                                        </motion.div>
                                    ))}
                                </div>
                            )}
                        </div>
                    </motion.div>
                </div>
            ) : null}
        </DashboardLayout>
    );
}
