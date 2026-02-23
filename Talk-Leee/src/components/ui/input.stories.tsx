import type { Meta, StoryObj } from "@storybook/react";
import { Input } from "@/components/ui/input";

const meta: Meta<typeof Input> = {
    title: "UI/Input",
    component: Input,
    args: {
        placeholder: "Type here…",
    },
};

export default meta;
type Story = StoryObj<typeof Input>;

export const Default: Story = {
    render: (args) => (
        <div className="p-6 bg-gray-50 max-w-md">
            <Input {...args} />
        </div>
    ),
};

export const Disabled: Story = {
    args: { disabled: true, value: "Disabled" },
    render: (args) => (
        <div className="p-6 bg-gray-50 max-w-md">
            <Input {...args} />
        </div>
    ),
};

