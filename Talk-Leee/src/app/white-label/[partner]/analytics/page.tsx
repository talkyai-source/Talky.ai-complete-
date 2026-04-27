import { notFound, redirect } from "next/navigation";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { getWhiteLabelBranding } from "@/lib/white-label/branding";
import { getServerMe, shouldBypassAuthOnThisRequest, WHITE_LABEL_ADMIN_ROLE } from "@/lib/server-auth";
import { PartnerAnalyticsClient } from "./partner-analytics-client";

type PageProps = {
    params: Promise<{ partner: string }>;
};

export default async function WhiteLabelPartnerAnalyticsPage(props: PageProps) {
    const { partner } = await props.params;
    const branding = getWhiteLabelBranding(partner);
    if (!branding) notFound();

    const nextPath = `/white-label/${branding.partnerId}/analytics`;

    if (!(await shouldBypassAuthOnThisRequest())) {
        const me = await getServerMe();
        if (!me) redirect(`/auth/login?next=${encodeURIComponent(nextPath)}`);
        if (me.role !== WHITE_LABEL_ADMIN_ROLE) redirect("/403");
    }

    return (
        <DashboardLayout
            title={`${branding.displayName} Usage Analytics`}
            description="High-level aggregated usage across all sub-tenants."
        >
            <PartnerAnalyticsClient partnerId={branding.partnerId} />
        </DashboardLayout>
    );
}
