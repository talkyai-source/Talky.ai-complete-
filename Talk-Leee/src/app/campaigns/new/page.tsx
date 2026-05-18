"use client";

import { useRouter } from "next/navigation";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { CampaignForm } from "@/components/campaigns/campaign-form";
import { ArrowLeft } from "lucide-react";
import { motion } from "framer-motion";

export default function NewCampaignPage() {
    const router = useRouter();

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

            <CampaignForm mode="create" />
        </DashboardLayout>
    );
}
