import type { ReactNode } from "react";
import { unavailablePartnerPage } from "../../operational-page";

/** Applies to direct nested URLs too: no tenant editor or client action mounts. */
export default async function WhiteLabelTenantsBoundary(props: {
    children: ReactNode;
    params: Promise<{ partner: string }>;
}) {
    const { partner } = await props.params;
    return unavailablePartnerPage(partner, "tenants");
}
