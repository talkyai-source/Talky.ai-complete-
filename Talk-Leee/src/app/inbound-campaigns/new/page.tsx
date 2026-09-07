"use client";

import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { InboundCampaignForm } from "@/components/inbound/inbound-campaign-form";
import { InboundErrorState, InboundLoadingState, InboundPermissionState } from "@/components/inbound/inbound-page-state";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { useAuth } from "@/hooks/useAuth";
import { getInboundCapabilities } from "@/lib/inbound-permissions";
import { useCreateInboundCampaign, useEffectivePermissions } from "@/lib/queries/inbound-queries";

function NewInboundCampaignPageInner() {
    const router = useRouter();
    // Set when the user came back from "Create a new AI campaign" — the new
    // draft is pre-selected so the round trip lands them exactly where they were.
    const preselectedCampaignId = useSearchParams().get("campaign_id");
    const { user } = useAuth();
    const permissions = useEffectivePermissions();
    const create = useCreateInboundCampaign();
    const capabilities = getInboundCapabilities(user?.role, permissions.isSuccess ? permissions.data.permissions : undefined);

    return (
        <DashboardLayout title="New Inbound Campaign" description="Configure a verified number, AI agent, trunk, and safe fallback">
            {permissions.isLoading ? <InboundLoadingState label="Checking create and assignment permissions…" /> : permissions.isError ? (
                <InboundErrorState title="Permissions could not be verified" message="Creating or assigning a public number is disabled until server permissions can be confirmed." onRetry={() => void permissions.refetch()} />
            ) : !capabilities.canCreate || !capabilities.canAssignNumber ? (
                <InboundPermissionState action="create and assign an inbound campaign" />
            ) : (
                <InboundCampaignForm
                    mode="create"
                    initialCampaignId={preselectedCampaignId}
                    pending={create.isPending}
                    canAssignNumber={capabilities.canAssignNumber}
                    onSubmit={async (input) => {
                        const created = await create.mutateAsync({ input, didNumber: input.did_number });
                        router.push(`/inbound-campaigns/${created.id}`);
                    }}
                />
            )}
        </DashboardLayout>
    );
}

export default function NewInboundCampaignPage() {
    // useSearchParams needs a Suspense boundary for the static prerender.
    return (
        <Suspense fallback={null}>
            <NewInboundCampaignPageInner />
        </Suspense>
    );
}
