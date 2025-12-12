"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { dashboardApi } from "@/lib/dashboard-api";
import { ArrowLeft, Loader2 } from "lucide-react";
import { motion } from "framer-motion";

const voiceOptions = [
    { id: "alloy", name: "Alloy", description: "Neutral and balanced" },
    { id: "echo", name: "Echo", description: "Warm and conversational" },
    { id: "fable", name: "Fable", description: "Expressive and dynamic" },
    { id: "onyx", name: "Onyx", description: "Deep and authoritative" },
    { id: "nova", name: "Nova", description: "Friendly and upbeat" },
    { id: "shimmer", name: "Shimmer", description: "Clear and professional" },
];

export default function NewCampaignPage() {
    const router = useRouter();
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const [formData, setFormData] = useState({
        name: "",
        description: "",
        system_prompt: "",
        voice_id: "alloy",
        goal: "",
    });

    function handleChange(
        e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>
    ) {
        setFormData((prev) => ({
            ...prev,
            [e.target.name]: e.target.value,
        }));
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
                    className="flex items-center gap-2 text-sm text-gray-400 hover:text-white transition-colors"
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
                    <h3 className="text-lg font-semibold text-white mb-6">Campaign Details</h3>
                    <form onSubmit={handleSubmit} className="space-y-6">
                        {/* Name */}
                        <div className="space-y-2">
                            <Label htmlFor="name" className="text-gray-400">Campaign Name</Label>
                            <Input
                                id="name"
                                name="name"
                                placeholder="e.g., Q1 Sales Outreach"
                                value={formData.name}
                                onChange={handleChange}
                                required
                                disabled={loading}
                                className="bg-white/10 border-white/20 text-white placeholder:text-gray-500"
                            />
                        </div>

                        {/* Description */}
                        <div className="space-y-2">
                            <Label htmlFor="description" className="text-gray-400">Description (optional)</Label>
                            <textarea
                                id="description"
                                name="description"
                                placeholder="Brief description of this campaign..."
                                value={formData.description}
                                onChange={handleChange}
                                disabled={loading}
                                rows={2}
                                className="flex w-full rounded-lg border border-white/20 bg-white/10 px-3 py-2 text-sm text-white placeholder:text-gray-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/30 disabled:cursor-not-allowed disabled:opacity-50"
                            />
                        </div>

                        {/* Goal */}
                        <div className="space-y-2">
                            <Label htmlFor="goal" className="text-gray-400">Campaign Goal (optional)</Label>
                            <Input
                                id="goal"
                                name="goal"
                                placeholder="e.g., Schedule a demo, Collect feedback"
                                value={formData.goal}
                                onChange={handleChange}
                                disabled={loading}
                                className="bg-white/10 border-white/20 text-white placeholder:text-gray-500"
                            />
                            <p className="text-xs text-gray-500">
                                Define what success looks like for each call
                            </p>
                        </div>

                        {/* Voice Selection */}
                        <div className="space-y-2">
                            <Label className="text-gray-400">AI Voice</Label>
                            <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                                {voiceOptions.map((voice) => (
                                    <button
                                        key={voice.id}
                                        type="button"
                                        onClick={() => setFormData((prev) => ({ ...prev, voice_id: voice.id }))}
                                        disabled={loading}
                                        className={`p-3 rounded-lg border text-left transition-all ${formData.voice_id === voice.id
                                            ? "border-white bg-white text-gray-900"
                                            : "border-white/20 bg-white/5 hover:bg-white/10 text-white"
                                            }`}
                                    >
                                        <p className="font-medium text-sm">{voice.name}</p>
                                        <p className={`text-xs ${formData.voice_id === voice.id ? "text-gray-600" : "text-gray-400"
                                            }`}>
                                            {voice.description}
                                        </p>
                                    </button>
                                ))}
                            </div>
                        </div>

                        {/* System Prompt */}
                        <div className="space-y-2">
                            <Label htmlFor="system_prompt" className="text-gray-400">AI Instructions</Label>
                            <textarea
                                id="system_prompt"
                                name="system_prompt"
                                placeholder="You are a friendly sales assistant calling on behalf of Acme Corp. Your goal is to schedule a product demo..."
                                value={formData.system_prompt}
                                onChange={handleChange}
                                required
                                disabled={loading}
                                rows={6}
                                className="flex w-full rounded-lg border border-white/20 bg-white/10 px-3 py-2 text-sm text-white placeholder:text-gray-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/30 disabled:cursor-not-allowed disabled:opacity-50"
                            />
                            <p className="text-xs text-gray-500">
                                Describe how the AI should behave during calls. Be specific about tone, objectives, and key talking points.
                            </p>
                        </div>

                        {error && (
                            <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/30 rounded-lg p-3">
                                {error}
                            </div>
                        )}

                        <div className="flex gap-4">
                            <Button type="submit" disabled={loading} className="bg-white text-gray-900 hover:bg-gray-100">
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
                                className="border-white/20 text-white hover:bg-white/10"
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
