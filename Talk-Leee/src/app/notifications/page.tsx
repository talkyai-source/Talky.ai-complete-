"use client";

import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { NotificationCenter } from "@/components/notifications/notification-center";

export default function NotificationsPage() {
    return (
        <DashboardLayout title="Notifications" description="Review, filter, and manage notification history.">
            <div className="mx-auto w-full max-w-5xl">
                <div className="rounded-2xl border border-border bg-background/70 backdrop-blur-sm">
                    <NotificationCenter maxHeightClassName="max-h-[calc(100vh-320px)]" />
                </div>
            </div>
        </DashboardLayout>
    );
}

