"use client";

import { useEffect, useState } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { dashboardApi, DashboardSummary, Campaign } from "@/lib/dashboard-api";
import { Phone, PhoneOff, Clock, Megaphone, ArrowUpRight, ArrowDownRight, TrendingUp } from "lucide-react";
import Link from "next/link";
import { motion } from "framer-motion";

function GlassStatCard({
    title,
    value,
    icon: Icon,
    trend,
    trendLabel,
    delay = 0,
}: {
    title: string;
    value: string | number;
    icon: React.ComponentType<{ className?: string }>;
    trend?: "up" | "down";
    trendLabel?: string;
    delay?: number;
}) {
    return (
        <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay, duration: 0.4 }}
            className="content-card group"
        >
            <div className="flex items-center justify-between">
                <div>
                    <p className="text-sm font-medium text-gray-400">{title}</p>
                    <p className="mt-2 text-3xl font-bold text-white">{value}</p>
                    {trendLabel && (
                        <div className="mt-2 flex items-center text-sm">
                            {trend === "up" ? (
                                <ArrowUpRight className="w-4 h-4 text-emerald-400" />
                            ) : trend === "down" ? (
                                <ArrowDownRight className="w-4 h-4 text-red-400" />
                            ) : null}
                            <span className={trend === "up" ? "text-emerald-400" : "text-red-400"}>
                                {trendLabel}
                            </span>
                        </div>
                    )}
                </div>
                <div className="p-3 bg-white/10 rounded-xl group-hover:bg-white/15 transition-colors">
                    <Icon className="w-6 h-6 text-white" />
                </div>
            </div>
        </motion.div>
    );
}

export default function DashboardPage() {
    const [summary, setSummary] = useState<DashboardSummary | null>(null);
    const [campaigns, setCampaigns] = useState<Campaign[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    useEffect(() => {
        loadData();
    }, []);

    async function loadData() {
        try {
            setLoading(true);
            const [summaryData, campaignsData] = await Promise.all([
                dashboardApi.getDashboardSummary(),
                dashboardApi.listCampaigns(),
            ]);
            setSummary(summaryData);
            setCampaigns(campaignsData.campaigns);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load dashboard");
        } finally {
            setLoading(false);
        }
    }

    const successRate = summary
        ? summary.total_calls > 0
            ? Math.round((summary.answered_calls / summary.total_calls) * 100)
            : 0
        : 0;

    return (
        <DashboardLayout title="Dashboard" description="Overview of your voice campaigns">
            {loading ? (
                <div className="flex items-center justify-center h-64">
                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-white" />
                </div>
            ) : error ? (
                <div className="content-card border-red-500/30 text-red-400">
                    {error}
                </div>
            ) : (
                <div className="space-y-8">
                    {/* Stats Grid */}
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
                        <GlassStatCard
                            title="Total Calls"
                            value={summary?.total_calls || 0}
                            icon={Phone}
                            delay={0}
                        />
                        <GlassStatCard
                            title="Answered"
                            value={summary?.answered_calls || 0}
                            icon={TrendingUp}
                            trend="up"
                            trendLabel={`${successRate}% success`}
                            delay={0.1}
                        />
                        <GlassStatCard
                            title="Failed"
                            value={summary?.failed_calls || 0}
                            icon={PhoneOff}
                            delay={0.2}
                        />
                        <GlassStatCard
                            title="Active Campaigns"
                            value={summary?.active_campaigns || 0}
                            icon={Megaphone}
                            delay={0.3}
                        />
                    </div>

                    {/* Minutes Usage */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.4 }}
                        className="content-card"
                    >
                        <h3 className="text-lg font-semibold text-white mb-4">Minutes Usage</h3>
                        <div className="flex items-center gap-4">
                            <div className="flex-1">
                                <div className="flex items-center justify-between mb-2">
                                    <span className="text-sm text-gray-400">Used</span>
                                    <span className="text-sm font-medium text-white">
                                        {summary?.minutes_used || 0} / {(summary?.minutes_used || 0) + (summary?.minutes_remaining || 0)} min
                                    </span>
                                </div>
                                <div className="w-full h-3 bg-white/10 rounded-full overflow-hidden">
                                    <motion.div
                                        initial={{ width: 0 }}
                                        animate={{
                                            width: `${summary
                                                ? (summary.minutes_used /
                                                    (summary.minutes_used + summary.minutes_remaining || 1)) *
                                                100
                                                : 0
                                                }%`
                                        }}
                                        transition={{ delay: 0.6, duration: 0.8 }}
                                        className="h-full bg-gradient-to-r from-emerald-400 to-emerald-500 rounded-full"
                                    />
                                </div>
                            </div>
                            <div className="flex items-center gap-2 text-sm text-gray-400">
                                <Clock className="w-4 h-4" />
                                {summary?.minutes_remaining || 0} min remaining
                            </div>
                        </div>
                    </motion.div>

                    {/* Recent Campaigns */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.5 }}
                        className="content-card"
                    >
                        <div className="flex items-center justify-between mb-4">
                            <h3 className="text-lg font-semibold text-white">Recent Campaigns</h3>
                            <Link
                                href="/campaigns"
                                className="text-sm text-gray-400 hover:text-white transition-colors"
                            >
                                View all
                            </Link>
                        </div>
                        {campaigns.length === 0 ? (
                            <div className="text-center py-8 text-gray-400">
                                <Megaphone className="w-12 h-12 mx-auto mb-4 opacity-50" />
                                <p>No campaigns yet</p>
                                <Link
                                    href="/campaigns/new"
                                    className="mt-4 inline-block text-sm font-medium text-white hover:underline"
                                >
                                    Create your first campaign
                                </Link>
                            </div>
                        ) : (
                            <div className="space-y-3">
                                {campaigns.slice(0, 5).map((campaign, index) => (
                                    <motion.div
                                        key={campaign.id}
                                        initial={{ opacity: 0, x: -20 }}
                                        animate={{ opacity: 1, x: 0 }}
                                        transition={{ delay: 0.6 + index * 0.1 }}
                                    >
                                        <Link
                                            href={`/campaigns/${campaign.id}`}
                                            className="flex items-center justify-between p-4 rounded-xl bg-white/5 border border-white/10 hover:bg-white/10 transition-all"
                                        >
                                            <div>
                                                <h4 className="font-medium text-white">{campaign.name}</h4>
                                                <p className="text-sm text-gray-400">
                                                    {campaign.total_leads} leads | {campaign.calls_completed} completed
                                                </p>
                                            </div>
                                            <div className="flex items-center gap-4">
                                                <span
                                                    className={`px-2 py-1 text-xs font-medium rounded-full ${campaign.status === "running"
                                                        ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30"
                                                        : campaign.status === "paused"
                                                            ? "bg-yellow-500/20 text-yellow-400 border border-yellow-500/30"
                                                            : campaign.status === "completed"
                                                                ? "bg-gray-500/20 text-gray-400 border border-gray-500/30"
                                                                : "bg-gray-500/20 text-gray-400"
                                                        }`}
                                                >
                                                    {campaign.status}
                                                </span>
                                                <ArrowUpRight className="w-4 h-4 text-gray-500" />
                                            </div>
                                        </Link>
                                    </motion.div>
                                ))}
                            </div>
                        )}
                    </motion.div>
                </div>
            )}
        </DashboardLayout>
    );
}
