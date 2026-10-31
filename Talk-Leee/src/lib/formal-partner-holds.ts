import { z } from "zod";
import { sharedHttpClient } from "@/lib/api";
import { backendEndpoints } from "@/lib/backend-endpoints";

const uuid = z.guid();
const timestamp = z.string().datetime({offset:true});
export const holdTypes = ["ADMIN", "TEMPORARY", "BILLING", "ABUSE", "COMPLIANCE", "EMERGENCY"] as const;
export type HoldType = typeof holdTypes[number];
const eventSchema = z.object({
    suspension_id: uuid, target_type: z.literal("partner"), target_id: uuid,
    suspension_type: z.enum(holdTypes), reason_description: z.string(), reason_category: z.string(),
    suspended_at: timestamp, suspended_until: timestamp.nullable(), is_active: z.boolean(),
    appeal_reason: z.string().nullable(), appeal_decision: z.string().nullable(),
    appeal_submitted_at: timestamp.nullable().optional(),
    appeal_response: z.string().nullable(), audit_log_id: uuid.nullable(),
    restored_at: timestamp.nullable(), restore_reason: z.string().nullable(),
    suspended_by: uuid.nullable(), evidence: z.object({operator_request_id:uuid.optional()}).nullable(),
});
const statusSchema = z.object({
    target_type: z.literal("partner"), target_id: uuid, status: z.string(), is_suspended: z.boolean(),
    active_suspension: eventSchema.nullable(), suspension_history: z.array(eventSchema),
    effective_status: z.string().nullable().optional(), remaining_hold_ids: z.array(uuid),
    history_complete: z.boolean(), history_limit: z.number().int().positive(),
});
const receiptSchema = z.object({
    suspension_id: uuid, audit_event_id: uuid, target_type: z.literal("partner").optional(), target_id: uuid.optional(),
    status: z.string().optional(), restored: z.boolean().optional(), decision: z.enum(["granted", "denied"]).optional(),
    appeal_submitted: z.boolean().optional(), effective_status: z.string().nullable().optional(),
    remaining_hold_ids: z.array(uuid).optional(), restoration_warnings: z.array(z.string()).optional(),
});
const evidenceSchema = z.object({
    version: z.literal(1),
    suspension_id: uuid, target_type: z.literal("partner"), target_id: uuid,
    event_state: z.enum(["active","restored"]), policy_cutoff: timestamp.nullable(), observed_at_db: timestamp,
    current_authority: z.object({status:z.enum(["allowed","restricted","unknown"]), remaining_hold_ids:z.array(uuid)}),
    propagation_status: z.enum(["confirmed", "pending", "unverified"]),
    dimensions: z.array(z.object({
        key: z.enum(["credentials","assistant_runtimes","telephony"]), status: z.enum(["confirmed","pending","unverified","exempt_by_policy","not_applicable"]), coverage: z.enum(["complete", "incomplete", "unknown"]),
        counts: z.object({captured:z.number().int().nonnegative(), observed:z.number().int().nonnegative(), confirmed:z.number().int().nonnegative(), pending:z.number().int().nonnegative(), exempt:z.number().int().nonnegative(), unknown:z.number().int().nonnegative()}),
        reasons: z.array(z.string()),
    })),
    reconciliation_reasons: z.array(z.string()),
}).superRefine((value,ctx) => {
    if (value.dimensions.length !== 3 || new Set(value.dimensions.map(item => item.key)).size !== 3) ctx.addIssue({code:"custom",message:"Complete enforcement dimensions are required."});
    const complete = value.dimensions.every(item => item.coverage === "complete" && ["confirmed","exempt_by_policy","not_applicable"].includes(item.status));
    const expected = complete ? "confirmed" : value.dimensions.some(item => item.status === "pending") ? "pending" : "unverified";
    if (value.propagation_status !== expected) ctx.addIssue({code:"custom",message:"Enforcement summary does not match the evidence."});
    for (const item of value.dimensions) {
        if (["confirmed","exempt_by_policy","not_applicable"].includes(item.status) && (item.coverage !== "complete" || item.counts.pending || item.counts.unknown)) ctx.addIssue({code:"custom",message:"Completed enforcement contains unresolved effects."});
    }
});
export type PartnerHoldEvent = z.infer<typeof eventSchema>;
export type PartnerHoldStatus = z.infer<typeof statusSchema>;
export type PartnerHoldReceipt = z.infer<typeof receiptSchema>;
export type PartnerHoldEvidence = z.infer<typeof evidenceSchema>;
export type PartnerHoldCommand =
    | { kind: "suspend"; reason: string; category: string; holdType: HoldType; operatorRequestId:string; hours?: number }
    | { kind: "restore" | "appeal" | "granted" | "denied"; eventId: string; reason: string };

