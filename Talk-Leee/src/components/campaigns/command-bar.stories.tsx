import type { Meta, StoryObj } from "@storybook/react";
import { CommandBar } from "@/components/campaigns/command-bar";
import type { Campaign } from "@/lib/dashboard-api";

const campaigns: Campaign[] = [
    {
        id: "camp-001",
        name: "Holiday Sales Outreach",
        description: "End of year promotional campaign for existing customers",
        status: "running",
        system_prompt: "You are a friendly sales representative...",
        voice_id: "voice-001",
        max_concurrent_calls: 10,
        total_leads: 500,
        calls_completed: 342,
        calls_failed: 45,
        created_at: "2025-12-15T10:00:00Z",
        started_at: "2025-12-16T09:00:00Z",
    },
    {
        id: "camp-002",
        name: "Customer Satisfaction Survey",
        description: "Post-purchase feedback collection",
        status: "paused",
        system_prompt: "You are conducting a brief customer satisfaction survey...",
        voice_id: "voice-002",
        max_concurrent_calls: 5,
        total_leads: 200,
        calls_completed: 87,
        calls_failed: 12,
        created_at: "2025-12-20T14:00:00Z",
        started_at: "2025-12-21T10:00:00Z",
    },
];

const meta: Meta<typeof CommandBar> = {
    title: "Campaigns/CommandBar",
    component: CommandBar,
    args: {
        campaigns,
        onPause: async () => { },
        onResume: async () => { },
    },
};

export default meta;
type Story = StoryObj<typeof CommandBar>;

export const Default: Story = {};

