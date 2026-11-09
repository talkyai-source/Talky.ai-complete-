"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { CampaignForm } from "@/components/campaigns/campaign-form";
import { CampaignWizard } from "@/components/campaigns/campaign-wizard";
import { useAuth } from "@/hooks/useAuth";
import { campaignActions, useKnownPermissions } from "@/lib/permissions";
import { RoleNotice } from "@/components/states/role-notice";
import { ArrowLeft } from "lucide-react";
import { motion } from "framer-motion";

// QA F1-22: a new outbound campaign's draft is kept in this tab through a
// reload, Back/Forward, other pages or a lost sign-in, under one key per
// account, so another tenant or user signing in on this tab starts empty.
function draftKey(scope: string, editor: "wizard" | "form") {
    return `talky:campaign-new:${scope}:${editor}`;
}

function discardDraft(key: string) {
    try {
        window.sessionStorage.removeItem(key);
    } catch {
        // Storage unavailable: nothing was kept.
    }
}

export default function NewCampaignPage() {
    const router = useRouter();
    // Default to the simplified knowledge-first wizard; power users can switch
    // to the classic slot-by-slot form (same one used for editing).
    const [classic, setClassic] = useState(false);
    const { user } = useAuth();
    // The last verified account. A passing re-check (user briefly null) keeps
    // the editor mounted; a different account remounts it with its own draft.
    const [scope, setScope] = useState<string | null>(null);
    const verified = user ? `${user.tenant_id ?? "no-tenant"}:${user.id}` : null;
    if (verified && verified !== scope) setScope(verified);
    const [restarts, setRestarts] = useState(0);
    // QA F1-21: a role without campaigns:create is told so, not given a form.
    const mayCreate = campaignActions(useKnownPermissions()).create;
    const startOver = (key: string) => {
        discardDraft(key);
        setRestarts((count) => count + 1);
    };

    return (
        <DashboardLayout title="Create Campaign" description="Set up a new voice campaign">
            <motion.div
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                className="mb-6 flex items-center justify-between"
            >
                <button
                    onClick={() => router.back()}
                    className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
                >
                    <ArrowLeft className="w-4 h-4" />
                    Back to campaigns
                </button>
                {mayCreate && <button
                    onClick={() => setClassic((c) => !c)}
                    className="text-sm text-muted-foreground hover:text-foreground transition-colors underline-offset-4 hover:underline"
                >
                    {classic ? "← Use the guided wizard" : "Prefer the detailed form? →"}
                </button>}
            </motion.div>

            {!mayCreate ? (
                <RoleNotice
                    title="Your role can't create campaigns"
                    message="You can view campaigns and their results. Ask an account admin if you need a new one."
                    href="/campaigns"
                    linkLabel="View campaigns"
                />
            ) : scope && (classic
                ? <CampaignForm key={`${scope}:form:${restarts}`} mode="create" draftStorageKey={draftKey(scope, "form")} onStartOver={() => startOver(draftKey(scope, "form"))} />
                : <CampaignWizard key={`${scope}:wizard:${restarts}`} draftStorageKey={draftKey(scope, "wizard")} onStartOver={() => startOver(draftKey(scope, "wizard"))} />)}
        </DashboardLayout>
    );
}
