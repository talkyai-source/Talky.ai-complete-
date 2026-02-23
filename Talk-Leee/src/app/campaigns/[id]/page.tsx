"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { dashboardApi, Campaign, Contact } from "@/lib/dashboard-api";
import {
    ArrowLeft,
    Play,
    Pause,
    Square,
    Plus,
    Users,
    Phone,
    CheckCircle,
    XCircle,
    Loader2,
} from "lucide-react";
import { motion } from "framer-motion";

function getStatusStyle(status: string) {
    switch (status) {
        case "running":
            return "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border border-emerald-500/20";
        case "paused":
            return "bg-yellow-500/10 text-yellow-700 dark:text-yellow-400 border border-yellow-500/20";
        case "completed":
            return "bg-blue-500/10 text-blue-700 dark:text-blue-400 border border-blue-500/20";
        case "stopped":
            return "bg-red-500/10 text-red-700 dark:text-red-400 border border-red-500/20";
        default:
            return "bg-muted text-muted-foreground border border-border";
    }
}

function getContactStatusStyle(result: string) {
    switch (result) {
        case "goal_achieved":
            return "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border border-emerald-500/20";
        case "pending":
            return "bg-muted text-muted-foreground border border-border";
        default:
            return "bg-yellow-500/10 text-yellow-700 dark:text-yellow-400 border border-yellow-500/20";
    }
}

