"use client";

import { useEffect, useRef, useState } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { extendedApi, CallSeriesItem } from "@/lib/extended-api";
import { BarChart2, TrendingUp, TrendingDown, Calendar, Activity, Percent } from "lucide-react";
import { motion } from "framer-motion";
import { Select } from "@/components/ui/select";
import { useTheme } from "@/components/providers/theme-provider";
import { cn } from "@/lib/utils";

function formatDate(dateStr: string) {
    return new Date(dateStr).toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
    });
}

function GlassStatCard({
    title,
    value,
    icon: Icon,
    iconColor = "text-foreground",
    delay = 0,
}: {
    title: string;
    value: string | number;
    icon: React.ComponentType<{ className?: string }>;
    iconColor?: string;
    delay?: number;
}) {
    const { theme } = useTheme();
    const isLight = theme === "light";

    return (
        <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay, duration: 0.4 }}
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.99 }}
            className="group rounded-2xl border border-border bg-muted/60 backdrop-blur-sm p-4 shadow-sm transition-[transform,background-color,border-color,box-shadow] duration-150 ease-out hover:-translate-y-0.5 hover:bg-background hover:shadow-md"
        >
            <div className="flex items-center gap-3">
                <div
                    className={cn(
                        "rounded-lg p-2 transition-colors",
                        isLight ? "bg-emerald-500/25 group-hover:bg-emerald-500/30" : "bg-zinc-900 group-hover:bg-zinc-900"
                    )}
                >
                    <Icon className={cn("w-5 h-5", isLight ? iconColor : "text-white")} />
                </div>
                <div>
                    <p className="text-2xl font-bold text-foreground">{value}</p>
                    <p className="text-sm text-muted-foreground">{title}</p>
                </div>
            </div>
        </motion.div>
    );
}

