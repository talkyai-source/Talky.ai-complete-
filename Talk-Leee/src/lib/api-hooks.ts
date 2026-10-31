"use client";

import { useSyncExternalStore } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { backendApi, type AdminPartnerListInput, type AdminResourceListInput, type AuditLogsListInput, type SecurityEventsListInput } from "@/lib/backend-api";
import { dashboardApi, type Call, type CallListFilters } from "@/lib/dashboard-api";
import { isActiveCallStatus } from "@/lib/call-history-workflow";
import { outboundCampaignsOnly } from "@/lib/campaign-direction";
import { extendedApi } from "@/lib/extended-api";
import type { AssistantRun, AssistantRunStatus, CalendarEvent, Connector, Reminder } from "@/lib/models";
import { emailAuditStore } from "@/lib/email-audit";
import { notificationsStore } from "@/lib/notifications";
import { useNotificationMutation } from "@/lib/notification-mutations";
import { captureException } from "@/lib/monitoring";
import { useAuth } from "@/lib/auth-context";
import { getRequestIdentityGeneration, subscribeRequestIdentityGeneration } from "@/lib/http-client";

function randomId() {
    if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
    return `tmp_${Math.random().toString(16).slice(2)}_${Date.now().toString(16)}`;
}

export const queryKeys = {
    health: () => ["health"] as const,
    connectors: () => ["connectors"] as const,
    connectorStatuses: () => ["connectorStatuses"] as const,
    salesforceSettings: () => ["salesforceSettings"] as const,
    connectorAccounts: (connectorId?: string) => ["connectorAccounts", connectorId ?? "all"] as const,
    meetings: () => ["meetings"] as const,
    calendarEvents: () => ["calendarEvents"] as const,
    reminders: () => ["reminders"] as const,
    emailTemplates: () => ["emailTemplates"] as const,
    assistantRuns: (key: string) => ["assistantRuns", key] as const,
    auditLogs: (key: string) => ["auditLogs", key] as const,
    securityEvents: (key: string) => ["securityEvents", key] as const,
    adminPartners: (key: string) => ["adminPartners", key] as const,
    adminTenants: (key: string) => ["adminTenants", key] as const,
    dashboardSummary: () => ["dashboardSummary"] as const,
    campaigns: () => ["campaigns"] as const,
    campaign: (id: string) => ["campaign", id] as const,
    campaignStats: (id: string) => ["campaignStats", id] as const,
    campaignContacts: (campaignId: string, page: number, pageSize: number) => ["campaignContacts", campaignId, page, pageSize] as const,
    calls: (page: number, pageSize: number, filters?: CallListFilters) => ["calls", page, pageSize, filters?.direction ?? "all", filters?.inboundCampaignId ?? "all"] as const,
    call: (id: string) => ["call", id] as const,
    callTranscript: (id: string, format: "json" | "text") => ["callTranscript", id, format] as const,
    callSummary: (id: string) => ["callSummary", id] as const,
    callAnalytics: (fromDate: string | undefined, toDate: string | undefined, groupBy: "day" | "week" | "month") =>
        ["callAnalytics", fromDate ?? "none", toDate ?? "none", groupBy] as const,
    recordings: (page: number, pageSize: number) => ["recordings", page, pageSize] as const,
};

export function useHealth() {
    return useQuery({
        queryKey: queryKeys.health(),
        queryFn: ({ signal }) => backendApi.health(signal),
        refetchInterval: 30_000,
    });
}

export function useConnectors() {
    return useQuery({
        queryKey: queryKeys.connectors(),
        queryFn: ({ signal }) => backendApi.connectors.list(signal),
    });
}

export function useConnectorStatuses(options?: { enabled?: boolean }) {
    return useQuery({
        queryKey: queryKeys.connectorStatuses(),
        queryFn: ({ signal }) => backendApi.connectors.status(signal),
        refetchInterval: () => {
            if (typeof document === "undefined") return 10_000;
            if (document.visibilityState === "hidden") return false;
            return 10_000;
        },
        refetchOnReconnect: true,
        refetchOnWindowFocus: true,
        refetchOnMount: "always",
        staleTime: 0,
        retry: 2,
        enabled: options?.enabled ?? true,
    });
}

