"use client";

import { useRef } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
    inboundApi,
    inboundErrorCode,
    inboundErrorStatus,
    createInboundIdempotencyKey,
    INBOUND_RETRY_WINDOW_MS,
    type InboundCampaign,
    type InboundCampaignInput,
} from "@/lib/inbound-api";
import { inboundStateForError } from "@/lib/inbound/inbound-types";
import { fetchEffectivePermissions } from "@/lib/inbound-permissions";
import { notificationsStore } from "@/lib/notifications";
import { dashboardApi } from "@/lib/dashboard-api";

export const inboundQueryKeys = {
    all: ["inbound-campaigns"] as const,
    lists: ["inbound-campaigns", "list"] as const,
    list: (includeArchived: boolean) => ["inbound-campaigns", "list", { includeArchived }] as const,
    detail: (id: string) => ["inbound-campaigns", id] as const,
    readiness: (id: string) => ["inbound-campaigns", id, "readiness"] as const,
    phoneNumbers: ["inbound-phone-numbers", "availability"] as const,
    permissions: ["rbac", "me", "permissions"] as const,
    controls: ["inbound-campaigns", "controls"] as const,
    capabilities: (configId?: string) => [
        "inbound-campaigns",
        "capabilities",
        configId ?? "new",
    ] as const,
    // Tenant-wide, not inbound-specific — same `/campaigns/minutes/status`
    // figure the outbound Start button reads — but the quota it reports also
    // gates inbound admission (inbound_admission.py:1071-1087), so the
    // inbound campaign page reads it too.
    minutesStatus: ["billing", "minutes-status"] as const,
};

function campaignNumber(campaign: InboundCampaign): string {
    return campaign.phone_number?.masked_number ?? "The assigned number";
}

/**
 * Reads the server's own code and status off a rejected request and resolves
 * the single named state the notification copy switches on.
 *
 * `inboundStateForError` is the one mapper; this wrapper exists only so the
 * two `onError` handlers below extract the code and status identically.
 */
function inboundErrorState(error: unknown) {
    return inboundStateForError(inboundErrorStatus(error), inboundErrorCode(error));
}

export function commitInboundCampaignCache(qc: ReturnType<typeof useQueryClient>, campaign: InboundCampaign) {
    qc.setQueryData(inboundQueryKeys.detail(campaign.id), campaign);
    qc.setQueriesData<InboundCampaign[]>({ queryKey: inboundQueryKeys.lists }, (current) => {
        if (!current) return current;
        return current.some((entry) => entry.id === campaign.id)
            ? current.map((entry) => entry.id === campaign.id ? campaign : entry)
            : [campaign, ...current];
    });
    qc.setQueryData(inboundQueryKeys.readiness(campaign.id), campaign.readiness);
}

export function useInboundCampaigns(enabled = true, includeArchived = false) {
    return useQuery({
        queryKey: inboundQueryKeys.list(includeArchived),
        queryFn: ({ signal }) => inboundApi.list({ includeArchived, signal }),
        placeholderData: keepPreviousData,
        enabled,
    });
}

export function useInboundCampaign(id: string | undefined, enabled = true) {
    return useQuery({
        queryKey: id ? inboundQueryKeys.detail(id) : (["inbound-campaigns", "missing"] as const),
        queryFn: ({ signal }) => {
            if (!id) throw new Error("Missing inbound campaign id");
            return inboundApi.get(id, signal);
        },
        enabled: Boolean(id) && enabled,
    });
}

export function useInboundReadiness(id: string | undefined, enabled = true) {
    return useQuery({
        queryKey: id ? inboundQueryKeys.readiness(id) : (["inbound-campaigns", "missing", "readiness"] as const),
        queryFn: ({ signal }) => {
            if (!id) throw new Error("Missing inbound campaign id");
            return inboundApi.readiness(id, signal);
        },
        enabled: Boolean(id) && enabled,
    });
}

export function useInboundPhoneNumbers(enabled = true) {
    return useQuery({
        queryKey: inboundQueryKeys.phoneNumbers,
        queryFn: ({ signal }) => inboundApi.availablePhoneNumbers(signal),
        enabled,
    });
}

export function useTenantInboundControls(enabled = true) {
    return useQuery({
        queryKey: inboundQueryKeys.controls,
        queryFn: ({ signal }) => inboundApi.getControls(signal),
        enabled,
    });
}

/** Best-effort: a quota-status hiccup must not block the campaign page. */
export function useMinutesStatus(enabled = true) {
    return useQuery({
        queryKey: inboundQueryKeys.minutesStatus,
        queryFn: () => dashboardApi.getMinutesStatus(),
        enabled,
        retry: false,
    });
}

export function useInboundRuntimeCapabilities(configId?: string, enabled = true) {
    return useQuery({
        queryKey: inboundQueryKeys.capabilities(configId),
        queryFn: ({ signal }) => inboundApi.getCapabilities(configId, signal),
        enabled,
        retry: false,
    });
}

export function useSetTenantInboundControls() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: (input: { inbound_enabled: boolean; expected_version: number; reason: string }) => inboundApi.setControls(input),
        onSuccess: (controls) => {
            qc.setQueryData(inboundQueryKeys.controls, controls);
            notificationsStore.create({
                type: controls.inbound_enabled ? "success" : "warning",
                title: controls.inbound_enabled ? "Inbound calling enabled" : "Inbound calling disabled",
                message: controls.inbound_enabled
                    ? "New calls may be admitted when each campaign also passes readiness."
                    : "New tenant inbound calls now fail closed before answer.",
            });
        },
        onError: (error) => {
            void qc.invalidateQueries({ queryKey: inboundQueryKeys.controls });
            notificationsStore.create({
                type: "error",
                title: "Inbound control was not changed",
                message: error instanceof Error ? error.message : "Reload the current control version and try again.",
            });
        },
    });
}

