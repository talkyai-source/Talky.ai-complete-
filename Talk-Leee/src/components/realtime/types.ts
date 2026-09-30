export type CampaignVoiceSettingsValue = {
    pipeline_mode?: "cascaded" | "realtime";
    realtime_model?: string;
    realtime_voice?: string;
    realtime_settings?: { turn_detection?: "low" | "medium" | "high" | "auto"; noise_reduction?: "near_field" | "far_field" | "none" | "off" };
    realtime_prompt?: { persona?: "assistant" | "sales" | "support" | "receptionist"; goal: string; instructions: string; opening_greeting: string };
};

export const DEFAULT_REALTIME_PROMPT = {
    persona: "assistant" as const, goal: "Help the caller using verified company information.", instructions: "", opening_greeting: "",
};