export function useSalesforceSettings(options?: { enabled?: boolean }) {
    return useQuery({
        queryKey: queryKeys.salesforceSettings(),
        queryFn: ({ signal }) => backendApi.salesforce.settings(signal),
        enabled: options?.enabled ?? true,
        staleTime: 5_000,
        retry: 1,
    });
}

export function useUpdateSalesforceSettings() {
    const qc = useQueryClient();
    return useNotificationMutation({
        mutationFn: backendApi.salesforce.updateSettings,
        onSuccess: (data) => {
            qc.setQueryData(queryKeys.salesforceSettings(), data);
            notificationsStore.create({ type: "success", title: "Salesforce settings saved", message: "Sync and callback settings updated." });
        },
        onError: (err) => {
            notificationsStore.create({ type: "error", title: "Could not save Salesforce settings", message: err instanceof Error ? err.message : "Request failed" });
        },
    });
}

export function useRevealSalesforceWebhookToken() {
    const qc = useQueryClient();
    return useNotificationMutation({
        mutationFn: backendApi.salesforce.revealWebhookToken,
        onSuccess: (data) => {
            qc.setQueryData(queryKeys.salesforceSettings(), data);
        },
        onError: (err) => {
            notificationsStore.create({ type: "error", title: "Could not reveal webhook token", message: err instanceof Error ? err.message : "Request failed" });
        },
    });
}

export function useRotateSalesforceWebhookToken() {
    const qc = useQueryClient();
    return useNotificationMutation({
        mutationFn: backendApi.salesforce.rotateWebhookToken,
        onSuccess: (data) => {
            qc.setQueryData(queryKeys.salesforceSettings(), data);
            notificationsStore.create({ type: "success", title: "Webhook token rotated", message: "Update the URL in your Salesforce Flow / Outbound Message." });
        },
        onError: (err) => {
            notificationsStore.create({ type: "error", title: "Could not rotate webhook token", message: err instanceof Error ? err.message : "Request failed" });
        },
    });
}

export function useTestSalesforceConnection() {
    return useNotificationMutation({
        mutationFn: backendApi.salesforce.test,
        onError: (err) => {
            notificationsStore.create({ type: "error", title: "Salesforce test failed", message: err instanceof Error ? err.message : "Request failed" });
        },
    });
}

export function useImportSalesforcePeople() {
    const qc = useQueryClient();
    return useNotificationMutation({
        mutationFn: backendApi.salesforce.importPeople,
        onSuccess: (data) => {
            void qc.invalidateQueries({ queryKey: queryKeys.campaigns() });
            void qc.invalidateQueries({ queryKey: queryKeys.campaign(data.campaign_id) });
            notificationsStore.create({
                type: "success",
                title: "Salesforce import finished",
                message: `${data.imported} imported, ${data.revived} revived, ${data.duplicates_skipped} already present, ${data.invalid} invalid.`,
            });
        },
        onError: (err) => {
            notificationsStore.create({ type: "error", title: "Salesforce import failed", message: err instanceof Error ? err.message : "Request failed" });
        },
    });
}

export function useAuthorizeConnector() {
    return useNotificationMutation({
        mutationFn: backendApi.connectors.authorize,
        retry: (failureCount, err) => {
            if (failureCount >= 2) return false;
            if (typeof err === "object" && err !== null && "status" in (err as object)) {
                const status = (err as { status?: number }).status;
                if (typeof status === "number" && status >= 400 && status < 500) return false;
            }
            return true;
        },
        onError: () => {
            notificationsStore.create({ type: "error", title: "Authorization failed", message: "Could not start the connection flow." });
        },
    });
}

export function useDisconnectConnector() {
    const qc = useQueryClient();
    return useNotificationMutation({
        mutationFn: backendApi.connectors.disconnect,
        onError: () => {
            notificationsStore.create({ type: "error", title: "Disconnect failed", message: "Could not disconnect. Please try again." });
        },
        onSuccess: () => {
            notificationsStore.create({ type: "success", title: "Disconnected", message: "Connector disconnected successfully." });
        },
        onSettled: () => {
            void qc.invalidateQueries({ queryKey: queryKeys.connectorStatuses() });
        },
    });
}

