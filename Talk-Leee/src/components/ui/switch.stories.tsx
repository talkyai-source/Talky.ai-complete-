import type { Meta, StoryObj } from "@storybook/react";
import { useState, type ComponentProps } from "react";
import { Switch } from "@/components/ui/switch";

function SwitchDemo({ initialChecked, ...props }: Omit<ComponentProps<typeof Switch>, "checked" | "onCheckedChange"> & { initialChecked: boolean }) {
    const [checked, setChecked] = useState(initialChecked);
    return (
        <div className="p-6 bg-gray-50">
            <Switch {...props} checked={checked} onCheckedChange={setChecked} />
        </div>
    );
}

const meta: Meta<typeof Switch> = {
    title: "UI/Switch",
    component: Switch,
    args: {
        ariaLabel: "Demo switch",
    },
};

export default meta;
type Story = StoryObj<typeof Switch>;

export const Unchecked: Story = {
    render: (args) => <SwitchDemo {...args} initialChecked={false} />,
};

export const Checked: Story = {
    render: (args) => <SwitchDemo {...args} initialChecked={true} />,
};

export const Disabled: Story = {
    render: (args) => (
        <div className="p-6 bg-gray-50 space-y-3">
            <Switch {...args} checked={false} onCheckedChange={() => {}} disabled />
            <Switch {...args} checked onCheckedChange={() => {}} disabled />
        </div>
    ),
};
