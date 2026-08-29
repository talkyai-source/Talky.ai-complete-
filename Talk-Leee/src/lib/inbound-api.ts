import { z } from "zod";
import { sharedHttpClient } from "@/lib/api";

export type InboundLifecycleStatus = "draft" | "active" | "paused" | "archived";
export type InboundAfterHoursAction = "hangup" | "voicemail" | "transfer";
export type InboundOpeningMode = "caller_first" | "agent_first";

export interface InboundReadinessBlocker {
    code: string;
    message: string;
    remediation: string;
}

export interface InboundReadinessCheck {
    key: string;
    label: string;
    passed: boolean;
    detail: string;
}

export interface InboundReadiness {
    ready: boolean;
    checks: InboundReadinessCheck[];
    blockers: InboundReadinessBlocker[];
    checked_at?: string | null;
}

export interface InboundPhoneNumber {
    id: string;
    /** Used only in API payloads. UI surfaces must render masked_number. */
    e164?: string;
    masked_number: string;
    label?: string | null;
    provider?: string | null;
    verification_status: "verified" | "pending" | "failed" | string;
    assignment_status: "available" | "assigned" | string;
    assigned_campaign_id?: string | null;
    version: number;
    available: boolean;
}

export interface InboundWeeklyWindow {
    day: number;
    enabled: boolean;
    start: string;
    end: string;
    /** Optional additional same-day windows for split shifts. */
    windows?: Array<{ start: string; end: string }>;
}

export interface InboundCampaign {
    id: string;
    name: string;
    purpose?: string | null;
    direction: "inbound";
    status: InboundLifecycleStatus;
    version: number;
    config_version: number;
    config_checksum: string;
    phone_number?: InboundPhoneNumber | null;
    campaign_id: string;
    campaign_name?: string | null;
    sip_trunk_id: string;
    sip_trunk_name?: string | null;
    agent_persona?: string | null;
    system_prompt?: string | null;
    knowledge_base_id?: string | null;
    voice_id?: string | null;
    allowed_tools: string[];
    opening_mode: InboundOpeningMode;
    greeting: string;
    silence_timeout_seconds: number;
    timezone: string;
    weekly_schedule: InboundWeeklyWindow[];
    holiday_policy: "closed" | "regular_hours" | string;
    after_hours_action: InboundAfterHoursAction;
    after_hours_message?: string | null;
    transfer_number?: string | null;
    transfer_enabled: boolean;
    transfer_destinations: string[];
    transfer_failure_action: "voicemail" | "return_to_agent" | "hangup" | string;
    max_transfer_attempts: number;
    max_transfer_hops: number;
    max_call_duration_seconds: number;
    recording_enabled: boolean;
    consent_message?: string | null;
    readiness: InboundReadiness;
    active_at?: string | null;
    last_call_at?: string | null;
    last_error?: string | null;
    created_at: string;
    updated_at: string;
}

export interface InboundCampaignInput {
    name: string;
    /** Selected from verified tenant inventory; never entered as free text. */
    did_number: string;
    purpose?: string | null;
    campaign_id: string;
    sip_trunk_id: string;
    agent_persona?: string | null;
    system_prompt?: string | null;
    knowledge_base_id?: string | null;
    voice_id?: string | null;
    allowed_tools?: string[];
    opening_mode: InboundOpeningMode;
    greeting: string;
    silence_timeout_seconds: number;
    timezone: string;
    weekly_schedule: InboundWeeklyWindow[];
    holiday_policy: "closed" | "regular_hours";
    after_hours_action: InboundAfterHoursAction;
    after_hours_message?: string | null;
    transfer_number?: string | null;
    transfer_enabled: boolean;
    transfer_destinations?: string[];
    transfer_failure_action: "voicemail" | "return_to_agent" | "hangup";
    max_transfer_attempts: number;
    max_transfer_hops: number;
    max_call_duration_seconds: number;
    recording_enabled: boolean;
    consent_message?: string | null;
}

export interface TenantInboundControls {
    inbound_enabled: boolean;
    version: number;
    reason?: string | null;
    updated_at?: string | null;
}

