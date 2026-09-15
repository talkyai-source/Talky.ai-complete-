/**
 * sessionStorage key naming the currently active in-progress draft of the
 * `/inbound-campaigns/new` flow (step 1's basics/agent form plus step 2's
 * number & routing form). Shared between `app/inbound-campaigns/new/page.tsx`
 * (which owns the draft id lifecycle — generating, reflecting it into the
 * URL, and clearing it on success or exit) and `InboundCampaignForm` (which
 * reads it only to find the step-1 draft key to clear when the user exits
 * the flow to the list from step 2).
 */
export const DRAFT_POINTER_KEY = "talky:inbound-campaign-new:active-draft-id";
