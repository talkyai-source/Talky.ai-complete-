"use client";

import { useEffect, useState } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { extendedApi, CallSeriesItem } from "@/lib/extended-api";
import { BarChart2, TrendingUp, TrendingDown, Calendar, Activity, Percent } from "lucide-react";
import { motion } from "framer-motion";

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
    iconColor = "text-white",
    delay = 0,
}: {
    title: string;
    value: string | number;
    icon: React.ComponentType<{ className?: string }>;
    iconColor?: string;
    delay?: number;
}) {
    return (
        <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay, duration: 0.4 }}
            className="content-card"
        >
            <div className="flex items-center gap-3">
                <div className={`p-2 bg-white/10 rounded-lg`}>
                    <Icon className={`w-5 h-5 ${iconColor}`} />
                </div>
                <div>
                    <p className="text-2xl font-bold text-white">{value}</p>
                    <p className="text-sm text-gray-400">{title}</p>
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

    useEffect(() => {
        loadAnalytics();
    }, [groupBy, dateRange]);

    async function loadAnalytics() {
        try {
            setLoading(true);
            const toDate = new Date().toISOString().split("T")[0];
            const fromDate = new Date(Date.now() - dateRange * 24 * 60 * 60 * 1000)
                .toISOString()
                .split("T")[0];

            const response = await extendedApi.getCallAnalytics(fromDate, toDate, groupBy);
            setData(response.series);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load analytics");
        } finally {
            setLoading(false);
        }
    }

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

    return (
        <DashboardLayout title="Analytics" description="Call performance over time">
            {/* Filters */}
            <motion.div
                initial={{ opacity: 0, y: -10 }}
                animate={{ opacity: 1, y: 0 }}
                className="flex items-center gap-4 mb-6"
            >
                <div className="flex items-center gap-2">
                    <Calendar className="w-4 h-4 text-gray-400" />
                    <select
                        value={dateRange}
                        onChange={(e) => setDateRange(Number(e.target.value))}
                        className="text-sm bg-white/10 border border-white/20 text-white rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-white/30"
                    >
                        <option value={7} className="bg-gray-900">Last 7 days</option>
                        <option value={30} className="bg-gray-900">Last 30 days</option>
                        <option value={90} className="bg-gray-900">Last 90 days</option>
                    </select>
                </div>

                <div className="flex items-center gap-2">
                    <span className="text-sm text-gray-400">Group by:</span>
                    <div className="flex bg-white/10 rounded-lg overflow-hidden border border-white/20">
                        {(["day", "week", "month"] as const).map((g) => (
                            <button
                                key={g}
                                onClick={() => setGroupBy(g)}
                                className={`px-3 py-1.5 text-sm transition-colors ${groupBy === g
                                    ? "bg-white text-gray-900"
                                    : "text-gray-300 hover:bg-white/10"
                                    }`}
                            >
                                {g.charAt(0).toUpperCase() + g.slice(1)}
                            </button>
                        ))}
                    </div>
                </div>
            </motion.div>

            {loading ? (
                <div className="flex items-center justify-center h-64">
                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-white" />
                </div>
            ) : error ? (
                <div className="content-card border-red-500/30 text-red-400">
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
                            iconColor="text-emerald-400"
                            delay={0.1}
                        />
                        <GlassStatCard
                            title="Failed"
                            value={totals.failed}
                            icon={TrendingDown}
                            iconColor="text-red-400"
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
                        className="content-card"
                    >
                        <h3 className="text-lg font-semibold text-white mb-6">Call Volume</h3>
                        {data.length === 0 ? (
                            <div className="text-center py-12 text-gray-400">
                                <Activity className="w-12 h-12 mx-auto mb-4 opacity-50" />
                                No data for the selected period
                            </div>
                        ) : (
                            <div className="space-y-4">
                                {/* Bar Chart */}
                                <div className="flex items-end gap-1 h-48">
                                    {data.map((item, index) => (
                                        <motion.div
                                            key={index}
                                            initial={{ scaleY: 0 }}
                                            animate={{ scaleY: 1 }}
                                            transition={{ delay: 0.5 + index * 0.02 }}
                                            className="flex-1 flex flex-col items-center gap-0.5 origin-bottom"
                                        >
                                            <div className="w-full flex flex-col gap-0.5">
                                                <motion.div
                                                    initial={{ height: 0 }}
                                                    animate={{
                                                        height: `${(item.answered / maxCalls) * 160}px`
                                                    }}
                                                    transition={{ delay: 0.5 + index * 0.02, duration: 0.5 }}
                                                    className="w-full bg-gradient-to-t from-emerald-600 to-emerald-400 rounded-t hover:from-emerald-500 hover:to-emerald-300 transition-colors cursor-pointer"
                                                    title={`Answered: ${item.answered}`}
                                                />
                                                <motion.div
                                                    initial={{ height: 0 }}
                                                    animate={{
                                                        height: `${(item.failed / maxCalls) * 160}px`
                                                    }}
                                                    transition={{ delay: 0.5 + index * 0.02, duration: 0.5 }}
                                                    className="w-full bg-gradient-to-b from-red-400 to-red-600 rounded-b hover:from-red-300 hover:to-red-500 transition-colors cursor-pointer"
                                                    title={`Failed: ${item.failed}`}
                                                />
                                            </div>
                                        </motion.div>
                                    ))}
                                </div>

                                {/* X-axis labels */}
                                <div className="flex gap-1">
                                    {data.map((item, index) => (
                                        <div
                                            key={index}
                                            className="flex-1 text-center text-xs text-gray-500"
                                        >
                                            {formatDate(item.date)}
                                        </div>
                                    ))}
                                </div>

                                {/* Legend */}
                                <div className="flex items-center justify-center gap-6 pt-4 border-t border-white/10">
                                    <div className="flex items-center gap-2">
                                        <div className="w-3 h-3 bg-gradient-to-t from-emerald-600 to-emerald-400 rounded" />
                                        <span className="text-sm text-gray-400">Answered</span>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        <div className="w-3 h-3 bg-gradient-to-b from-red-400 to-red-600 rounded" />
                                        <span className="text-sm text-gray-400">Failed</span>
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
                        className="content-card"
                    >
                        <h3 className="text-lg font-semibold text-white mb-4">Breakdown</h3>
                        <div className="overflow-x-auto">
                            <table className="w-full">
                                <thead className="border-b border-white/10">
                                    <tr>
                                        <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">
                                            Date
                                        </th>
                                        <th className="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase">
                                            Total
                                        </th>
                                        <th className="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase">
                                            Answered
                                        </th>
                                        <th className="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase">
                                            Failed
                                        </th>
                                        <th className="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase">
                                            Rate
                                        </th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-white/5">
                                    {data.map((item, index) => (
                                        <motion.tr
                                            key={index}
                                            initial={{ opacity: 0, x: -10 }}
                                            animate={{ opacity: 1, x: 0 }}
                                            transition={{ delay: 0.7 + index * 0.03 }}
                                            className="hover:bg-white/5 transition-colors"
                                        >
                                            <td className="px-4 py-3 text-sm text-gray-300">{item.date}</td>
                                            <td className="px-4 py-3 text-sm text-right text-white font-medium">{item.total_calls}</td>
                                            <td className="px-4 py-3 text-sm text-right text-emerald-400">
                                                {item.answered}
                                            </td>
                                            <td className="px-4 py-3 text-sm text-right text-red-400">
                                                {item.failed}
                                            </td>
                                            <td className="px-4 py-3 text-sm text-right text-white">
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
