"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { dashboardApi, PersonaType } from "@/lib/dashboard-api";
import {
    PERSONAS,
    PersonaSpec,
    SlotDef,
    parseAgentNames,
    parseKvList,
    parseList,
} from "@/lib/campaign-personas";
import { aiOptionsApi, AIProviderConfig, VoiceInfo } from "@/lib/ai-options-api";
import { captureException } from "@/lib/monitoring";
import { ArrowLeft, Loader2, RefreshCw } from "lucide-react";
import { motion } from "framer-motion";

function slotValueToText(value: unknown): string {
    if (Array.isArray(value)) {
        if (value.every((item) => item && typeof item === "object" && "issue" in item)) {
            return value
                .map((item) => {
                    const issue = String((item as { issue?: unknown }).issue ?? "").trim();
                    const solution = String((item as { solution?: unknown }).solution ?? "").trim();
                    return solution ? `${issue} | ${solution}` : issue;
                })
                .filter(Boolean)
                .join("\n");
        }
        return value.map((item) => String(item)).join("\n");
    }
    if (value && typeof value === "object") {
        return Object.entries(value as Record<string, unknown>)
            .map(([key, val]) => `${key}: ${String(val)}`)
            .join("\n");
    }
    return value == null ? "" : String(value);
}

export default function EditCampaignPage() {
    const params = useParams();
    const router = useRouter();
    const campaignId = params.id as string;

    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState("");
    const [voices, setVoices] = useState<VoiceInfo[]>([]);
    const [globalAiConfig, setGlobalAiConfig] = useState<AIProviderConfig | null>(null);

    const [formData, setFormData] = useState({
        name: "",
        description: "",
        system_prompt: "",
        voice_id: "",
        goal: "",
    });
    const [personaType, setPersonaType] = useState<PersonaType>("lead_gen");
    const [companyName, setCompanyName] = useState("");
    const [agentNamesRaw, setAgentNamesRaw] = useState("");
    const [slotValues, setSlotValues] = useState<Record<string, string>>({});

    const persona: PersonaSpec = PERSONAS.find((p) => p.value === personaType) ?? PERSONAS[0];

    const loadData = useCallback(async () => {
        try {
            setLoading(true);
            const [campaignData, voicesResult, config] = await Promise.all([
                dashboardApi.getCampaign(campaignId),
                aiOptionsApi.getVoices(),
                aiOptionsApi.getConfig(),
            ]);

            const campaign = campaignData.campaign;
            const scriptConfig = campaign.script_config ?? {};
            const nextPersona = (scriptConfig.persona_type ?? "lead_gen") as PersonaType;
            const slots = scriptConfig.campaign_slots ?? {};
            const nextSlotValues: Record<string, string> = {};
            for (const [key, value] of Object.entries(slots)) {
                nextSlotValues[key] = slotValueToText(value);
            }

            setVoices(voicesResult.voices);
            setGlobalAiConfig(config);
            setPersonaType(nextPersona);
            setCompanyName(scriptConfig.company_name ?? "");
            setAgentNamesRaw((scriptConfig.agent_names ?? []).join(", "));
            setSlotValues(nextSlotValues);
            setFormData({
                name: campaign.name ?? "",
                description: campaign.description ?? "",
                system_prompt: scriptConfig.additional_instructions ?? campaign.system_prompt ?? "",
                voice_id: campaign.voice_id ?? "",
                goal: (campaign as { goal?: string }).goal ?? "",
            });
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load campaign");
        } finally {
            setLoading(false);
        }
    }, [campaignId]);

    useEffect(() => {
        if (campaignId) void loadData();
    }, [campaignId, loadData]);

    function setSlot(key: string, value: string) {
        setSlotValues((prev) => ({ ...prev, [key]: value }));
    }

    function changePersona(next: PersonaType) {
        setPersonaType(next);
        setSlotValues({});
    }

    function buildCampaignSlots(): Record<string, unknown> {
        const out: Record<string, unknown> = {};
        for (const slot of persona.slots) {
            const raw = (slotValues[slot.key] ?? "").trim();
            if (!raw) continue;
            if (slot.kind === "list") {
                out[slot.key] = parseList(raw);
            } else if (slot.kind === "kv-list") {
                out[slot.key] = parseKvList(raw);
            } else {
                out[slot.key] = raw;
            }
        }
        return out;
    }

    function missingRequiredSlots(): SlotDef[] {
        return persona.slots.filter((slot) => slot.required && !(slotValues[slot.key] ?? "").trim());
    }

    function handleChange(e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) {
        setFormData((prev) => ({ ...prev, [e.target.name]: e.target.value }));
    }

    async function handleSubmit(e: React.FormEvent) {
        e.preventDefault();
        const agentNames = parseAgentNames(agentNamesRaw);
        if (!formData.voice_id) {
            setError("Select a voice from the active global TTS provider.");
            return;
        }
        if (agentNames.length === 0) {
            setError("Add at least one agent name.");
            return;
        }
        if (!companyName.trim()) {
            setError("Add the company or business name your agent represents.");
            return;
        }
        const missing = missingRequiredSlots();
        if (missing.length > 0) {
            setError(`Missing required fields: ${missing.map((slot) => slot.label).join(", ")}`);
            return;
        }

        try {
            setSaving(true);
            setError("");
            await dashboardApi.updateCampaign(campaignId, {
                name: formData.name,
                description: formData.description || undefined,
                system_prompt: formData.system_prompt,
                voice_id: formData.voice_id,
                goal: formData.goal || undefined,
                persona_type: personaType,
                company_name: companyName.trim(),
                agent_names: agentNames,
                campaign_slots: buildCampaignSlots(),
            });
            router.push(`/campaigns/${campaignId}`);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to update campaign");
        } finally {
            setSaving(false);
        }
    }

    const campaignVoices = globalAiConfig
        ? voices.filter((voice) => voice.provider === globalAiConfig.tts_provider)
        : voices;

    return (
        <DashboardLayout title="Edit Campaign" description="Update campaign details while keeping the production prompt system enforced.">
            <motion.div initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} className="mb-6">
                <button
                    onClick={() => router.push(`/campaigns/${campaignId}`)}
                    className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
                >
                    <ArrowLeft className="w-4 h-4" />
                    Back to campaign
                </button>
            </motion.div>

            {loading ? (
                <div className="flex items-center justify-center h-64">
                    <RefreshCw className="w-6 h-6 animate-spin text-muted-foreground" />
                </div>
            ) : (
                <div className="max-w-2xl">
                    <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="content-card">
                        <h3 className="text-lg font-semibold text-foreground mb-6">Campaign Details</h3>
                        <form onSubmit={handleSubmit} className="space-y-6">
                            <div className="space-y-2">
                                <Label htmlFor="name">Campaign Name</Label>
                                <Input id="name" name="name" value={formData.name} onChange={handleChange} required disabled={saving} />
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="description">Description</Label>
                                <textarea
                                    id="description"
                                    name="description"
                                    value={formData.description}
                                    onChange={handleChange}
                                    disabled={saving}
                                    rows={2}
                                    className="flex w-full rounded-lg border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50"
                                />
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="goal">Campaign Goal</Label>
                                <Input id="goal" name="goal" value={formData.goal} onChange={handleChange} disabled={saving} />
                            </div>

                            <div className="space-y-2">
                                <Label>Voice</Label>
                                {campaignVoices.length === 0 ? (
                                    <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-200">
                                        No voices are available for the current global TTS provider.
                                    </div>
                                ) : (
                                    <select
                                        value={formData.voice_id}
                                        onChange={(e) => setFormData((prev) => ({ ...prev, voice_id: e.target.value }))}
                                        disabled={saving}
                                        className="flex w-full rounded-lg border border-input bg-background px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50"
                                    >
                                        {campaignVoices.map((voice) => (
                                            <option key={voice.id} value={voice.id}>{voice.name}</option>
                                        ))}
                                    </select>
                                )}
                            </div>

                            <div className="space-y-2">
                                <Label>Production prompt role</Label>
                                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                                    {PERSONAS.map((p) => (
                                        <label
                                            key={p.value}
                                            className={`flex cursor-pointer flex-col gap-1 rounded-lg border p-3 transition-colors ${
                                                personaType === p.value
                                                    ? "border-emerald-500 bg-emerald-500/10"
                                                    : "border-border bg-muted/30 hover:bg-muted/40"
                                            }`}
                                        >
                                            <div className="flex items-center gap-2">
                                                <input
                                                    type="radio"
                                                    name="persona_type"
                                                    value={p.value}
                                                    checked={personaType === p.value}
                                                    onChange={() => changePersona(p.value)}
                                                    disabled={saving}
                                                />
                                                <span className="text-sm font-medium text-foreground">{p.title}</span>
                                            </div>
                                            <span className="text-xs text-muted-foreground">{p.summary}</span>
                                        </label>
                                    ))}
                                </div>
                            </div>

                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <div className="space-y-2">
                                    <Label htmlFor="company_name">Company / business name</Label>
                                    <Input id="company_name" value={companyName} onChange={(e) => setCompanyName(e.target.value)} required disabled={saving} />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="agent_names">Agent names</Label>
                                    <Input id="agent_names" value={agentNamesRaw} onChange={(e) => setAgentNamesRaw(e.target.value)} required disabled={saving} />
                                    <p className="text-xs text-muted-foreground">Comma-separated, up to three names.</p>
                                </div>
                            </div>

                            <div className="space-y-4">
                                <div className="text-sm font-medium text-foreground">{persona.title} details</div>
                                {persona.slots.map((slot) => (
                                    <div key={slot.key} className="space-y-2">
                                        <Label htmlFor={`slot-${slot.key}`}>
                                            {slot.label}
                                            {slot.required ? <span className="text-red-400"> *</span> : null}
                                        </Label>
                                        {slot.kind === "textarea" || slot.kind === "list" || slot.kind === "kv-list" ? (
                                            <textarea
                                                id={`slot-${slot.key}`}
                                                placeholder={slot.placeholder}
                                                value={slotValues[slot.key] ?? ""}
                                                onChange={(e) => setSlot(slot.key, e.target.value)}
                                                disabled={saving}
                                                rows={slot.kind === "textarea" ? 3 : 4}
                                                className="flex w-full rounded-lg border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50"
                                            />
                                        ) : (
                                            <Input
                                                id={`slot-${slot.key}`}
                                                placeholder={slot.placeholder}
                                                value={slotValues[slot.key] ?? ""}
                                                onChange={(e) => setSlot(slot.key, e.target.value)}
                                                disabled={saving}
                                            />
                                        )}
                                        {slot.help ? <p className="text-xs text-muted-foreground">{slot.help}</p> : null}
                                    </div>
                                ))}
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="system_prompt">Additional instructions</Label>
                                <textarea
                                    id="system_prompt"
                                    name="system_prompt"
                                    value={formData.system_prompt}
                                    onChange={handleChange}
                                    disabled={saving}
                                    rows={4}
                                    className="flex w-full rounded-lg border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50"
                                />
                                <p className="text-xs text-muted-foreground">
                                    These are additional campaign facts only. Backend safety and persona prompts always remain enforced.
                                </p>
                            </div>

                            {error && (
                                <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/30 rounded-lg p-3">
                                    {error}
                                </div>
                            )}

                            <div className="flex gap-4">
                                <Button type="submit" disabled={saving || !formData.voice_id}>
                                    {saving ? (
                                        <>
                                            <Loader2 className="w-4 h-4 animate-spin" />
                                            Saving...
                                        </>
                                    ) : (
                                        "Save Campaign"
                                    )}
                                </Button>
                                <Button type="button" variant="outline" onClick={() => router.push(`/campaigns/${campaignId}`)} disabled={saving}>
                                    Cancel
                                </Button>
                            </div>
                        </form>
                    </motion.div>
                </div>
            )}
        </DashboardLayout>
    );
}
