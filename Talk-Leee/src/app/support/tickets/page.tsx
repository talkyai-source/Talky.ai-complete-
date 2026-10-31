"use client";

import Link from "next/link";
import { useState } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ErrorState } from "@/components/states/page-states";
import { SupportPageGate } from "@/components/support/support-unavailable";
import { TicketList } from "@/components/support/ticket-list";
import { supportChatStore } from "@/lib/support-chat-store";
import { TICKET_TABS, useTicketTab, type TicketTab } from "@/lib/support-ticket-queries";

function TabBody({ tab, scope }: { tab: TicketTab; scope: "mine" | "team" }) {
    const list = useTicketTab(tab, scope, true);
    if (list.loading && !list.items.length) return <p className="text-sm text-muted-foreground">Loading tickets…</p>;
    if (list.error && !list.items.length) return <ErrorState title="Tickets could not be loaded" message="Retry in a moment." onRetry={list.refetch} />;
    return (
        <div className="flex flex-col gap-3">
            <TicketList items={list.items} emptyText={scope === "team" ? "No team tickets here." : "No tickets here."} />
            {list.hasMore ? (
                <div><Button type="button" variant="outline" size="sm" disabled={list.loading} onClick={list.loadMore}>Load more</Button></div>
            ) : null}
        </div>
    );
}

function Tickets({ canReadTeam }: { canReadTeam: boolean }) {
    const [tab, setTab] = useState<TicketTab>("open");
    const [team, setTeam] = useState(false);
    const scope = canReadTeam && team ? "team" : "mine";
    return (
        <div className="flex flex-col gap-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
                {canReadTeam ? (
                    <div className="flex items-center gap-2 text-sm">
                        <span id="support-scope-label">{team ? "Team tickets" : "My tickets"}</span>
                        <Switch checked={team} onCheckedChange={setTeam} ariaLabel="Show the team's tickets" />
                    </div>
                ) : <span />}
                <div className="flex flex-wrap gap-2">
                    <Button type="button" size="sm" onClick={() => supportChatStore.setOpen(true)}>Ask the assistant</Button>
                    <Button asChild variant="outline" size="sm"><Link href="/support">Support home</Link></Button>
                </div>
            </div>
            <Tabs value={tab} onValueChange={(next) => setTab(next as TicketTab)}>
                <TabsList className="h-auto flex-wrap justify-start" aria-label="Ticket status">
                    {TICKET_TABS.map((entry) => <TabsTrigger key={entry.id} value={entry.id}>{entry.label}</TabsTrigger>)}
                </TabsList>
                {TICKET_TABS.map((entry) => (
                    <TabsContent key={entry.id} value={entry.id}>
                        {entry.id === tab ? <TabBody tab={entry.id} scope={scope} /> : null}
                    </TabsContent>
                ))}
            </Tabs>
        </div>
    );
}

export default function SupportTicketsPage() {
    return (
        <DashboardLayout title="Support tickets" description="Every ticket you opened, and where it stands.">
            <div className="mx-auto w-full max-w-5xl">
                <SupportPageGate title="Loading tickets">{(gate) => <Tickets canReadTeam={gate.canReadTeam} />}</SupportPageGate>
            </div>
        </DashboardLayout>
    );
}
