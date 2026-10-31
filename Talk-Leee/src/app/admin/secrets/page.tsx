"use client";

import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { RouteGuard } from "@/components/guards/route-guard";
import { FeatureUnavailable } from "@/components/admin/feature-unavailable";

export default function SecretsPage() {
  return (
    <DashboardLayout title="Secrets Management" description="Stored credentials — not manageable from this console.">
      <RouteGuard
        title="Secrets Management"
        description="Restricted to platform administrators only."
        requiredRoles={["platform_admin", "admin"]}
        unauthorizedRedirectTo="/403"
      >
        <FeatureUnavailable
          title="Secrets management"
          purpose="Listing stored credentials, rotating them, and marking one compromised."
          reason="Unlike the other admin pages here, the API for this does exist and is served at /api/v1/admin/secrets — this page was simply never connected to it, and read from a fixtures module instead. It is not wired up now because the add and rotate controls it offered are destructive operations on live credentials, and shipping them against a surface nobody has exercised is a worse failure than showing nothing."
        />
      </RouteGuard>
    </DashboardLayout>
  );
}