export function useEffectivePermissions() {
    return useQuery({
        queryKey: inboundQueryKeys.permissions,
        queryFn: ({ signal }) => fetchEffectivePermissions(signal),
        staleTime: 60_000,
        retry: false,
    });
}

export function useCreateInboundCampaign() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: ({ input, didNumber }: { input: InboundCampaignInput; didNumber: string }) => inboundApi.create(input, didNumber),
        onSuccess: (created) => {
            commitInboundCampaignCache(qc, created);
            void qc.invalidateQueries({ queryKey: inboundQueryKeys.all });
            notificationsStore.create({
                type: "success",
                title: "Draft created",
                message: "The number remains inactive until readiness passes and activation is confirmed.",
            });
        },
    });
}

export function useUpdateInboundCampaign(id: string) {
    const qc = useQueryClient();
    const retryKeys = useRef(new Map<string, { update: string; assignment: string; expiresAt: number }>());
    return useMutation({
        mutationFn: async (variables: {
            input: InboundCampaignInput;
            expectedVersion: number;
            assignment?: { didNumber: string; sipTrunkId: string; reason: string };
        }) => {
            const signature = JSON.stringify(variables);
            let keys = retryKeys.current.get(signature);
            if (keys && Date.now() >= keys.expiresAt) {
                throw new Error("The safe retry window expired. Reload the latest campaign before starting a new edit.");
            }
            if (!keys) {
                if (retryKeys.current.size >= 32) {
                    throw new Error("Too many unresolved edits. Reload the latest campaign before continuing.");
                }
                keys = {
                    update: createInboundIdempotencyKey(),
                    assignment: createInboundIdempotencyKey(),
                    expiresAt: Date.now() + INBOUND_RETRY_WINDOW_MS,
                };
                retryKeys.current.set(signature, keys);
            }
            const updated = await inboundApi.update(id, variables.input, variables.expectedVersion, keys.update);
            if (!variables.assignment) return updated;
            return inboundApi.assign(
                id,
                { ...variables.assignment, expectedVersion: updated.version },
                keys.assignment,
            );
        },
        onSuccess: (updated, variables) => {
            retryKeys.current.delete(JSON.stringify(variables));
            commitInboundCampaignCache(qc, updated);
            void qc.invalidateQueries({ queryKey: inboundQueryKeys.all });
            notificationsStore.create({ type: "success", title: "Draft saved", message: "Server readiness was refreshed." });
        },
        onError: (error) => {
            void qc.invalidateQueries({ queryKey: inboundQueryKeys.detail(id) });
            const state = inboundErrorState(error);
            notificationsStore.create({
                type: "error",
                title: state === "conflict"
                    ? "Campaign changed elsewhere"
                    : state === "authorization-unavailable"
                        ? "Permissions could not be confirmed"
                        : "Could not save inbound campaign",
                message: state === "conflict"
                    ? "Reload the latest configuration, review it, and apply your changes again."
                    : state === "authorization-unavailable"
                        ? "Nothing was saved. Your access was not denied — the permission lookup itself failed. Try again in a moment."
                        : error instanceof Error ? error.message : "Please try again.",
            });
        },
    });
}

function useLifecycleMutation(id: string, action: "activate" | "deactivate" | "archive") {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: (expectedVersion: number) => action === "activate"
            ? inboundApi.activate(id, expectedVersion)
            : action === "archive"
                ? inboundApi.archive(id, expectedVersion)
                : inboundApi.deactivate(id, expectedVersion),
        onError: (error) => {
            void qc.invalidateQueries({ queryKey: inboundQueryKeys.detail(id) });
            void qc.invalidateQueries({ queryKey: inboundQueryKeys.readiness(id) });
            const state = inboundErrorState(error);
            notificationsStore.create({
                type: "error",
                title: state === "conflict"
                    ? "Campaign changed elsewhere"
                    : state === "no-permission"
                        ? "Permission denied"
                        // Distinct from "Permission denied": the lookup
                        // failed, so no denial has been established.
                        : state === "authorization-unavailable"
                            ? "Permissions could not be confirmed"
                            : state === "activation-blocked"
                                ? "Activation blocked by server readiness"
                                : action === "activate" ? "Activation blocked" : action === "archive" ? "Could not archive" : "Could not deactivate",
                message: state === "conflict"
                    ? "Reload the latest server version before changing live routing."
                    : state === "authorization-unavailable"
                        ? "Live routing was not changed. Your access was not denied — the permission lookup itself failed. Try again in a moment."
                        : state === "activation-blocked"
                            ? "Open the campaign and resolve each readiness blocker before activating."
                            : error instanceof Error ? error.message : "Refresh the latest server state and try again.",
            });
        },
        onSuccess: (updated) => {
            commitInboundCampaignCache(qc, updated);
            void qc.invalidateQueries({ queryKey: inboundQueryKeys.all });
            notificationsStore.create({
                type: "success",
                title: action === "activate" ? "Inbound calling activated" : action === "archive" ? "Inbound campaign archived" : "Inbound calling deactivated",
                message: action === "activate"
                    ? `${campaignNumber(updated)} is accepting calls.`
                    : action === "archive"
                        ? `${campaignNumber(updated)} is now read-only and remains inactive.`
                        : `${campaignNumber(updated)} is no longer routed to the AI agent.`,
            });
        },
    });
}

export function useActivateInboundCampaign(id: string) {
    return useLifecycleMutation(id, "activate");
}

export function usePauseInboundCampaign(id: string) {
    return useLifecycleMutation(id, "deactivate");
}

export function useArchiveInboundCampaign(id: string) {
    return useLifecycleMutation(id, "archive");
}
