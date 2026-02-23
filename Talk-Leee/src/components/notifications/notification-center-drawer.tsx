"use client";

import { ViewportDrawer } from "@/components/ui/viewport-drawer";
import { NotificationCenter } from "@/components/notifications/notification-center";

export function NotificationCenterDrawer({
    open,
    onOpenChange,
}: {
    open: boolean;
    onOpenChange: (next: boolean) => void;
}) {
    return (
        <ViewportDrawer
            open={open}
            onOpenChange={onOpenChange}
            side="right"
            size={460}
            margin={10}
            ariaLabel="Notification center"
            panelClassName="bg-background/90 border border-border"
        >
            <NotificationCenter />
        </ViewportDrawer>
    );
}
