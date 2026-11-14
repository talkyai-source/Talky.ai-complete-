"use client";

import { AlertCircle } from "lucide-react";

import { inboundSetupNeeds } from "@/lib/inbound-setup-needs";
import { isEligibleInboundTrunk } from "@/lib/inbound-validation";
import { useInboundPhoneNumbers } from "@/lib/queries/inbound-queries";
import { useSipTrunks } from "@/lib/telephony-api";

/**
 * Shown above step 1 of "New inbound campaign".
 *
 * Step 2 needs a verified phone number and a connected inbound trunk. Without
 * them its button stays switched off, and until 14 Nov 2026 the user only
 * found that out after finishing the whole of step 1. This says it first, and
 * says where the work is kept if they go ahead anyway.
 *
 * It says nothing until both answers are in: a guess would be worse than
 * silence, and step 2 reports a failed read itself.
 */
export function InboundSetupNeedsNotice({ canAssignNumber }: { canAssignNumber: boolean }) {
    const numbers = useInboundPhoneNumbers(canAssignNumber);
    const trunks = useSipTrunks();
    if (!numbers.isSuccess || !trunks.isSuccess) return null;
    const needs = inboundSetupNeeds({
        needsCampaign: false,
        numbers: numbers.data.length,
        trunks: trunks.data.filter(isEligibleInboundTrunk).length,
    });
    if (needs.length === 0) return null;
    return (
        <div role="status" className="mb-6 flex items-start gap-3 rounded-xl border border-amber-500/30 bg-amber-500/5 p-4 text-sm text-foreground">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" aria-hidden />
            <div className="min-w-0">
                <p className="font-medium">This campaign will not be able to take calls yet.</p>
                <p className="mt-1 text-muted-foreground">
                    Still needed for step 2: {needs.join("; ")}. You can set up the campaign and its agent now and add the
                    number later: until then it is kept under &ldquo;Setup not finished&rdquo; on the Inbound Campaigns page.
                </p>
            </div>
        </div>
    );
}
