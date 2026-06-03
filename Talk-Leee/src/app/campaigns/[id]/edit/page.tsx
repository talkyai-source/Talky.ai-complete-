"use client";

/*
 * Edit-campaign page.
 *
 * Reuses the exact same <CampaignForm> component the create page uses,
 * just with `mode="edit"` and prefilled `initialData`. This guarantees
 * visual + behavioural parity between create and edit — every persona
 * picker, voice card, slot field, helper text, and validation rule is
 * identical, by construction.
 */

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import {
    CampaignForm,
    type CampaignFormInitial,
} from "@/components/campaigns/campaign-form";
import { CampaignBasicsEditor } from "@/components/campaigns/campaign-basics-editor";
import { dashboardApi, type PersonaType } from "@/lib/dashboard-api";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { motion } from "framer-motion";

/**
 * Normalises a persisted slot value (any of: string, string[], or
 * [{issue, solution}, ...]) back into the textarea-friendly string the
 * shared form keeps in `slotValues`. Mirrors the parsers in
 * `@/lib/campaign-personas` (parseList / parseKvList) so the round-trip
 * load → edit → save preserves the user's text exactly.
 */
function slotValueToText(value: unknown): string {
    if (Array.isArray(value)) {
        // kv-list shape: [{issue, solution}, ...] from parseKvList
        if (
            value.length > 0 &&
            value.every(
                (item) => item && typeof item === "object" && "issue" in (item as object),
            )
        ) {
            return value
                .map((item) => {
                    const issue = String((item as { issue?: unknown }).issue ?? "").trim();
                    const solution = String((item as { solution?: unknown }).solution ?? "").trim();
                    return solution ? `${issue} | ${solution}` : issue;
                })
                .filter(Boolean)
                .join("\n");
        }
        // plain list — one per line
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
    const [error, setError] = useState("");
    const [initial, setInitial] = useState<CampaignFormInitial | null>(null);
    const [knowledgeDriven, setKnowledgeDriven] = useState(false);
    const [ttsProvider, setTtsProvider] = useState<string | null>(null);

    useEffect(() => {
        let cancelled = false;
        async function load() {
            try {
                setLoading(true);
                const { campaign } = await dashboardApi.getCampaign(campaignId);
                if (cancelled) return;

                const scriptConfig = campaign.script_config ?? {};
                const slots: Record<string, string> = {};
                for (const [key, val] of Object.entries(scriptConfig.campaign_slots ?? {})) {
                    slots[key] = slotValueToText(val);
                }

                setInitial({
                    name: campaign.name ?? "",
                    description: campaign.description ?? "",
                    // The persisted "additional instructions" can live in either
                    // script_config.additional_instructions OR the legacy
                    // top-level system_prompt; prefer the former.
                    system_prompt:
                        scriptConfig.additional_instructions ?? campaign.system_prompt ?? "",
                    voice_id: campaign.voice_id ?? "",
                    goal: (campaign as { goal?: string }).goal ?? "",
                    persona_type: (scriptConfig.persona_type ?? "lead_gen") as PersonaType,
                    company_name: scriptConfig.company_name ?? "",
                    agent_names: scriptConfig.agent_names ?? [],
                    slots,
                });
                setKnowledgeDriven(
                    Boolean((scriptConfig as Record<string, unknown>).knowledge_driven),
                );
                setTtsProvider((campaign as { tts_provider?: string | null }).tts_provider ?? null);
            } catch (err) {
                if (cancelled) return;
                setError(err instanceof Error ? err.message : "Failed to load campaign");
            } finally {
                if (!cancelled) setLoading(false);
            }
        }
        if (campaignId) void load();
        return () => {
            cancelled = true;
        };
    }, [campaignId]);

    return (
        <DashboardLayout
            title="Edit Campaign"
            description="Update campaign details — same form as creation, prefilled with the saved values."
        >
            <motion.div
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                className="mb-6"
            >
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
            ) : error ? (
                <div className="max-w-2xl">
                    <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/30 rounded-lg p-3">
                        {error}
                    </div>
                </div>
            ) : initial ? (
                knowledgeDriven ? (
                    <CampaignBasicsEditor
                        campaignId={campaignId}
                        initial={{
                            name: initial.name,
                            description: initial.description,
                            companyName: initial.company_name,
                            personaType: initial.persona_type,
                            agentNames: initial.agent_names,
                            voiceId: initial.voice_id,
                            ttsProvider,
                            goal: initial.goal,
                        }}
                    />
                ) : (
                    <CampaignForm mode="edit" campaignId={campaignId} initialData={initial} />
                )
            ) : null}
        </DashboardLayout>
    );
}
