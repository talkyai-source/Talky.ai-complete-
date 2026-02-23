"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Modal } from "@/components/ui/modal";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import type { Campaign } from "@/lib/dashboard-api";
import { parseCommandInput, CommandResultCategory } from "@/lib/campaign-performance";
import { useRouter } from "next/navigation";
import { cn } from "@/lib/utils";

type Result = {
    id: string;
    category: CommandResultCategory;
    title: string;
    subtitle?: string;
    href?: string;
    action?: () => void;
};

function matchScore(text: string, query: string) {
    const t = text.toLowerCase();
    const q = query.toLowerCase();
    if (q.length === 0) return 0;
    if (t === q) return 1000;
    if (t.startsWith(q)) return 700;
    if (t.includes(q)) return 300;
    return 0;
}

export function CommandBar({
    campaigns,
    onPause,
    onResume,
}: {
    campaigns: Campaign[];
    onPause: (id: string) => Promise<void>;
    onResume: (id: string) => Promise<void>;
}) {
    const router = useRouter();
    const [open, setOpen] = useState(false);
    const [value, setValue] = useState("");
    const [activeIndex, setActiveIndex] = useState(0);
    const inputRef = useRef<HTMLInputElement | null>(null);

    const openBar = () => {
        setOpen(true);
        setActiveIndex(0);
    };

    useEffect(() => {
        const onKeyDown = (e: KeyboardEvent) => {
            const key = e.key.toLowerCase();
            const isK = key === "k";
            const meta = e.metaKey || e.ctrlKey;
            if (meta && isK) {
                e.preventDefault();
                openBar();
            }
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, []);

    useEffect(() => {
        if (!open) return;
        const t = window.setTimeout(() => inputRef.current?.focus(), 0);
        return () => window.clearTimeout(t);
    }, [open]);

    const parsed = useMemo(() => parseCommandInput(value), [value]);

    const results = useMemo(() => {
        const q = parsed.query.trim();
        const users: Array<{ id: string; name: string; role: string }> = [
            { id: "usr-001", name: "Alex Operator", role: "Ops" },
            { id: "usr-002", name: "Morgan Analyst", role: "Analytics" },
            { id: "usr-003", name: "Jamie Admin", role: "Admin" },
        ];

        const tags: Array<{ id: string; name: string }> = [
            { id: "tag-priority", name: "priority" },
            { id: "tag-sales", name: "sales" },
            { id: "tag-support", name: "support" },
            { id: "tag-survey", name: "survey" },
        ];

        const base: Result[] = [
            { id: "nav-analytics", category: "Reports", title: "Analytics Dashboard", href: "/analytics" },
            { id: "nav-settings", category: "Settings", title: "Settings", href: "/ai-options" },
            { id: "nav-help", category: "Help Docs", title: "Getting Started", href: "/" },
        ];

        const campaignResults: Result[] = campaigns.map((c) => ({
            id: `camp-${c.id}`,
            category: "Campaigns",
            title: c.name,
            subtitle: c.id,
            href: `/campaigns/${c.id}`,
        }));

        if (parsed.prefix === ">") {
            const scored = [...base, ...campaignResults]
                .map((r) => ({ r, score: matchScore(r.title, q) + (r.subtitle ? matchScore(r.subtitle, q) * 0.2 : 0) }))
                .filter((x) => x.score > 0 || q.length === 0)
                .sort((a, b) => b.score - a.score)
                .slice(0, 12)
                .map((x) => x.r);
            return scored;
        }

        if (parsed.prefix === "/") {
            const actions: Result[] = [
                {
                    id: "action-export",
                    category: "Reports",
                    title: "Export campaigns",
                    subtitle: "Opens export modal",
                    action: () => router.push("/campaigns#export"),
                },
                {
                    id: "action-new-campaign",
                    category: "Campaigns",
                    title: "Create new campaign",
                    subtitle: "Navigate to create flow",
                    action: () => router.push("/campaigns/new"),
                },
                {
                    id: "action-pause-active",
                    category: "Campaigns",
                    title: "Pause all active campaigns",
                    subtitle: "Prototype action",
                    action: async () => {
                        for (const c of campaigns) {
                            if ((c.status || "").toLowerCase() === "running") await onPause(c.id);
                        }
                    },
                },
                {
                    id: "action-resume-paused",
                    category: "Campaigns",
                    title: "Resume all paused campaigns",
                    subtitle: "Prototype action",
                    action: async () => {
                        for (const c of campaigns) {
                            if ((c.status || "").toLowerCase() === "paused") await onResume(c.id);
                        }
                    },
                },
            ];
            const scored = actions
                .map((r) => ({ r, score: matchScore(r.title, q) + (r.subtitle ? matchScore(r.subtitle, q) * 0.2 : 0) }))
                .filter((x) => x.score > 0 || q.length === 0)
                .sort((a, b) => b.score - a.score)
                .slice(0, 12)
                .map((x) => x.r);
            return scored;
        }

        if (parsed.prefix === "@") {
            const scored = users
                .map((u) => ({
                    r: {
                        id: `user-${u.id}`,
                        category: "Settings" as const,
                        title: u.name,
                        subtitle: u.role,
                        action: () => router.push(`/ai-options?user=${encodeURIComponent(u.id)}`),
                    },
                    score: matchScore(u.name, q) + matchScore(u.id, q) * 0.2,
                }))
                .filter((x) => x.score > 0 || q.length === 0)
                .sort((a, b) => b.score - a.score)
                .slice(0, 12)
                .map((x) => x.r);
            return scored;
        }

        if (parsed.prefix === "#") {
            const scored = tags
                .map((t) => ({
                    r: {
                        id: `tag-${t.id}`,
                        category: "Reports" as const,
                        title: `#${t.name}`,
                        subtitle: "Filter view (prototype)",
                        action: () => router.push(`/campaigns?tag=${encodeURIComponent(t.name)}`),
                    },
                    score: matchScore(t.name, q) + matchScore(t.id, q) * 0.2,
                }))
                .filter((x) => x.score > 0 || q.length === 0)
                .sort((a, b) => b.score - a.score)
                .slice(0, 12)
                .map((x) => x.r);
            return scored;
        }

        const scored = [...campaignResults, ...base]
            .map((r) => ({ r, score: matchScore(r.title, q) + (r.subtitle ? matchScore(r.subtitle, q) * 0.2 : 0) }))
            .filter((x) => x.score > 0 || q.length === 0)
            .sort((a, b) => b.score - a.score)
            .slice(0, 12)
            .map((x) => x.r);

        return scored;
    }, [campaigns, onPause, onResume, parsed.prefix, parsed.query, router]);

    const runResult = async (r: Result) => {
        setOpen(false);
        setValue("");
        if (r.action) {
            await r.action();
            return;
        }
        if (r.href) router.push(r.href);
    };

    const onKeyDownInput = (e: React.KeyboardEvent<HTMLInputElement>) => {
        if (e.key === "ArrowDown") {
            e.preventDefault();
            setActiveIndex((i) => Math.min(results.length - 1, i + 1));
        } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActiveIndex((i) => Math.max(0, i - 1));
        } else if (e.key === "Enter") {
            e.preventDefault();
            const r = results[activeIndex];
            if (r) void runResult(r);
        } else if (e.key === "Escape") {
            e.preventDefault();
            setOpen(false);
        }
    };

    return (
        <>
            <div className="content-card flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div>
                    <div className="text-sm font-semibold text-foreground">Search & Command Bar</div>
                    <div className="mt-1 text-sm text-muted-foreground">Press Ctrl + K to open.</div>
                </div>
                <div className="flex items-center gap-2">
                    <Button
                        type="button"
                        variant="outline"
                        onClick={openBar}
                        className="border-teal-500/60 bg-teal-600 text-white shadow-sm hover:bg-teal-700 hover:text-white"
                    >
                        Ctrl + K
                    </Button>
                </div>
            </div>

            <Modal
                open={open}
                onOpenChange={(next) => {
                    if (next) {
                        openBar();
                        return;
                    }
                    setOpen(false);
                    setValue("");
                    setActiveIndex(0);
                }}
                title="Command Bar"
                description="Search campaigns, navigate, and run actions."
                size="lg"
            >
                <div className="space-y-3">
                    <Input
                        ref={inputRef}
                        value={value}
                        onChange={(e) => {
                            setValue(e.target.value);
                            setActiveIndex(0);
                        }}
                        onKeyDown={onKeyDownInput}
                        placeholder='Try "Holiday", "/pause", or "> analytics"…'
                        className="border-input bg-muted/50 text-foreground placeholder:text-muted-foreground focus-visible:ring-ring"
                    />
                    <div className="rounded-xl border border-border bg-card p-2">
                        {results.length === 0 ? (
                            <div className="px-2 py-6 text-center text-sm text-muted-foreground">No results</div>
                        ) : (
                            <div className="space-y-1">
                                {results.map((r, idx) => (
                                    <button
                                        key={r.id}
                                        type="button"
                                        className={cn(
                                            "flex w-full items-start justify-between gap-3 rounded-lg px-3 py-2 text-left transition-colors duration-150 ease-out",
                                            idx === activeIndex ? "bg-accent text-accent-foreground" : "hover:bg-accent hover:text-accent-foreground text-foreground"
                                        )}
                                        onMouseEnter={() => setActiveIndex(idx)}
                                        onClick={() => void runResult(r)}
                                    >
                                        <div className="min-w-0">
                                            <div className="truncate text-sm font-semibold">{r.title}</div>
                                            {r.subtitle ? <div className="truncate text-xs font-semibold text-muted-foreground">{r.subtitle}</div> : null}
                                        </div>
                                        <div className="shrink-0 text-xs font-semibold text-muted-foreground">{r.category}</div>
                                    </button>
                                ))}
                            </div>
                        )}
                    </div>
                    <div className="text-xs font-semibold text-muted-foreground">
                        Prefixes: <span className="text-foreground">/</span> actions, <span className="text-foreground">&gt;</span> navigation, <span className="text-foreground">@</span> users, <span className="text-foreground">#</span> tags
                    </div>
                </div>
            </Modal>
        </>
    );
}