export function useCreateConnector() {
    const qc = useQueryClient();
    return useNotificationMutation({
        mutationFn: backendApi.connectors.create,
        onMutate: async (input) => {
            await qc.cancelQueries({ queryKey: queryKeys.connectors() });
            const prev = qc.getQueryData<Awaited<ReturnType<typeof backendApi.connectors.list>>>(queryKeys.connectors());
            const optimistic: Connector = {
                id: randomId(),
                name: input.name,
                type: input.type,
                config: input.config,
                createdAt: new Date().toISOString(),
            };
            if (prev) qc.setQueryData(queryKeys.connectors(), { items: [optimistic, ...prev.items] });
            else qc.setQueryData(queryKeys.connectors(), { items: [optimistic] });
            return { prev };
        },
        onError: (_err, _input, ctx) => {
            if (ctx?.prev) qc.setQueryData(queryKeys.connectors(), ctx.prev);
            notificationsStore.create({ type: "error", title: "Connector failed", message: "Could not create connector." });
        },
        onSuccess: () => {
            notificationsStore.create({ type: "success", title: "Connector created", message: "Connector saved successfully." });
        },
        onSettled: () => {
            void qc.invalidateQueries({ queryKey: queryKeys.connectors() });
        },
    });
}

export function useMeetings(options?: { enabled?: boolean }) {
    return useQuery({
        queryKey: queryKeys.meetings(),
        queryFn: ({ signal }) => backendApi.meetings.list(signal),
        enabled: options?.enabled ?? true,
    });
}

export function useCalendarEvents(options?: { enabled?: boolean }) {
    return useQuery({
        queryKey: queryKeys.calendarEvents(),
        queryFn: ({ signal }) => backendApi.calendarEvents.list(undefined, signal),
        enabled: options?.enabled ?? true,
    });
}

export function useCreateCalendarEvent() {
    const qc = useQueryClient();
    return useNotificationMutation({
        mutationFn: backendApi.calendarEvents.create,
        onMutate: async (input) => {
            await qc.cancelQueries({ queryKey: queryKeys.calendarEvents() });
            const prev = qc.getQueryData<Awaited<ReturnType<typeof backendApi.calendarEvents.list>>>(queryKeys.calendarEvents());
            const optimistic: CalendarEvent = {
                id: randomId(),
                title: input.title,
                startTime: input.startTime,
                endTime: input.endTime,
                status: "scheduled",
                leadId: input.leadId,
                leadName: input.leadName,
                notes: input.notes,
                participants: input.leadName ? [{ id: undefined, name: input.leadName, email: undefined, role: undefined }] : undefined,
            };
            if (prev) qc.setQueryData(queryKeys.calendarEvents(), { items: [optimistic, ...prev.items] });
            else qc.setQueryData(queryKeys.calendarEvents(), { items: [optimistic] });
            return { prev };
        },
        onError: (_err, _input, ctx) => {
            if (ctx?.prev) qc.setQueryData(queryKeys.calendarEvents(), ctx.prev);
            notificationsStore.create({ type: "error", title: "Meeting failed", message: "Could not create meeting." });
        },
        onSuccess: () => {
            notificationsStore.create({ type: "success", title: "Meeting created", message: "Meeting saved successfully." });
        },
        onSettled: () => {
            void qc.invalidateQueries({ queryKey: queryKeys.calendarEvents() });
        },
    });
}

export function useCancelCalendarEvent() {
    const qc = useQueryClient();
    return useNotificationMutation({
        mutationFn: backendApi.calendarEvents.cancel,
        onMutate: async (id) => {
            await qc.cancelQueries({ queryKey: queryKeys.calendarEvents() });
            const prev = qc.getQueryData<Awaited<ReturnType<typeof backendApi.calendarEvents.list>>>(queryKeys.calendarEvents());
            if (prev) qc.setQueryData(queryKeys.calendarEvents(), { items: prev.items.filter((m) => m.id !== id) });
            return { prev };
        },
        onError: (_err, _id, ctx) => {
            if (ctx?.prev) qc.setQueryData(queryKeys.calendarEvents(), ctx.prev);
            notificationsStore.create({ type: "error", title: "Cancel failed", message: "Could not cancel meeting. Please try again." });
        },
        onSuccess: () => {
            notificationsStore.create({ type: "success", title: "Meeting cancelled", message: "Meeting removed." });
        },
        onSettled: () => {
            void qc.invalidateQueries({ queryKey: queryKeys.calendarEvents() });
        },
    });
}

