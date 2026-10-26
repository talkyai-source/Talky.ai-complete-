"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Activity, ChevronLeft, ChevronRight, CreditCard, Filter, LogIn, LockKeyhole, RefreshCw, Shield, ShieldAlert, UserCog, UserRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { EmptyState, ErrorState, LoadingState } from "@/components/states/page-states";
import { useAuth } from "@/hooks/useAuth";
import { getAdminUiCapabilities } from "@/lib/admin-access";
import { queryKeys, useAdminEventReadScope, useAdminPartners, useAdminTenants, useAuditLogs, useReactivateTenant, useSecurityEvents, useSuspendTenant } from "@/lib/api-hooks";
import { publicAppConfig } from "@/lib/env";
import { cn } from "@/lib/utils";
import type { AuditSeverity, EventBoundary, SecurityEventStatus, PartnerSummary, TenantSummary } from "@/lib/models";
import { FormalPartnerHoldPanel } from "@/components/admin/formal-partner-hold-panel";
import { getRequestIdentityGeneration } from "@/lib/http-client";

type AdminTab = "audit" | "security" | "suspensions" | "config";

type PendingAction = {
    kind: "tenant_suspend" | "tenant_reactivate"; item: TenantSummary;
    owner: string; query: string; generation: number; id: number;
};
type PartnerReview = { partner: PartnerSummary; owner: string; operatorId: string; generation: number; opener: HTMLButtonElement };

const AUDIT_PAGE_SIZE = 20;
const RESOURCE_PAGE_SIZE = 8;

function formatDateTime(value: string | null | undefined) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat(undefined, {
        year: "numeric",
        month: "short",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
    }).format(date);
}

function formatActor(actor: { id?: string; type: string }) {
    return actor.id ? `${actor.type}: ${actor.id}` : `${actor.type} (ID unavailable)`;
}

function formatTarget(target: { type: string; name?: string; id?: string } | undefined) {
    if (!target) return "—";
    const label = target.name?.trim() || target.id?.trim() || "Unknown target";
    return `${target.type}: ${label}`;
}

function metadataEntries(metadata: Record<string, unknown> | undefined) {
    if (!metadata) return [];
    return Object.entries(metadata)
        .filter(([key]) => !/(token|secret|password|credential|authorization|cookie|key)/i.test(key))
        .slice(0, 6)
        .map(([key, value]) => ({
            key,
            value:
                value === null
                    ? "null"
                    : typeof value === "string" || typeof value === "number" || typeof value === "boolean"
                      ? String(value)
                      : Array.isArray(value)
                        ? value.map((item) => (typeof item === "string" || typeof item === "number" || typeof item === "boolean" ? String(item) : "…")).join(", ")
                        : "Structured value",
        }));
}

function canGoNext(total: number | undefined, page: number, pageSize: number, currentCount: number) {
    if (typeof total === "number") return page * pageSize < total;
    return currentCount >= pageSize;
}

function SeverityPill({ severity }: { severity: AuditSeverity }) {
    return (
        <span
            className={cn(
                "inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold",
                ["CRITICAL", "HIGH"].includes(severity)
                    ? "bg-red-500/15 text-red-700"
                    : severity === "MEDIUM"
                      ? "bg-amber-500/15 text-amber-700"
                      : "bg-muted text-muted-foreground"
            )}
        >
            {severity}
        </span>
    );
}

function StatusBadge({ status, prefix = "" }: { status: PartnerSummary["status"] | TenantSummary["status"]; prefix?: string }) {
    return (
        <span
            className={cn(
                "inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold",
                status === "suspended" ? "bg-red-500/15 text-red-700" : status === "active" ? "bg-emerald-500/15 text-emerald-700" : "bg-amber-500/15 text-amber-700"
            )}
        >
            {prefix}{status.replaceAll("_", " ")}
        </span>
    );
}

function AuditEventIcon({ eventType }: { eventType: string }) {
    const iconClass = "h-4 w-4 text-muted-foreground";
    if (eventType === "login" || eventType.includes("login")) {
        return <LogIn className={iconClass} aria-hidden />;
    }
    if (eventType === "role_change" || eventType.includes("role")) {
        return <UserCog className={iconClass} aria-hidden />;
    }
    if (eventType === "billing_change" || eventType.includes("billing")) {
        return <CreditCard className={iconClass} aria-hidden />;
    }
    if (eventType === "suspension" || eventType.includes("suspend")) {
        return <ShieldAlert className={iconClass} aria-hidden />;
    }
    return <Activity className={iconClass} aria-hidden />;
}

function SectionTabs({ activeTab, onChange, canViewSecurityEvents, canManageSuspensions }: { activeTab: AdminTab; onChange: (tab: AdminTab) => void; canViewSecurityEvents: boolean; canManageSuspensions: boolean; }) {
    const tabs: Array<{ id: AdminTab; label: string }> = [
        { id: "audit", label: "Audit Logs" },
        ...(canViewSecurityEvents ? ([{ id: "security", label: "Security Events" }] as const) : []),
        ...(canManageSuspensions ? ([{ id: "suspensions", label: "Suspensions" }] as const) : []),
        { id: "config", label: "Configuration" },
    ];

    return (
        <div className="flex flex-wrap gap-2" role="tablist" aria-label="Admin sections">
            {tabs.map((tab) => (
                <button
                    key={tab.id}
                    type="button"
                    role="tab"
                    aria-selected={activeTab === tab.id}
                    onClick={() => onChange(tab.id)}
                    className={cn(
                        "rounded-full border px-4 py-2 text-sm font-semibold transition-colors",
                        activeTab === tab.id ? "border-foreground/20 bg-foreground text-background" : "border-border bg-background text-muted-foreground hover:text-foreground"
                    )}
                >
                    {tab.label}
                </button>
            ))}
        </div>
    );
}

