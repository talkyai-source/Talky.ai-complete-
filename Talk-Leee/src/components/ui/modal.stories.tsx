import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";

function Demo() {
    const [open, setOpen] = useState(false);
    return (
        <div className="p-6 bg-gray-950 min-h-[320px]">
            <Button variant="secondary" onClick={() => setOpen(true)}>
                Open modal
            </Button>
            <Modal
                open={open}
                onOpenChange={setOpen}
                title="Modal title"
                description="Description and content in a portal."
                footer={
                    <div className="flex justify-end gap-2">
                        <Button variant="ghost" onClick={() => setOpen(false)}>
                            Cancel
                        </Button>
                        <Button onClick={() => setOpen(false)}>Confirm</Button>
                    </div>
                }
            >
                <div className="space-y-2 text-sm text-gray-200">
                    <div>Body content goes here.</div>
                    <div>Press Escape or click outside to close.</div>
                </div>
            </Modal>
        </div>
    );
}

const meta: Meta<typeof Modal> = {
    title: "UI/Modal",
    component: Modal,
    parameters: {
        docs: {
            description: {
                component:
                    "UX & accessibility behavior:\n\n- Focus: moves focus into the modal on open and restores focus on close.\n- Keyboard: Escape closes; Tab is trapped within the modal.\n- Semantics: role=dialog, aria-modal=true; title/description are wired to aria-labelledby/aria-describedby.\n",
            },
        },
    },
};

export default meta;
type Story = StoryObj<typeof Modal>;

export const Default: Story = {
    render: () => <Demo />,
};