export function useReminders(options?: { enabled?: boolean }) {
    return useQuery({
        queryKey: queryKeys.reminders(),
        queryFn: ({ signal }) => backendApi.reminders.list(signal),
        enabled: options?.enabled ?? true,
        refetchInterval: () => {
            if (typeof document === "undefined") return 5000;
            if (document.visibilityState === "hidden") return false;
            return 5000;
        },
    });
}

export function useEmailTemplates(options?: { enabled?: boolean }) {
    return useQuery({
        queryKey: queryKeys.emailTemplates(),
        queryFn: ({ signal }) => backendApi.email.templates.list(signal),
        staleTime: 60_000,
        retry: 2,
        enabled: options?.enabled ?? true,
    });
}

export function useSendEmail() {
    return useNotificationMutation({
        mutationFn: backendApi.email.send,
        onMutate: async (input) => {
            const auditId = emailAuditStore.createAttempt({ to: input.to, templateId: input.templateId, subject: input.subject });
            return { auditId };
        },
        onError: (err, _input, ctx) => {
            const msg = err instanceof Error ? err.message : "Could not send email.";
            if (ctx?.auditId) emailAuditStore.markFailed(ctx.auditId, { errorMessage: msg });
            notificationsStore.create({ type: "error", title: "Send failed", message: msg });
        },
        onSuccess: (res, _input, ctx) => {
            if (ctx?.auditId) emailAuditStore.markSuccess(ctx.auditId, { messageId: res.messageId, providerStatus: res.status });
            notificationsStore.create({ type: "success", title: "Email sent", message: res.messageId ? `Message ID: ${res.messageId}` : "Delivery request accepted." });
        },
    });
}

export function useCreateReminder() {
    const qc = useQueryClient();
    return useNotificationMutation({
        mutationFn: backendApi.reminders.create,
        onMutate: async (input) => {
            await qc.cancelQueries({ queryKey: queryKeys.reminders() });
            const prev = qc.getQueryData<Awaited<ReturnType<typeof backendApi.reminders.list>>>(queryKeys.reminders());
            if (prev) {
                const optimistic: Reminder = {
                    id: randomId(),
                    content: input.content,
                    status: "scheduled",
                    channel: input.channel,
                    scheduledAt: input.scheduledAt,
                    meetingId: input.meetingId,
                    meetingTitle: input.meetingTitle,
                    contactId: input.contactId,
                    contactName: input.contactName,
                    toEmail: input.toEmail,
                    toPhone: input.toPhone,
                    createdAt: new Date().toISOString(),
                };
                qc.setQueryData(queryKeys.reminders(), {
                    items: [optimistic, ...prev.items],
                });
            }
            return { prev };
        },
        onError: (err, _vars, ctx) => {
            if (ctx?.prev) qc.setQueryData(queryKeys.reminders(), ctx.prev);
            captureException(err, { area: "reminders", action: "create" });
            notificationsStore.create({ type: "error", title: "Create failed", message: "Could not create reminder." });
        },
        onSuccess: (created) => {
            qc.setQueryData<Awaited<ReturnType<typeof backendApi.reminders.list>>>(queryKeys.reminders(), (cur) => {
                if (!cur) return cur;
                const withoutTmp = cur.items.filter((r) => !r.id.startsWith("tmp_"));
                return { items: [created, ...withoutTmp] };
            });
            notificationsStore.create({ type: "success", title: "Reminder created", message: "Scheduled." });
            try {
                const bc = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("reminders") : null;
                bc?.postMessage({ type: "reminders:updated" });
                bc?.close();
            } catch {
            }
        },
        onSettled: () => {
            void qc.invalidateQueries({ queryKey: queryKeys.reminders() });
        },
    });
}

