"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { dashboardApi } from "@/lib/dashboard-api";
import { aiOptionsApi, VoiceInfo } from "@/lib/ai-options-api";
import { captureException } from "@/lib/monitoring";
import { ArrowLeft, Loader2, Play, RefreshCw, Volume2, Check } from "lucide-react";
import { motion } from "framer-motion";

export default function NewCampaignPage() {
    const router = useRouter();
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const [voices, setVoices] = useState<VoiceInfo[]>([]);
    const [loadingVoices, setLoadingVoices] = useState(true);
    const [previewingVoiceId, setPreviewingVoiceId] = useState<string | null>(null);

    const [formData, setFormData] = useState({
        name: "",
        description: "",
        system_prompt: "",
        voice_id: "",
        goal: "",
    });

    // Fetch curated voices from AI Options
    useEffect(() => {
        async function fetchVoices() {
            try {
                setLoadingVoices(true);
                const voiceList = await aiOptionsApi.getVoices();
                setVoices(voiceList);
                // Set default to first voice if available
                if (voiceList.length > 0) {
                    setFormData((prev) => (prev.voice_id ? prev : { ...prev, voice_id: voiceList[0].id }));
                }
            } catch (err) {
                captureException(err, { area: "campaigns-new", kind: "voices" });
                setError("Failed to load voices.");
            } finally {
                setLoadingVoices(false);
            }
        }
        fetchVoices();
    }, []);

    function handleChange(
        e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>
    ) {
        setFormData((prev) => ({
            ...prev,
            [e.target.name]: e.target.value,
        }));
    }

    // Preview a voice
    async function handlePreviewVoice(voiceId: string) {
        try {
            setPreviewingVoiceId(voiceId);

            const response = await aiOptionsApi.previewVoice({
                voice_id: voiceId,
                text: "Hello, I am your AI voice assistant. How can I help you today?",
            });

            // Play audio
            const audioData = atob(response.audio_base64);
            const audioArray = new Float32Array(audioData.length / 4);
            const dataView = new DataView(new ArrayBuffer(audioData.length));
            for (let i = 0; i < audioData.length; i++) {
                dataView.setUint8(i, audioData.charCodeAt(i));
            }
            for (let i = 0; i < audioArray.length; i++) {
                audioArray[i] = dataView.getFloat32(i * 4, true);
            }

            const audioContext = new AudioContext({ sampleRate: 16000 });
            const audioBuffer = audioContext.createBuffer(1, audioArray.length, 16000);
            audioBuffer.getChannelData(0).set(audioArray);

            const source = audioContext.createBufferSource();
            source.buffer = audioBuffer;
            source.connect(audioContext.destination);
            source.start();

            source.onended = () => {
                audioContext.close();
                setPreviewingVoiceId(null);
            };
        } catch (err) {
            captureException(err, { area: "campaigns-new", kind: "voice-preview" });
            setPreviewingVoiceId(null);
        }
    }

    async function handleSubmit(e: React.FormEvent) {
        e.preventDefault();
        setLoading(true);
        setError("");

        try {
            const result = await dashboardApi.createCampaign({
                name: formData.name,
                description: formData.description || undefined,
                system_prompt: formData.system_prompt,
                voice_id: formData.voice_id,
                goal: formData.goal || undefined,
            });

            router.push(`/campaigns/${result.campaign.id}`);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to create campaign");
        } finally {
            setLoading(false);
        }
    }

    return (
        <DashboardLayout title="Create Campaign" description="Set up a new voice campaign">
            <motion.div
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                className="mb-6"
            >
                <button
                    onClick={() => router.back()}
                    className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
                >
                    <ArrowLeft className="w-4 h-4" />
                    Back to campaigns
                </button>
            </motion.div>

            <div className="max-w-2xl">
                <motion.div
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="content-card"
                >
                    <h3 className="text-lg font-semibold text-foreground mb-6">Campaign Details</h3>
                    <form onSubmit={handleSubmit} className="space-y-6">
                        {/* Name */}
                        <div className="space-y-2">
                            <Label htmlFor="name">Campaign Name</Label>
                            <Input
                                id="name"
                                name="name"
                                placeholder="e.g., Q1 Sales Outreach"
                                value={formData.name}
                                onChange={handleChange}
                                required
                                disabled={loading}
                            />
                        </div>

                        {/* Description */}
                        <div className="space-y-2">
                            <Label htmlFor="description">Description (optional)</Label>
                            <textarea
                                id="description"
                                name="description"
                                placeholder="Brief description of this campaign..."
                                value={formData.description}
                                onChange={handleChange}
                                disabled={loading}
                                rows={2}
                                className="flex w-full rounded-lg border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50"
                            />
                        </div>

                        {/* Goal */}
                        <div className="space-y-2">
                            <Label htmlFor="goal">Campaign Goal (optional)</Label>
                            <Input
                                id="goal"
                                name="goal"
                                placeholder="e.g., Schedule a demo, Collect feedback"
                                value={formData.goal}
                                onChange={handleChange}
                                disabled={loading}
                            />
                            <p className="text-xs text-muted-foreground">
                                Define what success looks like for each call
                            </p>
                        </div>

                        {/* Voice Selection - Updated to use AI Options voices */}
                        <div className="space-y-2">
                            <Label>AI Voice ({voices.length} available)</Label>
                            {loadingVoices ? (
                                <div className="flex items-center justify-center py-8">
                                    <RefreshCw className="w-5 h-5 animate-spin text-muted-foreground" />
                                    <span className="ml-2 text-muted-foreground">Loading voices...</span>
                                </div>
                            ) : (
                                <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                                    {voices.map((voice) => (
                                        <div
                                            key={voice.id}
                                            onClick={() => setFormData((prev) => ({ ...prev, voice_id: voice.id }))}
                                            className={`relative p-3 rounded-lg border cursor-pointer transition-colors ${formData.voice_id === voice.id
                                                    ? "border-emerald-500 bg-emerald-500/20"
                                                    : "border-border bg-muted/30 hover:bg-muted/40"
                                                }`}
                                        >
                                            {/* Play Preview Button */}
                                            <button
                                                type="button"
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    handlePreviewVoice(voice.id);
                                                }}
                                                disabled={previewingVoiceId === voice.id}
                                                className="absolute top-2 right-2 w-8 h-8 rounded-full flex items-center justify-center transition-all hover:scale-110"
                                                style={{ backgroundColor: (voice.accent_color || "#10B981") + "30" }}
                                            >
                                                {previewingVoiceId === voice.id ? (
                                                    <RefreshCw className="w-4 h-4 animate-spin" style={{ color: voice.accent_color || "#10B981" }} />
                                                ) : (
                                                    <Play className="w-4 h-4" style={{ color: voice.accent_color || "#10B981" }} />
                                                )}
                                            </button>

                                            {/* Voice Info */}
                                            <div className="pr-10">
                                                <div className="flex items-center gap-2">
                                                    <div
                                                        className="w-6 h-6 rounded-full flex items-center justify-center"
                                                        style={{ backgroundColor: (voice.accent_color || "#10B981") + "30" }}
                                                    >
                                                        <Volume2 className="w-3 h-3" style={{ color: voice.accent_color || "#10B981" }} />
                                                    </div>
                                                    <p className="font-medium text-sm text-foreground">{voice.name}</p>
                                                </div>
                                                <p className="text-xs text-muted-foreground mt-1 line-clamp-2">
                                                    {voice.description}
                                                </p>

                                                {/* Gender Tag */}
                                                <div className="mt-2 flex gap-1">
                                                    {voice.gender && (
                                                        <span className={`text-xs px-1.5 py-0.5 rounded ${voice.gender === "female"
                                                                ? "bg-pink-500/20 text-pink-400"
                                                                : "bg-blue-500/20 text-blue-400"
                                                            }`}>
                                                            {voice.gender}
                                                        </span>
                                                    )}
                                                </div>
                                            </div>

                                            {/* Selected Indicator */}
                                            {formData.voice_id === voice.id && (
                                                <div className="absolute bottom-2 right-2">
                                                    <Check className="w-4 h-4 text-emerald-400" />
                                                </div>
                                            )}
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>

                        {/* System Prompt */}
                        <div className="space-y-2">
                            <Label htmlFor="system_prompt">AI Instructions</Label>
                            <textarea
                                id="system_prompt"
                                name="system_prompt"
                                placeholder="You are a friendly sales assistant calling on behalf of Acme Corp. Your goal is to schedule a product demo..."
                                value={formData.system_prompt}
                                onChange={handleChange}
                                required
                                disabled={loading}
                                rows={6}
                                className="flex w-full rounded-lg border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50"
                            />
                            <p className="text-xs text-muted-foreground">
                                Describe how the AI should behave during calls. Be specific about tone, objectives, and key talking points.
                            </p>
                        </div>

                        {error && (
                            <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/30 rounded-lg p-3">
                                {error}
                            </div>
                        )}

                        <div className="flex gap-4">
                            <Button type="submit" disabled={loading}>
                                {loading ? (
                                    <>
                                        <Loader2 className="w-4 h-4 animate-spin" />
                                        Creating...
                                    </>
                                ) : (
                                    "Create Campaign"
                                )}
                            </Button>
                            <Button
                                type="button"
                                variant="outline"
                                onClick={() => router.back()}
                                disabled={loading}
                            >
                                Cancel
                            </Button>
                        </div>
                    </form>
                </motion.div>
            </div>
        </DashboardLayout>
    );
}
