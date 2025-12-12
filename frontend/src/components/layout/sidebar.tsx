"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
    LayoutDashboard,
    Phone,
    Users,
    Megaphone,
    Settings,
    LogOut,
    BarChart2,
    Volume2,
    Layers,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth-context";

const navigation = [
    { name: "Dashboard", href: "/dashboard", icon: LayoutDashboard },
    { name: "Campaigns", href: "/campaigns", icon: Megaphone },
    { name: "Call History", href: "/calls", icon: Phone },
    { name: "Contacts", href: "/contacts", icon: Users },
    { name: "Analytics", href: "/analytics", icon: BarChart2 },
    { name: "Recordings", href: "/recordings", icon: Volume2 },
];

const bottomNavigation = [
    { name: "Settings", href: "/settings", icon: Settings },
];

export function Sidebar() {
    const pathname = usePathname();
    const { user, logout } = useAuth();

    const handleLogout = async () => {
        await logout();
        window.location.href = "/auth/login";
    };

    return (
        <aside className="glass-sidebar flex flex-col h-full w-64 flex-shrink-0 z-10">
            {/* Logo */}
            <div className="h-20 flex items-center justify-center border-b border-white/10">
                <Link href="/dashboard" className="flex items-center gap-2">
                    <div className="w-8 h-8 text-indigo-400">
                        <Layers className="w-8 h-8" />
                    </div>
                    <span className="text-xl font-bold text-white">Talky.ai</span>
                </Link>
            </div>

            {/* Main Navigation */}
            <nav className="flex-grow p-4 space-y-2">
                {navigation.map((item) => {
                    const isActive = pathname === item.href || pathname.startsWith(item.href + "/");
                    return (
                        <Link
                            key={item.name}
                            href={item.href}
                            className={cn(
                                "nav-link flex items-center gap-3 px-4 py-2.5 rounded-lg text-gray-300 transition-all duration-200",
                                isActive
                                    ? "active bg-white/10 text-white border-l-2 border-indigo-400"
                                    : "hover:bg-white/5"
                            )}
                        >
                            <item.icon className="w-5 h-5" />
                            <span>{item.name}</span>
                        </Link>
                    );
                })}
            </nav>

            {/* Bottom Navigation */}
            <div className="p-4 border-t border-white/10 space-y-2">
                {bottomNavigation.map((item) => {
                    const isActive = pathname === item.href;
                    return (
                        <Link
                            key={item.name}
                            href={item.href}
                            className={cn(
                                "nav-link flex items-center gap-3 px-4 py-2.5 rounded-lg text-gray-300 transition-all duration-200",
                                isActive
                                    ? "active bg-white/10 text-white"
                                    : "hover:bg-white/5"
                            )}
                        >
                            <item.icon className="w-5 h-5" />
                            <span>{item.name}</span>
                        </Link>
                    );
                })}

                <button
                    onClick={handleLogout}
                    className="w-full nav-link flex items-center gap-3 px-4 py-2.5 rounded-lg text-gray-300 hover:bg-white/5 transition-all duration-200"
                >
                    <LogOut className="w-5 h-5" />
                    <span>Logout</span>
                </button>
            </div>

            {/* User Info */}
            {user && (
                <div className="p-4 border-t border-white/10">
                    <div className="flex items-center gap-3">
                        <div className="w-10 h-10 rounded-full border-2 border-indigo-400 bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center">
                            <span className="text-sm font-medium text-white">
                                {user.email?.charAt(0).toUpperCase()}
                            </span>
                        </div>
                        <div className="flex-1 min-w-0">
                            <p className="font-semibold text-white truncate">{user.name || user.email}</p>
                            <p className="text-xs text-gray-400 truncate">{user.business_name || "Admin"}</p>
                        </div>
                    </div>
                </div>
            )}
        </aside>
    );
}
