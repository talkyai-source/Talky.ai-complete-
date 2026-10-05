"use client";

import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { ActionHistory } from "@/components/assistant/action-history";

export default function AssistantActionsPage() {
    return <DashboardLayout title="Actions" description="Review saved assistant actions and their outcomes.">
        <ActionHistory />
    </DashboardLayout>;
}
