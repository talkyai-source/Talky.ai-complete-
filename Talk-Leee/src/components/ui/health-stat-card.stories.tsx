import type { Meta, StoryObj } from "@storybook/react";
import { HealthStatCard } from "@/components/ui/health-stat-card";

const meta: Meta<typeof HealthStatCard> = {
    title: "UI/HealthStatCard",
    component: HealthStatCard,
};

export default meta;
type Story = StoryObj<typeof HealthStatCard>;

export const Default: Story = {
    args: {
        title: "System health",
        stats: [
            { title: "Healthy", value: 92, unit: "%", changePercent: 1.2, changeDirection: "up" },
            { title: "Degraded", value: 6, unit: "%", changePercent: 0.3, changeDirection: "down" },
            { title: "Down", value: 2, unit: "%", changePercent: 0.1, changeDirection: "down" },
        ],
        graphData: [
            { label: "Healthy", value: 92, color: "#22c55e", description: "All systems nominal" },
            { label: "Degraded", value: 6, color: "#f59e0b", description: "Elevated latency" },
            { label: "Down", value: 2, color: "#ef4444", description: "Partial outage" },
        ],
    },
    render: (args) => (
        <div className="p-6 bg-gray-50">
            <HealthStatCard {...args} />
        </div>
    ),
};

