"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { dashboardApi, Campaign } from "@/lib/dashboard-api";
import { Plus, Play, Pause, Square, Users, Phone, Megaphone } from "lucide-react";
import { motion } from "framer-motion";

function getStatusStyle(status: string) {
    switch (status) {
        case "running":
            return "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30";
        case "paused":
            return "bg-yellow-500/20 text-yellow-400 border border-yellow-500/30";
        case "completed":
            return "bg-blue-500/20 text-blue-400 border border-blue-500/30";
        case "stopped":
            return "bg-red-500/20 text-red-400 border border-red-500/30";
        default:
            return "bg-gray-500/20 text-gray-400 border border-gray-500/30";
    }
}

export default function CampaignsPage() {
    const [campaigns, setCampaigns] = useState<Campaign[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [actionLoading, setActionLoading] = useState<string | null>(null);

    useEffect(() => {
        loadCampaigns();
    }, []);

    async function loadCampaigns() {
        try {
            setLoading(true);
            const data = await dashboardApi.listCampaigns();
            setCampaigns(data.campaigns);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load campaigns");
        } finally {
            setLoading(false);
        }
    }

    async function handleStart(id: string) {
        try {
            setActionLoading(id);
            await dashboardApi.startCampaign(id);
            await loadCampaigns();
        } catch (err) {
            alert(err instanceof Error ? err.message : "Failed to start campaign");
        } finally {
            setActionLoading(null);
        }
    }

    async function handlePause(id: string) {
        try {
            setActionLoading(id);
            await dashboardApi.pauseCampaign(id);
            await loadCampaigns();
        } catch (err) {
            alert(err instanceof Error ? err.message : "Failed to pause campaign");
        } finally {
            setActionLoading(null);
        }
    }

    async function handleStop(id: string) {
        try {
            setActionLoading(id);
            await dashboardApi.stopCampaign(id);
            await loadCampaigns();
        } catch (err) {
            alert(err instanceof Error ? err.message : "Failed to stop campaign");
        } finally {
            setActionLoading(null);
        }
    }

    return (
        <DashboardLayout title="Campaigns" description="Manage your voice campaigns">
            {/* Actions Header */}
            <motion.div
                initial={{ opacity: 0, y: -10 }}
                animate={{ opacity: 1, y: 0 }}
                className="flex items-center justify-between mb-6"
            >
                <div className="text-sm text-gray-400">
                    {campaigns.length} campaign{campaigns.length !== 1 ? "s" : ""}
                </div>
                <Link href="/campaigns/new">
                    <Button className="bg-white text-gray-900 hover:bg-gray-100">
                        <Plus className="w-4 h-4" />
                        New Campaign
                    </Button>
                </Link>
            </motion.div>

            {loading ? (
                <div className="flex items-center justify-center h-64">
                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-white" />
                </div>
            ) : error ? (
                <div className="content-card border-red-500/30 text-red-400">
                    {error}
                </div>
            ) : campaigns.length === 0 ? (
                <motion.div
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="content-card py-16 text-center"
                >
                    <div className="w-16 h-16 mx-auto mb-4 bg-white/10 rounded-full flex items-center justify-center">
                        <Megaphone className="w-8 h-8 text-gray-400" />
                    </div>
                    <h3 className="text-lg font-medium text-white mb-2">No campaigns yet</h3>
                    <p className="text-gray-400 mb-6">
                        Create your first campaign to start making automated calls.
                    </p>
                    <Link href="/campaigns/new">
                        <Button className="bg-white text-gray-900 hover:bg-gray-100">
                            <Plus className="w-4 h-4" />
                            Create Campaign
                        </Button>
                    </Link>
                </motion.div>
            ) : (
                <div className="space-y-4">
                    {campaigns.map((campaign, index) => (
                        <motion.div
                            key={campaign.id}
                            initial={{ opacity: 0, x: -20 }}
                            animate={{ opacity: 1, x: 0 }}
                            transition={{ delay: index * 0.05 }}
                            className="content-card"
                        >
                            <div className="flex items-start justify-between">
                                <div className="flex-1">
                                    <div className="flex items-center gap-3 mb-2">
                                        <Link
                                            href={`/campaigns/${campaign.id}`}
                                            className="text-lg font-medium text-white hover:underline"
                                        >
                                            {campaign.name}
                                        </Link>
                                        <span className={`px-2 py-0.5 text-xs font-medium rounded-full ${getStatusStyle(campaign.status)}`}>
                                            {campaign.status}
                                        </span>
                                    </div>
                                    {campaign.description && (
                                        <p className="text-sm text-gray-400 mb-4">{campaign.description}</p>
                                    )}
                                    <div className="flex items-center gap-6 text-sm text-gray-400">
                                        <div className="flex items-center gap-1">
                                            <Users className="w-4 h-4" />
                                            {campaign.total_leads || 0} leads
                                        </div>
                                        <div className="flex items-center gap-1">
                                            <Phone className="w-4 h-4" />
                                            {campaign.calls_completed || 0} completed
                                        </div>
                                        <div>
                                            Created {new Date(campaign.created_at).toLocaleDateString()}
                                        </div>
                                    </div>
                                </div>

                                <div className="flex items-center gap-2">
                                    {campaign.status === "draft" || campaign.status === "paused" || campaign.status === "stopped" ? (
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            onClick={() => handleStart(campaign.id)}
                                            disabled={actionLoading === campaign.id}
                                            className="border-white/20 text-white hover:bg-white/10"
                                        >
                                            <Play className="w-4 h-4" />
                                            Start
                                        </Button>
                                    ) : campaign.status === "running" ? (
                                        <>
                                            <Button
                                                variant="outline"
                                                size="sm"
                                                onClick={() => handlePause(campaign.id)}
                                                disabled={actionLoading === campaign.id}
                                                className="border-white/20 text-white hover:bg-white/10"
                                            >
                                                <Pause className="w-4 h-4" />
                                                Pause
                                            </Button>
                                            <Button
                                                variant="outline"
                                                size="sm"
                                                onClick={() => handleStop(campaign.id)}
                                                disabled={actionLoading === campaign.id}
                                                className="border-white/20 text-white hover:bg-white/10"
                                            >
                                                <Square className="w-4 h-4" />
                                                Stop
                                            </Button>
                                        </>
                                    ) : null}
                                </div>
                            </div>
                        </motion.div>
                    ))}
                </div>
            )}
        </DashboardLayout>
    );
}