export function useCancelReminder() {
    const qc = useQueryClient();
    return useNotificationMutation({
        mutationFn: backendApi.reminders.cancel,
        onMutate: async (id) => {
            await qc.cancelQueries({ queryKey: queryKeys.reminders() });
            const prev = qc.getQueryData<Awaited<ReturnType<typeof backendApi.reminders.list>>>(queryKeys.reminders());
            if (prev) {
                const now = new Date().toISOString();
                qc.setQueryData(queryKeys.reminders(), {
                    items: prev.items.map((r) =>
                        r.id === id ? ({ ...r, status: "canceled", canceledAt: now, updatedAt: now } satisfies Reminder) : r
                    ),
                });
            }
            return { prev };
        },
        onError: (err, _id, ctx) => {
            if (ctx?.prev) qc.setQueryData(queryKeys.reminders(), ctx.prev);
            captureException(err, { area: "reminders", action: "cancel" });
            notificationsStore.create({ type: "error", title: "Cancel failed", message: "Could not cancel reminder." });
        },
        onSuccess: (updated) => {
            qc.setQueryData<Awaited<ReturnType<typeof backendApi.reminders.list>>>(queryKeys.reminders(), (cur) => {
                if (!cur) return cur;
                return { items: cur.items.map((r) => (r.id === updated.id ? updated : r)) };
            });
            notificationsStore.create({ type: "success", title: "Reminder cancelled", message: "Canceled." });
            try {
                const bc = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("reminders") : null;
                bc?.postMessage({ type: "reminders:updated" });
                bc?.close();
            } catch {
            }
        },
        onSettled: () => {
            void qc.invalidateQueries({ queryKey: queryKeys.reminders() });
        },
    });
}

export type AssistantRunsQuery = {
    page: number;
    pageSize: number;
    statuses: AssistantRunStatus[];
    actionType?: string;
    leadId?: string;
    from?: string;
    to?: string;
    sortKey?: "createdAt" | "startedAt" | "completedAt" | "status" | "actionType";
    sortDir?: "asc" | "desc";
};

function assistantRunsKey(q: AssistantRunsQuery) {
    const stable = {
        ...q,
        statuses: [...q.statuses].sort(),
    };
    return JSON.stringify(stable);
}

export function useAssistantRuns(q: AssistantRunsQuery, options?: { enabled?: boolean }) {
    return useQuery({
        queryKey: queryKeys.assistantRuns(assistantRunsKey(q)),
        enabled: options?.enabled ?? true,
        queryFn: ({ signal }) => backendApi.assistantRuns.list(q, signal),
        placeholderData: keepPreviousData,
        refetchInterval: (query) => {
            const data = query.state.data as { items: AssistantRun[] } | undefined;
            const needs = data?.items?.some((r) => ["pending", "running", "in_progress", "scheduled"].includes(r.status));
            return needs ? 3000 : false;
        },
    });
}

export function useAdminEventReadScope() {
    const { user, loading } = useAuth();
    const generation = useSyncExternalStore(subscribeRequestIdentityGeneration, getRequestIdentityGeneration, () => 0);
    return {
        key: JSON.stringify([user?.id ?? null, user?.role ?? null, user?.tenant_id ?? null, user?.partner_id ?? null, generation, loading ? "checking" : "ready"]),
        ready: Boolean(user) && !loading,
    };
}

export function useAuditLogs(q: AuditLogsListInput, options?: { enabled?: boolean }) {
    const scope = useAdminEventReadScope();
    return useQuery({
        queryKey: queryKeys.auditLogs(JSON.stringify([scope.key, q])),
        queryFn: ({ signal }) => backendApi.admin.auditLogs.list(q, signal),
        enabled: scope.ready && (options?.enabled ?? true),
    });
}

export function useSecurityEvents(q: SecurityEventsListInput, options?: { enabled?: boolean }) {
    const scope = useAdminEventReadScope();
    return useQuery({
        queryKey: queryKeys.securityEvents(JSON.stringify([scope.key, q])),
        queryFn: ({ signal }) => backendApi.admin.securityEvents.list(q, signal),
        enabled: scope.ready && (options?.enabled ?? true),
    });
}

