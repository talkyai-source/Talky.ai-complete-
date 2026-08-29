"use client";

import { useMemo, useState, useRef, useCallback, useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { ArrowLeft, Phone, PhoneIncoming, PhoneOutgoing, Clock, FileText, Play, Download, Pause, Loader2, Route, ShieldCheck } from "lucide-react";
import { motion } from "framer-motion";
import { useCall, useCallTranscript } from "@/lib/api-hooks";
import { extendedApi } from "@/lib/extended-api";
import { statusPillClass } from "@/lib/status-colors";
import { VoiceFeedbackRecorder } from "@/components/calls/voice-feedback-recorder";
import { ConversationReviewPanel } from "@/components/calls/conversation-review-panel";
import { LeadDetailsPanel } from "@/components/calls/lead-details-panel";
import { getRecordingCapabilities } from "@/lib/media-permissions";
import { useEffectivePermissions } from "@/lib/queries/inbound-queries";

// Shared util so call detail agrees with call history + contacts on green/red.
const getStatusStyle = statusPillClass;

function formatDuration(seconds?: number) {
    if (!seconds) return "--";
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${secs.toString().padStart(2, "0")}`;
}

function formatTurnTimestamp(ts: string) {
    const parsed = Date.parse(ts);
    if (Number.isFinite(parsed)) return new Date(ts).toLocaleTimeString();
    return ts;
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

    const callQuery = useCall(callId);
    const permissions = useEffectivePermissions();
    const transcriptQuery = useCallTranscript(callId, "json");
    const call = callQuery.data ?? null;
    const recordingId = call?.recording_id;
    // This must be the dedicated inbound-config identifier returned by the
    // server. Never fall back to the base AI campaign id: they address
    // different resources and would produce a misleading detail/history link.
    const inboundCampaignId = typeof call?.inbound_campaign_id === "string"
        ? call.inbound_campaign_id.trim()
        : "";
    const transcript = useMemo(() => (transcriptQuery.data?.turns ?? []) as TranscriptTurn[], [transcriptQuery.data?.turns]);
    const error = callQuery.isError ? (callQuery.error instanceof Error ? callQuery.error.message : "Failed to load call details") : "";

    const [recordingLoading, setRecordingLoading] = useState(false);
    const [recordingDownloading, setRecordingDownloading] = useState(false);
    const [recordingError, setRecordingError] = useState<string | null>(null);
    const [isPlaying, setIsPlaying] = useState(false);
    const audioRef = useRef<HTMLAudioElement | null>(null);
    const mountedRef = useRef(true);
    const recordingBlobUrlRef = useRef<string | null>(null);
    const playbackAbortRef = useRef<AbortController | null>(null);
    const downloadAbortRef = useRef<AbortController | null>(null);
    const permissionCapabilities = getRecordingCapabilities(permissions.isSuccess ? permissions.data.permissions : undefined);
    const permissionsSettled = permissions.isSuccess || permissions.isError;
    const canPlayMedia = permissionsSettled && permissionCapabilities.canRead;
    const canDownloadMedia = permissionsSettled && permissionCapabilities.canDownload;
    const canPlayMediaRef = useRef(canPlayMedia);

    useEffect(() => {
        canPlayMediaRef.current = canPlayMedia;
    }, [canPlayMedia]);

    useEffect(() => {
        mountedRef.current = true;
        return () => {
            mountedRef.current = false;
            playbackAbortRef.current?.abort();
            downloadAbortRef.current?.abort();
            audioRef.current?.pause();
            if (recordingBlobUrlRef.current) URL.revokeObjectURL(recordingBlobUrlRef.current);
            recordingBlobUrlRef.current = null;
        };
    }, []);

    useEffect(() => {
        if (!canPlayMedia) {
            playbackAbortRef.current?.abort();
        }
        if (!canPlayMedia && recordingBlobUrlRef.current) {
            audioRef.current?.pause();
            URL.revokeObjectURL(recordingBlobUrlRef.current);
            recordingBlobUrlRef.current = null;
            audioRef.current = null;
        }
    }, [canPlayMedia]);

    const loadRecording = useCallback(async (recordingId: string): Promise<string> => {
        if (recordingBlobUrlRef.current) return recordingBlobUrlRef.current;
        setRecordingLoading(true);
        setRecordingError(null);
        const controller = new AbortController();
        playbackAbortRef.current?.abort();
        playbackAbortRef.current = controller;
        try {
            const blob = await extendedApi.fetchRecordingPlaybackBlob(recordingId, controller.signal);
            const url = URL.createObjectURL(blob);
            if (!mountedRef.current || controller.signal.aborted || !canPlayMediaRef.current) {
                URL.revokeObjectURL(url);
                throw new DOMException("Request aborted", "AbortError");
            }
            recordingBlobUrlRef.current = url;
            return url;
        } catch (e) {
            if (mountedRef.current && !controller.signal.aborted) {
                const msg = e instanceof Error ? e.message : "Failed to load recording";
                setRecordingError(msg);
            }
            throw e;
        } finally {
            if (playbackAbortRef.current === controller) playbackAbortRef.current = null;
            if (mountedRef.current) setRecordingLoading(false);
        }
    }, []);

    const handlePlay = useCallback(async () => {
        if (!recordingId || !canPlayMedia) return;
        try {
            const url = await loadRecording(recordingId);
            if (!audioRef.current) {
                audioRef.current = new Audio(url);
                audioRef.current.onended = () => setIsPlaying(false);
                audioRef.current.onpause = () => setIsPlaying(false);
                audioRef.current.onplay = () => setIsPlaying(true);
            }
            if (isPlaying) {
                audioRef.current.pause();
            } else {
                audioRef.current.src = url;
                await audioRef.current.play();
            }
        } catch {
            // error already set in state
        }
    }, [canPlayMedia, isPlaying, loadRecording, recordingId]);

    const handleDownload = useCallback(async () => {
        if (!recordingId || !canDownloadMedia || recordingDownloading) return;
        const controller = new AbortController();
        downloadAbortRef.current?.abort();
        downloadAbortRef.current = controller;
        setRecordingDownloading(true);
        setRecordingError(null);
        let url: string | null = null;
        try {
            const blob = await extendedApi.downloadRecordingBlob(recordingId, controller.signal);
            if (!mountedRef.current || controller.signal.aborted) return;
            url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = `recording-${recordingId}.wav`;
            a.click();
        } catch (error) {
            if (!controller.signal.aborted && mountedRef.current) {
                setRecordingError(error instanceof Error ? error.message : "Failed to download recording");
            }
        } finally {
            if (url) URL.revokeObjectURL(url);
            if (downloadAbortRef.current === controller) downloadAbortRef.current = null;
            if (mountedRef.current) setRecordingDownloading(false);
        }
    }, [canDownloadMedia, recordingDownloading, recordingId]);

    return (
        <DashboardLayout title="Call Details" description="Transcript, recording, and metadata for this call.">
            <motion.div
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                className="mb-6"
            >
                <Button variant="ghost" size="sm" onClick={() => router.back()} className="gap-2 px-2">
                    <ArrowLeft className="h-4 w-4" />
                    Back to calls
                </Button>
            </motion.div>

            {callQuery.isLoading ? (
                <div className="flex items-center justify-center h-64">
                    <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-foreground/60" />
                </div>
            ) : error ? (
                <div className="content-card border-destructive/30 text-destructive">
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
                            <div className="flex items-center justify-between gap-3 mb-4">
                                <h2 className="text-sm font-semibold text-foreground">Call Details</h2>
                                <div className="flex flex-wrap justify-end gap-2"><span className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-semibold ${call.direction === "inbound" ? "border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-300" : "border-border bg-muted text-muted-foreground"}`}>{call.direction === "inbound" ? <PhoneIncoming className="h-3.5 w-3.5" aria-hidden /> : <PhoneOutgoing className="h-3.5 w-3.5" aria-hidden />}{call.direction === "inbound" ? "Inbound" : "Outbound"}</span><span className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold ${getStatusStyle(call.status)}`}>{call.status}</span></div>
                            </div>

                            <div className="space-y-3">
                                <div className="group flex items-center gap-3 rounded-2xl border border-border bg-muted/60 p-3 shadow-sm transition-[transform,background-color,border-color,box-shadow] duration-150 ease-out hover:-translate-y-0.5 hover:bg-background hover:shadow-md">
                                    <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-background/60 text-foreground transition-colors group-hover:bg-background">
                                        <Phone className="h-5 w-5" />
                                    </div>
                                    <div className="min-w-0 flex-1">
                                        <div className="text-[11px] font-bold uppercase tracking-wide text-muted-foreground">{call.direction === "inbound" ? "Caller ANI" : "Phone Number"}</div>
                                        <div className="mt-0.5 truncate text-sm font-semibold text-foreground">{call.phone_number}</div>
                                    </div>
                                </div>

                                {call.direction === "inbound" ? (
                                    <div className="group flex items-center gap-3 rounded-2xl border border-border bg-muted/60 p-3 shadow-sm">
                                        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-background/60 text-foreground"><PhoneIncoming className="h-5 w-5" /></div>
                                        <div className="min-w-0 flex-1"><div className="text-[11px] font-bold uppercase tracking-wide text-muted-foreground">Called DID</div><div className="mt-0.5 truncate text-sm font-semibold text-foreground">{call.to_number || "Unavailable"}</div></div>
                                    </div>
                                ) : null}

                                <div className="group flex items-center gap-3 rounded-2xl border border-border bg-muted/60 p-3 shadow-sm transition-[transform,background-color,border-color,box-shadow] duration-150 ease-out hover:-translate-y-0.5 hover:bg-background hover:shadow-md">
                                    <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-background/60 text-foreground transition-colors group-hover:bg-background">
                                        <Clock className="h-5 w-5" />
                                    </div>
                                    <div className="min-w-0 flex-1">
                                        <div className="text-[11px] font-bold uppercase tracking-wide text-muted-foreground">Duration</div>
                                        <div className="mt-0.5 text-sm font-semibold text-foreground tabular-nums">
                                            {formatDuration(call.duration_seconds)}
                                        </div>
                                    </div>
                                </div>

                                {call.outcome ? (
                                    <div className="group rounded-2xl border border-border bg-muted/60 p-3 shadow-sm transition-[transform,background-color,border-color,box-shadow] duration-150 ease-out hover:-translate-y-0.5 hover:bg-background hover:shadow-md">
                                        <div className="text-[11px] font-bold uppercase tracking-wide text-muted-foreground">Outcome</div>
                                        <div className="mt-1">
                                            <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ${getStatusStyle(call.outcome)}`}>
                                                {call.outcome.replace(/_/g, " ")}
                                            </span>
                                        </div>
                                    </div>
                                ) : null}

                                <div className="group rounded-2xl border border-border bg-muted/60 p-3 shadow-sm transition-[transform,background-color,border-color,box-shadow] duration-150 ease-out hover:-translate-y-0.5 hover:bg-background hover:shadow-md">
                                    <div className="text-[11px] font-bold uppercase tracking-wide text-muted-foreground">Date</div>
                                    <div className="mt-1 text-sm font-semibold text-foreground">{new Date(call.created_at).toLocaleString()}</div>
                                </div>
                            </div>
                        </motion.div>

                        {call.direction === "inbound" ? (
                            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.08 }} className="content-card">
                                <h2 className="mb-4 flex items-center gap-2 text-sm font-semibold text-foreground"><Route className="h-4 w-4 text-primary" aria-hidden />Inbound route snapshot</h2>
                                <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1">
                                    <Metadata label="Inbound campaign" value={inboundCampaignId || undefined} />
                                    <Metadata label="Assignment" value={call.assignment_id} />
                                    <Metadata label="Route" value={call.route_id} />
                                    <Metadata label="Route version" value={call.route_version} />
                                    <Metadata label="Config version" value={call.config_version} />
                                    <Metadata label="Config checksum" value={call.config_checksum ? `${call.config_checksum.slice(0, 12)}…` : undefined} />
                                </dl>
                                {inboundCampaignId ? <Button asChild variant="outline" size="sm" className="mt-4"><Link href={`/inbound-campaigns/${encodeURIComponent(inboundCampaignId)}`}>Open inbound campaign</Link></Button> : null}
                            </motion.div>
                        ) : null}

                        {call.direction === "inbound" && (call.admission_status || call.consent_status || call.processing_status || call.media_state || call.recording_status || call.transcript_status) ? (
                            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.12 }} className="content-card">
                                <h2 className="mb-4 flex items-center gap-2 text-sm font-semibold text-foreground"><ShieldCheck className="h-4 w-4 text-primary" aria-hidden />Consent and media state</h2>
                                <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1"><Metadata label="Admission" value={call.admission_status} /><Metadata label="Consent" value={call.consent_status} /><Metadata label="Processing" value={call.processing_status} /><Metadata label="Media" value={call.media_state} /><Metadata label="Recording" value={call.recording_status} /><Metadata label="Transcript" value={call.transcript_status} /></dl>
                                {call.admission_reason ? <p className="mt-3 rounded-lg border border-amber-500/25 bg-amber-500/5 p-2 text-xs text-muted-foreground">{call.admission_reason}</p> : null}
                            </motion.div>
                        ) : null}

                        {call.summary && (
                            <motion.div
                                initial={{ opacity: 0, y: 20 }}
                                animate={{ opacity: 1, y: 0 }}
                                transition={{ delay: 0.1 }}
                                whileHover={{ scale: 1.01 }}
                                className="content-card"
                            >
                                <h2 className="text-sm font-semibold text-foreground mb-4">Summary</h2>
                                <div className="rounded-2xl border border-border bg-muted/60 p-4 shadow-sm transition-[transform,background-color,box-shadow] duration-150 ease-out hover:-translate-y-0.5 hover:bg-background hover:shadow-md">
                                    <p className="text-sm leading-relaxed text-muted-foreground">{call.summary}</p>
                                </div>
                            </motion.div>
                        )}

                        {call.recording_id && (
                            <motion.div
                                initial={{ opacity: 0, y: 20 }}
                                animate={{ opacity: 1, y: 0 }}
                                transition={{ delay: 0.2 }}
                                whileHover={{ scale: 1.01 }}
                                className="content-card"
                            >
                                <h2 className="text-sm font-semibold text-foreground mb-4">Recording</h2>
                                <div className="rounded-2xl border border-border bg-muted/60 p-4 shadow-sm space-y-2">
                                    {recordingError && (
                                        <p className="text-xs text-destructive mb-2">{recordingError}</p>
                                    )}
                                    <div className="flex gap-2">
                                        <Button
                                            variant="outline"
                                            className="flex-1 hover:scale-[1.02] hover:shadow-md active:scale-[0.99]"
                                            onClick={handlePlay}
                                            disabled={recordingLoading || !canPlayMedia}
                                        >
                                            {recordingLoading ? (
                                                <Loader2 className="w-4 h-4 animate-spin" />
                                            ) : isPlaying ? (
                                                <Pause className="w-4 h-4" />
                                            ) : (
                                                <Play className="w-4 h-4" />
                                            )}
                                            {isPlaying ? "Pause" : "Play"}
                                        </Button>
                                        {canDownloadMedia ? <Button
                                            variant="outline"
                                            className="flex-1 hover:scale-[1.02] hover:shadow-md active:scale-[0.99]"
                                            onClick={handleDownload}
                                            disabled={recordingDownloading}
                                        >
                                            {recordingDownloading ? (
                                                <Loader2 className="w-4 h-4 animate-spin" />
                                            ) : (
                                                <Download className="w-4 h-4" />
                                            )}
                                            Download
                                        </Button> : null}
                                    </div>
                                    {!permissionsSettled ? <p className="text-xs text-muted-foreground">Checking media permissions…</p> : !canPlayMedia ? <p className="text-xs text-muted-foreground">You do not have permission to play this recording.</p> : !canDownloadMedia ? <p className="text-xs text-muted-foreground">Playback is allowed; download requires a separate media permission.</p> : null}
                                </div>
                            </motion.div>
                        )}

                        {/* Reviewer voice note about the agent's responses. It
                            sits with the recording because the two are always
                            reviewed together — you listen, then you comment. */}
                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: 0.25 }}
                        >
                            <VoiceFeedbackRecorder callId={callId} />
                        </motion.div>

                        {/* Structured review (goals.md §3). Sits with the
                            recording and the voice note because all three are
                            the same act: listen, then say what happened. */}
                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: 0.3 }}
                        >
                            <ConversationReviewPanel callId={callId} />
                        </motion.div>

                        {/* What the agent got OUT of the call, and how much to
                            trust each piece (goals.md §7). Sits beside the
                            review for the same reason the review sits beside
                            the recording: you check a captured value against
                            what was actually said. */}
                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: 0.35 }}
                        >
                            <LeadDetailsPanel
                                callId={callId}
                                campaignId={call?.campaign_id ?? undefined}
                            />
                        </motion.div>
                    </div>

                    {/* Transcript */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.3 }}
                        className="lg:col-span-2"
                    >
                        <div className="content-card">
                            <h2 className="mb-4 flex items-center gap-2 text-sm font-semibold text-foreground">
                                <FileText className="h-5 w-5 text-muted-foreground" aria-hidden />
                                Transcript
                            </h2>
                            {transcript.length === 0 ? (
                                <div className="py-8 text-center text-sm text-muted-foreground">
                                    No transcript available
                                </div>
                            ) : (
                                <div className="rounded-2xl border border-border bg-muted/60 p-4 shadow-sm">
                                    <div className="space-y-3">
                                    {transcript.map((turn, index) => (
                                        <motion.div
                                            key={index}
                                            initial={{ opacity: 0, x: turn.role === "assistant" ? -10 : 10 }}
                                            animate={{ opacity: 1, x: 0 }}
                                            transition={{ delay: 0.4 + index * 0.05 }}
                                            whileHover={{ scale: 1.01 }}
                                            className={`flex gap-3 ${turn.role === "assistant" ? "flex-row" : "flex-row-reverse"}`}
                                        >
                                            <div
                                                className={`flex h-9 w-9 items-center justify-center rounded-full border border-border text-xs font-bold ${turn.role === "assistant" ? "bg-muted/60 text-foreground" : "bg-muted/80 text-foreground"}`}
                                            >
                                                {turn.role === "assistant" ? "AI" : "U"}
                                            </div>
                                            <div
                                                className={`flex-1 max-w-[82%] rounded-2xl border p-4 shadow-sm transition-[transform,box-shadow] duration-150 ease-out hover:-translate-y-0.5 hover:shadow-md ${turn.role === "assistant" ? "border-border bg-background" : "border-border bg-muted/60"}`}
                                            >
                                                <p className="text-sm text-foreground">{turn.content}</p>
                                                <p className="mt-2 text-xs text-muted-foreground">
                                                    {formatTurnTimestamp(turn.timestamp)}
                                                </p>
                                            </div>
                                        </motion.div>
                                    ))}
                                    </div>
                                </div>
                            )}
                        </div>
                    </motion.div>
                </div>
            ) : null}
        </DashboardLayout>
    );
}

function Metadata({ label, value }: { label: string; value: string | number | null | undefined }) {
    return <div className="rounded-xl border border-border bg-muted/40 px-3 py-2"><dt className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">{label}</dt><dd className="mt-1 break-all font-mono text-xs text-foreground">{value === null || value === undefined || value === "" ? "Unknown" : String(value)}</dd></div>;
}
