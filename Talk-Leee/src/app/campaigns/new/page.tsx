"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { CampaignForm } from "@/components/campaigns/campaign-form";
import { CampaignWizard } from "@/components/campaigns/campaign-wizard";
import { INBOUND_RETURN, afterCampaignCreateHref } from "@/lib/campaign-create-return";
import { ArrowLeft } from "lucide-react";
import { motion } from "framer-motion";

function NewCampaignPageInner() {
    const router = useRouter();
    // `?for=inbound` — the inbound number form sent the user here to create
    // the AI campaign it needs; go back there with the new draft selected.
    const returnTo = useSearchParams().get("for");
    const forInbound = returnTo === INBOUND_RETURN;
    const afterCreateHref = (campaignId: string) => afterCampaignCreateHref(campaignId, returnTo);
    // Default to the simplified knowledge-first wizard; power users can switch
    // to the classic slot-by-slot form (same one used for editing).
    const [classic, setClassic] = useState(false);

    return (
        <DashboardLayout
            title={forInbound ? "Create the AI campaign for your inbound number" : "Create Campaign"}
            description={forInbound ? "Saved as a draft, then you go straight back to the inbound number setup with it selected" : "Set up a new voice campaign"}
        >
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
                    {forInbound ? "Back to inbound number setup" : "Back to campaigns"}
                </button>
                <button
                    onClick={() => setClassic((c) => !c)}
                    className="text-sm text-muted-foreground hover:text-foreground transition-colors underline-offset-4 hover:underline"
                >
                    {classic ? "← Use the guided wizard" : "Prefer the detailed form? →"}
                </button>
            </motion.div>

            {classic ? <CampaignForm mode="create" afterCreateHref={afterCreateHref} /> : <CampaignWizard afterCreateHref={afterCreateHref} />}
        </DashboardLayout>
    );
}

export default function NewCampaignPage() {
    // useSearchParams needs a Suspense boundary for the static prerender.
    return (
        <Suspense fallback={null}>
            <NewCampaignPageInner />
        </Suspense>
    );
}
