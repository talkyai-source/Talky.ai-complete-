"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { backendApi } from "@/lib/backend-api";
import { dashboardApi, type Call } from "@/lib/dashboard-api";
import { extendedApi } from "@/lib/extended-api";
import type { AssistantRun, CalendarEvent, Connector, Reminder } from "@/lib/models";
import { emailAuditStore } from "@/lib/email-audit";
import { notificationsStore } from "@/lib/notifications";
import { captureException } from "@/lib/monitoring";

function randomId() {
    if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
    return `tmp_${Math.random().toString(16).slice(2)}_${Date.now().toString(16)}`;
}

export const queryKeys = {
    health: () => ["health"] as const,
    connectors: () => ["connectors"] as const,
    connectorStatuses: () => ["connectorStatuses"] as const,
    connectorAccounts: (connectorId?: string) => ["connectorAccounts", connectorId ?? "all"] as const,
    meetings: () => ["meetings"] as const,
    calendarEvents: () => ["calendarEvents"] as const,
    reminders: () => ["reminders"] as const,
    emailTemplates: () => ["emailTemplates"] as const,
    assistantActions: () => ["assistantActions"] as const,
    assistantRuns: (key: string) => ["assistantRuns", key] as const,
    dashboardSummary: () => ["dashboardSummary"] as const,
    campaigns: () => ["campaigns"] as const,
    campaign: (id: string) => ["campaign", id] as const,
    campaignStats: (id: string) => ["campaignStats", id] as const,
    campaignContacts: (campaignId: string, page: number, pageSize: number) => ["campaignContacts", campaignId, page, pageSize] as const,
    calls: (page: number, pageSize: number) => ["calls", page, pageSize] as const,
    call: (id: string) => ["call", id] as const,
    callTranscript: (id: string, format: "json" | "text") => ["callTranscript", id, format] as const,
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

export function useAuthorizeConnector() {
    return useMutation({
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
    return useMutation({
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
    return useMutation({
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
    return useMutation({
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
    return useMutation({
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
    return useMutation({
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
    return useMutation({
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
    return useMutation({
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

export function useAssistantActions() {
    return useQuery({
        queryKey: queryKeys.assistantActions(),
        queryFn: ({ signal }) => backendApi.assistantActions.list(signal),
    });
}

export type AssistantRunsQuery = {
    page: number;
    pageSize: number;
    statuses: Array<"pending" | "in_progress" | "completed" | "failed">;
    actionType?: string;
    leadId?: string;
    from?: string;
    to?: string;
    sortKey?: "createdAt" | "startedAt" | "completedAt" | "status" | "actionType" | "source" | "leadId";
    sortDir?: "asc" | "desc";
};

function assistantRunsKey(q: AssistantRunsQuery) {
    const stable = {
        ...q,
        statuses: [...q.statuses].sort(),
    };
    return JSON.stringify(stable);
}

export function useAssistantRuns(q: AssistantRunsQuery) {
    return useQuery({
        queryKey: queryKeys.assistantRuns(assistantRunsKey(q)),
        queryFn: ({ signal }) => backendApi.assistantRuns.list(q, signal),
        placeholderData: keepPreviousData,
        refetchInterval: (query) => {
            const data = query.state.data as { items: AssistantRun[] } | undefined;
            const needs = data?.items?.some((r) => r.status === "pending" || r.status === "in_progress");
            return needs ? 3000 : false;
        },
    });
}

export function useAssistantExecute() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: backendApi.assistant.execute,
        onMutate: async (input) => {
            const optimistic: AssistantRun = {
                id: randomId(),
                actionType: input.actionType,
                source: input.source ?? "dashboard",
                leadId: input.leadId,
                status: "pending",
                createdAt: new Date().toISOString(),
                result: undefined,
                requestPayload: { action_type: input.actionType, source: input.source ?? "dashboard", lead_id: input.leadId, context: input.context ?? {} },
                responsePayload: undefined,
                error: undefined,
            };

            const queries = qc.getQueriesData({ queryKey: ["assistantRuns"] });
            const touched: Array<{ key: unknown[]; prev: unknown }> = [];
            for (const [key, prev] of queries) {
                if (!Array.isArray(key)) continue;
                touched.push({ key, prev });
                qc.setQueryData(key, (cur: unknown) => {
                    if (!cur || typeof cur !== "object") return cur;
                    const obj = cur as { items?: AssistantRun[]; total?: number };
                    const items = Array.isArray(obj.items) ? obj.items : [];
                    const nextItems = [optimistic, ...items].slice(0, 50);
                    const total = typeof obj.total === "number" ? obj.total + 1 : obj.total;
                    return { ...obj, items: nextItems, total };
                });
            }
            return { touched };
        },
        onError: (_err, _input, ctx) => {
            for (const t of ctx?.touched ?? []) {
                qc.setQueryData(t.key, t.prev);
            }
            notificationsStore.create({ type: "error", title: "Execution failed", message: "Could not start the action." });
        },
        onSuccess: () => {
            notificationsStore.create({ type: "success", title: "Action started", message: "The action has been queued." });
        },
        onSettled: () => {
            void qc.invalidateQueries({ queryKey: ["assistantRuns"] });
        },
    });
}

export function useAssistantPlan() {
    return useMutation({
        mutationFn: backendApi.assistant.plan,
        onError: () => {
            notificationsStore.create({ type: "error", title: "Planning failed", message: "Could not generate a plan." });
        },
    });
}

export function useAssistantRunRetry() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: backendApi.assistantRuns.retry,
        onError: () => {
            notificationsStore.create({ type: "error", title: "Retry failed", message: "Could not retry. Please try again." });
        },
        onSuccess: () => {
            notificationsStore.create({ type: "success", title: "Retry started", message: "A new run was created." });
        },
        onSettled: () => {
            void qc.invalidateQueries({ queryKey: ["assistantRuns"] });
        },
    });
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

export function useCalls(page: number, pageSize: number) {
    return useQuery<{ calls: Call[]; total: number }>({
        queryKey: queryKeys.calls(page, pageSize),
        queryFn: () => dashboardApi.listCalls(page, pageSize),
        placeholderData: keepPreviousData,
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
    return useMutation({
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
