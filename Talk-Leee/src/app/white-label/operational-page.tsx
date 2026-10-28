import { notFound, redirect } from "next/navigation";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { getServerMe, shouldBypassAuthOnThisRequest, WHITE_LABEL_ADMIN_ROLE } from "@/lib/server-auth";
import { getWhiteLabelBranding } from "@/lib/white-label/branding";
import { ResellerUnavailable, type ResellerArea } from "./unavailable";

/** Retains the existing server-side role gate; never treats a branding slug as authority. */
export async function unavailablePartnerPage(partner: string, area: Exclude<ResellerArea, "workspace">) {
    const branding = getWhiteLabelBranding(partner);
    if (!branding) notFound();
    const segment = area === "overview" ? "dashboard" : area;
    const nextPath = `/white-label/${encodeURIComponent(branding.partnerId)}/${segment}`;
    if (!(await shouldBypassAuthOnThisRequest())) {
        const me = await getServerMe();
        if (!me) redirect(`/auth/login?next=${encodeURIComponent(nextPath)}`);
        if (me.role !== WHITE_LABEL_ADMIN_ROLE) redirect("/403");
    }
    return (
        <DashboardLayout title={`${branding.displayName} White-label Workspace`}
            description="Branding preview is available. Reseller operations are not included in this release.">
            <ResellerUnavailable area={area} partnerId={branding.partnerId} />
        </DashboardLayout>
    );
}
