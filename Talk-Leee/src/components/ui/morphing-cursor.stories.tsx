import type { Meta, StoryObj } from "@storybook/react";
import { MagneticText } from "@/components/ui/morphing-cursor";

const meta: Meta<typeof MagneticText> = {
    title: "UI/MagneticText",
    component: MagneticText,
    args: {
        text: "CREATIVE",
        hoverText: "EXPLORE",
    },
};

export default meta;
type Story = StoryObj<typeof MagneticText>;

export const Default: Story = {
    render: (args) => (
        <div className="p-10 bg-gray-50 flex items-center justify-center">
            <MagneticText {...args} />
        </div>
    ),
};