export default function CampaignDetailPage() {
    const params = useParams();
    const router = useRouter();
    const campaignId = params.id as string;

    const [campaign, setCampaign] = useState<Campaign | null>(null);
    const [contacts, setContacts] = useState<Contact[]>([]);
    const [stats, setStats] = useState<{
        total_leads: number;
        goals_achieved: number;
        job_status_counts: Record<string, number>;
        call_outcome_counts: Record<string, number>;
    } | null>(null);
    const [loading, setLoading] = useState(true);
    const [actionLoading, setActionLoading] = useState(false);
    const [error, setError] = useState("");

    // Add contact form
    const [showAddContact, setShowAddContact] = useState(false);
    const [contactForm, setContactForm] = useState({
        phone_number: "",
        first_name: "",
        last_name: "",
        email: "",
    });
    const [addingContact, setAddingContact] = useState(false);

    const loadData = useCallback(async () => {
        try {
            setLoading(true);
            const [campaignData, contactsData, statsData] = await Promise.all([
                dashboardApi.getCampaign(campaignId),
                dashboardApi.listContacts(campaignId),
                dashboardApi.getCampaignStats(campaignId),
            ]);
            setCampaign(campaignData.campaign);
            setContacts(contactsData.items);
            setStats(statsData);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load campaign");
        } finally {
            setLoading(false);
        }
    }, [campaignId]);

    useEffect(() => {
        if (campaignId) {
            void loadData();
        }
    }, [campaignId, loadData]);

    async function handleStart() {
        try {
            setActionLoading(true);
            await dashboardApi.startCampaign(campaignId);
            await loadData();
        } catch (err) {
            alert(err instanceof Error ? err.message : "Failed to start campaign");
        } finally {
            setActionLoading(false);
        }
    }

    async function handlePause() {
        try {
            setActionLoading(true);
            await dashboardApi.pauseCampaign(campaignId);
            await loadData();
        } catch (err) {
            alert(err instanceof Error ? err.message : "Failed to pause campaign");
        } finally {
            setActionLoading(false);
        }
    }

    async function handleStop() {
        try {
            setActionLoading(true);
            await dashboardApi.stopCampaign(campaignId);
            await loadData();
        } catch (err) {
            alert(err instanceof Error ? err.message : "Failed to stop campaign");
        } finally {
            setActionLoading(false);
        }
    }

    async function handleAddContact(e: React.FormEvent) {
        e.preventDefault();
        try {
            setAddingContact(true);
            await dashboardApi.addContact(campaignId, contactForm);
            setContactForm({ phone_number: "", first_name: "", last_name: "", email: "" });
            setShowAddContact(false);
            await loadData();
        } catch (err) {
            alert(err instanceof Error ? err.message : "Failed to add contact");
        } finally {
            setAddingContact(false);
        }
    }

    return (
        <DashboardLayout title={campaign?.name} description={campaign?.description ?? "Campaign stats, contacts, and controls."}>
            <motion.div
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                className="mb-6"
            >
                <button
                    onClick={() => router.push("/campaigns")}
                    className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
                >
                    <ArrowLeft className="w-4 h-4" />
                    Back to campaigns
                </button>
            </motion.div>

            {loading ? (
                <div className="flex items-center justify-center h-64">
                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-foreground/60" />
                </div>
            ) : error ? (
                <div className="content-card border-red-500/30 text-red-400">
                    {error}
                </div>
            ) : campaign ? (
                <div className="space-y-6">
                    {/* Header */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        className="flex items-start justify-between"
                    >
                        <div>
                            <div className="flex items-center gap-3">
                                <h2 className="text-2xl font-semibold text-foreground">{campaign.name}</h2>
                                <span className={`px-3 py-1 text-sm font-medium rounded-full ${getStatusStyle(campaign.status)}`}>
                                    {campaign.status}
                                </span>
                            </div>
                            {campaign.description && (
                                <p className="mt-2 text-muted-foreground">{campaign.description}</p>
                            )}
                        </div>

                        <div className="flex gap-2">
                            {campaign.status === "draft" || campaign.status === "paused" || campaign.status === "stopped" ? (
                                <Button onClick={handleStart} disabled={actionLoading}>
                                    <Play className="w-4 h-4" />
                                    Start
                                </Button>
                            ) : campaign.status === "running" ? (
                                <>
                                    <Button variant="outline" onClick={handlePause} disabled={actionLoading}>
                                        <Pause className="w-4 h-4" />
                                        Pause
                                    </Button>
                                    <Button variant="outline" onClick={handleStop} disabled={actionLoading}>
                                        <Square className="w-4 h-4" />
                                        Stop
                                    </Button>
                                </>
                            ) : null}
                        </div>
                    </motion.div>

                    {/* Stats */}
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: 0.1 }}
                            className="content-card"
                        >
                            <div className="flex items-center gap-3">
                                <div className="p-2 bg-muted/30 rounded-lg">
                                    <Users className="w-5 h-5 text-foreground" />
                                </div>
                                <div>
                                    <p className="text-2xl font-bold text-foreground">{stats?.total_leads || 0}</p>
                                    <p className="text-sm text-muted-foreground">Total Leads</p>
                                </div>
                            </div>
                        </motion.div>
                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: 0.15 }}
                            className="content-card"
                        >
                            <div className="flex items-center gap-3">
                                <div className="p-2 bg-muted/30 rounded-lg">
                                    <Phone className="w-5 h-5 text-foreground" />
                                </div>
                                <div>
                                    <p className="text-2xl font-bold text-foreground">
                                        {Object.values(stats?.call_outcome_counts || {}).reduce((a, b) => a + b, 0)}
                                    </p>
                                    <p className="text-sm text-muted-foreground">Calls Made</p>
                                </div>
                            </div>
                        </motion.div>
                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: 0.2 }}
                            className="content-card"
                        >
                            <div className="flex items-center gap-3">
                                <div className="p-2 bg-emerald-500/20 rounded-lg">
                                    <CheckCircle className="w-5 h-5 text-emerald-400" />
                                </div>
                                <div>
                                    <p className="text-2xl font-bold text-foreground">{stats?.goals_achieved || 0}</p>
                                    <p className="text-sm text-muted-foreground">Goals Achieved</p>
                                </div>
                            </div>
                        </motion.div>
                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: 0.25 }}
                            className="content-card"
                        >
                            <div className="flex items-center gap-3">
                                <div className="p-2 bg-red-500/20 rounded-lg">
                                    <XCircle className="w-5 h-5 text-red-400" />
                                </div>
                                <div>
                                    <p className="text-2xl font-bold text-foreground">
                                        {stats?.call_outcome_counts?.["failed"] || 0}
                                    </p>
                                    <p className="text-sm text-muted-foreground">Failed</p>
                                </div>
                            </div>
                        </motion.div>
                    </div>

                    {/* Contacts */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.3 }}
                        className="content-card"
                    >
                        <div className="flex items-center justify-between mb-4">
                            <h3 className="text-lg font-semibold text-foreground">Contacts</h3>
                            <Button size="sm" onClick={() => setShowAddContact(true)}>
                                <Plus className="w-4 h-4" />
                                Add Contact
                            </Button>
                        </div>

                        {showAddContact && (
                            <form onSubmit={handleAddContact} className="mb-6 p-4 bg-muted/30 rounded-lg border border-border">
                                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
                                    <div>
                                        <Label htmlFor="phone">Phone Number</Label>
                                        <Input
                                            id="phone"
                                            value={contactForm.phone_number}
                                            onChange={(e) => setContactForm((prev) => ({ ...prev, phone_number: e.target.value }))}
                                            placeholder="+1234567890"
                                            required
                                        />
                                    </div>
                                    <div>
                                        <Label htmlFor="first_name">First Name</Label>
                                        <Input
                                            id="first_name"
                                            value={contactForm.first_name}
                                            onChange={(e) => setContactForm((prev) => ({ ...prev, first_name: e.target.value }))}
                                            placeholder="John"
                                        />
                                    </div>
                                    <div>
                                        <Label htmlFor="last_name">Last Name</Label>
                                        <Input
                                            id="last_name"
                                            value={contactForm.last_name}
                                            onChange={(e) => setContactForm((prev) => ({ ...prev, last_name: e.target.value }))}
                                            placeholder="Doe"
                                        />
                                    </div>
                                    <div>
                                        <Label htmlFor="email">Email</Label>
                                        <Input
                                            id="email"
                                            type="email"
                                            value={contactForm.email}
                                            onChange={(e) => setContactForm((prev) => ({ ...prev, email: e.target.value }))}
                                            placeholder="john@example.com"
                                        />
                                    </div>
                                </div>
                                <div className="flex gap-2">
                                    <Button type="submit" size="sm" disabled={addingContact}>
                                        {addingContact ? <Loader2 className="w-4 h-4 animate-spin" /> : "Add"}
                                    </Button>
                                    <Button type="button" variant="outline" size="sm" onClick={() => setShowAddContact(false)}>
                                        Cancel
                                    </Button>
                                </div>
                            </form>
                        )}

                        {contacts.length === 0 ? (
                            <div className="text-center py-8 text-muted-foreground">
                                No contacts yet. Add contacts to start your campaign.
                            </div>
                        ) : (
                            <div className="overflow-x-auto">
                                <table className="w-full">
                                    <thead className="border-b border-border">
                                        <tr>
                                            <th className="px-4 py-2 text-left text-xs font-medium text-muted-foreground uppercase whitespace-nowrap">Phone</th>
                                            <th className="px-4 py-2 text-left text-xs font-medium text-muted-foreground uppercase whitespace-nowrap">Name</th>
                                            <th className="px-4 py-2 text-left text-xs font-medium text-muted-foreground uppercase whitespace-nowrap">Status</th>
                                            <th className="px-4 py-2 text-right text-xs font-medium text-muted-foreground uppercase whitespace-nowrap">Attempts</th>
                                        </tr>
                                    </thead>
                                    <tbody className="divide-y divide-border/60">
                                        {contacts.map((contact) => (
                                            <tr key={contact.id} className="hover:bg-muted/30 transition-colors">
                                                <td className="px-4 py-3 text-sm text-foreground tabular-nums whitespace-nowrap">{contact.phone_number}</td>
                                                <td className="px-4 py-3 text-sm text-muted-foreground whitespace-nowrap">
                                                    {contact.first_name || contact.last_name
                                                        ? `${contact.first_name || ""} ${contact.last_name || ""}`.trim()
                                                        : "--"}
                                                </td>
                                                <td className="px-4 py-3 text-sm whitespace-nowrap">
                                                    <span className={`px-2 py-0.5 text-xs font-medium rounded-full ${getContactStatusStyle(contact.last_call_result)}`}>
                                                        {contact.last_call_result}
                                                    </span>
                                                </td>
                                                <td className="px-4 py-3 text-right text-sm text-muted-foreground tabular-nums whitespace-nowrap">{contact.call_attempts}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </motion.div>
                </div>
            ) : null}
        </DashboardLayout>
    );
}
