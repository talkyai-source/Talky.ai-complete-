"use client";

import { useMemo, useState } from "react";
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
import { useAdminPartners, useAdminTenants, useAuditLogs, useReactivatePartner, useReactivateTenant, useSecurityEvents, useSuspendPartner, useSuspendTenant } from "@/lib/api-hooks";
import { publicAppConfig } from "@/lib/env";
import { cn } from "@/lib/utils";
import { useSuspensionState } from "@/components/admin/suspension-state-provider";
import type { PartnerSummary, TenantSummary } from "@/lib/models";

type AdminTab = "audit" | "security" | "suspensions" | "config";

type PendingAction =
    | { kind: "partner_suspend"; item: PartnerSummary }
    | { kind: "partner_reactivate"; item: PartnerSummary }
    | { kind: "tenant_suspend"; item: TenantSummary }
    | { kind: "tenant_reactivate"; item: TenantSummary };

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

function formatActor(actor: { name?: string; email?: string } | undefined) {
    if (!actor) return "System";
    return actor.name?.trim() || actor.email?.trim() || "System";
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

function SeverityPill({ severity }: { severity: "low" | "medium" | "high" }) {
    return (
        <span
            className={cn(
                "inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold",
                severity === "high"
                    ? "bg-red-500/15 text-red-700"
                    : severity === "medium"
                      ? "bg-amber-500/15 text-amber-700"
                      : "bg-emerald-500/15 text-emerald-700"
            )}
        >
            {severity}
        </span>
    );
}

function StatusBadge({ status }: { status: "active" | "suspended" }) {
    return (
        <span
            className={cn(
                "inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold",
                status === "suspended" ? "bg-red-500/15 text-red-700" : "bg-emerald-500/15 text-emerald-700"
            )}
        >
            {status === "suspended" ? "Suspended" : "Active"}
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

export function AdminOperationsConsole() {
    const { user } = useAuth();
    const { applyScopedUpdate } = useSuspensionState();
    const capabilities = useMemo(() => getAdminUiCapabilities(user), [user]);
    const config = useMemo(() => publicAppConfig(), []);
    const [activeTab, setActiveTab] = useState<AdminTab>("audit");
    const [confirmAction, setConfirmAction] = useState<PendingAction | null>(null);

    const [auditPage, setAuditPage] = useState(1);
    const [auditEventType, setAuditEventType] = useState("all");
    const [auditFrom, setAuditFrom] = useState("");
    const [auditTo, setAuditTo] = useState("");
    const [auditUser, setAuditUser] = useState("");
    const [auditTenant, setAuditTenant] = useState("");
    const [auditPartner, setAuditPartner] = useState("");

    const [securityPage, setSecurityPage] = useState(1);
    const [securityEventType, setSecurityEventType] = useState("all");
    const [securitySeverity, setSecuritySeverity] = useState("all");
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
    const [tenantPartnerFilter, setTenantPartnerFilter] = useState("");

    const [confirmReason, setConfirmReason] = useState("");

    const auditQuery = useMemo(
        () => ({
            page: auditPage,
            pageSize: AUDIT_PAGE_SIZE,
            eventType: auditEventType === "all" ? undefined : auditEventType,
            from: auditFrom ? `${auditFrom}T00:00:00.000Z` : undefined,
            to: auditTo ? `${auditTo}T23:59:59.999Z` : undefined,
            userQuery: auditUser.trim() || undefined,
            tenantId: (capabilities.allowedTenantId ?? auditTenant.trim()) || undefined,
            partnerId: (capabilities.allowedPartnerId ?? auditPartner.trim()) || undefined,
        }),
        [auditEventType, auditFrom, auditPage, auditPartner, auditTenant, auditTo, auditUser, capabilities.allowedPartnerId, capabilities.allowedTenantId]
    );

    const securityQuery = useMemo(
        () => ({
            page: securityPage,
            pageSize: AUDIT_PAGE_SIZE,
            eventType: securityEventType === "all" ? undefined : securityEventType,
            severity: securitySeverity === "all" ? undefined : (securitySeverity as "low" | "medium" | "high"),
            from: securityFrom ? `${securityFrom}T00:00:00.000Z` : undefined,
            to: securityTo ? `${securityTo}T23:59:59.999Z` : undefined,
            userQuery: securityUser.trim() || undefined,
            tenantId: (capabilities.allowedTenantId ?? securityTenant.trim()) || undefined,
            partnerId: capabilities.allowedPartnerId ?? undefined,
        }),
        [capabilities.allowedPartnerId, capabilities.allowedTenantId, securityEventType, securityFrom, securityPage, securitySeverity, securityTenant, securityTo, securityUser]
    );

    const partnerListQuery = useMemo(
        () => ({
            page: partnerPage,
            pageSize: RESOURCE_PAGE_SIZE,
            query: partnerQuery.trim() || undefined,
            status: partnerStatus === "all" ? undefined : (partnerStatus as "active" | "suspended"),
        }),
        [partnerPage, partnerQuery, partnerStatus]
    );

    const tenantListQuery = useMemo(
        () => ({
            page: tenantPage,
            pageSize: RESOURCE_PAGE_SIZE,
            query: tenantQuery.trim() || undefined,
            status: tenantStatus === "all" ? undefined : (tenantStatus as "active" | "suspended"),
            partnerId: capabilities.allowedPartnerId ?? (tenantPartnerFilter.trim() || undefined),
        }),
        [capabilities.allowedPartnerId, tenantPage, tenantPartnerFilter, tenantQuery, tenantStatus]
    );

    const auditLogsQ = useAuditLogs(auditQuery, { enabled: capabilities.canViewAuditLogs });
    const securityEventsQ = useSecurityEvents(securityQuery, { enabled: capabilities.canViewSecurityEvents });
    const partnersQ = useAdminPartners(partnerListQuery, { enabled: capabilities.canManagePartnerSuspensions && activeTab === "suspensions" });
    const tenantsQ = useAdminTenants(tenantListQuery, { enabled: capabilities.canManageTenantSuspensions && activeTab === "suspensions" });

    const suspendPartnerM = useSuspendPartner();
    const reactivatePartnerM = useReactivatePartner();
    const suspendTenantM = useSuspendTenant();
    const reactivateTenantM = useReactivateTenant();

    const mutationBusyId =
        suspendPartnerM.variables?.partnerId ??
        reactivatePartnerM.variables?.partnerId ??
        suspendTenantM.variables?.tenantId ??
        reactivateTenantM.variables?.tenantId ??
        null;

    async function runConfirmedAction() {
        if (!confirmAction) return;

        const reasonForRequest = confirmReason.trim() || undefined;

        if (confirmAction.kind === "partner_suspend") {
            const updated = await suspendPartnerM.mutateAsync({ partnerId: confirmAction.item.id, reason: reasonForRequest });
            applyScopedUpdate({ targetType: "partner", targetId: updated.id, status: "suspended" });
            setConfirmReason("");
            return;
        }

        if (confirmAction.kind === "partner_reactivate") {
            const updated = await reactivatePartnerM.mutateAsync({ partnerId: confirmAction.item.id, reason: reasonForRequest });
            applyScopedUpdate({ targetType: "partner", targetId: updated.id, status: "active" });
            setConfirmReason("");
            return;
        }

        if (confirmAction.kind === "tenant_suspend") {
            const updated = await suspendTenantM.mutateAsync({ tenantId: confirmAction.item.id, reason: reasonForRequest });
            applyScopedUpdate({ targetType: "tenant", targetId: updated.id, status: "suspended" });
            setConfirmReason("");
            return;
        }

        const updated = await reactivateTenantM.mutateAsync({ tenantId: confirmAction.item.id, reason: reasonForRequest });
        applyScopedUpdate({ targetType: "tenant", targetId: updated.id, status: "active" });
        setConfirmReason("");
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
                        onChange={setActiveTab}
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
                            {capabilities.allowedPartnerId ? `Scoped to partner ${capabilities.allowedPartnerId}.` : "Cross-partner visibility enabled."}
                        </div>
                    </div>
                    <div className="rounded-2xl border border-border bg-muted/20 p-4">
                        <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
                            <UserRound className="h-4 w-4" />
                            Audit Access
                        </div>
                        <div className="mt-2 text-sm text-muted-foreground">{capabilities.canViewAuditLogs ? "Audit views are available for your role." : "Audit views are restricted."}</div>
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
                                        setAuditPage(1);
                                        setAuditEventType(next);
                                    }}
                                    ariaLabel="Audit event type"
                                >
                                    <option value="all">All events</option>
                                    <option value="login">Login events</option>
                                    <option value="role_change">Role changes</option>
                                    <option value="billing_change">Billing changes</option>
                                    <option value="suspension">Suspension events</option>
                                </Select>
                            </div>
                            <div className="space-y-2">
                                <Label>From</Label>
                                <Input type="date" value={auditFrom} onChange={(e) => { setAuditPage(1); setAuditFrom(e.target.value); }} />
                            </div>
                            <div className="space-y-2">
                                <Label>To</Label>
                                <Input type="date" value={auditTo} onChange={(e) => { setAuditPage(1); setAuditTo(e.target.value); }} />
                            </div>
                            <div className="space-y-2">
                                <Label>User</Label>
                                <Input value={auditUser} onChange={(e) => { setAuditPage(1); setAuditUser(e.target.value); }} placeholder="Name or email" />
                            </div>
                            <div className="space-y-2">
                                <Label>Tenant</Label>
                                <Input value={capabilities.allowedTenantId ?? auditTenant} onChange={(e) => { setAuditPage(1); setAuditTenant(e.target.value); }} disabled={Boolean(capabilities.allowedTenantId)} placeholder="tenant-id" />
                            </div>
                            <div className="space-y-2">
                                <Label>Partner</Label>
                                <Input value={capabilities.allowedPartnerId ?? auditPartner} onChange={(e) => { setAuditPage(1); setAuditPartner(e.target.value); }} disabled={Boolean(capabilities.allowedPartnerId)} placeholder="partner-id" />
                            </div>
                        </div>

                        {auditLogsQ.isLoading ? <LoadingState title="Loading audit logs" description="Fetching backend activity history." /> : null}
                        {auditLogsQ.isError ? <ErrorState title="Audit logs unavailable" message="Could not load audit activity from the backend." onRetry={() => auditLogsQ.refetch().then(() => {})} /> : null}
                        {!auditLogsQ.isLoading && !auditLogsQ.isError ? (
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
                                                    <div className="text-sm text-muted-foreground">{formatActor(event.actor)}</div>
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
                                    <PaginationControls
                                        page={auditPage}
                                        disabled={auditLogsQ.isFetching}
                                        canNext={canGoNext(auditLogsQ.data?.total, auditPage, AUDIT_PAGE_SIZE, auditLogsQ.data?.items.length ?? 0)}
                                        onPrevious={() => setAuditPage((current) => Math.max(1, current - 1))}
                                        onNext={() => setAuditPage((current) => current + 1)}
                                    />
                                </div>
                            ) : (
                                <EmptyState title="No audit activity found" message="Adjust filters or widen the date range to see more events." kind="search" />
                            )
                        ) : null}
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
                                <Select value={securityEventType} onChange={(next) => { setSecurityPage(1); setSecurityEventType(next); }} ariaLabel="Security event type">
                                    <option value="all">All events</option>
                                    <option value="failed_login">Failed logins</option>
                                    <option value="rate_limit_violation">Rate limit violations</option>
                                    <option value="suspicious_activity">Suspicious activity</option>
                                </Select>
                            </div>
                            <div className="space-y-2">
                                <Label>Severity</Label>
                                <Select value={securitySeverity} onChange={(next) => { setSecurityPage(1); setSecuritySeverity(next); }} ariaLabel="Security severity">
                                    <option value="all">All severities</option>
                                    <option value="low">Low</option>
                                    <option value="medium">Medium</option>
                                    <option value="high">High</option>
                                </Select>
                            </div>
                            <div className="space-y-2">
                                <Label>From</Label>
                                <Input type="date" value={securityFrom} onChange={(e) => { setSecurityPage(1); setSecurityFrom(e.target.value); }} />
                            </div>
                            <div className="space-y-2">
                                <Label>To</Label>
                                <Input type="date" value={securityTo} onChange={(e) => { setSecurityPage(1); setSecurityTo(e.target.value); }} />
                            </div>
                            <div className="space-y-2">
                                <Label>User</Label>
                                <Input value={securityUser} onChange={(e) => { setSecurityPage(1); setSecurityUser(e.target.value); }} placeholder="Name or email" />
                            </div>
                            <div className="space-y-2">
                                <Label>Tenant</Label>
                                <Input value={capabilities.allowedTenantId ?? securityTenant} onChange={(e) => { setSecurityPage(1); setSecurityTenant(e.target.value); }} disabled={Boolean(capabilities.allowedTenantId)} placeholder="tenant-id" />
                            </div>
                        </div>

                        {securityEventsQ.isLoading ? <LoadingState title="Loading security events" description="Fetching authentication and abuse detection signals." /> : null}
                        {securityEventsQ.isError ? <ErrorState title="Security events unavailable" message="Could not load security events from the backend." onRetry={() => securityEventsQ.refetch().then(() => {})} /> : null}
                        {!securityEventsQ.isLoading && !securityEventsQ.isError ? (
                            securityEventsQ.data?.items.length ? (
                                <div className="space-y-4">
                                    <div className="overflow-hidden rounded-2xl border border-border">
                                        <div className="grid gap-3 border-b border-border bg-muted/30 px-4 py-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground md:grid-cols-[170px_130px_1fr_180px_1.2fr]">
                                            <div>Timestamp</div>
                                            <div>Severity</div>
                                            <div>Event</div>
                                            <div>Actor</div>
                                            <div>Metadata</div>
                                        </div>
                                        {securityEventsQ.data.items.map((event) => (
                                            <div key={event.id} className="grid gap-3 border-b border-border/70 px-4 py-4 last:border-b-0 md:grid-cols-[170px_130px_1fr_180px_1.2fr]">
                                                <div className="text-sm text-muted-foreground">{formatDateTime(event.timestamp)}</div>
                                                <div><SeverityPill severity={event.severity} /></div>
                                                <div className="space-y-1">
                                                    <div className="text-sm font-semibold text-foreground">{event.eventType}</div>
                                                    <div className="text-sm text-muted-foreground">{formatTarget(event.target)}</div>
                                                </div>
                                                <div className="text-sm text-muted-foreground">{formatActor(event.actor)}</div>
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
                                    <PaginationControls
                                        page={securityPage}
                                        disabled={securityEventsQ.isFetching}
                                        canNext={canGoNext(securityEventsQ.data?.total, securityPage, AUDIT_PAGE_SIZE, securityEventsQ.data?.items.length ?? 0)}
                                        onPrevious={() => setSecurityPage((current) => Math.max(1, current - 1))}
                                        onNext={() => setSecurityPage((current) => current + 1)}
                                    />
                                </div>
                            ) : (
                                <EmptyState title="No security events found" message="No matching security signals were returned for the active filters." kind="search" />
                            )
                        ) : null}
                    </CardContent>
                </Card>
            ) : null}

            {activeTab === "suspensions" && (capabilities.canManagePartnerSuspensions || capabilities.canManageTenantSuspensions) ? (
                <div className="grid gap-6 xl:grid-cols-2">
                    {capabilities.canManagePartnerSuspensions ? (
                        <Card className="border-border bg-background/80">
                            <CardHeader>
                                <CardTitle className="text-lg">Partner Suspension Controls</CardTitle>
                                <CardDescription>Suspend or reactivate entire partner scopes with explicit confirmation before the request is sent.</CardDescription>
                            </CardHeader>
                            <CardContent className="space-y-5">
                                <div className="grid gap-4 md:grid-cols-[1fr_180px]">
                                    <div className="space-y-2">
                                        <Label>Search partners</Label>
                                        <Input value={partnerQuery} onChange={(e) => { setPartnerPage(1); setPartnerQuery(e.target.value); }} placeholder="Partner name or ID" />
                                    </div>
                                    <div className="space-y-2">
                                        <Label>Status</Label>
                                        <Select value={partnerStatus} onChange={(next) => { setPartnerPage(1); setPartnerStatus(next); }} ariaLabel="Partner status">
                                            <option value="all">All statuses</option>
                                            <option value="active">Active</option>
                                            <option value="suspended">Suspended</option>
                                        </Select>
                                    </div>
                                </div>
                                {partnersQ.isLoading ? <LoadingState title="Loading partners" description="Fetching partner account scopes." /> : null}
                                {partnersQ.isError ? <ErrorState title="Partner controls unavailable" message="Could not load partner suspension data." onRetry={() => partnersQ.refetch().then(() => {})} /> : null}
                                {!partnersQ.isLoading && !partnersQ.isError ? (
                                    partnersQ.data?.items.length ? (
                                        <div className="space-y-3">
                                            {partnersQ.data.items.map((partner) => {
                                                const pending = mutationBusyId === partner.id;
                                                const suspended = partner.status === "suspended";
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
                                                                variant={suspended ? "outline" : "destructive"}
                                                                disabled={pending}
                                                                onClick={() => setConfirmAction({ kind: suspended ? "partner_reactivate" : "partner_suspend", item: partner })}
                                                            >
                                                                {pending ? <RefreshCw className="h-4 w-4 animate-spin" /> : suspended ? <RefreshCw className="h-4 w-4" /> : <ShieldAlert className="h-4 w-4" />}
                                                                {suspended ? "Reactivate partner" : "Suspend partner"}
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
                                <CardTitle className="text-lg">Tenant Suspension Controls</CardTitle>
                                <CardDescription>Apply tenant-level blocks instantly in the UI while deferring source-of-truth enforcement to backend APIs.</CardDescription>
                            </CardHeader>
                            <CardContent className="space-y-5">
                                <div className="grid gap-4 md:grid-cols-3">
                                    <div className="space-y-2">
                                        <Label>Search tenants</Label>
                                        <Input value={tenantQuery} onChange={(e) => { setTenantPage(1); setTenantQuery(e.target.value); }} placeholder="Tenant name or ID" />
                                    </div>
                                    <div className="space-y-2">
                                        <Label>Status</Label>
                                        <Select value={tenantStatus} onChange={(next) => { setTenantPage(1); setTenantStatus(next); }} ariaLabel="Tenant status">
                                            <option value="all">All statuses</option>
                                            <option value="active">Active</option>
                                            <option value="suspended">Suspended</option>
                                        </Select>
                                    </div>
                                    <div className="space-y-2">
                                        <Label>Partner filter</Label>
                                        <Input
                                            value={capabilities.allowedPartnerId ?? tenantPartnerFilter}
                                            onChange={(e) => { setTenantPage(1); setTenantPartnerFilter(e.target.value); }}
                                            disabled={Boolean(capabilities.allowedPartnerId)}
                                            placeholder="partner-id"
                                        />
                                    </div>
                                </div>
                                {tenantsQ.isLoading ? <LoadingState title="Loading tenants" description="Fetching tenant account scopes." /> : null}
                                {tenantsQ.isError ? <ErrorState title="Tenant controls unavailable" message="Could not load tenant suspension data." onRetry={() => tenantsQ.refetch().then(() => {})} /> : null}
                                {!tenantsQ.isLoading && !tenantsQ.isError ? (
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
                                                                    <StatusBadge status={tenant.status} />
                                                                </div>
                                                                <div className="mt-1 text-sm text-muted-foreground">Tenant ID: {tenant.id}</div>
                                                                <div className="mt-1 text-xs text-muted-foreground">
                                                                    {tenant.partnerId ? `Partner ${tenant.partnerId} • ` : ""}Updated {formatDateTime(tenant.updatedAt)}
                                                                </div>
                                                            </div>
                                                            <Button
                                                                type="button"
                                                                variant={suspended ? "outline" : "destructive"}
                                                                disabled={pending}
                                                                onClick={() => setConfirmAction({ kind: suspended ? "tenant_reactivate" : "tenant_suspend", item: tenant })}
                                                            >
                                                                {pending ? <RefreshCw className="h-4 w-4 animate-spin" /> : suspended ? <RefreshCw className="h-4 w-4" /> : <ShieldAlert className="h-4 w-4" />}
                                                                {suspended ? "Reactivate tenant" : "Suspend tenant"}
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

            <ConfirmDialog
                open={Boolean(confirmAction)}
                onOpenChange={(next) => {
                    if (!next) {
                        setConfirmAction(null);
                        setConfirmReason("");
                    }
                }}
                intent={
                    confirmAction?.kind === "partner_suspend" || confirmAction?.kind === "tenant_suspend"
                        ? "suspend"
                        : confirmAction?.kind === "partner_reactivate" || confirmAction?.kind === "tenant_reactivate"
                          ? "reactivate"
                          : "cancel"
                }
                title={
                    confirmAction?.kind === "partner_suspend"
                        ? "Suspend partner"
                        : confirmAction?.kind === "tenant_suspend"
                          ? "Suspend tenant"
                          : confirmAction?.kind === "partner_reactivate"
                            ? "Reactivate partner"
                            : "Reactivate tenant"
                }
                warningText={
                    confirmAction?.kind === "partner_suspend"
                        ? `Suspending ${confirmAction.item.name} immediately blocks partner-scoped activity in the UI after the backend confirms the request.`
                        : confirmAction?.kind === "tenant_suspend"
                          ? `Suspending ${confirmAction.item.name} immediately blocks tenant-scoped activity in the UI after the backend confirms the request.`
                          : confirmAction?.kind === "partner_reactivate"
                            ? `Reactivating ${confirmAction.item.name} restores partner access after the backend confirms the request.`
                            : confirmAction
                              ? `Reactivating ${confirmAction.item.name} restores tenant access after the backend confirms the request.`
                              : ""
                }
                onConfirm={runConfirmedAction}
                showReasonInput={confirmAction?.kind.includes("suspend") || confirmAction?.kind.includes("reactivate")}
                reasonValue={confirmReason}
                onReasonChange={setConfirmReason}
            />
        </div>
    );
}
