import type { Meta, StoryObj } from "@storybook/react";
import { Hero } from "@/components/ui/helix-hero";

const meta: Meta<typeof Hero> = {
    title: "UI/Hero",
    component: Hero,
    args: {
        title: "Talk-Lee",
        description: "Intelligent voice communication platform powered by advanced AI agents",
        stats: [
            { label: "Minutes remaining", value: "1,500" },
            { label: "Active campaigns", value: "4" },
            { label: "Success rate", value: "92%" },
        ],
    },
    parameters: {
        layout: "fullscreen",
    },
};

export default meta;
type Story = StoryObj<typeof Hero>;

export const Default: Story = {
    render: (args) => (
        <div className="min-h-[640px] bg-white">
            <Hero {...args} />
        </div>
    ),
};

