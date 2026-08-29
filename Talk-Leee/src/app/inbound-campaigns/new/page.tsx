"use client";

import { useRouter } from "next/navigation";

import { InboundCampaignForm } from "@/components/inbound/inbound-campaign-form";
import { InboundErrorState, InboundLoadingState, InboundPermissionState } from "@/components/inbound/inbound-page-state";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { useAuth } from "@/hooks/useAuth";
import { getInboundCapabilities } from "@/lib/inbound-permissions";
import { useCreateInboundCampaign, useEffectivePermissions } from "@/lib/queries/inbound-queries";

export default function NewInboundCampaignPage() {
    const router = useRouter();
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
