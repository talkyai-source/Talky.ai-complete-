import { useState } from "react";
import type { Meta, StoryObj } from "@storybook/react";

import { CampaignBriefFields } from "@/components/campaigns/campaign-brief-fields";
import { PromptLayerPreview } from "@/components/campaigns/prompt-layer-preview";
import type { CampaignBriefDraft } from "@/lib/campaign-brief";

function CampaignBriefShowcase() {
    const [brief, setBrief] = useState<CampaignBriefDraft>({
        decision_maker_role: "Head of Operations",
        approved_next_actions: ["schedule_callback", "send_email", "transfer"],
        transfer_destination: "Sales desk",
        opening_objective: "Confirm whether the operations lead owns vendor selection.",
        max_objection_attempts: 3,
    });

    return (
        <main className="min-h-screen bg-background p-4 text-foreground sm:p-8">
            <div className="mx-auto max-w-4xl space-y-6">
                <CampaignBriefFields
                    idPrefix="story-campaign-brief"
                    value={brief}
                    onChange={setBrief}
                    requiredLeadFields={[
                        { field_key: "email", label: "Email address" },
                        { field_key: "best_time_to_call", label: "Best time to call" },
                    ]}
                />
                <div className="rounded-2xl border border-border bg-card p-4 sm:p-5">
                    <PromptLayerPreview
                        headingId="story-prompt-layers"
                        promptChars={4821}
                        layers={[
                            {
                                key: "non_negotiables",
                                label: "Identity and non-negotiables",
                                content: "## NON-NEGOTIABLES\nUse the configured identity and protect caller consent.",
                            },
                            {
                                key: "persona",
                                label: "Persona",
                                content: "## PERSONA\nLead with the reason for the call, then ask one useful question.",
                            },
                            {
                                key: "campaign_brief",
                                label: "Campaign brief",
                                content: "## CAMPAIGN BRIEF\n- Intended decision-maker role: Head of Operations\n- Opening objective: Confirm who owns vendor selection.",
                            },
                            {
                                key: "compliance",
                                label: "Compliance floor and brand accuracy",
                                content: "## COMPLIANCE FLOOR\nNever invent a tool result or claim an action succeeded before confirmation.",
                            },
                        ]}
                    />
                </div>
            </div>
        </main>
    );
}

const meta: Meta<typeof CampaignBriefShowcase> = {
    title: "Campaigns/CampaignBrief",
    component: CampaignBriefShowcase,
    parameters: { layout: "fullscreen" },
};

export default meta;
type Story = StoryObj<typeof CampaignBriefShowcase>;

export const Default: Story = {};