function path(template: string, id: string) { return template.replace("{id}", encodeURIComponent(uuid.parse(id))); }
function matchingTarget(value: {target_type?: string; target_id?: string}, partnerId: string) {
    if ((value.target_type !== undefined && value.target_type !== "partner") || (value.target_id !== undefined && value.target_id !== partnerId)) {
        throw new Error("The response belongs to a different account. Refresh and review the selected partner.");
    }
}

export const formalPartnerHolds = {
    async status(partnerId: string, signal?: AbortSignal): Promise<PartnerHoldStatus> {
        const endpoint = backendEndpoints.formalPartnerStatus;
        const result = statusSchema.parse(await sharedHttpClient().request({path:path(endpoint.path,partnerId), method:endpoint.method, timeoutMs:12_000, signal}));
        matchingTarget(result, partnerId);
        for (const event of [...result.suspension_history, ...(result.active_suspension ? [result.active_suspension] : [])]) matchingTarget(event, partnerId);
        return result;
    },
    async evidence(partnerId: string, eventId: string, signal?: AbortSignal): Promise<PartnerHoldEvidence> {
        const endpoint = backendEndpoints.formalHoldEvidence;
        const result = evidenceSchema.parse(await sharedHttpClient().request({path:path(endpoint.path,eventId), method:endpoint.method, timeoutMs:12_000, signal}));
        matchingTarget(result, partnerId);
        if (result.suspension_id !== eventId) throw new Error("Enforcement evidence belongs to another hold.");
        return result;
    },
    async change(partnerId: string, command: PartnerHoldCommand): Promise<PartnerHoldReceipt> {
        uuid.parse(partnerId);
        const reason = command.reason.trim();
        if (!reason || reason.length > 4000) throw new Error("Enter a reason of up to 4,000 characters.");
        let route: string;
        let body: Record<string, unknown>;
        if (command.kind === "suspend") {
            if (!command.category.trim()) throw new Error("Enter a reason category.");
            if (command.holdType === "TEMPORARY" || command.holdType === "BILLING") {
                if (!Number.isInteger(command.hours) || command.hours! < 1) throw new Error("Enter a positive whole number of hours.");
            }
            route = path(backendEndpoints.formalPartnerSuspend.path, partnerId);
            body = {suspension_type:command.holdType, reason_category:command.category.trim(), reason_description:reason,
                evidence:{operator_request_id:uuid.parse(command.operatorRequestId)},
                ...(command.hours !== undefined ? {duration_hours:command.hours} : {})};
        } else if (command.kind === "restore") {
            route = path(backendEndpoints.formalPartnerRestore.path, partnerId);
            body = {reason, expected_suspension_id:uuid.parse(command.eventId)};
        } else if (command.kind === "appeal") {
            route = path(backendEndpoints.formalHoldAppeal.path, command.eventId);
            body = {appeal_reason:reason};
        } else {
            route = path(backendEndpoints.formalHoldReview.path, command.eventId);
            body = {decision:command.kind, response:reason};
        }
        const result = receiptSchema.parse(await sharedHttpClient().request({path:route, method:"POST", body, timeoutMs:15_000}));
        matchingTarget(result, partnerId);
        if (command.kind === "suspend" && (result.target_id !== partnerId || result.target_type !== "partner" || result.status !== "suspended")) throw new Error("The saved hold has not been confirmed for this partner.");
        if (command.kind !== "suspend" && result.suspension_id !== command.eventId) throw new Error("The saved receipt belongs to another hold; inspect the result before retrying.");
        if ((command.kind === "granted" || command.kind === "denied") && result.decision !== command.kind) throw new Error("The saved appeal decision does not match the request.");
        if ((command.kind === "restore" || command.kind === "granted") && result.restored !== true) throw new Error("Restoration has not been confirmed.");
        if (command.kind === "appeal" && (result.appeal_submitted !== true || result.status !== "pending_review")) throw new Error("Appeal submission has not been confirmed.");
        return result;
    },
};
