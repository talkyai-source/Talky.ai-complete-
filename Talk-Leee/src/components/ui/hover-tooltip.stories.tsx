import type { Meta, StoryObj } from "@storybook/react";
import { HoverTooltip, useHoverTooltip } from "@/components/ui/hover-tooltip";

function Demo() {
    const tooltip = useHoverTooltip();
    return (
        <div className="relative h-56 w-full p-6 bg-gray-50">
            <HoverTooltip state={tooltip.state} />
            <div
                className="h-full w-full rounded-xl border border-dashed border-gray-300 flex items-center justify-center text-sm font-semibold text-gray-700"
                onMouseMove={(e) => tooltip.show(e.clientX, e.clientY, "Move your cursor")}
                onMouseLeave={() => tooltip.hide()}
            >
                Hover to show tooltip
            </div>
        </div>
    );
}

const meta: Meta<typeof HoverTooltip> = {
    title: "UI/HoverTooltip",
    component: HoverTooltip,
};

export default meta;
type Story = StoryObj<typeof HoverTooltip>;

export const Default: Story = {
    render: () => <Demo />,
};

