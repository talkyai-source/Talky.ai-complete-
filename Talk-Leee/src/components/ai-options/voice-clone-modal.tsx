"use client";

/**
 * Voice clone modal — ElevenLabs Instant Voice Cloning.
 *
 * The user provides a short sample (record from the mic or upload a file),
 * names the voice, confirms they have the right to clone it, and submits.
 * On success the clone appears in their normal voice list (the backend
 * scopes clones per tenant) and is selectable per campaign.
 */
import { useEffect, useRef, useState } from "react";
import { AlertCircle, CheckCircle2, Loader2, Mic, Square, Upload } from "lucide-react";

import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import { extendedApi } from "@/lib/extended-api";

const SAMPLE_SCRIPT =
    "Hi, thanks for taking my call today. I'm reaching out to share something I think " +
    "you'll find genuinely useful, and I'd love just a minute of your time to walk you through it.";

export function VoiceCloneModal({
    open, onClose, onCloned,
}: {
    open: boolean;
    onClose: () => void;
    onCloned?: () => void;
}) {
    const [name, setName] = useState("");
    const [consent, setConsent] = useState(false);
    const [mode, setMode] = useState<"record" | "upload">("record");
    const [sample, setSample] = useState<{ blob: Blob; url: string; label: string } | null>(null);
    const [recording, setRecording] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [done, setDone] = useState(false);

    const recorderRef = useRef<MediaRecorder | null>(null);
    const chunksRef = useRef<BlobPart[]>([]);

    const reset = () => {
        setName(""); setConsent(false); setMode("record"); setError(null);
        setDone(false); setSubmitting(false);
        if (sample) URL.revokeObjectURL(sample.url);
        setSample(null);
        if (recorderRef.current && recording) recorderRef.current.stop();
        setRecording(false);
    };

    // Revoke the object URL on unmount.
    useEffect(() => () => { if (sample) URL.revokeObjectURL(sample.url); }, [sample]);

    const startRecording = async () => {
        setError(null);
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            const rec = new MediaRecorder(stream);
            chunksRef.current = [];
            rec.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data); };
            rec.onstop = () => {
                const blob = new Blob(chunksRef.current, { type: "audio/webm" });
                if (sample) URL.revokeObjectURL(sample.url);
                setSample({ blob, url: URL.createObjectURL(blob), label: "Recorded sample" });
                stream.getTracks().forEach((t) => t.stop());
            };
            rec.start();
            recorderRef.current = rec;
            setRecording(true);
        } catch {
            setError("Couldn't access the microphone. Allow mic access or upload a file instead.");
        }
    };

    const stopRecording = () => {
        recorderRef.current?.stop();
        setRecording(false);
    };

    const onFile = (f: File | null) => {
        if (!f) return;
        if (sample) URL.revokeObjectURL(sample.url);
        setSample({ blob: f, url: URL.createObjectURL(f), label: f.name });
    };

    const submit = async () => {
        if (!name.trim() || !sample || !consent) return;
        setSubmitting(true);
        setError(null);
        try {
            await extendedApi.cloneVoice(name.trim(), consent, sample.blob);
            setDone(true);
            onCloned?.();
        } catch (err) {
            setError(err instanceof Error ? err.message : "Clone failed");
        } finally {
            setSubmitting(false);
        }
    };

    const canSubmit = name.trim() && sample && consent && !submitting;

    return (
        <Modal
            open={open}
            onOpenChange={(o) => { if (!o) { reset(); onClose(); } }}
            title="Clone a voice"
            description="Add a short sample, name it, and we'll create a custom voice you can use in any campaign."
            size="lg"
            footer={
                <div className="flex justify-end gap-2">
                    <Button variant="ghost" onClick={() => { reset(); onClose(); }} disabled={submitting}>
                        {done ? "Close" : "Cancel"}
                    </Button>
                    {!done && (
                        <Button onClick={submit} disabled={!canSubmit}>
                            {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                            {submitting ? "Cloning…" : "Create voice"}
                        </Button>
                    )}
                </div>
            }
        >
            {error && (
                <div className="mb-3 flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/50 dark:text-red-300">
                    <AlertCircle className="h-4 w-4 shrink-0" /> {error}
                </div>
            )}

            {done ? (
                <div className="flex items-center gap-2 py-3 text-sm text-emerald-700 dark:text-emerald-400">
                    <CheckCircle2 className="h-5 w-5" /> Voice cloned! It's now in your voice list.
                </div>
            ) : (
                <div className="space-y-4">
                    <div>
                        <label className="mb-1 block text-xs font-medium text-muted-foreground">Voice name</label>
                        <input
                            value={name}
                            onChange={(e) => setName(e.target.value)}
                            placeholder="e.g. My voice, Sarah – warm"
                            maxLength={80}
                            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
                        />
                    </div>

                    {/* Record ↔ Upload */}
                    <div className="inline-flex rounded-lg border border-border p-0.5 text-sm">
                        <button type="button" onClick={() => setMode("record")}
                            className={`rounded-md px-3 py-1 font-medium ${mode === "record" ? "bg-emerald-600 text-white" : "text-muted-foreground hover:text-foreground"}`}>
                            Record
                        </button>
                        <button type="button" onClick={() => setMode("upload")}
                            className={`rounded-md px-3 py-1 font-medium ${mode === "upload" ? "bg-emerald-600 text-white" : "text-muted-foreground hover:text-foreground"}`}>
                            Upload
                        </button>
                    </div>

                    {mode === "record" ? (
                        <div className="space-y-2">
                            <p className="rounded-lg bg-muted/50 p-3 text-sm text-muted-foreground">
                                Read this aloud clearly (about 20–30 seconds):<br />
                                <span className="mt-1 block text-foreground">“{SAMPLE_SCRIPT}”</span>
                            </p>
                            {!recording ? (
                                <Button variant="outline" onClick={startRecording}>
                                    <Mic className="h-4 w-4" /> Start recording
                                </Button>
                            ) : (
                                <Button variant="outline" onClick={stopRecording}>
                                    <Square className="h-4 w-4 text-red-500" /> Stop
                                </Button>
                            )}
                        </div>
                    ) : (
                        <div>
                            <label className="flex w-full cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-border px-4 py-8 text-center hover:border-emerald-400">
                                <Upload className="h-6 w-6 text-muted-foreground" />
                                <span className="text-sm font-medium">Choose an audio file</span>
                                <span className="text-xs text-muted-foreground">mp3, wav, m4a, webm — 1–2 minutes of clean speech</span>
                                <input type="file" accept="audio/*" className="hidden" onChange={(e) => onFile(e.target.files?.[0] ?? null)} />
                            </label>
                        </div>
                    )}

                    {sample && (
                        <div className="flex items-center gap-3 rounded-lg border border-border p-2">
                            <audio src={sample.url} controls className="h-9 flex-1" />
                            <span className="truncate text-xs text-muted-foreground">{sample.label}</span>
                        </div>
                    )}

                    <label className="flex items-start gap-2 text-sm">
                        <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} className="mt-0.5 h-4 w-4 accent-emerald-600" />
                        <span className="text-muted-foreground">
                            I confirm I have the right to clone this voice and consent to its use. I won&apos;t
                            clone someone else&apos;s voice without their permission.
                        </span>
                    </label>
                </div>
            )}
        </Modal>
    );
}

export default VoiceCloneModal;
