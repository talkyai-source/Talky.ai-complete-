import { defaultInboundWeeklySchedule, type InboundCampaign, type InboundCampaignInput } from "@/lib/inbound-api";
import type { Campaign } from "@/lib/dashboard-api";
import type { SipTrunkRow } from "@/lib/telephony-api";

export type InboundFormErrors = Partial<Record<keyof InboundCampaignInput | "form", string>>;

export interface InboundValidationCapabilities {
    transferConfigurationAvailable?: boolean;
}

export function verifiedTransferConfigurationAvailable(state: {
    data?: { transfer_configuration_available?: boolean };
    isError: boolean;
    isSuccess: boolean;
}): boolean {
    return state.isSuccess
        && !state.isError
        && state.data?.transfer_configuration_available === true;
}

const E164_PATTERN = /^\+[1-9]\d{6,14}$/;

export function isValidE164(value: string): boolean {
    return E164_PATTERN.test(value.trim());
}

export function isEligibleInboundBaseCampaign(campaign: Pick<Campaign, "direction" | "status">): boolean {
    const status = campaign.status.trim().toLowerCase();
    if (!["active", "running", "draft", "paused", "stopped"].includes(status)) return false;
    if (campaign.direction === "inbound") return true;
    // The server may atomically convert a still-unused draft. It remains the
    // final authority because activity/queue state is not exposed in this list.
    return campaign.direction === "outbound" && status === "draft";
}

export function isEligibleInboundTrunk(trunk: Pick<SipTrunkRow, "direction" | "is_active" | "runtime_ready">): boolean {
    return trunk.is_active
        && trunk.runtime_ready
        && (trunk.direction === "inbound" || trunk.direction === "both");
}

export const INBOUND_AFTER_HOURS_OPTIONS = [
    { value: "hangup", label: "Reject before answer", runtimeSupported: true },
    { value: "voicemail", label: "AI message intake (call history)", runtimeSupported: true },
    { value: "transfer", label: "Transfer (runtime not yet available)", runtimeSupported: false },
] as const;

function browserTimezone(): string {
    try {
        return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    } catch {
        return "UTC";
    }
}

export function initialInboundCampaignInput(value?: InboundCampaign): InboundCampaignInput {
    return {
        name: value?.name ?? "",
        did_number: value?.phone_number?.e164 ?? "",
        purpose: value?.purpose ?? "",
        campaign_id: value?.campaign_id ?? "",
        sip_trunk_id: value?.sip_trunk_id ?? "",
        agent_persona: value?.agent_persona ?? "",
        system_prompt: value?.system_prompt ?? "",
        knowledge_base_id: "",
        voice_id: value?.voice_id ?? "",
        allowed_tools: [],
        opening_mode: value?.opening_mode ?? "caller_first",
        greeting: value?.greeting ?? "",
        silence_timeout_seconds: value?.silence_timeout_seconds ?? 8,
        timezone: value?.timezone ?? browserTimezone(),
        weekly_schedule: value?.weekly_schedule ?? defaultInboundWeeklySchedule(),
        holiday_policy: value?.holiday_policy === "regular_hours" ? "regular_hours" : "closed",
        after_hours_action: value?.after_hours_action ?? "hangup",
        after_hours_message: value?.after_hours_message ?? "",
        transfer_number: value?.transfer_number ?? "",
        transfer_enabled: value?.transfer_enabled ?? false,
        transfer_destinations: value?.transfer_destinations ?? [],
        transfer_failure_action: value?.transfer_failure_action === "return_to_agent" || value?.transfer_failure_action === "hangup" ? value.transfer_failure_action : "voicemail",
        max_transfer_attempts: value?.max_transfer_attempts ?? 2,
        max_transfer_hops: value?.max_transfer_hops ?? 2,
        max_call_duration_seconds: value?.max_call_duration_seconds ?? 1800,
        recording_enabled: value?.recording_enabled ?? false,
        consent_message: value?.consent_message ?? "",
    };
}

export function validateInboundCampaign(
    value: InboundCampaignInput,
    capabilities: InboundValidationCapabilities = {},
): InboundFormErrors {
    const errors: InboundFormErrors = {};
    const transferConfigurationAvailable = capabilities.transferConfigurationAvailable === true;
    if (!value.name.trim()) errors.name = "Enter a name for this inbound campaign.";
    if (!isValidE164(value.did_number)) errors.did_number = "Choose a verified phone number.";
    if (!value.campaign_id) errors.campaign_id = "Choose the AI campaign that should answer.";
    if (!value.sip_trunk_id) errors.sip_trunk_id = "Choose an inbound-capable SIP trunk.";
    if (!value.timezone.trim()) errors.timezone = "Choose a timezone.";
    if (value.opening_mode === "agent_first" && !value.greeting.trim()) errors.greeting = "Add the greeting the agent should play.";
    if (!Number.isFinite(value.silence_timeout_seconds) || !Number.isInteger(value.silence_timeout_seconds) || value.silence_timeout_seconds < 3 || value.silence_timeout_seconds > 60) errors.silence_timeout_seconds = "Silence timeout must be a whole number between 3 and 60 seconds.";
    const invalidWindow = value.weekly_schedule.some((day) => day.enabled && (day.windows?.length ? day.windows : [{ start: day.start, end: day.end }]).some((window) => !/^([01]\d|2[0-3]):[0-5]\d$/.test(window.start) || !/^([01]\d|2[0-3]):[0-5]\d$/.test(window.end) || window.start === window.end));
    if (invalidWindow) errors.weekly_schedule = "Each open business-hours window needs distinct valid start and end times.";
    if (value.after_hours_action === "voicemail" && !value.after_hours_message?.trim()) errors.after_hours_message = "Add the opening message the AI will use for after-hours intake.";
    if (value.after_hours_action === "transfer" && !transferConfigurationAvailable) errors.after_hours_action = "Choose reject or AI message intake. Inbound transfer remains blocked until the runtime and platform capability gates are enabled.";
    if (value.after_hours_action === "transfer" && !isValidE164(value.transfer_number ?? "")) errors.transfer_number = "Enter the transfer destination in E.164 format.";
    if (value.transfer_enabled && !transferConfigurationAvailable) errors.transfer_enabled = "Disable live transfer until linked-leg ownership, hard-cap teardown, carrier behavior, settlement, and both server gates are verified.";
    if (value.transfer_enabled && ((value.transfer_destinations ?? []).length === 0 || (value.transfer_destinations ?? []).some((destination) => !isValidE164(destination)))) errors.transfer_destinations = "Add at least one approved E.164 transfer destination.";
    if (!Number.isFinite(value.max_transfer_attempts) || !Number.isInteger(value.max_transfer_attempts) || value.max_transfer_attempts < 1 || value.max_transfer_attempts > 5) errors.max_transfer_attempts = "Transfer attempts must be a whole number between 1 and 5.";
    if (!Number.isFinite(value.max_transfer_hops) || !Number.isInteger(value.max_transfer_hops) || value.max_transfer_hops < 1 || value.max_transfer_hops > 5) errors.max_transfer_hops = "Transfer hops must be a whole number between 1 and 5.";
    if (!Number.isFinite(value.max_call_duration_seconds) || !Number.isInteger(value.max_call_duration_seconds) || value.max_call_duration_seconds < 60 || value.max_call_duration_seconds > 14400) errors.max_call_duration_seconds = "Maximum call duration must be a whole number between 60 and 14,400 seconds.";
    if (value.recording_enabled && !value.consent_message?.trim()) errors.consent_message = "Add the disclosure callers must hear before recording.";
    return errors;
}
