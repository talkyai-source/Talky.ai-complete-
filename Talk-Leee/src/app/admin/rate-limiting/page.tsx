"use client";

import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { RouteGuard } from "@/components/guards/route-guard";
import { FeatureUnavailable } from "@/components/admin/feature-unavailable";

export default function RateLimitingPage() {
  return (
    <DashboardLayout title="Rate Limiting" description="API and call rate limits — not configurable from this console.">
      <RouteGuard
        title="Rate Limiting"
        description="Restricted to platform and partner administrators."
        requiredRoles={["platform_admin", "partner_admin", "admin"]}
        unauthorizedRedirectTo="/403"
      >
        <FeatureUnavailable
          title="Rate-limit configuration"
          purpose="Setting per-user, per-tenant and per-IP ceilings on API requests and outbound calls."
          reason="The backend implements these routes in app/api/v1/endpoints/rate_limits.py, but that router is never registered in app/api/v1/routes.py, so /api/v1/admin/rate-limits is not served and returns 404. Rate limiting itself still runs in the request path — it is only the editing surface that is absent, so limits shown here would not have been the limits being enforced."
        />
      </RouteGuard>
    </DashboardLayout>
  );
}
