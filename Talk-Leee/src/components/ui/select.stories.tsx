import type { Meta, StoryObj } from "@storybook/react";
import { useState, type ComponentProps } from "react";
import { Select } from "@/components/ui/select";

function SelectDemo(props: Omit<ComponentProps<typeof Select>, "value" | "onChange">) {
    const [value, setValue] = useState("low");
    return (
        <div className="p-6 bg-gray-50 max-w-sm">
            <Select {...props} value={value} onChange={setValue}>
                <option value="low">Low</option>
                <option value="normal">Normal</option>
                <option value="high">High</option>
            </Select>
        </div>
    );
}

const meta: Meta<typeof Select> = {
    title: "UI/Select",
    component: Select,
    args: {
        ariaLabel: "Demo select",
    },
};

export default meta;
type Story = StoryObj<typeof Select>;

export const Default: Story = {
    render: (args) => <SelectDemo {...args} />,
};

export const Disabled: Story = {
    render: (args) => (
        <div className="p-6 bg-gray-50 max-w-sm">
            <Select {...args} value="normal" onChange={() => {}} disabled>
                <option value="low">Low</option>
                <option value="normal">Normal</option>
                <option value="high">High</option>
            </Select>
        </div>
    ),
};
