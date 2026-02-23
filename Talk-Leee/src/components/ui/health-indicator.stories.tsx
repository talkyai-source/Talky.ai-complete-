import type { Meta, StoryObj } from "@storybook/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HealthIndicator } from "@/components/ui/health-indicator";

const client = new QueryClient({
    defaultOptions: {
        queries: {
            retry: false,
        },
    },
});

const meta: Meta<typeof HealthIndicator> = {
    title: "UI/HealthIndicator",
    component: HealthIndicator,
    decorators: [
        (Story) => (
            <QueryClientProvider client={client}>
                <div className="p-6 bg-gray-50">
                    <Story />
                </div>
            </QueryClientProvider>
        ),
    ],
};

export default meta;
type Story = StoryObj<typeof HealthIndicator>;

export const Default: Story = {};

