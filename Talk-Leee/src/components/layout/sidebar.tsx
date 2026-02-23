"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import {
    LayoutDashboard,
    Phone,
    Users,
    Megaphone,
    Settings,
    LogOut,
    BarChart2,
    Volume2,
    Cpu,
    CalendarDays,
    Mail,
    Bell,
    Bot,
    PanelLeftClose,
    PanelLeftOpen,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { ViewportDrawer } from "@/components/ui/viewport-drawer";
import { HoverTooltip, useHoverTooltip } from "@/components/ui/hover-tooltip";
import { useSidebarActions, useSidebarState } from "@/lib/sidebar-client";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth-context";

const navigation = [
    { name: "Dashboard", href: "/dashboard", icon: LayoutDashboard },
    { name: "Campaigns", href: "/campaigns", icon: Megaphone },
    { name: "Call History", href: "/calls", icon: Phone },
    { name: "Contacts", href: "/contacts", icon: Users },
    { name: "Email", href: "/email", icon: Mail },
    { name: "Analytics", href: "/analytics", icon: BarChart2 },
    { name: "Recordings", href: "/recordings", icon: Volume2 },
    { name: "AI Options", href: "/ai-options", icon: Cpu },
    { name: "Meetings", href: "/meetings", icon: CalendarDays },
    { name: "Reminders", href: "/reminders", icon: Bell },
    { name: "Assistant", href: "/assistant", icon: Bot },
];

const bottomNavigation = [
    { name: "Settings", href: "/settings", icon: Settings },
];

import { useTheme } from "@/components/providers/theme-provider";

