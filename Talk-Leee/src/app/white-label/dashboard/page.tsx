import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { ResellerUnavailable } from "../unavailable";

// The existing dashboard layout retains its server-side authorization check.
export default function WhiteLabelDashboardPage() {
    return (
        <DashboardLayout title="White-label Workspace"
            description="Branding preview is available. Reseller operations are not included in this release.">
            <ResellerUnavailable area="workspace" />
        </DashboardLayout>
    );
}
