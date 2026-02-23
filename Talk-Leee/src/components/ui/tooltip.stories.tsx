import type { Meta, StoryObj } from "@storybook/react";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { Button } from "@/components/ui/button";

const meta: Meta<typeof Tooltip> = {
    title: "UI/Tooltip",
    component: Tooltip,
};

export default meta;
type Story = StoryObj<typeof Tooltip>;

export const Default: Story = {
    render: () => (
        <div className="p-10">
            <TooltipProvider>
                <Tooltip>
                    <TooltipTrigger asChild>
                        <Button>Hover</Button>
                    </TooltipTrigger>
                    <TooltipContent showArrow>Tooltip content</TooltipContent>
                </Tooltip>
            </TooltipProvider>
        </div>
    ),
};

