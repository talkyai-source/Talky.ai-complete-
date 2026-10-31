"use client";

import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { RouteGuard } from "@/components/guards/route-guard";
import { FeatureUnavailable } from "@/components/admin/feature-unavailable";

export default function AbuseDetectionPage() {
  return (
    <DashboardLayout title="Abuse Detection" description="Suspicious-activity monitoring — not available from this console.">
      <RouteGuard
        title="Abuse Detection"
        description="Restricted to platform and partner administrators."
        requiredRoles={["platform_admin", "partner_admin", "admin"]}
        unauthorizedRedirectTo="/403"
      >
        <FeatureUnavailable
          title="Abuse detection"
          purpose="Reviewing detected abuse events and blocking the IPs, numbers and accounts behind them."
          reason="The two halves of this page are unavailable for different reasons. Abuse events are served at /api/v1/admin/abuse/events, but only to the platform_admin role, which is narrower than the set this page admits — a partner admin would have been shown an empty table rather than a refusal. Blocking is implemented in app/api/v1/endpoints/blocked_entities.py, but that router is never registered in app/api/v1/routes.py, so every block and unblock made here went nowhere."
        />
      </RouteGuard>
    </DashboardLayout>
  );
}
