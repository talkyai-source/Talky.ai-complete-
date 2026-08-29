"use client";

import { useParams, useRouter } from "next/navigation";

import { InboundCampaignForm } from "@/components/inbound/inbound-campaign-form";
import { InboundErrorState, InboundLoadingState, InboundPermissionState } from "@/components/inbound/inbound-page-state";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { useAuth } from "@/hooks/useAuth";
import { getInboundCapabilities } from "@/lib/inbound-permissions";
import { useEffectivePermissions, useInboundCampaign, useUpdateInboundCampaign } from "@/lib/queries/inbound-queries";

export default function EditInboundCampaignPage() {
    const params = useParams<{ id: string }>();
    const id = typeof params.id === "string" ? params.id : "";
    const router = useRouter();
    const { user } = useAuth();
    const permissions = useEffectivePermissions();
    const capabilities = getInboundCapabilities(user?.role, permissions.isSuccess ? permissions.data.permissions : undefined);
    const campaignQuery = useInboundCampaign(id, permissions.isSuccess && capabilities.canView);
    const update = useUpdateInboundCampaign(id);
    const campaign = campaignQuery.data;

    return (
        <DashboardLayout title="Edit Inbound Campaign" description="Update an inactive configuration without silently changing live routing">
            {permissions.isLoading ? (
                <InboundLoadingState label="Checking edit and routing permissions…" />
            ) : permissions.isError ? (
                <InboundErrorState title="Permissions could not be verified" message="Editing is disabled until server permissions can be confirmed." onRetry={() => void permissions.refetch()} />
            ) : !capabilities.canView || !capabilities.canEdit ? (
                <InboundPermissionState action="edit inbound campaigns" />
            ) : campaignQuery.isLoading ? (
                <InboundLoadingState />
            ) : campaignQuery.isError || !campaign ? (
                <InboundErrorState title="Campaign is unavailable" message={campaignQuery.error instanceof Error ? campaignQuery.error.message : "The campaign could not be loaded."} onRetry={() => void campaignQuery.refetch()} />
            ) : campaign.status === "active" || campaign.status === "archived" ? (
                <InboundErrorState
                    title={campaign.status === "active" ? "Deactivate before editing" : "Archived campaigns are read-only"}
                    message={campaign.status === "active" ? "Live DID routing cannot be edited. Deactivate this campaign, then return to edit the configuration." : "Create a new campaign if this routing configuration is needed again."}
                />
            ) : (
                <InboundCampaignForm
                    key={campaign.id}
                    mode="edit"
                    initialValue={campaign}
                    pending={update.isPending}
                    canAssignNumber={capabilities.canAssignNumber}
                    onSubmit={async (input) => {
                        const didChanged = input.did_number !== (campaign.phone_number?.e164 ?? "");
                        const trunkChanged = input.sip_trunk_id !== campaign.sip_trunk_id;
                        await update.mutateAsync({
                            input,
                            expectedVersion: campaign.version,
                            assignment: capabilities.canAssignNumber && (didChanged || trunkChanged) ? {
                                didNumber: input.did_number,
                                sipTrunkId: input.sip_trunk_id,
                                reason: "Routing assignment changed through the inbound campaign editor.",
                            } : undefined,
                        });
                        router.push(`/inbound-campaigns/${id}`);
                    }}
                />
            )}
        </DashboardLayout>
    );
}
