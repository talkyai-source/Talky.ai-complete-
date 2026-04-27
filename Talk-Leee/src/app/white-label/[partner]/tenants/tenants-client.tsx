"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { StatusPill } from "@/components/ui/status-pill";

type TenantStatus = "active" | "suspended";

type Tenant = {
    id: string;
    tenantName: string;
    allocatedMinutes: number;
    subConcurrency: number;
    status: TenantStatus;
    createdAt: number;
    updatedAt: number;
};

type PartnerLimits = {
    maxMinutes: number;
    maxConcurrency: number;
};

type TenantDraft = {
    tenantName: string;
    allocatedMinutes: string;
    subConcurrency: string;
};

function stableNumberFromString(input: string) {
    let hash = 0;
    for (let i = 0; i < input.length; i++) {
        hash = (hash * 31 + input.charCodeAt(i)) >>> 0;
    }
    return hash;
}

function createId() {
    const c = globalThis.crypto;
    if (c && "randomUUID" in c && typeof c.randomUUID === "function") return c.randomUUID();
    return `t_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
}

function tenantStatusPill(status: TenantStatus) {
    if (status === "active") {
        return <StatusPill state="connected" label="Active" tooltip="This tenant can place calls and consume minutes." size="sm" />;
    }
    return <StatusPill state="expired" label="Suspended" tooltip="This tenant is suspended and cannot place calls or consume minutes." size="sm" />;
}

function normalizePartnerId(partnerId: string) {
    return partnerId.trim().toLowerCase();
}

function getPartnerLimits(partnerId: string): PartnerLimits {
    const key = normalizePartnerId(partnerId);

    if (key === "acme") return { maxMinutes: 10_000, maxConcurrency: 10 };
    if (key === "zen") return { maxMinutes: 7_500, maxConcurrency: 8 };

    const seed = stableNumberFromString(key || "default");
    const maxMinutes = 5_000 + (seed % 20_001);
    const maxConcurrency = 5 + (seed % 21);
    return { maxMinutes, maxConcurrency };
}

function initialTenantsForPartner(partnerId: string): Tenant[] {
    const key = normalizePartnerId(partnerId);
    const now = Date.now();

    if (key === "acme") {
        return [
            {
                id: "acme-clinic-ai",
                tenantName: "Clinic AI",
                allocatedMinutes: 1000,
                subConcurrency: 3,
                status: "active",
                createdAt: now - 14 * 24 * 60 * 60 * 1000,
                updatedAt: now - 2 * 24 * 60 * 60 * 1000,
            },
            {
                id: "acme-dental-bot",
                tenantName: "Dental Bot",
                allocatedMinutes: 800,
                subConcurrency: 2,
                status: "suspended",
                createdAt: now - 8 * 24 * 60 * 60 * 1000,
                updatedAt: now - 3 * 24 * 60 * 60 * 1000,
            },
        ];
    }

    if (key === "zen") {
        return [
            {
                id: "zen-salon-assistant",
                tenantName: "Salon Assistant",
                allocatedMinutes: 600,
                subConcurrency: 2,
                status: "active",
                createdAt: now - 10 * 24 * 60 * 60 * 1000,
                updatedAt: now - 1 * 24 * 60 * 60 * 1000,
            },
        ];
    }

    return [];
}

function storageKeyForPartner(partnerId: string) {
    return `white_label:${normalizePartnerId(partnerId)}:tenants:v1`;
}

function parseNonNegativeInt(raw: string): number | null {
    const v = raw.trim();
    if (v.length === 0) return null;
    const n = Number(v);
    if (!Number.isFinite(n)) return null;
    if (!Number.isInteger(n)) return null;
    if (n < 0) return null;
    return n;
}

function parsePositiveInt(raw: string): number | null {
    const n = parseNonNegativeInt(raw);
    if (n === null) return null;
    if (n < 1) return null;
    return n;
}

function totalsForTenants(items: Tenant[]) {
    let minutes = 0;
    let concurrency = 0;
    for (const t of items) {
        minutes += t.allocatedMinutes;
        concurrency += t.subConcurrency;
    }
    return { minutes, concurrency };
}

function clampMinZero(n: number) {
    return n < 0 ? 0 : n;
}

export function PartnerTenantsClient({ partnerId, partnerDisplayName }: { partnerId: string; partnerDisplayName: string }) {
    const limits = useMemo(() => getPartnerLimits(partnerId), [partnerId]);
    const [tenants, setTenants] = useState<Tenant[]>(() => initialTenantsForPartner(partnerId));
    const [loaded, setLoaded] = useState(false);

    const [formOpen, setFormOpen] = useState(false);
    const [editing, setEditing] = useState<Tenant | null>(null);

    const [confirmSuspendId, setConfirmSuspendId] = useState<string | null>(null);

    const initialFocusRef = useRef<HTMLInputElement | null>(null);

    useEffect(() => {
        const key = storageKeyForPartner(partnerId);
        setLoaded(false);
        try {
            const raw = globalThis.localStorage?.getItem(key) ?? null;
            if (raw) {
                const parsed = JSON.parse(raw) as unknown;
                if (Array.isArray(parsed)) {
                    const safe: Tenant[] = parsed
                        .map((t): Tenant | null => {
                            if (!t || typeof t !== "object") return null;
                            const o = t as Partial<Tenant>;
                            if (typeof o.id !== "string" || o.id.trim().length === 0) return null;
                            if (typeof o.tenantName !== "string" || o.tenantName.trim().length === 0) return null;
                            if (typeof o.allocatedMinutes !== "number" || !Number.isFinite(o.allocatedMinutes)) return null;
                            if (typeof o.subConcurrency !== "number" || !Number.isFinite(o.subConcurrency)) return null;
                            if (o.status !== "active" && o.status !== "suspended") return null;
                            const createdAt = typeof o.createdAt === "number" ? o.createdAt : Date.now();
                            const updatedAt = typeof o.updatedAt === "number" ? o.updatedAt : createdAt;
                            return {
                                id: o.id,
                                tenantName: o.tenantName,
                                allocatedMinutes: Math.max(0, Math.floor(o.allocatedMinutes)),
                                subConcurrency: Math.max(0, Math.floor(o.subConcurrency)),
                                status: o.status,
                                createdAt,
                                updatedAt,
                            };
                        })
                        .filter(Boolean) as Tenant[];
                    setTenants(safe);
                } else {
                    setTenants(initialTenantsForPartner(partnerId));
                }
            } else {
                setTenants(initialTenantsForPartner(partnerId));
            }
        } catch {
            setTenants(initialTenantsForPartner(partnerId));
        } finally {
            setLoaded(true);
        }
    }, [partnerId]);

    useEffect(() => {
        if (!loaded) return;
        const key = storageKeyForPartner(partnerId);
        try {
            globalThis.localStorage?.setItem(key, JSON.stringify(tenants));
        } catch {
        }
    }, [loaded, partnerId, tenants]);

    const totals = useMemo(() => totalsForTenants(tenants), [tenants]);

    const remainingMinutes = clampMinZero(limits.maxMinutes - totals.minutes);
    const remainingConcurrency = clampMinZero(limits.maxConcurrency - totals.concurrency);

    const openCreate = () => {
        setEditing(null);
        setFormOpen(true);
    };

    const openEdit = (tenant: Tenant) => {
        setEditing(tenant);
        setFormOpen(true);
    };

    const upsertTenant = (draft: { tenantName: string; allocatedMinutes: number; subConcurrency: number }) => {
        const now = Date.now();
        if (!editing) {
            const next: Tenant = {
                id: createId(),
                tenantName: draft.tenantName,
                allocatedMinutes: draft.allocatedMinutes,
                subConcurrency: draft.subConcurrency,
                status: "active",
                createdAt: now,
                updatedAt: now,
            };
            setTenants((prev) => [...prev, next]);
            return;
        }

        setTenants((prev) =>
            prev.map((t) =>
                t.id === editing.id
                    ? {
                          ...t,
                          tenantName: draft.tenantName,
                          allocatedMinutes: draft.allocatedMinutes,
                          subConcurrency: draft.subConcurrency,
                          updatedAt: now,
                      }
                    : t
            )
        );
    };

    const toggleSuspended = (id: string, status: TenantStatus) => {
        const now = Date.now();
        setTenants((prev) => prev.map((t) => (t.id === id ? { ...t, status, updatedAt: now } : t)));
    };

    const editingBaseline = useMemo(() => {
        if (!editing) return { minutes: 0, concurrency: 0 };
        return { minutes: editing.allocatedMinutes, concurrency: editing.subConcurrency };
    }, [editing]);

    const effectiveRemainingMinutes = useMemo(() => {
        if (!editing) return remainingMinutes;
        return clampMinZero(limits.maxMinutes - (totals.minutes - editingBaseline.minutes));
    }, [editing, editingBaseline.minutes, limits.maxMinutes, remainingMinutes, totals.minutes]);

    const effectiveRemainingConcurrency = useMemo(() => {
        if (!editing) return remainingConcurrency;
        return clampMinZero(limits.maxConcurrency - (totals.concurrency - editingBaseline.concurrency));
    }, [editing, editingBaseline.concurrency, limits.maxConcurrency, remainingConcurrency, totals.concurrency]);

    return (
        <div className="space-y-6">
            <div className="content-card">
                <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
                    <div className="min-w-0">
                        <div className="text-sm font-semibold text-foreground">Partner Resource Limits</div>
                        <div className="mt-1 text-sm text-muted-foreground">
                            Limits apply across all sub-tenants under {partnerDisplayName}.
                        </div>
                    </div>
                    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                        <div className="rounded-2xl border border-border bg-background/70 px-4 py-3">
                            <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Max Minutes</div>
                            <div className="mt-1 text-lg font-semibold tabular-nums text-foreground">{limits.maxMinutes.toLocaleString()}</div>
                        </div>
                        <div className="rounded-2xl border border-border bg-background/70 px-4 py-3">
                            <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Allocated</div>
                            <div className="mt-1 text-lg font-semibold tabular-nums text-foreground">{totals.minutes.toLocaleString()}</div>
                        </div>
                        <div className="rounded-2xl border border-border bg-background/70 px-4 py-3">
                            <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Max Concurrency</div>
                            <div className="mt-1 text-lg font-semibold tabular-nums text-foreground">{limits.maxConcurrency.toLocaleString()}</div>
                        </div>
                        <div className="rounded-2xl border border-border bg-background/70 px-4 py-3">
                            <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Allocated</div>
                            <div className="mt-1 text-lg font-semibold tabular-nums text-foreground">{totals.concurrency.toLocaleString()}</div>
                        </div>
                    </div>
                </div>
                <div className="mt-4 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                    <span>
                        Remaining minutes: <span className="font-semibold tabular-nums text-foreground">{remainingMinutes.toLocaleString()}</span>
                    </span>
                    <span className="text-border">•</span>
                    <span>
                        Remaining concurrency: <span className="font-semibold tabular-nums text-foreground">{remainingConcurrency.toLocaleString()}</span>
                    </span>
                </div>
            </div>

            <div className="content-card">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div className="min-w-0">
                        <div className="text-sm font-semibold text-foreground">Sub-Tenant Management</div>
                        <div className="mt-1 text-sm text-muted-foreground">Create, edit, suspend, and resume tenants scoped to this partner.</div>
                    </div>
                    <Button type="button" onClick={openCreate} className="shrink-0">
                        Create Sub-Tenant
                    </Button>
                </div>

                <TenantTable
                    partnerId={partnerId}
                    items={tenants}
                    loading={!loaded}
                    onEdit={openEdit}
                    onSuspend={(tenant) => setConfirmSuspendId(tenant.id)}
                    onResume={(tenant) => toggleSuspended(tenant.id, "active")}
                />
            </div>

            <TenantFormModal
                open={formOpen}
                onOpenChange={(next) => {
                    setFormOpen(next);
                    if (!next) setEditing(null);
                }}
                initialFocusRef={initialFocusRef}
                title={editing ? "Edit Tenant" : "Create Sub-Tenant"}
                submitLabel={editing ? "Save Changes" : "Create Tenant"}
                initialDraft={
                    editing
                        ? {
                              tenantName: editing.tenantName,
                              allocatedMinutes: String(editing.allocatedMinutes),
                              subConcurrency: String(editing.subConcurrency),
                          }
                        : { tenantName: "", allocatedMinutes: "", subConcurrency: "" }
                }
                existingTenants={tenants}
                editingTenantId={editing?.id ?? null}
                remainingMinutes={effectiveRemainingMinutes}
                remainingConcurrency={effectiveRemainingConcurrency}
                onSubmit={(result) => {
                    upsertTenant(result);
                    setFormOpen(false);
                    setEditing(null);
                }}
            />

            <ConfirmDialog
                open={Boolean(confirmSuspendId)}
                onOpenChange={(next) => {
                    if (!next) setConfirmSuspendId(null);
                }}
                intent="disconnect"
                title="Suspend tenant"
                description="Suspended tenants cannot place calls or consume minutes."
                warningText="This tenant will be blocked from making calls until resumed."
                confirmLabel="Suspend"
                pendingLabel="Suspending…"
                onConfirm={() => {
                    if (!confirmSuspendId) return;
                    toggleSuspended(confirmSuspendId, "suspended");
                    setConfirmSuspendId(null);
                }}
                onCancel={() => setConfirmSuspendId(null)}
            />
        </div>
    );
}

function TenantTable({
    partnerId,
    items,
    loading,
    onEdit,
    onSuspend,
    onResume,
}: {
    partnerId: string;
    items: Tenant[];
    loading: boolean;
    onEdit: (tenant: Tenant) => void;
    onSuspend: (tenant: Tenant) => void;
    onResume: (tenant: Tenant) => void;
}) {
    const gridCols = "md:grid-cols-[minmax(0,1fr)_170px_150px_140px_200px]";

    if (loading) {
        return (
            <div className="mt-6 flex items-center justify-center py-16" role="status" aria-live="polite" aria-busy="true">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" aria-hidden />
                <span className="sr-only">Loading tenants…</span>
            </div>
        );
    }

    if (items.length === 0) {
        return (
            <div className="mt-6 rounded-2xl border border-border bg-background/60 px-6 py-10 text-center">
                <div className="text-sm font-semibold text-foreground">No sub-tenants yet</div>
                <div className="mt-1 text-sm text-muted-foreground">Create your first tenant to start allocating minutes and concurrency.</div>
            </div>
        );
    }

    return (
        <div className="mt-6 overflow-hidden rounded-2xl border border-border bg-background/60">
            <div className={`hidden md:grid ${gridCols} items-center gap-2 border-b border-border bg-background/70 px-4 py-3 text-xs font-semibold text-muted-foreground`}>
                <div>Tenant Name</div>
                <div className="text-right">Allocated Minutes</div>
                <div className="text-right">Concurrency</div>
                <div>Status</div>
                <div className="text-right">Actions</div>
            </div>

            <div role="rowgroup" className="divide-y divide-border">
                {items.map((t) => (
                    <div key={t.id} role="row" className={`grid grid-cols-1 ${gridCols} gap-3 px-4 py-4 text-sm text-foreground`}>
                        <div className="min-w-0">
                            <div className="flex items-center justify-between gap-3 md:block">
                                <div className="min-w-0">
                                    <div className="truncate font-semibold">{t.tenantName}</div>
                                    <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground md:hidden">
                                        <span className="tabular-nums">{t.allocatedMinutes.toLocaleString()} min</span>
                                        <span className="text-border">•</span>
                                        <span className="tabular-nums">{t.subConcurrency.toLocaleString()} concurrent</span>
                                    </div>
                                </div>
                                <div className="md:hidden">{tenantStatusPill(t.status)}</div>
                            </div>
                        </div>
                        <div className="hidden text-right tabular-nums md:block">{t.allocatedMinutes.toLocaleString()}</div>
                        <div className="hidden text-right tabular-nums md:block">{t.subConcurrency.toLocaleString()}</div>
                        <div className="hidden md:block">{tenantStatusPill(t.status)}</div>
                        <div className="flex flex-wrap items-center justify-start gap-2 md:justify-end">
                            <Button type="button" variant="secondary" size="sm" asChild>
                                <Link href={`/white-label/${encodeURIComponent(partnerId)}/tenants/${encodeURIComponent(t.id)}/agent-settings`}>
                                    Agent Settings
                                </Link>
                            </Button>
                            <Button type="button" variant="outline" size="sm" onClick={() => onEdit(t)}>
                                Edit
                            </Button>
                            {t.status === "active" ? (
                                <Button type="button" variant="destructive" size="sm" onClick={() => onSuspend(t)}>
                                    Suspend
                                </Button>
                            ) : (
                                <Button type="button" variant="secondary" size="sm" onClick={() => onResume(t)}>
                                    Resume
                                </Button>
                            )}
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}

function TenantFormModal({
    open,
    onOpenChange,
    title,
    submitLabel,
    initialDraft,
    existingTenants,
    editingTenantId,
    remainingMinutes,
    remainingConcurrency,
    initialFocusRef,
    onSubmit,
}: {
    open: boolean;
    onOpenChange: (next: boolean) => void;
    title: string;
    submitLabel: string;
    initialDraft: TenantDraft;
    existingTenants: Tenant[];
    editingTenantId: string | null;
    remainingMinutes: number;
    remainingConcurrency: number;
    initialFocusRef: React.RefObject<HTMLInputElement | null>;
    onSubmit: (tenant: { tenantName: string; allocatedMinutes: number; subConcurrency: number }) => void;
}) {
    const [draft, setDraft] = useState<TenantDraft>(initialDraft);

    useEffect(() => {
        if (!open) return;
        setDraft(initialDraft);
    }, [initialDraft, open]);

    const normalizedNames = useMemo(() => {
        const set = new Set<string>();
        for (const t of existingTenants) {
            if (editingTenantId && t.id === editingTenantId) continue;
            set.add(t.tenantName.trim().toLowerCase());
        }
        return set;
    }, [editingTenantId, existingTenants]);

    const validation = useMemo(() => {
        const errors: Partial<Record<keyof TenantDraft, string>> = {};
        const name = draft.tenantName.trim();
        if (name.length === 0) errors.tenantName = "Tenant name is required.";
        if (name.length > 0 && normalizedNames.has(name.toLowerCase())) errors.tenantName = "Tenant name must be unique for this partner.";

        const minutes = parseNonNegativeInt(draft.allocatedMinutes);
        if (minutes === null) errors.allocatedMinutes = "Allocated minutes must be a whole number (0 or greater).";
        if (typeof minutes === "number" && minutes > remainingMinutes) {
            errors.allocatedMinutes = `Allocated minutes exceed remaining capacity (${remainingMinutes.toLocaleString()}).`;
        }

        const conc = parsePositiveInt(draft.subConcurrency);
        if (conc === null) errors.subConcurrency = "Sub-concurrency must be a whole number (1 or greater).";
        if (typeof conc === "number" && conc > remainingConcurrency) {
            errors.subConcurrency = `Sub-concurrency exceeds remaining capacity (${remainingConcurrency.toLocaleString()}).`;
        }

        const msgs = Object.values(errors).filter(Boolean) as string[];
        const message = msgs.length > 0 ? msgs[0]! : null;
        const value =
            message === null && minutes !== null && conc !== null
                ? { tenantName: name, allocatedMinutes: minutes, subConcurrency: conc }
                : null;
        return { errors, message, value };
    }, [draft, normalizedNames, remainingConcurrency, remainingMinutes]);

    const showInlineError = Boolean(
        validation.message &&
            (draft.tenantName.trim().length > 0 || draft.allocatedMinutes.trim().length > 0 || draft.subConcurrency.trim().length > 0 || remainingConcurrency === 0)
    );

    return (
        <Modal
            open={open}
            onOpenChange={onOpenChange}
            title={title}
            description="All allocations are enforced against partner limits."
            ariaLabel={title}
            initialFocusRef={initialFocusRef as unknown as React.RefObject<HTMLElement | null>}
            footer={
                <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
                    <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
                        Cancel
                    </Button>
                    <Button
                        type="button"
                        disabled={!validation.value}
                        onClick={() => {
                            if (!validation.value) return;
                            onSubmit(validation.value);
                        }}
                    >
                        {submitLabel}
                    </Button>
                </div>
            }
        >
            <div className="space-y-4">
                {showInlineError ? (
                    <div className="rounded-2xl border border-red-500/20 bg-red-500/10 px-4 py-3 text-sm text-red-100" role="alert">
                        {validation.message}
                    </div>
                ) : null}

                <div className="space-y-2">
                    <Label htmlFor="tenantName">Tenant Name</Label>
                    <Input
                        id="tenantName"
                        ref={initialFocusRef}
                        value={draft.tenantName}
                        onChange={(e) => setDraft((prev) => ({ ...prev, tenantName: e.target.value }))}
                        placeholder="Clinic AI"
                        aria-invalid={Boolean(validation.errors.tenantName)}
                    />
                    {validation.errors.tenantName ? <div className="text-xs text-destructive">{validation.errors.tenantName}</div> : null}
                </div>

                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <div className="space-y-2">
                        <Label htmlFor="allocatedMinutes">Allocated Minutes</Label>
                        <Input
                            id="allocatedMinutes"
                            inputMode="numeric"
                            type="number"
                            min={0}
                            step={1}
                            value={draft.allocatedMinutes}
                            onChange={(e) => setDraft((prev) => ({ ...prev, allocatedMinutes: e.target.value }))}
                            placeholder="1000"
                            aria-invalid={Boolean(validation.errors.allocatedMinutes)}
                        />
                        <div className="text-xs text-muted-foreground">
                            Remaining: <span className="font-semibold tabular-nums text-foreground">{remainingMinutes.toLocaleString()}</span>
                        </div>
                        {validation.errors.allocatedMinutes ? <div className="text-xs text-destructive">{validation.errors.allocatedMinutes}</div> : null}
                    </div>

                    <div className="space-y-2">
                        <Label htmlFor="subConcurrency">Sub-Concurrency</Label>
                        <Input
                            id="subConcurrency"
                            inputMode="numeric"
                            type="number"
                            min={1}
                            step={1}
                            value={draft.subConcurrency}
                            onChange={(e) => setDraft((prev) => ({ ...prev, subConcurrency: e.target.value }))}
                            placeholder="3"
                            aria-invalid={Boolean(validation.errors.subConcurrency)}
                        />
                        <div className="text-xs text-muted-foreground">
                            Remaining: <span className="font-semibold tabular-nums text-foreground">{remainingConcurrency.toLocaleString()}</span>
                        </div>
                        {validation.errors.subConcurrency ? <div className="text-xs text-destructive">{validation.errors.subConcurrency}</div> : null}
                    </div>
                </div>
            </div>
        </Modal>
    );
}