export function useAdminPartners(q: AdminPartnerListInput, options?: { enabled?: boolean }) {
    const scope = useAdminEventReadScope();
    return useQuery({
        queryKey: queryKeys.adminPartners(JSON.stringify([scope.key, q])),
        queryFn: ({ signal }) => backendApi.admin.partners.list(q, signal),
        enabled: scope.ready && (options?.enabled ?? true),
    });
}

export function useAdminTenants(q: AdminResourceListInput, options?: { enabled?: boolean }) {
    const scope = useAdminEventReadScope();
    return useQuery({
        queryKey: queryKeys.adminTenants(JSON.stringify([scope.key, q])),
        queryFn: ({ signal }) => backendApi.admin.tenants.list(q, signal),
        enabled: scope.ready && (options?.enabled ?? true),
    });
}

type TenantSubscriptionMutationInput = Parameters<typeof backendApi.admin.tenants.suspend>[0] & {
    // Local completion ownership only; neither value is sent to the API.
    ownsOperation: () => boolean;
    queryKey: ReturnType<typeof queryKeys.adminTenants>;
};

function useTenantSubscriptionMutation(kind: "suspend" | "reactivate") {
    const qc = useQueryClient();
    return useMutation({
        // Platform admins may have no tenant notification scope. Business
        // completion belongs to the review; optional notifications keep their
        // own captured identity fence.
        onMutate: () => notificationsStore.capture(),
        mutationFn: ({ tenantId, expectedUpdatedAt, reason, ownsOperation }: TenantSubscriptionMutationInput) => {
            if (!ownsOperation()) throw new Error("The tenant review is no longer current. Review the subscription again.");
            return backendApi.admin.tenants[kind]({ tenantId, expectedUpdatedAt, reason });
        },
        onSuccess: (updated, variables, notifier) => {
            if (!variables.ownsOperation()) return;
            notifier?.create({ type: "success", title: kind === "suspend" ? "Tenant subscription suspended" : "Tenant subscription active",
                message: `Subscription ${updated.id} is ${updated.status}. Separate account and formal restrictions still apply.` });
        },
        onError: (_error, variables, notifier) => {
            if (!variables.ownsOperation()) return;
            notifier?.create({ type: "error", title: "Tenant subscription change not confirmed", message: "Refresh and review the subscription before retrying." });
        },
        onSettled: (_data, _error, variables) => {
            if (!variables.ownsOperation()) return;
            // Other cached pages become stale, without publishing a receipt
            // into their values. Refetch only the initiating current query.
            void qc.invalidateQueries({ queryKey: ["adminTenants"], refetchType: "none" }).catch(() => {});
            void qc.invalidateQueries({ queryKey: variables.queryKey, exact: true }).catch(() => {});
            void qc.invalidateQueries({ queryKey: ["auditLogs"], refetchType: "none" }).catch(() => {});
        },
    });
}

export function useSuspendTenant() {
    return useTenantSubscriptionMutation("suspend");
}

export function useReactivateTenant() {
    return useTenantSubscriptionMutation("reactivate");
}

export function useDashboardSummary() {
    return useQuery({
        queryKey: queryKeys.dashboardSummary(),
        queryFn: () => dashboardApi.getDashboardSummary(),
    });
}

export function useCampaigns() {
    return useQuery({
        queryKey: queryKeys.campaigns(),
        queryFn: async () => {
            const data = await dashboardApi.listCampaigns();
            return data.campaigns;
        },
    });
}

/** Outbound-only view of the campaign list. Inbound campaigns are managed
 *  on /inbound-campaigns; the outbound endpoints 409 on them. */
export function useOutboundCampaigns() {
    return useQuery({
        queryKey: queryKeys.campaigns(),
        queryFn: async () => {
            const data = await dashboardApi.listCampaigns();
            return data.campaigns;
        },
        select: outboundCampaignsOnly,
    });
}

export function useCampaign(id: string | undefined) {
    return useQuery({
        queryKey: id ? queryKeys.campaign(id) : (["campaign", "missing"] as const),
        queryFn: async () => {
            if (!id) throw new Error("Missing campaign id");
            const data = await dashboardApi.getCampaign(id);
            return data.campaign;
        },
        enabled: Boolean(id),
    });
}

