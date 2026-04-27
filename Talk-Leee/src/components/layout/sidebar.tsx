"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
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
    Shield,
    CreditCard,
    ScrollText,
    Key,
    Webhook,
    Gauge,
    ShieldCheck,
    ShieldAlert,
    Lock,
    ChevronDown,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { ViewportDrawer } from "@/components/ui/viewport-drawer";
import { HoverTooltip, useHoverTooltip } from "@/components/ui/hover-tooltip";
import { useSidebarActions, useSidebarState } from "@/lib/sidebar-client";
import { Button } from "@/components/ui/button";
import { useWhiteLabelBranding } from "@/components/white-label/white-label-branding-provider";
import { useTheme } from "@/components/providers/theme-provider";
import { useAuth } from "@/hooks/useAuth";
import { getAdminUiCapabilities, roleLabel } from "@/lib/admin-access";

type NavChild = {
    name: string;
    href: string;
    icon: React.ComponentType<{ className?: string }>;
    adminOnly?: boolean;
};

type NavItem = {
    name: string;
    href: string;
    icon: React.ComponentType<{ className?: string }>;
    adminOnly?: boolean;
    children?: NavChild[];
};

const navigation: NavItem[] = [
    { name: "Dashboard", href: "/dashboard", icon: LayoutDashboard },
    { name: "Campaigns", href: "/campaigns", icon: Megaphone },
    { name: "Call History", href: "/calls", icon: Phone },
    { name: "Contacts", href: "/contacts", icon: Users },
    { name: "Email", href: "/email", icon: Mail },
    { name: "Analytics", href: "/analytics", icon: BarChart2 },
    { name: "Recordings", href: "/recordings", icon: Volume2 },
    {
        name: "AI Options", href: "/ai-options", icon: Cpu,
        children: [
            { name: "AI Options", href: "/ai-options", icon: Cpu },
            { name: "Assistant", href: "/assistant", icon: Bot },
        ],
    },
    {
        name: "Meetings", href: "/meetings", icon: CalendarDays,
        children: [
            { name: "Meetings", href: "/meetings", icon: CalendarDays },
            { name: "Reminders", href: "/reminders", icon: Bell },
        ],
    },
    {
        name: "Billing & Logs", href: "/billing", icon: CreditCard,
        children: [
            { name: "Billing", href: "/billing", icon: CreditCard },
            { name: "Audit Logs", href: "/admin/audit-logs", icon: ScrollText, adminOnly: true },
        ],
    },
    {
        name: "Security Center", href: "/admin", icon: Shield, adminOnly: true,
        children: [
            { name: "Audit & Access", href: "/admin", icon: Shield },
            { name: "Voice Security", href: "/admin/voice-security", icon: ShieldCheck },
            { name: "Abuse Detection", href: "/admin/abuse-detection", icon: ShieldAlert },
        ],
    },
    {
        name: "Developer Hub", href: "/admin/api-keys", icon: Key, adminOnly: true,
        children: [
            { name: "API Keys", href: "/admin/api-keys", icon: Key },
            { name: "Webhooks", href: "/admin/webhooks", icon: Webhook },
            { name: "Rate Limiting", href: "/admin/rate-limiting", icon: Gauge },
            { name: "Secrets", href: "/admin/secrets", icon: Lock },
        ],
    },
];

const bottomNavigation = [
    { name: "Settings", href: "/settings", icon: Settings },
];

