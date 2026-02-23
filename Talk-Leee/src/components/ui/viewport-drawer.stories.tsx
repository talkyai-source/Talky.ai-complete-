import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { ViewportDrawer } from "@/components/ui/viewport-drawer";
import { Button } from "@/components/ui/button";

function Demo() {
    const [open, setOpen] = useState(false);
    return (
        <div className="p-6 bg-gray-50 min-h-[320px]">
            <Button onClick={() => setOpen(true)}>Open drawer</Button>
            <ViewportDrawer open={open} onOpenChange={setOpen} ariaLabel="Demo drawer" side="right">
                <div className="p-4 space-y-3">
                    <div className="text-sm font-semibold text-gray-900">Drawer content</div>
                    <div className="text-sm text-gray-700">
                        This drawer adapts its size to the viewport with a margin.
                    </div>
                    <Button variant="outline" onClick={() => setOpen(false)}>
                        Close
                    </Button>
                </div>
            </ViewportDrawer>
        </div>
    );
}

const meta: Meta<typeof ViewportDrawer> = {
    title: "UI/ViewportDrawer",
    component: ViewportDrawer,
    parameters: {
        docs: {
            description: {
                component:
                    "UX & accessibility behavior:\n\n- Focus: moves focus into the drawer on open and restores focus on close.\n- Keyboard: Escape closes the drawer; Tab is trapped within the drawer.\n- Semantics: rendered as a modal dialog (role=dialog, aria-modal=true) with an accessible label.\n",
            },
        },
    },
};

export default meta;
type Story = StoryObj<typeof ViewportDrawer>;

export const Default: Story = {
    render: () => <Demo />,
};
