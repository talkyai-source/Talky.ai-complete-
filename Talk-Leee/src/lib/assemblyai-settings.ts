import { z } from "zod";

/** Policy IDs supported by the provider's streaming PII redaction API. */
export const ASSEMBLYAI_PII_POLICIES = [
    "account_number", "banking_information", "blood_type", "credit_card_cvv",
    "credit_card_expiration", "credit_card_number", "date", "date_interval",
    "date_of_birth", "drivers_license", "drug", "duration", "email_address",
    "event", "filename", "gender_sexuality", "healthcare_number", "injury",
    "ip_address", "language", "location", "location_address", "location_address_street",
    "location_city", "location_coordinate", "location_country", "location_state",
    "location_zip", "marital_status", "medical_condition", "medical_process",
    "money_amount", "nationality", "number_sequence", "occupation", "organization",
    "passport_number", "password", "person_age", "person_name", "phone_number",
    "physical_attribute", "political_affiliation", "religion", "statistics", "time",
    "url", "us_social_security_number", "username", "vehicle_id", "zodiac_sign",
] as const;

/** Universal-3.6 Pro controls. Null overrides leave AssemblyAI's mode defaults intact. */
export const AssemblyAISettingsSchema = z.object({
    region: z.enum(["global", "us", "eu"]).optional(),
    mode: z.enum(["balanced", "min_latency", "max_accuracy"]).optional(),
    min_turn_silence: z.number().int().min(50).max(10000).nullish(),
    max_turn_silence: z.number().int().positive().nullish(),
    interruption_delay: z.number().int().min(0).max(1000).nullish(),
    vad_threshold: z.number().min(0).max(1).nullish(),
    include_partial_turns: z.boolean().nullish(),
    prompt: z.string().max(1750).optional(),
    keyterms_prompt: z.array(z.string().trim().min(1).max(50)).max(100).optional(),
    auto_agent_context: z.boolean().optional(),
    previous_context_n_turns: z.number().int().min(0).max(100).nullish(),
    language_detection: z.boolean().optional(),
    speaker_labels: z.boolean().optional(),
    max_speakers: z.number().int().min(1).max(10).nullish(),
    speaker_labels_revision_interval_ms: z.number().int().refine((value) => value === 0 || value >= 120000, "Use 0 to disable mid-session revisions or at least 120000 ms").nullish(),
    voice_focus: z.enum(["near-field", "far-field"]).nullish(),
    voice_focus_threshold: z.number().min(0).max(1).nullish(),
    domain: z.literal("medical-v1").nullish(),
    redact_pii: z.boolean().optional(),
    redact_pii_policies: z.array(z.enum(ASSEMBLYAI_PII_POLICIES)).max(ASSEMBLYAI_PII_POLICIES.length).optional(),
    redact_pii_sub: z.enum(["hash", "entity_name"]).optional(),
    filter_profanity: z.boolean().optional(),
    session_heartbeat: z.boolean().optional(),
    inactivity_timeout: z.number().int().min(5).max(3600).nullish(),
}).refine((settings) => settings.min_turn_silence == null || settings.max_turn_silence == null || settings.max_turn_silence >= settings.min_turn_silence, {
    path: ["max_turn_silence"], message: "Maximum silence must be at least the minimum silence",
});

export type AssemblyAISettings = z.infer<typeof AssemblyAISettingsSchema>;

export const ASSEMBLYAI_MODE_OPTIONS = [
    { value: "balanced", label: "Balanced", description: "A balance of recognition accuracy and response speed." },
    { value: "min_latency", label: "Fast", description: "The shortest wait for responses, with less time to resolve detail." },
    { value: "max_accuracy", label: "Accuracy", description: "More time to recognize names, spelling and detailed answers." },
] as const;

export function assemblyAISettingsError(value: AssemblyAISettings | null | undefined): string | null {
    const result = AssemblyAISettingsSchema.safeParse(value ?? {});
    if (result.success) return null;
    const issue = result.error.issues[0];
    return `AssemblyAI ${issue.path.join(" ").replaceAll("_", " ")}: ${issue.message}`;
}