export default function AnalyticsPage() {
    const [data, setData] = useState<CallSeriesItem[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [groupBy, setGroupBy] = useState<"day" | "week" | "month">("day");
    const [dateRange, setDateRange] = useState(30);
    const [barTooltip, setBarTooltip] = useState<{ index: number; x: number; y: number } | null>(null);
    const barsWrapRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        let cancelled = false;

        async function loadAnalytics() {
            try {
                setLoading(true);
                const toDate = new Date().toISOString().split("T")[0];
                const fromDate = new Date(Date.now() - dateRange * 24 * 60 * 60 * 1000)
                    .toISOString()
                    .split("T")[0];

                const response = await extendedApi.getCallAnalytics(fromDate, toDate, groupBy);
                if (cancelled) return;
                setData(response.series);
            } catch (err) {
                if (cancelled) return;
                setError(err instanceof Error ? err.message : "Failed to load analytics");
            } finally {
                if (cancelled) return;
                setLoading(false);
            }
        }

        void loadAnalytics();

        return () => {
            cancelled = true;
        };
    }, [groupBy, dateRange]);

    const totals = data.reduce(
        (acc, item) => ({
            calls: acc.calls + item.total_calls,
            answered: acc.answered + item.answered,
            failed: acc.failed + item.failed,
        }),
        { calls: 0, answered: 0, failed: 0 }
    );

    const successRate = totals.calls > 0 ? Math.round((totals.answered / totals.calls) * 100) : 0;
    const maxCalls = Math.max(...data.map((d) => d.total_calls), 1);
    const barWidthClass = data.length <= 10 ? "w-12" : data.length <= 20 ? "w-9" : data.length <= 40 ? "w-7" : "w-5";
    const activeBar = barTooltip ? data[barTooltip.index] : null;

    return (
        <DashboardLayout title="Analytics" description="Call performance over time">
            {/* Filters */}
            <motion.div
                initial={{ opacity: 0, y: -10 }}
                animate={{ opacity: 1, y: 0 }}
                className="flex flex-col gap-3 mb-6 sm:flex-row sm:items-center sm:gap-4"
            >
                <motion.div
                    whileHover={{ scale: 1.02 }}
                    whileTap={{ scale: 0.99 }}
                    className="flex items-center gap-2 rounded-xl bg-muted/60 px-3 py-2 shadow-sm transition-[background-color,box-shadow] duration-150 ease-out hover:bg-background hover:shadow-md"
                >
                    <Calendar className="w-4 h-4 text-muted-foreground" aria-hidden />
                    <Select
                        value={String(dateRange)}
                        onChange={(v) => setDateRange(Number(v))}
                        ariaLabel="Select date range"
                        lightThemeGreen
                        className="w-40"
                        selectClassName="border-0 bg-transparent shadow-none h-9 pr-8 focus-visible:ring-0"
                    >
                        <option value="7">Last 7 days</option>
                        <option value="30">Last 30 days</option>
                        <option value="90">Last 90 days</option>
                    </Select>
                </motion.div>

                <div className="flex items-center gap-2">
                    <span className="text-sm text-muted-foreground">Group by:</span>
                    <motion.div
                        whileHover={{ scale: 1.02 }}
                        whileTap={{ scale: 0.99 }}
                        className="flex rounded-lg overflow-hidden bg-muted/60 px-0.5 py-0.5 shadow-sm transition-[box-shadow] duration-150 ease-out hover:shadow-md"
                    >
                        {(["day", "week", "month"] as const).map((g) => (
                            <button
                                key={g}
                                type="button"
                                onClick={() => setGroupBy(g)}
                                className={`px-3 py-1.5 text-sm transition-colors ${groupBy === g
                                    ? "rounded-md bg-background text-foreground shadow-sm"
                                    : "rounded-md text-muted-foreground hover:bg-background hover:text-foreground"
                                    }`}
                                aria-pressed={groupBy === g}
                            >
                                {g.charAt(0).toUpperCase() + g.slice(1)}
                            </button>
                        ))}
                    </motion.div>
                </div>
            </motion.div>

            {loading ? (
                <div className="flex items-center justify-center h-64" role="status" aria-live="polite" aria-busy="true">
                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-foreground/60" aria-hidden />
                    <span className="sr-only">Loading analytics…</span>
                </div>
            ) : error ? (
                <div className="rounded-2xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-600" role="alert" aria-live="assertive">
                    {error}
                </div>
            ) : (
                <div className="space-y-6">
                    {/* Summary Stats */}
                    <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                        <GlassStatCard
                            title="Total Calls"
                            value={totals.calls}
                            icon={BarChart2}
                            delay={0}
                        />
                        <GlassStatCard
                            title="Answered"
                            value={totals.answered}
                            icon={TrendingUp}
                            iconColor="text-black"
                            delay={0.1}
                        />
                        <GlassStatCard
                            title="Failed"
                            value={totals.failed}
                            icon={TrendingDown}
                            iconColor="text-black"
                            delay={0.2}
                        />
                        <GlassStatCard
                            title="Success Rate"
                            value={`${successRate}%`}
                            icon={Percent}
                            delay={0.3}
                        />
                    </div>

                    {/* Chart */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.4 }}
                        className="rounded-2xl border border-border bg-muted/60 backdrop-blur-sm p-4 shadow-sm transition-[background-color,box-shadow] duration-150 ease-out hover:bg-background hover:shadow-md"
                    >
                        <h3 className="text-lg font-semibold text-foreground mb-6">Call Volume</h3>
                        {data.length === 0 ? (
                            <div className="text-center py-12 text-muted-foreground">
                                <Activity className="w-12 h-12 mx-auto mb-4 opacity-50" aria-hidden />
                                No data for the selected period
                            </div>
                        ) : (
                            <div className="space-y-4">
                                {/* Bar Chart */}
                                <div ref={barsWrapRef} className="relative">
                                    {activeBar && barTooltip ? (
                                        <motion.div
                                            key={`${activeBar.date}-${barTooltip.x}-${barTooltip.y}`}
                                            initial={{ opacity: 0, scale: 0.98 }}
                                            animate={{ opacity: 1, scale: 1 }}
                                            transition={{ duration: 0.12, ease: "easeOut" }}
                                            className="pointer-events-none absolute z-10 rounded-xl border border-border bg-background/95 px-3 py-2 text-xs shadow-lg"
                                            style={{
                                                left: barTooltip.x,
                                                top: barTooltip.y,
                                                transform: "translate(-50%, -110%)",
                                            }}
                                        >
                                            <div className="text-sm font-semibold text-foreground">{formatDate(activeBar.date)}</div>
                                            <div className="mt-1 grid grid-cols-2 gap-x-4 gap-y-0.5 text-xs font-semibold">
                                                <div className="text-muted-foreground">
                                                    Total: <span className="font-black tabular-nums text-foreground">{activeBar.total_calls}</span>
                                                </div>
                                                <div className="text-muted-foreground">
                                                    Rate:{" "}
                                                    <span className="font-black tabular-nums text-foreground">
                                                        {activeBar.total_calls > 0 ? `${Math.round((activeBar.answered / activeBar.total_calls) * 100)}%` : "--"}
                                                    </span>
                                                </div>
                                                <div className="text-muted-foreground">
                                                    Answered: <span className="font-black tabular-nums text-emerald-600">{activeBar.answered}</span>
                                                </div>
                                                <div className="text-muted-foreground">
                                                    Failed: <span className="font-black tabular-nums text-red-600">{activeBar.failed}</span>
                                                </div>
                                            </div>
                                        </motion.div>
                                    ) : null}

                                    <div className="overflow-x-auto">
                                        <div className="min-w-max flex items-end justify-center gap-3 h-40 px-2">
                                        {data.map((item, index) => (
                                            <motion.div
                                                key={index}
                                                initial={{ scaleY: 0 }}
                                                animate={{ scaleY: 1 }}
                                                transition={{ delay: 0.5 + index * 0.02 }}
                                                whileHover={{ scale: 1.06 }}
                                                className={`flex flex-col items-center gap-1 origin-bottom ${barWidthClass}`}
                                                onMouseEnter={(e) => {
                                                    const wrap = barsWrapRef.current;
                                                    if (!wrap) return;
                                                    const rect = wrap.getBoundingClientRect();
                                                    setBarTooltip({ index, x: e.clientX - rect.left, y: e.clientY - rect.top });
                                                }}
                                                onMouseMove={(e) => {
                                                    const wrap = barsWrapRef.current;
                                                    if (!wrap) return;
                                                    const rect = wrap.getBoundingClientRect();
                                                    setBarTooltip((prev) => {
                                                        if (!prev || prev.index !== index) return { index, x: e.clientX - rect.left, y: e.clientY - rect.top };
                                                        const nextX = e.clientX - rect.left;
                                                        const nextY = e.clientY - rect.top;
                                                        if (Math.abs(prev.x - nextX) < 2 && Math.abs(prev.y - nextY) < 2) return prev;
                                                        return { index, x: nextX, y: nextY };
                                                    });
                                                }}
                                                onMouseLeave={() => {
                                                    setBarTooltip(null);
                                                }}
                                                onFocus={(e) => {
                                                    const wrap = barsWrapRef.current;
                                                    if (!wrap) return;
                                                    const rect = wrap.getBoundingClientRect();
                                                    const t = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
                                                    setBarTooltip({ index, x: t.left + t.width / 2 - rect.left, y: t.top - rect.top });
                                                }}
                                                onBlur={() => {
                                                    setBarTooltip(null);
                                                }}
                                                tabIndex={0}
                                                role="group"
                                                aria-label={`${formatDate(item.date)}: total ${item.total_calls}, answered ${item.answered}, failed ${item.failed}`}
                                            >
                                                <div className="w-full flex flex-col gap-0.5">
                                                    <motion.div
                                                        initial={{ height: 0 }}
                                                        animate={{
                                                            height: `${(item.answered / maxCalls) * 120}px`,
                                                        }}
                                                        transition={{ delay: 0.5 + index * 0.02, duration: 0.5 }}
                                                        className="w-full bg-gradient-to-t from-emerald-600 to-emerald-400 rounded-t hover:from-emerald-500 hover:to-emerald-300 transition-colors cursor-pointer"
                                                        title={`Answered: ${item.answered}`}
                                                    />
                                                    <motion.div
                                                        initial={{ height: 0 }}
                                                        animate={{
                                                            height: `${(item.failed / maxCalls) * 120}px`,
                                                        }}
                                                        transition={{ delay: 0.5 + index * 0.02, duration: 0.5 }}
                                                        className="w-full bg-gradient-to-b from-red-400 to-red-600 rounded-b hover:from-red-300 hover:to-red-500 transition-colors cursor-pointer"
                                                        title={`Failed: ${item.failed}`}
                                                    />
                                                </div>
                                            </motion.div>
                                        ))}
                                        </div>
                                    </div>
                                </div>

                                {/* X-axis labels */}
                                <div className="overflow-x-auto">
                                    <div className="min-w-max flex items-start justify-center gap-3 px-2">
                                        {data.map((item, index) => (
                                            <div
                                                key={index}
                                                className={`${barWidthClass} text-center text-xs text-muted-foreground`}
                                            >
                                                {formatDate(item.date)}
                                            </div>
                                        ))}
                                    </div>
                                </div>

                                {/* Legend */}
                                <div className="flex items-center justify-center gap-6 pt-4 border-t border-border/60">
                                    <div className="flex items-center gap-2">
                                        <div className="w-3 h-3 bg-gradient-to-t from-emerald-600 to-emerald-400 rounded" />
                                        <span className="text-sm text-muted-foreground">Answered</span>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        <div className="w-3 h-3 bg-gradient-to-b from-red-400 to-red-600 rounded" />
                                        <span className="text-sm text-muted-foreground">Failed</span>
                                    </div>
                                </div>
                            </div>
                        )}
                    </motion.div>

                    {/* Data Table */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.6 }}
                        className="rounded-2xl border border-border bg-muted/60 backdrop-blur-sm p-4 shadow-sm transition-[box-shadow] duration-150 ease-out hover:shadow-md"
                    >
                        <h3 className="text-lg font-semibold text-foreground mb-4">Breakdown</h3>
                        <div className="overflow-x-hidden overflow-y-visible">
                            <table className="w-full table-fixed">
                                <thead className="border-b border-border/60">
                                    <tr>
                                        <th className="w-[32%] px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase">
                                            Date
                                        </th>
                                        <th className="w-[17%] px-4 py-3 text-right text-xs font-medium text-muted-foreground uppercase">
                                            Total
                                        </th>
                                        <th className="w-[17%] px-4 py-3 text-right text-xs font-medium text-muted-foreground uppercase">
                                            Answered
                                        </th>
                                        <th className="w-[17%] px-4 py-3 text-right text-xs font-medium text-muted-foreground uppercase">
                                            Failed
                                        </th>
                                        <th className="w-[17%] px-4 py-3 text-right text-xs font-medium text-muted-foreground uppercase">
                                            Rate
                                        </th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-border/60">
                                    {data.map((item, index) => (
                                        <motion.tr
                                            key={index}
                                            initial={{ opacity: 0, x: -10 }}
                                            animate={{ opacity: 1, x: 0 }}
                                            transition={{ delay: 0.7 + index * 0.03 }}
                                            whileHover={{ scale: 1.02 }}
                                            className="bg-muted/40 hover:bg-background transition-[transform,background-color,box-shadow] duration-150 ease-out hover:shadow-sm"
                                            style={{ transformOrigin: "center" }}
                                        >
                                            <td className="px-4 py-3 text-sm text-muted-foreground truncate">{item.date}</td>
                                            <td className="px-4 py-3 text-sm text-right text-foreground font-semibold tabular-nums truncate">{item.total_calls}</td>
                                            <td className="px-4 py-3 text-sm text-right text-emerald-600 tabular-nums truncate">
                                                {item.answered}
                                            </td>
                                            <td className="px-4 py-3 text-sm text-right text-red-600 tabular-nums truncate">
                                                {item.failed}
                                            </td>
                                            <td className="px-4 py-3 text-sm text-right text-foreground tabular-nums truncate">
                                                {item.total_calls > 0
                                                    ? `${Math.round((item.answered / item.total_calls) * 100)}%`
                                                    : "--"}
                                            </td>
                                        </motion.tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </motion.div>
                </div>
            )}
        </DashboardLayout>
    );
}