export function Sidebar({ className }: { className?: string }) {
    const pathname = usePathname();
    const { user, logout } = useAuth();
    const whiteLabel = useWhiteLabelBranding();
    const brandName = whiteLabel?.branding.displayName ?? "Talk-Lee";
    const brandLogoSrc = whiteLabel?.branding.logo.src ?? "/favicon.svg";
    const brandLogoAlt = whiteLabel?.branding.logo.alt ?? "Talk-Lee";
    const brandLogoWidth = whiteLabel?.branding.logo.width ?? 28;
    const brandLogoHeight = whiteLabel?.branding.logo.height ?? 28;
    const brandHomeHref = whiteLabel ? `/white-label/${whiteLabel.branding.partnerId}/preview` : "/dashboard";
    const { collapsed, mobileOpen } = useSidebarState();
    const { toggleCollapsed, closeMobile } = useSidebarActions();
    const tooltip = useHoverTooltip();
    const { theme } = useTheme();
    const isDark = theme === "dark";

    const measureRef = useRef<HTMLDivElement | null>(null);
    const [isShortViewport, setIsShortViewport] = useState(false);
    const [openDropdowns, setOpenDropdowns] = useState<Set<string>>(new Set());

    const toggleDropdown = (name: string) => {
        setOpenDropdowns(prev => {
            const next = new Set(prev);
            if (next.has(name)) next.delete(name);
            else next.add(name);
            return next;
        });
    };

    const desktopWidth = collapsed ? "var(--sidebar-collapsed-width)" : "var(--sidebar-expanded-width)";
    const desktopNavItemClass = collapsed ? "justify-center px-2" : "justify-start px-2";
    const desktopTextClass = collapsed ? "hidden" : "block";
    const capabilities = useMemo(() => getAdminUiCapabilities(user), [user]);
    const isAdmin = capabilities.canViewAuditLogs;
    const visibleNavigation = useMemo(
        () => {
            return navigation
                .filter((item) => !item.adminOnly || isAdmin)
                .map((item) => {
                    if (!item.children) return item;
                    const visibleChildren = item.children.filter((child) => !child.adminOnly || isAdmin);
                    if (visibleChildren.length === 0) return item;
                    if (visibleChildren.length === 1) return { ...item, children: undefined };
                    return { ...item, children: visibleChildren };
                });
        },
        [isAdmin]
    );

    // Automatically expand dropdowns if a child is active
    useEffect(() => {
        setOpenDropdowns((prev) => {
            const next = new Set(prev);
            let changed = false;
            visibleNavigation.forEach((item) => {
                if (item.children) {
                    const hasActiveChild = item.children.some(
                        (child) => pathname === child.href || pathname.startsWith(child.href + "/")
                    );
                    if (hasActiveChild && !next.has(item.name)) {
                        next.add(item.name);
                        changed = true;
                    }
                }
            });
            return changed ? next : prev;
        });
    }, [pathname, visibleNavigation]);

    const profileUser = user ?? {
        id: "guest",
        email: "guest@talk-lee.ai",
        name: "Guest",
        business_name: brandName,
        role: "user",
    };

    const measurementLabels = useMemo(
        () => [...visibleNavigation.map((x) => x.name), ...bottomNavigation.map((x) => x.name), "Logout", brandName],
        [brandName, visibleNavigation]
    );

    useEffect(() => {
        if (typeof window === "undefined") return;
        const mql = window.matchMedia("(max-height: 760px)");
        const update = () => setIsShortViewport(mql.matches);
        update();
        if (typeof mql.addEventListener === "function") {
            mql.addEventListener("change", update);
            return () => mql.removeEventListener("change", update);
        }
        (mql as unknown as { addListener?: (cb: () => void) => void }).addListener?.(update);
        return () => (mql as unknown as { removeListener?: (cb: () => void) => void }).removeListener?.(update);
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

    const handleLogout = () => {
        void logout().finally(() => {
            window.location.href = "/";
        });
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
        <div className="flex flex-col h-full overflow-y-auto">
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
                            href={brandHomeHref}
                            className={cn(
                                "flex items-center gap-3 min-w-0 overflow-hidden transition-[opacity,transform,max-width] duration-300 ease-in-out",
                                collapsed ? "max-w-0 opacity-0 -translate-x-2 pointer-events-none" : "max-w-[190px] opacity-100 translate-x-0"
                            )}
                            onClick={onClose}
                        >
                            {whiteLabel ? (
                                <Image src={brandLogoSrc} alt={brandLogoAlt} width={brandLogoWidth} height={brandLogoHeight} className="w-7 h-7" />
                            ) : (
                                <svg viewBox="327 327 369 369" className="w-7 h-7 text-sidebar-foreground" aria-hidden="true" fill="currentColor">
                                    <path d="m547.23 349.3q2.35 0.84 4.77 1.7c2.95 0.95 5.9 1.9 8.94 2.87 22.37 8.68 39.25 22.32 56.06 39.13q3.62 4.92 7 10 1.69 2.52 3.44 5.12c11.29 18.62 16.5 35.46 19.56 56.88q0.06 9.5 0 19 2.46 0.98 5 2c9.18 6.91 16.17 13.96 21.81 24 4.58 10.47 6.08 19.98 6.5 31.44-0.38 11.7-2.05 22.04-7.31 32.56-6.13 10.3-12.57 18.37-22.13 25.69-7.89 4.95-16 9.35-24.87 12.31-1.49 3.46-1.49 3.46-3 7-4.27 8.45-8.73 15.88-15 23q-0.99 0-2 0 0 0.98 0 2c-10.16 9.19-20.99 15.16-33.81 19.87-9.76 2.89-18 4.39-28.19 4.13-0.66 2.31-1.32 4.62-2 7-1.98 1.65-3.96 3.3-6 5-6.75 0.47-13.19 0.65-19.94 0.56q-2.72 0.02-5.52 0.04c-4.85-0.03-9.7-0.3-14.54-0.6-7-4-7-4-13-11-2-5-2-5-2.44-12.25 0.44-7.75 0.44-7.75 2.5-12.88 2.94-3.87 2.94-3.87 9.94-8.87 6.75-2.25 14.65-1.27 21.75-1.31q2.6-0.05 5.27-0.09c4.66-0.02 9.32 0.18 13.98 0.4 7 4 7 4 10 8q0 0.98 0 2c10.53-0.96 20.22-1.87 30-6 10-6 17.3-12.21 24.06-21.69 1.46-2.63 1.46-2.63 2.94-5.31-0.65-4.88-0.65-4.88-3-8-0.21-3.81-0.28-7.62-0.29-11.43-0.01-2.42-0.03-4.84-0.04-7.33 0-2.63 0-5.26-0.01-7.97-0.01-4.03-0.01-4.03-0.02-8.15q-0.01-8.54-0.01-17.09-0.01-13.1-0.08-26.19-0.01-8.29-0.01-16.57c-0.02-2.63-0.03-5.25-0.04-7.95 0-2.44 0.01-4.87 0.01-7.37 0-2.15 0-4.29 0-6.5 0.49-5.45 0.49-5.45 4.49-12.45q2.46-1.48 5-3 8-0.19 16 0c0-14.03-3.54-26.14-9-39-7.15-14.3-16.6-26.87-29-37-16.61-12.49-31.88-19.49-52-24q-2.92-0.68-5.94-1.38c-16.98-1.75-33.39-1.39-49.74 3.89-17.42 6.12-31.41 14.71-45.45 26.74-11.15 10.92-20.16 23.22-25.85 37.84-4.02 11.34-7.21 21.84-8.02 33.91q0.99-0.49 2-1c6.62-0.31 6.62-0.31 14 0 2.64 1.32 5.28 2.64 8 4q1.48 2.46 3 5c0.29 3.87 0.4 7.76 0.42 11.64 0.02 3.62 0.02 3.62 0.05 7.32 0 2.62 0 5.24 0 7.93 0.01 4.03 0.01 4.03 0.02 8.13q0.02 8.52 0.01 17.04 0.01 13.06 0.09 26.11 0 8.27 0 16.54c0.02 2.61 0.04 5.22 0.05 7.91-0.01 2.43-0.02 4.85-0.02 7.35 0 2.14 0 4.27 0 6.47-0.31 2.75-0.31 2.75-0.62 5.56-1.65 2.64-3.3 5.28-5 8-4 2-4 2-11.56 2.31-9.78-0.36-16.57-2.03-25.44-6-10.84-5.98-19.14-12.72-27-22.31-4.93-7.84-8.78-15.01-11-24-0.66-2.31-1.32-4.62-2-7q-0.23-5.18-0.19-10.38 0.01-2.65 0.02-5.39c0.36-11 2.68-19.24 7.17-29.23 3.31-4.81 3.31-4.81 7-9 1.49-1.73 2.97-3.47 4.5-5.25 4.5-4.75 4.5-4.75 11.5-9.75q0.99 0 2 0c-0.08-3.26-0.16-6.52-0.25-9.88 0.02-16.7 4.12-31.64 10.25-47.12 9.91-20.77 22.78-35.78 39-52 17.04-13.37 36.11-22.55 57-28 21.94-3.48 44.89-4.61 66.23 2.3z"/>
                                </svg>
                            )}
                            <div className="min-w-0">
                                <div className="text-base font-black leading-none text-sidebar-foreground tracking-tight">{brandName}</div>
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
                {visibleNavigation.map((item) => {
                    const isActive = pathname === item.href || pathname.startsWith(item.href + "/");

                    // Dropdown item
                    if (item.children && !collapsed) {
                        const isOpen = openDropdowns.has(item.name);
                        const hasActiveChild = item.children.some(
                            (child) => pathname === child.href || pathname.startsWith(child.href + "/")
                        );
                        return (
                            <div key={item.name}>
                                <button
                                    type="button"
                                    onClick={() => toggleDropdown(item.name)}
                                    onMouseEnter={(e) => maybeShowTooltip(e, item.name)}
                                    onMouseMove={(e) => maybeShowTooltip(e, item.name)}
                                    onMouseLeave={() => tooltip.hide()}
                                    className={cn(
                                        "w-full group flex min-w-0 items-center gap-2 rounded-xl text-sm font-semibold transition-colors border",
                                        desktopNavItemClass,
                                        isShortViewport ? "py-1.5" : "py-2",
                                        hasActiveChild
                                            ? "bg-sidebar-accent border-sidebar-border/60 text-sidebar-accent-foreground"
                                            : "bg-transparent border-transparent text-sidebar-foreground/70 hover:bg-sidebar-accent hover:border-sidebar-border/60 hover:text-sidebar-foreground"
                                    )}
                                >
                                    <item.icon className={cn("w-5 h-5 shrink-0", hasActiveChild ? "text-sidebar-accent-foreground" : "text-sidebar-foreground/60 group-hover:text-sidebar-foreground")} />
                                    <span className={cn("min-w-0 whitespace-nowrap leading-tight flex-1 text-left", desktopTextClass)}>{item.name}</span>
                                    <ChevronDown
                                        className={cn(
                                            "w-4 h-4 shrink-0 transition-[transform,opacity] duration-200 opacity-0 group-hover/sidebar:opacity-100",
                                            desktopTextClass,
                                            isOpen ? "rotate-0" : "-rotate-90"
                                        )}
                                    />
                                </button>
                                <div
                                    className={cn(
                                        "grid transition-[grid-template-rows] duration-200 ease-in-out",
                                        isOpen ? "grid-rows-[1fr]" : "grid-rows-[0fr]"
                                    )}
                                >
                                    <div className="overflow-hidden">
                                        <div className="pl-4 mt-1 space-y-0.5">
                                            {item.children.map((child) => {
                                                const isChildActive = pathname === child.href || pathname.startsWith(child.href + "/");
                                                return (
                                                    <Link
                                                        key={child.name}
                                                        href={child.href}
                                                        onClick={onClose}
                                                        className={cn(
                                                            "group flex min-w-0 items-center gap-2 rounded-lg text-[13px] font-medium transition-colors border px-2",
                                                            isShortViewport ? "py-1" : "py-1.5",
                                                            isChildActive
                                                                ? "bg-sidebar-accent/70 border-sidebar-border/40 text-sidebar-accent-foreground"
                                                                : "bg-transparent border-transparent text-sidebar-foreground/60 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground"
                                                        )}
                                                    >
                                                        <child.icon className={cn("w-4 h-4 shrink-0", isChildActive ? "text-sidebar-accent-foreground" : "text-sidebar-foreground/50 group-hover:text-sidebar-foreground")} />
                                                        <span className="min-w-0 whitespace-nowrap leading-tight">{child.name}</span>
                                                    </Link>
                                                );
                                            })}
                                        </div>
                                    </div>
                                </div>
                            </div>
                        );
                    }

                    // Regular link (or collapsed dropdown acting as direct link)
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
                    className={cn(
                        "w-full group flex min-w-0 items-center gap-2 rounded-xl text-sm font-semibold text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-foreground transition-colors border border-transparent hover:border-sidebar-border/60",
                        isShortViewport ? "py-1.5" : "py-2",
                        desktopNavItemClass
                    )}
                    onMouseEnter={(e) => maybeShowTooltip(e, "Logout")}
                    onMouseMove={(e) => maybeShowTooltip(e, "Logout")}
                    onMouseLeave={() => tooltip.hide()}
                >
                    <LogOut className="w-5 h-5 text-sidebar-foreground/60 group-hover:text-sidebar-foreground" />
                    <span className={cn("min-w-0 whitespace-nowrap leading-tight", desktopTextClass)}>Logout</span>
                    {collapsed ? <span className="sr-only">Logout</span> : null}
                </button>
            </div>

            <div className={cn("px-2 pb-3", isShortViewport ? "hidden" : "block")}>
                <div className="rounded-2xl border border-sidebar-border/60 bg-sidebar-accent/60 px-3 py-4 backdrop-blur-sm shadow-sm transition-colors duration-300">
                    <div className="flex items-center gap-3">
                        <div className="w-10 h-10 rounded-full bg-sidebar-primary/15 border border-sidebar-border/60 flex items-center justify-center shadow-sm">
                            <span className="text-sm font-black text-sidebar-foreground">{profileUser.email?.charAt(0).toUpperCase()}</span>
                        </div>
                        <div className="flex-1 min-w-0">
                            <p className="font-bold text-sidebar-foreground truncate">{profileUser.name ?? profileUser.email}</p>
                            <p className="text-xs text-sidebar-foreground/60 font-semibold truncate">{roleLabel(profileUser.role)}</p>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );

    return (
        <>
            <aside
                className={cn("talklee-sidebar group/sidebar hidden lg:block fixed left-0 top-0 bottom-0 z-20 transition-[width] ease-in-out", className)}
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
                panelClassName={cn(isDark ? "dark" : undefined, "group/sidebar border-sidebar-border/60 bg-sidebar/85 overflow-hidden")}
            >
                {NavContent}
            </ViewportDrawer>

            <HoverTooltip tooltip={tooltip} />

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
