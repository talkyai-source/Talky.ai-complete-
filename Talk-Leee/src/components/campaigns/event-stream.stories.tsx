import type { Meta, StoryObj } from "@storybook/react";
import { EventStream } from "@/components/campaigns/event-stream";
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
];

const meta: Meta<typeof EventStream> = {
    title: "Campaigns/EventStream",
    component: EventStream,
    args: { campaigns },
};

export default meta;
type Story = StoryObj<typeof EventStream>;

export const Default: Story = {};

