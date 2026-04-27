import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { RouteGuard } from "@/components/guards/route-guard";
import { AdminOperationsConsole } from "@/components/admin/admin-operations-console";

export default function AdminPage() {
    return (
        <DashboardLayout title="Audit & Access" description="Monitor platform activity, security signals, suspension controls, and public frontend configuration.">
            <RouteGuard
                title="Audit & Access"
                description="Restricted to platform and partner administrators."
                requiredRoles={["platform_admin", "partner_admin", "admin"]}
                unauthorizedRedirectTo="/403"
            >
                <AdminOperationsConsole />
            </RouteGuard>
        </DashboardLayout>
    );
}
