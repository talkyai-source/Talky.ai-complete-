"use client";

/**
 * Lean editor for a knowledge-driven campaign's basics.
 *
 * Knowledge campaigns carry no per-persona content slots, so the old slot-by-
 * slot <CampaignForm> doesn't fit. This edits exactly what such a campaign has:
 * name, company, persona, agent names, voice+provider (per-campaign), and goal.
 * Content is edited separately in the knowledge panel on the campaign page.
 */

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { BookOpen, Check, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { dashboardApi, PersonaType } from "@/lib/dashboard-api";
import { PERSONAS, parseAgentNames } from "@/lib/campaign-personas";
import { VoiceProviderPicker } from "@/components/campaigns/voice-provider-picker";
import { AgentNameGender, pruneGenders } from "@/components/campaigns/agent-name-gender";

export type CampaignBasicsEditorInitial = {
    name: string;
    description?: string;
    companyName: string;
    personaType: PersonaType;
    agentNames: string[];
    agentNameGenders?: Record<string, string>;
    voiceId: string;
    ttsProvider?: string | null;
    goal: string;
};

export function CampaignBasicsEditor({
    campaignId, initial,
}: { campaignId: string; initial: CampaignBasicsEditorInitial }) {
    const router = useRouter();

    const [name, setName] = useState(initial.name);
    const [companyName, setCompanyName] = useState(initial.companyName);
    const [personaType, setPersonaType] = useState<PersonaType>(initial.personaType);
    const [agentNamesRaw, setAgentNamesRaw] = useState(initial.agentNames.join(", "));
    const [agentGenders, setAgentGenders] = useState<Record<string, string>>(initial.agentNameGenders ?? {});
    const [voiceId, setVoiceId] = useState(initial.voiceId);
    const [provider, setProvider] = useState(initial.ttsProvider ?? "");
    const [goal, setGoal] = useState(initial.goal);

    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const agentNames = useMemo(() => parseAgentNames(agentNamesRaw), [agentNamesRaw]);
    const valid = name.trim() && companyName.trim() && agentNames.length >= 1 && voiceId;

    const onSave = async () => {
        setSaving(true);
        setError(null);
        try {
            await dashboardApi.updateCampaign(campaignId, {
                name: name.trim(),
                description: initial.description,
                system_prompt: goal.trim(),
                voice_id: voiceId,
                tts_provider: provider || undefined,
                goal: goal.trim() || undefined,
                persona_type: personaType,
                company_name: companyName.trim(),
                agent_names: agentNames,
                agent_name_genders: pruneGenders(agentGenders, agentNames),
                campaign_slots: {},
                knowledge_driven: true,
            });
            router.push(`/campaigns/${campaignId}`);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to save campaign");
            setSaving(false);
        }
    };

    return (
        <div className="mx-auto max-w-3xl">
            {error && (
                <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/50 dark:text-red-300">
                    {error}
                </div>
            )}

            <div className="rounded-2xl border border-gray-200 dark:border-white/10 bg-white dark:bg-white/5 p-5 shadow-sm space-y-5">
                <div className="grid gap-4 sm:grid-cols-2">
                    <div>
                        <Label htmlFor="ce-name">Campaign name</Label>
                        <Input id="ce-name" value={name} onChange={(e) => setName(e.target.value)} className="mt-1" />
                    </div>
                    <div>
                        <Label htmlFor="ce-company">Company name</Label>
                        <Input id="ce-company" value={companyName} onChange={(e) => setCompanyName(e.target.value)} className="mt-1" />
                    </div>
                </div>

                <div>
                    <Label>Persona</Label>
                    <div className="mt-1 grid gap-2 sm:grid-cols-3">
                        {PERSONAS.map((p) => (
                            <button
                                type="button"
                                key={p.value}
                                onClick={() => setPersonaType(p.value)}
                                className={`rounded-xl border p-3 text-left transition ${
                                    personaType === p.value
                                        ? "border-emerald-500 ring-1 ring-emerald-500 bg-emerald-50 dark:bg-emerald-950/40"
                                        : "border-gray-200 dark:border-white/10 hover:border-gray-300"}`}
                            >
                                <div className="text-sm font-semibold text-gray-900 dark:text-zinc-100">{p.title}</div>
                                <div className="mt-0.5 text-xs text-muted-foreground">{p.summary}</div>
                            </button>
                        ))}
                    </div>
                </div>

                <div>
                    <Label htmlFor="ce-agents">Agent names</Label>
                    <Input id="ce-agents" value={agentNamesRaw} onChange={(e) => setAgentNamesRaw(e.target.value)} placeholder="Alex, Jordan, Sam" className="mt-1" />
                    <p className="mt-1 text-xs text-muted-foreground">
                        1–3 names, comma-separated.
                        {agentNames.length > 0 && <span className="ml-1 text-emerald-600 dark:text-emerald-400">{agentNames.length} name{agentNames.length > 1 ? "s" : ""}.</span>}
                    </p>
                    <AgentNameGender names={agentNames} value={agentGenders} onChange={setAgentGenders} />
                </div>

                <div>
                    <Label htmlFor="ce-goal">Goal <span className="text-muted-foreground font-normal">(optional)</span></Label>
                    <textarea
                        id="ce-goal" value={goal} onChange={(e) => setGoal(e.target.value)} rows={2}
                        className="mt-1 w-full rounded-md border border-gray-300 dark:border-white/15 bg-white dark:bg-zinc-900 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
                    />
                </div>

                <VoiceProviderPicker
                    personaType={personaType}
                    voiceId={voiceId}
                    initialProvider={provider}
                    onVoiceChange={(id) => setVoiceId(id)}
                    onProviderChange={setProvider}
                />

                <div className="flex items-start gap-2 rounded-lg border border-gray-200 dark:border-white/10 bg-gray-50 dark:bg-white/5 px-3 py-2.5 text-xs text-muted-foreground">
                    <BookOpen className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />
                    This campaign answers from its knowledge base. Edit that content (upload, tree,
                    spoken answers) in the <span className="font-medium text-gray-900 dark:text-zinc-100">Knowledge base</span> panel on the campaign page.
                </div>

                <div className="flex justify-end gap-2 pt-1">
                    <Button variant="ghost" onClick={() => router.push(`/campaigns/${campaignId}`)} disabled={saving}>Cancel</Button>
                    <Button onClick={onSave} disabled={!valid || saving}>
                        {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                        {saving ? "Saving…" : "Save changes"}
                    </Button>
                </div>
            </div>
        </div>
    );
}

export default CampaignBasicsEditor;
