"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

const LABELS: Record<string, string> = {
    dashboard: "Dashboard",
    email: "Email",
    settings: "Settings",
    connectors: "Connectors",
    assistant: "Assistant",
    actions: "Actions",
    meetings: "Meetings",
    reminders: "Reminders",
    campaigns: "Campaigns",
    calls: "Call History",
    contacts: "Contacts",
    analytics: "Analytics",
    recordings: "Recordings",
    "ai-options": "AI Options",
    notifications: "Notifications",
    auth: "Auth",
    login: "Login",
    register: "Register",
    callback: "Callback",
};

function labelForSegment(seg: string) {
    const decoded = decodeURIComponent(seg);
    return LABELS[decoded] ?? decoded.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function Breadcrumbs({ className }: { className?: string }) {
    const pathname = usePathname() || "/";
    const segments = pathname.split("/").filter(Boolean);
    if (segments.length <= 1) return null;

    const crumbs = segments.map((seg, idx) => {
        const href = "/" + segments.slice(0, idx + 1).join("/");
        return { seg, href, label: labelForSegment(seg), isLast: idx === segments.length - 1 };
    });

    return (
        <nav aria-label="Breadcrumb" className={cn("flex flex-wrap items-center gap-1 text-xs text-muted-foreground", className)}>
            {crumbs.map((c) => (
                <span key={c.href} className="flex items-center gap-1">
                    {c.isLast ? (
                        <span className="text-foreground/80">{c.label}</span>
                    ) : (
                        <Link href={c.href} className="hover:text-foreground transition-colors">
                            {c.label}
                        </Link>
                    )}
                    {c.isLast ? null : <span className="opacity-50">/</span>}
                </span>
            ))}
        </nav>
    );
}