export interface InboundRuntimeCapabilities {
    transfer_runtime_available: boolean;
    transfer_platform_enabled: boolean;
    transfer_configuration_available: boolean;
}

const UnknownRecordSchema = z.record(z.string(), z.unknown());

function asRecord(value: unknown): Record<string, unknown> {
    const parsed = UnknownRecordSchema.safeParse(value);
    return parsed.success ? parsed.data : {};
}

function textValue(value: unknown, fallback = ""): string {
    return typeof value === "string" ? value : fallback;
}

function nullableText(value: unknown): string | null {
    return typeof value === "string" && value.trim() ? value : null;
}

function numberValue(value: unknown, fallback: number): number {
    const parsed = typeof value === "number" ? value : Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
}

function booleanValue(value: unknown, fallback = false): boolean {
    if (typeof value === "boolean") return value;
    if (value === "true" || value === 1) return true;
    if (value === "false" || value === 0) return false;
    return fallback;
}

function stringList(value: unknown): string[] {
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function maskPhoneNumber(value: string): string {
    const compact = value.replace(/\s/g, "");
    if (!compact) return "Number not assigned";
    if (compact.includes("•") || compact.includes("*")) return value;
    if (compact.length <= 4) return `••${compact.slice(-2)}`;
    return `${compact.slice(0, Math.min(3, compact.length - 4))} ••• ••${compact.slice(-2)}`;
}

function normalizeStatus(value: unknown): InboundLifecycleStatus {
    const status = textValue(value, "draft").toLowerCase();
    if (status === "active") return "active";
    if (status === "paused" || status === "inactive" || status === "deactivating") return "paused";
    if (status === "archived" || status === "deleted") return "archived";
    return "draft";
}

function normalizeReadiness(value: unknown): InboundReadiness {
    const raw = asRecord(value);
    const rawChecks = Array.isArray(raw.checks) ? raw.checks : [];
    const checks = rawChecks.map((entry, index) => {
        const check = asRecord(entry);
        const key = textValue(check.key ?? check.code, `check_${index + 1}`);
        return {
            key,
            label: textValue(check.label ?? check.message, key.replace(/_/g, " ")),
            passed: booleanValue(check.passed ?? check.ok),
            detail: textValue(check.detail ?? check.remediation),
        };
    });
    const rawBlockers = Array.isArray(raw.blockers) ? raw.blockers : [];
    const blockers = rawBlockers.map((entry, index) => {
        const blocker = asRecord(entry);
        return {
            code: textValue(blocker.code ?? blocker.key, `blocker_${index + 1}`),
            message: textValue(blocker.message ?? blocker.label, "Activation requirement is not met."),
            remediation: textValue(blocker.remediation ?? blocker.detail, "Review this campaign and try again."),
        };
    });
    if (blockers.length === 0) {
        for (const check of checks.filter((entry) => !entry.passed)) {
            blockers.push({ code: check.key, message: check.label, remediation: check.detail });
        }
    }
    return {
        // Fail closed: only an explicit server readiness flag permits the UI
        // to offer activation. Passing-looking client data never invents it.
        ready: booleanValue(raw.ready ?? raw.is_ready, false),
        checks,
        blockers,
        checked_at: nullableText(raw.checked_at),
    };
}

function normalizePhoneNumber(value: unknown, legacyNumber?: unknown): InboundPhoneNumber | null {
    const raw = asRecord(value);
    const source = textValue(raw.masked_number ?? raw.e164_masked ?? raw.display_number ?? raw.phone_number ?? raw.number ?? legacyNumber);
    const id = textValue(raw.id ?? raw.phone_number_id) || source;
    if (!source && !id) return null;
    const verificationStatus = textValue(raw.verification_status ?? raw.status, booleanValue(raw.verified) ? "verified" : "pending_verification");
    const assignmentStatus = textValue(raw.assignment_status, "assigned");
    return {
        id,
        e164: textValue(raw.e164 ?? raw.did_number ?? raw.phone_number ?? raw.number ?? legacyNumber) || undefined,
        masked_number: maskPhoneNumber(source),
        label: nullableText(raw.label ?? raw.friendly_name),
        provider: nullableText(raw.provider),
        verification_status: verificationStatus,
        assignment_status: assignmentStatus,
        assigned_campaign_id: nullableText(raw.assigned_campaign_id ?? raw.inbound_campaign_id),
        version: numberValue(raw.version, 1),
        available: booleanValue(raw.available, assignmentStatus === "available"),
    };
}

export function defaultInboundWeeklySchedule(): InboundWeeklyWindow[] {
    return Array.from({ length: 7 }, (_, day) => ({
        day,
        enabled: day < 5,
        start: "09:00",
        end: "17:00",
        windows: [{ start: "09:00", end: "17:00" }],
    }));
}

function normalizeWeeklySchedule(value: unknown): InboundWeeklyWindow[] {
    if (!Array.isArray(value)) return defaultInboundWeeklySchedule();
    const byDay = new Map<number, InboundWeeklyWindow>();
    for (const entry of value) {
        const row = asRecord(entry);
        const day = numberValue(row.day ?? row.day_of_week, -1);
        if (day < 0 || day > 6) continue;
        const rawWindows = Array.isArray(row.windows) ? row.windows : [];
        const windows = rawWindows
            .map((item) => asRecord(item))
            .map((item) => ({ start: textValue(item.start ?? item.start_time), end: textValue(item.end ?? item.end_time) }))
            .filter((item) => item.start && item.end);
        const start = textValue(row.start ?? row.start_time, windows[0]?.start ?? "09:00");
        const end = textValue(row.end ?? row.end_time, windows[0]?.end ?? "17:00");
        byDay.set(day, {
            day,
            enabled: booleanValue(row.enabled, true),
            start,
            end,
            windows: windows.length > 0 ? windows : [{ start, end }],
        });
    }
    return defaultInboundWeeklySchedule().map((entry) => byDay.get(entry.day) ?? entry);
}

export function parseInboundCampaign(value: unknown): InboundCampaign {
    const envelope = asRecord(value);
    const raw = asRecord(envelope.inbound_campaign ?? envelope.campaign ?? value);
    const config = asRecord(raw.config);
    const ai = asRecord(raw.ai_behavior ?? raw.qualification_config ?? config.ai_behavior);
    const opening = asRecord(raw.opening ?? config.opening);
    const schedule = asRecord(raw.schedule ?? raw.business_hours ?? config.schedule);
    const transfer = asRecord(raw.transfer_policy ?? config.transfer_policy);
    const recording = asRecord(raw.recording ?? raw.recording_policy ?? config.recording);
    const id = textValue(raw.id);
    if (!id) throw new Error("Inbound campaign response is missing an id.");
    const action = textValue(raw.after_hours_action ?? schedule.after_hours_action);
    return {
        id,
        name: textValue(raw.name, "Untitled inbound campaign"),
        purpose: nullableText(raw.purpose ?? raw.description ?? ai.purpose),
        direction: "inbound",
        status: normalizeStatus(raw.status),
        version: numberValue(raw.version, 1),
        config_version: numberValue(raw.config_version, numberValue(raw.version, 1)),
        config_checksum: textValue(raw.config_checksum),
        phone_number: normalizePhoneNumber(
            raw.phone_number ?? raw.did_assignment ?? {
                id: raw.assignment_id,
                did_number: raw.did_number,
                assignment_status: raw.assignment_status,
                version: raw.assignment_version,
                verified: true,
            },
            raw.did_number,
        ),
        campaign_id: textValue(raw.campaign_id ?? ai.campaign_id),
        campaign_name: nullableText(raw.campaign_name ?? ai.campaign_name),
        sip_trunk_id: textValue(raw.sip_trunk_id),
        sip_trunk_name: nullableText(raw.sip_trunk_name),
        agent_persona: nullableText(raw.agent_persona ?? ai.persona),
        system_prompt: nullableText(raw.system_prompt ?? ai.system_prompt),
        knowledge_base_id: nullableText(raw.knowledge_base_id ?? ai.knowledge_base_id),
        voice_id: nullableText(raw.voice_id ?? ai.voice_id),
        allowed_tools: stringList(raw.allowed_tools ?? ai.allowed_tools),
        opening_mode: textValue(raw.opening_mode ?? opening.mode) === "agent_first" ? "agent_first" : "caller_first",
        greeting: textValue(raw.greeting ?? opening.greeting),
        silence_timeout_seconds: numberValue(raw.silence_timeout_seconds ?? opening.silence_timeout_seconds ?? ai.silence_timeout_seconds, 8),
        timezone: textValue(raw.timezone ?? schedule.timezone, "UTC"),
        weekly_schedule: normalizeWeeklySchedule(raw.weekly_schedule ?? schedule.weekly_schedule ?? schedule.windows),
        holiday_policy: textValue(raw.holiday_policy ?? schedule.holiday_policy, "closed"),
        after_hours_action: action === "hangup" || action === "transfer" ? action : "voicemail",
        after_hours_message: nullableText(raw.after_hours_message ?? schedule.after_hours_message),
        transfer_number: nullableText(raw.transfer_number ?? transfer.primary_destination),
        transfer_enabled: booleanValue(raw.transfer_enabled ?? transfer.enabled),
        transfer_destinations: stringList(raw.transfer_destinations ?? transfer.destinations),
        transfer_failure_action: textValue(raw.transfer_failure_action ?? transfer.failure_action, "voicemail"),
        max_transfer_attempts: numberValue(raw.max_transfer_attempts ?? transfer.max_attempts, 2),
        max_transfer_hops: numberValue(raw.max_transfer_hops ?? transfer.max_hops, 2),
        max_call_duration_seconds: numberValue(raw.max_call_duration_seconds ?? transfer.max_call_duration_seconds, 1800),
        recording_enabled: booleanValue(raw.recording_enabled ?? recording.enabled),
        consent_message: nullableText(raw.consent_message ?? recording.consent_message),
        readiness: normalizeReadiness(raw.readiness),
        active_at: nullableText(raw.active_at),
        last_call_at: nullableText(raw.last_call_at),
        last_error: nullableText(raw.last_error),
        created_at: textValue(raw.created_at),
        updated_at: textValue(raw.updated_at),
    };
}

export function parseInboundCampaignList(value: unknown): InboundCampaign[] {
    const raw = asRecord(value);
    const items = Array.isArray(value)
        ? value
        : Array.isArray(raw.items)
            ? raw.items
            : Array.isArray(raw.inbound_campaigns)
                ? raw.inbound_campaigns
                : Array.isArray(raw.campaigns)
                    ? raw.campaigns
                    : null;
    if (!items) throw new Error("Inbound campaign list response has an invalid format.");
    return items.map(parseInboundCampaign);
}

export function parsePhoneNumberAvailability(value: unknown): InboundPhoneNumber[] {
    const raw = asRecord(value);
    const items = Array.isArray(value)
        ? value
        : Array.isArray(raw.items)
            ? raw.items
            : Array.isArray(raw.phone_numbers)
                ? raw.phone_numbers
                : [];
    return items
        .map((entry) => normalizePhoneNumber(entry))
        .filter((entry): entry is InboundPhoneNumber => Boolean(entry))
        .filter((entry) => entry.verification_status === "verified" && entry.available);
}

export function parseInboundRuntimeCapabilities(value: unknown): InboundRuntimeCapabilities {
    const raw = asRecord(value);
    const runtimeAvailable = raw.transfer_runtime_available === true;
    const platformEnabled = raw.transfer_platform_enabled === true;
    return {
        transfer_runtime_available: runtimeAvailable,
        transfer_platform_enabled: platformEnabled,
        // Never trust a loose truthy value or a contradictory aggregate from
        // the wire. Both independent server gates must be explicitly true.
        transfer_configuration_available:
            raw.transfer_configuration_available === true
            && runtimeAvailable
            && platformEnabled,
    };
}

export function createInboundIdempotencyKey(): string {
    return typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export const INBOUND_RETRY_WINDOW_MS = 23 * 60 * 60 * 1000;
const MAX_PENDING_RETRY_KEYS = 128;

interface PendingRetryKey {
    key: string;
    expiresAt: number;
}

function isAmbiguousMutationFailure(error: unknown): boolean {
    const status = (error as { status?: unknown } | null)?.status;
    if (typeof status !== "number") return true;
    return status === 408 || status === 425 || status === 429 || status >= 500;
}

function cleanText(value: string | null | undefined): string | null {
    return value?.trim() || null;
}

function cleanInput(input: InboundCampaignInput, includeAssignment = false) {
    const consentMessage = input.recording_enabled ? cleanText(input.consent_message) : null;
    const transferNumber = input.after_hours_action === "transfer" ? cleanText(input.transfer_number) : null;
    const afterHoursMessage = input.after_hours_action === "hangup" ? null : cleanText(input.after_hours_message);
    // Only runtime-backed, admission-pinned overrides are sent here.
    // Knowledge and executable tools remain owned by the selected base
    // campaign so this form cannot advertise capabilities the voice runtime
    // cannot actually execute.
    const qualificationConfig: Record<string, unknown> = {};
    const supportedText = {
        purpose: cleanText(input.purpose),
        persona: cleanText(input.agent_persona),
        system_prompt: cleanText(input.system_prompt),
        voice_id: cleanText(input.voice_id),
    };
    for (const [key, supportedValue] of Object.entries(supportedText)) {
        if (supportedValue) qualificationConfig[key] = supportedValue;
    }
    if (input.silence_timeout_seconds !== 8) {
        qualificationConfig.silence_timeout_seconds = input.silence_timeout_seconds;
    }
    return {
        name: input.name.trim(),
        ...(includeAssignment ? {
            did_number: input.did_number.trim(),
            sip_trunk_id: input.sip_trunk_id,
        } : {}),
        campaign_id: input.campaign_id,
        timezone: input.timezone.trim(),
        opening_mode: input.opening_mode,
        greeting: cleanText(input.greeting),
        business_hours: {
            weekly_schedule: input.weekly_schedule,
            holiday_policy: input.holiday_policy,
            after_hours_message: afterHoursMessage,
        },
        after_hours_action: input.after_hours_action,
        transfer_number: transferNumber,
        transfer_policy: {
            enabled: input.transfer_enabled,
            destinations: input.transfer_destinations ?? [],
            failure_action: input.transfer_failure_action,
            max_attempts: input.max_transfer_attempts,
            max_hops: input.max_transfer_hops,
            max_call_duration_seconds: input.max_call_duration_seconds,
        },
        recording_enabled: input.recording_enabled,
        consent_message: consentMessage,
        recording_policy: {
            enabled: input.recording_enabled,
            consent_message: consentMessage,
        },
        qualification_config: qualificationConfig,
    };
}

export function inboundErrorKind(error: unknown): "forbidden" | "conflict" | "other" {
    const status = (error as { status?: unknown } | null)?.status;
    if (status === 403) return "forbidden";
    if (status === 409 || status === 412) return "conflict";
    return "other";
}

export function inboundErrorCode(error: unknown): string | null {
    const code = (error as { code?: unknown } | null)?.code;
    return typeof code === "string" && code.trim() ? code.trim() : null;
}

class InboundApi {
    private readonly retryKeys = new Map<string, PendingRetryKey>();

    private get client() {
        return sharedHttpClient();
    }

    private async idempotentMutation<T>(
        scope: string,
        body: unknown,
        request: (idempotencyKey: string) => Promise<T>,
        suppliedKey?: string,
    ): Promise<T> {
        const signature = `${scope}:${JSON.stringify(body)}`;
        const pending = this.retryKeys.get(signature);
        if (!suppliedKey && pending && Date.now() >= pending.expiresAt) {
            // The server retains claims for 24 hours. Never send an old key
            // after that boundary because it could be accepted as a brand-new
            // create. Keep this bounded tombstone until the page/session is
            // deliberately refreshed and current server state is reviewed.
            throw new Error("The safe retry window expired. Refresh current server state before starting a new operation.");
        }
        const idempotencyKey = suppliedKey
            ?? pending?.key
            ?? createInboundIdempotencyKey();
        if (!suppliedKey && !pending) {
            if (this.retryKeys.size >= MAX_PENDING_RETRY_KEYS) {
                throw new Error("Too many unresolved inbound operations. Refresh current server state before continuing.");
            }
            this.retryKeys.set(signature, {
                key: idempotencyKey,
                expiresAt: Date.now() + INBOUND_RETRY_WINDOW_MS,
            });
        }

        try {
            const result = await request(idempotencyKey);
            if (!suppliedKey && this.retryKeys.get(signature)?.key === idempotencyKey) {
                this.retryKeys.delete(signature);
            }
            return result;
        } catch (error) {
            // A timeout, disconnect, gateway response, or server failure can
            // happen after commit. A retry must replay with the original key.
            if (!suppliedKey && !isAmbiguousMutationFailure(error) && this.retryKeys.get(signature)?.key === idempotencyKey) {
                this.retryKeys.delete(signature);
            }
            throw error;
        }
    }

    async list(options: { includeArchived?: boolean; signal?: AbortSignal } = {}): Promise<InboundCampaign[]> {
        const path = options.includeArchived
            ? "/inbound-campaigns?include_archived=true"
            : "/inbound-campaigns";
        const data = await this.client.request({ path, method: "GET", signal: options.signal });
        return parseInboundCampaignList(data);
    }

    async get(id: string, signal?: AbortSignal): Promise<InboundCampaign> {
        const data = await this.client.request({ path: `/inbound-campaigns/${id}`, method: "GET", signal });
        return parseInboundCampaign(data);
    }

    async create(input: InboundCampaignInput, didNumber: string, idempotencyKey?: string): Promise<InboundCampaign> {
        const body = cleanInput({ ...input, did_number: didNumber }, true);
        return this.idempotentMutation("create", body, async (key) => {
            const data = await this.client.request({
                path: "/inbound-campaigns",
                method: "POST",
                headers: { "Idempotency-Key": key },
                body,
            });
            return parseInboundCampaign(data);
        }, idempotencyKey);
    }

    async update(id: string, input: InboundCampaignInput, expectedVersion: number, idempotencyKey?: string): Promise<InboundCampaign> {
        const body = { ...cleanInput(input), expected_version: expectedVersion };
        return this.idempotentMutation(`update:${id}`, body, async (key) => {
            const data = await this.client.request({
                path: `/inbound-campaigns/${id}`,
                method: "PUT",
                headers: { "Idempotency-Key": key },
                body,
            });
            return parseInboundCampaign(data);
        }, idempotencyKey);
    }

    async assign(id: string, input: {
        didNumber: string;
        sipTrunkId: string;
        expectedVersion: number;
        reason: string;
    }, idempotencyKey?: string): Promise<InboundCampaign> {
        const body = {
            did_number: input.didNumber.trim(),
            sip_trunk_id: input.sipTrunkId,
            expected_version: input.expectedVersion,
            reason: input.reason.trim(),
        };
        return this.idempotentMutation(`assign:${id}`, body, async (key) => {
            const data = await this.client.request({
                path: `/inbound-campaigns/${id}/assign`,
                method: "POST",
                headers: { "Idempotency-Key": key },
                body,
            });
            return parseInboundCampaign(data);
        }, idempotencyKey);
    }

    async readiness(id: string, signal?: AbortSignal): Promise<InboundReadiness> {
        const data = await this.client.request({ path: `/inbound-campaigns/${id}/readiness`, method: "GET", signal });
        return normalizeReadiness(data);
    }

    async activate(id: string, expectedVersion: number, idempotencyKey?: string): Promise<InboundCampaign> {
        return this.lifecycle(id, "activate", expectedVersion, idempotencyKey);
    }

    async deactivate(id: string, expectedVersion: number, idempotencyKey?: string): Promise<InboundCampaign> {
        return this.lifecycle(id, "deactivate", expectedVersion, idempotencyKey);
    }

    async pause(id: string, expectedVersion: number, idempotencyKey?: string): Promise<InboundCampaign> {
        return this.deactivate(id, expectedVersion, idempotencyKey);
    }

    async archive(id: string, expectedVersion: number, idempotencyKey?: string): Promise<InboundCampaign> {
        return this.lifecycle(id, "archive", expectedVersion, idempotencyKey);
    }

    private async lifecycle(id: string, action: "activate" | "deactivate" | "archive", expectedVersion: number, idempotencyKey?: string): Promise<InboundCampaign> {
        const body = { expected_version: expectedVersion };
        return this.idempotentMutation(`${action}:${id}`, body, async (key) => {
            const data = await this.client.request({
                path: `/inbound-campaigns/${id}/${action}`,
                method: "POST",
                headers: { "Idempotency-Key": key },
                body,
            });
            return parseInboundCampaign(data);
        }, idempotencyKey);
    }

    async availablePhoneNumbers(signal?: AbortSignal): Promise<InboundPhoneNumber[]> {
        const inventory = await this.client.request({ path: "/tenant-phone-numbers/", method: "GET", signal });
        const inventoryRecord = asRecord(inventory);
        const rows = Array.isArray(inventory)
            ? inventory
            : Array.isArray(inventoryRecord.items)
                ? inventoryRecord.items
                : Array.isArray(inventoryRecord.phone_numbers)
                    ? inventoryRecord.phone_numbers
                    : [];
        const verified = rows
            .map((entry) => {
                const row = asRecord(entry);
                return normalizePhoneNumber({
                    ...row,
                    verification_status: row.verification_status ?? row.status,
                    assignment_status: "unknown",
                    available: false,
                });
            })
            .filter((entry): entry is InboundPhoneNumber => entry !== null && entry.verification_status === "verified" && Boolean(entry.e164));

        // Assignment truth lives in inbound_did_assignments, not mutable DID
        // metadata. Check every tenant-owned verified number through the
        // privacy-preserving availability contract before presenting it.
        const checked = await Promise.all(verified.map(async (number) => {
            const availability = asRecord(await this.client.request({
                path: "/inbound-campaigns/dids/availability",
                method: "GET",
                params: { did_number: number.e164 as string },
                signal,
            }));
            const available = booleanValue(availability.available);
            return {
                ...number,
                available,
                assignment_status: available ? "available" : "assigned",
            };
        }));
        return checked.filter((number) => number.available);
    }

    async getControls(signal?: AbortSignal): Promise<TenantInboundControls> {
        const raw = asRecord(await this.client.request({
            path: "/inbound-campaigns/controls",
            method: "GET",
            signal,
        }));
        return {
            inbound_enabled: booleanValue(raw.inbound_enabled, false),
            version: numberValue(raw.version, 1),
            reason: nullableText(raw.reason),
            updated_at: nullableText(raw.updated_at),
        };
    }

    async getCapabilities(
        configId?: string,
        signal?: AbortSignal,
    ): Promise<InboundRuntimeCapabilities> {
        const raw = await this.client.request({
            path: "/inbound-campaigns/capabilities",
            method: "GET",
            ...(configId ? { params: { config_id: configId } } : {}),
            signal,
        });
        return parseInboundRuntimeCapabilities(raw);
    }

    async setControls(input: {
        inbound_enabled: boolean;
        expected_version: number;
        reason: string;
    }, idempotencyKey?: string): Promise<TenantInboundControls> {
        return this.idempotentMutation("controls", input, async (key) => {
            const raw = asRecord(await this.client.request({
                path: "/inbound-campaigns/controls",
                method: "PATCH",
                headers: { "Idempotency-Key": key },
                body: input,
            }));
            return {
                inbound_enabled: booleanValue(raw.inbound_enabled, false),
                version: numberValue(raw.version, input.expected_version + 1),
                reason: nullableText(raw.reason),
                updated_at: nullableText(raw.updated_at),
            };
        }, idempotencyKey);
    }
}

export const inboundApi = new InboundApi();
