"use client";

import { Sidebar } from "./sidebar";

interface DashboardLayoutProps {
    children: React.ReactNode;
    title?: string;
    description?: string;
}

export function DashboardLayout({ children, title, description }: DashboardLayoutProps) {
    return (
        <div className="relative min-h-screen w-full flex bg-gray-900 text-gray-200">
            {/* Animated Background Shapes */}
            <div className="shape-1"></div>
            <div className="shape-2"></div>

            <Sidebar />

            <div className="flex-1 flex flex-col overflow-hidden z-10">
                {/* Header */}
                {(title || description) && (
                    <header className="bg-gray-900/50 backdrop-blur-sm border-b border-white/10 px-8 py-6">
                        {title && (
                            <h1 className="text-2xl font-semibold text-white">{title}</h1>
                        )}
                        {description && (
                            <p className="mt-1 text-sm text-gray-400">{description}</p>
                        )}
                    </header>
                )}

                {/* Main Content */}
                <main className="flex-1 overflow-auto p-8">
                    {children}
                </main>
            </div>
        </div>
    );
}
