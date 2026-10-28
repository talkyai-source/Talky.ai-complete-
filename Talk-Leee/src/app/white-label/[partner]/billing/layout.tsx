import type { ReactNode } from "react";
import { unavailablePartnerPage } from "../../operational-page";

/** Partner billing stays unavailable for this entire subtree. */
export default async function WhiteLabelBillingBoundary(props: {
    children: ReactNode;
    params: Promise<{ partner: string }>;
}) {
    const { partner } = await props.params;
    return unavailablePartnerPage(partner, "billing");
}
