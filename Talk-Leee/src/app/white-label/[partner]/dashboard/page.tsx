import { notFound } from "next/navigation";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { PartnerDashboard, getPartnerDashboardStats } from "@/components/dashboard/PartnerDashboard";
import { getWhiteLabelBranding } from "@/lib/white-label/branding";

type PageProps = {
    params: Promise<{ partner: string }>;
};

export default async function WhiteLabelPartnerDashboardPage(props: PageProps) {
    const { partner } = await props.params;
    const branding = getWhiteLabelBranding(partner);
    if (!branding) notFound();

    const stats = getPartnerDashboardStats(partner);

    return (
        <DashboardLayout
            title={`${branding.displayName} Partner Dashboard`}
            description="Aggregated usage metrics across all sub-tenants."
        >
            <PartnerDashboard stats={stats} />
        </DashboardLayout>
    );
}
