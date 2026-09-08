"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";

import { CampaignForm } from "@/components/campaigns/campaign-form";
import { CampaignWizard } from "@/components/campaigns/campaign-wizard";
import { InboundCampaignForm } from "@/components/inbound/inbound-campaign-form";
import { InboundErrorState, InboundLoadingState, InboundPermissionState } from "@/components/inbound/inbound-page-state";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { useAuth } from "@/hooks/useAuth";
import { afterCampaignCreateHref, INBOUND_RETURN } from "@/lib/campaign-create-return";
import { getInboundCapabilities } from "@/lib/inbound-permissions";
import { useCreateInboundCampaign, useEffectivePermissions } from "@/lib/queries/inbound-queries";

/**
 * Creating an inbound campaign (2026-09-09) is the SAME experience as creating
 * an outbound one — the identical wizard (or classic form): persona, agent
 * names, brief, guidance, voice, knowledge upload, contact fields, review with
 * prompt preview. The campaign is born inbound. Step 2 then attaches what only
 * an inbound campaign has: the verified number, trunk, opening, hours and
 * safety routing. Nothing is picked from or shared with outbound campaigns.
 */
function StepIndicator({ step }: { step: 1 | 2 }) {
    const items = ["Campaign & agent", "Number & routing"] as const;
    return (
        <ol className="mb-6 flex items-center gap-2" aria-label="Inbound campaign setup steps">
            {items.map((label, i) => {
                const n = (i + 1) as 1 | 2;
                return (
                    <li key={label} className="flex flex-1 items-center gap-2">
                        <div className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${
                            n < step ? "bg-emerald-500 text-white" : n === step ? "bg-emerald-100 text-emerald-700 ring-2 ring-emerald-500 dark:bg-emerald-950 dark:text-emerald-300" : "bg-gray-100 text-gray-400 dark:bg-white/10"}`}>{n}</div>
                        <span className={`text-sm font-medium ${n === step ? "text-foreground" : "text-muted-foreground"}`}>{label}</span>
                        {i < items.length - 1 ? <div className="h-px flex-1 bg-gray-200 dark:bg-white/10" /> : null}
                    </li>
                );
            })}
        </ol>
    );
}

function NewInboundCampaignPageInner() {
    const router = useRouter();
    const { user } = useAuth();
    const permissions = useEffectivePermissions();
    const create = useCreateInboundCampaign();
    const capabilities = getInboundCapabilities(user?.role, permissions.isSuccess ? permissions.data.permissions : undefined);
    // Step 2 carries the campaign created in step 1.
    const campaignId = useSearchParams().get("campaign_id");
    const [classic, setClassic] = useState(false);

    return (
        <DashboardLayout
            title="New Inbound Campaign"
            description={campaignId ? "Step 2 of 2 — the verified number, trunk, opening, hours and safe fallback" : "Step 1 of 2 — the campaign and its agent, exactly as for an outbound campaign"}
        >
            {permissions.isLoading ? <InboundLoadingState label="Checking create and assignment permissions…" /> : permissions.isError ? (
                <InboundErrorState title="Permissions could not be verified" message="Creating or assigning a public number is disabled until server permissions can be confirmed." onRetry={() => void permissions.refetch()} />
            ) : !capabilities.canCreate || !capabilities.canAssignNumber ? (
                <InboundPermissionState action="create and assign an inbound campaign" />
            ) : campaignId ? (
                <>
                    <StepIndicator step={2} />
                    <InboundCampaignForm
                        mode="create"
                        initialCampaignId={campaignId}
                        lockCampaign
                        pending={create.isPending}
                        canAssignNumber={capabilities.canAssignNumber}
                        onSubmit={async (input) => {
                            const created = await create.mutateAsync({ input, didNumber: input.did_number });
                            router.push(`/inbound-campaigns/${created.id}`);
                        }}
                    />
                </>
            ) : (
                <>
                    <StepIndicator step={1} />
                    <div className="mb-6 flex items-center justify-between">
                        <Link href="/inbound-campaigns" className="flex items-center gap-2 text-sm text-muted-foreground transition-colors hover:text-foreground">
                            <ArrowLeft className="h-4 w-4" aria-hidden />
                            Back to inbound campaigns
                        </Link>
                        <button type="button" onClick={() => setClassic((c) => !c)} className="text-sm text-muted-foreground underline-offset-4 transition-colors hover:text-foreground hover:underline">
                            {classic ? "← Use the guided wizard" : "Prefer the detailed form? →"}
                        </button>
                    </div>
                    {classic
                        ? <CampaignForm mode="create" direction="inbound" afterCreateHref={(id) => afterCampaignCreateHref(id, INBOUND_RETURN)} />
                        : <CampaignWizard direction="inbound" afterCreateHref={(id) => afterCampaignCreateHref(id, INBOUND_RETURN)} />}
                </>
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