function PaginationControls({
    page,
    disabled,
    canNext,
    onPrevious,
    onNext,
}: {
    page: number;
    disabled?: boolean;
    canNext: boolean;
    onPrevious: () => void;
    onNext: () => void;
}) {
    return (
        <div className="flex items-center justify-between gap-3 border-t border-border pt-4">
            <div className="text-sm text-muted-foreground">Page {page}</div>
            <div className="flex items-center gap-2">
                <Button type="button" variant="outline" onClick={onPrevious} disabled={disabled || page <= 1}>
                    <ChevronLeft className="h-4 w-4" />
                    Previous
                </Button>
                <Button type="button" variant="outline" onClick={onNext} disabled={disabled || !canNext}>
                    Next
                    <ChevronRight className="h-4 w-4" />
                </Button>
            </div>
        </div>
    );
}

type EventNavigation = { scope: string; boundaries: Array<EventBoundary | undefined>; index: number };
const firstEventPage = (scope: string): EventNavigation => ({ scope, boundaries: [undefined], index: 0 });

export function AdminOperationsConsole() {
    const { user, refreshUser } = useAuth();
    const eventOwner = useAdminEventReadScope();
    const queryClient = useQueryClient();
    const capabilities = useMemo(() => ({ ...getAdminUiCapabilities(user),
        canManageTenantSuspensions: user?.role === "platform_admin" }), [user]);
    const config = useMemo(() => publicAppConfig(), []);
    const [activeTab, setActiveTab] = useState<AdminTab>("audit");
    const [confirmAction, setConfirmAction] = useState<PendingAction | null>(null);
    const tenantReviewId = useRef(0);
    const tenantInFlight = useRef<{ review: PendingAction; promise: Promise<void> } | null>(null);
    const [formalPartner, setFormalPartner] = useState<PartnerReview | null>(null);
    const partnerSearchRef = useRef<HTMLInputElement>(null);

    const [auditNavigation, setAuditNavigation] = useState(() => firstEventPage(""));
    const [auditEventType, setAuditEventType] = useState("all");
    const [auditFrom, setAuditFrom] = useState("");
    const [auditTo, setAuditTo] = useState("");
    const [auditUser, setAuditUser] = useState("");
    const [auditTenant, setAuditTenant] = useState("");
    const [auditPartner, setAuditPartner] = useState("");

    const [securityNavigation, setSecurityNavigation] = useState(() => firstEventPage(""));
    const [securityEventType, setSecurityEventType] = useState("all");
    const [securitySeverity, setSecuritySeverity] = useState("all");
    const [securityStatus, setSecurityStatus] = useState("all");
    const [securityFrom, setSecurityFrom] = useState("");
    const [securityTo, setSecurityTo] = useState("");
    const [securityUser, setSecurityUser] = useState("");
    const [securityTenant, setSecurityTenant] = useState("");

    const [partnerPage, setPartnerPage] = useState(1);
    const [partnerQuery, setPartnerQuery] = useState("");
    const [partnerStatus, setPartnerStatus] = useState("all");

    const [tenantPage, setTenantPage] = useState(1);
    const [tenantQuery, setTenantQuery] = useState("");
    const [tenantStatus, setTenantStatus] = useState("all");

    const [confirmReason, setConfirmReason] = useState("");

    const auditFilters = useMemo(() => ({
        pageSize: AUDIT_PAGE_SIZE,
        eventGroup: auditEventType === "all" ? undefined : auditEventType,
        from: auditFrom ? `${auditFrom}T00:00:00Z` : undefined,
        to: auditTo ? `${auditTo}T23:59:59.999999Z` : undefined,
        userQuery: auditUser.trim() || undefined,
        tenantId: (capabilities.allowedTenantId ?? auditTenant.trim()) || undefined,
        partnerId: (capabilities.allowedPartnerId ?? auditPartner.trim()) || undefined,
    }), [auditEventType, auditFrom, auditPartner, auditTenant, auditTo, auditUser, capabilities.allowedPartnerId, capabilities.allowedTenantId]);
    const securityFilters = useMemo(() => ({
        pageSize: AUDIT_PAGE_SIZE,
        eventGroup: securityEventType === "all" ? undefined : securityEventType,
        severity: securitySeverity === "all" ? undefined : securitySeverity as AuditSeverity,
        status: securityStatus === "all" ? undefined : securityStatus as SecurityEventStatus,
        from: securityFrom ? `${securityFrom}T00:00:00Z` : undefined,
        to: securityTo ? `${securityTo}T23:59:59.999999Z` : undefined,
        userQuery: securityUser.trim() || undefined,
        tenantId: (capabilities.allowedTenantId ?? securityTenant.trim()) || undefined,
        partnerId: capabilities.allowedPartnerId ?? undefined,
    }), [capabilities.allowedPartnerId, capabilities.allowedTenantId, securityEventType, securityFrom, securitySeverity, securityStatus, securityTenant, securityTo, securityUser]);
    const auditScope = JSON.stringify([eventOwner.key, auditFilters]);
    const securityScope = JSON.stringify([eventOwner.key, securityFilters]);
    // Resolve a new identity/filter to page one during render, before any read
    // can reuse a prior actor's boundary. No effect-time reset race.
    const auditPosition = auditNavigation.scope === auditScope ? auditNavigation : firstEventPage(auditScope);
    const securityPosition = securityNavigation.scope === securityScope ? securityNavigation : firstEventPage(securityScope);
    // Commit the reset after the synchronous derived guard. Otherwise returning
    // to an earlier filter could resurrect its stored page-two boundary.
    useEffect(() => {
        // eslint-disable-next-line react-hooks/set-state-in-effect -- retire stored navigation so returning to an old filter cannot revive its cursor
        setAuditNavigation(current => current.scope === auditScope ? current : firstEventPage(auditScope));
    }, [auditScope]);
    useEffect(() => {
        // eslint-disable-next-line react-hooks/set-state-in-effect -- retire stored navigation so returning to an old filter cannot revive its cursor
        setSecurityNavigation(current => current.scope === securityScope ? current : firstEventPage(securityScope));
    }, [securityScope]);
    const auditPage = auditPosition.index + 1, securityPage = securityPosition.index + 1;
    const auditQuery = { ...auditFilters, before: auditPosition.boundaries[auditPosition.index] };
    const securityQuery = { ...securityFilters, before: securityPosition.boundaries[securityPosition.index] };

    const partnerListQuery = useMemo(
        () => ({
            page: partnerPage,
            pageSize: RESOURCE_PAGE_SIZE,
            query: partnerQuery.trim() || undefined,
            status: partnerStatus === "all" ? undefined : (partnerStatus as PartnerSummary["status"]),
        }),
        [partnerPage, partnerQuery, partnerStatus]
    );

    const tenantListQuery = useMemo(
        () => ({
            page: tenantPage,
            pageSize: RESOURCE_PAGE_SIZE,
            query: tenantQuery.trim() || undefined,
            status: tenantStatus === "all" ? undefined : (tenantStatus as "active" | "suspended"),
        }),
        [tenantPage, tenantQuery, tenantStatus]
    );

    const auditLogsQ = useAuditLogs(auditQuery, { enabled: capabilities.canViewAuditLogs && activeTab === "audit" });
    const securityEventsQ = useSecurityEvents(securityQuery, { enabled: capabilities.canViewSecurityEvents && activeTab === "security" });
    const partnersQ = useAdminPartners(partnerListQuery, { enabled: capabilities.canManagePartnerSuspensions && activeTab === "suspensions" });
    const tenantsQ = useAdminTenants(tenantListQuery, { enabled: capabilities.canManageTenantSuspensions && activeTab === "suspensions" });

    const generation = getRequestIdentityGeneration();
    const partnerOwner = JSON.stringify([user?.id, user?.role, user?.tenant_id, user?.partner_id, generation]);
    const tenantOwner = partnerOwner;
    const tenantQueryScope = JSON.stringify([eventOwner.key, tenantListQuery]);
    const tenantReview = confirmAction && eventOwner.ready && capabilities.canManageTenantSuspensions
        && activeTab === "suspensions" && confirmAction.owner === tenantOwner && confirmAction.query === tenantQueryScope
        ? confirmAction : null;
    const tenantContext = useRef<{
        owner: string; query: string; ready: boolean; active: boolean; fetching: boolean;
        selection: PendingAction | null; items: TenantSummary[] | undefined;
    } | null>(null);
    useLayoutEffect(() => {
        tenantContext.current = { owner: tenantOwner, query: tenantQueryScope, ready: eventOwner.ready,
            active: activeTab === "suspensions" && capabilities.canManageTenantSuspensions,
            fetching: tenantsQ.isFetching, selection: tenantReview, items: tenantsQ.data?.items };
        return () => { tenantContext.current = null; };
    }, [activeTab, capabilities.canManageTenantSuspensions, eventOwner.ready, tenantOwner, tenantQueryScope, tenantReview, tenantsQ.data?.items, tenantsQ.isFetching]);
    useEffect(() => {
        if (!confirmAction || tenantReview) return;
        // eslint-disable-next-line react-hooks/set-state-in-effect -- retire stored review after its synchronous render/dispatch ownership guard
        setConfirmAction(null);
        setConfirmReason("");
    }, [confirmAction, tenantReview]);

    function ownsTenantReview(review: PendingAction) {
        const current = tenantContext.current;
        return Boolean(current?.ready && current.active && current.owner === review.owner
            && current.query === review.query && current.selection === review
            && getRequestIdentityGeneration() === review.generation);
    }
    function openTenantReview(tenant: TenantSummary, opener: HTMLButtonElement) {
        const current = tenantContext.current;
        if (!current?.ready || !current.active || current.fetching || current.owner !== tenantOwner
            || current.query !== tenantQueryScope || getRequestIdentityGeneration() !== generation
            || !current.items?.includes(tenant) || tenant.updatedAt === undefined
            || !["active", "suspended"].includes(tenant.status) || !opener.isConnected || opener.disabled) return;
        setConfirmReason("");
        setConfirmAction({ kind: tenant.status === "suspended" ? "tenant_reactivate" : "tenant_suspend", item: tenant,
            owner: tenantOwner, query: tenantQueryScope, generation, id: ++tenantReviewId.current });
    }
    function closeTenantReview(review: PendingAction) {
        if (!ownsTenantReview(review)) return;
        setConfirmAction(null);
        setConfirmReason("");
    }
    function retryTenants() {
        const current = tenantContext.current;
        if (!current?.ready || !current.active || current.owner !== tenantOwner || current.query !== tenantQueryScope
            || getRequestIdentityGeneration() !== generation) return;
        void tenantsQ.refetch();
    }

    const partnerQueryScope = JSON.stringify([eventOwner.key, partnerListQuery]);
    const partnerContext = useRef<{
        owner: string; query: string; ready: boolean; active: boolean;
        selection: PartnerReview | null; items: PartnerSummary[] | undefined;
    } | null>(null);
    // A retained row/notification callback must use the latest committed owner
    // and query. Checking/loading does not retire a saved decision for the same
    // principal; a real identity-generation change does.
    useLayoutEffect(() => {
        partnerContext.current = { owner: partnerOwner, query: partnerQueryScope, ready: eventOwner.ready,
            active: activeTab === "suspensions", selection: formalPartner, items: partnersQ.data?.items };
        return () => { partnerContext.current = null; };
    }, [activeTab, eventOwner.ready, formalPartner, partnerOwner, partnerQueryScope, partnersQ.data?.items]);

    function openPartnerReview(partner: PartnerSummary, opener: HTMLButtonElement) {
        const current = partnerContext.current;
        if (!current?.ready || !current.active || current.owner !== partnerOwner || current.query !== partnerQueryScope
            || user?.role !== "platform_admin" || !current.items?.includes(partner) || !opener.isConnected || opener.disabled) return;
        setFormalPartner({ partner, owner: partnerOwner, operatorId: user.id, generation, opener });
    }
    function ownsPartnerReview(review: PartnerReview) {
        const current = partnerContext.current;
        return current?.active && current.owner === review.owner && current.selection === review
            && getRequestIdentityGeneration() === review.generation;
    }
    function partnerChanged(review: PartnerReview) {
        if (!ownsPartnerReview(review)) return;
        // Refetch the active current query, while marking other cached pages
        // stale. A receipt is not an optimistic account-status override.
        void queryClient.invalidateQueries({ queryKey: ["adminPartners"], refetchType: "active" }).catch(() => {});
        // Keep the saved decision mounted while canonical authority refreshes.
        // This read may fail silently; the receipt is not a freshness claim.
        void refreshUser({ silent: true }).catch(() => {});
    }
    function closePartnerReview(review: PartnerReview) {
        if (!ownsPartnerReview(review)) return;
        setFormalPartner(null);
        const target = review.opener.isConnected && !review.opener.disabled ? review.opener : partnerSearchRef.current;
        if (target?.isConnected) target.focus();
    }

    function refreshEventHistory(kind: "audit" | "security") {
        if (!eventOwner.ready) return;
        const audit = kind === "audit", filters = audit ? auditFilters : securityFilters;
        const firstQuery = { ...filters, before: undefined };
        const key = audit ? queryKeys.auditLogs(JSON.stringify([eventOwner.key, firstQuery]))
            : queryKeys.securityEvents(JSON.stringify([eventOwner.key, firstQuery]));
        // Mark exactly this owner's first page stale before changing the stack;
        // refetching the current page here would otherwise fetch the old cursor.
        void queryClient.invalidateQueries({ queryKey: key, exact: true, refetchType: "none" });
        if (audit) {
            setAuditNavigation(firstEventPage(auditScope));
            if (auditPosition.index === 0) void auditLogsQ.refetch();
        } else {
            setSecurityNavigation(firstEventPage(securityScope));
            if (securityPosition.index === 0) void securityEventsQ.refetch();
        }
    }

    const suspendTenantM = useSuspendTenant();
    const reactivateTenantM = useReactivateTenant();

    const mutationBusyId = suspendTenantM.isPending && suspendTenantM.variables?.queryKey[1] === tenantQueryScope
        ? suspendTenantM.variables.tenantId
        : reactivateTenantM.isPending && reactivateTenantM.variables?.queryKey[1] === tenantQueryScope
          ? reactivateTenantM.variables.tenantId : null;

    function runConfirmedAction(review: PendingAction): Promise<void> {
        if (!ownsTenantReview(review)) return Promise.resolve();
        if (tenantInFlight.current?.review === review) return tenantInFlight.current.promise;
        const expectedUpdatedAt = review.item.updatedAt;
        if (expectedUpdatedAt === undefined) return Promise.reject(new Error("Refresh and review this tenant before changing its subscription."));
        const promise = (async () => {
            try {
                const mutation = review.kind === "tenant_suspend" ? suspendTenantM : reactivateTenantM;
                await mutation.mutateAsync({ tenantId: review.item.id, expectedUpdatedAt,
                    reason: confirmReason.trim() || undefined, ownsOperation: () => ownsTenantReview(review),
                    queryKey: queryKeys.adminTenants(review.query) });
                if (!ownsTenantReview(review)) return;
                // The subscription receipt is not an account-access override or a
                // guarantee that this independent canonical read will succeed.
                void refreshUser({ silent: true }).catch(() => {});
                setConfirmReason("");
            } catch (error) {
                if (ownsTenantReview(review)) throw error;
            } finally {
                if (tenantInFlight.current?.review === review) tenantInFlight.current = null;
            }
        })();
        tenantInFlight.current = { review, promise };
        return promise;
    }

    return (
        <div className="space-y-6">
            <Card className="border-border bg-background/80">
                <CardHeader className="gap-4 md:flex-row md:items-start md:justify-between">
                    <div className="space-y-2">
                        <CardTitle className="text-lg">Operations Console</CardTitle>
                        <CardDescription>
                            Review backend-driven audit activity, inspect security events, and execute guarded suspension actions without exposing secrets or inferring server-side rules.
                        </CardDescription>
                    </div>
                    <SectionTabs
                        activeTab={activeTab}
                        onChange={tab => { setFormalPartner(null); setActiveTab(tab); }}
                        canViewSecurityEvents={capabilities.canViewSecurityEvents}
                        canManageSuspensions={capabilities.canManagePartnerSuspensions || capabilities.canManageTenantSuspensions}
                    />
                </CardHeader>
                <CardContent className="grid gap-3 md:grid-cols-3">
                    <div className="rounded-2xl border border-border bg-muted/20 p-4">
                        <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
                            <Shield className="h-4 w-4" />
                            Role Scope
                        </div>
                        <div className="mt-2 text-sm text-muted-foreground">
                            {user?.role === "platform_admin" ? "Platform operator; each read is authorized by the server." : "Requested scope does not grant access; the server decides each read."}
                        </div>
                    </div>
                    <div className="rounded-2xl border border-border bg-muted/20 p-4">
                        <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
                            <UserRound className="h-4 w-4" />
                            Audit Access
                        </div>
                        <div className="mt-2 text-sm text-muted-foreground">Read results confirm access for the current session. A denied or unavailable read is shown explicitly.</div>
                    </div>
                    <div className="rounded-2xl border border-border bg-muted/20 p-4">
                        <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
                            <LockKeyhole className="h-4 w-4" />
                            Safe Config
                        </div>
                        <div className="mt-2 text-sm text-muted-foreground">Only public environment configuration is surfaced here. Secrets remain server-side.</div>
                    </div>
                </CardContent>
            </Card>

            {activeTab === "audit" ? (
                <Card className="border-border bg-background/80">
                    <CardHeader>
                        <CardTitle className="text-lg">Audit Logs</CardTitle>
                        <CardDescription>Track authentication, role, billing, and suspension events with scoped filtering and paginated results.</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-5">
                        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-6">
                            <div className="space-y-2">
                                <Label>Event type</Label>
                                <Select
                                    value={auditEventType}
                                    onChange={(next) => {
                                        setAuditEventType(next);
                                    }}
                                    ariaLabel="Audit event type"
                                >
                                    <option value="all">All events</option>
                                    <option value="login">Login events</option>
                                    <option value="role_change">Role operations</option>
                                    <option value="billing_change">Billing events</option>
                                    <option value="suspension">Suspension events</option>
                                </Select>
                            </div>
                            <div className="space-y-2">
                                <Label>From</Label>
                                <Input aria-label="Audit from date (UTC)" type="date" value={auditFrom} onChange={(e) => {  setAuditFrom(e.target.value); }} />
                            </div>
                            <div className="space-y-2">
                                <Label>To</Label>
                                <Input aria-label="Audit to date (UTC)" type="date" value={auditTo} onChange={(e) => {  setAuditTo(e.target.value); }} />
                            </div>
                            <div className="space-y-2">
                                <Label>Actor name, email or ID</Label>
                                <Input aria-label="Audit actor search" value={auditUser} onChange={(e) => {  setAuditUser(e.target.value); }} placeholder="Name or email" />
                            </div>
                            <div className="space-y-2">
                                <Label>Tenant</Label>
                                <Input aria-label="Audit tenant ID" value={capabilities.allowedTenantId ?? auditTenant} onChange={(e) => {  setAuditTenant(e.target.value); }} disabled={Boolean(capabilities.allowedTenantId)} placeholder="tenant-id" />
                            </div>
                            <div className="space-y-2">
                                <Label>Partner (current assignment)</Label>
                                <Input aria-label="Audit partner ID (current assignment)" value={capabilities.allowedPartnerId ?? auditPartner} onChange={(e) => {  setAuditPartner(e.target.value); }} disabled={Boolean(capabilities.allowedPartnerId)} placeholder="partner-id" />
                            </div>
                        </div>

                        <p className="text-xs text-muted-foreground">User search uses current profile names/emails or the saved actor ID. Partner matches current tenant assignment and direct partner records, not historical ownership. Dates are UTC. History may change while you browse.</p>
                        <Button type="button" variant="outline" disabled={!eventOwner.ready || auditLogsQ.isFetching} onClick={() => refreshEventHistory("audit")}>Refresh audit history</Button>
                        {auditLogsQ.isPending ? <LoadingState title="Loading audit logs" description="Fetching backend activity history." /> : null}
                        {auditLogsQ.isError ? <ErrorState title="Audit logs unavailable" message={auditLogsQ.error instanceof Error ? auditLogsQ.error.message : "Could not load audit activity from the backend."} onRetry={() => auditLogsQ.refetch().then(() => {})} /> : null}
                        {!auditLogsQ.isPending && !auditLogsQ.isError ? (
                            auditLogsQ.data?.items.length ? (
                                <div className="space-y-4">
                                    <div className="overflow-hidden rounded-2xl border border-border">
                                        <div className="grid gap-3 border-b border-border bg-muted/30 px-4 py-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground md:grid-cols-[180px_1fr_180px_1.2fr]">
                                            <div>Timestamp</div>
                                            <div>Actor & Action</div>
                                            <div>Target</div>
                                            <div>Metadata</div>
                                        </div>
                                        {auditLogsQ.data.items.map((event) => (
                                            <div key={event.id} className="grid gap-3 border-b border-border/70 px-4 py-4 last:border-b-0 md:grid-cols-[180px_1fr_180px_1.2fr]">
                                                <div className="text-sm text-muted-foreground">{formatDateTime(event.timestamp)}</div>
                                                <div className="space-y-1">
                                                    <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
                                                        <AuditEventIcon eventType={event.eventType ?? event.actionType} />
                                                        {event.actionType}
                                                    </div>
                                                    <div className="break-all text-sm text-muted-foreground">{formatActor(event.actor)}</div>
                                                    <SeverityPill severity={event.severity} />
                                                </div>
                                                <div className="text-sm text-foreground">{formatTarget(event.target)}</div>
                                                <div className="flex flex-wrap gap-2">
                                                    {metadataEntries(event.metadata).length ? metadataEntries(event.metadata).map((entry) => (
                                                        <span key={`${event.id}-${entry.key}`} className="rounded-full bg-muted px-2.5 py-1 text-xs text-muted-foreground">
                                                            {entry.key}: {entry.value}
                                                        </span>
                                                    )) : <span className="text-sm text-muted-foreground">No metadata</span>}
                                                </div>
                                            </div>
                                        ))}
                                    </div>

                                </div>
                            ) : (
                                <EmptyState title="No audit activity found" message="Adjust filters or widen the date range to see more events." kind="search" />
                            )
                        ) : null}
                        <PaginationControls
                            page={auditPage}
                            disabled={auditLogsQ.isFetching}
                            canNext={!auditLogsQ.isError && Boolean(auditLogsQ.data?.hasNext)}
                            onPrevious={() => setAuditNavigation({ ...auditPosition, index: Math.max(0, auditPosition.index - 1) })}
                            onNext={() => {
                                const boundary = auditLogsQ.data?.nextBoundary;
                                if (!boundary || auditLogsQ.isFetching || auditLogsQ.isError) return;
                                setAuditNavigation({ scope: auditScope, index: auditPosition.index + 1,
                                    boundaries: [...auditPosition.boundaries.slice(0, auditPosition.index + 1), boundary] });
                            }}
                        />
                    </CardContent>
                </Card>
            ) : null}

            {activeTab === "security" && capabilities.canViewSecurityEvents ? (
                <Card className="border-border bg-background/80">
                    <CardHeader>
                        <CardTitle className="text-lg">Security Events</CardTitle>
                        <CardDescription>Monitor failed logins, rate limiting, and suspicious activity with severity-aware indicators.</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-5">
                        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-6">
                            <div className="space-y-2">
                                <Label>Event type</Label>
                                <Select value={securityEventType} onChange={(next) => {  setSecurityEventType(next); }} ariaLabel="Security event type">
                                    <option value="all">All events</option>
                                    <option value="failed_login">Failed logins</option>
                                    <option value="rate_limit_violation">Rate limit violations</option>
                                    <option value="suspicious_activity">Suspicious activity</option>
                                </Select>
                            </div>
                            <div className="space-y-2">
                                <Label>Severity</Label>
                                <Select value={securitySeverity} onChange={(next) => {  setSecuritySeverity(next); }} ariaLabel="Security severity">
                                    <option value="all">All severities</option>
                                    {["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"].map(value => <option key={value} value={value}>{value}</option>)}
                                </Select>
                            </div>
                            <div className="space-y-2">
                                <Label>Status</Label>
                                <Select value={securityStatus} onChange={setSecurityStatus} ariaLabel="Security status">
                                    <option value="all">All statuses</option>
                                    <option value="open">Open</option><option value="investigating">Investigating</option>
                                    <option value="resolved">Resolved</option><option value="false_positive">False positive</option>
                                    <option value="escalated">Escalated</option>
                                </Select>
                            </div>
                            <div className="space-y-2">
                                <Label>From</Label>
                                <Input aria-label="Security from date (UTC)" type="date" value={securityFrom} onChange={(e) => {  setSecurityFrom(e.target.value); }} />
                            </div>
                            <div className="space-y-2">
                                <Label>To</Label>
                                <Input aria-label="Security to date (UTC)" type="date" value={securityTo} onChange={(e) => {  setSecurityTo(e.target.value); }} />
                            </div>
                            <div className="space-y-2">
                                <Label>Affected user name, email or ID</Label>
                                <Input aria-label="Security affected user search" value={securityUser} onChange={(e) => {  setSecurityUser(e.target.value); }} placeholder="Name or email" />
                            </div>
                            <div className="space-y-2">
                                <Label>Tenant</Label>
                                <Input aria-label="Security tenant ID" value={capabilities.allowedTenantId ?? securityTenant} onChange={(e) => {  setSecurityTenant(e.target.value); }} disabled={Boolean(capabilities.allowedTenantId)} placeholder="tenant-id" />
                            </div>
                        </div>

                        <p className="text-xs text-muted-foreground">User search uses current affected-user names/emails or the saved user ID. It does not identify the actor who caused the event. Dates are UTC. History may change while you browse.</p>
                        <Button type="button" variant="outline" disabled={!eventOwner.ready || securityEventsQ.isFetching} onClick={() => refreshEventHistory("security")}>Refresh security history</Button>
                        {securityEventsQ.isPending ? <LoadingState title="Loading security events" description="Fetching authentication and abuse detection signals." /> : null}
                        {securityEventsQ.isError ? <ErrorState title="Security events unavailable" message={securityEventsQ.error instanceof Error ? securityEventsQ.error.message : "Could not load security events from the backend."} onRetry={() => securityEventsQ.refetch().then(() => {})} /> : null}
                        {!securityEventsQ.isPending && !securityEventsQ.isError ? (
                            securityEventsQ.data?.items.length ? (
                                <div className="space-y-4">
                                    <div className="overflow-hidden rounded-2xl border border-border">
                                        <div className="grid gap-3 border-b border-border bg-muted/30 px-4 py-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground md:grid-cols-[170px_130px_1fr_180px_1.2fr]">
                                            <div>Timestamp</div>
                                            <div>Severity</div>
                                            <div>Event</div>
                                            <div>Affected user</div>
                                            <div>Evidence</div>
                                        </div>
                                        {securityEventsQ.data.items.map((event) => (
                                            <div key={event.id} className="grid gap-3 border-b border-border/70 px-4 py-4 last:border-b-0 md:grid-cols-[170px_130px_1fr_180px_1.2fr]">
                                                <div className="text-sm text-muted-foreground">{formatDateTime(event.timestamp)}</div>
                                                <div><SeverityPill severity={event.severity} /></div>
                                                <div className="space-y-1">
                                                    <div className="text-sm font-semibold text-foreground">{event.title || "Untitled security event"}</div>
                                                    <div className="text-xs text-muted-foreground">{event.eventType} / {event.status.replaceAll("_", " ")}</div>
                                                    <div className="text-sm text-muted-foreground">{event.description ?? "Description unavailable"}</div>
                                                </div>
                                                <div className="text-sm text-muted-foreground">{event.subjectId ?? "No affected user recorded"}</div>
                                                <div className="flex flex-wrap gap-2">
                                                    {metadataEntries(event.metadata).length ? metadataEntries(event.metadata).map((entry) => (
                                                        <span key={`${event.id}-${entry.key}`} className="rounded-full bg-muted px-2.5 py-1 text-xs text-muted-foreground">
                                                            {entry.key}: {entry.value}
                                                        </span>
                                                    )) : <span className="text-sm text-muted-foreground">No metadata</span>}
                                                </div>
                                            </div>
                                        ))}
                                    </div>

                                </div>
                            ) : (
                                <EmptyState title="No security events found" message="No matching security signals were returned for the active filters." kind="search" />
                            )
                        ) : null}
                        <PaginationControls
                            page={securityPage}
                            disabled={securityEventsQ.isFetching}
                            canNext={!securityEventsQ.isError && Boolean(securityEventsQ.data?.hasNext)}
                            onPrevious={() => setSecurityNavigation({ ...securityPosition, index: Math.max(0, securityPosition.index - 1) })}
                            onNext={() => {
                                const boundary = securityEventsQ.data?.nextBoundary;
                                if (!boundary || securityEventsQ.isFetching || securityEventsQ.isError) return;
                                setSecurityNavigation({ scope: securityScope, index: securityPosition.index + 1,
                                    boundaries: [...securityPosition.boundaries.slice(0, securityPosition.index + 1), boundary] });
                            }}
                        />
                    </CardContent>
                </Card>
            ) : null}

            {activeTab === "suspensions" && (capabilities.canManagePartnerSuspensions || capabilities.canManageTenantSuspensions) ? (
                <div className="grid gap-6 xl:grid-cols-2">
                    {capabilities.canManagePartnerSuspensions ? (
                        <Card className="border-border bg-background/80">
                            <CardHeader>
                                <CardTitle className="text-lg">Partner Suspension Controls</CardTitle>
                                <CardDescription>Review formal holds, appeals and exact restorations. Independent or historical account restrictions are not automatically reactivated.</CardDescription>
                            </CardHeader>
                            <CardContent className="space-y-5">
                                <div className="grid gap-4 md:grid-cols-[1fr_180px]">
                                    <div className="space-y-2">
                                        <Label htmlFor="admin-partner-search">Search partners</Label>
                                        <Input id="admin-partner-search" ref={partnerSearchRef} value={partnerQuery} onChange={(e) => { setPartnerPage(1); setPartnerQuery(e.target.value); }} placeholder="Partner name or ID" />
                                    </div>
                                    <div className="space-y-2">
                                        <Label>Status</Label>
                                        <Select value={partnerStatus} onChange={(next) => { setPartnerPage(1); setPartnerStatus(next); }} ariaLabel="Partner status">
                                            <option value="all">All statuses</option>
                                            <option value="active">Active</option>
                                            <option value="suspended">Suspended</option>
                                            <option value="pending_deletion">Pending deletion</option>
                                            <option value="unknown">Unknown</option>
                                        </Select>
                                    </div>
                                </div>
                                {partnersQ.isPending ? <LoadingState title="Loading partners" description="Fetching partner account scopes." /> : null}
                                {partnersQ.isError ? <ErrorState title="Partner controls unavailable" message="Could not load partner suspension data." onRetry={() => eventOwner.ready ? partnersQ.refetch().then(() => {}) : Promise.resolve()} /> : null}
                                {!partnersQ.isPending && !partnersQ.isError ? (
                                    partnersQ.data?.items.length ? (
                                        <div className="space-y-3">
                                            {partnersQ.data.items.map((partner) => {
                                                return (
                                                    <div key={partner.id} className="rounded-2xl border border-border bg-muted/20 p-4">
                                                        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                                                            <div className="min-w-0">
                                                                <div className="flex flex-wrap items-center gap-2">
                                                                    <div className="text-sm font-semibold text-foreground">{partner.name}</div>
                                                                    <StatusBadge status={partner.status} />
                                                                </div>
                                                                <div className="mt-1 text-sm text-muted-foreground">Partner ID: {partner.id}</div>
                                                                <div className="mt-1 text-xs text-muted-foreground">
                                                                    Updated {formatDateTime(partner.updatedAt)}{typeof partner.tenantCount === "number" ? ` • ${partner.tenantCount} tenants` : ""}
                                                                </div>
                                                            </div>
                                                            <Button
                                                                type="button"
                                                                variant="outline"
                                                                disabled={!eventOwner.ready}
                                                                onClick={event => openPartnerReview(partner, event.currentTarget)}
                                                            >
                                                                <ShieldAlert className="h-4 w-4" />
                                                                Manage suspension
                                                            </Button>
                                                        </div>
                                                    </div>
                                                );
                                            })}
                                            <PaginationControls
                                                page={partnerPage}
                                                disabled={partnersQ.isFetching}
                                                canNext={canGoNext(partnersQ.data?.total, partnerPage, RESOURCE_PAGE_SIZE, partnersQ.data?.items.length ?? 0)}
                                                onPrevious={() => setPartnerPage((current) => Math.max(1, current - 1))}
                                                onNext={() => setPartnerPage((current) => current + 1)}
                                            />
                                        </div>
                                    ) : (
                                        <EmptyState title="No partners found" message="No partner records matched the active filters." kind="search" />
                                    )
                                ) : null}
                            </CardContent>
                        </Card>
                    ) : null}

                    {capabilities.canManageTenantSuspensions ? (
                        <Card className="border-border bg-background/80">
                            <CardHeader>
                                <CardTitle className="text-lg">Tenant subscription controls</CardTitle>
                                <CardDescription>Change subscription status. Formal holds and other account restrictions are separate and still apply.</CardDescription>
                            </CardHeader>
                            <CardContent className="space-y-5">
                                <div className="grid gap-4 md:grid-cols-3">
                                    <div className="space-y-2">
                                        <Label>Search tenants</Label>
                                        <Input value={tenantQuery} onChange={(e) => { setTenantPage(1); setTenantQuery(e.target.value); }} placeholder="Tenant business name" />
                                    </div>
                                    <div className="space-y-2">
                                        <Label>Subscription status</Label>
                                        <Select value={tenantStatus} onChange={(next) => { setTenantPage(1); setTenantStatus(next); }} ariaLabel="Tenant subscription status">
                                            <option value="all">All statuses</option>
                                            <option value="active">Active</option>
                                            <option value="suspended">Suspended</option>
                                        </Select>
                                    </div>
                                </div>
                                {tenantsQ.isPending ? <LoadingState title="Loading tenants" description="Fetching tenant subscription status." /> : null}
                                {tenantsQ.isError ? <ErrorState title="Tenant controls unavailable" message="Could not load tenant subscription data." onRetry={retryTenants} /> : null}
                                {!tenantsQ.isPending && !tenantsQ.isError ? (
                                    tenantsQ.data?.items.length ? (
                                        <div className="space-y-3">
                                            {tenantsQ.data.items.map((tenant) => {
                                                const pending = mutationBusyId === tenant.id;
                                                const suspended = tenant.status === "suspended";
                                                return (
                                                    <div key={tenant.id} className="rounded-2xl border border-border bg-muted/20 p-4">
                                                        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                                                            <div className="min-w-0">
                                                                <div className="flex flex-wrap items-center gap-2">
                                                                    <div className="text-sm font-semibold text-foreground">{tenant.name}</div>
                                                                    <StatusBadge status={tenant.status} prefix="Subscription: " />
                                                                </div>
                                                                <div className="mt-1 text-sm text-muted-foreground">Tenant ID: {tenant.id}</div>
                                                                <div className="mt-1 text-xs text-muted-foreground">
                                                                    {tenant.partnerId ? `Partner ${tenant.partnerId} • ` : ""}Updated {formatDateTime(tenant.updatedAt)}
                                                                </div>
                                                            </div>
                                                            <Button
                                                                type="button"
                                                                variant={suspended ? "outline" : "destructive"}
                                                                disabled={pending || tenantsQ.isFetching || tenant.updatedAt === undefined || !["active", "suspended"].includes(tenant.status)}
                                                                onClick={(event) => openTenantReview(tenant, event.currentTarget)}
                                                            >
                                                                {pending ? <RefreshCw className="h-4 w-4 animate-spin" /> : suspended ? <RefreshCw className="h-4 w-4" /> : <ShieldAlert className="h-4 w-4" />}
                                                                {suspended ? "Resume subscription" : "Suspend subscription"}
                                                            </Button>
                                                        </div>
                                                    </div>
                                                );
                                            })}
                                            <PaginationControls
                                                page={tenantPage}
                                                disabled={tenantsQ.isFetching}
                                                canNext={canGoNext(tenantsQ.data?.total, tenantPage, RESOURCE_PAGE_SIZE, tenantsQ.data?.items.length ?? 0)}
                                                onPrevious={() => setTenantPage((current) => Math.max(1, current - 1))}
                                                onNext={() => setTenantPage((current) => current + 1)}
                                            />
                                        </div>
                                    ) : (
                                        <EmptyState title="No tenants found" message="No tenant records matched the active filters." kind="search" />
                                    )
                                ) : null}
                            </CardContent>
                        </Card>
                    ) : null}
                </div>
            ) : null}

            {activeTab === "config" ? (
                <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
                    <Card className="border-border bg-background/80">
                        <CardHeader>
                            <CardTitle className="text-lg">Public Environment Configuration</CardTitle>
                            <CardDescription>Client-safe configuration surfaced to the browser. No private tokens, API keys, or credentials are rendered.</CardDescription>
                        </CardHeader>
                        <CardContent className="grid gap-4 md:grid-cols-2">
                            <div className="rounded-2xl border border-border bg-muted/20 p-4">
                                <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Environment</div>
                                <div className="mt-2 text-sm font-semibold text-foreground">{config.appEnvironment}</div>
                            </div>
                            <div className="rounded-2xl border border-border bg-muted/20 p-4">
                                <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">API Base URL</div>
                                <div className="mt-2 break-all text-sm font-semibold text-foreground">{config.apiBaseUrl}</div>
                            </div>
                            <div className="rounded-2xl border border-border bg-muted/20 p-4">
                                <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Build Commit</div>
                                <div className="mt-2 text-sm font-semibold text-foreground">{config.commitSha ?? "Unavailable"}</div>
                            </div>
                            <div className="rounded-2xl border border-border bg-muted/20 p-4">
                                <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Telemetry</div>
                                <div className="mt-2 text-sm font-semibold text-foreground">{config.sentry.enabled ? "Enabled" : "Disabled"}</div>
                                <div className="mt-1 text-xs text-muted-foreground">
                                    DSN {config.sentry.dsnConfigured ? "configured" : "not configured"} • traces {config.sentry.tracesSampleRate} • profiles {config.sentry.profilesSampleRate}
                                </div>
                            </div>
                        </CardContent>
                    </Card>
                    <Card className="border-border bg-background/80">
                        <CardHeader>
                            <CardTitle className="text-lg">Safety Rules</CardTitle>
                            <CardDescription>Frontend configuration stays declarative and public-only.</CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="rounded-2xl border border-emerald-500/20 bg-emerald-500/10 p-4 text-sm text-emerald-800">
                                API base URLs and runtime flags are centralized through a single public config helper.
                            </div>
                            <div className="rounded-2xl border border-amber-500/20 bg-amber-500/10 p-4 text-sm text-amber-800">
                                This UI intentionally avoids rendering raw internal payloads and strips common secret-like metadata keys before display.
                            </div>
                            <div className="rounded-2xl border border-border bg-muted/20 p-4">
                                <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
                                    <Filter className="h-4 w-4" />
                                    Public keys in use
                                </div>
                                <div className="mt-3 flex flex-wrap gap-2">
                                    {config.publicEnvKeys.map((key) => (
                                        <span key={key} className="rounded-full border border-border bg-background px-2.5 py-1 text-xs text-muted-foreground">
                                            {key}
                                        </span>
                                    ))}
                                </div>
                            </div>
                        </CardContent>
                    </Card>
                </div>
            ) : null}

            {tenantReview && <ConfirmDialog
                key={tenantReview.id}
                open
                description="This changes subscription status. Separate account restrictions still apply."
                onOpenChange={(next) => {
                    if (!next) closeTenantReview(tenantReview);
                }}
                intent={tenantReview.kind === "tenant_suspend" ? "suspend" : "reactivate"}
                title={tenantReview.kind === "tenant_suspend" ? "Suspend subscription" : "Resume subscription"}
                confirmLabel={tenantReview.kind === "tenant_suspend" ? "Confirm subscription suspension" : "Confirm subscription resume"}
                warningText={
                    tenantReview.kind === "tenant_suspend"
                        ? `This marks the subscription for ${tenantReview.item.name} suspended after confirmation. Formal holds and other account restrictions remain separate.`
                        : `This marks the subscription for ${tenantReview.item.name} active after confirmation. It does not remove formal holds or other account restrictions.`
                }
                onConfirm={() => runConfirmedAction(tenantReview)}
                showReasonInput
                reasonValue={confirmReason}
                onReasonChange={(value) => { if (ownsTenantReview(tenantReview)) setConfirmReason(value); }}
            />}
            {formalPartner && formalPartner.owner === partnerOwner && user?.role === "platform_admin" && activeTab === "suspensions" && <FormalPartnerHoldPanel
                partnerId={formalPartner.partner.id} partnerName={formalPartner.partner.name} operatorId={formalPartner.operatorId}
                identityGeneration={formalPartner.generation} onClose={() => closePartnerReview(formalPartner)}
                onChanged={() => partnerChanged(formalPartner)}
            />}
        </div>
    );
}
