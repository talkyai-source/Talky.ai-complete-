import type { Meta, StoryObj } from "@storybook/react";
import { Button } from "@/components/ui/button";

const meta: Meta<typeof Button> = {
    title: "UI/Button",
    component: Button,
    args: {
        children: "Button",
        variant: "default",
        size: "default",
        disabled: false,
    },
};

export default meta;
type Story = StoryObj<typeof Button>;

export const Default: Story = {};

export const Variants: Story = {
    render: () => (
        <div className="grid gap-4 p-6 bg-background text-foreground">
            <div className="space-y-3">
                <div className="text-sm font-semibold text-muted-foreground">Light</div>
                <div className="flex flex-wrap items-center gap-3">
                    <Button variant="default">Default</Button>
                    <Button variant="secondary">Secondary</Button>
                    <Button variant="outline">Outline</Button>
                    <Button variant="destructive">Destructive</Button>
                    <Button variant="ghost">Ghost</Button>
                    <Button variant="link">Link</Button>
                    <Button disabled>Disabled</Button>
                </div>
            </div>

            <div className="dark rounded-xl border border-border bg-background p-4">
                <div className="text-sm font-semibold text-muted-foreground">Dark</div>
                <div className="mt-3 flex flex-wrap items-center gap-3">
                    <Button variant="default">Default</Button>
                    <Button variant="secondary">Secondary</Button>
                    <Button variant="outline">Outline</Button>
                    <Button variant="destructive">Destructive</Button>
                    <Button variant="ghost">Ghost</Button>
                    <Button variant="link">Link</Button>
                    <Button disabled>Disabled</Button>
                </div>
            </div>
        </div>
    ),
};

export const Sizes: Story = {
    render: () => (
        <div className="flex flex-wrap items-center gap-3 bg-background p-6 text-foreground">
            <Button size="sm">Small</Button>
            <Button size="default">Default</Button>
            <Button size="lg">Large</Button>
            <Button size="icon" aria-label="Icon button">
                +
            </Button>
        </div>
    ),
};
