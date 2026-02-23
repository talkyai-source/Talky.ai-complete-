"use client";

import { useEffect, useMemo, useState } from "react";
import { Sidebar } from "./sidebar";
import { usePathname, useRouter } from "next/navigation";
import { NotificationBell } from "@/components/notifications/notification-bell";
import { useSidebarActions, useSidebarState } from "@/lib/sidebar-client";
import { Breadcrumbs } from "@/components/layout/breadcrumbs";
import { HealthIndicator } from "@/components/ui/health-indicator";
import { useAuth } from "@/lib/auth-context";
import { Menu } from "lucide-react";
import { Button } from "@/components/ui/button";

interface DashboardLayoutProps {
    children: React.ReactNode;
    title?: string;
    description?: string;
    requireAuth?: boolean;
}

export function DashboardLayout({ children, title, description, requireAuth = true }: DashboardLayoutProps) {
    const pathname = usePathname();
    const router = useRouter();
    const { user, loading: authLoading, refreshUser } = useAuth();
    const { collapsed, mobileOpen } = useSidebarState();
    const { setMobileOpen } = useSidebarActions();
    const [isDesktop, setIsDesktop] = useState(false);
    const [attemptedRefresh, setAttemptedRefresh] = useState(false);

    useEffect(() => {
        if (!requireAuth) return;
        if (authLoading) return;
        if (user) return;
        if (!attemptedRefresh) {
            setAttemptedRefresh(true);
            void refreshUser();
            return;
        }
        const next = pathname ?? "/dashboard";
        try {
            router.replace(`/auth/login?next=${encodeURIComponent(next)}`);
        } catch {
            window.location.href = `/auth/login?next=${encodeURIComponent(next)}`;
        }
    }, [attemptedRefresh, authLoading, pathname, refreshUser, requireAuth, router, user]);

    useEffect(() => {
        if (typeof window === "undefined") return;
        const mql = window.matchMedia("(min-width: 1024px)");
        const update = () => setIsDesktop(mql.matches);
        update();
        mql.addEventListener("change", update);
        return () => mql.removeEventListener("change", update);
    }, []);

    const desktopPaddingLeft = useMemo(() => {
        if (!isDesktop) return undefined;
        return collapsed ? "var(--sidebar-collapsed-width)" : "var(--sidebar-expanded-width)";
    }, [collapsed, isDesktop]);

    if (requireAuth) {
        if (authLoading) {
            return (
                <div className="flex min-h-screen items-center justify-center bg-background text-foreground" role="status" aria-live="polite" aria-busy="true">
                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-foreground/60" aria-hidden />
                    <span className="sr-only">Loading…</span>
                </div>
            );
        }
        if (!user) {
            return (
                <div className="flex min-h-screen items-center justify-center bg-background text-foreground" role="status" aria-live="polite" aria-busy="true">
                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-foreground/60" aria-hidden />
                    <span className="sr-only">{attemptedRefresh ? "Redirecting to sign in…" : "Loading…"}</span>
                </div>
            );
        }
    }

    return (
        <div className="relative min-h-screen w-full bg-background text-foreground shadow-inner transition-colors duration-300">
            {/* Animated Background Shapes */}
            <div className="shape-1"></div>
            <div className="shape-2"></div>

            {!isDesktop && !mobileOpen ? (
                <div
                    role="button"
                    tabIndex={0}
                    aria-label="Open sidebar"
                    title="Open sidebar"
                    className="fixed left-0 top-0 z-[60] h-full w-3 bg-transparent lg:hidden"
                    onClick={() => setMobileOpen(true)}
                    onKeyDown={(e) => {
                        if (e.key !== "Enter" && e.key !== " ") return;
                        e.preventDefault();
                        setMobileOpen(true);
                    }}
                />
            ) : null}

            <Sidebar />

            <div
                className="flex flex-col overflow-hidden z-10 transition-[padding-left] ease-in-out"
                style={{
                    paddingLeft: desktopPaddingLeft,
                    transitionDuration: "var(--sidebar-transition-ms)",
                    willChange: "padding-left",
                }}
            >
                {/* Header */}
                <header className="bg-background/80 backdrop-blur-sm border-b border-border/60 px-4 md:px-8 py-4 md:py-6 transition-colors duration-300">
                    <div className="grid grid-cols-1 items-start gap-3 md:grid-cols-[minmax(0,1fr)_auto] md:items-center">
                        <div className="flex items-start gap-3">
                            {!isDesktop && (
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    className="lg:hidden shrink-0"
                                    onClick={() => setMobileOpen(true)}
                                    aria-label="Open sidebar"
                                >
                                    <Menu className="h-5 w-5" />
                                </Button>
                            )}
                            <div className="min-w-0">
                                <Breadcrumbs className={title || description ? "mb-1.5" : undefined} />
                                {title ? (
                                    <h1 className="text-xl md:text-2xl font-semibold text-foreground leading-tight break-words">{title}</h1>
                                ) : null}
                                {description ? (
                                    <p className={title ? "mt-1 text-sm text-muted-foreground leading-snug break-words" : "text-sm text-muted-foreground leading-snug break-words"}>{description}</p>
                                ) : null}
                            </div>
                        </div>
                        <div className="flex items-center gap-2 justify-self-start md:justify-self-end">
                            <HealthIndicator />
                            <NotificationBell />
                        </div>
                    </div>
                </header>

                {/* Main Content */}
                <main className="flex-1 overflow-y-auto overflow-x-hidden scroll-smooth p-4 md:p-8 transition-colors duration-300">
                    {children}
                </main>
            </div>
        </div>
    );
}
