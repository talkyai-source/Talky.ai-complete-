import type { Meta, StoryObj } from "@storybook/react";
import { AskAICard } from "@/components/ui/ask-ai-card";

const meta: Meta<typeof AskAICard> = {
    title: "UI/AskAICard",
    component: AskAICard,
};

export default meta;
type Story = StoryObj<typeof AskAICard>;

export const Default: Story = {
    render: () => (
        <div className="p-10 bg-gray-950 min-h-[420px] flex items-center justify-center">
            <AskAICard />
        </div>
    ),
};