export function useCampaignStats(id: string | undefined) {
    return useQuery({
        queryKey: id ? queryKeys.campaignStats(id) : (["campaignStats", "missing"] as const),
        queryFn: async () => {
            if (!id) throw new Error("Missing campaign id");
            return dashboardApi.getCampaignStats(id);
        },
        enabled: Boolean(id),
    });
}

export function useCampaignContacts(campaignId: string | undefined, page: number, pageSize: number) {
    return useQuery({
        queryKey: campaignId ? queryKeys.campaignContacts(campaignId, page, pageSize) : (["campaignContacts", "missing", page, pageSize] as const),
        queryFn: async () => {
            if (!campaignId) throw new Error("Missing campaign id");
            return dashboardApi.listContacts(campaignId, page, pageSize);
        },
        enabled: Boolean(campaignId),
    });
}

export function useCalls(page: number, pageSize: number, filters: CallListFilters = {}) {
    return useQuery<{ calls: Call[]; total: number }>({
        queryKey: queryKeys.calls(page, pageSize, filters),
        queryFn: () => dashboardApi.listCalls(page, pageSize, filters),
        placeholderData: keepPreviousData,
        // Keep the history live only while a call is actually changing state.
        // This lets the page raise its one-time review prompt immediately after
        // an active call ends without polling settled history forever.
        refetchInterval: (query) => {
            const data = query.state.data as { calls: Call[]; total: number } | undefined;
            const hasActiveCall = data?.calls.some((call) => isActiveCallStatus(call.status));
            return hasActiveCall ? 3_000 : false;
        },
    });
}

export function useCall(id: string | undefined) {
    return useQuery({
        queryKey: id ? queryKeys.call(id) : (["call", "missing"] as const),
        queryFn: async () => {
            if (!id) throw new Error("Missing call id");
            return dashboardApi.getCall(id);
        },
        enabled: Boolean(id),
    });
}

export function useCallTranscript(id: string | undefined, format: "json" | "text") {
    return useQuery({
        queryKey: id ? queryKeys.callTranscript(id, format) : (["callTranscript", "missing", format] as const),
        queryFn: async () => {
            if (!id) throw new Error("Missing call id");
            return dashboardApi.getCallTranscript(id, format);
        },
        enabled: Boolean(id),
    });
}

export function useCallSummary(id: string | undefined) {
    return useQuery({
        queryKey: id ? queryKeys.callSummary(id) : (["callSummary", "missing"] as const),
        queryFn: async () => {
            if (!id) throw new Error("Missing call id");
            return dashboardApi.getCallSummary(id);
        },
        enabled: Boolean(id),
        // Summary generation can take ~2-4s on first call; don't flash stale
        staleTime: 60_000,
    });
}

export function useCallAnalytics(fromDate: string | undefined, toDate: string | undefined, groupBy: "day" | "week" | "month") {
    return useQuery({
        queryKey: queryKeys.callAnalytics(fromDate, toDate, groupBy),
        queryFn: () => extendedApi.getCallAnalytics(fromDate, toDate, groupBy),
    });
}

export function useRecordings(page: number, pageSize: number) {
    return useQuery({
        queryKey: queryKeys.recordings(page, pageSize),
        queryFn: () => extendedApi.listRecordings(undefined, page, pageSize),
        placeholderData: keepPreviousData,
    });
}

export function useUploadContactsCsv() {
    return useNotificationMutation({
        mutationFn: (payload: { campaignId: string; file: File; skipDuplicates?: boolean }) =>
            extendedApi.uploadCSV(payload.campaignId, payload.file, payload.skipDuplicates ?? true),
        onError: () => {
            notificationsStore.create({ type: "error", title: "Upload failed", message: "Could not upload CSV." });
        },
        onSuccess: (data) => {
            notificationsStore.create({
                type: data.failed > 0 ? "warning" : "success",
                title: "Upload complete",
                message: data.failed > 0 ? "Imported with some errors." : "Contacts imported successfully.",
            });
        },
    });
}
