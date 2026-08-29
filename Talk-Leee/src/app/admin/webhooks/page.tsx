"use client";

import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { RouteGuard } from "@/components/guards/route-guard";
import { FeatureUnavailable } from "@/components/admin/feature-unavailable";

export default function WebhooksPage() {
  return (
    <DashboardLayout title="Webhooks" description="Outbound webhook endpoints — not manageable from this console.">
      <RouteGuard
        title="Webhooks"
        description="Restricted to platform and partner administrators."
        requiredRoles={["platform_admin", "partner_admin", "admin"]}
        unauthorizedRedirectTo="/403"
      >
        <FeatureUnavailable
          title="Webhook management"
          purpose="Registering endpoints, choosing which events they receive, and reviewing delivery attempts."
          reason="The backend implements these routes in app/api/v1/endpoints/webhooks_admin.py, but that router is never registered in app/api/v1/routes.py, so /api/v1/admin/webhooks is not served. The webhook router that is mounted (/api/v1/webhooks) only receives HMAC-signed callbacks from the platform; it has no endpoint that lists or edits subscriptions."
        />
      </RouteGuard>
    </DashboardLayout>
  );
}
