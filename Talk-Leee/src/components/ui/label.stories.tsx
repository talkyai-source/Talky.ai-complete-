import type { Meta, StoryObj } from "@storybook/react";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";

const meta: Meta<typeof Label> = {
    title: "UI/Label",
    component: Label,
};

export default meta;
type Story = StoryObj<typeof Label>;

export const Default: Story = {
    render: () => (
        <div className="p-6 bg-gray-50 max-w-md space-y-2">
            <Label htmlFor="email">Email</Label>
            <Input id="email" placeholder="name@company.com" />
        </div>
    ),
};

