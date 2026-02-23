import type { Meta, StoryObj } from "@storybook/react";
import { CampaignPerformanceTable } from "@/components/campaigns/campaign-performance-table";
import type { Campaign } from "@/lib/dashboard-api";

const demo: Campaign[] = [
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
    {
        id: "camp-003",
        name: "New Product Launch",
        description: "Introducing our latest product line to premium customers",
        status: "draft",
        system_prompt: "You are introducing an exciting new product...",
        voice_id: "voice-003",
        max_concurrent_calls: 15,
        total_leads: 750,
        calls_completed: 0,
        calls_failed: 0,
        created_at: "2025-12-28T16:00:00Z",
    },
    {
        id: "camp-004",
        name: "Appointment Reminders",
        description: "Automated appointment reminder calls",
        status: "completed",
        system_prompt: "You are calling to remind about an upcoming appointment...",
        voice_id: "voice-001",
        max_concurrent_calls: 20,
        total_leads: 300,
        calls_completed: 285,
        calls_failed: 15,
        created_at: "2025-12-10T08:00:00Z",
        started_at: "2025-12-11T07:00:00Z",
        completed_at: "2025-12-12T18:00:00Z",
    },
];

const meta: Meta<typeof CampaignPerformanceTable> = {
    title: "Campaigns/CampaignPerformanceTable",
    component: CampaignPerformanceTable,
    args: {
        campaigns: demo,
        loading: false,
        error: "",
        onPause: async () => { },
        onResume: async () => { },
        onDelete: async () => { },
        onDuplicate: async () => { },
        onUpdate: async () => { },
    },
};

export default meta;
type Story = StoryObj<typeof CampaignPerformanceTable>;

export const Default: Story = {};

