"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { CampaignForm } from "@/components/campaigns/campaign-form";
import { CampaignWizard } from "@/components/campaigns/campaign-wizard";
import { ArrowLeft } from "lucide-react";
import { motion } from "framer-motion";

export default function NewCampaignPage() {
    const router = useRouter();
    // Default to the simplified knowledge-first wizard; power users can switch
    // to the classic slot-by-slot form (same one used for editing).
    const [classic, setClassic] = useState(false);

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
                <button
                    onClick={() => setClassic((c) => !c)}
                    className="text-sm text-muted-foreground hover:text-foreground transition-colors underline-offset-4 hover:underline"
                >
                    {classic ? "← Use the guided wizard" : "Prefer the detailed form? →"}
                </button>
            </motion.div>

            {classic ? <CampaignForm mode="create" /> : <CampaignWizard />}
        </DashboardLayout>
    );
}
