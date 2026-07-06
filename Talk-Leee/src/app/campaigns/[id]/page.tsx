"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { dashboardApi, Campaign, Contact, ContactList, MinutesStatus } from "@/lib/dashboard-api";
import { SmartCsvImport } from "@/components/campaigns/smart-csv-import";
import { ContactLists, ActiveContactsSummary } from "@/components/campaigns/contact-lists";
import { ScriptCard } from "@/components/campaigns/script-card";
import { LiveCallsPanel } from "@/components/campaigns/live-calls-panel";
import { CallIssuesPanel } from "@/components/campaigns/call-issues-panel";
import { KnowledgePanel } from "@/components/campaigns/knowledge-panel";
import { TestAgentButton } from "@/components/campaigns/test-agent-button";
import { Modal } from "@/components/ui/modal";
import { checkCallingWindow } from "@/lib/calling-window";
import {
    ArrowLeft,
    Play,
    Pause,
    Square,
    Plus,
    Upload,
    Pencil,
    Users,
    Phone,
    CheckCircle,
    XCircle,
    Loader2,
    Trash2,
    Search,
    Clock,
    AlertTriangle,
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
        qualified_leads?: number;
        goals_achieved: number;
        job_status_counts: Record<string, number>;
        call_outcome_counts: Record<string, number>;
    } | null>(null);
    const [loading, setLoading] = useState(true);
    const [actionLoading, setActionLoading] = useState(false);
    const [error, setError] = useState("");
    const [minutes, setMinutes] = useState<MinutesStatus | null>(null);

    // Out of plan minutes ⇒ the backend will 402 a Start, so we disable the
    // button up front and explain why. `unlimited` plans are never blocked.
    const outOfMinutes = !!minutes && !minutes.unlimited && minutes.exhausted;

    // CSV Upload (smart import modal)
    const [csvModalOpen, setCsvModalOpen] = useState(false);

    // Contact lists — the grouped-upload cards + the active-contacts summary.
    // `listsRefreshToken` re-fetches the lists row after an import; `lists`
    // mirrors what the row loaded so the Start summary can read active counts.
    const [listsRefreshToken, setListsRefreshToken] = useState(0);
    const [lists, setLists] = useState<ContactList[]>([]);
    const listsSectionRef = useRef<HTMLDivElement | null>(null);

    // First-speaker selector shown after the Start button is pressed
    const [startModalOpen, setStartModalOpen] = useState(false);
    const [firstSpeaker, setFirstSpeaker] = useState<"agent" | "user">("agent");

    // Add contact form
    const [showAddContact, setShowAddContact] = useState(false);
    const [contactForm, setContactForm] = useState({
        phone_number: "",
        first_name: "",
        last_name: "",
        email: "",
    });
    const [addingContact, setAddingContact] = useState(false);
    const [deletingContactId, setDeletingContactId] = useState<string | null>(null);
    const [editingContact, setEditingContact] = useState<Contact | null>(null);
    const [contactSearch, setContactSearch] = useState("");
    const [contactFilter, setContactFilter] = useState<"all" | "leads">("all");

    const filteredContacts = contacts.filter((c) => {
        if (contactFilter === "leads" && !c.is_lead) return false;
        const term = contactSearch.trim().toLowerCase();
        if (!term) return true;
        const name = `${c.first_name || ""} ${c.last_name || ""}`.toLowerCase();
        return (
            (c.phone_number || "").toLowerCase().includes(term) ||
            name.includes(term) ||
            (c.email || "").toLowerCase().includes(term)
        );
    });

    const loadData = useCallback(async () => {
        try {
            setLoading(true);
            const [campaignData, contactsData, statsData, minutesData] = await Promise.all([
                dashboardApi.getCampaign(campaignId),
                dashboardApi.listContacts(campaignId),
                dashboardApi.getCampaignStats(campaignId),
                // Best-effort — a quota hiccup must not blank the whole page.
                dashboardApi.getMinutesStatus().catch(() => null),
            ]);
            setCampaign(campaignData.campaign);
            setContacts(contactsData.items);
            setStats(statsData);
            setMinutes(minutesData);
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

    function handleStartClick() {
        // Out of plan minutes — don't even open the modal; the backend
        // would 402 anyway. The banner above the button explains why.
        if (outOfMinutes) {
            alert(
                `You're out of plan minutes (${minutes?.used_minutes}/${minutes?.allocated} used this month). ` +
                "Add minutes or upgrade your plan to start campaigns.",
            );
            return;
        }
        // Default back to "agent" every time so a previous choice doesn't
        // silently carry over into the next Start.
        setFirstSpeaker("agent");
        setStartModalOpen(true);
    }

    async function handleConfirmStart() {
        try {
            setActionLoading(true);
            setStartModalOpen(false);
            await dashboardApi.startCampaign(campaignId, { first_speaker: firstSpeaker });
            await loadData();
        } catch (err) {
            // The backend's out-of-minutes 402 ships a structured detail
            // ({ message, ... }) the http client exposes as `.details`.
            const detail = (err as { details?: { message?: string } })?.details;
            const msg =
                (detail && typeof detail.message === "string" && detail.message) ||
                (err instanceof Error ? err.message : "Failed to start campaign");
            alert(msg);
            // Refresh quota so the button/banner reflect reality after a 402.
            void loadData();
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
            if (editingContact) {
                await dashboardApi.updateContact(campaignId, editingContact.id, contactForm);
            } else {
                await dashboardApi.addContact(campaignId, contactForm);
            }
            setContactForm({ phone_number: "", first_name: "", last_name: "", email: "" });
            setShowAddContact(false);
            setEditingContact(null);
            await loadData();
        } catch (err) {
            alert(
                err instanceof Error
                    ? err.message
                    : `Failed to ${editingContact ? "update" : "add"} contact`,
            );
        } finally {
            setAddingContact(false);
        }
    }

    function startEditContact(contact: Contact) {
        setEditingContact(contact);
        setContactForm({
            phone_number: contact.phone_number || "",
            first_name: contact.first_name || "",
            last_name: contact.last_name || "",
            email: contact.email || "",
        });
        setShowAddContact(true);
    }

    async function handleDeleteContact(contactId: string, phone: string) {
        if (!confirm(`Remove ${phone} from this campaign? It will no longer be dialed.`)) return;
        try {
            setDeletingContactId(contactId);
            await dashboardApi.deleteContact(campaignId, contactId);
            // Optimistically drop the row; reload stats in the background.
            setContacts((prev) => prev.filter((c) => c.id !== contactId));
            loadData().catch(() => {});
        } catch (err) {
            alert(err instanceof Error ? err.message : "Failed to delete contact");
        } finally {
            setDeletingContactId(null);
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

                        <div className="flex items-center gap-2">
                            {/* Live minutes remaining — visible right next to the
                                control that consumes them. Amber when low, red at 0. */}
                            {minutes && !minutes.unlimited && (
                                <span
                                    title={`${minutes.used_minutes} of ${minutes.allocated} plan minutes used this month`}
                                    className={`hidden sm:inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-medium border ${
                                        outOfMinutes
                                            ? "bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/20"
                                            : minutes.remaining_minutes <= 30
                                              ? "bg-yellow-500/10 text-yellow-700 dark:text-yellow-400 border-yellow-500/20"
                                              : "bg-muted/40 text-muted-foreground border-border"
                                    }`}
                                >
                                    <Clock className="w-3.5 h-3.5" />
                                    {minutes.remaining_minutes} min left
                                </span>
                            )}
                            <TestAgentButton campaignId={campaignId} disabled={actionLoading} />
                            <Button variant="outline" onClick={() => router.push(`/campaigns/${campaignId}/edit`)} disabled={actionLoading}>
                                <Pencil className="w-4 h-4" />
                                Edit
                            </Button>
                            {campaign.status === "draft" || campaign.status === "paused" || campaign.status === "stopped" ? (
                                <Button
                                    onClick={handleStartClick}
                                    disabled={actionLoading || outOfMinutes}
                                    title={outOfMinutes ? "Out of plan minutes — add minutes to start" : undefined}
                                >
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

                    {/* Out-of-minutes banner — the campaign can't be started
                        until the tenant has plan minutes again. */}
                    {outOfMinutes && (
                        <div className="content-card border-red-500/30 bg-red-500/5 flex items-start gap-3">
                            <AlertTriangle className="w-5 h-5 text-red-500 shrink-0 mt-0.5" />
                            <div className="text-sm">
                                <p className="font-medium text-red-600 dark:text-red-400">
                                    Out of plan minutes
                                </p>
                                <p className="text-muted-foreground mt-0.5">
                                    You&apos;ve used {minutes?.used_minutes} of your {minutes?.allocated} monthly
                                    minutes. Campaigns can&apos;t be started until you add minutes or upgrade your plan.
                                </p>
                            </div>
                        </div>
                    )}

                    {/* Active-contacts summary — which lists get dialed on Start.
                        "View more" scrolls to the full lists row below. */}
                    <ActiveContactsSummary
                        lists={lists}
                        onViewMore={() => listsSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })}
                    />

                    {/* Stats */}
                    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
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
                            transition={{ delay: 0.12 }}
                            className="content-card"
                        >
                            <div className="flex items-center gap-3">
                                <div className="p-2 rounded-lg bg-emerald-500/10">
                                    <Users className="w-5 h-5 text-emerald-600 dark:text-emerald-400" />
                                </div>
                                <div>
                                    <p className="text-2xl font-bold text-emerald-700 dark:text-emerald-400">{stats?.qualified_leads ?? 0}</p>
                                    <p className="text-sm text-muted-foreground">Qualified Leads</p>
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

                    {/* Live calls — Track B */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.3 }}
                    >
                        <LiveCallsPanel campaignId={campaignId} />
                    </motion.div>

                    {/* Call issues — why calls aren't going through (only
                        renders when there's something to surface). */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.35 }}
                    >
                        <CallIssuesPanel campaignId={campaignId} />
                    </motion.div>

                    {/* Contact lists row — toggle active/inactive + call a whole list. */}
                    <div ref={listsSectionRef}>
                        <ContactLists
                            campaignId={campaignId}
                            refreshToken={listsRefreshToken}
                            onListsLoaded={setLists}
                            onCallStarted={() => { void loadData(); }}
                        />
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
                            <div className="flex gap-2">
                                <Button size="sm" variant="outline" onClick={() => setCsvModalOpen(true)}>
                                    <Upload className="w-4 h-4" />
                                    Import CSV
                                </Button>
                                <Button size="sm" onClick={() => { setEditingContact(null); setContactForm({ phone_number: "", first_name: "", last_name: "", email: "" }); setShowAddContact(true); }}>
                                    <Plus className="w-4 h-4" />
                                    Add Contact
                                </Button>
                            </div>
                        </div>
                        <SmartCsvImport
                            open={csvModalOpen}
                            campaignId={campaignId}
                            onClose={() => setCsvModalOpen(false)}
                            onImported={() => { void loadData(); setListsRefreshToken((t) => t + 1); }}
                        />

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
                                        {addingContact ? <Loader2 className="w-4 h-4 animate-spin" /> : (editingContact ? "Save changes" : "Add")}
                                    </Button>
                                    <Button type="button" variant="outline" size="sm" onClick={() => { setShowAddContact(false); setEditingContact(null); }}>
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
                          <>
                            {/* Search + lead filter toolbar */}
                            <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                                <div className="relative w-full sm:max-w-xs">
                                    <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                                    <input
                                        type="text"
                                        value={contactSearch}
                                        onChange={(e) => setContactSearch(e.target.value)}
                                        placeholder="Search by phone, name or email…"
                                        className="w-full rounded-lg border border-border bg-background py-2 pl-9 pr-3 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring/40"
                                    />
                                </div>
                                <div className="flex items-center gap-1 rounded-lg border border-border bg-background p-1">
                                    {([
                                        ["all", "All"],
                                        ["leads", "Leads"],
                                    ] as const).map(([key, label]) => (
                                        <button
                                            key={key}
                                            type="button"
                                            onClick={() => setContactFilter(key)}
                                            className={`rounded-md px-3 py-1 text-xs font-semibold transition-colors duration-150 ease-out ${
                                                contactFilter === key
                                                    ? "bg-accent text-accent-foreground"
                                                    : "text-muted-foreground hover:text-foreground"
                                            }`}
                                        >
                                            {label}
                                        </button>
                                    ))}
                                </div>
                            </div>

                            {filteredContacts.length === 0 ? (
                              <div className="py-8 text-center text-muted-foreground">
                                No contacts match your search or filter.
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
                                            <th className="px-4 py-2 text-right text-xs font-medium text-muted-foreground uppercase whitespace-nowrap">Actions</th>
                                        </tr>
                                    </thead>
                                    <tbody className="divide-y divide-border/60">
                                        {filteredContacts.map((contact) => (
                                            <tr key={contact.id} className={`transition-colors hover:bg-muted/30 ${contact.is_lead ? "bg-green-500/5" : ""}`}>
                                                <td className="px-4 py-3 text-sm text-foreground tabular-nums whitespace-nowrap">{contact.phone_number}</td>
                                                <td className="px-4 py-3 text-sm text-muted-foreground whitespace-nowrap">
                                                    {contact.first_name || contact.last_name
                                                        ? `${contact.first_name || ""} ${contact.last_name || ""}`.trim()
                                                        : "--"}
                                                </td>
                                                <td className="px-4 py-3 text-sm whitespace-nowrap">
                                                    {contact.is_lead ? (
                                                        <div className="flex flex-col gap-0.5">
                                                            <span
                                                                className="w-fit rounded-full border border-emerald-500/20 bg-emerald-500/10 px-2 py-0.5 text-xs font-medium text-emerald-700 dark:text-emerald-400"
                                                                title={contact.follow_up_note ?? undefined}
                                                            >
                                                                Lead — follow up
                                                            </span>
                                                            {contact.follow_up_note ? (
                                                                <span className="max-w-[16rem] truncate text-xs text-muted-foreground" title={contact.follow_up_note}>
                                                                    {contact.follow_up_note}
                                                                </span>
                                                            ) : null}
                                                        </div>
                                                    ) : (
                                                        <span className={`px-2 py-0.5 text-xs font-medium rounded-full ${getContactStatusStyle(contact.last_call_result)}`}>
                                                            {contact.last_call_result}
                                                        </span>
                                                    )}
                                                </td>
                                                <td className="px-4 py-3 text-right text-sm text-muted-foreground tabular-nums whitespace-nowrap">{contact.call_attempts}</td>
                                                <td className="px-4 py-3 text-right whitespace-nowrap">
                                                    <div className="flex items-center justify-end gap-1">
                                                        <button
                                                            type="button"
                                                            onClick={() => startEditContact(contact)}
                                                            aria-label={`Edit ${contact.phone_number}`}
                                                            title="Edit contact"
                                                            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                                                        >
                                                            <Pencil className="h-3.5 w-3.5" />
                                                        </button>
                                                        <button
                                                            type="button"
                                                            onClick={() => handleDeleteContact(contact.id, contact.phone_number)}
                                                            disabled={deletingContactId === contact.id}
                                                            aria-label={`Delete ${contact.phone_number}`}
                                                            title="Remove contact"
                                                            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive disabled:opacity-50"
                                                        >
                                                            {deletingContactId === contact.id ? (
                                                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                                            ) : (
                                                                <Trash2 className="h-3.5 w-3.5" />
                                                            )}
                                                        </button>
                                                    </div>
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                              </div>
                            )}
                          </>
                        )}
                    </motion.div>

                    {/* Knowledge base — vectorless-RAG tree the agent answers from */}
                    <KnowledgePanel campaignId={campaignId} />

                    {/* Script Card — transcripts of every call with timestamps */}
                    <ScriptCard campaignId={campaignId} />
                </div>
            ) : null}

            <Modal
                open={startModalOpen}
                onOpenChange={setStartModalOpen}
                title="Who speaks first?"
                description="Pick who opens the conversation when the callee answers. The call pipeline pre-warms either way — this only controls whether the agent speaks an opening line or waits for the caller."
                size="sm"
                footer={
                    <div className="flex justify-end gap-2">
                        <Button
                            variant="outline"
                            onClick={() => setStartModalOpen(false)}
                            disabled={actionLoading}
                        >
                            Cancel
                        </Button>
                        <Button onClick={handleConfirmStart} disabled={actionLoading}>
                            {actionLoading ? (
                                <Loader2 className="w-4 h-4 animate-spin" />
                            ) : (
                                <Play className="w-4 h-4" />
                            )}
                            Start campaign
                        </Button>
                    </div>
                }
            >
                <div className="space-y-2">
                    {(() => {
                        const sched = campaign?.calling_config;
                        if (!sched) return null;
                        const win = checkCallingWindow(sched);
                        if (!win.outside) return null;
                        return (
                            <div className="mb-2 flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300">
                                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                                <span>
                                    {win.message}{" "}
                                    {sched.ignore_schedule
                                        ? "“Call anytime” is on, so calls will go out now."
                                        : "Calls will wait until the window opens. You can start anyway — turn on “call anytime” in the campaign’s calling hours to dial immediately."}
                                </span>
                            </div>
                        );
                    })()}
                    <label
                        className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors ${
                            firstSpeaker === "agent"
                                ? "border-primary bg-primary/5"
                                : "border-border hover:bg-muted/40"
                        }`}
                    >
                        <input
                            type="radio"
                            name="first-speaker"
                            value="agent"
                            checked={firstSpeaker === "agent"}
                            onChange={() => setFirstSpeaker("agent")}
                            className="mt-1"
                        />
                        <div>
                            <div className="text-sm font-medium text-foreground">AI agent speaks first</div>
                            <div className="text-xs text-muted-foreground">
                                Agent plays a pre-synthesized greeting the instant the callee picks up. Best for cold outreach.
                            </div>
                        </div>
                    </label>

                    <label
                        className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors ${
                            firstSpeaker === "user"
                                ? "border-primary bg-primary/5"
                                : "border-border hover:bg-muted/40"
                        }`}
                    >
                        <input
                            type="radio"
                            name="first-speaker"
                            value="user"
                            checked={firstSpeaker === "user"}
                            onChange={() => setFirstSpeaker("user")}
                            className="mt-1"
                        />
                        <div>
                            <div className="text-sm font-medium text-foreground">Caller speaks first</div>
                            <div className="text-xs text-muted-foreground">
                                Agent waits for the callee to say &ldquo;hello&rdquo; before responding. Feels more natural on warm lists.
                            </div>
                        </div>
                    </label>
                </div>
            </Modal>
        </DashboardLayout>
    );
}
