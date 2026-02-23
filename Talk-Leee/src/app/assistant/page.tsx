"use client";

import Link from "next/link";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { useTheme } from "@/components/providers/theme-provider";
import { cn } from "@/lib/utils";

export default function AssistantPage() {
    const { theme } = useTheme();
    const isDark = theme === "dark";

    return (
        <DashboardLayout title="Assistant" description="Manage assistant capabilities and actions.">
            <div className="mx-auto w-full max-w-5xl space-y-6">
                <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                    <div
                        className={cn(
                            isDark
                                ? "content-card"
                                : "rounded-2xl border border-border bg-background/70 backdrop-blur-sm p-6 transition-[transform,background-color,box-shadow,border-color] duration-150 ease-out hover:scale-[1.02] hover:bg-background/85 hover:shadow-md"
                        )}
                    >
                        <div className="text-sm font-semibold text-foreground">Actions</div>
                        <div className="mt-1 text-sm text-muted-foreground">Browse and configure assistant actions.</div>
                        <div className="mt-4">
                            <Link
                                href="/assistant/actions"
                                className="inline-flex items-center justify-center rounded-xl border border-teal-500/60 bg-teal-600 px-3 py-2 text-sm font-semibold text-white transition-[transform,background-color,border-color] duration-150 ease-out hover:scale-[1.02] hover:bg-teal-700 hover:text-white active:scale-[0.99]"
                            >
                                Open Actions
                            </Link>
                        </div>
                    </div>
                    <div
                        className={cn(
                            isDark
                                ? "content-card"
                                : "rounded-2xl border border-border bg-background/70 backdrop-blur-sm p-6 transition-[transform,background-color,box-shadow,border-color] duration-150 ease-out hover:scale-[1.02] hover:bg-background/85 hover:shadow-md"
                        )}
                    >
                        <div className="text-sm font-semibold text-foreground">Meetings</div>
                        <div className="mt-1 text-sm text-muted-foreground">Review assistant-linked meetings.</div>
                        <div className="mt-4">
                            <Link
                                href="/assistant/meetings"
                                className="inline-flex items-center justify-center rounded-xl border border-teal-500/60 bg-teal-600 px-3 py-2 text-sm font-semibold text-white transition-[transform,background-color,border-color] duration-150 ease-out hover:scale-[1.02] hover:bg-teal-700 hover:text-white active:scale-[0.99]"
                            >
                                Open Meetings
                            </Link>
                        </div>
                    </div>
                    <div
                        className={cn(
                            isDark
                                ? "content-card"
                                : "rounded-2xl border border-border bg-background/70 backdrop-blur-sm p-6 transition-[transform,background-color,box-shadow,border-color] duration-150 ease-out hover:scale-[1.02] hover:bg-background/85 hover:shadow-md"
                        )}
                    >
                        <div className="text-sm font-semibold text-foreground">Reminders</div>
                        <div className="mt-1 text-sm text-muted-foreground">Track assistant-generated reminders.</div>
                        <div className="mt-4">
                            <Link
                                href="/assistant/reminders"
                                className="inline-flex items-center justify-center rounded-xl border border-teal-500/60 bg-teal-600 px-3 py-2 text-sm font-semibold text-white transition-[transform,background-color,border-color] duration-150 ease-out hover:scale-[1.02] hover:bg-teal-700 hover:text-white active:scale-[0.99]"
                            >
                                Open Reminders
                            </Link>
                        </div>
                    </div>
                </div>
            </div>
        </DashboardLayout>
    );
}
