"use client";

import Link from "next/link";
import { useMemo } from "react";
import { ArrowRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useCampaigns } from "@/lib/api-hooks";
import { isEligibleInboundBaseCampaign } from "@/lib/inbound-validation";
import { useInboundCampaigns } from "@/lib/queries/inbound-queries";

/**
 * Inbound campaigns whose step 1 (the campaign and its agent) was saved but
 * whose step 2 (the number and routing) never was.
 *
 * Such a campaign is in neither list: the outbound list leaves inbound
 * campaigns out, and the inbound list shows number setups only. Until
 * 14 Nov 2026 it could be reached only from the browser tab that made it, so
 * closing that tab lost the work. Each one is listed here with a link straight
 * to its step 2.
 *
 * Archived number setups are read too: a campaign whose setup was archived is
 * finished, not missing. Nothing is shown while either read is pending or has
 * failed; the page's own list reports its failures.
 */
export function UnfinishedInboundSetups() {
    const campaigns = useCampaigns();
    const setups = useInboundCampaigns(true, true);
    const unfinished = useMemo(() => {
        if (!campaigns.isSuccess || !setups.isSuccess) return [];
        const attached = new Set((setups.data ?? []).map((setup) => setup.campaign_id));
        return (campaigns.data ?? []).filter((campaign) => campaign.direction === "inbound" && !attached.has(campaign.id) && isEligibleInboundBaseCampaign(campaign));
    }, [campaigns.isSuccess, campaigns.data, setups.isSuccess, setups.data]);
    if (unfinished.length === 0) return null;
    return (
        <section className="content-card" aria-labelledby="unfinished-inbound-heading">
            <h2 id="unfinished-inbound-heading" className="text-sm font-semibold text-foreground">Setup not finished</h2>
            <p className="mt-1 text-sm text-muted-foreground">
                {unfinished.length === 1 ? "This campaign has its agent set up but no phone number yet, so it cannot take calls and is not in the list below." : "These campaigns have their agent set up but no phone number yet, so they cannot take calls and are not in the list below."}
            </p>
            <ul className="mt-3 divide-y divide-border">
                {unfinished.map((campaign) => (
                    <li key={campaign.id} className="flex items-center justify-between gap-3 py-2">
                        <span className="min-w-0 truncate text-sm text-foreground">{campaign.name}</span>
                        <Button asChild size="sm" variant="outline" className="shrink-0">
                            <Link href={`/inbound-campaigns/new?campaign_id=${encodeURIComponent(campaign.id)}`} aria-label={`Continue setup of ${campaign.name}`}>
                                Continue setup <ArrowRight className="h-4 w-4" aria-hidden />
                            </Link>
                        </Button>
                    </li>
                ))}
            </ul>
        </section>
    );
}
