"use client";

import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { RouteGuard } from "@/components/guards/route-guard";
import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import Link from "next/link";

export default function WhiteLabelDashboardPage() {
    return (
        <DashboardLayout title="White-label Dashboard" description="Manage your white-label workspace.">
            <RouteGuard title="White-label Dashboard" requiredRoles={["white_label_admin"]} unauthorizedRedirectTo="/403">
                <div className="space-y-4">
                    <PartnersAdmin />
                </div>
            </RouteGuard>
        </DashboardLayout>
    );
}

type PartnerRecord = {
    partner_id: string;
    display_name: string;
    allow_transfer: boolean;
    created_at: string;
    admin_email: string;
    admin_token: string;
};

function normalizePartnerIdInput(raw: string) {
    return raw
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9-]/g, "-")
        .replace(/-+/g, "-")
        .replace(/^-+|-+$/g, "");
}

function PartnersAdmin() {
    const [loading, setLoading] = useState(true);
    const [items, setItems] = useState<PartnerRecord[]>([]);
    const [error, setError] = useState<string | null>(null);

    const [partnerId, setPartnerId] = useState("");
    const [displayName, setDisplayName] = useState("");
    const [adminEmail, setAdminEmail] = useState("");
    const [allowTransfer, setAllowTransfer] = useState(true);
    const [creating, setCreating] = useState(false);
    const [created, setCreated] = useState<PartnerRecord | null>(null);

    const normalizedPartnerId = useMemo(() => normalizePartnerIdInput(partnerId), [partnerId]);

    const canCreate = useMemo(() => {
        if (creating) return false;
        if (!normalizedPartnerId) return false;
        if (!displayName.trim()) return false;
        if (!adminEmail.trim() || !/@/.test(adminEmail)) return false;
        return true;
    }, [adminEmail, creating, displayName, normalizedPartnerId]);

    const load = async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await fetch("/api/v1/white-label/partners", { method: "GET", headers: { accept: "application/json" }, cache: "no-store" });
            const data = (await res.json().catch(() => null)) as { items?: PartnerRecord[]; detail?: string } | null;
            if (!res.ok) throw new Error(typeof data?.detail === "string" ? data.detail : "Failed to load partners.");
            const next = Array.isArray(data?.items) ? data.items : [];
            setItems(next);
        } catch (e) {
            setError(e instanceof Error ? e.message : "Failed to load partners.");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        void load();
    }, []);

    const createPartner = async () => {
        if (!canCreate) return;
        setCreating(true);
        setError(null);
        setCreated(null);
        try {
            const res = await fetch("/api/v1/white-label/partners", {
                method: "POST",
                headers: { "content-type": "application/json", accept: "application/json" },
                body: JSON.stringify({
                    partner_id: normalizedPartnerId,
                    display_name: displayName.trim(),
                    allow_transfer: Boolean(allowTransfer),
                    admin_email: adminEmail.trim(),
                }),
            });
            const data = (await res.json().catch(() => null)) as PartnerRecord | { detail?: string } | null;
            if (!res.ok) throw new Error(typeof (data as { detail?: string } | null)?.detail === "string" ? (data as { detail: string }).detail : "Failed to create partner.");
            const rec = data as PartnerRecord;
            setCreated(rec);
            setPartnerId("");
            setDisplayName("");
            setAdminEmail("");
            setAllowTransfer(true);
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : "Failed to create partner.");
        } finally {
            setCreating(false);
        }
    };

    return (
        <div className="space-y-6">
            {error ? (
                <div className="rounded-2xl border border-red-500/20 bg-red-500/10 px-4 py-3 text-sm text-red-100" role="alert">
                    {error}
                </div>
            ) : null}

            {created ? (
                <div className="rounded-2xl border border-emerald-500/20 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-100" role="status">
                    <div className="font-semibold">Partner created</div>
                    <div className="mt-1 break-words">
                        Partner ID: <span className="font-semibold">{created.partner_id}</span> · Admin token:{" "}
                        <span className="font-semibold">{created.admin_token}</span>
                    </div>
                </div>
            ) : null}

            <div className="content-card space-y-4">
                <div className="text-sm font-semibold text-foreground">Partner Management</div>
                <div className="text-sm text-muted-foreground">Create partners and generate partner-admin credentials for testing and demos.</div>

                <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                    <div className="space-y-2">
                        <Label htmlFor="partnerId">Partner ID</Label>
                        <Input id="partnerId" value={partnerId} onChange={(e) => setPartnerId(e.target.value)} placeholder="example-partner" />
                        <div className="text-xs text-muted-foreground">
                            Normalized: <span className="font-semibold tabular-nums text-foreground">{normalizedPartnerId || "—"}</span>
                        </div>
                    </div>

                    <div className="space-y-2">
                        <Label htmlFor="displayName">Display Name</Label>
                        <Input id="displayName" value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="Example Partner" />
                    </div>

                    <div className="space-y-2">
                        <Label htmlFor="adminEmail">Partner Admin Email</Label>
                        <Input id="adminEmail" value={adminEmail} onChange={(e) => setAdminEmail(e.target.value)} placeholder="admin@example.com" type="email" />
                    </div>

                    <div className="flex items-center justify-between gap-3 rounded-2xl border border-border bg-background/60 px-4 py-3">
                        <div className="min-w-0">
                            <div className="text-sm font-semibold text-foreground">Call Transfer Enabled</div>
                            <div className="text-xs text-muted-foreground">Controls the tenant transfer toggle in Agent Settings.</div>
                        </div>
                        <Switch checked={allowTransfer} onCheckedChange={setAllowTransfer} ariaLabel="Toggle call transfer for this partner" />
                    </div>
                </div>

                <div className="flex flex-wrap gap-2">
                    <Button type="button" onClick={createPartner} disabled={!canCreate}>
                        {creating ? "Creating…" : "Create Partner"}
                    </Button>
                    <Button type="button" variant="secondary" onClick={() => void load()} disabled={loading}>
                        Refresh
                    </Button>
                </div>
            </div>

            <div className="content-card space-y-3">
                <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                        <div className="text-sm font-semibold text-foreground">Partners</div>
                        <div className="mt-1 text-sm text-muted-foreground">Use the generated token to sign in as that partner admin.</div>
                    </div>
                    <div className="text-xs text-muted-foreground">{loading ? "Loading…" : `${items.length} partners`}</div>
                </div>

                <div className="overflow-hidden rounded-2xl border border-border bg-background/60">
                    <div className="hidden grid-cols-[160px_minmax(0,1fr)_220px_120px_180px] gap-2 border-b border-border bg-background/70 px-4 py-3 text-xs font-semibold text-muted-foreground md:grid">
                        <div>Partner ID</div>
                        <div>Display Name</div>
                        <div>Admin Token</div>
                        <div>Transfer</div>
                        <div className="text-right">Actions</div>
                    </div>
                    <div className="divide-y divide-border">
                        {items.map((p) => (
                            <div
                                key={p.partner_id}
                                className="grid grid-cols-1 gap-3 px-4 py-4 text-sm text-foreground md:grid-cols-[160px_minmax(0,1fr)_220px_120px_180px]"
                            >
                                <div className="font-semibold tabular-nums">{p.partner_id}</div>
                                <div className="min-w-0 truncate">{p.display_name}</div>
                                <div className="font-mono text-xs break-all">{p.admin_token}</div>
                                <div className="text-xs font-semibold">{p.allow_transfer ? "On" : "Off"}</div>
                                <div className="flex flex-wrap items-center justify-start gap-2 md:justify-end">
                                    <Button type="button" size="sm" variant="secondary" asChild>
                                        <Link href={`/white-label/${encodeURIComponent(p.partner_id)}/preview`}>Preview</Link>
                                    </Button>
                                    <Button type="button" size="sm" variant="outline" asChild>
                                        <Link href={`/white-label/${encodeURIComponent(p.partner_id)}/tenants`}>Tenants</Link>
                                    </Button>
                                </div>
                            </div>
                        ))}
                        {!loading && items.length === 0 ? (
                            <div className="px-6 py-10 text-center text-sm text-muted-foreground">No partners found.</div>
                        ) : null}
                    </div>
                </div>
            </div>
        </div>
    );
}
