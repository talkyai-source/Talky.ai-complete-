"use client";

import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { RouteGuard } from "@/components/guards/route-guard";
import { FeatureUnavailable } from "@/components/admin/feature-unavailable";

export default function VoiceSecurityPage() {
  return (
    <DashboardLayout title="Voice Security" description="Call guards and call limits — not configurable from this console.">
      <RouteGuard
        title="Voice Security"
        description="Restricted to platform and partner administrators."
        requiredRoles={["platform_admin", "partner_admin", "admin"]}
        unauthorizedRedirectTo="/403"
      >
        <FeatureUnavailable
          title="Voice security"
          purpose="Reviewing call-guard decisions and setting per-tenant and per-partner concurrency and spend limits."
          reason="Call guards are implemented in app/api/v1/endpoints/call_guards.py, but that router is never registered in app/api/v1/routes.py, so /api/v1/admin/call-guards returns 404. Call limits are served (/api/v1/admin/tenants/{id}/call-limits and /api/v1/admin/partners/{id}/limits), but only one tenant or partner at a time — there is no listing endpoint for this page to read, and both require the platform_admin role rather than the broader set this page admits."
        />
      </RouteGuard>
    </DashboardLayout>
  );
}
