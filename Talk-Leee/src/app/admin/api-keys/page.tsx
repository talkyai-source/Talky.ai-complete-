"use client";

import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { RouteGuard } from "@/components/guards/route-guard";
import { FeatureUnavailable } from "@/components/admin/feature-unavailable";

export default function ApiKeysPage() {
  return (
    <DashboardLayout title="API Keys" description="Programmatic access keys — not manageable from this console.">
      <RouteGuard
        title="API Keys"
        description="Restricted to platform and partner administrators."
        requiredRoles={["platform_admin", "partner_admin", "admin"]}
        unauthorizedRedirectTo="/403"
      >
        <FeatureUnavailable
          title="API key management"
          purpose="Issuing, scoping, rotating and revoking keys for programmatic access."
          reason="The backend implements these routes in app/api/v1/endpoints/api_keys.py, but that router is never registered in app/api/v1/routes.py, so /api/v1/admin/api-keys is not served and returns 404. Registering it is a backend change, not a frontend one."
        />
      </RouteGuard>
    </DashboardLayout>
  );
}
