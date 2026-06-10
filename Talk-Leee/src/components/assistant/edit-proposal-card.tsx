"use client";

/*
 * Edit-proposal card for the assistant diff accept/reject flow.
 *
 * When an edit tool previews a change, the backend emits an `edit_proposal`
 * frame; this card renders the before→after diff with Apply / Reject buttons.
 * Apply/Reject send `apply_proposal` / `reject_proposal` back over the WS; the
 * backend re-runs the SAME tool with confirm=true (the args live server-side, so
 * the applied change always equals the previewed one).
 */

import { Check, X, CheckCircle2, XCircle, AlertTriangle } from "lucide-react";
import { DiffView, type DiffChange } from "./diff-view";

export type ProposalCampaign = { campaign_id: string; name?: string; changes: DiffChange[] };
export type ProposalStatus = "pending" | "applied" | "rejected" | "error";

export interface ProposalData {
    proposalId: string;
    tool: string;
    warnings?: string[];
    changes?: DiffChange[];
    campaigns?: ProposalCampaign[];
    status: ProposalStatus;
    error?: string;
}

const TOOL_TITLES: Record<string, string> = {
    update_campaign_config: "Update campaign",
    update_knowledge_node: "Update knowledge",
    manage_lead: "Update contact",
    apply_campaign_voice: "Change voice",
};

export function EditProposalCard({
    proposal,
    onApply,
    onReject,
}: {
    proposal: ProposalData;
    onApply: (id: string) => void;
    onReject: (id: string) => void;
}) {
    const { proposalId, tool, warnings, changes, campaigns, status, error } = proposal;
    const title = TOOL_TITLES[tool] ?? "Proposed change";
    const pending = status === "pending";

    return (
        <div className="rounded-xl border border-cyan-500/30 bg-cyan-500/5 p-3 text-sm">
            <div className="mb-2 flex items-center justify-between gap-2">
                <span className="font-semibold text-foreground">{title}</span>
                {status === "applied" && (
                    <span className="inline-flex items-center gap-1 text-xs font-semibold text-emerald-600 dark:text-emerald-400">
                        <CheckCircle2 className="h-3.5 w-3.5" />Applied
                    </span>
                )}
                {status === "rejected" && (
                    <span className="inline-flex items-center gap-1 text-xs font-semibold text-muted-foreground">
                        <XCircle className="h-3.5 w-3.5" />Rejected
                    </span>
                )}
                {status === "error" && (
                    <span className="inline-flex items-center gap-1 text-xs font-semibold text-red-600 dark:text-red-400">
                        <AlertTriangle className="h-3.5 w-3.5" />Failed
                    </span>
                )}
            </div>

            {campaigns && campaigns.length > 0 ? (
                <div className="space-y-3">
                    {campaigns.map((c) => (
                        <div key={c.campaign_id}>
                            {c.name && <div className="mb-1 text-xs font-semibold text-foreground">{c.name}</div>}
                            <DiffView changes={c.changes} />
                        </div>
                    ))}
                </div>
            ) : (
                <DiffView changes={changes ?? []} />
            )}

            {warnings && warnings.length > 0 && (
                <ul className="mt-2 space-y-0.5">
                    {warnings.map((w, i) => (
                        <li key={i} className="flex gap-1.5 text-xs text-amber-700 dark:text-amber-400">
                            <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                            <span>{w}</span>
                        </li>
                    ))}
                </ul>
            )}

            {status === "error" && error && (
                <p className="mt-2 text-xs text-red-600 dark:text-red-400">{error}</p>
            )}

            {pending && (
                <div className="mt-3 flex gap-2">
                    <button
                        type="button"
                        onClick={() => onApply(proposalId)}
                        className="inline-flex items-center gap-1 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-emerald-500"
                    >
                        <Check className="h-3.5 w-3.5" />Apply
                    </button>
                    <button
                        type="button"
                        onClick={() => onReject(proposalId)}
                        className="inline-flex items-center gap-1 rounded-lg border border-border bg-background px-3 py-1.5 text-xs font-semibold text-foreground transition-colors hover:bg-muted"
                    >
                        <X className="h-3.5 w-3.5" />Reject
                    </button>
                </div>
            )}
        </div>
    );
}
