import { notFound } from "next/navigation";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { getWhiteLabelBranding } from "@/lib/white-label/branding";
import { PartnerTenantsClient } from "./tenants-client";

type PageProps = {
    params: Promise<{ partner: string }>;
};

export default async function WhiteLabelPartnerTenantsPage(props: PageProps) {
    const { partner } = await props.params;
    const branding = getWhiteLabelBranding(partner);
    if (!branding) notFound();

    return (
        <DashboardLayout
            title={`${branding.displayName} Tenants`}
            description="Create and manage sub-tenants while enforcing strict resource limits."
        >
            <PartnerTenantsClient partnerId={branding.partnerId} partnerDisplayName={branding.displayName} />
        </DashboardLayout>
    );
}

