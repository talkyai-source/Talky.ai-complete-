"use client";

/**
 * "Apply this voice/provider to campaigns" — shown after an AI Options save.
 *
 * TTS provider+voice are per-campaign, so saving the account default doesn't
 * touch existing campaigns. This modal lets the user push the just-saved
 * provider+voice onto the campaigns they pick (Select all, or individual ticks).
 * Unselected campaigns keep their own engine.
 */

import { useEffect, useState } from "react";
import { CheckSquare, Loader2, Square } from "lucide-react";

import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import { Campaign, dashboardApi } from "@/lib/dashboard-api";

export function ApplyToCampaignsModal({
    open, provider, voiceId, voiceLabel, onClose,
}: {
    open: boolean;
    provider: string;
    voiceId: string;
    voiceLabel?: string;
    onClose: () => void;
}) {
    const [campaigns, setCampaigns] = useState<Campaign[]>([]);
    const [loading, setLoading] = useState(true);
    const [selected, setSelected] = useState<Set<string>>(new Set());
    const [applying, setApplying] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [doneCount, setDoneCount] = useState<number | null>(null);

    useEffect(() => {
        if (!open) return;
        let cancelled = false;
        setLoading(true); setError(null); setDoneCount(null); setSelected(new Set());
        dashboardApi.listCampaigns()
            .then((r) => { if (!cancelled) setCampaigns(r.campaigns); })
            .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load campaigns"); })
            .finally(() => { if (!cancelled) setLoading(false); });
        return () => { cancelled = true; };
    }, [open]);

    const allSelected = campaigns.length > 0 && selected.size === campaigns.length;
    const label = voiceLabel || voiceId;

    const toggle = (id: string) =>
        setSelected((p) => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n; });
    const toggleAll = () =>
        setSelected(allSelected ? new Set() : new Set(campaigns.map((c) => c.id)));

    const currentOf = (c: Campaign) =>
        (c.tts_provider || "global").toString();

    const apply = async () => {
        setApplying(true); setError(null);
        try {
            const res = await dashboardApi.applyTtsConfig({
                tts_provider: provider,
                tts_voice_id: voiceId,
                campaign_ids: [...selected],
            });
            setDoneCount(res.count);
            setTimeout(onClose, 1200);
        } catch (e) {
            setError(e instanceof Error ? e.message : "Failed to apply to campaigns");
            setApplying(false);
        }
    };

    const footer = (
        <div className="flex items-center justify-between gap-2">
            <span className="text-xs text-muted-foreground">
                {doneCount !== null
                    ? `Applied to ${doneCount} campaign${doneCount === 1 ? "" : "s"}.`
                    : `${selected.size} selected`}
            </span>
            <div className="flex gap-2">
                <Button variant="ghost" onClick={onClose} disabled={applying}>Skip</Button>
                <Button onClick={apply} disabled={applying || selected.size === 0 || doneCount !== null}>
                    {applying ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                    {applying ? "Applying…" : `Apply to ${selected.size || ""} ${selected.size === 1 ? "campaign" : "campaigns"}`}
                </Button>
            </div>
        </div>
    );

    return (
        <Modal
            open={open}
            onOpenChange={(o) => { if (!o) onClose(); }}
            title="Apply this voice to campaigns?"
            description={`Use ${label} (${provider}) for the campaigns you pick. Provider + voice are per-campaign, so anything you don't select keeps its own voice.`}
            size="md"
            footer={footer}
        >
            {error && (
                <div className="mb-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/50 dark:text-red-300">
                    {error}
                </div>
            )}
            {loading ? (
                <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
                    <Loader2 className="h-4 w-4 animate-spin" /> Loading campaigns…
                </div>
            ) : campaigns.length === 0 ? (
                <p className="py-6 text-sm text-muted-foreground">No campaigns yet — this will apply to new ones you create.</p>
            ) : (
                <div className="space-y-1">
                    <button
                        type="button"
                        onClick={toggleAll}
                        className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm font-medium text-gray-900 dark:text-zinc-100 hover:bg-gray-50 dark:hover:bg-white/5"
                    >
                        {allSelected ? <CheckSquare className="h-4 w-4 text-emerald-600" /> : <Square className="h-4 w-4 text-muted-foreground" />}
                        Select all ({campaigns.length})
                    </button>
                    <div className="max-h-72 overflow-y-auto">
                        {campaigns.map((c) => {
                            const checked = selected.has(c.id);
                            return (
                                <button
                                    type="button"
                                    key={c.id}
                                    onClick={() => toggle(c.id)}
                                    className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left hover:bg-gray-50 dark:hover:bg-white/5"
                                >
                                    {checked ? <CheckSquare className="h-4 w-4 shrink-0 text-emerald-600" /> : <Square className="h-4 w-4 shrink-0 text-muted-foreground" />}
                                    <span className="min-w-0 flex-1">
                                        <span className="block truncate text-sm font-medium text-gray-900 dark:text-zinc-100">{c.name}</span>
                                        <span className="block truncate text-xs text-muted-foreground">
                                            currently: {currentOf(c)} · {c.voice_id || "—"}
                                        </span>
                                    </span>
                                </button>
                            );
                        })}
                    </div>
                </div>
            )}
        </Modal>
    );
}

export default ApplyToCampaignsModal;