export function Sidebar({
    className,
}: {
    className?: string;
}) {
    const pathname = usePathname();
    const router = useRouter();
    const { user, logout } = useAuth();
    const { collapsed, mobileOpen } = useSidebarState();
    const { toggleCollapsed, closeMobile } = useSidebarActions();
    const tooltip = useHoverTooltip();
    const { theme } = useTheme();
    const isDark = theme === "dark";

    const measureRef = useRef<HTMLDivElement | null>(null);
    const [isShortViewport, setIsShortViewport] = useState(false);
    const [isLoggingOut, setIsLoggingOut] = useState(false);

    const desktopWidth = collapsed ? "var(--sidebar-collapsed-width)" : "var(--sidebar-expanded-width)";
    const desktopNavItemClass = collapsed ? "justify-center px-2" : "justify-start px-2";
    const desktopTextClass = collapsed ? "hidden" : "block";

    const measurementLabels = useMemo(() => [...navigation.map((x) => x.name), ...bottomNavigation.map((x) => x.name), "Logout", "Talk-Lee"], []);

    useEffect(() => {
        if (typeof window === "undefined") return;
        const mql = window.matchMedia("(max-height: 760px)");
        const update = () => setIsShortViewport(mql.matches);
        update();
        mql.addEventListener("change", update);
        return () => mql.removeEventListener("change", update);
    }, []);

    useEffect(() => {
        if (typeof document === "undefined") return;
        if (collapsed) return;
        const root = document.documentElement;
        const el = measureRef.current;
        if (!el) return;

        const id = window.requestAnimationFrame(() => {
            const labelEls = Array.from(el.querySelectorAll<HTMLElement>("[data-measure-label]"));
            const maxLabel = labelEls.reduce((acc, node) => Math.max(acc, Math.ceil(node.getBoundingClientRect().width)), 0);
            const outerPx = 16;
            const linkPx = 16;
            const iconPx = 20;
            const gapPx = 10;
            const extraRightPx = 16;
            const widthPx = Math.min(300, Math.max(200, outerPx + linkPx + iconPx + gapPx + maxLabel + extraRightPx));
            root.style.setProperty("--sidebar-expanded-width", `${widthPx}px`);
        });

        return () => window.cancelAnimationFrame(id);
    }, [collapsed]);

    const handleLogout = async () => {
        if (isLoggingOut) return;
        setIsLoggingOut(true);
        try {
            await logout();
        } catch {
            // Best effort: even if backend logout call fails, local auth token is cleared.
        }
        onClose();
        try {
            router.replace("/auth/login?logged_out=1");
            router.refresh();
        } catch {
            window.location.href = "/auth/login?logged_out=1";
        }
    };

    const onClose = () => {
        closeMobile();
        tooltip.hide();
    };

    const maybeShowTooltip = (e: React.MouseEvent<HTMLElement>, content: string) => {
        if (!collapsed) return;
        const isDesktop = window.matchMedia?.("(min-width: 1024px)")?.matches;
        if (!isDesktop) return;
        tooltip.show(e.clientX + 18, e.clientY + 24, content);
    };

    const NavContent = (
        <div className="flex flex-col h-full">
            <div
                className={cn(
                    "relative flex items-center border-b border-sidebar-border/60",
                    isShortViewport ? "h-14" : "h-16",
                    collapsed ? "justify-center px-2" : "justify-between px-3"
                )}
            >
                <div className={cn("flex items-center w-full gap-3", collapsed ? "justify-center" : "justify-between")}>
                    <div className={cn("flex items-center min-w-0", collapsed ? "w-full justify-center" : undefined)}>
                        {collapsed ? (
                            <Button
                                type="button"
                                variant="ghost"
                                size="icon"
                                onClick={toggleCollapsed}
                                className="hidden lg:inline-flex h-10 w-10 group rounded-xl text-sidebar-foreground/70 hover:text-sidebar-foreground hover:bg-sidebar-accent transition-[transform,color,background-color] duration-300 ease-in-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring/40 [&_svg]:!size-5"
                                aria-label="Expand sidebar"
                                aria-expanded={!collapsed}
                            >
                                <PanelLeftOpen className="transition-transform duration-300 ease-in-out group-hover:translate-x-0.5" aria-hidden />
                            </Button>
                        ) : null}

                        <Link
                            href="/dashboard"
                            className={cn(
                                "flex items-center gap-3 min-w-0 overflow-hidden transition-[opacity,transform,max-width] duration-300 ease-in-out",
                                collapsed ? "max-w-0 opacity-0 -translate-x-2 pointer-events-none" : "max-w-[190px] opacity-100 translate-x-0"
                            )}
                            onClick={onClose}
                        >
                            <Image src="/favicon.svg" alt="Talk-Lee" width={28} height={28} className="w-7 h-7" />
                            <div className="min-w-0">
                                <div className="text-base font-black leading-none text-sidebar-foreground tracking-tight">Talk-Lee</div>
                            </div>
                        </Link>
                    </div>

                    <div className={cn("flex items-center gap-2 shrink-0", collapsed ? "hidden" : undefined)}>
                        <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            onClick={onClose}
                            className="lg:hidden inline-flex group rounded-xl text-sidebar-foreground/70 hover:text-sidebar-foreground hover:bg-sidebar-accent transition-[transform,color,background-color] duration-300 ease-in-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring/40"
                            aria-label="Close sidebar"
                        >
                            <PanelLeftClose
                                className="h-5 w-5 transition-transform duration-300 ease-in-out group-hover:-translate-x-0.5"
                                aria-hidden
                            />
                        </Button>

                        <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            onClick={toggleCollapsed}
                            className="hidden lg:inline-flex group rounded-xl text-sidebar-foreground/70 hover:text-sidebar-foreground hover:bg-sidebar-accent transition-[transform,color,background-color] duration-300 ease-in-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring/40 [&_svg]:!size-5"
                            aria-label="Collapse sidebar"
                            aria-expanded={!collapsed}
                        >
                            <PanelLeftClose className="transition-transform duration-300 ease-in-out group-hover:-translate-x-0.5" aria-hidden />
                        </Button>
                    </div>
                </div>
            </div>

            <nav className={cn("flex-1 px-2 space-y-1.5", isShortViewport ? "py-2" : "py-3")}>
                {navigation.map((item) => {
                    const isActive = pathname === item.href || pathname.startsWith(item.href + "/");
                    return (
                        <Link
                            key={item.name}
                            href={item.href}
                            onClick={onClose}
                            onMouseEnter={(e) => maybeShowTooltip(e, item.name)}
                            onMouseMove={(e) => maybeShowTooltip(e, item.name)}
                            onMouseLeave={() => tooltip.hide()}
                            className={cn(
                                "group flex min-w-0 items-center gap-2 rounded-xl text-sm font-semibold transition-colors border",
                                desktopNavItemClass,
                                isShortViewport ? "py-1.5" : "py-2",
                                isActive
                                    ? "bg-sidebar-accent border-sidebar-border/60 text-sidebar-accent-foreground"
                                    : "bg-transparent border-transparent text-sidebar-foreground/70 hover:bg-sidebar-accent hover:border-sidebar-border/60 hover:text-sidebar-foreground"
                            )}
                        >
                            <item.icon className={cn("w-5 h-5", isActive ? "text-sidebar-accent-foreground" : "text-sidebar-foreground/60 group-hover:text-sidebar-foreground")} />
                            <span className={cn("min-w-0 whitespace-nowrap leading-tight", desktopTextClass)}>{item.name}</span>
                            {collapsed ? <span className="sr-only">{item.name}</span> : null}
                        </Link>
                    );
                })}
            </nav>

            <div
                className={cn(
                    "px-2 border-t border-sidebar-border/60 space-y-1.5",
                    isShortViewport ? "mt-1 pt-2 pb-2" : "mt-2 pt-3 pb-3"
                )}
            >
                {bottomNavigation.map((item) => {
                    const isActive = pathname === item.href || pathname.startsWith(item.href + "/");
                    return (
                        <Link
                            key={item.name}
                            href={item.href}
                            onClick={onClose}
                            onMouseEnter={(e) => maybeShowTooltip(e, item.name)}
                            onMouseMove={(e) => maybeShowTooltip(e, item.name)}
                            onMouseLeave={() => tooltip.hide()}
                            className={cn(
                                "group flex min-w-0 items-center gap-2 rounded-xl text-sm font-semibold transition-colors border",
                                desktopNavItemClass,
                                isShortViewport ? "py-1.5" : "py-2",
                                isActive
                                    ? "bg-sidebar-accent border-sidebar-border/60 text-sidebar-accent-foreground"
                                    : "bg-transparent border-transparent text-sidebar-foreground/70 hover:bg-sidebar-accent hover:border-sidebar-border/60 hover:text-sidebar-foreground"
                            )}
                        >
                            <item.icon className={cn("w-5 h-5", isActive ? "text-sidebar-accent-foreground" : "text-sidebar-foreground/60 group-hover:text-sidebar-foreground")} />
                            <span className={cn("min-w-0 whitespace-nowrap leading-tight", desktopTextClass)}>{item.name}</span>
                            {collapsed ? <span className="sr-only">{item.name}</span> : null}
                        </Link>
                    );
                })}

                <button
                    type="button"
                    onClick={handleLogout}
                    disabled={isLoggingOut}
                    className={cn(
                        "w-full group flex min-w-0 items-center gap-2 rounded-xl text-sm font-semibold text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-foreground transition-colors border border-transparent hover:border-sidebar-border/60 disabled:cursor-not-allowed disabled:opacity-60",
                        isShortViewport ? "py-1.5" : "py-2",
                        desktopNavItemClass
                    )}
                    onMouseEnter={(e) => maybeShowTooltip(e, "Logout")}
                    onMouseMove={(e) => maybeShowTooltip(e, "Logout")}
                    onMouseLeave={() => tooltip.hide()}
                >
                    <LogOut className="w-5 h-5 text-sidebar-foreground/60 group-hover:text-sidebar-foreground" />
                    <span className={cn("min-w-0 whitespace-nowrap leading-tight", desktopTextClass)}>
                        {isLoggingOut ? "Logging out..." : "Logout"}
                    </span>
                    {collapsed ? <span className="sr-only">{isLoggingOut ? "Logging out" : "Logout"}</span> : null}
                </button>
            </div>

            <div className={cn("px-2 pb-3", isShortViewport ? "hidden" : "block")}>
                <div className="rounded-2xl border border-sidebar-border/60 bg-sidebar-accent/60 px-3 py-4 backdrop-blur-sm shadow-sm transition-colors duration-300">
                    <div className="flex items-center gap-3">
                        <div className="w-10 h-10 rounded-full bg-sidebar-primary/15 border border-sidebar-border/60 flex items-center justify-center shadow-sm">
                            <span className="text-sm font-black text-sidebar-foreground">{user?.email?.charAt(0).toUpperCase() ?? "U"}</span>
                        </div>
                        <div className="flex-1 min-w-0">
                            <p className="font-bold text-sidebar-foreground truncate">{user?.name ?? user?.email ?? "User"}</p>
                            <p className="text-xs text-sidebar-foreground/60 font-semibold truncate">{user?.business_name ?? "Talk-Lee"}</p>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );

    return (
        <>
            <aside
                className={cn("talklee-sidebar hidden lg:block fixed left-0 top-0 bottom-0 z-20 transition-[width] ease-in-out", className)}
                style={{
                    width: desktopWidth,
                    transitionDuration: "var(--sidebar-transition-ms)",
                    willChange: "width",
                }}
            >
                <div
                    className={cn(
                        isDark ? "dark" : undefined,
                        "w-full h-full bg-sidebar/70 text-sidebar-foreground backdrop-blur-xl border-r border-sidebar-border/60 shadow-sm overflow-hidden"
                    )}
                >
                    {NavContent}
                </div>
            </aside>

            <ViewportDrawer
                open={mobileOpen}
                onOpenChange={(next) => {
                    if (!next) onClose();
                }}
                side="left"
                size={320}
                margin={10}
                hideScrollbar
                ariaLabel="Sidebar"
                className="lg:hidden"
                panelClassName={cn(isDark ? "dark" : undefined, "border-sidebar-border/60 bg-sidebar/85 overflow-hidden")}
            >
                {NavContent}
            </ViewportDrawer>

            <HoverTooltip state={tooltip.state} />

            <div ref={measureRef} className="pointer-events-none absolute -left-[10000px] top-0 opacity-0 whitespace-nowrap">
                {measurementLabels.map((label) => (
                    <span key={label} data-measure-label className="text-sm font-semibold">
                        {label}
                    </span>
                ))}
            </div>
        </>
    );
}
